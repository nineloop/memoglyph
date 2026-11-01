"""Transcription Voxtral découpée : une réunion de plus d'une heure doit passer.

Au palier par défaut, Mistral limite l'audio à 3 600 secondes par minute : une
requête plus longue est refusée à chaque essai. Au-delà du seuil, l'audio part
en tranches qui se recouvrent, au rythme de la limite ; les horodatages sont
recalés sur la réunion, le recouvrement n'est gardé qu'une fois et les
locuteurs sont raccordés d'une tranche à l'autre.

Aucun appel réseau ni ffmpeg ici : `requests.post` est remplacé, le SDK
`mistralai` masqué, l'extraction des tranches et la durée simulées, l'horloge
et l'attente injectées. Le découpage réel par ffmpeg est éprouvé dans
`test_media_decoupe.py`.
"""

from __future__ import annotations

import sys
import types

import pytest

from core import config, media, stt_mistral
from core.stt_base import Segment
from core.stt_mistral import (
    MistralSTTEngine, MistralSTTError, RythmeAudio, fusionner_tranches, raccorder_locuteurs,
)

# Assemblée à l'exécution : le contrôle d'export ne doit pas y lire une clé.
CLE = "sk-" + "abcdefghijklmnopqrstuvwxyz0123456789"


class _Horloge:
    """Horloge simulée : `dormir` avance le temps au lieu d'attendre."""

    def __init__(self) -> None:
        self.t = 0.0
        self.attentes: list[float] = []

    def __call__(self) -> float:
        return self.t

    def dormir(self, delai: float) -> None:
        self.attentes.append(delai)
        self.t += delai


class _Reponse:
    def __init__(self, statut: int, corps: str = "", charge: dict | None = None) -> None:
        self.status_code = statut
        self.ok = 200 <= statut < 300
        self.text = corps
        self.headers: dict = {}
        self._charge = charge

    def json(self) -> dict:
        return self._charge or {}


def _seg(debut: float, fin: float, texte: str, locuteur=None) -> dict:
    return {"start": debut, "end": fin, "text": texte, "speaker_id": locuteur}


@pytest.fixture
def api(monkeypatch, tmp_path):
    """Moteur prêt à transcrire une réunion de `duree` secondes, sans réseau."""
    monkeypatch.setitem(sys.modules, "mistralai", None)
    monkeypatch.setattr(MistralSTTEngine, "_record_usage", lambda *a, **k: None)
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(config, "MISTRAL_STT_TRANCHE_S", 3000.0)
    monkeypatch.setattr(config, "MISTRAL_STT_RECOUVREMENT_S", 30.0)
    horloge = _Horloge()
    etat = {"duree": 7500.0, "reponses": [], "envois": [], "extraits": [],
            "horloge": horloge, "compressions": 0, "messages": []}
    monkeypatch.setattr(stt_mistral, "_duree_audio", lambda chemin: etat["duree"])
    monkeypatch.setattr(stt_mistral.time, "sleep", horloge.dormir)

    def _extraire(self, source, debut, fin, dossier, index):
        etat["extraits"].append((debut, fin))
        chemin = dossier / f"tranche.{index:03d}.m4a"
        chemin.write_bytes(b"\x00" * 8)
        return chemin

    def _preparer(self, source, progress=None):
        etat["compressions"] += 1
        return source, False

    def _post(*_a, **kwargs):
        etat["envois"].append(kwargs["files"]["file"].name)
        horloge.t += 20.0   # durée de la requête
        return etat["reponses"].pop(0)

    monkeypatch.setattr(MistralSTTEngine, "_extraire_tranche", _extraire)
    monkeypatch.setattr(MistralSTTEngine, "_preparer_envoi", _preparer)
    monkeypatch.setattr("requests.post", _post)
    audio = tmp_path / "reunion.wav"
    audio.write_bytes(b"RIFF")
    etat["audio"] = audio

    def transcrire(**options):
        moteur = MistralSTTEngine(
            api_key=CLE, rythme=RythmeAudio(3600, horloge=horloge, dormir=horloge.dormir),
            **options)
        return moteur.transcribe(audio, progress=lambda f, m: etat["messages"].append(m))

    etat["transcrire"] = transcrire
    return etat


