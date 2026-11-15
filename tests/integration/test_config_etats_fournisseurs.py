"""`GET /api/config` dit, pour chaque fournisseur connu, OÙ il tourne (`lieu`) et S'IL est prêt (`etat`).

Deux champs et non une énumération : un serveur « compatible » hébergé ailleurs est « cloud privé » ET peut être « non configuré ».
Le champ s'AJOUTE : `llm_providers`, `llm_ready` et `llm_models` ne changent pas. Aucune clé, aucun nom de variable, aucune adresse
ni modèle dans `llm_etats`. Aucun appel réel.
"""
from __future__ import annotations

import json

import pytest

from api import auth
from api import main as api_main
from api.jobs import JobManager
from core import config, llm
from tests.integration.test_regeneration_job import connecte

pytestmark = [pytest.mark.integration, pytest.mark.sans_session]

MOT_DE_PASSE = "un mot de passe solide"
NOMMES = {"openrouter": ("OPENROUTER_API_KEY", "MEMOGLYPH_OPENROUTER_MODEL"),
          "groq": ("GROQ_API_KEY", "MEMOGLYPH_GROQ_MODEL")}


@pytest.fixture
def client(isolated_stores, tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(auth, "CODE_PATH", tmp_path / ".memoglyph-installation")
    monkeypatch.setattr(api_main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(api_main, "jobs", JobManager())
    auth.creer_compte("alice", MOT_DE_PASSE)
    for cle, modele in NOMMES.values():                      # départ propre : ni clé ni modèle
        monkeypatch.delenv(cle, raising=False)
        monkeypatch.delenv(modele, raising=False)
        monkeypatch.delenv(modele.removeprefix("MEMOGLYPH_"), raising=False)
    monkeypatch.setattr(config, "COMPATIBLE_BASE_URL", "")
    monkeypatch.setattr(config, "COMPATIBLE_MODEL", "")
    return connecte("alice")


def _etats(client) -> dict:
    return client.get("/api/config").json()["llm_etats"]


def _configurer(monkeypatch, nom: str, cle: str | None, modele: str | None) -> None:
    variable_cle, variable_modele = NOMMES[nom]
    if cle:
        monkeypatch.setenv(variable_cle, cle)
    if modele:
        monkeypatch.setenv(variable_modele, modele)


def test_le_champ_couvre_tout_le_registre_sans_l_alias_ni_les_moteurs_hors_interface(client) -> None:
    etats = _etats(client)
    attendus = set(llm.MOTEURS) - set(llm.HORS_INTERFACE)
    assert set(etats) == attendus
    assert "cloud" not in etats                                     # l'alias n'est pas un moteur
    assert len(attendus) >= 5, "le registre a perdu des moteurs : le balayage ne prouve plus rien"
    for nom, valeur in etats.items():
        assert set(valeur) == {"lieu", "etat"}, nom
        assert valeur["lieu"] in {"local", "en_ligne", "cloud_prive"}, nom
        assert valeur["etat"] in {"pret", "gratuit", "non_configure"}, nom


def test_les_champs_existants_ne_changent_pas(client) -> None:
    corps = client.get("/api/config").json()
    for ancien in ("llm_providers", "llm_ready", "llm_models", "local_providers", "private_cloud_providers"):
        assert ancien in corps, ancien
    assert set(corps["llm_etats"]) == set(corps["llm_ready"]) - set(llm.HORS_INTERFACE)
    for nom, valeur in corps["llm_etats"].items():
        assert (valeur["etat"] != "non_configure") == corps["llm_ready"][nom], nom      # une seule vérité sur « prêt »


@pytest.mark.parametrize("nom", sorted(NOMMES))
def test_un_nomme_sans_cle_est_non_configure_et_present(client, nom) -> None:
    etat = _etats(client)[nom]
    assert etat == {"lieu": "en_ligne", "etat": "non_configure"}
    assert nom not in client.get("/api/config").json()["llm_providers"], "cacher dans `llm_providers` reste le comportement d'avant"


def test_un_modele_gratuit_sans_cle_n_est_pas_gratuit_mais_non_configure(client, monkeypatch) -> None:
    """La condition « prêt » du calcul : un modèle `:free` configuré SANS clé ne devient pas « gratuit »."""
    _configurer(monkeypatch, "openrouter", None, "auteur/un-modele:free")
    assert _etats(client)["openrouter"]["etat"] == "non_configure"


def test_gratuit_seulement_pour_un_fournisseur_pret_dont_le_modele_est_marque_gratuit(client, monkeypatch) -> None:
    _configurer(monkeypatch, "openrouter", "cle-fictive-1", "auteur/un-modele:free")
    assert _etats(client)["openrouter"] == {"lieu": "en_ligne", "etat": "gratuit"}
    _configurer(monkeypatch, "openrouter", "cle-fictive-1", "auteur/un-modele-payant")
    assert _etats(client)["openrouter"] == {"lieu": "en_ligne", "etat": "pret"}


def test_un_nomme_pret_sans_marqueur_de_gratuite_est_pret(client, monkeypatch) -> None:
    _configurer(monkeypatch, "groq", "cle-fictive-2", "un-modele")
    assert _etats(client)["groq"] == {"lieu": "en_ligne", "etat": "pret"}


def test_un_serveur_compatible_non_configure_est_cloud_prive_pas_local(client) -> None:
    assert _etats(client)["compatible"] == {"lieu": "cloud_prive", "etat": "non_configure"}


def test_un_serveur_compatible_sur_la_machine_est_local_et_un_distant_est_cloud_prive(client, monkeypatch) -> None:
    monkeypatch.setattr(config, "COMPATIBLE_MODEL", "un-modele")
    monkeypatch.setattr(config, "COMPATIBLE_BASE_URL", "http://127.0.0.1:9/v1")
    assert _etats(client)["compatible"] == {"lieu": "local", "etat": "pret"}
    monkeypatch.setattr(config, "COMPATIBLE_BASE_URL", "https://serveur.example/v1")
    assert _etats(client)["compatible"] == {"lieu": "cloud_prive", "etat": "pret"}


def test_le_modele_local_est_local(client) -> None:
    assert _etats(client)["mlx"]["lieu"] == "local"


def test_aucune_cle_ni_nom_de_variable_ni_adresse_dans_la_reponse(client, monkeypatch) -> None:
    """Une clé sentinelle par fournisseur nommé ne sort jamais, ni le nom de sa variable ; le modèle sentinelle n'est pas dans
    `llm_etats` (il est dans `llm_models`, par conception : le champ ajouté n'en ajoute pas)."""
    sentinelles = {}
    for nom in NOMMES:
        sentinelles[nom] = (f"SENTINELLE_CLE_{nom.upper()}_9f3a", f"sentinelle/modele-{nom}-77c1")
        _configurer(monkeypatch, nom, *sentinelles[nom])
    reponse = client.get("/api/config")
    corps = reponse.text
    etats = json.dumps(reponse.json()["llm_etats"])
    for nom, (cle, modele) in sentinelles.items():
        variable_cle, variable_modele = NOMMES[nom]
        assert cle not in corps, f"la clé de {nom} fuit"
        assert variable_cle not in corps and variable_modele not in corps
        assert modele not in etats, f"le modèle de {nom} entre dans `llm_etats`"
    for adresse in ("openrouter.ai", "api.groq.com"):
        assert adresse not in etats
    assert reponse.json()["llm_models"]["openrouter"] == sentinelles["openrouter"][1], "ancre : llm_models porte bien le modèle servi"
