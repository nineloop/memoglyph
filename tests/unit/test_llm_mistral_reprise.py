"""Le Chat et les embeddings Mistral retentent une fois un refus passager.

Le profilage et chaque tranche du compte-rendu abandonnaient au premier 429 :
une limite de débit faisait perdre tout le compte-rendu. Aucun appel réseau :
`requests.post` est remplacé, et le SDK `mistralai` masqué pour suivre le
chemin HTTP, celui de l'installation par défaut.
"""

from __future__ import annotations

import sys

import pytest

from core import llm_mistral

# Assemblée à l'exécution : le contrôle d'export ne doit pas y lire une clé.
CLE = "sk-" + "abcdefghijklmnopqrstuvwxyz0123456789"
CHAT_OK = {"choices": [{"message": {"content": "bonjour"}, "finish_reason": "stop"}]}
EMBED_OK = {"data": [{"embedding": [0.1, 0.2]}]}


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
def envoi(monkeypatch):
    monkeypatch.setitem(sys.modules, "mistralai", None)
    monkeypatch.setattr(llm_mistral.telemetry, "record_model_call", lambda **k: None)
    etat = {"reponses": [], "envois": 0, "attentes": []}

    def _post(*_a, **_k):
        etat["envois"] += 1
        return etat["reponses"].pop(0)

    monkeypatch.setattr("requests.post", _post)
    monkeypatch.setattr(llm_mistral.time, "sleep", etat["attentes"].append)
    return etat


def test_le_chat_retente_un_429_une_fois(envoi) -> None:
    envoi["reponses"] = [
        _Reponse(429, '{"type":"rate_limited"}', {"Retry-After": "2"}),
        _Reponse(200, charge=CHAT_OK),
    ]

    assert llm_mistral.chat("s", "u", api_key=CLE) == "bonjour"
    assert envoi["envois"] == 2
    assert envoi["attentes"] == [2.0]


def test_le_chat_remonte_le_second_refus_avec_son_corps(envoi) -> None:
    envoi["reponses"] = [_Reponse(429, '{"type":"rate_limited"}')] * 2

    with pytest.raises(llm_mistral.MistralLLMError, match="rate_limited"):
        llm_mistral.chat("s", "u", api_key=CLE)

    assert envoi["envois"] == 2
    assert envoi["attentes"] == [10.0]


def test_un_refus_d_authentification_n_est_pas_retente(envoi) -> None:
    envoi["reponses"] = [_Reponse(401, '{"message":"Unauthorized"}')]

    with pytest.raises(llm_mistral.MistralLLMError, match="401"):
        llm_mistral.chat("s", "u", api_key=CLE)

    assert envoi["envois"] == 1
    assert envoi["attentes"] == []


def test_les_embeddings_retentent_un_503_une_fois(envoi) -> None:
    envoi["reponses"] = [_Reponse(503, "overloaded"), _Reponse(200, charge=EMBED_OK)]

    assert llm_mistral.embed(["texte"], api_key=CLE) == [[0.1, 0.2]]
    assert envoi["envois"] == 2
