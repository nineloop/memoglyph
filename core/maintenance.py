"""Diagnostics de maintenance bornés à une portée projet.

Tout est dérivé de SQLite ou des archives locales : aucun état autoritaire ne
naît ici. Les requêtes partent toujours de `transcripts`, ce qui empêche une vue
projet de compter des chunks ou des empreintes appartenant à une autre portée.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from . import backfill, dedup, faithful_backfill, library


def _scalar(conn: sqlite3.Connection, sql: str, params: list) -> int:
    try:
        row = conn.execute(sql, params).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0] or 0) if row else 0


def _etat_corpus() -> dict:
    """Le corpus autoritaire permet-il de reconstruire l'index ?

    Retourne `{"ok": bool, "issues": {...}}`. Un corpus non conforme bloque
    TOUTE réindexation de bibliothèque : les suppressions, renommages et
    déplacements de projet cessent alors d'être pris en compte, sans qu'aucun
    message ne l'indique. C'est précisément ce que l'interface doit pouvoir dire.
    """
    try:
        audit = library.audit_corpus()
    except Exception as exc:  # noqa: BLE001 — un diagnostic ne doit pas tomber
        return {"ok": False, "issues": {}, "erreur": str(exc)}
    return {
        "ok": bool(audit.get("ok")),
        # Seulement les catégories non vides : l'interface affiche une liste de
        # problèmes, pas un formulaire de catégories vides.
        "issues": {
            cle: valeur for cle, valeur in (audit.get("issues") or {}).items() if valeur
        },
    }


def status(project_id: Optional[str] = None) -> dict:
    """État des index et retraitements, sans jamais sortir de `project_id`."""
    clause = " WHERE t.project_id = ?" if project_id else ""
    params = [project_id] if project_id else []
    conn = library._conn()
    try:
        documents = _scalar(
            conn, "SELECT COUNT(*) FROM transcripts t" + clause, params,
        )
        diarized = _scalar(
            conn,
            "SELECT COUNT(*) FROM transcripts t"
            + (" WHERE t.project_id = ? AND" if project_id else " WHERE")
            + " t.diarization = 1",
            params,
        )
        semantic_documents = _scalar(
            conn,
            "SELECT COUNT(DISTINCT COALESCE(c.document_id, c.name)) "
            "FROM chunks c JOIN transcripts t ON t.id = c.document_id "
            "OR (c.document_id IS NULL AND t.name = c.name)" + clause,
            params,
        )
        chunks = _scalar(
            conn,
            "SELECT COUNT(*) FROM chunks c JOIN transcripts t "
            "ON t.id = c.document_id OR (c.document_id IS NULL AND t.name = c.name)"
            + clause,
            params,
        )
        voice_documents = _scalar(
            conn,
            "SELECT COUNT(DISTINCT COALESCE(m.document_id, m.name)) "
            "FROM voice_members m JOIN transcripts t ON t.id = m.document_id "
            "OR (m.document_id IS NULL AND t.name = m.name)" + clause,
            params,
        )
        voice_occurrences = _scalar(
            conn,
            "SELECT COUNT(*) FROM voice_members m JOIN transcripts t "
            "ON t.id = m.document_id OR (m.document_id IS NULL AND t.name = m.name)"
            + clause,
            params,
        )
    finally:
        conn.close()

    # Un DIAGNOSTIC ne doit jamais mourir de ce qu'il diagnostique. Ces trois
    # analyses relisent les `.md` : un seul fichier corrompu y lève
    # (`FaithfulBackfillError: Markdown illisible`) et emportait tout l'écran de
    # maintenance en 500 — précisément quand l'utilisateur vient y chercher la
    # cause. Chacune dégrade donc séparément, et le bloc `corpus` reste lisible :
    # c'est lui qui NOMME les fichiers fautifs.
    def _sans_tomber(label: str, analyse, defaut):
        try:
            return analyse()
        except Exception as exc:  # noqa: BLE001
            indisponibles[label] = str(exc)
            return defaut

    indisponibles: dict[str, str] = {}
    eligible = _sans_tomber(
        "backfill", lambda: [path.name for path in backfill.eligible(project_id)], [],
    )
    faithful_plan = _sans_tomber(
        "faithful",
        lambda: faithful_backfill.prepare(library.OUTPUT_DIR, project_id=project_id),
        None,
    )
    duplicates = _sans_tomber(
        "doublons", lambda: dedup.find_groups(project_id=project_id), [],
    )
    return {
        "documents": documents,
        "diarized_documents": diarized,
        "semantic_documents": semantic_documents,
        "semantic_chunks": chunks,
        "voice_documents": voice_documents,
        "voice_occurrences": voice_occurrences,
        "backfill_eligible": eligible,
        "faithful_backfill_eligible": [
            candidate.document.name for candidate in faithful_plan.candidates
        ] if faithful_plan is not None else [],
        "faithful_backfill_missing_originals": list(
            faithful_plan.missing_originals
        ) if faithful_plan is not None else [],
        "duplicate_groups": len(duplicates),
        # État du CORPUS lui-même, pas seulement des index dérivés.
        # `library.reindex()` est tout-ou-rien : un seul .md invalide laisse le
        # corpus entier non réindexé. Ce diagnostic existait déjà
        # (`library.audit_corpus`) mais n'était câblé nulle part — l'échec ne
        # partait qu'en `print` au démarrage, et rien ne le disait à l'écran.
        # Calculé en DIRECT plutôt qu'au démarrage : l'utilisateur peut avoir
        # réparé ou cassé un .md depuis, et un instantané périmé mentirait.
        "corpus": _etat_corpus(),
        # Analyses qui n'ont pas pu aboutir. Une liste vide parce qu'une analyse
        # a échoué n'est PAS une liste vide parce qu'il n'y a rien à signaler :
        # sans ce champ, l'écran afficherait « aucun écart » sur un corpus cassé.
        "analyses_indisponibles": indisponibles,
    }
