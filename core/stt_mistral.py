"""Moteur de transcription via l'API Mistral AI (Voxtral Mini).

Une réunion plus longue que `config.MISTRAL_STT_TRANCHE_S` part en tranches
successives qui se recouvrent : au palier par défaut, Mistral limite l'audio à
3 600 secondes par minute, et une requête de plus d'une heure ne passe jamais.
Les envois suivent cette limite (`RythmeAudio`), les horodatages sont recalés
sur la réunion entière, et les locuteurs raccordés d'une tranche à l'autre par
leur temps de parole commun dans le recouvrement. Réglages : `MEMOGLYPH_MISTRAL_STT_TRANCHE_S`,
`MEMOGLYPH_MISTRAL_STT_RECOUVREMENT_S` et `MEMOGLYPH_MISTRAL_AUDIO_S_PAR_MINUTE` (`docs/configuration.md`).
"""

from __future__ import annotations

import math
import os
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Any, Callable, Optional

from .config import MISTRAL_API_KEY, MISTRAL_STT_MODEL
from . import messages
from . import telemetry
from .llm_mistral import STATUTS_A_RETENTER, _sans_secret, attente_avant_reprise
from .stt_base import PartialCb, Progress, Segment, Transcript


class MistralSTTError(RuntimeError):
    """Refus de l'API de transcription. Le message est assaini."""


def _refus(response: Any, *actives: Optional[str]) -> MistralSTTError:
    """Erreur lisible : statut et corps de la réponse, sans secret.

    `raise_for_status()` ne gardait que « 429 Too Many Requests » : le corps,
    seul à distinguer un débit dépassé d'un quota épuisé, était perdu.
    """
    return _refus_lisible(response.status_code, response.text, *actives)


def _refus_lisible(statut: Any, corps: object, *actives: Optional[str]) -> MistralSTTError:
    """Le statut, une cause en clair pour les refus de palier, puis le corps assaini.

    La cause est un littéral : seul le corps de la réponse est une valeur
    dynamique, et il passe par `_sans_secret`, comme pour le Chat.
    """
    if statut == 429:
        cause = ("limite du palier Mistral atteinte, débit ou quota (au palier par défaut : "
                 "3 600 s d'audio par minute, tous modèles Voxtral confondus). Réessayer "
                 "dans une minute ; le détail dit s'il s'agit d'un quota épuisé. ")
    elif "tier_not_allowed" in str(corps):
        cause = ("modèle non disponible dans le palier de l'abonnement (tier_not_allowed) : "
                 "choisir un autre modèle (MISTRAL_STT_MODEL) ou passer à un palier supérieur. ")
    else:
        cause = ""
    return MistralSTTError(
        f"Transcription refusée par Mistral (HTTP {statut}) : {cause}"
        f"{_sans_secret(corps, *actives)}")