def _ok(*segments: dict, langue: str = "fr") -> _Reponse:
    return _Reponse(200, charge={"text": " ".join(s["text"] for s in segments),
                                 "segments": list(segments), "language": langue,
                                 "model": "voxtral-mini-2602"})


# --- Découpage ---------------------------------------------------------------

def test_les_bornes_se_recouvrent_et_couvrent_toute_la_reunion() -> None:
    assert media.bornes_tranches(7500, 3000, 30) == [
        (0.0, 3000.0), (2970.0, 5970.0), (5940.0, 7500.0)]


def test_une_reunion_sous_la_borne_tient_en_une_tranche() -> None:
    assert media.bornes_tranches(1200, 3000, 30) == [(0.0, 1200.0)]


def test_une_reunion_longue_part_en_tranches_aux_bonnes_bornes(api) -> None:
    api["reponses"] = [_ok(_seg(0, 5, "un")), _ok(_seg(100, 105, "deux")),
                       _ok(_seg(100, 105, "trois"))]

    api["transcrire"]()

    assert api["extraits"] == [(0.0, 3000.0), (2970.0, 5970.0), (5940.0, 7500.0)]
    assert len(api["envois"]) == 3
    assert api["compressions"] == 0, "les tranches partent déjà au format d'envoi"
    assert [m.params for m in api["messages"] if m.code == "step.transcription.chunk"] == [
        {"done": 1, "total": 3}, {"done": 2, "total": 3}, {"done": 3, "total": 3}]


def test_le_seuil_et_le_recouvrement_se_reglent(api) -> None:
    api["duree"] = 1300.0
    api["reponses"] = [_ok(_seg(0, 5, "a")), _ok(_seg(0, 5, "b"))]

    api["transcrire"](tranche_s=1000.0, recouvrement_s=10.0)

    assert api["extraits"] == [(0.0, 1000.0), (990.0, 1300.0)]


def test_les_tranches_temporaires_ne_restent_pas(api) -> None:
    api["reponses"] = [_ok(_seg(0, 5, "un"))] * 3

    api["transcrire"]()

    # Ancre : les tranches vivaient bien dans le dossier balayé, qui est vide ensuite.
    assert len(api["envois"]) == 3
    assert all(str(config.CACHE_DIR) in chemin for chemin in api["envois"])
    assert list(config.CACHE_DIR.iterdir()) == []


# --- Réunion courte ----------------------------------------------------------

@pytest.mark.parametrize("duree", [1200.0, 3000.0, 0.0])
def test_une_reunion_courte_part_toujours_d_un_seul_envoi(api, duree) -> None:
    """3 000 s pile reste un envoi : on découpe AU-DELÀ du seuil. 0 = durée illisible."""
    api["duree"] = duree
    api["reponses"] = [_ok(_seg(0, 2, "bonjour", 0), _seg(2, 4, "salut", 1))]

    t = api["transcrire"]()

    assert len(api["envois"]) == 1 and api["extraits"] == []
    assert api["compressions"] == 1
    assert [(s.text, s.speaker) for s in t.segments] == [
        ("bonjour", "Speaker 1"), ("salut", "Speaker 2")]
    assert t.full_text == "bonjour salut"


def test_un_seuil_nul_desactive_le_decoupage(api) -> None:
    api["reponses"] = [_ok(_seg(0, 2, "tout"))]

    api["transcrire"](tranche_s=0.0)

    assert len(api["envois"]) == 1 and api["extraits"] == []


def test_une_tranche_plus_longue_que_la_limite_est_refusee_avant_tout_envoi(api) -> None:
    with pytest.raises(ValueError, match="MISTRAL_STT_TRANCHE_S"):
        api["transcrire"](tranche_s=4000.0)
    assert api["envois"] == []


# --- Recalage et doublons ----------------------------------------------------

