"""Un second envoi du même fichier est refusé tant que le premier attend ou tourne.

`POST /api/transcribe` répond 409 quand le MÊME compte a déjà un traitement non final (en attente ou en cours)
pour le MÊME contenu (SHA-256), sauf pour un envoi qui porte un identifiant de capture ou qui vient d'un jeton d'appareil (le compagnon :
un 409 serait pour lui un refus définitif, et nommerait un traitement de la session). Le refus passe par le même nettoyage qu'un envoi trop gros : le traitement
créé pour l'envoi est abandonné, son fichier et son dossier effacés, aucune place n'est perdue.
"""
from __future__ import annotations

import hashlib
import threading
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from api import auth
from api import main as api_main
from api.jobs import JobManager
from core import docid, projects, resources

pytestmark = [pytest.mark.integration, pytest.mark.sans_session]

MOT_DE_PASSE = "un mot de passe solide"
CONTENU = b"un enregistrement de reunion, premier"
AUTRE_CONTENU = b"un autre enregistrement, tout different"


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


class FauxPipeline:
    """Écrit un `.md` par appel ; peut attendre (traitement « en cours ») ou échouer."""

    def __init__(self) -> None:
        self.appels = 0
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
def pipeline(comptes, monkeypatch) -> FauxPipeline:
    faux = FauxPipeline()
    monkeypatch.setattr(api_main.pipeline, "run", faux)
    monkeypatch.setattr(api_main.semantic, "index_name", lambda name: 0)
    monkeypatch.setattr(api_main.voiceprint, "index_name", lambda name: {})
    return faux


@pytest.fixture
def libere_a_la_fin(pipeline):
    """Garantit qu'aucun fil de pipeline ne reste suspendu après le test, quoi qu'il arrive.

    Le créneau de calcul lourd est GLOBAL à la session de tests : un fil qui le tient encore quand le test suivant
    démarre le lui fait refuser. On attend donc la fin de tous les traitements.
    """
    yield
    pipeline.libere.set()
    attendre_la_fin_de_tout()


def envoyer(client: TestClient, contenu: bytes = CONTENU, nom: str = "visio.wav", **champs):
    return client.post("/api/transcribe", files={"file": (nom, contenu)}, data=champs)


def attendre(client: TestClient, job_id: str, statuts: tuple[str, ...] = ("done", "error")) -> dict:
    for _ in range(2000):
        etat = client.get(f"/api/jobs/{job_id}").json()
        if etat["status"] in statuts:
            return etat
        time.sleep(0.005)
    raise AssertionError("traitement sans fin")


def attendre_en_cours(client: TestClient, job_id: str) -> None:
    for _ in range(2000):
        etat = client.get(f"/api/jobs/{job_id}").json()
        if etat["status"] == "running" and not etat["en_attente"]:
            return
        time.sleep(0.005)
    raise AssertionError("le traitement n'est jamais passé en cours")


def attendre_la_fin_de_tout() -> None:
    for _ in range(4000):
        if api_main.jobs.active_count() == 0:
            return
        time.sleep(0.005)
    raise AssertionError("un traitement tient encore le créneau après le test")


def dossiers_d_envoi(racine) -> list[str]:
    envois = racine / "uploads"
    return sorted(p.name for p in envois.iterdir()) if envois.exists() else []


def test_meme_contenu_meme_compte_pendant_que_le_premier_tourne_est_refuse_en_nommant_le_premier(
        comptes, pipeline, libere_a_la_fin) -> None:
    alice = connecte("alice")
    pipeline.libere.clear()
    premier = envoyer(alice).json()["job_id"]
    attendre_en_cours(alice, premier)

    refus = envoyer(alice, nom="copie.wav")

    assert refus.status_code == 409
    corps = refus.json()
    assert corps["code"] == "transcribe.duplicate" and corps["job_id"] == premier
    assert corps["titre"] == "visio" and corps["detail"]               # le titre du traitement EXISTANT, une phrase
    assert pipeline.appels == 1                                          # rien n'a été relancé
    pipeline.libere.set()
    assert attendre(alice, premier)["status"] == "done"


