"""Index SQLite des transcripts (couche de données / store).

Les `.md` de `output/` restent la **source de vérité** ; `index.db` est un
index **dérivé et reconstructible** (scan + parsing du front-matter).

Conçu comme une petite interface de store (list / get / reindex / sync) pour
rester swappable (SQLite → Postgres+pgvector le jour du multi-user).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import OUTPUT_DIR
from .docid import est_valide
from . import projects as project_store, sensitivity as _sensibilite, sqlite_db
from .writer_md import slugify

try:
    import yaml
except Exception:  # noqa: BLE001
    yaml = None

# Emplacement canonique et générique de l'index — défini une seule fois dans
# `sqlite_db`. Ne PAS redéfinir un
# nom lié au produit ici : c'est exactement ce qui avait scindé le registre vocal.
DB_PATH = sqlite_db.DB_PATH

# Deux versions distinctes :
# - le schéma décrit les tables/colonnes effectivement installées ;
# - l'index décrit la façon dont les `.md` sont transformés en lignes.
#
# Une évolution de `INDEX_VERSION` force la relecture de TOUS les Markdown,
# même si leur mtime n'a pas changé. La version n'est enregistrée qu'à la fin
# de la transaction de reindexation, après validation du corpus et de la base.
SCHEMA_VERSION = 2
INDEX_VERSION = 5  # v3 : relations document / réunion / projet ; v4 : sensibilité (partage) ;
#                    v5 : titre lisible (titre saisi, sinon nom de la source)
_SCHEMA_COMPONENT = "library_schema"
_INDEX_COMPONENT = "library_index"

# Un **projet** = un sous-dossier de output/. Les .md à la racine appartiennent au
# projet par défaut. Le nom de fichier reste la clé (unique tous projets confondus)
# → les URLs/API ne changent pas ; `project` est une colonne dérivée du chemin.
DEFAULT_PROJECT = project_store.INBOX_NAME
_LEGACY_DEFAULT_PROJECT = "Général"

# Colonnes triables (whitelist anti-injection) -> colonne SQL réelle.
_SORT_COLUMNS = {
    "modified": "mtime",
    "title": "title",
    "duration": "duration",
    "date": "date",
    "language": "language",
    "project": "project",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transcripts (
    name         TEXT PRIMARY KEY,
    -- Identifiant STABLE, lu depuis le front-matter (jamais généré ici : la
    -- base est reconstructible, un id né en base disparaîtrait au reindex).
    id           TEXT,
    title        TEXT,
    date         TEXT,
    duration     TEXT,
    language     TEXT,
    engine       TEXT,
    model        TEXT,
    source       TEXT,
    project      TEXT,
    diarization  INTEGER DEFAULT 0,
    profiled     INTEGER DEFAULT 0,
    n_speakers   INTEGER,
    generated_at TEXT,
    -- Trois colonnes ajoutées pour que la bibliothèque puisse
    -- HIÉRARCHISER. `type` et `source_transcript` étaient déjà écrits dans le
    -- front-matter par core/report.py, mais `_row_from_file` est une liste
    -- blanche qui les JETAIT à l'indexation : l'UI ne les a jamais vus.
    type              TEXT,      -- « report » pour un CR, NULL pour un transcript
    source_transcript TEXT,      -- nom du .md parent, pour un CR
    source_document_id TEXT,     -- identité stable du transcript parent
    meeting_id        TEXT,      -- réunion partagée par versions et CR
    project_id        TEXT,      -- identité du manifeste projet
    sensitivity       TEXT,      -- sensibilité du front-matter, normalisée (partage, Q16)
    warnings          INTEGER DEFAULT 0,  -- nb de réserves de qualité (dérivé)
    size         INTEGER,
    mtime        REAL,
    body         TEXT,
    indexed_at   REAL
);
"""


# Index plein-texte FTS5 (recherche dans le corps). `remove_diacritics 2` rend
# la recherche insensible aux accents (utile en français). Table autonome
# (dupliquant le body) : simple et robuste à notre échelle.
_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts USING fts5(
    name UNINDEXED, title, body,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""


# Versions du schéma et de l'index, par composant. Nom générique, comme celui de
# la base (`sqlite_db`) : un renommage du produit ne touche pas l'index dérivé.
_META_SCHEMA = """
CREATE TABLE IF NOT EXISTS library_meta (
    component  TEXT PRIMARY KEY,
    version    INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
"""


# `CREATE TABLE IF NOT EXISTS` ne touche pas une table existante. La liste est
# donc volontairement COMPLÈTE : une base ancienne ou partiellement migrée est
# ramenée au schéma courant, pas seulement au dernier delta connu.
_MIGRATION_COLUMNS = (
    ("id", "TEXT"),
    ("title", "TEXT"),
    ("date", "TEXT"),
    ("duration", "TEXT"),
    ("language", "TEXT"),
    ("engine", "TEXT"),
    ("model", "TEXT"),
    ("source", "TEXT"),
    ("project", "TEXT"),
    ("diarization", "INTEGER DEFAULT 0"),
    ("profiled", "INTEGER DEFAULT 0"),
    ("n_speakers", "INTEGER"),
    ("generated_at", "TEXT"),
    ("type", "TEXT"),
    ("source_transcript", "TEXT"),
    ("source_document_id", "TEXT"),
    ("meeting_id", "TEXT"),
    ("project_id", "TEXT"),
    ("sensitivity", "TEXT"),
    ("warnings", "INTEGER DEFAULT 0"),
    ("size", "INTEGER"),
    ("mtime", "REAL"),
    ("body", "TEXT"),
    ("indexed_at", "REAL"),
)


# ---------- Connexion ----------

def _meta_version(conn: sqlite3.Connection, component: str) -> int | None:
    row = conn.execute(
        "SELECT version FROM library_meta WHERE component = ?", (component,)
    ).fetchone()
    return int(row[0]) if row is not None else None


def _set_meta_version(conn: sqlite3.Connection, component: str, version: int) -> None:
    conn.execute(
        "INSERT INTO library_meta (component, version, updated_at) "
        "VALUES (?, ?, datetime('now')) "
        "ON CONFLICT(component) DO UPDATE SET "
        "version=excluded.version, updated_at=excluded.updated_at",
        (component, version),
    )


def _conn() -> sqlite3.Connection:
    conn = sqlite_db.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            conn.execute(_SCHEMA)
            conn.execute(_FTS_SCHEMA)
            conn.execute(_META_SCHEMA)
            stored_schema = _meta_version(conn, _SCHEMA_COMPONENT)
            if stored_schema is not None and stored_schema > SCHEMA_VERSION:
                raise sqlite3.DatabaseError(
                    f"Base créée par une version plus récente de Memoglyph "
                    f"(schéma {stored_schema} > {SCHEMA_VERSION})"
                )
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(transcripts)")}
            if "name" not in cols:
                raise sqlite3.DatabaseError("La table transcripts n'a pas de colonne name")
            for nom, decl in _MIGRATION_COLUMNS:
                if nom not in cols:
                    conn.execute(f"ALTER TABLE transcripts ADD COLUMN {nom} {decl}")
            installed = {
                r["name"] for r in conn.execute("PRAGMA table_info(transcripts)")
            }
            expected = {"name", *(nom for nom, _ in _MIGRATION_COLUMNS)}
            missing = expected - installed
            if missing:
                raise sqlite3.DatabaseError(
                    "Migration SQLite incomplète, colonnes absentes : "
                    + ", ".join(sorted(missing))
                )
            # À la création ou à la migration seulement : réécrite à chaque connexion,
            # la version changeait `updated_at`, et toute lecture de l'index écrivait.
            if stored_schema != SCHEMA_VERSION:
                _set_meta_version(conn, _SCHEMA_COMPONENT, SCHEMA_VERSION)
    except Exception:
        conn.close()
        raise
    return conn


# ---------- Projets (un projet = un sous-dossier de output/) ----------

def _project_of(path: Path) -> str:
    """Projet d'un .md, déduit de son chemin (racine de output/ = projet par défaut).

    Le nom vient du manifeste du dossier quand il existe : deux comptes peuvent
    nommer un projet pareil, et le dossier du second porte alors un suffixe
    que l'affichage n'a pas à montrer.
    """
    try:
        rel = Path(path).resolve().relative_to(OUTPUT_DIR.resolve())
    except ValueError:
        return DEFAULT_PROJECT
    if len(rel.parts) <= 1:
        return DEFAULT_PROJECT
    dossier = OUTPUT_DIR / rel.parts[0]
    if project_store.manifest_path(dossier).exists():
        try:
            return project_store.load(dossier).name
        except project_store.ProjectManifestError:
            pass
    return rel.parts[0]


class UnsafeProject(ValueError):
    """Nom de projet refusé (traversée de chemin, dossier d'intendance…)."""


def safe_project_name(project: Optional[str]) -> Optional[str]:
    """Valide un nom de projet. Retourne None pour le projet par défaut.

    `Path(x).name` NE suffit PAS : `Path('..').name == '..'`, donc un projet
    « .. » sortirait de output/. On exige un segment unique, non caché.
    """
    if project is None:
        return None
    name = project.strip().strip("/\\")
    if not name or name == DEFAULT_PROJECT:
        return None
    if "/" in name or "\\" in name or "\x00" in name:
        raise UnsafeProject("Un nom de projet ne peut pas contenir de séparateur de chemin.")
    if name.startswith("."):
        # couvre « . », « .. » et les dossiers d'intendance (.trash, .audio)
        raise UnsafeProject("Un nom de projet ne peut pas commencer par un point.")
    return name


def project_dir(project: Optional[str]) -> Path:
    """Dossier d'un projet (le projet par défaut vit à la racine de output/).

    La destination est vérifiée **par résolution** : elle doit rester strictement
    sous output/ (défense en profondeur, y compris si le nom passait la validation).
    """
    name = safe_project_name(project)
    if name is None:
        return OUTPUT_DIR
    d = OUTPUT_DIR / name
    root = OUTPUT_DIR.resolve()
    if d.resolve() == root or root not in d.resolve().parents:
        raise UnsafeProject(f"Destination hors de output/ : {project!r}")
    return d


def project_dir_by_id(project_id: str) -> Path:
    """Destination correspondant à un manifeste, jamais à une portée de vue."""
    try:
        directory, _project = project_store.resolve(OUTPUT_DIR, project_id)
    except project_store.ProjectManifestError as exc:
        raise UnsafeProject(f"Projet de destination inconnu : {project_id!r}") from exc
    return directory


def _iter_md() -> "list[Path]":
    """Tous les .md de output/ (récursif), hors dossiers d'intendance (.trash, .audio…)."""
    if not OUTPUT_DIR.exists():
        return []
    return [
        p for p in OUTPUT_DIR.rglob("*.md")
        if not any(part.startswith(".") for part in p.relative_to(OUTPUT_DIR).parts)
    ]


def projects() -> list[dict]:
    """Projets connus, identité stable et nombre de réunions.

    `project` reste le libellé historique consommé par l'UI ; `id` et `kind`
    viennent du manifeste autoritaire. Un manifeste absent est rendu visible
    avec `id=None` jusqu'à l'exécution explicite de la migration.
    """
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT project_id, project, "
            "COUNT(DISTINCT COALESCE(NULLIF(meeting_id, ''), NULLIF(id, ''), name)) AS n "
            "FROM transcripts GROUP BY project_id, project"
        )
        counts_by_id: dict[str, int] = {}
        counts_by_name: dict[str, int] = {}
        for row in rows:
            label = row["project"] or DEFAULT_PROJECT
            if label == _LEGACY_DEFAULT_PROJECT:
                label = DEFAULT_PROJECT
            if row["project_id"]:
                counts_by_id[row["project_id"]] = (
                    counts_by_id.get(row["project_id"], 0) + row["n"]
                )
            counts_by_name[label] = counts_by_name.get(label, 0) + row["n"]
    finally:
        conn.close()

    directories = [(OUTPUT_DIR, DEFAULT_PROJECT, "inbox")]
    if OUTPUT_DIR.exists():
        for d in sorted(OUTPUT_DIR.iterdir()):
            if d.is_dir() and not d.name.startswith("."):
                directories.append((d, d.name, "regular"))

    known = []
    for directory, fallback_name, fallback_kind in directories:
        manifest = project_store.manifest_path(directory)
        if manifest.exists():
            project = project_store.load(directory)
            project_id, name, kind, owner = project.id, project.name, project.kind, project.owner
        else:
            project_id, name, kind, owner = None, fallback_name, fallback_kind, None
        known.append({
            "id": project_id,
            "project": name,  # compatibilité API/UI pendant la transition
            "name": name,
            "kind": kind,
            "owner": owner,
            "count": (
                counts_by_id.get(project_id, 0)
                if project_id else counts_by_name.get(fallback_name, 0)
            ),
        })
    return known