def test_les_horodatages_sont_recales_sur_la_reunion_sans_doublon(api) -> None:
    api["reponses"] = [
        _ok(_seg(0, 10, "ouverture"),
            _seg(2975, 2982, "dans le recouvrement"),       # aussi dans la tranche 2
            _seg(2990, 3000, "phrase coup")),               # coupée par la fin de tranche
        _ok(_seg(5, 12, "dans le recouvrement"),            # 2975 → 2982 en temps de réunion
            _seg(20, 35, "phrase coupée net, ici entière"),  # 2990 → 3005
            _seg(1000, 1010, "milieu")),                    # 3970 → 3980
        _ok(_seg(25, 30, "déjà entendu"),                   # 5965 → 5970, gardé ici
            _seg(1500, 1510, "clôture")),                   # 7440 → 7450
    ]
    api["reponses"][1]._charge["segments"].append(_seg(2994, 3000, "déjà entend"))

    t = api["transcrire"]()

    assert [(s.start, s.end, s.text) for s in t.segments] == [
        (0.0, 10.0, "ouverture"),
        (2975.0, 2982.0, "dans le recouvrement"),
        (2990.0, 3005.0, "phrase coupée net, ici entière"),
        (3970.0, 3980.0, "milieu"),
        (5965.0, 5970.0, "déjà entendu"),
        (7440.0, 7450.0, "clôture"),
    ]
    assert t.full_text.count("dans le recouvrement") == 1
    assert t.language == "fr" and t.model_servi == "voxtral-mini-2602"


def test_une_tranche_sans_segments_garde_son_texte(api) -> None:
    api["duree"] = 3500.0
    api["reponses"] = [_ok(_seg(0, 5, "début")),
                       _Reponse(200, charge={"text": "reste", "segments": []})]

    t = api["transcrire"]()

    assert [(s.start, s.end, s.text) for s in t.segments] == [
        (0.0, 5.0, "début"), (2970.0, 3500.0, "reste")]


# --- Raccord des locuteurs ---------------------------------------------------

def _s(debut, fin, texte, locuteur=None) -> Segment:
    return Segment(start=debut, end=fin, text=texte, speaker=locuteur)


def test_la_paire_au_plus_grand_temps_commun_l_emporte() -> None:
    precedents = [_s(100, 108, "a", "Speaker 1"), _s(108, 110, "b", "Speaker 2"),
                  _s(110, 113, "c", "Speaker 1")]
    courants = [_s(100, 110, "x", "A"),   # 8 s avec Speaker 1, 2 s avec Speaker 2
                _s(110, 113, "y", "B")]   # 3 s avec Speaker 1, déjà pris par A

    assert raccorder_locuteurs(precedents, courants, 100, 130) == {"A": "Speaker 1"}


def test_seul_le_recouvrement_compte() -> None:
    precedents = [_s(0, 50, "avant", "Speaker 1")]
    courants = [_s(0, 50, "même instant, mais hors zone", "A")]

    assert raccorder_locuteurs(precedents, courants, 60, 90) == {}


def test_les_locuteurs_sont_raccordes_d_une_tranche_a_l_autre() -> None:
    tranches = [
        (0.0, 100.0, [_s(0, 10, "a", "1"), _s(10, 20, "b", "2"), _s(20, 30, "c", "3"),
                      _s(75, 85, "d", "2"), _s(85, 100, "e", "1")]),
        # Voxtral renumérote : « 2 » de la tranche 1 devient « 1 », et inversement.
        # Le locuteur « 3 » n'y parle pas ; « 9 » est nouveau, hors recouvrement.
        (70.0, 170.0, [_s(75, 85, "d", "1"), _s(85, 100, "e", "2"),
                       _s(120, 130, "f", "9"), _s(140, 150, "g", "1")]),
    ]

    sortie = fusionner_tranches(tranches)

    assert [(s.text, s.speaker) for s in sortie] == [
        ("a", "Speaker 1"), ("b", "Speaker 2"), ("c", "Speaker 3"),
        ("d", "Speaker 2"),
        ("e", "Speaker 1"),
        ("f", "Speaker 4"),     # nouveau : ni « Speaker 3 », absent, ni un autre déjà pris
        ("g", "Speaker 2"),
    ]


