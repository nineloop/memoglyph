"""Étapes d'un traitement : « étape n sur N ».

La progression se déduit des CODES de message (`core/messages.py`), jamais du texte : chaque code
appartient à un groupe, et un traitement a un PLAN, la liste ordonnée des groupes que ses options
demandent (`api/jobs.plan_etapes`). Un code hors table, ou d'un groupe que ce traitement n'a pas
demandé, ne fait ni avancer ni reculer.

Ce module est à part de `messages.py` : celui-ci ne contient que des constructeurs de messages, et
un test exige que chacun d'eux soit couvert par la table de traduction.
"""
from __future__ import annotations

from typing import Optional

_GROUPES_PAR_PREFIXE: tuple[tuple[str, str], ...] = (
    # Du plus précis au plus général : le premier préfixe qui correspond gagne.
    ("step.report.organize", "organisation"),
    ("step.report.write", "redaction"),
    ("step.report", "extraction"),
    ("step.profiling", "profil"),
    ("step.diarization", "locuteurs"),
    ("step.voiceprints", "locuteurs"),
    ("step.markdown", "ecriture"),
    ("step.done", "ecriture"),
    ("step.audio", "transcription"),
    ("step.model", "transcription"),
    ("step.transcription", "transcription"),
    ("step.upload", "transcription"),
    ("step.segments", "transcription"),
    ("step.languageInferred", "transcription"),
)


def groupe_etape(code: Optional[str]) -> Optional[str]:
    """Le groupe d'étape d'un code de message, ou `None` hors table."""
    if not code:
        return None
    for prefixe, groupe in _GROUPES_PAR_PREFIXE:
        if code == prefixe or code.startswith(prefixe + "."):
            return groupe
    return None