class RythmeAudio:
    """Secondes d'audio envoyées sur une minute glissante, sous la limite du palier.

    Avant chaque envoi, `reserver` attend que la durée de l'envoi tienne dans
    la minute, puis la réserve ; `terminer` date la réservation de la fin de la
    requête. Dater de la fin est le choix prudent : que Mistral compte une
    requête à sa réception ou à sa fin, la fenêtre ne se libère jamais trop tôt.
    `marge_s` couvre l'écart d'horloge avec le service.

    Une instance est partagée par le processus (`_rythme_partage`) : deux
    transcriptions simultanées consomment la même limite d'organisation.
    L'horloge et l'attente sont injectables, pour les tests.
    """

    def __init__(
        self,
        limite_s: float,
        *,
        fenetre_s: float = 60.0,
        marge_s: float = 1.0,
        horloge: Callable[[], float] = time.monotonic,
        dormir: Callable[[float], None] = time.sleep,
    ) -> None:
        if limite_s <= 0:
            raise ValueError("La limite audio doit être strictement positive")
        self.limite_s = float(limite_s)
        self.fenetre_s = float(fenetre_s)
        self.marge_s = float(marge_s)
        self._horloge = horloge
        self._dormir = dormir
        self._envois: list[list[float]] = []   # [instant, durée]
        self._verrou = threading.Lock()

    def reserver(
        self, duree_s: float, *, annoncer: Optional[Callable[[float], None]] = None,
    ) -> list[float]:
        """Attend que `duree_s` tienne dans la minute, puis la réserve.

        Un envoi plus long que la limite à lui seul attend une fenêtre vide puis
        part quand même : le refus du service, lisible, vaut mieux qu'une
        attente sans fin. `annoncer(delai_s)` est appelé avant chaque attente.
        """
        while True:
            with self._verrou:
                maintenant = self._horloge()
                self._envois = [e for e in self._envois if e[0] + self.fenetre_s > maintenant]
                occupe = sum(d for _, d in self._envois)
                if not self._envois or occupe + duree_s <= self.limite_s:
                    entree = [maintenant, float(duree_s)]
                    self._envois.append(entree)
                    return entree
                # Libérer le plus ancien envoi ne suffit pas toujours : la
                # boucle revérifie après chaque attente.
                delai = min(t for t, _ in self._envois) + self.fenetre_s - maintenant + self.marge_s
            if annoncer:
                annoncer(delai)
            # L'attente se fait verrou relâché : `terminer` d'un envoi en cours
            # ne doit pas attendre derrière elle.
            self._dormir(delai)

    def terminer(self, entree: list[float]) -> None:
        with self._verrou:
            entree[0] = self._horloge()


_RYTHME_PARTAGE: Optional[RythmeAudio] = None
_VERROU_PARTAGE = threading.Lock()


def _rythme_partage() -> RythmeAudio:
    global _RYTHME_PARTAGE
    from .config import MISTRAL_AUDIO_S_PAR_MINUTE

    with _VERROU_PARTAGE:
        if _RYTHME_PARTAGE is None:
            _RYTHME_PARTAGE = RythmeAudio(MISTRAL_AUDIO_S_PAR_MINUTE)
        return _RYTHME_PARTAGE


def _duree_audio(chemin: Path) -> float:
    """Durée en secondes ; 0.0 si elle est illisible, ce qui garde l'envoi unique.

    Le WAV du pipeline se lit dans son en-tête, sans lancer ffprobe.
    """
    if chemin.suffix.lower() == ".wav":
        try:
            with wave.open(str(chemin), "rb") as w:
                return w.getnframes() / float(w.getframerate())
        except (wave.Error, OSError, EOFError, ZeroDivisionError):
            pass
    try:
        from . import media

        return media.probe_duration(chemin)
    except OSError:   # ffprobe absent
        return 0.0


def _cle_locuteur(item: dict[str, Any]) -> Optional[str]:
    """Identifiant de locuteur rendu par l'API, normalisé en texte, ou None."""
    # `or` traitait un identifiant FALSY comme absent : un `speaker_id` entier
    # valant 0 — indice de locuteur parfaitement légitime — perdait son
    # étiquette. Le tour devenait « Speaker ? » dans le .md, QUI FAIT FOI ; les
    # locuteurs suivants étaient décalés d'un rang ; et `already_diarized`
    # restait vrai, ce qui supprimait le repli pyannote qui aurait tout rattrapé.
    # Non observé sur le trafic réel (l'API rend des chaînes ou des entiers
    # commençant à 1) — mais un changement de format côté fournisseur écrirait
    # de faux locuteurs, en silence.
    raw_spk = item.get("speaker_id")
    if raw_spk is None:
        raw_spk = item.get("speaker")
    # Normalisé en texte : `0` et `"0"` désignent le même locuteur et ne
    # doivent pas créer deux entrées. `core/merge.py` garde déjà correctement
    # avec `is not None`.
    cle = "" if raw_spk is None else str(raw_spk).strip()
    return cle or None


def _segments_bruts(payload: dict[str, Any], decalage: float = 0.0) -> list[Segment]:
    """Segments non vides d'une réponse, décalés de `decalage` secondes.

    `speaker` porte l'identifiant BRUT de l'API, pas encore une étiquette :
    d'une tranche à l'autre, ces identifiants ne désignent pas la même personne.
    """
    segments = []
    for item in payload.get("segments") or []:
        text = str(item.get("text", "")).strip()
        if text:
            segments.append(Segment(
                start=decalage + float(item.get("start", 0.0)),
                end=decalage + float(item.get("end", 0.0)),
                text=text, speaker=_cle_locuteur(item)))
    return segments


