"""GET /api/jobs : les traitements du compte connecté, et rien d'un autre."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api import auth
from api import main as api_main
from api.jobs import JobManager

pytestmark = [pytest.mark.integration, pytest.mark.sans_session]

MOT_DE_PASSE = "un mot de passe solide"


@pytest.fixture
def comptes(isolated_stores, tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(auth, "CODE_PATH", tmp_path / ".memoglyph-installation")
    monkeypatch.setattr(api_main, "UPLOAD_DIR", tmp_path / "uploads")
    gestionnaire = JobManager(max_active=20)
    monkeypatch.setattr(api_main, "jobs", gestionnaire)
    auth.creer_compte("admin", MOT_DE_PASSE, admin=True)
    auth.creer_compte("alice", MOT_DE_PASSE)
    auth.creer_compte("bruno", MOT_DE_PASSE)
    return gestionnaire


def connecte(identifiant: str) -> TestClient:
    client = TestClient(api_main.app)
    assert client.post("/api/auth/connexion", json={
        "identifiant": identifiant, "mot_de_passe": MOT_DE_PASSE}).status_code == 200
    return client


def _id(identifiant: str) -> str:
    return auth.utilisateur_actif(identifiant).id


def test_la_liste_rend_les_traitements_du_compte_avec_les_champs_du_contrat(comptes) -> None:
    alice = _id("alice")
    actif = comptes.create(owner=alice, nature="transcription", titre="visio du 2 novembre",
                           projet_id="P1", projet_nom="Réunions AMI", plan=["transcription", "ecriture"])
    actif.status, actif.progress, actif.etape = "running", 0.42, 1
    attente = comptes.create(owner=alice, nature="compte-rendu", titre="Comité")
    attente.en_attente = True
    fini = comptes.create(owner=alice, nature="transcription", titre="Ancienne")
    fini.status, fini.result = "done", {"name": "a.md", "content": "TOUT LE TEXTE", "meeting_id": "M1",
                                        "project_id": "P1"}
    echec = comptes.create(owner=alice, nature="transcription", titre="Ratée")
    echec.status, echec.error = "error", "boum"

    corps = connecte("alice").get("/api/jobs").json()
    par_id = {t["id"]: t for t in corps["traitements"]}
    assert set(par_id) == {actif.id, attente.id, fini.id, echec.id}
    assert corps["actifs"] == 1 and corps["en_attente"] == 1
    e = par_id[actif.id]
    assert (e["nature"], e["statut"], e["titre"], e["projet_id"], e["projet_nom"]) == (
        "transcription", "running", "visio du 2 novembre", "P1", "Réunions AMI")
    assert (e["etape"], e["etapes"], e["progression"], e["en_attente"]) == (1, 2, 0.42, False)
    assert par_id[attente.id]["en_attente"] is True
    assert par_id[fini.id]["statut"] == "done"
    assert par_id[fini.id]["meeting"] == {"name": "a.md", "meeting_id": "M1", "project_id": "P1"}
    assert par_id[echec.id]["statut"] == "error" and par_id[echec.id]["erreur"]
    # Les actifs d'abord, puis l'historique du plus récent au plus ancien.
    ordre = [t["statut"] for t in corps["traitements"]]
    assert ordre.index("running") < ordre.index("done")


def test_la_liste_est_legere_ni_texte_ni_jeton_ni_evenements(comptes) -> None:
    alice = _id("alice")
    job = comptes.create(owner=alice, nature="transcription", titre="T", jeton="J-SECRET")
    job.status, job.result = "done", {"name": "a.md", "content": "LE TEXTE ENTIER DU TRANSCRIPT"}
    brut = connecte("alice").get("/api/jobs").text
    assert "LE TEXTE ENTIER" not in brut and "content" not in brut
    assert "J-SECRET" not in brut and '"jeton"' not in brut and '"events"' not in brut


def test_etancheite_a_deux_comptes(comptes) -> None:
    alice, bruno = _id("alice"), _id("bruno")
    comptes.create(owner=alice, nature="transcription", titre="Réunion secrète d'Alice",
                   projet_id="PA", projet_nom="Projet d'Alice")
    comptes.create(owner=bruno, nature="transcription", titre="Réunion de Bruno",
                   projet_nom="Projet de Bruno").status = "running"
    vue_de_bruno = connecte("bruno").get("/api/jobs")
    assert vue_de_bruno.status_code == 200
    for fuite in ("secrète", "Alice", "PA"):
        assert fuite not in vue_de_bruno.text
    assert [t["titre"] for t in vue_de_bruno.json()["traitements"]] == ["Réunion de Bruno"]
    # Les comptes ne comptent que ses propres traitements : il ne devine pas qu'Alice est occupée.
    assert vue_de_bruno.json()["actifs"] == 1
    vue_d_alice = connecte("alice").get("/api/jobs").json()
    assert (vue_d_alice["actifs"], vue_d_alice["en_attente"]) == (0, 1)      # le sien : en attente
    assert "Réunion de Bruno" not in str(vue_d_alice)


def test_sans_session_ni_jeton_la_route_refuse(comptes) -> None:
    assert TestClient(api_main.app).get("/api/jobs").status_code == 401


def test_le_jeton_d_appareil_ne_voit_que_les_comptes_et_ses_propres_traitements(comptes) -> None:
    alice_client = connecte("alice")
    alice = auth.utilisateur_actif("alice")
    description, secret = auth.creer_jeton(alice, "mon Mac")
    jeton_id = description["id"]
    autre_secret = auth.creer_jeton(alice, "ancien Mac")[1]
    # Trois traitements du MÊME compte : un né de ce jeton, un d'un autre jeton, un lancé à la main.
    mien = comptes.create(owner=alice.id, nature="transcription", titre="capture du Mac", jeton=jeton_id)
    mien.status = "running"
    comptes.create(owner=alice.id, nature="transcription", titre="CAPTURE DE L'AUTRE JETON",
                   jeton="autre-id").status = "running"
    comptes.create(owner=alice.id, nature="compte-rendu", titre="TITRE PRIVÉ DE LA SESSION",
                   projet_nom="PROJET PRIVÉ").en_attente = True

    corps = TestClient(api_main.app, headers={"Authorization": f"Bearer {secret}"}).get("/api/jobs")
    assert corps.status_code == 200
    donnees = corps.json()
    assert [t["titre"] for t in donnees["traitements"]] == ["capture du Mac"]
    for fuite in ("AUTRE JETON", "PRIVÉ", "TITRE PRIVÉ"):
        assert fuite not in corps.text
    # Les comptes, eux, sont ceux du compte (« en attente : N traitements »).
    assert donnees["actifs"] == 2 and donnees["en_attente"] == 1
    # Et la session voit tout.
    assert len(alice_client.get("/api/jobs").json()["traitements"]) == 3
    del autre_secret


def test_le_jeton_ne_lit_que_les_traitements_nes_de_ce_jeton(comptes) -> None:
    """`GET /api/jobs/{id}` (et ses événements) avec un jeton d'appareil : strict nécessaire."""
    alice = auth.utilisateur_actif("alice")
    description, secret = auth.creer_jeton(alice, "mon Mac")
    _autre, secret_autre = auth.creer_jeton(alice, "ancien Mac")
    mien = comptes.create(owner=alice.id, nature="transcription", titre="MA CAPTURE", jeton=description["id"])
    de_la_session = comptes.create(owner=alice.id, nature="transcription", titre="TITRE DE LA SESSION")
    d_un_autre_jeton = comptes.create(owner=alice.id, nature="transcription", titre="AUTRE JETON", jeton="autre")

    compagnon = TestClient(api_main.app, headers={"Authorization": f"Bearer {secret}"})
    assert compagnon.get(f"/api/jobs/{mien.id}").status_code == 200
    for interdit in (de_la_session, d_un_autre_jeton):
        reponse = compagnon.get(f"/api/jobs/{interdit.id}")
        assert reponse.status_code == 404, interdit.titre
        assert interdit.titre not in reponse.text
        assert compagnon.get(f"/api/jobs/{interdit.id}/events").status_code == 404
    # La session, elle, lit tout ce qui est à elle.
    session = connecte("alice")
    assert session.get(f"/api/jobs/{de_la_session.id}").status_code == 200
    assert session.get(f"/api/jobs/{mien.id}").status_code == 200
    del secret_autre


def test_get_job_garde_ses_cles_et_gagne_les_nouvelles(comptes) -> None:
    alice = _id("alice")
    job = comptes.create(owner=alice, nature="transcription", titre="T", projet_nom="P")
    corps = connecte("alice").get(f"/api/jobs/{job.id}").json()
    for cle in ("id", "status", "progress", "message", "message_code", "message_params", "result", "error"):
        assert cle in corps
    for cle in ("titre", "projet_id", "projet_nom", "etape", "etapes", "en_attente", "debut"):
        assert cle in corps


def test_un_message_d_attente_trop_longue_est_rendu_avec_son_code(comptes) -> None:
    alice = _id("alice")
    job = comptes.create(owner=alice, nature="transcription", titre="T")
    job.status, job.error, job.error_code, job.error_params = (
        "error", "Ressources occupées : un autre traitement est en cours.",
        "step.error.waitTooLong", {"label": "traitement"})
    e = connecte("alice").get("/api/jobs").json()["traitements"][0]
    assert e["erreur"] == "Ressources occupées : un autre traitement est en cours."
    assert (e["erreur_code"], e["erreur_params"]) == ("step.error.waitTooLong", {"label": "traitement"})
