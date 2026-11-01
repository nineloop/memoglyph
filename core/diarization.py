"""Diarisation des locuteurs via pyannote.audio (module optionnel).

`pyannote`/`torch` ne sont importés qu'à l'appel de `diarize()`, pour que
l'application tourne sans ces dépendances lourdes quand l'option est désactivée.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import config
from . import messages
from .config import DIARIZATION_MODEL

Progress = Callable[[float, str], None]


class DiarizationError(RuntimeError):
    """Erreur liée à la diarisation (dépendances, token, modèle…)."""


@dataclass
class SpeakerTurn:
    start: float
    end: float
    speaker: str  # ex. "SPEAKER_00"


@dataclass
class DiarizeResult:
    turns: list["SpeakerTurn"]
    # Empreinte vocale par locuteur BRUT (ex. "SPEAKER_00" -> vecteur 256 d).
    # Vide si le modèle n'expose pas d'embeddings.
    embeddings: dict[str, list[float]]


def _pick_device():
    import torch

    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def load_pipeline(hf_token: Optional[str]):
    """Charge le pipeline pyannote (à réutiliser sur plusieurs fichiers : backfill)."""
    if not hf_token:
        # `config.HF_TOKEN` est lu à l'import, AVANT tout chargement du `.env` :
        # seuls `api/main.py` et `core/pipeline.py` appellent `load_env()`. Tout
        # autre usage de `core/` comme bibliothèque — sa raison d'être — échouait
        # donc en réclamant de renseigner un token DÉJÀ présent dans `.env`. Le
        # message envoyait corriger ce qui n'était pas cassé.
        config.load_env()
        hf_token = config.HF_TOKEN
    if not hf_token:
        raise DiarizationError(
            "HF_TOKEN manquant. Renseigne-le dans .env et accepte les conditions "
            f"du modèle : https://huggingface.co/{DIARIZATION_MODEL}"
        )
    try:
        from pyannote.audio import Pipeline
    except ImportError as exc:
        raise DiarizationError(
            "pyannote.audio n'est pas installé. Installe :  "
            "pip install pyannote.audio torch"
        ) from exc
    try:
        pipe = Pipeline.from_pretrained(DIARIZATION_MODEL, token=hf_token)
    except Exception as exc:  # noqa: BLE001
        raise DiarizationError(
            f"Impossible de charger '{DIARIZATION_MODEL}'. Vérifie ton HF_TOKEN et "
            f"que tu as accepté les conditions : https://huggingface.co/{DIARIZATION_MODEL}\n"
            f"Détail : {exc}"
        ) from exc
    try:
        pipe.to(_pick_device())
    except Exception:  # noqa: BLE001 — fallback silencieux sur CPU
        pass
    return pipe


def diarize(
    wav_path: Path,
    hf_token: Optional[str],
    progress: Optional[Progress] = None,
    pipe=None,
) -> DiarizeResult:
    """Retourne les tours de parole + l'empreinte vocale par locuteur.

    `pipe` : pipeline pyannote pré-chargé à réutiliser (sinon chargé à la volée).
    Lève DiarizationError si les dépendances/token manquent ou si le modèle
    n'est pas accessible (conditions non acceptées sur Hugging Face).
    """
    if progress:
        progress(0.05, messages.chargement_diarisation())

    if pipe is None:
        pipe = load_pipeline(hf_token)

    if progress:
        progress(0.3, messages.analyse_locuteurs())

    output = pipe(str(wav_path))

    # pyannote 4.x renvoie un DiarizeOutput ; 3.x renvoie directement une Annotation.
    annotation = getattr(output, "speaker_diarization", output)

    turns = [
        SpeakerTurn(start=float(segment.start), end=float(segment.end), speaker=speaker)
        for segment, _, speaker in annotation.itertracks(yield_label=True)
    ]
    turns.sort(key=lambda t: t.start)

    # Empreinte vocale par locuteur : `DiarizeOutput.speaker_embeddings` = ndarray
    # (n_locuteurs, 256), ligne i alignée sur annotation.labels()[i] (spike validé).
    embeddings: dict[str, list[float]] = {}
    emb = getattr(output, "speaker_embeddings", None)
    if emb is not None:
        try:
            labels = list(annotation.labels())
            for label, vec in zip(labels, emb):
                embeddings[label] = [float(x) for x in vec]
        except Exception:  # noqa: BLE001 — l'embedding est un bonus, jamais bloquant
            embeddings = {}

    if progress:
        progress(1.0, messages.locuteurs_detectes(len({t.speaker for t in turns})))
    return DiarizeResult(turns=turns, embeddings=embeddings)


# ---------- Empreintes sur des bornes fournies (chemin cloud) ----------
#
# Voxtral diarise mais ne rend AUCUN vecteur : une réunion transcrite en cloud
# laissait donc l'écran Voix vide, et aucune de ses voix n'était rapprochable
# d'une réunion locale. C'est le trou que ce bloc comble.
#
# On ré-embarque donc localement, sur les bornes rendues par le moteur, avec le
# MÊME checkpoint que le pipeline de diarisation — `pipe.embedding['checkpoint']`
# et son sous-dossier. Charger un autre modèle d'empreinte donnerait des
# vecteurs d'un autre espace, dont les distances avec les existants n'auraient
# aucun sens, et rien ne le signalerait.
#
# Vérifié avant d'écrire ce code : le vecteur recalculé pour un
# locuteur colle à +0,860 avec son empreinte stockée par le pipeline, contre
# +0,035 au maximum avec les trois autres locuteurs du même fichier. L'espace
# est partagé — mesuré, pas supposé.

_MODELE_EMPREINTE = None

# En dessous, le vecteur est du bruit plutôt qu'une voix. Un locuteur qui dit
# « oui » deux fois n'a pas d'empreinte, et lui en fabriquer une polluerait le
# regroupement de tous les autres.
DUREE_MIN_EMPREINTE = 3.0


def _modele_empreinte(hf_token: Optional[str] = None):
    global _MODELE_EMPREINTE
    if _MODELE_EMPREINTE is not None:
        return _MODELE_EMPREINTE
    if not hf_token:
        config.load_env()
        hf_token = config.HF_TOKEN
    if not hf_token:
        raise DiarizationError("HF_TOKEN manquant pour l'extraction d'empreintes")
    try:
        from pyannote.audio import Inference, Model
    except ImportError as exc:
        raise DiarizationError("pyannote.audio n'est pas installé") from exc
    modele = Model.from_pretrained(
        DIARIZATION_MODEL, token=hf_token, subfolder="embedding",
    )
    inference = Inference(modele, window="whole")
    try:
        inference.to(_pick_device())
    except Exception:  # noqa: BLE001 — repli CPU silencieux, comme le pipeline
        pass
    _MODELE_EMPREINTE = inference
    return inference


def unload_empreinte() -> None:
    """Rend la mémoire du modèle d'empreinte. Discipline RAM unifiée."""
    global _MODELE_EMPREINTE
    if _MODELE_EMPREINTE is None:
        return
    _MODELE_EMPREINTE = None
    try:
        import gc

        import torch

        gc.collect()
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
    except Exception:  # noqa: BLE001 — libérer est un bonus, jamais bloquant
        pass