def _etiquette(numero: int) -> str:
    return f"Speaker {numero}"


def raccorder_locuteurs(
    precedents: list[Segment], courants: list[Segment], debut: float, fin: float,
) -> dict[str, str]:
    """Identifiant brut de la tranche courante -> étiquette de la précédente.

    Seul le recouvrement `[debut, fin[` compte : les deux tranches y ont
    entendu le même audio. Pour chaque paire (étiquette précédente, identifiant
    courant), on cumule le temps où les deux parlent en même temps, puis on
    apparie les paires du plus grand temps commun au plus petit, chaque
    locuteur une seule fois. Un identifiant sans temps commun n'est pas
    apparié : l'appelant lui donne une étiquette nouvelle.
    """
    def _dans_la_zone(segments: list[Segment]) -> list[tuple[float, float, str]]:
        return [(max(s.start, debut), min(s.end, fin), s.speaker)
                for s in segments
                if s.speaker is not None and min(s.end, fin) > max(s.start, debut)]

    commun: dict[tuple[str, str], float] = {}
    courants_zone = _dans_la_zone(courants)
    for p_debut, p_fin, etiquette in _dans_la_zone(precedents):
        for c_debut, c_fin, brut in courants_zone:
            recoupe = min(p_fin, c_fin) - max(p_debut, c_debut)
            if recoupe > 0:
                commun[(etiquette, brut)] = commun.get((etiquette, brut), 0.0) + recoupe

    correspondance: dict[str, str] = {}
    prises: set[str] = set()
    for (etiquette, brut), _ in sorted(commun.items(), key=lambda kv: (-kv[1], kv[0])):
        if brut not in correspondance and etiquette not in prises:
            correspondance[brut] = etiquette
            prises.add(etiquette)
    return correspondance


def fusionner_tranches(
    tranches: list[tuple[float, float, list[Segment]]],
) -> list[Segment]:
    """Une seule suite de segments à partir des tranches `(début, fin, segments)`.

    Les segments sont déjà en temps de réunion, et leur `speaker` est
    l'identifiant brut de leur tranche.

    - **Doublons** : deux tranches voisines transcrivent toutes deux leur
      recouvrement. On le coupe en son milieu, et chaque segment va à la
      tranche du côté où tombe son propre milieu. Un passage contenu dans le
      recouvrement, ou qui n'en déborde que d'un côté, est ainsi gardé une seule
      fois ; seul un segment plus long que tout le recouvrement peut figurer
      dans les deux tranches, chacune n'en ayant entendu qu'une partie.
    - **Locuteurs** : la première tranche numérote dans l'ordre d'apparition ;
      chaque tranche suivante reprend les étiquettes de la précédente par
      `raccorder_locuteurs`. Un locuteur sans correspondance reçoit une
      étiquette nouvelle, jamais une étiquette déjà donnée. Le raccord ne
      regarde que la tranche précédente : un locuteur absent de toute une
      tranche revient sous une étiquette nouvelle.
    """
    sortie: list[Segment] = []
    precedents: list[Segment] = []
    numero = 0
    for i, (debut, _fin, bruts) in enumerate(tranches):
        correspondance: dict[str, str] = {}
        if i > 0:
            fin_precedente = tranches[i - 1][1]
            correspondance = raccorder_locuteurs(precedents, bruts, debut, fin_precedente)
        etiquetes = []
        for s in bruts:
            speaker = None
            if s.speaker is not None:
                if s.speaker not in correspondance:
                    numero += 1
                    correspondance[s.speaker] = _etiquette(numero)
                speaker = correspondance[s.speaker]
            etiquetes.append(Segment(start=s.start, end=s.end, text=s.text, speaker=speaker))

        coupe_avant = (debut + tranches[i - 1][1]) / 2 if i > 0 else -math.inf
        coupe_apres = ((tranches[i + 1][0] + _fin) / 2 if i + 1 < len(tranches)
                       else math.inf)
        sortie.extend(s for s in etiquetes
                      if coupe_avant <= (s.start + s.end) / 2 < coupe_apres)
        precedents = etiquetes
    return sortie


