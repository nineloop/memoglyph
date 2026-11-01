"""Moteur Anthropic (`core/llm_anthropic.py`), sans réseau : un faux serveur httpx.

Chaque test pose une clé FACTICE dans `config` : importer `api.main` ailleurs dans la
suite charge le vrai `.env`, et une assertion en échec afficherait alors la vraie clé.
"""
from __future__ import annotations

import json

import httpx
import pytest

from core import config, llm, llm_anthropic, pricing_official, telemetry, usage_cost

pytestmark = pytest.mark.unit

# Assemblée à l'exécution : le contrôle d'export ne doit pas y lire une clé.
CLE = "sk-ant-" + "factice-pour-les-tests"


@pytest.fixture(autouse=True)
def _isole(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", CLE)
    monkeypatch.setattr(llm_anthropic, "_dormir", lambda s: None)
    llm_anthropic.USAGE.clear()
    yield
    llm_anthropic.USAGE.clear()


def _serveur(monkeypatch, *reponses):
    """Rend les réponses (statut, corps[, en-têtes]) dans l'ordre ; retient les requêtes."""
    vus, reponses = [], list(reponses)

    def repondre(requete):
        vus.append(requete)
        statut, corps, *entetes = reponses.pop(0)
        return httpx.Response(statut, json=corps, headers=(entetes[0] if entetes else None))
    monkeypatch.setattr(llm_anthropic, "_transport", httpx.MockTransport(repondre))
    return vus


def _ok(texte, usage=None, stop="end_turn"):
    return (200, {"stop_reason": stop, "usage": usage or {"input_tokens": 10, "output_tokens": 5},
                  "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": texte}]})


def test_la_cle_va_dans_l_en_tete_et_la_requete_ne_porte_ni_reglage_ni_preremplissage(monkeypatch) -> None:
    vus = _serveur(monkeypatch, _ok('{"faits": []}'))
    assert llm.chat_json("consigne", "transcript", provider="anthropic", temperature=0.7, seed=3) == {"faits": []}
    requete = vus[0]
    corps = json.loads(requete.content)
    assert str(requete.url) == llm_anthropic.URL
    assert requete.headers["x-api-key"] == CLE and requete.headers["anthropic-version"] == llm_anthropic.VERSION_API
    assert corps["model"] == "claude-haiku-5-5" and corps["system"] == "consigne"
    assert corps["messages"] == [{"role": "user", "content": "transcript"}]     # aucun tour assistant prérempli
    assert not {"temperature", "top_p", "top_k", "seed"} & set(corps)            # refusés par l'API


def test_une_cle_absente_ne_fait_aucun_appel(monkeypatch) -> None:
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(llm_anthropic, "ANTHROPIC_API_KEY", "")
    vus = _serveur(monkeypatch)
    assert llm_anthropic.is_available() is False
    with pytest.raises(llm.LLMError, match="ANTHROPIC_API_KEY manquante"):
        llm.chat_json("s", "u", provider="anthropic")
    assert vus == []


def test_les_jetons_sont_comptes_et_le_cout_suit_le_tarif_officiel(monkeypatch) -> None:
    _serveur(monkeypatch, _ok('{"ok": 1}', usage={"input_tokens": 800, "cache_read_input_tokens": 200,
                                                   "cache_creation_input_tokens": 0, "output_tokens": 2000}))
    vus = []
    monkeypatch.setattr(telemetry, "record_model_call", lambda **k: vus.append(k))
    llm.chat_json("s", "u", provider="anthropic")
    assert llm_anthropic.USAGE == [{"in": 1000, "out": 2000}]                   # le cache compte dans l'entrée
    appel = vus[0]
    assert (appel["provider"], appel["model"], appel["status"]) == ("anthropic", "claude-haiku-5-5", "ok")
    assert (appel["in_tok"], appel["out_tok"], appel["cached_tok"]) == (1000, 2000, 200)
    tarif = next(e for e in pricing_official.OFFICIAL_RATES if e["model"] == "claude-haiku-5-5")
    snap = usage_cost.PricingSnapshot(pricing_snapshot_id=None, provider="anthropic", model=tarif["model"],
                                      kind=tarif["kind"], unit=tarif["unit"], input_rate=tarif["input_rate"],
                                      output_rate=tarif["output_rate"], cached_rate=tarif["cached_rate"])
    cout = usage_cost.token_cost(in_tok=1000, out_tok=2000, cached_tok=200, snap=snap)
    assert cout == pytest.approx((800 * 0.10 + 200 * 0.01 + 2000 * 0.50) / 1e6)


