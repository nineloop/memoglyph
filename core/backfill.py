"""Backfill des empreintes vocales sur les transcripts diarisés antérieurs.

Les transcripts produits avant la signature vocale n'ont pas d'empreintes. Ce
module **re-diarise** leur audio (archive conservée, sinon fichier d'origine),
aligne les empreintes fraîches sur les « Speaker N » DÉJÀ présents dans le `.md`
(par recouvrement temporel des timestamps), les enregistre dans le stockage
annexe puis réindexe les voix.

Le `.md` n'est PAS modifié : on ne renumérote jamais les locuteurs.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Optional

from . import config, library, media, resources, voiceprint
from .config import CACHE_DIR, ROOT

Progress = Callable[[float, str], None]

# Ligne diarisée du corps : « **Speaker 1** [00:01:23] texte… » (timestamp optionnel).
_LINE = re.compile(r"^\*\*(Speaker\s+\d+)\*\*\s*(?:\[(\d{1,2}:\d{2}(?::\d{2})?)\])?")


def _ts_to_sec(ts: str) -> float:
    parts = [int(x) for x in ts.split(":")]
    if len(parts) == 3:
        h, m, s = parts
    else:
        h, m, s = 0, parts[0], parts[1]
    return h * 3600 + m * 60 + s


def _parse_intervals(body: str) -> tuple[dict[str, list[tuple[float, float]]], bool]:
    """{Speaker N -> [(start, end)]} depuis les lignes horodatées du corps.

    Chaque tour finit au début du tour suivant. Retourne aussi si des timestamps
    ont été trouvés (sinon l'alignement temporel est impossible).
    """
    marks: list[tuple[str, float]] = []
    for line in body.splitlines():
        m = _LINE.match(line.strip())
        if m and m.group(2):
            marks.append((m.group(1), _ts_to_sec(m.group(2))))
    intervals: dict[str, list[tuple[float, float]]] = {}
    for i, (spk, start) in enumerate(marks):
        end = marks[i + 1][1] if i + 1 < len(marks) else start + 10.0
        if end > start:
            intervals.setdefault(spk, []).append((start, end))
    return intervals, bool(marks)


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _align(diar, intervals: dict[str, list[tuple[float, float]]]) -> dict[str, list[float]]:
    """Mappe les empreintes fraîches (labels bruts) sur les « Speaker N » du .md.

    Chaque label brut est attribué au Speaker N avec lequel il a le plus de
    recouvrement temporel ; chaque Speaker N garde le label brut dominant.
    """
    # Recouvrement total (label brut -> Speaker N).
    raw_best: dict[str, tuple[Optional[str], float]] = {}
    for raw in diar.embeddings:
        best_spk, best_ov = None, 0.0
        raw_turns = [(t.start, t.end) for t in diar.turns if t.speaker == raw]
        for spk, ivs in intervals.items():
            ov = sum(_overlap(rs, re_, s, e) for rs, re_ in raw_turns for s, e in ivs)
            if ov > best_ov:
                best_spk, best_ov = spk, ov
        raw_best[raw] = (best_spk, best_ov)

    # Chaque Speaker N récupère le label brut dominant qui le vise.
    embeddings: dict[str, list[float]] = {}
    for spk in intervals:
        cands = [(raw, ov) for raw, (bspk, ov) in raw_best.items() if bspk == spk]
        if cands:
            raw = max(cands, key=lambda c: c[1])[0]
            embeddings[spk] = diar.embeddings[raw]
    return embeddings


def _find_source_audio(source: Optional[str], transcript_name: str = "") -> Optional[Path]:
    """Localise l'audio d'un transcript : ORIGINAL d'abord, archive en repli.

    L'ordre compte, et l'inverse était le nôtre. L'archive de `output/.audio/`
    n'est pas une copie : elle est ré-encodée en mono AAC 48 kbps (voir
    `media.keep_audio`) — un format pensé pour l'ÉCOUTE dans le navigateur, pas
    pour le retraitement.

    Rejoué depuis l'archive, le pipeline complet trouve MOINS de locuteurs que
    depuis l'original : deux voix y fusionnent.
    Or ce chemin sert à recalculer les empreintes vocales, et le registre de voix
    est le seul magasin qui traverse les fichiers. Une empreinte issue d'un
    fusionnement de deux voix y contaminerait tous les fichiers suivants.

    L'archive reste le repli — les uploads sont effacés après traitement, donc
    c'est souvent le seul audio qui subsiste. Mieux vaut une empreinte imparfaite
    que pas d'empreinte du tout ; mais quand l'original est là, il gagne.
    """
    if source:
        for base in (CACHE_DIR / "uploads", ROOT / "inputs", CACHE_DIR):
            cand = base / source
            if cand.exists():
                return cand
    if transcript_name:
        fidele = media.faithful_path(transcript_name)
        if fidele.exists():
            return fidele
        archived = media.audio_path(transcript_name)
        if archived.exists():
            return archived
    return None


def eligible(project_id: Optional[str] = None) -> list[Path]:
    """Transcripts diarisés, sans empreintes, dont l'audio source est retrouvé."""
    out: list[Path] = []
    for p in sorted(library._iter_md(), key=lambda x: x.name):  # récursif (projets)
        meta, _ = library._split_frontmatter(p.read_text(encoding="utf-8"))
        if project_id and meta.get("project_id") != project_id:
            continue
        if not meta.get("diarization"):
            continue
        if voiceprint.load_embeddings(p.name) or isinstance(
            meta.get("speaker_embeddings"), dict
        ):
            continue  # empreintes déjà connues (annexe ou ancien front-matter)
        if _find_source_audio(meta.get("source"), p.name):
            out.append(p)
    return out


