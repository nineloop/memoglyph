"""Régénérer un compte rendu comme un traitement.

`POST /api/transcript/{name}/report/jobs` fait ce que `POST …/report` fait, avec les mêmes contrôles AVANT
toute création, mais en traitement suivi (vue des traitements, indicateur). La chaîne de génération est la
vraie (versions, front matter, indexation) ; seule l'étape qui appelle le modèle est remplacée.
"""
from __future__ import annotations

import threading
import time

import pytest
import yaml
from fastapi.testclient import TestClient

from api import auth
from api import main as api_main
from api.jobs import JobManager
from core import docid, library, messages, projects, resources, sensitivity
from core.experimental import app_report
from tests.unit.test_cr_rubriques import _sortie

pytestmark = [pytest.mark.integration, pytest.mark.sans_session]

MOT_DE_PASSE = "un mot de passe solide"


@pytest.fixture
def comptes(isolated_stores, tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(auth, "CODE_PATH", tmp_path / ".memoglyph-installation")
    monkeypatch.setattr(api_main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(api_main, "jobs", JobManager())
    auth.creer_compte("admin", MOT_DE_PASSE, admin=True)
    auth.creer_compte("alice", MOT_DE_PASSE)
    auth.creer_compte("bruno", MOT_DE_PASSE)
    return isolated_stores["output_dir"]


def connecte(identifiant: str) -> TestClient:
    client = TestClient(api_main.app)
    assert client.post("/api/auth/connexion", json={
        "identifiant": identifiant, "mot_de_passe": MOT_DE_PASSE}).status_code == 200
    return client


class Modele:
    """Remplace l'appel au modèle : émet les vrais codes d'étape, peut attendre, compte les appels."""

    def __init__(self) -> None:
        self.appels: list[dict] = []
        self.libere = threading.Event()
        self.libere.set()

    def __call__(self, name, item, **options):
        self.appels.append({"name": name, "provider": options.get("provider")})
        progress = options.get("progress") or (lambda *_: None)
        progress(0.05, messages.compte_rendu_extraction())
        assert self.libere.wait(10), "modèle jamais libéré"
        progress(0.5, messages.compte_rendu_organisation())
        progress(0.9, messages.compte_rendu_redaction())
        return _sortie()


@pytest.fixture
def modele(comptes, monkeypatch) -> Modele:
    faux = Modele()
    monkeypatch.setattr(app_report._pipeline, "run_stages", faux)
    from core import report
    monkeypatch.setattr(report, "classify_genre", lambda *a, **k: "projet")
    monkeypatch.setattr(app_report.semantic, "index_name", lambda name: 0)
    return faux


def reunion(sortie, proprietaire: str, nom: str = "reunion.md", sensibilite: str | None = None,
            corps: str = "[00:00:01] Bonjour à tous.\n\n[00:00:04] On fixe le prix.") -> str:
    dossier, projet = projects.personal_inbox(sortie, proprietaire)
    meta = {"id": docid.nouveau(), "meeting_id": docid.nouveau(), "project_id": projet.id, "title": "Point prix"}
    if sensibilite:
        meta["sensitivity"] = sensibilite
    (dossier / nom).write_text(f"---\n{yaml.safe_dump(meta, allow_unicode=True)}---\n\n## Transcript\n\n{corps}\n",
                               encoding="utf-8")
    library.reindex()
    return nom


def lancer(client: TestClient, nom: str, **corps):
    return client.post(f"/api/transcript/{nom}/report/jobs", params={"view_scope": "all"},
                       json={"llm_provider": "mlx", **corps})


def attendre(client: TestClient, job_id: str) -> dict:
    for _ in range(1000):
        etat = client.get(f"/api/jobs/{job_id}").json()
        if etat["status"] in {"done", "error"}:
            return etat
        time.sleep(0.005)
    raise AssertionError("traitement sans fin")


def test_la_regeneration_est_un_traitement_de_nature_compte_rendu(comptes, modele) -> None:
    alice = connecte("alice")
    nom = reunion(comptes, "alice")

    reponse = lancer(alice, nom)

    assert reponse.status_code == 200 and set(reponse.json()) == {"job_id"}
    etat = attendre(alice, reponse.json()["job_id"])
    assert etat["status"] == "done"
    ligne = next(t for t in alice.get("/api/jobs").json()["traitements"] if t["id"] == reponse.json()["job_id"])
    assert ligne["nature"] == "compte-rendu"
    assert ligne["titre"] == "Point prix"                       # le titre de la réunion
    assert ligne["projet_nom"] and ligne["projet_id"]            # le projet de la réunion
    assert ligne["etapes"] == 3                                  # extraction, organisation, rédaction
    resultat = etat["result"]
    assert resultat["name"] != nom and resultat["meeting_id"] and resultat["project_id"]
    assert "content" not in resultat                             # le texte ne voyage pas dans le suivi


def test_une_nouvelle_version_s_ajoute_sans_ecraser_et_dit_son_modele(comptes, modele) -> None:
    alice = connecte("alice")
    nom = reunion(comptes, "alice")

    premier = attendre(alice, lancer(alice, nom).json()["job_id"])["result"]["name"]
    second = attendre(alice, lancer(alice, nom).json()["job_id"])["result"]["name"]

    assert premier != second
    dossier = next(iter(comptes.rglob(premier))).parent
    assert (dossier / premier).exists() and (dossier / second).exists()      # aucune version écrasée
    for version in (premier, second):
        meta, _ = library._split_frontmatter((dossier / version).read_text(encoding="utf-8"))
        assert meta["type"] == "report" and meta["source_transcript"] == nom
        assert meta["model"], "le modèle servi est écrit dans le front matter"
    assert len(modele.appels) == 2 and modele.appels[0]["provider"] == "mlx"


def test_l_etape_avance_avec_les_codes_du_compte_rendu(comptes, modele) -> None:
    alice = connecte("alice")
    nom = reunion(comptes, "alice")
    modele.libere.clear()
    job = lancer(alice, nom).json()["job_id"]

    for _ in range(1000):
        etat = alice.get(f"/api/jobs/{job}").json()
        if etat["message_code"] == "step.report.extract":
            break
        time.sleep(0.005)
    assert etat["etape"] == 1 and etat["etapes"] == 3 and etat["status"] == "running"
    modele.libere.set()
    assert attendre(alice, job)["status"] == "done"


def test_la_route_synchrone_ne_change_pas(comptes, modele) -> None:
    alice = connecte("alice")
    nom = reunion(comptes, "alice")

    reponse = alice.post(f"/api/transcript/{nom}/report", params={"view_scope": "all"}, json={"llm_provider": "mlx"})

    assert reponse.status_code == 200
    assert {"name", "content", "genre"} <= set(reponse.json())
    assert alice.get("/api/jobs").json()["traitements"] == []     # pas de traitement : réponse directe


def test_une_reunion_confidentielle_refuse_un_fournisseur_en_ligne_sans_rien_creer(comptes, modele) -> None:
    alice = connecte("alice")
    nom = reunion(comptes, "alice", sensibilite="ultra_confidential")

    reponse = lancer(alice, nom, llm_provider="mistral")

    assert reponse.status_code == 403 and reponse.json()["detail"] == sensitivity.REFUS_CLOUD
    assert alice.get("/api/jobs").json()["traitements"] == [] and modele.appels == []
    # En local, la même réunion se régénère.
    assert attendre(alice, lancer(alice, nom).json()["job_id"])["status"] == "done"


def test_les_refus_avant_le_traitement(comptes, modele) -> None:
    alice = connecte("alice")
    nom = reunion(comptes, "alice")
    sans_heure = reunion(comptes, "alice", nom="sans-heure.md", corps="Bonjour à tous. On fixe le prix.")

    assert lancer(alice, nom, llm_provider="inconnu").status_code in (400, 422)   # fournisseur invalide
    assert lancer(alice, sans_heure).status_code == 400                           # transcript non citable
    assert alice.post("/api/transcript/absent.md/report/jobs", params={"view_scope": "all"},
                      json={"llm_provider": "mlx"}).status_code == 404
    assert alice.get("/api/jobs").json()["traitements"] == [] and modele.appels == []


def test_un_autre_compte_a_un_404_et_rien_ne_part(comptes, modele) -> None:
    alice, bruno = connecte("alice"), connecte("bruno")
    nom = reunion(comptes, "alice")

    reponse = lancer(bruno, nom)

    assert reponse.status_code == 404
    assert alice.get("/api/jobs").json()["traitements"] == [] and modele.appels == []
    # Le suivi d'Alice reste à Alice.
    job = lancer(alice, nom).json()["job_id"]
    assert bruno.get(f"/api/jobs/{job}").status_code == 404
    assert bruno.get("/api/jobs").json()["traitements"] == []
    attendre(alice, job)


def test_trop_de_traitements_donne_un_429_et_n_enregistre_rien(comptes, modele, monkeypatch) -> None:
    alice = connecte("alice")
    nom = reunion(comptes, "alice")
    monkeypatch.setattr(api_main, "jobs", JobManager(max_active=1))
    modele.libere.clear()
    premier = lancer(alice, nom).json()["job_id"]

    refuse = lancer(alice, nom)

    assert refuse.status_code == 429
    assert len(alice.get("/api/jobs").json()["traitements"]) == 1
    modele.libere.set()
    attendre(alice, premier)
    assert len(modele.appels) == 1


def test_le_traitement_attend_son_tour_et_le_dit(comptes, modele, monkeypatch) -> None:
    alice = connecte("alice")
    nom = reunion(comptes, "alice")
    pris, fin = threading.Event(), threading.Event()

    def occuper() -> None:
        with resources.heavy(label="autre travail"):
            pris.set()
            fin.wait(10)

    fil = threading.Thread(target=occuper)
    fil.start()
    assert pris.wait(5)
    job = lancer(alice, nom).json()["job_id"]
    for _ in range(1000):
        ligne = next(t for t in alice.get("/api/jobs").json()["traitements"] if t["id"] == job)
        if ligne["en_attente"]:
            break
        time.sleep(0.005)
    assert ligne["en_attente"] is True and modele.appels == []
    fin.set()
    fil.join(5)
    assert attendre(alice, job)["status"] == "done"
    assert len(modele.appels) == 1


def test_la_cle_en_ligne_ne_figure_dans_aucun_suivi(comptes, modele) -> None:
    alice = connecte("alice")
    nom = reunion(comptes, "alice")
    cle = "cle-secrete-de-test-0123456789"

    job = lancer(alice, nom, mistral_api_key=cle).json()["job_id"]
    etat = attendre(alice, job)

    assert cle not in alice.get(f"/api/jobs/{job}").text
    assert cle not in alice.get("/api/jobs").text
    assert cle not in str(etat)


def test_une_regeneration_en_attente_annulee_n_ecrit_aucun_compte_rendu(comptes, modele) -> None:
    """La régénération passe par le MÊME assistant de créneau que la transcription."""
    from tests.integration.test_annulation_job import creneau_tenu, _fils_de_traitement
    alice = connecte("alice")
    nom = reunion(comptes, "alice")
    avant_fichiers = sorted(p.name for p in comptes.rglob("*.md"))
    fils_avant = {id(t) for t in _fils_de_traitement()}
    try:
        with creneau_tenu():
            job = lancer(alice, nom).json()["job_id"]
            for _ in range(1000):
                if alice.get(f"/api/jobs/{job}").json()["en_attente"]:
                    break
                time.sleep(0.005)
            assert alice.get(f"/api/jobs/{job}").json()["en_attente"] is True, "ancre : il attend sa place"

            reponse = alice.post(f"/api/jobs/{job}/cancel")

            assert reponse.status_code == 200 and reponse.json()["statut"] == "annule"
    finally:                                   # le créneau est libéré ; on attend la fin du fil, quoi qu'il arrive
        for fil in _fils_de_traitement():
            if id(fil) not in fils_avant:
                fil.join(15)
    assert modele.appels == [], "le modèle n'a pas été appelé"
    assert sorted(p.name for p in comptes.rglob("*.md")) == avant_fichiers, "aucun compte rendu écrit"
    etat = alice.get(f"/api/jobs/{job}").json()
    assert etat["status"] == "error" and etat["error_code"] == "step.error.cancelled"
    # Ancre : sans annulation, la même chaîne appelle bien le modèle une fois.
    second = lancer(alice, nom).json()["job_id"]
    assert attendre(alice, second)["status"] == "done" and len(modele.appels) == 1