def test_une_sortie_coupee_ou_refusee_est_un_echec_sans_reprise(monkeypatch) -> None:
    vus = _serveur(monkeypatch, _ok('{"faits": [', stop="max_tokens"))
    with pytest.raises(llm.LLMError, match="max_tokens"):
        llm.chat_json("s", "u", provider="anthropic", retries=2)
    assert len(vus) == 1
    vus = _serveur(monkeypatch, _ok("", stop="refusal"))
    with pytest.raises(llm.LLMError, match="refus"):
        llm.chat_json("s", "u", provider="anthropic")


def test_une_erreur_de_facturation_s_arrete_aussitot_et_ne_montre_pas_la_cle(monkeypatch) -> None:
    vus = _serveur(monkeypatch, (402, {"type": "error", "error": {
        "type": "billing_error", "message": "Your credit balance is too low."}}))
    with pytest.raises(llm.LLMError) as erreur:
        llm.chat_json("s", "u", provider="anthropic")
    assert "billing_error" in str(erreur.value) and CLE not in str(erreur.value)
    assert len(vus) == 1                                                         # 402 : pas de reprise
    # Un appelant qui s'arrête sur une erreur de facturation la reconnaît à son message : ce
    # sont les marqueurs qu'il cherche, recopiés ici.
    marqueurs = ("tier_not_allowed", "payment", "billing", "quota", "402", "credit balance")
    assert any(m in str(erreur.value).lower() for m in marqueurs)


def test_une_limite_ou_une_surcharge_se_reprend_puis_abandonne(monkeypatch) -> None:
    attentes = []
    monkeypatch.setattr(llm_anthropic, "_dormir", attentes.append)
    limite = (429, {"type": "error", "error": {"type": "rate_limit_error", "message": "trop vite"}}, {"retry-after": "7"})
    vus = _serveur(monkeypatch, limite, (529, {"type": "error", "error": {"type": "overloaded_error"}}), _ok('{"a": 1}'))
    assert llm.chat_json("s", "u", provider="anthropic") == {"a": 1}
    assert len(vus) == 3 and attentes == [7.0, 2.0]
    vus = _serveur(monkeypatch, limite, limite, limite)
    with pytest.raises(llm.LLMError, match="rate_limit_error"):
        llm.chat_json("s", "u", provider="anthropic")
    assert len(vus) == 1 + llm_anthropic.REPRISES_HTTP


def test_plusieurs_objets_json_a_la_suite_reunissent_leurs_faits(monkeypatch) -> None:
    _serveur(monkeypatch, _ok('Voici {"faits": [1]}\n\n{"faits": [2, 3]} fin'))
    assert llm.chat_json("s", "u", provider="anthropic") == {"faits": [1, 2, 3]}
    vus = _serveur(monkeypatch, _ok("pas de JSON"), _ok('{"faits": []}'))
    assert llm.chat_json("s", "u", provider="anthropic", retries=1) == {"faits": []}  # illisible : redemandé
    assert len(vus) == 2


def test_un_schema_passe_par_la_consigne(monkeypatch) -> None:
    vus = _serveur(monkeypatch, _ok('{"x": 1}'))
    llm.chat_json("consigne", "u", provider="anthropic", schema={"type": "object"})
    systeme = json.loads(vus[0].content)["system"]
    assert systeme.startswith("consigne") and '{"type": "object"}' in systeme


def test_le_moteur_est_au_registre_et_propose_a_l_ecran_sans_changer_le_defaut() -> None:
    assert "anthropic" in llm.FOURNISSEURS and isinstance(llm.MOTEURS["anthropic"], llm_anthropic.AnthropicEngine)
    assert llm.FOURNISSEUR_DEFAUT == "mlx" and llm.est_local("anthropic") is False
    assert llm.modele_servi("anthropic") == "claude-haiku-5-5"
    assert llm.modele_servi("anthropic", "claude-sonnet-5-5") == "claude-sonnet-5-5"
    assert "anthropic" not in llm.HORS_INTERFACE
    assert llm.MOTEURS["anthropic"].pret_declare() is True                         # clé factice posée
