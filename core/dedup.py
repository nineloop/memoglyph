"""Détection de transcripts en doublon + recommandation du meilleur à garder.

Deux signaux de matching :
- **même source** (mêmes ré-exécutions d'un fichier),
- **similarité de contenu** (difflib) pour les quasi-doublons de source différente.

La reco privilégie le transcript le plus « riche » (diarisé, profilé, complet).
"""

from __future__ import annotations

import difflib
import re
import sqlite3

from . import library, sqlite_db
from . import projects as project_store

DB_PATH = library.DB_PATH

_FIELDS = (
    "name, title, source, language, duration, project, project_id, diarization, "
    "profiled, n_speakers, generated_at, mtime, body"
)


def _records(project_id: project_store.Portee = None) -> list[dict]:
    conn = sqlite_db.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        sql = f"SELECT {_FIELDS} FROM transcripts"
        params: list = []
        condition, params = project_store.filtre_sql("project_id", project_id)
        if condition:
            sql += f" WHERE {condition}"
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def _norm(body: str) -> str:
    """Normalise le corps pour comparer le contenu (retire markdown, timestamps)."""
    b = re.sub(r"\*\*[^*]+\*\*", "", body or "")
    b = re.sub(r"\[[0-9:]+\]", "", b)
    b = re.sub(r"[#*_>`\-]", " ", b)
    return " ".join(b.lower().split())[:3000]


def _score(r: dict) -> float:
    """Score de complétude : plus haut = à garder de préférence."""
    s = 0.0
    if r.get("diarization"):
        s += 3
    if r.get("profiled"):
        s += 3
    if r.get("language"):
        s += 1
    s += (r.get("n_speakers") or 0) * 0.2
    s += len(r.get("body") or "") / 10000.0
    s += (r.get("mtime") or 0) / 1e12  # départage : plus récent
    return s


def find_groups(threshold: float = 0.85, project_id: project_store.Portee = None) -> list[dict]:
    """Retourne les groupes de doublons (≥2 membres) avec le membre recommandé.

    `project_id` restreint la détection à un projet (les doublons se cherchent
    naturellement à l'intérieur d'un même projet).
    """
    recs = _records(project_id)
    if len(recs) < 2:
        return []
    norms = {r["name"]: _norm(r["body"]) for r in recs}

    parent = {r["name"]: r["name"] for r in recs}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    n = len(recs)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = recs[i], recs[j]
            if a["source"] and a["source"] == b["source"]:
                union(a["name"], b["name"])
                continue
            ratio = difflib.SequenceMatcher(None, norms[a["name"]], norms[b["name"]]).ratio()
            if ratio >= threshold:
                union(a["name"], b["name"])

    by_root: dict[str, list[dict]] = {}
    for r in recs:
        by_root.setdefault(find(r["name"]), []).append(r)

    groups = []
    for members in by_root.values():
        if len(members) < 2:
            continue
        best = max(members, key=_score)
        reasons = []
        if best.get("diarization"):
            reasons.append("diarisé")
        if best.get("profiled"):
            reasons.append("profilé")
        reason = ("le plus complet — " + " + ".join(reasons)) if reasons else "le plus récent"
        items = [
            {
                "name": m["name"], "title": m["title"], "source": m["source"],
                "language": m["language"], "duration": m["duration"],
                "diarization": bool(m["diarization"]), "profiled": bool(m["profiled"]),
                "n_speakers": m["n_speakers"], "generated_at": m["generated_at"],
                "keep": m["name"] == best["name"],
            }
            for m in members
        ]
        items.sort(key=lambda x: (not x["keep"], x["name"]))
        groups.append({
            "keep": best["name"],
            "reason": reason,
            "count": len(members),
            "source": best["source"],
            "members": items,
        })
    # Les plus gros groupes d'abord.
    groups.sort(key=lambda g: g["count"], reverse=True)
    return groups
