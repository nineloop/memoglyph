"""Fusion des segments STT avec les tours de parole de la diarisation."""

from __future__ import annotations

from typing import Optional

from .diarization import SpeakerTurn
from .stt_base import Segment


def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    """Durée de chevauchement temporel entre deux intervalles (0 si disjoints)."""
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def _best_speaker(seg: Segment, turns: list[SpeakerTurn]) -> Optional[str]:
    """Locuteur brut au chevauchement temporel maximal, ou None si aucun."""
    best: Optional[str] = None
    best_overlap = 0.0
    for turn in turns:
        ov = _overlap(seg.start, seg.end, turn.start, turn.end)
        if ov > best_overlap:
            best_overlap = ov
            best = turn.speaker
    return best


def merge_speakers(
    segments: list[Segment], turns: list[SpeakerTurn]
) -> tuple[list[Segment], dict[str, str]]:
    """Segments étiquetés « Speaker N » + mapping label brut -> label lisible.

    La numérotation suit l'ordre d'apparition **dans le résultat**, pas celui des
    tours de diarisation. Numéroter d'après les tours était faux : un locuteur
    dont les premiers tours ne remportent aucun segment (chevauchement perdu face
    à un tour plus long) recevait un petit numéro tout en n'apparaissant que bien
    plus tard — d'où un transcript affiché « 1, 2, 3, 4, 6, 7, 5 », qui a l'air
    cassé alors que seule la numérotation l'était.

    Le mapping est **renvoyé** plutôt que recalculé par l'appelant : les
    empreintes vocales sont indexées par ces labels, et deux calculs divergents
    décaleraient les voix dans le registre global.

    Conséquence assumée : un locuteur diarisé qui ne remporte **aucun** segment
    est absent du mapping, donc son empreinte est écartée — l'enregistrer
    reviendrait à référencer un « Speaker N » introuvable dans le `.md`.
    """
    if not turns:
        return segments, {}

    raw = [_best_speaker(seg, turns) for seg in segments]

    label_map: dict[str, str] = {}
    for speaker in raw:
        if speaker is not None and speaker not in label_map:
            label_map[speaker] = f"Speaker {len(label_map) + 1}"

    labeled = [
        Segment(
            start=seg.start,
            end=seg.end,
            text=seg.text,
            speaker=label_map[speaker] if speaker else None,
        )
        for seg, speaker in zip(segments, raw)
    ]
    return labeled, label_map


def assign_speakers(
    segments: list[Segment], turns: list[SpeakerTurn]
) -> list[Segment]:
    """Segments étiquetés seuls. Voir `merge_speakers` pour le mapping."""
    return merge_speakers(segments, turns)[0]