def backfill_one(path: Path, *, pipe=None) -> dict:
    """Re-diarise, aligne, injecte les empreintes d'un transcript. Retourne un statut."""
    meta, body = library._split_frontmatter(path.read_text(encoding="utf-8"))
    src = _find_source_audio(meta.get("source"), path.name)
    if src is None:
        return {"name": path.name, "status": "skipped", "reason": "audio source introuvable"}

    intervals, has_ts = _parse_intervals(body)
    if not has_ts:
        return {"name": path.name, "status": "skipped", "reason": "pas de timestamps (alignement impossible)"}

    wav = media.to_wav16k(src)
    try:
        from .diarization import diarize
        diar = diarize(wav, config.HF_TOKEN, pipe=pipe)
    finally:
        wav.unlink(missing_ok=True)

    if not diar.embeddings:
        return {"name": path.name, "status": "skipped", "reason": "aucune empreinte renvoyée"}

    embeddings = _align(diar, intervals)
    if not embeddings:
        return {"name": path.name, "status": "skipped", "reason": "alignement vide"}

    voiceprint.save_embeddings(path.name, embeddings)  # fichier annexe, .md intact
    library.sync_file(path)
    voiceprint.index_name(path.name)
    return {
        "name": path.name, "status": "done",
        "speakers": len(embeddings), "detected": len(diar.embeddings),
    }


def backfill_all(
    progress: Optional[Progress] = None, project_id: Optional[str] = None,
    resource_wait: float = 900.0,
) -> dict:
    """Backfill de tous les transcripts éligibles (pipeline pyannote chargé 1 fois)."""
    with resources.heavy(wait=resource_wait, label="backfill des voix"):
        return _backfill_all(progress=progress, project_id=project_id)


def _backfill_all(
    progress: Optional[Progress] = None, project_id: Optional[str] = None,
) -> dict:
    targets = eligible(project_id)
    if not targets:
        if progress:
            progress(1.0, "Aucun transcript à traiter.")
        return {"done": 0, "skipped": 0, "results": []}

    from .diarization import load_pipeline
    pipe = load_pipeline(config.HF_TOKEN)

    results, done, skipped = [], 0, 0
    for i, path in enumerate(targets):
        if progress:
            progress(i / len(targets), f"Re-diarisation : {path.name} ({i + 1}/{len(targets)})…")
        try:
            r = backfill_one(path, pipe=pipe)
        except Exception as exc:  # noqa: BLE001
            r = {"name": path.name, "status": "error", "reason": str(exc)}
        results.append(r)
        done += r["status"] == "done"
        skipped += r["status"] != "done"
    if progress:
        progress(1.0, f"Terminé : {done} traité(s), {skipped} ignoré(s).")
    return {"done": done, "skipped": skipped, "results": results}