def _fts_upsert(conn: sqlite3.Connection, name: str, title: str, body: str) -> None:
    conn.execute("DELETE FROM transcripts_fts WHERE name = ?", (name,))
    conn.execute(
        "INSERT INTO transcripts_fts (name, title, body) VALUES (?, ?, ?)",
        (name, title or "", body or ""),
    )


def _fts_query(q: str) -> str:
    """Transforme une saisie libre en requête FTS5 sûre : chaque mot en préfixe,
    combinés en AND. Échappe les guillemets pour éviter toute injection de syntaxe."""
    words = [w.replace('"', '""') for w in q.split() if w.strip()]
    return " AND ".join(f'"{w}"*' for w in words)


# ---------- Front-matter ----------

def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Sépare le front-matter YAML du corps. Retourne (meta, body)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_raw = text[3:end].strip("\n")
    body = text[end + 4:].lstrip("\n")
    meta: dict = {}
    if yaml is not None:
        try:
            meta = yaml.safe_load(fm_raw) or {}
        except Exception:  # noqa: BLE001
            meta = {}
    else:
        for line in fm_raw.splitlines():
            if ":" in line and not line.startswith(" "):
                k, _, v = line.partition(":")
                meta[k.strip()] = v.strip().strip('"')
    return meta, body


def _strict_frontmatter(text: str) -> tuple[dict, str]:
    """Version stricte réservée aux audits/reconstructions.

    La lecture interactive reste tolérante via `_split_frontmatter`, mais une
    migration ne peut pas prendre un YAML cassé pour un document sans méta et
    déclarer malgré tout l'index à jour.
    """
    if not text.startswith("---"):
        raise ValueError("front-matter absent")
    end = text.find("\n---", 3)
    if end == -1:
        raise ValueError("front-matter non terminé")
    fm_raw = text[3:end].strip("\n")
    body = text[end + 4:].lstrip("\n")
    if yaml is None:
        meta, _ = _split_frontmatter(text)
    else:
        try:
            meta = yaml.safe_load(fm_raw) or {}
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"YAML illisible : {exc}") from exc
    if not isinstance(meta, dict):
        raise ValueError("le front-matter YAML doit être une table")
    return meta, body


def _relative_path(path: Path) -> str:
    try:
        return str(path.relative_to(OUTPUT_DIR))
    except ValueError:
        return path.name


def _project_id_du_dossier(directory: Path) -> Optional[str]:
    """Identité du projet réellement portée par le dossier, ou None.

    None = pas de manifeste ici (dossier racine, sous-dossier libre) : on ne
    peut alors rien affirmer, et affirmer serait pire que se taire.
    """
    from . import projects

    try:
        return projects.load(directory).id
    except Exception:  # noqa: BLE001 — absent ou illisible : indécidable
        return None


def _scan_corpus() -> tuple[dict, list[tuple[Path, dict, str]]]:
    """Parse une fois le corpus et produit un diagnostic déterministe."""
    paths = sorted(_iter_md(), key=lambda p: _relative_path(p))
    parsed: list[tuple[Path, dict, str]] = []
    invalid_yaml: list[str] = []
    missing_ids: list[str] = []
    missing_meeting_ids: list[str] = []
    missing_project_ids: list[str] = []
    misplaced: list[dict] = []
    ids: dict[str, list[str]] = {}
    names: dict[str, list[str]] = {}

    for path in paths:
        rel = _relative_path(path)
        names.setdefault(path.name, []).append(rel)
        try:
            meta, body = _strict_frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            invalid_yaml.append(rel)
            continue
        parsed.append((path, meta, body))
        identifiant = meta.get("id")
        if not est_valide(identifiant):
            missing_ids.append(rel)
        else:
            ids.setdefault(identifiant, []).append(rel)
        if not est_valide(meta.get("meeting_id")):
            missing_meeting_ids.append(rel)
        if not est_valide(meta.get("project_id")):
            missing_project_ids.append(rel)
        else:
            # Le `project_id` du front-matter était DÉCLARATIF : on vérifiait
            # sa syntaxe, jamais qu'il correspond au manifeste du dossier qui
            # contient le fichier. SQLite, le RAG et les vues filtrent ensuite
            # dessus — un `.md` déplacé à la main, ou une réunion déplacée dont
            # le déplacement s'est arrêté au milieu, était donc physiquement
            # dans A et visible dans B. Risque de cohérence aujourd'hui, faille
            # d'isolation le jour où `project_id` devient une frontière d'ACL.
            reel = _project_id_du_dossier(path.parent)
            if reel is not None and reel != meta["project_id"]:
                misplaced.append({
                    "path": rel,
                    "declared_project_id": meta["project_id"],
                    "actual_project_id": reel,
                })

    duplicate_ids = {
        identifiant: sorted(relpaths)
        for identifiant, relpaths in sorted(ids.items())
        if len(relpaths) > 1
    }
    duplicate_names = {
        name: sorted(relpaths)
        for name, relpaths in sorted(names.items())
        if len(relpaths) > 1
    }
    known_names = set(names)
    known_ids = set(ids)
    by_id = {
        meta["id"]: (path, meta)
        for path, meta, _ in parsed if est_valide(meta.get("id"))
    }
    orphan_reports: list[dict] = []
    invalid_report_relations: list[dict] = []
    for path, meta, _ in parsed:
        if meta.get("type") != "report":
            continue
        source_id = meta.get("source_document_id")
        source_name = meta.get("source_transcript")
        if source_id:
            if source_id not in known_ids:
                orphan_reports.append({
                    "path": _relative_path(path), "source_document_id": source_id,
                })
            else:
                source_path, source_meta = by_id[source_id]
                if source_meta.get("type") == "report":
                    invalid_report_relations.append({
                        "path": _relative_path(path),
                        "error": "la source stable est elle-même un rapport",
                    })
                elif (
                    meta.get("meeting_id") != source_meta.get("meeting_id")
                    or meta.get("project_id") != source_meta.get("project_id")
                ):
                    invalid_report_relations.append({
                        "path": _relative_path(path),
                        "source": _relative_path(source_path),
                        "error": "meeting_id/project_id non hérités de la source",
                    })
        elif not source_name or source_name not in known_names:
            orphan_reports.append({
                "path": _relative_path(path), "source_transcript": source_name,
            })
        else:
            invalid_report_relations.append({
                "path": _relative_path(path),
                "error": "source_document_id absent ou invalide",
            })

    issues = {
        "invalid_yaml": sorted(invalid_yaml),
        "missing_or_invalid_ids": sorted(missing_ids),
        "missing_or_invalid_meeting_ids": sorted(missing_meeting_ids),
        "missing_or_invalid_project_ids": sorted(missing_project_ids),
        # Déclare un projet, vit dans un autre. Diagnostic seulement : on ne
        # corrige pas d'autorité, parce qu'on ne sait pas laquelle des deux
        # vérités l'utilisateur voulait — le dossier ou le front-matter.
        "misplaced_documents": sorted(misplaced, key=lambda d: d["path"]),
        "duplicate_ids": duplicate_ids,
        "duplicate_names": duplicate_names,
        "orphan_reports": sorted(orphan_reports, key=lambda item: item["path"]),
        "invalid_report_relations": sorted(
            invalid_report_relations, key=lambda item: item["path"]
        ),
    }
    return ({
        "documents": len(paths),
        "parsed_documents": len(parsed),
        "valid_ids": sum(len(relpaths) for relpaths in ids.values()),
        "unique_ids": len(ids),
        "ok": not any(issues.values()),
        "issues": issues,
    }, parsed)


def audit_corpus() -> dict:
    """Diagnostic public, sans écriture, avant migration/reconstruction."""
    audit, _ = _scan_corpus()
    return audit


class CorpusValidationError(ValueError):
    """Le corpus autoritaire est ambigu : l'index dérivé reste en attente."""

    def __init__(self, issues: dict):
        self.issues = issues
        counts = ", ".join(
            f"{key}={len(value)}" for key, value in issues.items() if value
        )
        super().__init__(f"Corpus invalide, reindexation annulée : {counts}")


# Ce que les générateurs accolent au titre de la réunion dans le titre d'un CR.
_SUFFIXES_TITRE = (" — compte-rendu", " — rapport expérimental")


def _est_un_slug(titre: str, source_stem: str) -> bool:
    """Le pipeline écrit `title: slugify(source.stem)` : un titre resté sous
    cette forme n'a pas été saisi. Hors de ce cas exact, seul un titre déjà
    invariant par `slugify` et à tirets passe pour un slug, comme le repère
    `displayMeetingTitle` côté interface."""
    if source_stem and titre == slugify(source_stem):
        return True
    return "-" in titre and titre == slugify(titre)


def titre_lisible(titre: object, source: object = None, *, nom: object = None) -> str:
    """Titre affichable d'un document : le titre saisi, sinon le nom de la source
    sans son extension, jamais le slug du fichier.

    Un titre de CR (« <titre> — compte-rendu ») garde son suffixe autour de la
    base rendue lisible ; `source` est alors celle du transcript dont il est
    tiré. Le Markdown n'est jamais réécrit : seul ce qui est rendu change.
    """
    texte = str(titre or "").strip()
    suffixe = next((s for s in _SUFFIXES_TITRE if texte.endswith(s)), "")
    base = texte[: len(texte) - len(suffixe)].strip() if suffixe else texte
    source_stem = Path(str(source)).stem.strip() if source else ""
    if base and not _est_un_slug(base, source_stem):
        return texte
    lisible = source_stem or base or (Path(str(nom)).stem if nom else "")
    return f"{lisible}{suffixe}" if lisible else texte


# H1 que les générateurs écrivent en tête d'un CR.
_H1_RAPPORT = re.compile(r"^# (Compte-rendu|Report) — (.+)$")


def h1_lisible(markdown: str, titre: object) -> str:
    """Un CR écrit avant le titre lisible ouvre sur « # Compte-rendu — <slug> ».
    À la LECTURE, ce premier H1 porte `titre` ; le fichier n'est pas réécrit et
    un H1 qui n'est pas un slug (titre saisi, édition à la main) reste tel quel."""
    lignes = markdown.split("\n")
    for i, ligne in enumerate(lignes):
        if not ligne.strip():
            continue
        h1 = _H1_RAPPORT.match(ligne.strip())
        if h1 and titre and _est_un_slug(h1.group(2).strip(), ""):
            lignes[i] = f"# {h1.group(1)} — {titre}"
        break
    return "\n".join(lignes)


