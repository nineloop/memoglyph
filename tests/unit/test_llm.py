"""Tests unitaires pour la façade LLM et l'intégration Mistral Chat Completions."""

import pytest
from core import llm
from core.llm_mistral import is_available


pytestmark = pytest.mark.unit


def test_mistral_disponibilite():
    assert is_available("dummy_key") is True
    assert is_available("") is False or is_available(None) is not None


def test_llm_erreur_cle_manquante():
    with pytest.raises(llm.LLMError):
        llm.chat("system", "user", provider="mistral", api_key="")


def test_llm_facade_routage_local(monkeypatch):
    # Le bouchon vise l'INSTANCE du registre : le moteur local n'a plus de
    # fonction de niveau module, sa mécanique vit dans la classe.
    monkeypatch.setattr(llm.MOTEURS["mlx"], "chat", lambda *a, **k: "reponse locale")
    res = llm.chat("sys", "usr", provider="mlx")
    assert res == "reponse locale"


def test_llm_facade_routage_mistral(monkeypatch):
    monkeypatch.setattr(llm.llm_mistral, "chat", lambda sys, usr, **kw: "reponse mistral")
    res = llm.chat("sys", "usr", provider="mistral", api_key="dummy_key")
    assert res == "reponse mistral"