def test_un_locuteur_absent_d_une_tranche_ne_cede_pas_son_etiquette() -> None:
    """« Speaker 2 » parle dans la tranche 1 puis se tait : les nouveaux venus des
    tranches 2 et 3 reçoivent des étiquettes neuves, jamais la sienne."""
    tranches = [
        (0.0, 100.0, [_s(0, 10, "a", "1"), _s(20, 30, "b", "2"), _s(80, 95, "c", "1")]),
        (70.0, 170.0, [_s(80, 95, "c", "5"), _s(145, 160, "d", "6")]),
        (140.0, 240.0, [_s(145, 160, "d", "1"), _s(175, 185, "e", "2"), _s(200, 210, "f", "1")]),
    ]

    sortie = fusionner_tranches(tranches)

    assert [(s.text, s.speaker) for s in sortie] == [
        ("a", "Speaker 1"), ("b", "Speaker 2"), ("c", "Speaker 1"),
        ("d", "Speaker 3"),
        ("e", "Speaker 4"), ("f", "Speaker 3"),
    ]


def test_sans_diarisation_aucune_etiquette_n_apparait() -> None:
    tranches = [(0.0, 100.0, [_s(0, 10, "a"), _s(80, 90, "b")]),
                (70.0, 170.0, [_s(80, 90, "b"), _s(120, 130, "c")])]

    assert [(s.text, s.speaker) for s in fusionner_tranches(tranches)] == [
        ("a", None), ("b", None), ("c", None)]


def test_le_raccord_traverse_le_moteur(api) -> None:
    api["duree"] = 3500.0
    api["reponses"] = [
        _ok(_seg(0, 10, "a", 1), _seg(2972, 2984, "b", 2), _seg(2984, 2998, "c", 1)),
        _ok(_seg(2, 14, "b", 0), _seg(14, 28, "c", 3), _seg(300, 310, "d", 7)),
    ]

    t = api["transcrire"]()

    assert [(s.text, s.speaker) for s in t.segments] == [
        ("a", "Speaker 1"), ("b", "Speaker 2"), ("c", "Speaker 1"), ("d", "Speaker 3")]


# --- Rythme ------------------------------------------------------------------

def test_le_rythme_attend_que_la_minute_glissante_ait_de_la_place() -> None:
    h = _Horloge()
    rythme = RythmeAudio(3600, horloge=h, dormir=h.dormir)

    premier = rythme.reserver(3000)
    h.t = 40.0
    rythme.terminer(premier)          # la requête a duré 40 s
    rythme.reserver(3000)

    # Fenêtre datée de la FIN du premier envoi, plus une seconde de marge.
    assert h.attentes == [61.0]
    assert h.t == 101.0


def test_deux_envois_qui_tiennent_dans_la_minute_n_attendent_pas() -> None:
    h = _Horloge()
    rythme = RythmeAudio(3600, horloge=h, dormir=h.dormir)

    rythme.terminer(rythme.reserver(1800))
    rythme.terminer(rythme.reserver(1800))

    assert h.attentes == []


def test_un_envoi_plus_long_que_la_limite_part_sur_une_fenetre_vide() -> None:
    """Sans attente sans fin : le refus du service, lisible, vaut mieux."""
    h = _Horloge()
    rythme = RythmeAudio(3600, horloge=h, dormir=h.dormir)

    rythme.terminer(rythme.reserver(5000))
    h.t = 10.0
    rythme.reserver(5000)

    assert h.attentes == [51.0]


def test_les_tranches_suivent_le_rythme_et_le_disent(api) -> None:
    api["reponses"] = [_ok(_seg(0, 5, "un")), _ok(_seg(100, 105, "deux")),
                       _ok(_seg(100, 105, "trois"))]

    api["transcrire"]()

    # Chaque requête dure 20 s ; 3 000 + 3 000 puis 3 000 + 1 560 dépassent 3 600.
    assert api["horloge"].attentes == [61.0, 61.0]
    pauses = [m for m in api["messages"] if m.code == "step.upload.mistral.pause"]
    assert [m.params for m in pauses] == [{"delay_s": 61}, {"delay_s": 61}]


