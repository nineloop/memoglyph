"""Migration explicite vers document_id / meeting_id / project_id.

Le plan est séparé de son application : `prepare()` ne touche rien, expose les
regroupements candidats, et `apply()` vérifie que chaque source est inchangée
avant de remplacer atomiquement chaque fichier préparé. Une égalité de nom de
média n'est jamais une autorisation de fusion.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from . import docid, projects, writer_md
from .library import _strict_frontmatter


class IdentityMigrationError(ValueError):
    """Le corpus ne permet pas une migration non ambiguë."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class _DocumentChange:
    path: Path
    before_sha256: str
    after_text: str
    document_id: str
    meeting_id: str
    project_id: str
    source_document_id: str | None
    archived: bool

    def summary(self, root: Path) -> dict:
        return {
            "path": str(self.path.relative_to(root)),
            "document_id": self.document_id,
            "meeting_id": self.meeting_id,
            "project_id": self.project_id,
            "source_document_id": self.source_document_id,
            "archived": self.archived,
        }


@dataclass(frozen=True)
class _TextChange:
    path: Path
    before_sha256: str | None
    after_text: str


@dataclass(frozen=True)
class _Rename:
    source: Path
    destination: Path


@dataclass(frozen=True)
class IdentityPlan:
    root: Path
    documents: tuple[_DocumentChange, ...]
    text_changes: tuple[_TextChange, ...]
    renames: tuple[_Rename, ...]
    candidate_groups: tuple[dict, ...]
    unmapped_sidecar_keys: tuple[str, ...]
    unmapped_audio_files: tuple[str, ...]

    def summary(self) -> dict:
        return {
            "simulation": True,
            "root": str(self.root),
            "documents": [d.summary(self.root) for d in self.documents],
            "candidate_groups": list(self.candidate_groups),
            "sidecar_keys_migrated": sum(
                1 for change in self.text_changes
                if change.path.name == ".voiceprints.json"
            ),
            "audio_files_renamed": len(self.renames),
            "unmapped_sidecar_keys": list(self.unmapped_sidecar_keys),
            "unmapped_audio_files": list(self.unmapped_audio_files),
        }


def _read_document(
    path: Path, *, allow_missing_id: bool = False,
) -> tuple[dict, str, bytes]:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
        meta, body = _strict_frontmatter(text)
    except Exception as exc:  # noqa: BLE001
        raise IdentityMigrationError(f"Markdown illisible : {path}: {exc}") from exc
    if not docid.est_valide(meta.get("id")):
        if not allow_missing_id:
            raise IdentityMigrationError(f"Identifiant document invalide : {path}")
        meta = {"id": docid.nouveau(), **meta}
    return meta, body, raw


def _render(meta: dict, body: str) -> str:
    ordered = {"id": meta["id"]}
    for key in ("meeting_id", "project_id", "source_document_id"):
        if meta.get(key) is not None:
            ordered[key] = meta[key]
    ordered.update({k: v for k, v in meta.items() if k not in ordered})
    return writer_md._yaml_frontmatter(ordered) + "\n\n" + body.lstrip("\n")