def test_meme_contenu_pendant_que_le_premier_attend_sa_place_est_refuse(comptes, pipeline, libere_a_la_fin) -> None:
    alice = connecte("alice")
    place_prise, relache = threading.Event(), threading.Event()

    def occuper() -> None:
        with resources.heavy(label="autre travail"):
            place_prise.set()
            relache.wait(15)

    fil = threading.Thread(target=occuper)
    fil.start()
    try:
        assert place_prise.wait(5)
        premier = envoyer(alice).json()["job_id"]
        for _ in range(2000):
            if alice.get(f"/api/jobs/{premier}").json()["en_attente"]:
                break
            time.sleep(0.005)
        assert alice.get(f"/api/jobs/{premier}").json()["en_attente"] is True, "ancre : le premier attend bien sa place"

        refus = envoyer(alice)

        assert refus.status_code == 409 and refus.json()["job_id"] == premier
    finally:
        relache.set()
        fil.join(10)
    assert attendre(alice, premier)["status"] == "done"


def test_un_refus_ne_laisse_ni_dossier_d_envoi_ni_place_perdue(comptes, pipeline, libere_a_la_fin) -> None:
    alice = connecte("alice")
    pipeline.libere.clear()
    premier = envoyer(alice).json()["job_id"]
    attendre_en_cours(alice, premier)
    actifs = api_main.jobs.active_count()
    dossiers = dossiers_d_envoi(comptes)
    assert dossiers == [premier], "ancre : seul le dossier du premier envoi existe"

    for _ in range(3):
        assert envoyer(alice).status_code == 409

    assert api_main.jobs.active_count() == actifs                       # aucun traitement « pending » ne reste
    assert dossiers_d_envoi(comptes) == [premier]                         # ni fichier ni dossier du refusé
    assert len(alice.get("/api/jobs").json()["traitements"]) == 1


def test_apres_la_fin_du_premier_un_nouvel_envoi_du_meme_fichier_est_accepte(comptes, pipeline) -> None:
    alice = connecte("alice")
    premier = envoyer(alice).json()["job_id"]
    assert attendre(alice, premier)["status"] == "done"

    second = envoyer(alice)

    assert second.status_code == 200 and second.json()["job_id"] != premier
    assert attendre(alice, second.json()["job_id"])["status"] == "done"


def test_un_premier_traitement_en_echec_ne_compte_plus(comptes, pipeline) -> None:
    alice = connecte("alice")
    pipeline.echouer = True
    premier = envoyer(alice).json()["job_id"]
    assert attendre(alice, premier)["status"] == "error"

    second = envoyer(alice)

    assert second.status_code == 200
    assert attendre(alice, second.json()["job_id"])["status"] == "error"


def test_un_autre_compte_n_est_jamais_refuse_et_rien_ne_fuit(comptes, pipeline, libere_a_la_fin) -> None:
    alice, bruno = connecte("alice"), connecte("bruno")
    pipeline.libere.clear()
    premier = envoyer(alice).json()["job_id"]
    attendre_en_cours(alice, premier)

    reponse = envoyer(bruno)

    assert reponse.status_code == 200
    assert premier not in reponse.text and "visio" not in reponse.text   # rien du traitement d'Alice
    assert alice.get("/api/jobs").json()["traitements"][0]["id"] == premier


def test_un_contenu_different_est_accepte(comptes, pipeline, libere_a_la_fin) -> None:
    alice = connecte("alice")
    pipeline.libere.clear()
    premier = envoyer(alice).json()["job_id"]
    attendre_en_cours(alice, premier)

    assert envoyer(alice, AUTRE_CONTENU).status_code == 200


def test_un_envoi_avec_identifiant_de_capture_n_est_jamais_refuse_pour_doublon(
        comptes, pipeline, libere_a_la_fin) -> None:
    """Le compagnon se reconnaît à son identifiant ; un 409 serait pour lui un refus DÉFINITIF."""
    alice = connecte("alice")
    pipeline.libere.clear()
    premier = envoyer(alice).json()["job_id"]
    attendre_en_cours(alice, premier)

    reponse = envoyer(alice, capture_application="zoom.us", capture_mode="visio", capture_id=str(uuid.uuid4()))

    assert reponse.status_code == 200


