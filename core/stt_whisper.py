"""Moteur de transcription Whisper via MLX (mlx-whisper).

Détection de langue automatique et fiable, et surtout FORÇABLE : le jeton
`<|fr|>` vit dans le prompt du décodeur et conditionne TOUTE la passe.

C'est précisément ce mécanisme qui manquait à Parakeet, retiré.
Son vocabulaire contient pourtant les mêmes jetons de langue, mais le
checkpoint est un `EncDecRNNTBPEModel` — un RNN-T simple, sans prompt : ces
jetons y sont vestigiaux, hérités du tokenizer partagé avec la famille
multi-tâches. Vérifié en les injectant à la main : aucun effet.

Seul moteur de Memoglyph.

**Transcription progressive** : les fichiers longs sont transcrits par tranches
(coupées sur un creux d'énergie pour ne pas tronquer un mot), ce qui permet
d'afficher le texte au fil de l'eau et d'avoir une vraie barre de progression.
La langue est détectée sur la 1re tranche puis **verrouillée** pour les
suivantes — sinon Whisper peut changer de langue d'une tranche à l'autre.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .config import SAMPLE_RATE, STT_CHUNK_SEC, STT_CUT_SEARCH_SEC, WHISPER_MODEL
from . import artifacts, messages
from .stt_base import PartialCb, Progress, Segment, Transcript

# Sous cette durée, une fenêtre n'a pas assez d'audio pour un second décodage utile.
REDECODAGE_MIN_S = 1.0


def redecoder_boucles(segments: list[Segment], decoder) -> tuple[list[Segment], int]:
    """Re-décode UNE fois chaque segment en boucle (`artifacts.est_une_boucle`) sur
    sa propre fenêtre ; rend les segments et le nombre de boucles réparées.

    `decoder(debut, fin)` rend les segments d'un second décodage de cette fenêtre,
    sans le contexte précédent : une boucle se nourrit de son propre texte, qui
    revient sinon en amorce. Si le second décodage boucle encore, ou ne rend rien,
    le segment reste tel quel : la politique d'artefacts le repliera avec sa
    marque et le consignera (`repetition_loops`), jamais en silence.
    """
    sortie: list[Segment] = []
    reparees = 0
    for seg in segments:
        if not artifacts.est_une_boucle(seg.text) or seg.end - seg.start < REDECODAGE_MIN_S:
            sortie.append(seg)
            continue
        nouveaux = [n for n in decoder(seg.start, seg.end) if n.text.strip()]
        if nouveaux and not any(artifacts.est_une_boucle(n.text) for n in nouveaux):
            sortie += nouveaux
            reparees += 1
        else:
            sortie.append(seg)
    return sortie, reparees


def _cut_points(audio, chunk_sec: float) -> list[tuple[int, int]]:
    """Découpe l'audio en tranches ~chunk_sec, chaque coupe placée sur le creux
    d'énergie le plus marqué autour de la frontière visée (évite de couper un mot).

    Retourne des bornes d'échantillons [(début, fin), …].
    """
    import numpy as np

    n = len(audio)
    step = int(chunk_sec * SAMPLE_RATE)
    search = int(STT_CUT_SEARCH_SEC * SAMPLE_RATE)
    win = max(1, int(0.05 * SAMPLE_RATE))  # fenêtre d'énergie ~50 ms

    bounds: list[tuple[int, int]] = []
    start = 0
    while start < n:
        target = start + step
        if target >= n - search:  # dernière tranche
            bounds.append((start, n))
            break
        lo = max(start + step // 2, target - search)
        hi = min(n, target + search)
        zone = np.abs(audio[lo:hi])
        if len(zone) >= win:
            # énergie moyenne glissante -> on coupe au minimum (silence le plus net)
            kernel = np.ones(win, dtype=np.float32) / win
            energy = np.convolve(zone, kernel, mode="valid")
            cut = lo + int(np.argmin(energy)) + win // 2
        else:
            cut = target
        bounds.append((start, cut))
        start = cut
    return bounds


def _nom_lisible(repo: str) -> str:
    """« mlx-community/whisper-large-v3-turbo » → « whisper-large-v3-turbo »."""
    return repo.rsplit("/", 1)[-1]


def _modele_en_cache(repo: str) -> bool:
    """Le modèle est-il DÉJÀ téléchargé ? Sinon, le prochain appel le télécharge.

    Best-effort et prudent : au moindre doute on rend `True`, pour ne PAS
    annoncer un téléchargement qui n'aura pas lieu. Un faux « pas en cache »
    afficherait « premier usage » à chaque run — pire qu'un silence. Le seul cas
    qu'on veut attraper avec certitude est le vrai premier usage, où aucun
    fichier du repo n'est en cache.
    """
    # Un chemin local (modèle déjà sur disque) est toujours « en cache ».
    if Path(repo).exists():
        return True
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:  # paquet absent (ex. CI sans stack ML) → ne rien annoncer
        return True
    # Un seul fichier retrouvé suffit à prouver que le repo a déjà été tiré.
    for nom in ("config.json", "weights.npz", "model.safetensors"):
        try:
            trouve = try_to_load_from_cache(repo_id=repo, filename=nom)
        except Exception:  # noqa: BLE001
            return True
        if isinstance(trouve, str) and trouve:
            return True
    return False


class WhisperEngine:
    """Whisper MLX. `language=None` => détection automatique."""

    name = "whisper"

    def __init__(
        self, model_name: str = WHISPER_MODEL, language: Optional[str] = None
    ) -> None:
        self.model_name = model_name
        self.language = language  # None = auto-détection

    def _to_segments(
        self, result: dict, offset: float = 0.0, span: Optional[float] = None
    ) -> list[Segment]:
        """Segments décalés de `offset`. `span` borne les temps à la durée réelle
        de la tranche : Whisper peut déborder (padding 30 s), ce qui casserait la
        monotonie des timestamps entre tranches."""
        out: list[Segment] = []
        for s in result.get("segments", []):
            text = s.get("text", "").strip()
            if not text:
                continue
            start = float(s["start"])
            end = float(s["end"])
            if span is not None:
                start = min(start, span)
                end = min(end, span)
            out.append(Segment(start=start + offset, end=end + offset, text=text))
        return out

    def _decodeur(self, audio, language: Optional[str]):
        """Second décodage d'une fenêtre [début, fin] en secondes, langue verrouillée,
        sans amorce ni contexte précédent (voir `redecoder_boucles`)."""
        import mlx_whisper

        def decoder(debut: float, fin: float) -> list[Segment]:
            s0, s1 = int(debut * SAMPLE_RATE), min(len(audio), int(fin * SAMPLE_RATE))
            result = mlx_whisper.transcribe(
                audio[s0:s1],
                path_or_hf_repo=self.model_name,
                language=language,
                word_timestamps=False,
                condition_on_previous_text=False,
            )
            return self._to_segments(result, offset=debut, span=fin - debut)

        return decoder

    def transcribe(
        self,
        wav_path: Path,
        *,
        progress: Optional[Progress] = None,
        on_partial: Optional[PartialCb] = None,
    ) -> Transcript:
        import mlx_whisper
        from mlx_whisper.audio import load_audio

        lang_msg = self.language or "auto"
        if progress:
            progress(0.02, messages.transcription_whisper(lang_msg))
        # Premier usage : le modèle (~1,5 Go) va se télécharger DANS l'appel
        # `mlx_whisper.transcribe` qui suit, sans signal propre. Sans cette
        # annonce, la barre reste figée vers 11 % pendant des minutes,
        # indiscernable d'un blocage.
        if progress and not _modele_en_cache(self.model_name):
            progress(0.03, messages.telechargement_modele(_nom_lisible(self.model_name)))

        audio = load_audio(str(wav_path))
        total_sec = len(audio) / SAMPLE_RATE

        # Fichier court (ou mode progressif désactivé) : une seule passe, comme avant.
        if STT_CHUNK_SEC <= 0 or total_sec <= STT_CHUNK_SEC * 1.5:
            result = mlx_whisper.transcribe(
                audio,
                path_or_hf_repo=self.model_name,
                language=self.language,
                word_timestamps=False,
            )
            segments = self._to_segments(result)
            if on_partial and segments:
                on_partial(segments)
            segments, _ = redecoder_boucles(
                segments, self._decodeur(audio, result.get("language") or self.language))
            if progress:
                progress(1.0, messages.transcription_terminee())
            return Transcript(
                segments=segments,
                full_text=" ".join(s.text for s in segments).strip(),
                language=result.get("language"),
            )

        # Transcription progressive, tranche par tranche.
        bounds = _cut_points(audio, STT_CHUNK_SEC)
        segments: list[Segment] = []
        language = self.language
        carry = ""  # fin du texte précédent : rend le contexte perdu à la coupe
        for i, (s0, s1) in enumerate(bounds):
            result = mlx_whisper.transcribe(
                audio[s0:s1],
                path_or_hf_repo=self.model_name,
                language=language,
                word_timestamps=False,
                initial_prompt=carry or None,
            )
            # Langue verrouillée après la 1re tranche (évite la dérive de langue).
            if language is None:
                language = result.get("language")
            new = self._to_segments(
                result, offset=s0 / SAMPLE_RATE, span=(s1 - s0) / SAMPLE_RATE
            )
            segments += new
            if new:  # ~200 derniers caractères comme amorce de la tranche suivante
                carry = " ".join(s.text for s in new)[-200:]
            if on_partial and new:
                on_partial(new)
            if progress:
                done = s1 / len(audio)
                progress(done, messages.transcription_progression(i + 1, len(bounds)))

        segments, _ = redecoder_boucles(segments, self._decodeur(audio, language))
        if progress:
            progress(1.0, messages.transcription_terminee())
        return Transcript(
            segments=segments,
            full_text=" ".join(s.text for s in segments).strip(),
            language=language,
        )
