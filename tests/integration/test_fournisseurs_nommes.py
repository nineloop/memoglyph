"""Les fournisseurs nommés vus par l'API : `/api/config`, plusieurs à la fois, la régénération.

Aucun appel réel : tout passe par un transport simulé ou par le niveau de simulation de `test_regeneration_job.py`
(`run_stages`, comme pour Mistral). La garde réseau de la suite vérifie qu'aucune de ces routes ne sort de la machine.
"""
from __future__ import annotations

import importlib

import pytest

from api import auth
from api import main as api_main
from api.jobs import JobManager
from core import config, llm
from core.experimental import app_report
from tests.integration.test_regeneration_job import Modele, attendre, connecte, lancer, reunion

pytestmark = [pytest.mark.integration, pytest.mark.sans_session]

MOT_DE_PASSE = "un mot de passe solide"


@pytest.fixture
def comptes(isolated_stores, tmp_path, monkeypatch):
    """Les deux comptes de test, sur des magasins isolés (même mise en place que `test_regeneration_job.py`)."""
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(auth, "CODE_PATH", tmp_path / ".memoglyph-installation")
    monkeypatch.setattr(api_main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(api_main, "jobs", JobManager())
    auth.creer_compte("admin", MOT_DE_PASSE, admin=True)
    auth.creer_compte("alice", MOT_DE_PASSE)
    return isolated_stores["output_dir"]


@pytest.fixture
def modele(comptes, monkeypatch) -> Modele:
    """Le niveau de simulation de la régénération (`run_stages`), comme pour Mistral dans `test_regeneration_job.py`."""
    from core import report
    faux = Modele()
    monkeypatch.setattr(app_report._pipeline, "run_stages", faux)
    monkeypatch.setattr(report, "classify_genre", lambda *a, **k: "projet")
    monkeypatch.setattr(app_report.semantic, "index_name", lambda name: 0)
    return faux

NOMS = ["openrouter", "groq"]
CLE = "cle-fictive-de-test-0123456789"
MODELE = "auteur/un-modele-de-test"


@pytest.fixture
def nomme():
    return importlib.import_module("core.llm_nomme")


@pytest.fixture(autouse=True)
def environnement_propre(monkeypatch):
    for nom in NOMS:
        for variable in (f"{nom.upper()}_API_KEY", f"MEMOGLYPH_{nom.upper()}_MODEL", f"{nom.upper()}_MODEL"):
            monkeypatch.setenv(variable, "x")
            monkeypatch.delenv(variable)


def _prets(monkeypatch, *noms) -> None:
    for nom in noms:
        monkeypatch.setenv(f"{nom.upper()}_API_KEY", CLE)
        monkeypatch.setenv(f"MEMOGLYPH_{nom.upper()}_MODEL", f"{nom}/modele-de-test")


def test_config_liste_les_deux_noms_dans_le_registre_meme_non_prets(comptes, nomme) -> None:
    config_api = connecte("alice").get("/api/config").json()
    for nom in NOMS:
        assert nom in config_api["llm_ready"] and config_api["llm_ready"][nom] is False
        assert config_api["llm_models"][nom] == f"{nom}-non-configure"         # un nom « non configuré » PAR profil, pas de réseau
        assert nom not in config_api["llm_providers"], "non prêt : non proposé"


def test_plusieurs_a_la_fois_chacun_sous_son_nom_et_jamais_local_ni_cloud_prive(comptes, nomme, monkeypatch) -> None:
    monkeypatch.setattr(config, "COMPATIBLE_BASE_URL", "https://serveur.example/v1")        # un « compatible » distant (cloud privé)
    monkeypatch.setattr(config, "COMPATIBLE_MODEL", "m")
    _prets(monkeypatch, *NOMS)
    config_api = connecte("alice").get("/api/config").json()
    for nom in NOMS:
        assert nom in config_api["llm_providers"] and config_api["llm_ready"][nom] is True
        assert config_api["llm_models"][nom] == f"{nom}/modele-de-test"
        assert nom not in config_api["local_providers"]
        assert nom not in config_api["private_cloud_providers"]
    assert "compatible" in config_api["private_cloud_providers"]                    # ancre : un cloud privé s'y trouve bien


@pytest.mark.parametrize("nom", NOMS)
def test_une_adresse_locale_injectee_ne_fait_pas_du_nom_un_fournisseur_local(comptes, nomme, monkeypatch, nom) -> None:
    _prets(monkeypatch, nom)
    profil = nomme.avec_adresse(nomme.PROFILS[nom], "http://127.0.0.1:9/v1")
    monkeypatch.setitem(llm.MOTEURS, nom, nomme.MoteurNomme(profil))
    config_api = connecte("alice").get("/api/config").json()
    assert nom in config_api["llm_providers"]
    assert nom not in config_api["local_providers"] and nom not in config_api["private_cloud_providers"]


@pytest.mark.parametrize("nom", NOMS)
def test_la_regeneration_accepte_le_nom_et_le_traite_comme_un_fournisseur_en_ligne(comptes, modele, monkeypatch, nom) -> None:
    _prets(monkeypatch, nom)
    alice = connecte("alice")
    document = reunion(comptes, "alice")
    reponse = lancer(alice, document, llm_provider=nom)
    assert reponse.status_code == 200, reponse.text
    assert attendre(alice, reponse.json()["job_id"])["status"] == "done"
    assert [a["provider"] for a in modele.appels] == [nom]


@pytest.mark.parametrize("nom", NOMS)
def test_un_document_ultra_confidentiel_est_refuse_pour_un_nom_en_ligne(comptes, modele, monkeypatch, nom) -> None:
    _prets(monkeypatch, nom)
    alice = connecte("alice")
    document = reunion(comptes, "alice", sensibilite="ultra_confidential")
    reponse = lancer(alice, document, llm_provider=nom)
    assert reponse.status_code == 403
    assert alice.get("/api/jobs").json()["traitements"] == [] and modele.appels == []      # rien créé, rien envoyé


def test_un_nom_inconnu_reste_refuse_en_400(comptes, modele) -> None:
    alice = connecte("alice")
    document = reunion(comptes, "alice")
    assert lancer(alice, document, llm_provider="openrouteur").status_code in (400, 422)