def _modele_servi(payload: dict[str, Any]) -> Optional[str]:
    """Ce que l'API déclare avoir SERVI, et non l'alias qu'on lui a demandé.

    `self.model_name` est ce que NOUS avons demandé ; ce champ-ci est ce que le
    fournisseur déclare avoir servi. Ils coïncident tant que l'API échoie la
    requête, mais l'un est une intention et l'autre un fait. La réponse, elle,
    peut nommer le fait — et si elle ne le fait pas, on laisse `None` plutôt que
    de recopier la demande en la faisant passer pour une confirmation.
    """
    servi = payload.get("model")
    return servi if isinstance(servi, str) and servi else None


class MistralSTTEngine:
    """Moteur STT basé sur l'API Mistral AI (Voxtral Mini) avec horodatages et diarisation native."""

    name = "mistral"

    def __init__(
        self,
        model_name: str | None = None,
        api_key: str | None = None,
        language: str | None = None,
        diarize: bool = True,
        *,
        tranche_s: float | None = None,
        recouvrement_s: float | None = None,
        rythme: RythmeAudio | None = None,
    ) -> None:
        # Le repli ultime reprend le défaut ÉPINGLÉ de `config`, pas un alias :
        # un repli qui réintroduirait `-latest` rendrait la provenance
        # ambiguë précisément quand la configuration a échoué.
        self.model_name = model_name or MISTRAL_STT_MODEL or "voxtral-mini-2602"
        self.api_key = api_key
        # Code langue forcé (« fr ») ou None pour la détection automatique.
        self.language = (language or None)
        # `diarize` était figé à True : décocher « Identifier les locuteurs »
        # n'avait aucun effet en mode Mistral, et le pipeline enchaînait en plus
        # une extraction d'empreintes pyannote — un calcul LOCAL lourd dans un
        # mode présenté comme « Tout via Mistral Cloud ». L'option pilote
        # désormais réellement le contrat envoyé à l'API.
        self.diarize = diarize
        # Découpage des longues réunions : None = réglages de `config`, lus à
        # chaque transcription. `rythme` None = la limite partagée du processus.
        self.tranche_s = tranche_s
        self.recouvrement_s = recouvrement_s
        self.rythme = rythme

    def transcribe(
        self,
        wav_path: Path,
        *,
        progress: Optional[Progress] = None,
        on_partial: Optional[PartialCb] = None,
    ) -> Transcript:
        key = self.api_key if self.api_key is not None else (MISTRAL_API_KEY or os.getenv("MISTRAL_API_KEY"))
        if not key:
            raise ValueError(
                "MISTRAL_API_KEY manquante. Renseignez la clé API dans la UI ou dans votre fichier .env."
            )

        from .config import MISTRAL_STT_RECOUVREMENT_S, MISTRAL_STT_TRANCHE_S

        tranche_s = MISTRAL_STT_TRANCHE_S if self.tranche_s is None else self.tranche_s
        recouvrement_s = (MISTRAL_STT_RECOUVREMENT_S if self.recouvrement_s is None
                          else self.recouvrement_s)
        rythme = self.rythme or _rythme_partage()
        duree = _duree_audio(Path(wav_path))

        if tranche_s > 0 and duree > tranche_s:
            t = self._transcrire_par_tranches(
                Path(wav_path), key, duree, tranche_s, recouvrement_s, rythme,
                progress=progress)
        else:
            t = self._transcrire_d_un_bloc(Path(wav_path), key, duree, rythme, progress=progress)

        if on_partial:
            on_partial(t.segments)

        if progress:
            progress(1.0, messages.transcription_mistral_terminee())

        return t

    def _envoyer(
        self, fichier: Path, key: str, duree: float, rythme: RythmeAudio,
        *, progress: Optional[Progress] = None, fraction: float = 0.1,
    ) -> dict[str, Any]:
        """Un envoi, après avoir attendu que sa durée tienne dans la minute."""
        def _annoncer(delai: float) -> None:
            print(f"[stt-mistral] limite audio de la minute : pause de {delai:.0f} s")
            if progress:
                progress(fraction, messages.pause_limite_audio_mistral(math.ceil(delai)))

        reservation = rythme.reserver(duree, annoncer=_annoncer)
        try:
            return self._call_api(fichier, key, progress=progress)
        finally:
            rythme.terminer(reservation)

    def _transcrire_d_un_bloc(
        self, wav_path: Path, key: str, duree: float, rythme: RythmeAudio,
        *, progress: Optional[Progress] = None,
    ) -> Transcript:
        """Réunion courte, ou découpage désactivé : un seul envoi, comme toujours."""
        envoi, temporaire = self._preparer_envoi(wav_path, progress=progress)
        try:
            if progress:
                progress(0.1, messages.envoi_mistral())
            payload = self._envoyer(envoi, key, duree, rythme, progress=progress)
        finally:
            if temporaire:
                envoi.unlink(missing_ok=True)

        if progress:
            progress(0.9, messages.formatage_segments())

        raw_text = payload.get("text", "")
        raw_segments = payload.get("segments", [])
        # Langue : forcée si l'utilisateur l'a imposée, sinon celle détectée par
        # l'API. Elle n'était PAS enregistrée — le front-matter tombait à
        # `language: None`, cassant l'ancrage de langue du LLM en aval (profil, CR).
        detected_language = self.language or (payload.get("language") or None)

        speaker_map: dict[str, str] = {}
        segments: list[Segment] = []

        if raw_segments:
            for item in raw_segments:
                start = float(item.get("start", 0.0))
                end = float(item.get("end", 0.0))
                text = str(item.get("text", "")).strip()
                cle = _cle_locuteur(item)
                speaker = None
                if cle:
                    if cle not in speaker_map:
                        speaker_map[cle] = _etiquette(len(speaker_map) + 1)
                    speaker = speaker_map[cle]
                if text:
                    segments.append(Segment(start=start, end=end, text=text, speaker=speaker))

        if not segments:
            segments = [Segment(start=0.0, end=0.0, text=raw_text.strip())]

        return Transcript(
            segments=segments, full_text=raw_text.strip(), language=detected_language,
            model_servi=_modele_servi(payload),
        )

    def _transcrire_par_tranches(
        self, wav_path: Path, key: str, duree: float, tranche_s: float,
        recouvrement_s: float, rythme: RythmeAudio,
        *, progress: Optional[Progress] = None,
    ) -> Transcript:
        """Réunion longue : tranches successives, recalées puis fusionnées.

        Chaque tranche est extraite du WAV d'origine directement au format
        d'envoi (`_extraire_tranche`) : pas de compression du fichier entier.
        """
        from . import media
        from .config import CACHE_DIR

        if tranche_s > rythme.limite_s:
            raise ValueError(
                f"MISTRAL_STT_TRANCHE_S ({tranche_s:.0f} s) dépasse la limite audio de la "
                f"minute ({rythme.limite_s:.0f} s) : chaque tranche serait refusée.")
        bornes = media.bornes_tranches(duree, tranche_s, recouvrement_s)
        print(f"[stt-mistral] {duree:.0f} s d'audio : {len(bornes)} tranches de "
              f"{tranche_s:.0f} s au plus, recouvrement {recouvrement_s:.0f} s")
        if progress:
            progress(0.1, messages.envoi_mistral())

        tranches: list[tuple[float, float, list[Segment]]] = []
        langue: Optional[str] = None
        servi: Optional[str] = None
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="mistral-tranches-", dir=CACHE_DIR) as dossier:
            for index, (debut, fin) in enumerate(bornes):
                fraction = 0.1 + 0.8 * index / len(bornes)
                if progress:
                    progress(fraction, messages.transcription_progression(index + 1, len(bornes)))
                fichier = self._extraire_tranche(wav_path, debut, fin, Path(dossier), index + 1)
                try:
                    payload = self._envoyer(fichier, key, fin - debut, rythme,
                                            progress=progress, fraction=fraction)
                finally:
                    fichier.unlink(missing_ok=True)
                bruts = _segments_bruts(payload, decalage=debut)
                texte = str(payload.get("text", "")).strip()
                if not bruts and texte:
                    # Réponse sans segments : le texte couvre toute la tranche.
                    bruts = [Segment(start=debut, end=fin, text=texte)]
                tranches.append((debut, fin, bruts))
                langue = langue or (payload.get("language") or None)
                servi = servi or _modele_servi(payload)

        if progress:
            progress(0.9, messages.formatage_segments())

        segments = fusionner_tranches(tranches)
        texte_complet = " ".join(s.text for s in segments)
        if not segments:
            segments = [Segment(start=0.0, end=0.0, text="")]
        return Transcript(
            segments=segments, full_text=texte_complet,
            language=self.language or langue, model_servi=servi,
        )

    def _extraire_tranche(
        self, source: Path, debut: float, fin: float, dossier: Path, index: int,
    ) -> Path:
        """`[debut, fin[` de `source`, au format d'envoi de `_preparer_envoi`."""
        from . import media
        from .config import MISTRAL_UPLOAD_KBPS

        if MISTRAL_UPLOAD_KBPS > 0:
            codec, debit, ext = "aac", f"{MISTRAL_UPLOAD_KBPS}k", ".m4a"
        else:   # compression désactivée : la tranche part en WAV
            codec, debit, ext = "pcm_s16le", "256k", ".wav"
        return media.extraire(source, debut, fin, dossier / f"{source.stem}.{index:03d}{ext}",
                              codec=codec, debit=debit)

    def _preparer_envoi(
        self, source: Path, *, progress: Optional[Progress] = None
    ) -> tuple[Path, bool]:
        """Compresse l'audio avant l'envoi. Retourne (chemin, à_supprimer).

        Le pipeline fabrique un WAV 16 kHz — le bon format pour Whisper et pour
        la diarisation locale, mais 115 Mo par heure à téléverser. Le moteur
        distant, lui, décode de toute façon : on lui envoie de l'AAC.

        Le WAV d'origine n'est PAS touché : c'est lui qui porte l'empreinte
        `pcm_sha256` de provenance et qui sert à la diarisation.

        Un échec de compression n'interrompt pas la transcription — on repart
        sur le fichier d'origine, mais en le DISANT : une dégradation
        silencieuse du transport ferait croire à un gain qui n'a pas eu lieu.
        """
        from .config import CACHE_DIR, MISTRAL_UPLOAD_KBPS

        debit = MISTRAL_UPLOAD_KBPS
        if debit <= 0 or source.suffix.lower() != ".wav":
            return source, False  # déjà compressé, ou compression désactivée

        try:
            from . import media

            media.ensure_ffmpeg()
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cible = CACHE_DIR / f"{source.stem}-upload-{debit}k.m4a"
            subprocess.run(
                [
                    "ffmpeg", "-y", "-i", str(source),
                    "-c:a", "aac", "-b:a", f"{debit}k", "-ac", "1",
                    "-vn", str(cible),
                ],
                capture_output=True,
                check=True,
            )
            if not cible.exists() or cible.stat().st_size == 0:
                raise RuntimeError("fichier compressé vide")
        except Exception as exc:  # noqa: BLE001 — la transcription prime
            print(f"[stt-mistral] compression impossible, envoi du WAV brut : {exc}")
            return source, False

        if progress:
            avant, apres = source.stat().st_size, cible.stat().st_size
            progress(
                0.08,
                messages.audio_compresse(round(avant / 1e6), round(apres / 1e6)),
            )
        return cible, True

    def _call_api(
        self, file_path: Path, api_key: str, *, progress: Optional[Progress] = None,
    ) -> dict[str, Any]:
        # ⚠️ Tension à VÉRIFIER empiriquement (impossible ici sans envoyer l'audio
        # de l'utilisateur au cloud) : certaines API de transcription refusent
        # `language` combiné à des horodatages de segment. Or nos horodatages sont
        # INDISPENSABLES à la fusion avec la diarisation. On envoie donc la langue
        # ET on garde `timestamp_granularities` : si l'API rejette la combinaison,
        # l'erreur est BRUYANTE (raise), jamais une perte silencieuse d'horodatages.
        sdk_kwargs: dict[str, Any] = {
            "model": self.model_name,
            "diarize": self.diarize,
            "timestamp_granularities": ["segment"],
        }
        if self.language:
            sdk_kwargs["language"] = self.language
        try:
            from mistralai import Mistral  # type: ignore[import-untyped]
        except ImportError:
            # SDK absent : repli sur l'appel HTTP direct. Une VRAIE erreur d'API
            # (401, 429, timeout) ne doit PAS déclencher ce repli — sinon on
            # ré-uploade l'audio et on masque la cause.
            import requests

            headers = {"Authorization": f"Bearer {api_key}"}
            data = {
                "model": self.model_name,
                "diarize": "true" if self.diarize else "false",
                "timestamp_granularities[]": "segment",
            }
            if self.language:
                data["language"] = self.language
            for tentative in (1, 2):
                # Même règle de reprise que le Chat (`llm_mistral._poster`), mais
                # boucle propre : le fichier est rouvert à chaque tentative, le
                # premier envoi l'ayant lu jusqu'au bout.
                with open(file_path, "rb") as f:
                    _t0 = time.perf_counter()
                    response = requests.post(
                        "https://api.mistral.ai/v1/audio/transcriptions",
                        headers=headers,
                        files={"file": f},
                        data=data,
                        timeout=300,
                    )
                if response.ok:
                    break
                if tentative == 1 and response.status_code in STATUTS_A_RETENTER:
                    delai = attente_avant_reprise(response)
                    print(f"[stt-mistral] HTTP {response.status_code}, "
                          f"nouvelle tentative dans {delai:.0f} s")
                    if progress:
                        progress(0.1, messages.nouvelle_tentative_mistral(
                            response.status_code, round(delai)))
                    time.sleep(delai)
                    continue
                raise _refus(response, api_key)
            payload = response.json()
            self._record_usage(payload, _t0)
            return payload

        # SDK présent : chemin principal. Une erreur d'API n'ouvre pas de repli
        # HTTP masquant, la garde ImportError ci-dessus ayant déjà traité le seul
        # cas « SDK absent ». Une erreur qui porte un statut HTTP devient la même
        # erreur lisible et assainie que sur le chemin HTTP ; les autres
        # remontent telles quelles.
        with Mistral(api_key=api_key) as client:
            with open(file_path, "rb") as f:
                _t0 = time.perf_counter()
                try:
                    res = client.audio.transcriptions.complete(file=f, **sdk_kwargs)
                except Exception as exc:
                    statut = getattr(exc, "status_code", None)
                    if not isinstance(statut, int):
                        raise
                    corps = getattr(exc, "body", None) or exc
                    raise _refus_lisible(statut, corps, api_key) from None
            payload = (res.model_dump() if hasattr(res, "model_dump")
                       else {"text": getattr(res, "text", str(res))})
            self._record_usage(payload, _t0)
            return payload

    def _record_usage(self, payload: Any, t0: float) -> None:
        """Télémétrie FinOps (best-effort, hors contrat) : enregistre l'appel STT.

        L'API `audio/transcriptions` PEUT exposer un bloc `usage` ; s'il n'est pas
        en jetons (secondes audio, ou absent), `telemetry` laisse les comptes à
        `None` et n'appose PAS `provider_reported` — on n'invente aucune sémantique.
        Le champ `usage` est capturé ICI, seul endroit où la réponse complète est en
        portée avant que `transcribe` n'en garde que le texte/les segments.
        """
        usage = payload.get("usage") if isinstance(payload, dict) else None
        telemetry.record_model_call(
            provider="mistral", model=self.model_name,
            latency_s=time.perf_counter() - t0, status="ok",
            usage=usage,
            usage_source="provider_reported" if usage is not None else None,
        )