def _project_maps(root: Path) -> tuple[dict[Path, str], dict[str, str]]:
    by_directory: dict[Path, str] = {}
    by_name: dict[str, str] = {}
    targets = [root]
    targets += [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")]
    for directory in targets:
        project = projects.load(directory)
        by_directory[directory.resolve()] = project.id
        by_name[project.name] = project.id
        by_name[directory.name] = project.id
    by_name[projects.INBOX_NAME] = projects.load(root).id
    return by_directory, by_name


def _active_paths(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*.md")
        if not any(part.startswith(".") for part in p.relative_to(root).parts)
    )


def _project_id_for(path: Path, root: Path, by_directory: dict[Path, str]) -> str:
    relative = path.relative_to(root)
    directory = root if len(relative.parts) == 1 else root / relative.parts[0]
    try:
        return by_directory[directory.resolve()]
    except KeyError as exc:
        raise IdentityMigrationError(f"Projet sans manifeste pour {path}") from exc


def _new_meeting(existing: list[str]) -> str:
    valid = {value for value in existing if docid.est_valide(value)}
    if len(valid) > 1:
        raise IdentityMigrationError("Plusieurs meeting_id contradictoires dans un groupe")
    return next(iter(valid), docid.nouveau())


def prepare(
    root: Path,
    *,
    confirmed_groups: list[list[str]] | None = None,
) -> IdentityPlan:
    """Prépare une migration sans écriture.

    `confirmed_groups` contient des noms ou IDs de transcripts que l'utilisateur
    a explicitement décidé de réunir. Sans confirmation, chaque transcript
    reçoit sa propre réunion, même si son champ `source` est identique.
    """
    root = Path(root).resolve()
    if not root.exists():
        raise IdentityMigrationError(f"Corpus absent : {root}")
    by_directory, by_project_name = _project_maps(root)

    records: list[dict] = []
    ids: dict[str, dict] = {}
    names: dict[str, dict] = {}
    for path in _active_paths(root):
        meta, body, raw = _read_document(path)
        record = {
            "path": path, "meta": meta, "body": body, "raw": raw,
            "archived": False,
            "project_id": _project_id_for(path, root, by_directory),
        }
        if meta["id"] in ids:
            raise IdentityMigrationError(f"document_id dupliqué : {meta['id']}")
        if path.name in names:
            raise IdentityMigrationError(f"nom de document dupliqué : {path.name}")
        ids[meta["id"]] = record
        names[path.name] = record
        records.append(record)

    trash = root / ".trash"
    manifest_path = trash / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        manifest = {}
    except Exception as exc:  # noqa: BLE001
        raise IdentityMigrationError(f"Manifeste de corbeille illisible : {exc}") from exc
    if trash.exists():
        for path in sorted(trash.glob("*.md")):
            # Les tout premiers backfills excluaient volontairement `.trash` :
            # l'archive reste une source autoritaire et reçoit donc maintenant
            # son ID, sans jamais modifier un ID valide existant.
            meta, body, raw = _read_document(path, allow_missing_id=True)
            info = manifest.get(path.name, {})
            project_id = info.get("project_id") or by_project_name.get(info.get("project"))
            if not docid.est_valide(project_id):
                project_id = by_project_name.get(projects.INBOX_NAME)
            record = {
                "path": path, "meta": meta, "body": body, "raw": raw,
                "archived": True, "project_id": project_id,
            }
            if meta["id"] in ids:
                raise IdentityMigrationError(f"document_id dupliqué : {meta['id']}")
            ids[meta["id"]] = record
            # Le nom d'origine du manifeste est la relation de compatibilité.
            origin = info.get("name") or path.name
            names.setdefault(origin, record)
            records.append(record)

    transcripts = [r for r in records if r["meta"].get("type") != "report"]
    reference = {r["meta"]["id"]: r for r in transcripts}
    reference.update({r["path"].name: r for r in transcripts})

    groups: list[list[dict]] = []
    claimed: set[str] = set()
    for raw_group in confirmed_groups or []:
        members: list[dict] = []
        for ref in raw_group:
            record = reference.get(ref)
            if record is None:
                raise IdentityMigrationError(f"Transcript de groupe introuvable : {ref}")
            identifiant = record["meta"]["id"]
            if identifiant in claimed:
                raise IdentityMigrationError(f"Transcript présent dans deux groupes : {ref}")
            claimed.add(identifiant)
            members.append(record)
        if len(members) < 2:
            raise IdentityMigrationError("Un groupe confirmé doit contenir au moins 2 transcripts")
        if len({r["project_id"] for r in members}) != 1:
            raise IdentityMigrationError("Une réunion confirmée ne peut pas traverser plusieurs projets")
        groups.append(members)
    groups += [[r] for r in transcripts if r["meta"]["id"] not in claimed]

    meeting_by_document: dict[str, str] = {}
    for group in groups:
        meeting_id = _new_meeting([r["meta"].get("meeting_id") for r in group])
        for record in group:
            meeting_by_document[record["meta"]["id"]] = meeting_id

    # Rapports : le lien stable et les deux identités sont hérités de la source.
    source_by_report: dict[str, str] = {}
    for record in records:
        meta = record["meta"]
        if meta.get("type") != "report":
            continue
        source = ids.get(meta.get("source_document_id")) or names.get(meta.get("source_transcript"))
        if source is None or source["meta"].get("type") == "report":
            raise IdentityMigrationError(f"Rapport orphelin : {record['path']}")
        source_id = source["meta"]["id"]
        source_by_report[meta["id"]] = source_id
        record["project_id"] = source["project_id"]
        meeting_by_document[meta["id"]] = meeting_by_document[source_id]

    changes: list[_DocumentChange] = []
    name_to_id: dict[str, str] = {}
    for record in records:
        meta = dict(record["meta"])
        document_id = meta["id"]
        expected_project = record["project_id"]
        if docid.est_valide(meta.get("project_id")) and meta["project_id"] != expected_project:
            raise IdentityMigrationError(f"project_id contradictoire : {record['path']}")
        expected_meeting = meeting_by_document[document_id]
        if docid.est_valide(meta.get("meeting_id")) and meta["meeting_id"] != expected_meeting:
            raise IdentityMigrationError(f"meeting_id contradictoire : {record['path']}")
        meta["meeting_id"] = expected_meeting
        meta["project_id"] = expected_project
        source_id = source_by_report.get(document_id)
        if source_id:
            if docid.est_valide(meta.get("source_document_id")) and meta["source_document_id"] != source_id:
                raise IdentityMigrationError(f"source_document_id contradictoire : {record['path']}")
            meta["source_document_id"] = source_id
        changes.append(_DocumentChange(
            path=record["path"], before_sha256=_sha(record["raw"]),
            after_text=_render(meta, record["body"]), document_id=document_id,
            meeting_id=expected_meeting, project_id=expected_project,
            source_document_id=source_id, archived=record["archived"],
        ))
        name_to_id[record["path"].stem] = document_id
        name_to_id[record["path"].name] = document_id

    # Groupes de même source : visibles, jamais appliqués implicitement.
    by_source: dict[str, list[dict]] = defaultdict(list)
    active_transcripts = [r for r in transcripts if not r["archived"]]
    for record in active_transcripts:
        source = record["meta"].get("source")
        if source:
            by_source[str(source)].append(record)
    confirmed_sets = {
        frozenset(r["meta"]["id"] for r in group) for group in groups if len(group) > 1
    }
    candidates = []
    for source, members in sorted(by_source.items()):
        if len(members) < 2:
            continue
        ordered = sorted(members, key=lambda r: r["path"].name)
        member_ids = [r["meta"]["id"] for r in ordered]
        candidates.append({
            "source": source,
            "documents": member_ids,
            "names": [r["path"].name for r in ordered],
            "confirmed": frozenset(member_ids) in confirmed_sets,
        })

    text_changes: list[_TextChange] = []
    sidecar_path = root / ".voiceprints.json"
    unmapped_sidecar: list[str] = []
    if sidecar_path.exists():
        raw = sidecar_path.read_bytes()
        try:
            sidecar = json.loads(raw.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise IdentityMigrationError(f"Annexe d'empreintes illisible : {exc}") from exc
        migrated: dict = {}
        for key, value in sidecar.items():
            target = key if docid.est_valide(key) else name_to_id.get(key)
            if target is None:
                unmapped_sidecar.append(key)
                target = key  # conservé et signalé, jamais perdu
            if target in migrated and migrated[target] != value:
                raise IdentityMigrationError(f"Collision d'empreintes pour {target}")
            migrated[target] = value
        text_changes.append(_TextChange(
            sidecar_path, _sha(raw), json.dumps(migrated, ensure_ascii=False),
        ))

    if manifest_path.exists():
        raw = manifest_path.read_bytes()
        updated = dict(manifest)
        for archived_name, info in updated.items():
            path = trash / archived_name
            change = next((c for c in changes if c.path == path), None)
            if change:
                info["document_id"] = change.document_id
                info["project_id"] = change.project_id
        text_changes.append(_TextChange(
            manifest_path, _sha(raw), json.dumps(updated, ensure_ascii=False, indent=1),
        ))

    renames: list[_Rename] = []
    unmapped_audio: list[str] = []
    audio_root = root / ".audio"
    if audio_root.exists():
        for source in sorted(p for p in audio_root.rglob("*") if p.is_file()):
            if docid.est_valide(source.stem):
                continue
            target_id = name_to_id.get(source.stem)
            if target_id is None:
                unmapped_audio.append(str(source.relative_to(root)))
                continue
            destination = source.with_name(target_id + source.suffix)
            if destination.exists() and destination != source:
                raise IdentityMigrationError(f"Collision d'archive audio : {destination}")
            renames.append(_Rename(source, destination))

    return IdentityPlan(
        root=root, documents=tuple(changes), text_changes=tuple(text_changes),
        renames=tuple(renames), candidate_groups=tuple(candidates),
        unmapped_sidecar_keys=tuple(unmapped_sidecar),
        unmapped_audio_files=tuple(unmapped_audio),
    )


def _stage(path: Path, text: str) -> Path:
    tmp = path.parent / f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    with tmp.open("x", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    return tmp


def apply(plan: IdentityPlan) -> dict:
    """Applique exactement un plan encore valide ; aucun recalcul implicite."""
    staged: list[tuple[Path, Path]] = []
    try:
        for change in plan.documents:
            if _sha(change.path.read_bytes()) != change.before_sha256:
                raise IdentityMigrationError(f"Document modifié depuis l'aperçu : {change.path}")
            staged.append((_stage(change.path, change.after_text), change.path))
        for change in plan.text_changes:
            current = _sha(change.path.read_bytes()) if change.path.exists() else None
            if current != change.before_sha256:
                raise IdentityMigrationError(f"Annexe modifiée depuis l'aperçu : {change.path}")
            staged.append((_stage(change.path, change.after_text), change.path))
        for rename in plan.renames:
            if not rename.source.exists() or rename.destination.exists():
                raise IdentityMigrationError(
                    f"Archive audio modifiée depuis l'aperçu : {rename.source}"
                )

        for temporary, destination in staged:
            temporary.replace(destination)
        for rename in plan.renames:
            rename.source.replace(rename.destination)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)

    result = plan.summary()
    result["simulation"] = False
    result["documents_changed"] = len(plan.documents)
    return result


def migrate(
    root: Path,
    *,
    simulation: bool = True,
    confirmed_groups: list[list[str]] | None = None,
) -> dict:
    """Façade sûre : simulation par défaut."""
    plan = prepare(root, confirmed_groups=confirmed_groups)
    return plan.summary() if simulation else apply(plan)
