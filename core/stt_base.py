"""Types et interface partagés par les moteurs de transcription (STT)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Protocol

# Callback de progression : (fraction 0..1, message)
Progress = Callable[[float, str], None]

# Callback de texte partiel : reçoit les segments fraîchement transcrits, au fil
# de l'eau (transcription progressive). Permet d'afficher le texte pendant le
# traitement au lieu d'attendre la fin.
PartialCb = Callable[[list["Segment"]], None]


@dataclass
class Segment:
    """Une phrase transcrite avec ses bornes temporelles (secondes)."""

    start: float
    end: float
    text: str
    speaker: Optional[str] = None  # rempli par la diarisation, sinon None


@dataclass
class Transcript:
    segments: list[Segment] = field(default_factory=list)
    full_text: str = ""
    language: Optional[str] = None
    # Le modèle que le fournisseur déclare avoir SERVI, quand il le déclare.
    #
    # `None` = il ne l'a pas dit, et c'est une réponse honnête. La provenance
    # retombe alors sur le nom DEMANDÉ, en sachant que c'en est un.
    #
    # Ce champ existe parce que `MISTRAL_STT_MODEL` vaut `voxtral-mini-latest`,
    # un ALIAS. Deux documents produits à six mois d'écart portaient donc la
    # même chaîne en ayant été produits par des modèles différents : la
    # provenance nommait l'intention, jamais le fait. Et l'écart n'est pas
    # théorique — la fiche de `Voxtral-Mini-3B-2507` annonce 8 langues sans
    # l'arabe, quand la documentation de « Voxtral Mini Transcribe V2 » en
    # annonce 13 avec. Savoir lequel a tourné change la réponse.
    model_servi: Optional[str] = None


class STTEngine(Protocol):
    """Contrat commun à tous les moteurs.

    Les deux callbacks sont MOT-CLÉ SEULEMENT et portent un nom imposé. Ce
    n'était pas le cas : le moteur Mistral attendait `on_partial`, Whisper et ce
    protocole disaient `on_segment`, et `core/pipeline.py` s'était aligné sur le
    premier. Résultat, toute transcription LOCALE lancée depuis l'interface
    échouait sur « unexpected keyword argument ». `Protocol` est du typage
    structurel : rien ne le vérifie à l'exécution, et `make check` ne type-check
    pas. `tests/unit/test_stt_contrat.py` le vérifie donc explicitement.
    """

    name: str

    def transcribe(
        self,
        wav_path: Path,
        *,
        progress: Optional[Progress] = None,
        on_partial: Optional[PartialCb] = None,
    ) -> Transcript:
        ...