def _row_from_parsed(path: Path, meta: dict, body: str,
                     source_parente: Optional[str] = None) -> dict:
    """Construit une ligne depuis un front-matter déjà analysé.

    `source_parente` : média du transcript dont un CR est tiré, pour que son
    titre indexé soit lisible ; le CR n'a pas de `source` à lui."""
    # `speakers` peut être une map (fichiers profilés) ou un int (anciens).
    raw_speakers = meta.get("speakers")
    speakers_map = raw_speakers if isinstance(raw_speakers, dict) else {}
    n_speakers = meta.get("n_speakers")
    if n_speakers is None:
        if speakers_map:
            n_speakers = len(speakers_map)
        elif isinstance(raw_speakers, int):
            n_speakers = raw_speakers
    st = path.stat()
    return {
        "name": path.name,
        "id": meta.get("id"),
        # Titre LISIBLE, dérivé : le front-matter garde le sien, intact.
        "title": titre_lisible(
            meta.get("title"), meta.get("source") or source_parente, nom=path.name
        ),
        "date": str(meta.get("date", "")),
        "duration": meta.get("duration"),
        "language": meta.get("language"),
        "engine": meta.get("engine"),
        "model": meta.get("model"),
        "source": meta.get("source"),
        "project": _project_of(path),
        "diarization": 1 if meta.get("diarization") else 0,
        "profiled": 1 if meta.get("profiled") else 0,
        "n_speakers": n_speakers,
        "generated_at": meta.get("generated_at"),
        "type": meta.get("type"),
        "source_transcript": meta.get("source_transcript"),
        "source_document_id": meta.get("source_document_id"),
        "meeting_id": meta.get("meeting_id"),
        "project_id": meta.get("project_id"),
        "sensitivity": _sensibilite.normaliser(meta.get("sensitivity")),
        # Compte DÉRIVÉ, jamais écrit dans le .md : la liste des clés fait foi
        # ici, et un champ absent ou vide ne compte pas. Sert au seul glyphe « ⚠ N »
        # de la liste — le détail reste dans le .md, seule source de vérité.
        "warnings": sum(
            1 for cle in _CLES_AVERTISSEMENT if meta.get(cle)
        ),
        "size": st.st_size,
        "mtime": st.st_mtime,
        "body": body,
    }


def _row_from_file(path: Path) -> dict:
    """Construit la ligne d'index à partir d'un fichier .md."""
    meta, body = _split_frontmatter(path.read_text(encoding="utf-8"))
    return _row_from_parsed(path, meta, body)


def _source_parente_indexee(
    conn: sqlite3.Connection, meta: dict
) -> Optional[str]:
    """Média du transcript dont un CR est tiré, lu dans l'index (rien pour un
    transcript, ou si le parent n'y est pas)."""
    if meta.get("type") != "report":
        return None
    row = conn.execute(
        "SELECT source FROM transcripts WHERE source IS NOT NULL "
        "AND (id = ? OR (? IS NULL AND name = ?)) LIMIT 1",
        (meta.get("source_document_id"), meta.get("source_document_id"),
         meta.get("source_transcript")),
    ).fetchone()
    return row["source"] if row is not None else None


# Source affichable d'une ligne `t` de l'index : la sienne, ou pour un CR celle
# de son transcript. Elle n'est PAS recopiée dans la colonne `source` du CR :
# `dedup` y verrait une version de plus du même média.
SOURCE_AFFICHEE_SQL = (
    "COALESCE(t.source, (SELECT p.source FROM transcripts p "
    "WHERE p.id = t.source_document_id "
    "OR (t.source_document_id IS NULL AND p.name = t.source_transcript) LIMIT 1))"
)


def _source_affichee(meta: dict) -> Optional[str]:
    """Source affichable d'un document lu sur disque, hors index (corbeille,
    plan d'archivage) : la sienne, ou pour un CR celle de son transcript."""
    if meta.get("source") or meta.get("type") != "report":
        return meta.get("source")
    conn = _conn()
    try:
        return _source_parente_indexee(conn, meta)
    finally:
        conn.close()


# Réserves de qualité écrites par le pipeline (transcript) et par le générateur
# de CR. Leur PRÉSENCE suffit ; leur contenu reste lu depuis le .md.
_CLES_AVERTISSEMENT = (
    "repetition_loops", "removed_credits", "repeated_series",   # transcript
    "unsupported_numbers", "removed_speaker_bindings",          # compte-rendu
    "map_chunks_failed",
    "citation_warnings",
)

_COLUMNS = (
    "name", "id", "title", "date", "duration", "language", "engine", "model", "source",
    "project", "diarization", "profiled", "n_speakers", "generated_at",
    "type", "source_transcript", "source_document_id", "meeting_id", "project_id",
    "sensitivity", "warnings", "size",
    "mtime", "body",
)


def _upsert(conn: sqlite3.Connection, row: dict) -> None:
    cols = ", ".join(_COLUMNS) + ", indexed_at"
    placeholders = ", ".join(["?"] * len(_COLUMNS)) + ", strftime('%s','now')"
    updates = ", ".join(f"{c}=excluded.{c}" for c in _COLUMNS if c != "name")
    conn.execute(
        f"INSERT INTO transcripts ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT(name) DO UPDATE SET {updates}, indexed_at=strftime('%s','now')",
        [row[c] for c in _COLUMNS],
    )


# ---------- Reindex / sync ----------

def reindex() -> dict:
    """Synchronise l'index avec output/.

    Incrémental par mtime tant que `INDEX_VERSION` est courant ; reconstruction
    complète sinon. La version n'est validée qu'après audit du corpus, réécriture
    des lignes, miroir FTS et contrôles d'identité dans la même transaction.
    """
    conn = _conn()
    try:
        audit, parsed = _scan_corpus()
        if not audit["ok"]:
            raise CorpusValidationError(audit["issues"])
        stored_index = _meta_version(conn, _INDEX_COMPONENT)
        if stored_index is not None and stored_index > INDEX_VERSION:
            raise sqlite3.DatabaseError(
                f"Index créé par une version plus récente de Memoglyph "
                f"({stored_index} > {INDEX_VERSION})"
            )
        force = stored_index != INDEX_VERSION
        existing_rows = list(conn.execute("SELECT name, id, mtime FROM transcripts"))
        existing = {r["name"]: r["mtime"] for r in existing_rows}
        existing_by_id = {r["id"]: r["name"] for r in existing_rows if r["id"]}
        seen: set[str] = set()
        added = updated = 0
        # Le média d'un transcript, pour le titre lisible des CR qui en sont
        # tirés : lu dans le corpus analysé, le parent pouvant venir après.
        sources_par_id = {m.get("id"): m.get("source") for _p, m, _b in parsed if m.get("id")}
        sources_par_nom = {p.name: m.get("source") for p, m, _b in parsed}

        def _ligne(path: Path, meta: dict, body: str) -> dict:
            parente = None
            if meta.get("type") == "report":
                parente = (sources_par_id.get(meta.get("source_document_id"))
                           or sources_par_nom.get(meta.get("source_transcript")))
            return _row_from_parsed(path, meta, body, parente)

        for path, meta, body in parsed:  # récursif : un sous-dossier = un projet
            seen.add(path.name)
            mtime = path.stat().st_mtime
            identifiant = meta.get("id")
            ancien_nom = existing_by_id.get(identifiant)
            if path.name not in existing and ancien_nom and ancien_nom != path.name:
                # Un renommage n'est pas une suppression + création : on déplace
                # la clé d'affichage tout en gardant l'identité et les relations.
                conn.execute(
                    "UPDATE transcripts SET name = ? WHERE id = ?",
                    (path.name, identifiant),
                )
                for table in ("chunks", "voice_members"):
                    columns = {
                        r["name"] for r in conn.execute(f"PRAGMA table_info({table})")
                    }
                    if {"name", "document_id"} <= columns:
                        conn.execute(
                            f"UPDATE {table} SET name = ? WHERE document_id = ?",
                            (path.name, identifiant),
                        )
                existing[path.name] = existing.pop(ancien_nom)
                existing_by_id[identifiant] = path.name
            if path.name not in existing:
                _upsert(conn, _ligne(path, meta, body))
                added += 1
            elif force or mtime > (existing[path.name] or 0):
                _upsert(conn, _ligne(path, meta, body))
                updated += 1
        # Supprime les entrées dont le fichier a disparu.
        removed = 0
        for name in set(existing) - seen:
            conn.execute("DELETE FROM transcripts WHERE name=?", (name,))
            removed += 1
        # Reconstruit le miroir FTS depuis la table (peu coûteux à notre échelle).
        conn.execute("DELETE FROM transcripts_fts")
        conn.execute(
            "INSERT INTO transcripts_fts (name, title, body) "
            "SELECT name, title, body FROM transcripts"
        )
        total = conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0]
        if total != audit["documents"]:
            raise sqlite3.DatabaseError(
                f"Index incomplet : {total} lignes pour {audit['documents']} Markdown"
            )
        indexed_ids = conn.execute(
            "SELECT COUNT(id), COUNT(DISTINCT id) FROM transcripts"
        ).fetchone()
        if tuple(indexed_ids) != (audit["documents"], audit["documents"]):
            raise sqlite3.DatabaseError("Identifiants absents ou dupliqués dans l'index")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_transcripts_document_id "
            "ON transcripts(id) WHERE id IS NOT NULL"
        )
        _set_meta_version(conn, _INDEX_COMPONENT, INDEX_VERSION)
        conn.commit()
        return {"added": added, "updated": updated, "removed": removed, "total": total}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def sync_file(path: Path) -> None:
    """Indexe (ou met à jour) un seul fichier — appelé après une transcription.

    Puis revérifie ses faits ECL si ses répliques ont changé (`ecl.synchroniser`) :
    c'est l'un des deux moments, avec `ecl.reindexer`, où un fait peut devenir périmé.
    """
    path = Path(path)
    if not path.exists() or path.suffix != ".md":
        return
    conn = _conn()
    try:
        meta, body = _split_frontmatter(path.read_text(encoding="utf-8"))
        row = _row_from_parsed(path, meta, body, _source_parente_indexee(conn, meta))
        _upsert(conn, row)
        _fts_upsert(conn, row["name"], row["title"], row["body"])
        conn.commit()
    finally:
        conn.close()
    _synchroniser_faits(meta.get("id"), body)


def _synchroniser_faits(document_id: Optional[str], body: str) -> None:
    """Les faits ECL du document suivent sa source ; un échec est dit, l'index du `.md` reste."""
    from . import ecl  # noqa: PLC0415 — `ecl` importe `library`

    try:
        ecl.synchroniser(document_id, body)
    except Exception as exc:  # noqa: BLE001 — l'indexation du document est faite, elle ne recule pas
        print(f"[ecl] revérification des faits de {document_id} impossible : {type(exc).__name__}: {exc}")


# ---------- Requêtes ----------

def _to_item(row: sqlite3.Row) -> dict:
    d = dict(row)
    d.pop("body", None)  # pas de corps dans la liste
    # Contrat API : la colonne SQLite s'appelle `mtime`, mais le client et les
    # autres ressources exposent `modified`. Conserver mtime pour les tris
    # internes de réunion tout en publiant la clé stable attendue.
    d["modified"] = d.get("mtime") or 0
    d["diarization"] = bool(d["diarization"])
    d["profiled"] = bool(d["profiled"])
    # Lignes antérieures aux projets : project NULL == projet par défaut.
    d["project"] = d.get("project") or DEFAULT_PROJECT
    return d