def test_un_jeton_d_appareil_n_est_jamais_refuse_et_n_apprend_rien_d_un_traitement_de_la_session(
        comptes, pipeline, libere_a_la_fin) -> None:
    """Un jeton ne voit ni le titre ni l'état d'un traitement lancé par la session (`_job_du_compte`) : un 409 qui
    les nommerait les lui rendrait, et ce serait pour le compagnon un refus DÉFINITIF."""
    alice = connecte("alice")
    secret = alice.post("/api/jetons", json={"nom": "MacBook de test"}).json()["secret"]
    appareil = TestClient(api_main.app, headers={"Authorization": f"Bearer {secret}"})
    pipeline.libere.clear()
    session = envoyer(alice, nom="reunion-privee.wav").json()["job_id"]
    attendre_en_cours(alice, session)

    reponse = envoyer(appareil, nom="autre.wav")        # même contenu, SANS identifiant de capture

    assert reponse.status_code == 200                      # jamais refusé
    assert session not in reponse.text and "reunion-privee" not in reponse.text


def test_l_empreinte_n_est_jamais_exposee(comptes, pipeline, libere_a_la_fin) -> None:
    alice = connecte("alice")
    pipeline.libere.clear()
    premier = envoyer(alice).json()["job_id"]
    attendre_en_cours(alice, premier)
    empreinte = hashlib.sha256(CONTENU).hexdigest()

    textes = [alice.get("/api/jobs").text, alice.get(f"/api/jobs/{premier}").text,
              envoyer(alice).text]

    assert all(len(t) > 20 for t in textes), "ancre : les trois réponses ont un corps"
    assert all(empreinte not in t for t in textes)


# ---------- Au niveau du gestionnaire : reconnaissance et enregistrement d'un seul geste ----------

def test_deux_envois_identiques_en_parallele_n_en_acceptent_qu_un() -> None:
    manager = JobManager(max_active=100)
    empreinte = hashlib.sha256(b"meme contenu").hexdigest()
    tours = 40
    for tour in range(tours):
        a = manager.create(owner="alice", nature="transcription", titre=f"a{tour}")
        b = manager.create(owner="alice", nature="transcription", titre=f"b{tour}")
        barriere = threading.Barrier(2, timeout=5)
        resultats: dict[str, object] = {}

        def envoi(nom, job, barriere=barriere, resultats=resultats) -> None:
            barriere.wait()
            resultats[nom] = manager.enregistrer_envoi(job, empreinte)

        fils = [threading.Thread(target=envoi, args=("a", a)), threading.Thread(target=envoi, args=("b", b))]
        for fil in fils:
            fil.start()
        for fil in fils:
            fil.join(10)
        acceptes = [nom for nom, r in resultats.items() if r is None]
        refuses = [nom for nom, r in resultats.items() if r is not None]
        assert len(acceptes) == 1 and len(refuses) == 1, (tour, resultats)       # exactement un des deux
        # Pour le tour suivant : libérer l'empreinte (traitement final).
        for job in (a, b):
            job.status = "error"
    assert tours == 40, "ancre : les quarante tours ont tous été classés"


def test_le_gestionnaire_ignore_les_traitements_finaux_et_les_autres_comptes() -> None:
    manager = JobManager(max_active=100)
    empreinte = hashlib.sha256(b"contenu").hexdigest()
    ancien = manager.create(owner="alice", nature="transcription")
    assert manager.enregistrer_envoi(ancien, empreinte) is None
    ancien.status = "done"
    suivant = manager.create(owner="alice", nature="transcription")
    assert manager.enregistrer_envoi(suivant, empreinte) is None               # le premier est final : pas un doublon
    autre_compte = manager.create(owner="bruno", nature="transcription")
    assert manager.enregistrer_envoi(autre_compte, empreinte) is None          # un autre compte n'est jamais refusé
    doublon = manager.create(owner="alice", nature="transcription")
    existant = manager.enregistrer_envoi(doublon, empreinte)
    assert existant is suivant                                                  # le traitement NON final du même compte
