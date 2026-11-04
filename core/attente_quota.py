"""L'attente d'un quota atteint (429) chez un fournisseur nommé : une pause bornée, dite dans la progression (chantier 006, H et H2).

Un petit palier gratuit se vide en une minute (8K jetons par minute chez Groq pour le modèle essayé le 4/11) : sans pause,
une tranche sur trois d'un compte rendu se perdait. Le moteur nommé (`llm_nomme`) attend donc le délai annoncé puis réessaie,
mais seulement DANS un traitement qui a ouvert ce contexte (`ouvrir`, ou `suivre` qui s'occupe aussi de la progression) : il a
une progression où dire l'attente. L'Assistant (`/api/ask`), qui n'en a pas, garde la panne immédiate. Aucun autre fournisseur
ne lit ce module.

Les bornes (décisions du 4/11, 16:59 puis 18:10) : jusqu'à `REESSAIS_MAX` réessais par appel ; un délai annoncé de plus de 60 s
n'est pas attendu ; l'attente cumulée d'un TRAITEMENT est plafonnée à 300 s. Un contexte ouvert DANS un traitement déjà ouvert
(la transcription fait son profilage puis son compte rendu dans le même fil) partage le budget du premier : le plafond est celui
du traitement, pas celui de chaque étape. Aucun des délais n'est documenté par les services : `Retry-After` l'est
(`research.md` O8, G5), pas de délai par défaut ; 20 s est un repli choisi.

La pause passe par `dormir`, le seul point de remplacement : les tests le remplacent (`tests/conftest.py` fait échouer tout
test qui dormirait pour de vrai).
"""
from __future__ import annotations

import contextlib
import contextvars
import math
import threading
import time
from typing import Any, Callable, Iterator, Optional

DELAI_DEFAUT_S = 20.0       # repli quand `Retry-After` manque ou n'est pas un nombre de secondes
DELAI_MAX_S = 60.0          # un délai annoncé au-delà n'est pas attendu
CUMUL_MAX_S = 300.0         # attente totale permise à un traitement
REESSAIS_MAX = 2            # réessais par appel refusé (H : 1 ; H2, décision du 4/11 18:10 : 2)


def dormir(secondes: float) -> None:
    """Le point de remplacement de la pause."""
    time.sleep(secondes)


class _Budget:
    """Les secondes déjà attendues par UN traitement, partagées par tous les contextes emboîtés qu'il ouvre."""

    __slots__ = ("cumul_s", "_verrou")

    def __init__(self) -> None:
        self.cumul_s = 0.0
        self._verrou = threading.Lock()

    def reserver(self, delai_s: float) -> bool:
        """Compte `delai_s` dans le cumul si le plafond le permet (borne incluse)."""
        with self._verrou:
            if self.cumul_s + delai_s > CUMUL_MAX_S:
                return False
            self.cumul_s += delai_s
            return True


class Attente:
    """L'état d'attente d'un contexte : qui prévenir avant la pause, qui prévenir après, et le budget du traitement."""

    __slots__ = ("notifier", "reprise", "budget")

    def __init__(self, notifier: Optional[Callable[[float], None]] = None, reprise: Optional[Callable[[], None]] = None,
                 budget: Optional[_Budget] = None) -> None:
        self.notifier = notifier
        self.reprise = reprise
        self.budget = budget or _Budget()

    @property
    def cumul_s(self) -> float:
        return self.budget.cumul_s

    def reserver(self, delai_s: float) -> bool:
        return self.budget.reserver(delai_s)


_courante: contextvars.ContextVar[Optional[Attente]] = contextvars.ContextVar("attente_quota", default=None)


@contextlib.contextmanager
def ouvrir(notifier: Optional[Callable[[float], None]] = None,
           reprise: Optional[Callable[[], None]] = None) -> Iterator[Attente]:
    """Ouvre l'attente d'un traitement (à poser dans le fil qui exécute le traitement).

    Ouvert DANS un traitement déjà ouvert, il en partage le budget et n'ajoute que ses propres `notifier` et `reprise`.
    """
    parent = _courante.get()
    attente = Attente(notifier, reprise, parent.budget if parent is not None else None)
    jeton = _courante.set(attente)
    try:
        yield attente
    finally:
        _courante.reset(jeton)


@contextlib.contextmanager
def suivre(progress: Optional[Callable[[float, Any], None]]) -> Iterator[Callable[[float, Any], None]]:
    """`ouvrir` pour un traitement qui a une progression `progress(fraction, message)`. Rend la progression ENVELOPPÉE.

    À passer au travail à la place de `progress` : l'enveloppe retient la dernière étape réelle (fraction et message). Une pause
    se dit à cette fraction ; une fois l'attente finie, cette dernière étape est republiée, pour qu'un message de pause périmé
    ne reste pas affiché. Les messages de pause ne passent pas par l'enveloppe : deux pauses de suite republient la vraie étape.
    """
    derniere: dict[str, Any] = {"fraction": 0.0, "message": None}

    def enveloppe(fraction: float, message: Any) -> None:
        derniere["fraction"], derniere["message"] = fraction, message
        if progress is not None:
            progress(fraction, message)

    def dire_la_pause(secondes: float) -> None:
        if progress is not None:
            from . import messages  # noqa: PLC0415 — module sans dépendance, importé à l'usage
            progress(derniere["fraction"], messages.pause_quota_fournisseur(math.ceil(secondes)))

    def republier() -> None:
        if progress is not None and derniere["message"] is not None:
            progress(derniere["fraction"], derniere["message"])

    with ouvrir(dire_la_pause, republier):
        yield enveloppe


def delai_annonce(entete: Optional[str]) -> float:
    """Le délai en secondes que dit `Retry-After`, sinon le repli. Une date, un texte, un négatif valent « absent »."""
    try:
        delai = float(str(entete).strip())
    except (TypeError, ValueError):
        return DELAI_DEFAUT_S
    if not math.isfinite(delai) or delai < 0:
        return DELAI_DEFAUT_S
    return delai


def attendre_avant_reessai(entete_retry_after: Optional[str]) -> bool:
    """Attend si un traitement est ouvert et que les bornes le permettent. Rend vrai si l'attente a eu lieu (on réessaie)."""
    attente = _courante.get()
    if attente is None:
        return False
    delai = delai_annonce(entete_retry_after)
    if delai > DELAI_MAX_S or not attente.reserver(delai):
        return False
    if attente.notifier is not None:
        try:
            attente.notifier(delai)
        except Exception:  # noqa: BLE001 — dire l'attente ne doit jamais la casser
            pass
    dormir(delai)
    if attente.reprise is not None:
        try:
            attente.reprise()
        except Exception:  # noqa: BLE001 — republier l'étape ne doit jamais casser le réessai
            pass
    return True