def list_transcripts(
    *,
    q: Optional[str] = None,
    sort: str = "modified",
    order: str = "desc",
    language: Optional[str] = None,
    diarized: Optional[bool] = None,
    profiled: Optional[bool] = None,
    project: Optional[str] = None,
    project_id: project_store.Portee = None,
) -> list[dict]:
    """Liste filtrée/triée. `project_id` est la portée stable préférée : un
    identifiant, ou la collection des projets d'un compte."""
    col = _SORT_COLUMNS.get(sort, "mtime")
    direction = "ASC" if str(order).lower() == "asc" else "DESC"

    where, params = [], []
    if q:
        where.append("(title LIKE ? OR source LIKE ? OR name LIKE ?)")
        like = f"%{q}%"
        params += [like, like, like]
    if project_id is not None:
        condition, valeurs = project_store.filtre_sql("project_id", project_id)
        where.append(condition)
        params += valeurs
    elif project:
        where.append("COALESCE(NULLIF(project, ''), ?) = ?")
        params += [DEFAULT_PROJECT, project]
    if language:
        where.append("language = ?")
        params.append(language)
    if diarized is not None:
        where.append("diarization = ?")
        params.append(1 if diarized else 0)
    if profiled is not None:
        where.append("profiled = ?")
        params.append(1 if profiled else 0)

    sql = "SELECT * FROM transcripts"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {col} {direction}"

    conn = _conn()
    try:
        return _avec_sensibilite(conn, [_to_item(r) for r in conn.execute(sql, params)])
    finally:
        conn.close()


def _avec_sensibilite(conn: sqlite3.Connection, items: list[dict]) -> list[dict]:
    """`sensitivity` de chaque élément : le niveau EFFECTIF, celui de sa réunion
    (`sensibilite_effective`). Le niveau noté à la création reste dans le `.md`."""
    if items:
        documents = _documents_par_reunion(conn)
        reglages = _reglages(documents)
        for item in items:
            item["sensitivity"] = _effective(item.get("sensitivity"), item.get("meeting_id"),
                                             item.get("project_id"), reglages, documents)
    return items


def languages(project_id: project_store.Portee = None) -> list[str]:
    """Langues distinctes présentes dans l'index (pour les filtres UI)."""
    condition, valeurs = project_store.filtre_sql("project_id", project_id)
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT language FROM transcripts WHERE language IS NOT NULL "
            "AND language != ''" + (f" AND {condition}" if condition else "")
            + " ORDER BY language",
            valeurs,
        )
        return [r[0] for r in rows]
    finally:
        conn.close()


# ---------- Réunions (agrégat dérivé des documents) ----------

def _meeting_key(item: dict) -> str:
    meeting_id = item.get("meeting_id")
    if est_valide(meeting_id):
        return meeting_id
    # Rien ne disparaît pour un document legacy, mais la clé annonce qu'elle
    # n'est pas une identité persistante et les routes de mutation la refusent.
    return f"legacy:{item.get('id') or item['name']}"


def _preferred_transcript(transcripts: list[dict]) -> Optional[dict]:
    if not transcripts:
        return None
    return max(
        transcripts,
        key=lambda item: (
            1 if item.get("diarization") else 0,
            1 if item.get("profiled") else 0,
            item.get("mtime") or 0,
        ),
    )


def _meeting_from_items(meeting_id: str, items: list[dict]) -> dict:
    transcripts = [item for item in items if item.get("type") != "report"]
    reports = [item for item in items if item.get("type") == "report"]
    transcripts.sort(key=lambda item: item.get("mtime") or 0, reverse=True)
    reports.sort(key=lambda item: item.get("mtime") or 0, reverse=True)
    preferred = _preferred_transcript(transcripts)
    representative = preferred or (reports[0] if reports else items[0])
    dates = sorted(str(item["date"]) for item in items if item.get("date"))
    generated = sorted(
        str(item["generated_at"]) for item in items if item.get("generated_at")
    )
    reservations = [
        {
            "document_id": item.get("id"),
            "name": item["name"],
            "count": item.get("warnings") or 0,
        }
        for item in items if (item.get("warnings") or 0) > 0
    ]
    return {
        "id": meeting_id,
        "title": representative.get("title"),
        "project_id": representative.get("project_id"),
        "project": representative.get("project") or DEFAULT_PROJECT,
        "source": representative.get("source"),
        "date": dates[0] if dates else "",
        "generated_at": generated[0] if generated else None,
        "modified": max((item.get("mtime") or 0) for item in items),
        "duration": representative.get("duration"),
        "language": representative.get("language"),
        "diarization": any(item.get("diarization") for item in transcripts),
        "profiled": any(item.get("profiled") for item in transcripts),
        "n_speakers": representative.get("n_speakers"),
        "transcripts": transcripts,
        "reports": reports,
        "preferred_transcript": preferred,
        "quality_reservations": reservations,
        # Le niveau de la réunion : chaque élément porte déjà le niveau effectif
        # (`_avec_sensibilite`), le même pour tous ceux d'une réunion identifiée.
        "sensitivity": plus_stricte(*(item.get("sensitivity") for item in items)),
    }


def list_meetings(
    *,
    q: Optional[str] = None,
    sort: str = "modified",
    order: str = "desc",
    language: Optional[str] = None,
    diarized: Optional[bool] = None,
    profiled: Optional[bool] = None,
    project_id: Optional[str] = None,
) -> list[dict]:
    """Sélectionne des réunions, puis renvoie tous leurs documents enfants."""
    items = list_transcripts(project_id=project_id)
    # Une réunion = un `meeting_id` DANS un projet : deux projets (deux comptes) dont des
    # documents portent le même `meeting_id` ont deux réunions, jamais mélangées.
    grouped: dict[tuple, list[dict]] = {}
    for item in items:
        grouped.setdefault((item.get("project_id"), _meeting_key(item)), []).append(item)
    meetings = [
        _meeting_from_items(meeting_id, members)
        for (_projet, meeting_id), members in grouped.items()
    ]

    needle = (q or "").strip().casefold()
    if needle:
        meetings = [
            meeting for meeting in meetings
            if any(
                needle in str(item.get(field) or "").casefold()
                for item in (*meeting["transcripts"], *meeting["reports"])
                for field in ("title", "source", "name")
            )
        ]
    if language:
        meetings = [
            meeting for meeting in meetings
            if any(
                item.get("language") == language
                for item in (*meeting["transcripts"], *meeting["reports"])
            )
        ]
    if diarized is not None:
        meetings = [meeting for meeting in meetings if meeting["diarization"] is diarized]
    if profiled is not None:
        meetings = [meeting for meeting in meetings if meeting["profiled"] is profiled]

    sort_key = sort if sort in {"modified", "title", "duration", "date", "language"} else "modified"
    meetings.sort(
        key=lambda meeting: meeting.get(sort_key) or "",
        reverse=str(order).lower() != "asc",
    )
    return meetings


class ReunionAmbigue(ValueError):
    """Plusieurs projets portent des documents de ce `meeting_id` : sans projet désigné, on
    ne choisit pas, et on ne mélange jamais leurs documents."""


def projets_de_la_reunion(meeting_id: str) -> list[str]:
    """Les projets (indexés) dont des documents portent ce `meeting_id`."""
    conn = _conn()
    try:
        return sorted(r[0] for r in conn.execute(
            "SELECT DISTINCT project_id FROM transcripts WHERE meeting_id = ? AND project_id IS NOT NULL",
            (meeting_id,)))
    finally:
        conn.close()


def reunion_dans_la_portee(meeting_id: str, portee: project_store.Portee) -> Optional[dict]:
    """LA réunion de l'appelant : les documents de ce `meeting_id` dans UN projet de sa
    portée (propriétaire ou partage), tous visibles depuis elle.

    Le document homonyme d'un projet hors de la portée n'en fait jamais partie, et ne la
    bloque pas. None si aucun projet de la portée ne la porte ; `ReunionAmbigue` si plusieurs.
    Une clé qui n'est pas un ULID (nom, identifiant, source ou titre d'un document, comme
    le repli de `get_meeting`) désigne la réunion de CHAQUE document qui lui répond, dans
    son projet : seuls comptent ceux que la portée voit, jamais le premier venu.
    """
    if not est_valide(meeting_id):
        candidates = _reunions_par_cle_libre(meeting_id)
    else:
        candidates = [get_meeting(meeting_id, project_id=p) for p in projets_de_la_reunion(meeting_id)]
    retenues = []
    for reunion in candidates:
        noms = [d["name"] for d in (*(reunion or {}).get("transcripts", []), *(reunion or {}).get("reports", []))]
        if noms and all(visible_in_project(nom, portee) for nom in noms):
            retenues.append(reunion)
    if len(retenues) > 1:
        raise ReunionAmbigue(meeting_id)
    return retenues[0] if retenues else None


def _reunions_par_cle_libre(cle: str) -> list[dict]:
    """Les réunions, chacune dans son projet, des documents qui répondent à une clé libre
    (identifiant, nom, source ou titre ; nom sans `.md` en repli)."""
    if not cle:
        return []
    conn = _conn()
    try:
        lignes = list(conn.execute(
            "SELECT * FROM transcripts WHERE id = ? OR name = ? OR source = ? OR title = ?",
            (cle, cle, cle, cle)))
        if not lignes:
            lignes = list(conn.execute("SELECT * FROM transcripts WHERE name = ?",
                                       (cle if cle.endswith(".md") else f"{cle}.md",)))
        reunions: dict[tuple, dict] = {}
        for ligne in lignes:
            mid, projet = ligne["meeting_id"], ligne["project_id"]
            if (projet, mid or ligne["id"]) in reunions:
                continue
            if est_valide(mid):
                items = [_to_item(r) for r in conn.execute(
                    "SELECT * FROM transcripts WHERE meeting_id = ? AND project_id IS ?", (mid, projet))]
            else:
                items = [_to_item(ligne)]
            items = _avec_sensibilite(conn, items)
            reunions[(projet, mid or ligne["id"])] = _meeting_from_items(mid or ligne["id"], items)
        return list(reunions.values())
    finally:
        conn.close()


def get_meeting(meeting_id: str, project_id: Optional[str] = None) -> Optional[dict]:
    """Lit un agrégat par identité stable depuis l'index reconstructible.

    `project_id` : la seule réunion de ce projet (une réunion homonyme d'un autre projet
    n'en fait pas partie). Sans lui, la réunion si un seul projet la porte ;
    `ReunionAmbigue` si plusieurs : leurs documents ne sont jamais mélangés. Une route
    lit la réunion de l'appelant par `reunion_dans_la_portee`.
    """
    if not meeting_id:
        return None
    conn = _conn()
    try:
        if project_id is not None:
            if not est_valide(meeting_id):
                return None
            items = _avec_sensibilite(conn, [
                _to_item(row) for row in conn.execute(
                    "SELECT * FROM transcripts WHERE meeting_id = ? AND project_id = ?",
                    (meeting_id, project_id))])
            return _meeting_from_items(meeting_id, items) if items else None
        if est_valide(meeting_id):
            items = _avec_sensibilite(conn, [
                _to_item(row)
                for row in conn.execute(
                    "SELECT * FROM transcripts WHERE meeting_id = ?", (meeting_id,),
                )
            ])
            if len({item.get("project_id") for item in items}) > 1:
                raise ReunionAmbigue(meeting_id)
            if items:
                return _meeting_from_items(meeting_id, items)

        # Fallback par document_id, name, source, ou title
        rows = list(conn.execute(
            "SELECT * FROM transcripts WHERE id = ? OR name = ? OR source = ? OR title = ?",
            (meeting_id, meeting_id, meeting_id, meeting_id),
        ))
        if not rows:
            candidate_name = f"{meeting_id}.md" if not meeting_id.endswith(".md") else meeting_id
            rows = list(conn.execute(
                "SELECT * FROM transcripts WHERE name = ?", (candidate_name,),
            ))

        if rows:
            real_meeting_id = rows[0]["meeting_id"] or rows[0]["id"]
            items = _avec_sensibilite(conn, [
                _to_item(row)
                for row in conn.execute(
                    "SELECT * FROM transcripts WHERE meeting_id = ? AND project_id IS ?",
                    (real_meeting_id, rows[0]["project_id"]),
                )
            ])
            return _meeting_from_items(real_meeting_id, items) if items else None
    finally:
        conn.close()
    return None