def test_une_reunion_courte_compte_aussi_dans_la_minute(api) -> None:
    """Deux réunions courtes coup sur coup partagent la même limite."""
    h = api["horloge"]
    rythme = RythmeAudio(3600, horloge=h, dormir=h.dormir)
    api["duree"] = 2400.0
    api["reponses"] = [_ok(_seg(0, 1, "a")), _ok(_seg(0, 1, "b"))]
    moteur = MistralSTTEngine(api_key=CLE, rythme=rythme)

    moteur.transcribe(api["audio"])
    moteur.transcribe(api["audio"])

    assert h.attentes == [61.0]


# --- Erreurs lisibles, sans clé ----------------------------------------------

def test_un_429_persistant_dit_la_limite_sans_la_cle(api) -> None:
    corps = f'{{"message":"Requests rate limit exceeded","key":"{CLE}"}}'
    api["duree"] = 1200.0
    api["reponses"] = [_Reponse(429, corps), _Reponse(429, corps)]

    with pytest.raises(MistralSTTError) as erreur:
        api["transcrire"]()

    message = str(erreur.value)
    assert "HTTP 429" in message and "limite du palier" in message
    assert "3 600 s d'audio par minute" in message
    assert "Requests rate limit exceeded" in message
    assert CLE not in message


def test_un_refus_de_palier_est_nomme_sans_la_cle(api) -> None:
    corps = ('{"object":"error","message":"This model is not available in your '
             f'subscription tier","type":"tier_not_allowed","echo":"{CLE}"}}')
    api["duree"] = 1200.0
    api["reponses"] = [_Reponse(403, corps)]

    with pytest.raises(MistralSTTError) as erreur:
        api["transcrire"]()

    message = str(erreur.value)
    assert "HTTP 403" in message and "palier de l'abonnement" in message
    assert "MISTRAL_STT_MODEL" in message
    assert CLE not in message
    assert len(api["envois"]) == 1, "un refus de palier n'est pas retenté"


def test_un_refus_en_cours_de_tranches_arrete_tout_et_ne_laisse_rien(api) -> None:
    api["reponses"] = [_ok(_seg(0, 5, "un")), _Reponse(403, '{"type":"tier_not_allowed"}')]

    with pytest.raises(MistralSTTError, match="tier_not_allowed"):
        api["transcrire"]()

    assert len(api["envois"]) == 2
    assert all(str(config.CACHE_DIR) in chemin for chemin in api["envois"])
    assert list(config.CACHE_DIR.iterdir()) == []


def test_le_chemin_sdk_rend_la_meme_erreur_lisible(monkeypatch, tmp_path) -> None:
    """Le SDK lève sa propre exception : sans conversion, ni cause ni assainissement."""
    class _ErreurSDK(Exception):
        def __init__(self) -> None:
            super().__init__(f"API error occurred: Status 429 {CLE}")
            self.status_code = 429
            self.body = f'{{"message":"Requests rate limit exceeded","key":"{CLE}"}}'

    class _Client:
        def __init__(self, api_key=None):
            self.audio = types.SimpleNamespace(
                transcriptions=types.SimpleNamespace(complete=self._complete))

        def _complete(self, file=None, **kwargs):
            raise _ErreurSDK()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    faux = types.ModuleType("mistralai")
    faux.Mistral = _Client
    monkeypatch.setitem(sys.modules, "mistralai", faux)
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"\x00")

    with pytest.raises(MistralSTTError) as erreur:
        MistralSTTEngine(api_key=CLE)._call_api(audio, CLE)

    message = str(erreur.value)
    assert "HTTP 429" in message and "limite du palier" in message
    assert CLE not in message
    assert erreur.value.__cause__ is None and erreur.value.__suppress_context__
