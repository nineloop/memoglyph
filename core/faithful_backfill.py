"""Backfill sûr des archives FLAC qui reproduisent l'entrée exacte des modèles.

La préparation est strictement en lecture seule. L'application n'écrit ni les
Markdown ni SQLite et refuse toute source modifiée depuis le plan. Une archive
d'écoute lossy n'est jamais une source admissible : sans original, le document
reste explicitement signalé comme impossible à compléter.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

from . import config, docid, library, media, resources

Progress = Callable[[float, str], None]


class FaithfulBackfillError(RuntimeError):
    """Le backfill ne peut pas garantir une opération additive et fidèle."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class Candidate:
    document: Path
    document_id: str
    source: Path
    destination: Path
    source_size: int
    source_mtime_ns: int

    def summary(self, root: Path) -> dict:
        return {
            "document": str(self.document.relative_to(root)),
            "document_id": self.document_id,
            "source": self.source.name,
            "destination": self.destination.name,
        }


@dataclass(frozen=True)
class FaithfulPlan:
    root: Path
    candidates: tuple[Candidate, ...]
    missing_originals: tuple[str, ...]
    existing: tuple[str, ...]
    document_hashes: tuple[tuple[Path, str], ...]

    def summary(self) -> dict:
        return {
            "simulation": True,
            "root": str(self.root),
            "eligible": [candidate.summary(self.root) for candidate in self.candidates],
            "missing_originals": list(self.missing_originals),
            "already_present": list(self.existing),
        }


def _active_markdown(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*.md")
        if not any(part.startswith(".") for part in path.relative_to(root).parts)
    )


def _default_source_dirs() -> tuple[Path, ...]:
    return config.CACHE_DIR / "uploads", config.ROOT / "inputs", config.CACHE_DIR


def _find_original(
    document_id: str,
    source_name: object,
    *,
    root: Path,
    source_dirs: Sequence[Path],
) -> Optional[Path]:
    retained = media.original_path(document_id, root=root)
    if retained is not None and retained.is_file():
        return retained
    if not isinstance(source_name, str) or not source_name.strip():
        return None
    basename = Path(source_name).name
    for directory in source_dirs:
        candidate = Path(directory) / basename
        if candidate.is_file() and candidate.suffix.lower() in config.SUPPORTED_EXTS:
            return candidate
    return None


def prepare(
    root: Optional[Path] = None,
    *,
    project_id: Optional[str] = None,
    source_dirs: Optional[Sequence[Path]] = None,
) -> FaithfulPlan:
    """Inventorie les archives à créer, sans aucune écriture."""
    root = Path(root or library.OUTPUT_DIR).resolve()
    if not root.exists():
        raise FaithfulBackfillError(f"Corpus absent : {root}")
    directories = tuple(Path(path) for path in (source_dirs or _default_source_dirs()))

    candidates: list[Candidate] = []
    missing: list[str] = []
    existing: list[str] = []
    hashes: list[tuple[Path, str]] = []
    for path in _active_markdown(root):
        raw = path.read_bytes()
        try:
            meta, _body = library._strict_frontmatter(raw.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise FaithfulBackfillError(f"Markdown illisible : {path}: {exc}") from exc
        if meta.get("type") == "report" or meta.get("source_transcript"):
            continue
        if project_id and meta.get("project_id") != project_id:
            continue
        hashes.append((path, hashlib.sha256(raw).hexdigest()))

        document_id = meta.get("id")
        if not docid.est_valide(document_id):
            raise FaithfulBackfillError(f"Identifiant document invalide : {path}")
        destination = media.faithful_path(document_id, root=root)
        if destination.exists():
            existing.append(path.name)
            continue
        source = _find_original(
            document_id, meta.get("source"), root=root, source_dirs=directories,
        )
        if source is None:
            missing.append(path.name)
            continue
        stat = source.stat()
        candidates.append(Candidate(
            document=path,
            document_id=document_id,
            source=source.resolve(),
            destination=destination,
            source_size=stat.st_size,
            source_mtime_ns=stat.st_mtime_ns,
        ))
    return FaithfulPlan(
        root=root,
        candidates=tuple(candidates),
        missing_originals=tuple(missing),
        existing=tuple(existing),
        document_hashes=tuple(hashes),
    )


def _verify_documents(plan: FaithfulPlan) -> None:
    for path, expected in plan.document_hashes:
        if not path.is_file() or _sha256(path) != expected:
            raise FaithfulBackfillError(
                f"Corpus modifié depuis la simulation : {path.relative_to(plan.root)}"
            )


def apply(plan: FaithfulPlan, progress: Optional[Progress] = None) -> dict:
    """Applique un plan préparé, sans écraser ni modifier le corpus Markdown."""
    _verify_documents(plan)
    results: list[dict] = []
    total = len(plan.candidates)
    for index, candidate in enumerate(plan.candidates):
        if progress:
            progress(
                index / max(total, 1),
                f"Archive fidèle : {candidate.document.name} ({index + 1}/{total})…",
            )
        try:
            stat = candidate.source.stat()
            if (
                stat.st_size != candidate.source_size
                or stat.st_mtime_ns != candidate.source_mtime_ns
            ):
                raise FaithfulBackfillError(
                    f"Original modifié depuis la simulation : {candidate.source.name}"
                )
            media.create_faithful(
                candidate.source,
                candidate.destination,
                verify_pcm=True,
                overwrite=False,
            )
            published_stat = candidate.destination.stat()
            final_stat = candidate.source.stat()
            if (
                final_stat.st_size != candidate.source_size
                or final_stat.st_mtime_ns != candidate.source_mtime_ns
            ):
                # Ne retirer que notre propre publication. Un processus tiers
                # peut remplacer le chemin entre la création et ce contrôle.
                try:
                    current_stat = candidate.destination.stat()
                    if (
                        current_stat.st_dev == published_stat.st_dev
                        and current_stat.st_ino == published_stat.st_ino
                    ):
                        candidate.destination.unlink()
                except FileNotFoundError:
                    pass
                raise FaithfulBackfillError(
                    f"Original modifié pendant le traitement : {candidate.source.name}"
                )
            result = {
                "name": candidate.document.name,
                "status": "done",
                "archive": candidate.destination.name,
            }
        except Exception as exc:  # noqa: BLE001 — chaque échec reste visible
            result = {
                "name": candidate.document.name,
                "status": "error",
                "reason": str(exc),
            }
        results.append(result)
        _verify_documents(plan)

    done = sum(result["status"] == "done" for result in results)
    errors = len(results) - done
    if progress:
        progress(1.0, f"Terminé : {done} archive(s), {errors} échec(s).")
    return {
        "done": done,
        "errors": errors,
        "missing_originals": list(plan.missing_originals),
        "results": results,
    }


def run(
    *,
    root: Optional[Path] = None,
    project_id: Optional[str] = None,
    source_dirs: Optional[Sequence[Path]] = None,
    progress: Optional[Progress] = None,
    resource_wait: float = 900.0,
) -> dict:
    plan = prepare(root, project_id=project_id, source_dirs=source_dirs)
    with resources.heavy(wait=resource_wait, label="backfill des archives fidèles"):
        return apply(plan, progress=progress)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=library.OUTPUT_DIR)
    parser.add_argument(
        "--apply", action="store_true",
        help="crée les archives ; sans cette option, la commande simule seulement",
    )
    args = parser.parse_args(argv)
    plan = prepare(args.root)
    result = apply(plan) if args.apply else plan.summary()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