def archive_plan(name: str) -> list[dict]:
    """Document demandé puis CR qui en dépendent, sans élargir à la réunion.

    Les CR sont placés AVANT leur source : si un archivage s'interrompt, il vaut
    mieux garder un transcript actif sans son CR que laisser un CR actif dont la
    source a disparu du corpus et du RAG.
    """
    detail = get_transcript(name)
    if detail is None:
        return []
    meta = detail.get("meta") or {}

    def compact(item: dict) -> dict:
        return {
            "name": item.get("name"),
            "title": item.get("title") or item.get("name"),
            "type": item.get("type"),
            "document_id": item.get("id"),
        }

    selected = {
        "name": name,
        "title": titre_lisible(meta.get("title"), _source_affichee(meta), nom=name),
        "type": meta.get("type"),
        "id": meta.get("id"),
    }
    if meta.get("type") == "report":
        return [compact(selected)]

    source_id = meta.get("id")
    linked = []
    for item in list_transcripts(project_id=meta.get("project_id")):
        if item.get("type") != "report":
            continue
        report_source_id = item.get("source_document_id")
        if (
            (source_id and report_source_id and report_source_id == source_id)
            or (not report_source_id and item.get("source_transcript") == name)
        ):
            linked.append(compact(item))
    linked.sort(key=lambda item: str(item["name"]))
    return [*linked, compact(selected)]


def move_meeting_to_project_id(
    meeting_id: str, destination_project_id: str, source_project_id: Optional[str] = None,
) -> Optional[Path]:
    """Classe toute une réunion (celle du projet `source_project_id`) en réutilisant la
    mutation atomique éprouvée."""
    meeting = get_meeting(meeting_id, project_id=source_project_id)
    if meeting is None:
        return None
    representative = meeting["preferred_transcript"]
    if representative is None:
        representative = (meeting["reports"] or [None])[0]
    if representative is None:
        return None
    return move_to_project_id(representative["name"], destination_project_id)


# ---------- Recherche plein-texte (FTS5) ----------

# Marqueurs de surlignage : caractères de contrôle STX/ETX, absents des
# transcripts → le frontend les remplace par des <mark> sans risque d'injection.
SNIPPET_START = ""
SNIPPET_END = ""


def search(
    q: str, limit: int = 30, project: Optional[str] = None,
    project_id: project_store.Portee = None,
) -> list[dict]:
    """Recherche plein-texte dans le corps des transcripts. Retourne des
    résultats classés (bm25) avec un extrait surligné et les métadonnées.

    `project=None` = tout le corpus ; sinon restreint au projet.
    """
    q = (q or "").strip()
    match = _fts_query(q)
    if not match:
        return []
    where_project = ""
    extra: list = []
    if project_id is not None:
        condition, extra = project_store.filtre_sql("t.project_id", project_id)
        where_project = f" AND {condition}"
    elif project:
        where_project = " AND COALESCE(NULLIF(t.project, ''), ?) = ?"
        extra = [DEFAULT_PROJECT, project]
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT t.name, t.id AS document_id, t.meeting_id, t.project_id, "
            f"  t.title, {SOURCE_AFFICHEE_SQL} AS source, t.language, t.duration, t.project, "
            "  t.diarization, t.profiled, t.n_speakers, t.date, "
            "  snippet(transcripts_fts, 2, ?, ?, '…', 14) AS snippet, "
            "  bm25(transcripts_fts) AS score "
            "FROM transcripts_fts "
            "JOIN transcripts t ON t.name = transcripts_fts.name "
            "WHERE transcripts_fts MATCH ?" + where_project +
            " ORDER BY bm25(transcripts_fts) LIMIT ?",
            (SNIPPET_START, SNIPPET_END, match, *extra, limit),
        )
        out = []
        for r in rows:
            d = dict(r)
            d["diarization"] = bool(d["diarization"])
            d["profiled"] = bool(d["profiled"])
            d["project"] = d.get("project") or DEFAULT_PROJECT
            out.append(d)
        return out
    except sqlite3.OperationalError:
        return []  # requête FTS invalide → aucun résultat
    finally:
        conn.close()


# ---------- Lecture d'un transcript (source de vérité = le fichier) ----------

def transcript_path(name: str) -> Optional[Path]:
    """Chemin d'un transcript, quel que soit son projet ou son nom/extension."""
    if not name:
        return None

    candidates = [name]
    if not name.endswith(".md"):
        candidates.append(f"{name}.md")

    for safe_name in candidates:
        safe = Path(safe_name).name
        p = OUTPUT_DIR / safe
        if p.exists():
            return p
        for cand in _iter_md():
            if cand.name == safe:
                return cand

    # Recherche dans SQLite si le nom transmis est un ID, source ou titre
    conn = _conn()
    try:
        rows = list(conn.execute(
            "SELECT name FROM transcripts WHERE id = ? OR name = ? OR source = ? OR title = ?",
            (name, name, name, name),
        ))
        for row in rows:
            real_name = row["name"]
            p = OUTPUT_DIR / real_name
            if p.exists():
                return p
            for cand in _iter_md():
                if cand.name == real_name:
                    return cand
    finally:
        conn.close()

    return None


def document_visible_in_project(
    path: Optional[Path], project_id: project_store.Portee,
) -> bool:
    """Un document est-il visible depuis la portée `project_id` ?

    La source de vérité est le DOSSIER, pas le `project_id` du front-matter.
    Ce dernier est DÉCLARATIF : un .md déplacé à la main, ou recopié depuis un
    autre projet, l'emporte avec lui et se rendrait visible depuis sa portée
    d'origine. Le manifeste du dossier, lui, ne ment pas.

    `project_id is None` = portée `all`, élargissement EXPLICITE : les liens
    partagés `?projet=all` doivent continuer de fonctionner.

    Fermeture par défaut : un document dont la portée n'est pas résoluble
    (chemin inconnu, dossier sans manifeste) n'est visible depuis AUCUNE portée
    nommée. Mesuré avant de trancher : sur le corpus réel,
    `missing_or_invalid_project_ids` est vide et aucun document n'est mal placé,
    donc cette fermeture ne rend inaccessible aucun document existant.
    """
    if project_id is None:
        return True
    if path is None:
        return False
    reel = _project_id_du_dossier(path.parent)
    if isinstance(project_id, project_store.PorteeLecture):
        if reel not in project_id.sources:
            return False
        try:
            meta, _ = _split_frontmatter(path.read_text(encoding="utf-8"))
        except OSError:
            return False
        return meta.get("id") in project_id.documents
    return reel is not None and project_store.dans_la_portee(reel, project_id)


def documents_lisibles(
    projets: frozenset, reunions: frozenset, sources_reunions: frozenset,
) -> project_store.PorteeLecture:
    """Les documents qu'ouvrent des partages : projets entiers et réunions.

    `reunions` ne vaut que dans `sources_reunions`, les projets de ceux qui les
    partagent. Q16 : une réunion ultra-confidentielle (`sensibilite_de_reunion`) est
    exclue en entier, comptes-rendus compris, même si elle l'est devenue après
    le partage — un compte-rendu résume le transcript qu'il cache.
    """
    if not projets and not reunions:
        return project_store.PorteeLecture(frozenset(), frozenset())
    cond_p, val_p = project_store.filtre_sql("project_id", frozenset(projets))
    cond_r, val_r = project_store.filtre_sql("meeting_id", frozenset(reunions))
    cond_s, val_s = project_store.filtre_sql("project_id", frozenset(sources_reunions))
    conn = _conn()
    try:
        lignes = list(conn.execute(
            "SELECT id, meeting_id, project_id, sensitivity FROM transcripts "
            f"WHERE {cond_p} OR ({cond_r} AND {cond_s})", [*val_p, *val_r, *val_s],
        ))
        documents = _documents_par_reunion(conn)
    finally:
        conn.close()
    reglages = _reglages(documents)
    retenues = [
        r for r in lignes
        if r["id"] and _effective(r["sensitivity"], r["meeting_id"], r["project_id"], reglages, documents)
        != _sensibilite.ULTRA_CONFIDENTIEL
    ]
    return project_store.PorteeLecture(
        frozenset(r["id"] for r in retenues), frozenset(r["project_id"] for r in retenues),
    )


# ---------- Sensibilité : une propriété de la RÉUNION ----------
#
# Une seule source de vérité par réunion : le registre des réunions, un fichier à la
# racine de la bibliothèque, à côté des `.md` (comme les manifestes des projets et de
# la corbeille), jamais l'index, qui reste dérivé. Le changer vaut pour TOUTES les
# versions de la réunion, transcripts et comptes rendus, sans réécrire aucun `.md` :
# l'ordre des versions (mtime) et leur contenu ne bougent pas.
#
# La réunion est celle d'un PROJET : le réglage est rangé sous `(project_id, meeting_id)`,
# la portée des droits d'accès (un projet a un seul propriétaire ; un partage ouvre un
# projet ou une réunion en lecture seule). Deux comptes, ou deux projets, peuvent porter
# des documents au même `meeting_id` : ils ne partagent jamais de réglage, ni pour écrire
# ni pour lire, et le niveau d'un document ne tient compte que des documents de son projet.
#
# Un réglage retient le niveau ET les documents qu'il couvre : ceux de la réunion au
# moment où il est fait, nommés par leur identifiant stable (`id` du front-matter, qui
# survit à une copie, une restauration ou un déplacement, contrairement au mtime). Un
# document arrivé APRÈS (nouvelle transcription, compte rendu importé, document
# regroupé par `backfill_identity`) ne peut que RELEVER la réunion : marqué plus strict
# à sa création, il impose son niveau, jamais abaissé en silence. Le réglage suivant
# le couvre à son tour, ce qui permet de baisser de nouveau. Rien n'est écrit pour
# relever : le calcul est le même partout, il suffit que chaque surface le lise.
#
# Sans réglage, le niveau d'une réunion est le plus strict de ses documents (le
# `sensitivity` de leur front-matter, noté à leur création). C'est aussi ce que la
# migration inscrit (`migrer_sensibilites`), à la demande seulement. Un document sans
# `meeting_id` (antérieur aux identités) est sa propre réunion : son front-matter reste
# sa seule place.
#
# Formats antérieurs du registre (version 1, rangé par `meeting_id` seul : le niveau
# seul, ou `{niveau, documents}`) : toujours lus, chaque entrée valant dans chaque projet
# où vivent les documents qu'elle couvre (tous ceux de la réunion pour le niveau seul) ;
# la migration les réécrit par projet.

REGISTRE_SENSIBILITE = ".memoglyph-sensibilite.json"
_REGISTRE_VERSION = 2
_VERSION_PAR_REUNION = 1   # rangé par `meeting_id` seul : lu, réécrit par la migration
_REGISTRE_LOCK = threading.RLock()
_registre_lu: dict = {"cle": None, "portees": {}, "anciens": {}}
# Du plus ouvert au plus strict : `SENSIBILITES` va du plus sensible au plus ouvert.
_RANG_SENSIBILITE = {niveau: rang for rang, niveau in enumerate(reversed(_sensibilite.SENSIBILITES))}