def empreintes_par_locuteur(
    wav_path: Path,
    tours: dict[str, list[tuple[float, float]]],
    *,
    hf_token: Optional[str] = None,
    duree_min: float = DUREE_MIN_EMPREINTE,
) -> dict[str, list[float]]:
    """Une empreinte moyenne par locuteur, depuis des bornes déjà connues.

    `tours` : {« Speaker 1 » -> [(début, fin), …]} en secondes.

    Un locuteur totalisant moins de `duree_min` secondes est ÉCARTÉ plutôt que
    doté d'un vecteur douteux — et la liste des écartés se lit dans le journal.
    Rendre une empreinte de bruit serait pire que n'en rendre aucune : elle
    entrerait dans le regroupement et déplacerait des voix réelles.
    """
    import numpy as np
    from pyannote.core import Segment

    inference = _modele_empreinte(hf_token)
    out: dict[str, list[float]] = {}
    ecartes: list[str] = []
    for locuteur, intervalles in sorted(tours.items()):
        valides = [(a, b) for a, b in intervalles if b > a]
        total = sum(b - a for a, b in valides)
        if total < duree_min:
            ecartes.append(f"{locuteur} ({total:.1f} s)")
            continue
        vecteurs, non_finis = [], []
        for a, b in valides:
            try:
                v = inference.crop(str(wav_path), Segment(a, b))
            except Exception as exc:  # noqa: BLE001 — un tour illisible n'annule pas les autres
                print(f"[empreinte] {locuteur} [{a:.1f}→{b:.1f}] ignoré : {exc}")
                continue
            arr = np.asarray(v, dtype=float).ravel()
            # Un crop peut rendre des NaN — segment trop court pour le modèle,
            # ou tombant en bordure de fichier. `np.mean` les propage ensuite à
            # TOUT le vecteur du locuteur. Observé sur un essai cloud : le
            # locuteur dominant, celui qui a le plus de tours,
            # ressortait entièrement NaN et se voyait attribuer une voix neuve à
            # chaque transcription. Un test `norme == 0` ne l'attrape pas :
            # `NaN == 0` est faux.
            if not np.isfinite(arr).all():
                non_finis.append(f"{a:.1f}→{b:.1f}")
                continue
            vecteurs.append(arr)
        if not vecteurs:
            ecartes.append(f"{locuteur} (aucun tour exploitable)")
            continue
        if non_finis:
            print(f"[empreinte] {locuteur} : {len(non_finis)}/{len(valides)} tour(s) "
                  f"non finis, écartés de la moyenne ({', '.join(non_finis[:4])})")
        moyen = np.mean(vecteurs, axis=0)
        norme = float(np.linalg.norm(moyen))
        # Pas de garde `isfinite` ici : le filtre par crop l'a rendu
        # inatteignable, et une mutation l'a prouvé. Une norme nulle, elle,
        # reste possible — des vecteurs opposés s'annulent en moyenne.
        if norme == 0.0:
            ecartes.append(f"{locuteur} (vecteur nul)")
            continue
        out[locuteur] = [float(x) for x in moyen / norme]

    if ecartes:
        print(f"[empreinte] sans empreinte, moins de {duree_min:.0f} s de parole : "
              f"{', '.join(ecartes)}")
    return out
