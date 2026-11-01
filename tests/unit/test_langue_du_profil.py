"""Langue du profil : thèmes, résumé et rôles dans la langue du transcript.

Des réunions anglaises longues ont eu un profil entièrement en français. Le prompt
envoyé portait une seule ligne en anglais, en tête, avant une consigne, des
exemples et un schéma écrits en français, puis des milliers de jetons de
transcript : le modèle local suit la langue de la consigne, pas celle d'une
directive lue bien plus tôt (même défaut que pour les faits de la réunion). Sans
modèle : on lit le prompt que `analyze` envoie vraiment.
"""
from __future__ import annotations

import pytest

from core import profiling
from core.stt_base import Segment, Transcript

pytestmark = pytest.mark.unit

# Ce qui, dans la consigne française, ancrait la sortie en français.
FRANCAIS = ("Analyse cette conversation", "Analyse cet enregistrement", "thèmes",
            "Structure JSON attendue", "Transcription :", "intervieweur", "animateur",
            "une entrée par locuteur", "Utilise exactement")


def _envoye(monkeypatch, language, diarized=True) -> dict:
    capture: dict = {}

    def faux(system, user, **options):
        capture.update(system=system, user=user)
        return {"conversation": {}, "speakers": {}}

    monkeypatch.setattr(profiling.llm, "chat_json", faux)
    segments = [Segment(0.0, 4.0, "Okay, let's start with the remote control.", "Project Manager"),
                Segment(5.0, 9.0, "The target group is fifteen to thirty five.", "Marketing Expert")]
    transcript = Transcript(segments=segments, full_text=" ".join(s.text for s in segments),
                            language=language)
    labels = ["Project Manager", "Marketing Expert"] if diarized else []
    profiling.analyze(transcript, diarized=diarized, speaker_labels=labels)
    return capture


@pytest.mark.parametrize("diarized", [True, False])
def test_un_transcript_anglais_recoit_une_consigne_sans_francais(monkeypatch, diarized) -> None:
    user = _envoye(monkeypatch, "en", diarized)["user"]
    for mot in FRANCAIS:
        assert mot not in user, mot
    assert "topics" in user and "Okay, let's start with the remote control." in user


@pytest.mark.parametrize("diarized", [True, False])
def test_la_directive_de_langue_revient_apres_le_transcript(monkeypatch, diarized) -> None:
    user = _envoye(monkeypatch, "en", diarized)["user"]
    fin = user.rstrip().splitlines()[-1]
    assert fin == profiling._LANG_DIRECTIVE["en"]
    assert user.index("The target group is fifteen to thirty five.") < user.rindex(fin)


def test_les_cles_de_locuteurs_et_la_regle_des_noms_restent(monkeypatch) -> None:
    user = _envoye(monkeypatch, "en")["user"]
    assert "Use exactly these speaker keys: Project Manager, Marketing Expert." in user
    assert '"key_points"' in user and "introduces themselves" in user


def test_un_transcript_francais_garde_sa_consigne(monkeypatch) -> None:
    user = _envoye(monkeypatch, "fr")["user"]
    assert user.startswith(profiling._LANG_DIRECTIVE["fr"])
    assert "Analyse cette conversation" in user and "Transcription :" in user
    assert user.rstrip().endswith("The target group is fifteen to thirty five.")


def test_une_autre_langue_a_sa_directive_en_tete_et_en_fin(monkeypatch) -> None:
    user = _envoye(monkeypatch, "es")["user"]
    assert user.startswith(profiling._LANG_DIRECTIVE["es"])
    assert user.rstrip().splitlines()[-1] == profiling._LANG_DIRECTIVE["es"]
    assert "Analyse cette conversation" not in user