# La clé d'un réglage : la réunion dans son projet.
Cle = tuple[Optional[str], str]


class RegistreSensibiliteIllisible(ValueError):
    """Le registre des réunions est illisible ou porte une valeur inconnue.

    Jamais ramené au défaut : une réunion relevée par sa propriétaire y redescendrait
    en silence. La règle d'envoi lit cette erreur comme un refus (`sensitivity`).
    """


class _Reglage:
    """Le réglage d'une réunion : son niveau, et les documents qu'il couvre (`None` : une
    entrée au niveau seul, qui couvre tous les documents présents)."""

    __slots__ = ("niveau", "documents")

    def __init__(self, niveau: str, documents: Optional[frozenset]) -> None:
        self.niveau, self.documents = niveau, documents

    def couvre(self, document_id: object) -> bool:
        return self.documents is None or document_id in self.documents

    def __eq__(self, autre: object) -> bool:
        return isinstance(autre, _Reglage) and (self.niveau, self.documents) == (autre.niveau, autre.documents)

    def ecrit(self) -> object:
        if self.documents is None:
            return self.niveau
        return {"niveau": self.niveau, "documents": sorted(self.documents)}


def registre_sensibilite_path() -> Path:
    return OUTPUT_DIR / REGISTRE_SENSIBILITE


def plus_stricte(*niveaux: object) -> str:
    """Le niveau le plus strict parmi `niveaux` (une valeur inconnue ou absente vaut le défaut)."""
    return max((_sensibilite.normaliser(n) for n in niveaux), key=_RANG_SENSIBILITE.__getitem__,
               default=_sensibilite.SENSIBILITE_DEFAUT)


def _reglage_lu(valeur: object) -> Optional[_Reglage]:
    if isinstance(valeur, str):   # le niveau seul
        return _Reglage(valeur, None) if valeur in _sensibilite.SENSIBILITES else None
    if not isinstance(valeur, dict) or valeur.get("niveau") not in _sensibilite.SENSIBILITES:
        return None
    documents = valeur.get("documents")
    if not isinstance(documents, list) or not all(isinstance(d, str) and d for d in documents):
        return None
    return _Reglage(valeur["niveau"], frozenset(documents))


def _cle_valide(cle: object) -> bool:
    return isinstance(cle, str) and bool(cle)


