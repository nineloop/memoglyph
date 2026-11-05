"""Annuler un traitement en attente de place.

Un traitement « en attente » est un traitement dont le fil attend le créneau de calcul lourd (`running` +
`en_attente`). Ce fichier éprouve, sur la chaîne réelle de l'API, ce qui touche au fichier reçu et à la route
d'annulation.

Le créneau lourd est GLOBAL à toute la session de tests : chaque tenue du créneau est dans un `try/finally`, chaque
attente a un délai, et le démontage attend la FIN DES FILS de traitement (pas seulement leur état : un traitement
annulé est « en échec » tout de suite, mais son fil peut rester bloqué sur le créneau, qu'il prend puis rend aussitôt).
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from api import auth
from api import main as api_main
from api.jobs import JobManager
from core import config, docid, projects, resources

pytestmark = [pytest.mark.integration, pytest.mark.sans_session]

MOT_DE_PASSE = "un mot de passe solide"
CONTENU = b"un enregistrement de reunion, premier"


@pytest.fixture
def comptes(isolated_stores, tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(auth, "CODE_PATH", tmp_path / ".memoglyph-installation")
    monkeypatch.setattr(api_main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(api_main, "jobs", JobManager(max_active=10))
    auth.creer_compte("admin", MOT_DE_PASSE, admin=True)
    auth.creer_compte("alice", MOT_DE_PASSE)
    auth.creer_compte("bruno", MOT_DE_PASSE)
    return tmp_path


def connecte(identifiant: str) -> TestClient:
    client = TestClient(api_main.app)
    assert client.post("/api/auth/connexion", json={
        "identifiant": identifiant, "mot_de_passe": MOT_DE_PASSE}).status_code == 200
    return client


def _fils_de_traitement() -> list[threading.Thread]:
    return [t for t in threading.enumerate()
            if t.is_alive() and getattr(getattr(t, "_target", None), "__qualname__", "").endswith("run.<locals>._worker")]


@pytest.fixture
def fils_termines():
    """Au démontage, tous les fils de traitement ont fini : aucun ne tient encore le créneau global."""
    avant = {id(t) for t in _fils_de_traitement()}
    yield
    for fil in _fils_de_traitement():
        if id(fil) not in avant:
            fil.join(15)
            assert not fil.is_alive(), "un fil de traitement tient encore le créneau après le test"


class FauxPipeline:
    """Écrit un `.md` par appel ; garde la trace des appels et des indexations ; peut attendre ou échouer."""

    def __init__(self) -> None:
        self.appels = 0
        self.indexations: list[str] = []
        self.libere = threading.Event()
        self.libere.set()
        self.echouer = False

    def __call__(self, path, **options):
        self.appels += 1
        assert self.libere.wait(10), "pipeline jamais libéré"
        if self.echouer:
            raise RuntimeError("le moteur a planté")
        meta = (f"---\nid: {docid.nouveau()}\nmeeting_id: {docid.nouveau()}\n"
                f"project_id: {projects.load(options['output_dir']).id}\ntitle: t\n---\n\nTexte")
        sortie = options["output_dir"] / f"reunion-{self.appels}.md"
        sortie.write_text(meta, encoding="utf-8")
        return sortie, "Texte"


@pytest.fixture
def pipeline(comptes, fils_termines, monkeypatch):
    faux = FauxPipeline()
    monkeypatch.setattr(api_main.pipeline, "run", faux)
    monkeypatch.setattr(api_main.semantic, "index_name", lambda name: faux.indexations.append(name) or 0)
    monkeypatch.setattr(api_main.voiceprint, "index_name", lambda name: faux.indexations.append(name) or {})
    yield faux
    faux.libere.set()          # quoi qu'il arrive, aucun fil de traitement ne reste suspendu sur le pipeline


@contextmanager
def creneau_tenu():
    """Un autre travail lourd tient le créneau global jusqu'à la sortie du bloc (libéré quoi qu'il arrive)."""
    pris, relache = threading.Event(), threading.Event()

    def occuper() -> None:
        with resources.heavy(label="autre travail"):
            pris.set()
            relache.wait(30)

    fil = threading.Thread(target=occuper)
    fil.start()
    try:
        assert pris.wait(5), "le créneau n'a pas pu être pris"
        yield relache
    finally:
        relache.set()
        fil.join(10)


def envoyer(client: TestClient, contenu: bytes = CONTENU, nom: str = "visio.wav", **champs):
    return client.post("/api/transcribe", files={"file": (nom, contenu)}, data=champs)


def attendre(client: TestClient, job_id: str, statuts: tuple[str, ...] = ("done", "error")) -> dict:
    for _ in range(2000):
        etat = client.get(f"/api/jobs/{job_id}").json()
        if etat["status"] in statuts:
            return etat
        time.sleep(0.005)
    raise AssertionError("traitement sans fin")


def attendre_en_attente(client: TestClient, job_id: str) -> None:
    for _ in range(2000):
        if client.get(f"/api/jobs/{job_id}").json()["en_attente"]:
            return
        time.sleep(0.005)
    raise AssertionError("le traitement n'attend jamais sa place")


def dossiers_d_envoi(racine) -> list[str]:
    envois = racine / "uploads"
    return sorted(p.name for p in envois.iterdir()) if envois.exists() else []


# ---------- la fuite existante, prouvée d'abord ----------

def test_apres_trop_d_attente_le_fichier_recu_est_efface(comptes, pipeline, monkeypatch) -> None:
    alice = connecte("alice")
    monkeypatch.setattr(config, "ATTENTE_DE_FOND_API_S", 0.3)      # petit ET positif : à 0, `heavy` n'attendrait pas

    with creneau_tenu():
        job = envoyer(alice).json()["job_id"]
        etat = attendre(alice, job)

    # Ancre : l'échec vient bien de « trop d'attente » (sinon un rouge pourrait venir d'un réglage sans effet).
    assert etat["status"] == "error" and etat["error_code"] == "step.error.waitTooLong"
    assert pipeline.appels == 0
    assert dossiers_d_envoi(comptes) == [], "le fichier reçu et son dossier doivent être effacés"


# ---------- la route d'annulation ----------

def _id_du_compte(identifiant: str) -> str:
    return auth.utilisateur_actif(identifiant).id


def _en_attente(client: TestClient, relache_apres: bool = False) -> str:
    """Envoie un fichier pendant que le créneau est tenu ailleurs ; rend le traitement quand il attend vraiment."""
    job = envoyer(client).json()["job_id"]
    attendre_en_attente(client, job)
    # Garde : la détection des fils (attribut privé `_target`, nom qualifié) trouve bien le fil qui attend ; sinon
    # les vérifications « jamais exécuté » après la libération perdraient silencieusement leur attente.
    assert _fils_de_traitement(), "ancre : le fil de traitement en attente doit être détecté"
    return job


def _fichiers_md(bibliotheque) -> list[str]:
    return sorted(str(p.relative_to(bibliotheque)) for p in bibliotheque.rglob("*.md"))


def test_ancre_un_traitement_non_annule_ecrit_sa_reunion(comptes, pipeline, isolated_stores) -> None:
    """Sans cette ancre, « aucune réunion écrite » après une annulation pourrait être vrai pour une mauvaise raison."""
    alice = connecte("alice")
    with creneau_tenu():
        job = _en_attente(alice)
    assert attendre(alice, job)["status"] == "done"
    assert pipeline.appels == 1 and len(_fichiers_md(isolated_stores["output_dir"])) == 1
    assert len(pipeline.indexations) >= 1


def test_le_proprietaire_annule_un_traitement_en_attente_et_rien_n_est_ecrit(
        comptes, pipeline, isolated_stores) -> None:
    alice = connecte("alice")
    with creneau_tenu():
        job = _en_attente(alice)
        assert dossiers_d_envoi(comptes) == [job], "ancre : le fichier reçu est là avant l'annulation"
        assert api_main.jobs.active_count() == 1

        reponse = alice.post(f"/api/jobs/{job}/cancel")

        assert reponse.status_code == 200
        assert reponse.json() == {"statut": "annule", "job_id": job}
        etat = alice.get(f"/api/jobs/{job}").json()
        assert etat["status"] == "error" and etat["error_code"] == "step.error.cancelled"
        assert etat["en_attente"] is False and etat["error"] == "Traitement annulé."
        assert api_main.jobs.active_count() == 0, "la place est rendue"
        assert dossiers_d_envoi(comptes) == [], "le fichier reçu est effacé tout de suite"
        ligne = next(t for t in alice.get("/api/jobs").json()["traitements"] if t["id"] == job)
        assert ligne["statut"] == "error" and ligne["erreur_code"] == "step.error.cancelled"
        assert ligne["en_attente"] is False

    # Le créneau est libéré : le fil du traitement l'obtient, le rend aussitôt et n'exécute rien.
    for fil in _fils_de_traitement():
        fil.join(10)
    assert pipeline.appels == 0 and pipeline.indexations == []
    assert _fichiers_md(isolated_stores["output_dir"]) == []
    assert alice.get(f"/api/jobs/{job}").json()["error_code"] == "step.error.cancelled"   # rien ne l'a réécrit


def test_annuler_deux_fois_est_sans_effet(comptes, pipeline) -> None:
    alice = connecte("alice")
    with creneau_tenu():
        job = _en_attente(alice)
        premier = alice.post(f"/api/jobs/{job}/cancel")
        evenements = len(api_main.jobs.get(job).events)

        second = alice.post(f"/api/jobs/{job}/cancel")

        assert premier.status_code == second.status_code == 200
        assert second.json() == premier.json()
        assert len(api_main.jobs.get(job).events) == evenements, "pas de second événement final"


def test_un_autre_compte_a_le_meme_404_qu_un_identifiant_inconnu(comptes, pipeline) -> None:
    alice, bruno = connecte("alice"), connecte("bruno")
    with creneau_tenu():
        job = _en_attente(alice)
        inconnu = bruno.post("/api/jobs/identifiant-inconnu/cancel")
        voisin = bruno.post(f"/api/jobs/{job}/cancel")

        assert inconnu.status_code == 404
        assert (voisin.status_code, voisin.json()) == (inconnu.status_code, inconnu.json())
        assert alice.get(f"/api/jobs/{job}").json()["en_attente"] is True, "ancre : rien n'a bougé pour Alice"
        assert alice.post(f"/api/jobs/{job}/cancel").status_code == 200, "ancre : la route existe pour le propriétaire"


def test_un_traitement_qui_n_attend_plus_refuse_l_annulation_et_dit_son_etat(comptes, pipeline) -> None:
    alice = connecte("alice")
    # en cours (la place est obtenue, le pipeline tourne)
    pipeline.libere.clear()
    en_cours = envoyer(alice).json()["job_id"]
    for _ in range(2000):
        etat = alice.get(f"/api/jobs/{en_cours}").json()
        if etat["status"] == "running" and not etat["en_attente"]:
            break
        time.sleep(0.005)
    reponse = alice.post(f"/api/jobs/{en_cours}/cancel")
    assert reponse.status_code == 409
    assert reponse.json()["code"] == "job.cancel.impossible" and reponse.json()["statut"] == "running"
    assert reponse.json()["detail"]
    assert alice.get(f"/api/jobs/{en_cours}").json()["status"] == "running", "le traitement continue"
    pipeline.libere.set()
    assert attendre(alice, en_cours)["status"] == "done"
    # terminé
    termine = alice.post(f"/api/jobs/{en_cours}/cancel")
    assert termine.status_code == 409 and termine.json()["statut"] == "done"
    assert alice.get(f"/api/jobs/{en_cours}").json()["status"] == "done"
    # en échec, non annulé
    pipeline.echouer = True
    rate = envoyer(alice, contenu=b"un autre contenu").json()["job_id"]
    assert attendre(alice, rate)["status"] == "error"
    refus = alice.post(f"/api/jobs/{rate}/cancel")
    assert refus.status_code == 409 and refus.json()["statut"] == "error"
    assert alice.get(f"/api/jobs/{rate}").json()["error_code"] != "step.error.cancelled"


def test_un_traitement_encore_en_reception_refuse_l_annulation(comptes, pipeline) -> None:
    alice = connecte("alice")
    en_reception = api_main.jobs.create(owner=_id_du_compte("alice"), nature="transcription")
    assert en_reception.status == "pending"

    reponse = alice.post(f"/api/jobs/{en_reception.id}/cancel")

    assert reponse.status_code == 409 and reponse.json()["statut"] == "pending"
    assert en_reception.status == "pending"


def test_le_flux_d_evenements_se_termine_par_une_erreur_annule(comptes, pipeline) -> None:
    alice = connecte("alice")
    with creneau_tenu():
        job = _en_attente(alice)
        alice.post(f"/api/jobs/{job}/cancel")

        flux = alice.get(f"/api/jobs/{job}/events")

        assert flux.status_code == 200
        donnees = [ligne for ligne in flux.text.splitlines() if ligne.startswith("data: ")]
        assert donnees and '"type": "error"' in donnees[-1]
        assert '"code": "step.error.cancelled"' in donnees[-1] and "Traitement annulé." in donnees[-1]


def test_une_capture_annulee_peut_etre_renvoyee_avec_le_meme_identifiant(comptes, pipeline) -> None:
    """Un traitement annulé ne compte pas comme « capture reçue »."""
    alice = connecte("alice")
    secret = alice.post("/api/jetons", json={"nom": "MacBook de test"}).json()["secret"]
    compagnon = TestClient(api_main.app, headers={"Authorization": f"Bearer {secret}"})
    capture = "11111111-2222-3333-4444-555555555555"
    champs = {"capture_application": "zoom.us", "capture_mode": "visio", "capture_id": capture}
    with creneau_tenu():
        envoi = envoyer(compagnon, **champs)
        assert envoi.status_code == 200, envoi.text
        premier = envoi.json()["job_id"]
        attendre_en_attente(compagnon, premier)
        assert alice.post(f"/api/jobs/{premier}/cancel").status_code == 200

        renvoi = envoyer(compagnon, **champs)

        assert renvoi.status_code == 200 and renvoi.json().get("job_id") not in (None, premier)


def test_le_texte_de_l_envoi_annule_n_expose_ni_chemin_ni_empreinte(comptes, pipeline) -> None:
    alice = connecte("alice")
    with creneau_tenu():
        job = _en_attente(alice)
        assert alice.post(f"/api/jobs/{job}/cancel").status_code == 200, "ancre : l'annulation a eu lieu"
        sortie = alice.get(f"/api/jobs/{job}").text + alice.get("/api/jobs").text
        assert str(comptes) not in sortie and "uploads" not in sortie
        assert api_main.jobs.get(job).empreinte not in sortie
