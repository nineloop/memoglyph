"""Arbitrage d'une ressource rare : la mémoire unifiée.

## Le problème

`MAX_ACTIVE_JOBS` ne bornait que deux chemins — transcription et backfill — parce
que seuls ceux-là passent par le gestionnaire de jobs. Le compte-rendu, le RAG et
la réindexation sémantique sont des endpoints **synchrones** : ils échappaient
complètement à la borne.

Une transcription (MLX + pyannote) pouvait donc tourner pendant qu'un CR chargeait
Qwen 9B et qu'une question RAG en demandait un troisième contexte. Sur 18 Go de
RAM unifiée, c'est le scénario que `config.py` décrit depuis le début comme
rendant la machine instable : swap, latences extrêmes, ou échecs Ollama.

## Le choix

Un seul travail lourd à la fois (`MEMOGLYPH_MAX_HEAVY_TASKS`, défaut 1). Sérialiser
n'est pas une limitation regrettable ici : ces tâches sont déjà limitées par le
GPU et la mémoire, les paralléliser ne les accélère pas — ça les fait échouer.

Le garde vit dans `core/` et non dans `api/` : il protège aussi le CLI, les tests
et un futur agent, pas seulement les requêtes HTTP.

## Réentrance

Impérative. `pipeline.run` prend le jeton, puis appelle `profiling` qui interroge
le LLM ; si le profilage reprenait un jeton, on s'auto-bloquerait. Un compteur par
thread rend l'imbrication sans effet — seule l'entrée la plus externe compte.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

from .config import MAX_HEAVY_TASKS


class ResourcesBusy(RuntimeError):
    """Aucun jeton disponible dans le délai imparti."""


_SEM = threading.BoundedSemaphore(max(1, MAX_HEAVY_TASKS))
_local = threading.local()


def _depth() -> int:
    return getattr(_local, "depth", 0)


@contextmanager
def heavy(*, wait: float = 0.0, label: str = "traitement") -> Iterator[None]:
    """Réserve un créneau de calcul lourd (STT, diarisation, LLM).

    `wait` : secondes d'attente avant d'abandonner. `0` = échoue immédiatement si
    occupé — c'est ce qu'on veut pour une action interactive, où un blocage
    ressemblerait à un plantage. Une tâche de fond, elle, peut attendre.

    Lève `ResourcesBusy` plutôt que de laisser la machine partir en swap : mieux
    vaut un refus explicite qu'une latence de plusieurs minutes inexpliquée.
    """
    if _depth() > 0:  # déjà détenu par ce thread : imbrication sans effet
        _local.depth += 1
        try:
            yield
        finally:
            _local.depth -= 1
        return

    acquired = _SEM.acquire(timeout=wait) if wait > 0 else _SEM.acquire(blocking=False)
    if not acquired:
        raise ResourcesBusy(
            f"Ressources occupées : un autre {label} est en cours. Réessaie dans "
            "un moment — cette machine ne traite qu'une tâche lourde à la fois."
        )
    _local.depth = 1
    try:
        yield
    finally:
        _local.depth = 0
        _SEM.release()


def busy() -> bool:
    """Un travail lourd est-il en cours ? (indicatif, sans réservation)"""
    if _SEM.acquire(blocking=False):
        _SEM.release()
        return False
    return True