def _registre_brut() -> tuple[dict[Cle, _Reglage], dict[str, _Reglage]]:
    """Le registre tel qu'il est écrit : les réglages par projet, et les entrées d'un
    format antérieur rangées par `meeting_id` seul. Relu seulement s'il a changé."""
    chemin = registre_sensibilite_path()
    try:
        etat = chemin.stat()
    except FileNotFoundError:
        return {}, {}
    cle = (str(chemin), etat.st_mtime_ns, etat.st_size)
    with _REGISTRE_LOCK:
        if _registre_lu["cle"] != cle:
            try:
                brut = json.loads(chemin.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise RegistreSensibiliteIllisible(f"{chemin} : {exc}") from exc
            reunions = brut.get("reunions") if isinstance(brut, dict) else None
            if not isinstance(reunions, dict):
                raise RegistreSensibiliteIllisible(f"{chemin} : contenu invalide")
            portees: dict[Cle, _Reglage] = {}
            anciens: dict[str, _Reglage] = {}
            if brut.get("version") == _REGISTRE_VERSION:
                for projet, par_reunion in reunions.items():
                    if not _cle_valide(projet) or not isinstance(par_reunion, dict):
                        raise RegistreSensibiliteIllisible(f"{chemin} : contenu invalide")
                    for meeting_id, valeur in par_reunion.items():
                        reglage = _reglage_lu(valeur)
                        if not _cle_valide(meeting_id) or reglage is None:
                            raise RegistreSensibiliteIllisible(f"{chemin} : contenu invalide")
                        portees[(projet, meeting_id)] = reglage
                anciennes = brut.get("par_reunion", {})
            elif brut.get("version") == _VERSION_PAR_REUNION:
                anciennes = reunions
            else:
                raise RegistreSensibiliteIllisible(f"{chemin} : version inconnue")
            if not isinstance(anciennes, dict):
                raise RegistreSensibiliteIllisible(f"{chemin} : contenu invalide")
            for meeting_id, valeur in anciennes.items():
                reglage = _reglage_lu(valeur)
                if not _cle_valide(meeting_id) or reglage is None:
                    raise RegistreSensibiliteIllisible(f"{chemin} : contenu invalide")
                anciens[meeting_id] = reglage
            _registre_lu.update(cle=cle, portees=portees, anciens=anciens)
        return dict(_registre_lu["portees"]), dict(_registre_lu["anciens"])


def _documents_par_reunion(conn: sqlite3.Connection,
                           meeting_id: Optional[str] = None) -> dict[Cle, list[tuple[str, str]]]:
    """Les documents indexés, `(id, niveau)`, de chaque réunion dans son projet ; ceux de la
    seule `meeting_id` (tous projets confondus) si elle est donnée."""
    sql = ("SELECT id, meeting_id, project_id, sensitivity FROM transcripts "
           "WHERE meeting_id IS NOT NULL AND meeting_id != ''")
    valeurs: tuple = ()
    if meeting_id is not None:
        sql += " AND meeting_id = ?"
        valeurs = (meeting_id,)
    documents: dict[Cle, list[tuple[str, str]]] = {}
    for r in conn.execute(sql, valeurs):
        documents.setdefault((r["project_id"], r["meeting_id"]), []).append((r["id"], r["sensitivity"]))
    return documents


def _anciens_en_portee(anciens: dict[str, _Reglage], documents: dict) -> dict[Cle, _Reglage]:
    """Chaque entrée rangée par `meeting_id` seul, dans chaque projet où vivent les documents
    qu'elle couvre (tous ceux de la réunion, pour le niveau seul), au niveau de l'entrée."""
    en_portee: dict[Cle, _Reglage] = {}
    for (projet, meeting_id), lignes in documents.items():
        ancien = anciens.get(meeting_id)
        if ancien is None:
            continue
        if ancien.documents is None:
            en_portee[(projet, meeting_id)] = _Reglage(ancien.niveau, None)
        elif couverts := frozenset(d for d, _n in lignes if d in ancien.documents):
            en_portee[(projet, meeting_id)] = _Reglage(ancien.niveau, couverts)
    return en_portee


def _reglages(documents: dict) -> dict[Cle, _Reglage]:
    """Les réglages qui valent pour ces documents : ceux du registre par projet, et les
    entrées d'un format antérieur placées dans leur projet (`_anciens_en_portee`)."""
    portees, anciens = _registre_brut()
    return {**_anciens_en_portee(anciens, documents), **portees} if anciens else portees


def lire_registre_sensibilite() -> dict[Cle, str]:
    """Le niveau réglé de chaque réunion, `{(project_id, meeting_id): niveau}`, entrées d'un
    format antérieur placées dans leur projet ; vide sans registre."""
    conn = _conn()
    try:
        documents = _documents_par_reunion(conn)
    finally:
        conn.close()
    return {cle: reglage.niveau for cle, reglage in _reglages(documents).items()}


def _ecrire_registre(portees: dict[Cle, _Reglage], anciens: dict[str, _Reglage]) -> None:
    """Réécriture atomique du registre (fichier temporaire puis `replace`), sous verrou.

    Les entrées d'un format antérieur restent écrites telles quelles : seule la
    migration les range par projet.
    """
    chemin = registre_sensibilite_path()
    chemin.parent.mkdir(parents=True, exist_ok=True)
    reunions: dict[str, dict] = {}
    for (projet, meeting_id), reglage in sorted(portees.items(), key=lambda e: (str(e[0][0]), e[0][1])):
        reunions.setdefault(projet, {})[meeting_id] = reglage.ecrit()
    contenu: dict = {"version": _REGISTRE_VERSION, "reunions": reunions}
    if anciens:
        contenu["par_reunion"] = {mid: r.ecrit() for mid, r in sorted(anciens.items())}
    texte = json.dumps(contenu, ensure_ascii=False, indent=1) + "\n"
    tmp = chemin.parent / f".{chemin.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with tmp.open("x", encoding="utf-8") as handle:
            handle.write(texte)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(chemin)
    finally:
        tmp.unlink(missing_ok=True)


def _niveau_de(cle: Cle, reglages: dict, documents: dict, repli: object = None) -> str:
    """Le niveau de la réunion `cle` (réunion dans son projet) : son réglage, relevé par tout
    document qu'il ne couvre pas ; sans réglage, le plus strict de ses documents (et de
    `repli`, un document pas encore indexé). Avec un réglage, `repli` ne compte pas : on ne
    sait pas s'il le couvre."""
    lignes = documents.get(cle, [])
    reglage = reglages.get(cle)
    if reglage is None:
        return plus_stricte(repli, *(niveau for _id, niveau in lignes))
    return plus_stricte(reglage.niveau, *(niveau for d, niveau in lignes if not reglage.couvre(d)))


def _niveau(meeting_id: str, project_id: Optional[str], reglages: dict, documents: dict,
            repli: object = None) -> str:
    """Le niveau de la réunion `meeting_id` du projet `project_id`. Sans projet (appelant
    qui ne le connaît pas), le plus strict de la réunion dans TOUS les projets où elle
    vit : jamais un réglage d'un projet n'abaisse le document d'un autre."""
    if project_id is not None:
        return _niveau_de((project_id, meeting_id), reglages, documents, repli)
    cles = [cle for cle in documents if cle[1] == meeting_id]
    return plus_stricte(repli, *(_niveau_de(cle, reglages, documents) for cle in cles))


def _effective(propre: object, meeting_id: object, project_id: Optional[str],
               reglages: dict, documents: dict) -> str:
    """Le niveau d'un document indexé : celui de sa réunion dans son projet, ou le sien
    s'il n'a pas de réunion."""
    if isinstance(meeting_id, str) and meeting_id:
        return _niveau(meeting_id, project_id, reglages, documents, repli=propre)
    return _sensibilite.normaliser(propre)


def sensibilite_de_reunion(meeting_id: str, project_id: Optional[str] = None,
                           repli: object = None) -> str:
    """LE niveau d'une réunion dans son projet, relu maintenant (`_niveau`)."""
    conn = _conn()
    try:
        documents = _documents_par_reunion(conn, meeting_id)
    finally:
        conn.close()
    return _niveau(meeting_id, project_id, _reglages(documents), documents, repli=repli)


def reunion_ultra_confidentielle(meeting_id: str, project_id: Optional[str] = None) -> bool:
    """La réunion de ce projet est-elle ultra-confidentielle (Q16) ?"""
    return sensibilite_de_reunion(meeting_id, project_id) == _sensibilite.ULTRA_CONFIDENTIEL


def sensibilite_effective(sensibilite: object, meeting_id: object,
                          project_id: Optional[str] = None) -> str:
    """LA sensibilité d'un document pour la règle d'envoi et l'affichage : celle de sa
    réunion dans son projet (`sensibilite_de_reunion`), ou la sienne s'il n'a pas de réunion.

    Un seul calcul pour le compte rendu, l'ECL, l'Assistant, le MCP, l'API et l'écran (T1) :
    une réunion peut porter plusieurs transcripts (`backfill_identity` les regroupe) et
    plusieurs comptes rendus ; tous disent le niveau de la réunion. `sensibilite` (le
    front-matter du document) ne compte que pour une réunion sans réglage ; un document
    indexé après le réglage compte par l'index. `project_id` : le projet du document ;
    sans lui, le calcul ferme (le plus strict des projets où vit la réunion).
    """
    if isinstance(meeting_id, str) and meeting_id:
        return sensibilite_de_reunion(meeting_id, project_id, repli=sensibilite)
    return _sensibilite.normaliser(sensibilite)


def sensibilite_du_document(document_id: str) -> Optional[str]:
    """La sensibilité effective d'un document indexé, relue maintenant ; None s'il est inconnu.

    Le résolveur de `sensitivity.controler` : la façade relit la sensibilité à chaque
    appel au fournisseur (T3), comme l'autorisation du compte. Un changement du niveau
    de la réunion vaut donc dès l'appel suivant, travail en cours compris.
    """
    conn = _conn()
    try:
        ligne = conn.execute("SELECT sensitivity, meeting_id, project_id FROM transcripts "
                             "WHERE id = ? LIMIT 1", (document_id,)).fetchone()
    finally:
        conn.close()
    if ligne is None:
        return None
    return sensibilite_effective(ligne["sensitivity"], ligne["meeting_id"], ligne["project_id"])


def fixer_sensibilite_reunion(meeting_id: str, project_id: Optional[str], valeur: object) -> str:
    """Fixe le niveau de la réunion `meeting_id` du projet `project_id`, pour toutes ses
    versions dans ce projet, en une écriture du registre. Aucune autre portée n'est touchée.

    Le réglage couvre les documents indexés de cette réunion dans ce projet à cet instant ;
    un document arrivé ensuite ne pourra que le relever. Aucun `.md` n'est réécrit : ni
    contenu, ni mtime, donc ni l'ordre des versions. Rend le niveau relu après l'écriture.
    """
    if not project_id:
        raise ValueError("Une réunion se règle dans son projet : project_id requis")
    valeur = _sensibilite.normaliser(valeur)
    cle = (project_id, meeting_id)
    with _REGISTRE_LOCK:
        conn = _conn()
        try:
            documents = _documents_par_reunion(conn, meeting_id)
        finally:
            conn.close()
        couverts = frozenset(d for d, _n in documents.get(cle, []))
        portees, anciens = _registre_brut()
        nouveau = _Reglage(valeur, couverts)
        if portees.get(cle) != nouveau:
            portees[cle] = nouveau
            _ecrire_registre(portees, anciens)
    return sensibilite_de_reunion(meeting_id, project_id)


def _deplacer_reglage(meeting_id: str, depuis: Optional[str], vers: Optional[str]) -> None:
    """La réunion change de projet : son réglage la suit. Une réunion homonyme déjà réglée
    dans la destination garde le plus strict des deux niveaux, et couvre leurs documents."""
    if depuis == vers:
        return
    with _REGISTRE_LOCK:
        portees, anciens = _registre_brut()
        reglage = portees.pop((depuis, meeting_id), None)
        if reglage is None:
            return
        present = portees.get((vers, meeting_id))
        if present is not None:
            documents = (None if reglage.documents is None or present.documents is None
                         else reglage.documents | present.documents)
            reglage = _Reglage(plus_stricte(reglage.niveau, present.niveau), documents)
        portees[(vers, meeting_id)] = reglage
        _ecrire_registre(portees, anciens)


def migrer_sensibilites(*, appliquer: bool = False) -> dict:
    """Inscrit au registre chaque réunion indexée (dans son projet) qui n'y est pas, au plus
    strict de ses documents, et range par projet les entrées d'un format antérieur (rangées
    par `meeting_id` seul) : chacune dans chaque projet où vivent les documents qu'elle
    couvre, au niveau de l'entrée, avec la liste de ces documents (tous ceux de la réunion
    dans ce projet, pour une entrée au niveau seul).

    Simulation par défaut. Idempotente : un niveau réglé ne change jamais, et un registre
    déjà complet n'est pas réécrit. Ne touche aucun `.md`. Lit l'index tel quel : à
    l'appelant de le resynchroniser d'abord (`reindex`). Ne change aucun niveau lu.
    """
    conn = _conn()
    try:
        documents = _documents_par_reunion(conn)
    finally:
        conn.close()
    with _REGISTRE_LOCK:
        portees, anciens = _registre_brut()
        places = {cle: r for cle, r in _anciens_en_portee(anciens, documents).items()
                  if cle not in portees and cle[0]}
        reglees = {**places, **portees}
        inscrites = {cle: plus_stricte(*(n for _d, n in lignes))
                     for cle, lignes in sorted(documents.items(), key=lambda e: (str(e[0][0]), e[0][1]))
                     if cle not in reglees and cle[0]}
        # Une entrée au niveau seul couvrait tous les documents présents : ils sont nommés.
        completees = {cle: _Reglage(r.niveau, frozenset(d for d, _n in documents[cle]))
                      for cle, r in {**places, **portees}.items()
                      if r.documents is None and cle in documents}
        # Une entrée antérieure sans aucun document indexé (corbeille…) reste telle quelle.
        restants = {mid: r for mid, r in anciens.items() if not any(m == mid for _p, m in documents)}
        nouveau = {**portees, **places, **completees,
                   **{cle: _Reglage(niveau, frozenset(d for d, _n in documents[cle]))
                      for cle, niveau in inscrites.items()}}
        change = nouveau != portees or restants != anciens
        if appliquer and change:
            _ecrire_registre(nouveau, restants)
    return {"inscrites": inscrites, "completees": sorted(completees, key=lambda c: (str(c[0]), c[1])),
            "deja_reglees": sum(1 for cle in documents if cle in reglees), "appliquee": appliquer}


def proprietaire_du_document(name: str) -> Optional[str]:
    """Le compte propriétaire du projet qui CONTIENT ce document, ou None.

    Lu dans le manifeste du dossier, comme la portée (`document_visible_in_project`),
    jamais dans le `project_id` du front-matter : un `.md` recopié dans un autre
    dossier n'emporte pas l'autorisation du compte d'origine. Projet d'avant les
    comptes, dossier sans manifeste, document introuvable : None.
    """
    chemin = transcript_path(name)
    if chemin is None:
        return None
    try:
        return project_store.load(chemin.parent).owner
    except Exception:  # noqa: BLE001 — absent ou illisible : aucun propriétaire
        return None


def envoi_du_document(name: str, meta: Optional[dict] = None) -> _sensibilite.Envoi:
    """Ce que la règle d'envoi doit savoir d'un document : sa sensibilité et son propriétaire.

    `meta` : le front-matter déjà lu par l'appelant ; sinon relu ici. La sensibilité
    est la sensibilité EFFECTIVE (`sensibilite_effective`), celle que les routes
    contrôlent et que la façade relit à chaque appel (`sensibilite_du_document`).
    """
    if meta is None:
        meta = (get_transcript(name) or {}).get("meta") or {}
    return _sensibilite.Envoi(sensibilite=sensibilite_effective(meta.get("sensitivity"),
                                                                meta.get("meeting_id"),
                                                                meta.get("project_id")),
                              proprietaire=proprietaire_du_document(name),
                              document_id=meta.get("id"))


def visible_in_project(name: str, project_id: project_store.Portee) -> bool:
    """Idem, à partir de n'importe laquelle des clés acceptées par
    `transcript_path` (nom, document_id, source, titre).

    Résoudre le chemin AVANT de comparer est le fond de l'affaire : un garde qui
    ne filtrerait que sur le nom laisserait les trois autres portes ouvertes.
    """
    if project_id is None:
        return True
    return document_visible_in_project(transcript_path(name), project_id)


def move_to_project(name: str, project: Optional[str]) -> Optional[Path]:
    """Déplace la réunion entière et réécrit son `project_id` autoritaire.

    Déplacer seulement le transcript casserait l'héritage de ses CR. La route
    historique reste nommée « transcript », mais l'unité cohérente est désormais
    la réunion.
    """
    return _move_to_project_dir(name, project_dir(project))


def move_to_project_id(name: str, destination_project_id: str) -> Optional[Path]:
    """Déplace une réunion vers une identité projet réelle."""
    return _move_to_project_dir(name, project_dir_by_id(destination_project_id))


def _move_to_project_dir(name: str, dest_dir: Path) -> Optional[Path]:
    src = transcript_path(name)
    if src is None:
        return None
    try:
        target_project = project_store.load(dest_dir)
    except project_store.ProjectManifestError:
        raise

    source_meta, _ = _strict_frontmatter(src.read_text(encoding="utf-8"))
    meeting_id = source_meta.get("meeting_id")
    members: list[Path] = []
    if est_valide(meeting_id):
        # Les membres de la réunion DANS le projet (le dossier) du document adressé :
        # jamais le document homonyme d'un autre projet ou d'un autre compte.
        origine = _project_id_du_dossier(src.parent)
        projets: dict[Path, Optional[str]] = {}
        for candidate in _iter_md():
            meta, _ = _strict_frontmatter(candidate.read_text(encoding="utf-8"))
            if meta.get("meeting_id") != meeting_id:
                continue
            if candidate.parent not in projets:
                projets[candidate.parent] = _project_id_du_dossier(candidate.parent)
            if (projets[candidate.parent] == origine if origine is not None
                    else candidate.parent == src.parent):
                members.append(candidate)
    else:  # compatibilité contrôlée avec un document d'avant la migration
        members = [src]

    destinations = {member: dest_dir / member.name for member in members}
    requested = destinations[src]
    if all(dest.resolve() == member.resolve() for member, dest in destinations.items()):
        return src
    if any(dest.exists() and dest.resolve() != member.resolve()
           for member, dest in destinations.items()):
        return None

    # Prépare toutes les nouvelles versions avant la première mutation.
    from . import writer_md
    staged: list[tuple[Path, Path]] = []
    try:
        for member in members:
            text = member.read_text(encoding="utf-8")
            meta, body = _strict_frontmatter(text)
            meta["project_id"] = target_project.id
            rendered = writer_md._yaml_frontmatter(meta) + "\n\n" + body.lstrip("\n")
            tmp = member.parent / (
                f".{member.name}.{os.getpid()}.{threading.get_ident()}.project.tmp"
            )
            with tmp.open("x", encoding="utf-8") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            staged.append((tmp, member))
        # ---- Point de non-retour, et il doit être ANNULABLE ----
        #
        # Deux mutations se suivent : réécrire le front-matter de chaque
        # membre, puis les renommer vers le dossier de destination. Une erreur
        # au milieu de la seconde laissait le premier fichier déplacé et le
        # second non — les DEUX portant déjà le nouveau `project_id`. Le membre
        # resté sur place déclarait alors un projet et vivait dans un autre :
        # exactement l'incohérence que `audit_corpus` signale désormais, et une
        # faille d'isolation le jour où `project_id` deviendra une frontière
        # d'ACL.
        #
        # On garde donc de quoi revenir en arrière sur les deux étapes. Un
        # `rename` dans le même système de fichiers est atomique par fichier :
        # défaire est fiable, même si refaire ne l'est pas.
        originaux = {member: member.read_bytes() for member in members}
        reecrits: list[Path] = []
        deplaces: list[tuple[Path, Path]] = []
        try:
            for tmp, member in staged:
                tmp.replace(member)
                reecrits.append(member)
            dest_dir.mkdir(parents=True, exist_ok=True)
            for member, dest in destinations.items():
                if member.resolve() != dest.resolve():
                    member.rename(dest)
                    deplaces.append((member, dest))
        except Exception:
            # Ordre inverse : on remet d'abord les fichiers à leur place, puis
            # on restaure leur contenu. L'inverse écrirait dans un chemin qui
            # n'existe plus.
            for origine, arrivee in reversed(deplaces):
                try:
                    arrivee.rename(origine)
                except OSError as echec:  # noqa: PERF203
                    # On ne peut pas se taire ici : le corpus est à moitié
                    # déplacé et seul l'humain peut trancher.
                    print(f"[projet] ANNULATION INCOMPLÈTE, {arrivee} n'a pas pu "
                          f"revenir vers {origine} : {echec}")
            for membre in reecrits:
                try:
                    membre.write_bytes(originaux[membre])
                except OSError as echec:  # noqa: PERF203
                    print(f"[projet] ANNULATION INCOMPLÈTE, contenu de {membre} "
                          f"non restauré : {echec}")
            raise
        reindex()  # reconnaît chaque renommage par document_id
        if est_valide(meeting_id):
            _deplacer_reglage(meeting_id, _project_id_du_dossier(src.parent), target_project.id)
        return requested
    finally:
        for tmp, _ in staged:
            tmp.unlink(missing_ok=True)


def projet_du_document(name: str) -> Optional[str]:
    """L'identité du projet dont le DOSSIER contient ce document (manifeste), ou None.

    La portée des droits d'accès (`document_visible_in_project`), jamais le `project_id`
    déclaratif du front-matter.
    """
    chemin = transcript_path(name)
    return _project_id_du_dossier(chemin.parent) if chemin is not None else None


def set_sensitivity(name: str, valeur: object) -> Optional[str]:
    """Fixe la sensibilité de la RÉUNION de ce document, pour toutes ses versions.

    `fixer_sensibilite_reunion` : une écriture du registre, aucun `.md` réécrit. Un
    document sans `meeting_id` est sa propre réunion : son front-matter est sa seule
    place, réécrit ici comme avant les identités. MÉTADONNÉE seulement : aucun contenu,
    aucune exécution modifiés. Retourne le niveau appliqué (normalisé), ou `None` si le
    document est introuvable.
    """
    cible = transcript_path(name)
    if cible is None:
        return None
    meeting_id = _strict_frontmatter(cible.read_text(encoding="utf-8"))[0].get("meeting_id")
    if isinstance(meeting_id, str) and meeting_id:
        # La réunion du PROJET qui contient le document (son dossier, comme les droits
        # d'accès), jamais une réunion homonyme d'un autre projet.
        return fixer_sensibilite_reunion(meeting_id, projet_du_document(name), valeur)
    valeur = _sensibilite.normaliser(valeur)
    if ecrire_champ_meta(cible, "sensitivity", valeur):
        sync_file(cible)
    return valeur


def ecrire_champ_meta(cible: Path, cle: str, valeur: object) -> bool:
    """Fixe un champ du front-matter de `cible` ; `None` le retire.

    Réécriture atomique (fichier temporaire puis `replace` dans le même dossier),
    corps intact. Rend vrai si le fichier a changé. N'indexe rien : à l'appelant
    de resynchroniser si le document l'est déjà.
    """
    from . import writer_md  # noqa: PLC0415
    meta, body = _strict_frontmatter(cible.read_text(encoding="utf-8"))
    if meta.get(cle) == valeur:
        return False
    if valeur is None:
        meta.pop(cle, None)
    else:
        meta[cle] = valeur
    rendered = writer_md._yaml_frontmatter(meta) + "\n\n" + body.lstrip("\n")
    tmp = cible.parent / f".{cible.name}.{os.getpid()}.{threading.get_ident()}.{cle}.tmp"
    with tmp.open("x", encoding="utf-8") as handle:
        handle.write(rendered)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(cible)
    return True


def forget(name: str) -> None:
    """Retire un transcript des index dérivés (table + miroir FTS). Ne touche à
    aucun fichier — partagé par l'archivage et la suppression définitive."""
    conn = _conn()
    try:
        conn.execute("DELETE FROM transcripts WHERE name = ?", (name,))
        conn.execute("DELETE FROM transcripts_fts WHERE name = ?", (name,))
        conn.commit()
    finally:
        conn.close()


# ---------- Corbeille ----------
#
# Un fichier archivé peut être renommé (`foo.md` → `foo.1.md`) : sans registre,
# retrouver « les copies de foo.md » se faisait au préfixe, ce qui emportait
# `foobar.md`. Le manifeste garde le lien nom archivé → transcript d'origine.

TRASH_DIR_NAME = ".trash"
_MANIFEST = "manifest.json"
# Même raison que pour l'annexe d'empreintes : l'écriture atomique protège d'un
# plantage, pas d'une course entre deux archivages simultanés.
_MANIFEST_LOCK = threading.RLock()


def _trash_dir() -> Path:
    return OUTPUT_DIR / TRASH_DIR_NAME


def _read_manifest() -> dict:
    try:
        return json.loads((_trash_dir() / _MANIFEST).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — absent ou corrompu = manifeste vide
        return {}


def _write_manifest(data: dict) -> None:
    d = _trash_dir()
    d.mkdir(parents=True, exist_ok=True)
    # Nom temporaire unique : un nom fixe créerait une course sur replace().
    tmp = d / f"{_MANIFEST}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(d / _MANIFEST)  # écriture atomique
    finally:
        tmp.unlink(missing_ok=True)


def _legacy_archive_names(name: str) -> "list[str]":
    """Noms d'archive plausibles pour les fichiers d'AVANT le manifeste :
    exactement `foo.md` ou `foo.<n>.md` — surtout pas `foobar.md`."""
    p = Path(name)
    pattern = re.compile(rf"^{re.escape(p.stem)}(\.\d+)?{re.escape(p.suffix)}$")
    if not _trash_dir().exists():
        return []
    return [f.name for f in _trash_dir().glob("*.md") if pattern.match(f.name)]


def archived_names_for(name: str) -> "list[str]":
    """Fichiers de la corbeille correspondant EXACTEMENT à ce transcript."""
    manifest = _read_manifest()

    def _original(archive_key: str) -> str:
        """« foo.2.md » -> « foo.md » : le nom de fichier, reconstructible."""
        tige = Path(archive_key).stem
        base, _, suffixe = tige.rpartition(".")
        if base and suffixe.isdigit():
            tige = base
        return f"{tige}.md"

    cible = name if name.endswith(".md") else f"{name}.md"
    # Le champ `name` du manifeste est la CHAÎNE D'APPEL de l'archivage (titre,
    # stem…), pas forcément un nom de fichier : un document archivé par titre
    # puis purgé par nom de fichier n'était pas retrouvé, et sa copie de
    # corbeille survivait à une purge annoncée complète. On accepte donc aussi
    # la correspondance sur le nom reconstruit depuis la clé d'archive.
    known = [
        arch for arch, meta in manifest.items()
        if meta.get("name") == name or _original(arch) == cible
    ]
    unknown = [n for n in _legacy_archive_names(name) if n not in manifest]
    return sorted({*known, *unknown})


def trash_names() -> set:
    """Noms de transcripts présents en corbeille (pour ne pas réutiliser leur clé)."""
    manifest = _read_manifest()
    out = {meta.get("name") for meta in manifest.values() if meta.get("name")}
    if _trash_dir().exists():
        out |= {p.name for p in _trash_dir().glob("*.md")}
    return out


def archive(name: str) -> bool:
    """Archive un transcript (déplace vers output/.trash/, récupérable) et purge
    son entrée de l'index FTS. Retourne True si effectué."""
    p = transcript_path(name)
    if p is None:
        return False
    project = _project_of(p)
    meta, _ = _split_frontmatter(p.read_text(encoding="utf-8"))
    document_id = meta.get("id") if est_valide(meta.get("id")) else None
    project_id = meta.get("project_id") if est_valide(meta.get("project_id")) else None
    trash = _trash_dir()
    trash.mkdir(parents=True, exist_ok=True)
    dest = trash / p.name
    i = 1
    while dest.exists():
        dest = trash / f"{p.stem}.{i}{p.suffix}"
        i += 1
    p.rename(dest)

    # Le manifeste conserve l'identité : quel transcript, dans quel projet.
    with _MANIFEST_LOCK:
        manifest = _read_manifest()
        manifest[dest.name] = {
            "name": name,
            "project": project,
            "document_id": document_id,
            "project_id": project_id,
            "meeting_id": meta.get("meeting_id"),
            "title": meta.get("title"),
            "type": meta.get("type"),
            "source_transcript": meta.get("source_transcript"),
            "source_document_id": meta.get("source_document_id"),
            "archived_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        }
        _write_manifest(manifest)

    # `forget` supprime par NOM DE FICHIER (`WHERE name = ?`). Lui passer la
    # chaîne d'appel — qui peut être un titre ou un stem — ne supprimait rien :
    # la ligne d'index survivait à l'archivage, et cette ligne fantôme faisait
    # ensuite passer le document pour vivant lors de la purge de sa corbeille.
    forget(p.name)
    return True


def trash_items() -> list[dict]:
    """Contenu de la corbeille (transcripts archivés, encore récupérables).

    `name` = nom du fichier archivé ; `origin`/`project` = d'où il vient
    (manifeste), ce qui rend une restauration possible.
    """
    trash = _trash_dir()
    if not trash.exists():
        return []
    manifest = _read_manifest()
    out = []
    for p in sorted(trash.glob("*.md")):
        st = p.stat()
        manifest_meta = manifest.get(p.name, {})
        try:
            document_meta, _ = _split_frontmatter(p.read_text(encoding="utf-8"))
        except OSError:
            document_meta = {}
        source = _source_affichee({**document_meta, **{
            cle: manifest_meta[cle]
            for cle in ("type", "source_document_id", "source_transcript")
            if manifest_meta.get(cle)
        }})
        out.append({
            "name": p.name,
            "origin": manifest_meta.get("name", p.name),
            "project": manifest_meta.get("project"),
            "document_id": (
                manifest_meta.get("document_id") or document_meta.get("id")
            ),
            "project_id": (
                manifest_meta.get("project_id") or document_meta.get("project_id")
            ),
            "meeting_id": (
                manifest_meta.get("meeting_id") or document_meta.get("meeting_id")
            ),
            "title": titre_lisible(
                manifest_meta.get("title") or document_meta.get("title"),
                source, nom=manifest_meta.get("name", p.name),
            ),
            "source": source,
            "type": manifest_meta.get("type") or document_meta.get("type"),
            "source_transcript": (
                manifest_meta.get("source_transcript")
                or document_meta.get("source_transcript")
            ),
            "source_document_id": (
                manifest_meta.get("source_document_id")
                or document_meta.get("source_document_id")
            ),
            "archived_at": manifest_meta.get("archived_at"),
            "size": st.st_size,
            "modified": st.st_mtime,
        })
    return out


def forget_archived(archived_name: str) -> None:
    """Retire une entrée du manifeste (après suppression définitive du fichier)."""
    with _MANIFEST_LOCK:
        manifest = _read_manifest()
        if manifest.pop(archived_name, None) is not None:
            _write_manifest(manifest)


def get_transcript(name: str) -> Optional[dict]:
    """Contenu complet + méta d'un transcript, lu depuis le fichier (toujours à jour)."""
    p = transcript_path(name)
    if p is None:
        return None
    text = p.read_text(encoding="utf-8")
    meta, body = _split_frontmatter(text)
    return {"name": p.name, "meta": meta, "body": body, "content": text}


# Branché à l'import, comme le résolveur d'autorisation de `api/auth.py` : le module
# de la règle n'importe pas la bibliothèque.
_sensibilite.enregistrer_resolveur_sensibilite(sensibilite_du_document)
