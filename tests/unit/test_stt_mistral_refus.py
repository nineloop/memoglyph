"""Un refus de l'API de transcription Mistral doit rester lisible et sans secret.

`raise_for_status()` ne gardait que « 429 Too Many Requests for url » : le
corps de la réponse, seul à distinguer un débit dépassé d'un quota épuisé, était
perdu, et le traitement s'arrêtait au premier refus. Aucun appel réseau ici :
`requests.post` est remplacé, et le SDK `mistralai` est masqué pour suivre le
chemin HTTP, celui de l'installation par défaut.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from core import llm_mistral, stt_mistral
from core.stt_mistral import MistralSTTEngine, MistralSTTError

# Assemblée à l'exécution : le contrôle d'export ne doit pas y lire une clé.
CLE = "sk-" + "abcdefghijklmnopqrstuvwxyz0123456789"
CHARGE_OK = {"text": "bonjour", "segments": [], "language": "fr"}


class _Reponse:
    def __init__(self, statut: int, corps: str = "", entetes: dict | None = None,
                 charge: dict | None = None) -> None:
        self.status_code = statut
        self.ok = 200 <= statut < 300
        self.text = corps
        self.headers = entetes or {}
        self._charge = charge

    def json(self) -> dict:
        return self._charge or {}


@pytest.fixture
def envoi(monkeypatch, tmp_path):
    """Rejoue une suite de réponses et note les envois et les attentes."""
    monkeypatch.setitem(sys.modules, "mistralai", None)
    monkeypatch.setattr(MistralSTTEngine, "_record_usage", lambda *a, **k: None)
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"\x00" * 16)
    etat = {"reponses": [], "envois": 0, "attentes": []}

    def _post(*_a, **kwargs):
        # Le fichier doit être lisible depuis le début à chaque envoi.
        assert kwargs["files"]["file"].read() == b"\x00" * 16
        etat["envois"] += 1
        return etat["reponses"].pop(0)

    monkeypatch.setattr("requests.post", _post)
    monkeypatch.setattr(stt_mistral.time, "sleep", etat["attentes"].append)
    etat["audio"] = audio
    return etat


def _appeler(etat, progression=None) -> dict:
    return MistralSTTEngine(api_key=CLE)._call_api(etat["audio"], CLE, progress=progression)


def test_un_429_passager_est_retente_une_fois(envoi) -> None:
    envoi["reponses"] = [
        _Reponse(429, '{"message":"Requests rate limit exceeded"}', {"Retry-After": "7"}),
        _Reponse(200, charge=CHARGE_OK),
    ]
    vus = []

    assert _appeler(envoi, lambda frac, msg: vus.append(msg)) == CHARGE_OK
    assert envoi["envois"] == 2
    assert envoi["attentes"] == [7.0]
    assert [m.code for m in vus] == ["step.upload.mistral.retry"]
    assert vus[0].params == {"status": 429, "delay_s": 7}


def test_un_second_refus_remonte_le_corps_de_la_reponse(envoi) -> None:
    corps = '{"message":"Monthly spending limit reached"}'
    envoi["reponses"] = [_Reponse(429, corps), _Reponse(429, corps)]

    with pytest.raises(MistralSTTError) as erreur:
        _appeler(envoi)

    assert envoi["envois"] == 2
    assert "HTTP 429" in str(erreur.value)
    assert "Monthly spending limit reached" in str(erreur.value)


def test_un_refus_d_authentification_n_est_pas_retente(envoi) -> None:
    envoi["reponses"] = [_Reponse(401, '{"message":"Unauthorized"}')]

    with pytest.raises(MistralSTTError, match="HTTP 401"):
        _appeler(envoi)

    assert envoi["envois"] == 1
    assert envoi["attentes"] == []


def test_la_cle_renvoyee_en_echo_est_effacee(envoi) -> None:
    envoi["reponses"] = [_Reponse(400, f'{{"error":"bad key","provided":"{CLE}"}}')]

    with pytest.raises(MistralSTTError) as erreur:
        _appeler(envoi)

    assert CLE not in str(erreur.value)
    assert "bad key" in str(erreur.value)


@pytest.mark.parametrize("entetes,attendu", [
    ({}, 10.0),
    ({"Retry-After": "3"}, 3.0),
    ({"Retry-After": "3600"}, 60.0),
    ({"Retry-After": "-5"}, 0.0),
    ({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, 10.0),
])
def test_l_attente_suit_retry_after_et_reste_bornee(entetes, attendu) -> None:
    assert llm_mistral.attente_avant_reprise(_Reponse(429, entetes=entetes)) == attendu


def test_transcribe_transmet_la_progression_a_l_envoi(monkeypatch, tmp_path) -> None:
    """Sans ce relais, le message de nouvelle tentative n'atteint pas l'écran."""
    moteur = MistralSTTEngine(api_key=CLE)
    recu = {}

    def _call_api(chemin: Path, cle: str, *, progress=None) -> dict:
        recu["progress"] = progress
        return CHARGE_OK

    monkeypatch.setattr(moteur, "_call_api", _call_api)
    monkeypatch.setattr(moteur, "_preparer_envoi", lambda src, progress=None: (src, False))
    rappel = lambda frac, msg: None  # noqa: E731

    moteur.transcribe(tmp_path / "audio.wav", progress=rappel)

    assert recu["progress"] is rappel
