"""Moteur OpenAI — contrat `LLMEngine`, enregistrement, et honneur du JSON.

Déterministe : la SEULE frontière réseau (`_generer`) est bouchonnée. Aucun appel
réel. Vérifie l'intégration au registre, la résolution de modèle/clé, et que
`chat_json` parse le JSON, réessaie, et refuse un non-objet.
"""
from __future__ import annotations

import json

import pytest

from core import config, llm, llm_openai


# ---------- enregistrement au registre des fournisseurs ----------
def test_openai_est_enregistre_comme_fournisseur():
    assert "openai" in llm.FOURNISSEURS
    assert llm.MOTEURS["openai"].name == "openai"
    assert llm.moteur("openai") is llm.MOTEURS["openai"]


def test_openai_est_distant_pas_local():
    assert llm.est_local("openai") is False


def test_modele_servi_defaut_et_surcharge():
    assert llm.modele_servi("openai") == config.OPENAI_CHAT_MODEL == "gpt-5.6-luna"
    assert llm_openai.modele_servi("gpt-5.6-sol") == "gpt-5.6-sol"


# ---------- disponibilité = présence de clé, sans réseau ----------
def test_is_available_suit_la_cle(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert llm.is_available(provider="openai") is True
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert llm.is_available(provider="openai") is False


# ---------- chat_json : parse, mode JSON, réessai, refus non-objet ----------
def test_chat_json_parse_et_mode_json_object(monkeypatch):
    vus = {}

    def faux_generer(system, user, *, model, api_key, max_tokens, text_format):
        vus["system"] = system
        vus["text_format"] = text_format
        vus["max_tokens"] = max_tokens
        return '{"units": [1, 2]}', "completed"

    monkeypatch.setattr(llm_openai, "_generer", faux_generer)
    out = llm.chat_json("SYS", "USER", provider="openai", max_tokens=800)
    assert out == {"units": [1, 2]}
    # sans schéma → mode json_object + rappel « json » dans l'invite
    assert vus["text_format"] == {"type": "json_object"}
    assert "json" in vus["system"].lower()
    assert vus["max_tokens"] == 800


def test_chat_json_schema_strict(monkeypatch):
    vus = {}

    def faux_generer(system, user, *, model, api_key, max_tokens, text_format):
        vus["text_format"] = text_format
        return "{}", "completed"

    monkeypatch.setattr(llm_openai, "_generer", faux_generer)
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    llm.chat_json("SYS", "USER", provider="openai", schema=schema)
    assert vus["text_format"]["type"] == "json_schema"
    assert vus["text_format"]["strict"] is True
    assert vus["text_format"]["schema"] is schema


def test_chat_json_reessaie_puis_echoue(monkeypatch):
    appels = {"n": 0}

    def faux_generer(*a, **k):
        appels["n"] += 1
        return "pas du json", "completed"

    monkeypatch.setattr(llm_openai, "_generer", faux_generer)
    with pytest.raises(llm_openai.OpenAILLMError):
        llm_openai.chat_json("SYS", "USER", retries=2)
    assert appels["n"] == 3  # 1 + 2 réessais


def test_chat_json_refuse_un_non_objet(monkeypatch):
    monkeypatch.setattr(llm_openai, "_generer",
                        lambda *a, **k: (json.dumps([1, 2, 3]), "completed"))
    with pytest.raises(llm_openai.OpenAILLMError):
        llm_openai.chat_json("SYS", "USER")


def test_chat_json_sans_cle_leve(monkeypatch):
    # `_generer` non bouchonné, mais aucune clé → erreur claire, pas d'appel réseau.
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(llm_openai, "OPENAI_API_KEY", "")
    with pytest.raises(llm_openai.OpenAILLMError):
        llm_openai.chat_json("SYS", "USER")


# ---------- profil d'exécution résolu par capacité (distant → grand-contexte) ----------
def test_profil_openai_est_grand_contexte():
    from core.experimental import profiles
    p = profiles.resolve("openai")
    assert p is profiles.LARGE_CONTEXT       # distant → profil cloud prouvé
    assert p.chunk_tokens == 2000            # extraction prudente (rétention)
