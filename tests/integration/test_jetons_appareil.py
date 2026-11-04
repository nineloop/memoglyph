"""Jetons d'appareil et provenance d'une capture : ce dont le compagnon a besoin.

Le compagnon de capture n'a pas de navigateur : il appelle Memoglyph avec un
jeton d'appareil, créé par le compte dans l'interface. Ce jeton n'ouvre que les
routes du compagnon ; le test d'étanchéité (`test_etancheite_comptes.py`)
l'éprouve sur chaque route de `app.routes`.
"""
from __future__ import annotations

import re
import sqlite3
import time

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from api import auth
from api import main as api_main
from api.jobs import JobManager
from core import docid, library, projects

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


def porteur(secret: str) -> TestClient:
    """Un client sans cookie, comme le compagnon : seulement le jeton."""
    return TestClient(api_main.app, headers={"Authorization": f"Bearer {secret}"})


def creer_jeton(client: TestClient, nom: str = "MacBook de test") -> tuple[dict, str]:
    reponse = client.post("/api/jetons", json={"nom": nom})
    assert reponse.status_code == 201, reponse.text
    corps = reponse.json()
    return corps["jeton"], corps["secret"]


def faux_pipeline(monkeypatch, appels: list) -> None:
    """Écrit un `.md` minimal à la destination, avec la provenance reçue."""
    import yaml

    def run(path, **options):
        appels.append(options)
        meta = {"id": docid.nouveau(), "meeting_id": docid.nouveau(),
                "project_id": projects.load(options["output_dir"]).id, "title": "capture"}
        if options.get("capture"):
            meta["capture"] = options["capture"]
        sortie = options["output_dir"] / "capture.md"
        sortie.write_text(f"---\n{yaml.safe_dump(meta, allow_unicode=True)}---\n\nTexte",
                          encoding="utf-8")
        return sortie, "Texte"

    monkeypatch.setattr(api_main.pipeline, "run", run)
    monkeypatch.setattr(api_main.semantic, "index_name", lambda name: 0)
    monkeypatch.setattr(api_main.voiceprint, "index_name", lambda name: {})


def attendre(client: TestClient, job_id: str) -> dict:
    for _ in range(400):
        etat = client.get(f"/api/jobs/{job_id}").json()
        if etat["status"] in {"done", "error"}:
            return etat
        time.sleep(0.005)
    raise AssertionError("job sans fin")


# ---------- (1) Créer, lister, retirer ----------

def test_un_jeton_s_affiche_une_fois_et_n_est_stocke_qu_en_empreinte(comptes, tmp_path) -> None:
    alice = connecte("alice")

    description, secret = creer_jeton(alice, "  MacBook   d'Alice ")

    assert secret.startswith(auth.PREFIXE_JETON) and len(secret) > 40
    assert description["nom"] == "MacBook d'Alice" and description["dernier_usage"] is None
    liste = alice.get("/api/jetons").json()
    assert [j["id"] for j in liste] == [description["id"]]
    assert secret not in alice.get("/api/jetons").text
    with sqlite3.connect(tmp_path / "auth.db") as con:
        contenu = "\n".join(str(ligne) for ligne in con.execute("SELECT * FROM jetons_appareil"))
    assert secret not in contenu


def test_le_nom_d_un_jeton_est_borne(comptes) -> None:
    alice = connecte("alice")

    assert alice.post("/api/jetons", json={"nom": "   "}).status_code == 400
    assert alice.post("/api/jetons", json={"nom": "x" * 65}).status_code == 400
    assert alice.get("/api/jetons").json() == []


def test_chaque_compte_ne_voit_et_ne_retire_que_ses_jetons(comptes) -> None:
    alice, bruno = connecte("alice"), connecte("bruno")
    jeton_a, secret_a = creer_jeton(alice)
    creer_jeton(bruno, "Mac de Bruno")

    assert [j["nom"] for j in bruno.get("/api/jetons").json()] == ["Mac de Bruno"]
    assert bruno.delete(f"/api/jetons/{jeton_a['id']}").status_code == 404
    assert porteur(secret_a).get("/api/projects").status_code == 200

    assert alice.delete(f"/api/jetons/{jeton_a['id']}").status_code == 200
    assert alice.get("/api/jetons").json() == []
    assert alice.delete(f"/api/jetons/{jeton_a['id']}").status_code == 404


def test_un_jeton_retire_ne_vaut_plus_rien(comptes) -> None:
    alice = connecte("alice")
    jeton, secret = creer_jeton(alice)
    compagnon = porteur(secret)
    assert compagnon.get("/api/projects").status_code == 200

    alice.delete(f"/api/jetons/{jeton['id']}")

    assert compagnon.get("/api/projects").status_code == 401
    assert compagnon.post("/api/transcribe", files={"file": ("a.wav", b"a")}).status_code == 401


def test_le_jeton_d_un_compte_desactive_ne_vaut_plus_rien_meme_reactive(comptes) -> None:
    alice, admin = connecte("alice"), connecte("admin")
    _jeton, secret = creer_jeton(alice)

    assert admin.post("/api/admin/utilisateurs/alice/desactivation").status_code == 200
    assert porteur(secret).get("/api/projects").status_code == 401

    assert admin.post("/api/admin/utilisateurs/alice/reactivation").status_code == 200
    assert porteur(secret).get("/api/projects").status_code == 401
    assert connecte("alice").get("/api/jetons").json() == []


def test_un_jeton_reste_sans_valeur_si_son_compte_est_inactif(comptes, tmp_path) -> None:
    """Garde de la lecture elle-même, même si un jeton survivait à la désactivation."""
    _jeton, secret = creer_jeton(connecte("alice"))
    with sqlite3.connect(tmp_path / "auth.db") as con:
        con.execute("UPDATE utilisateurs SET actif = 0 WHERE identifiant = 'alice'")

    assert auth.utilisateur_de_jeton(secret) is None
    assert porteur(secret).get("/api/projects").status_code == 401


def test_le_dernier_usage_se_note_sans_ecrire_a_chaque_appel(comptes, monkeypatch) -> None:
    alice = connecte("alice")
    jeton, secret = creer_jeton(alice)
    compagnon = porteur(secret)

    compagnon.get("/api/projects")
    premier = alice.get("/api/jetons").json()[0]["dernier_usage"]
    compagnon.get("/api/projects")

    assert premier is not None
    assert alice.get("/api/jetons").json()[0]["dernier_usage"] == premier


# ---------- (2) Le jeton n'ouvre que les routes du compagnon ----------

def _routes_api() -> set[tuple[str, str]]:
    return {(methode, route.path) for route in api_main.app.routes
            if isinstance(route, APIRoute) and route.path.startswith("/api/")
            for methode in route.methods - {"HEAD", "OPTIONS"}}


def test_les_routes_du_compagnon_existent_et_restent_peu_nombreuses() -> None:
    assert api_main.ROUTES_DU_COMPAGNON <= _routes_api()
    assert api_main.ROUTES_DU_COMPAGNON == {
        ("POST", "/api/transcribe"), ("GET", "/api/jobs/{job_id}"),
        ("GET", "/api/jobs/{job_id}/events"), ("GET", "/api/projects"),
        # Chantier 003 : les comptes (« en attente : N traitements ») et SES captures seulement.
        ("GET", "/api/jobs")}
    assert ("GET", "/api/health") in api_main.ROUTES_PUBLIQUES


def test_un_jeton_seul_recoit_401_hors_des_routes_du_compagnon(comptes) -> None:
    alice = connecte("alice")
    _jeton, secret = creer_jeton(alice)
    compagnon = porteur(secret)

    ouvertes = []
    for methode, chemin in sorted(_routes_api()):
        if (methode, chemin) in api_main.ROUTES_PUBLIQUES | api_main.ROUTES_DU_COMPAGNON:
            continue
        url = re.sub(r"\{[^}]+\}", "x", chemin)
        if compagnon.request(methode, url, params={"view_scope": "all"}).status_code != 401:
            ouvertes.append(f"{methode} {chemin}")

    assert ouvertes == []
    assert compagnon.get("/api/health").status_code == 200
    # Un jeton ne crée pas d'autre jeton.
    assert compagnon.post("/api/jetons", json={"nom": "intrus"}).status_code == 401


def test_un_jeton_inconnu_ou_mal_forme_ne_vaut_rien(comptes) -> None:
    alice = connecte("alice")
    _jeton, secret = creer_jeton(alice)

    for entete in (f"Bearer {secret}x", "Bearer ", f"Basic {secret}", secret):
        client = TestClient(api_main.app, headers={"Authorization": entete})
        assert client.get("/api/projects").status_code == 401, entete
    assert TestClient(api_main.app, headers={"Authorization": f"bearer {secret}"}) \
        .get("/api/projects").status_code == 200


def test_le_compagnon_envoie_suit_son_travail_et_liste_ses_projets(comptes, monkeypatch) -> None:
    alice, bruno = connecte("alice"), connecte("bruno")
    _jeton, secret = creer_jeton(alice)
    compagnon = porteur(secret)
    projet_b = bruno.post("/api/projects", json={"name": "Beta"}).json()["id"]
    appels: list = []
    faux_pipeline(monkeypatch, appels)
    # Le « Non classé » d'Alice, tel que la création de son compte par l'API le donne : la
    # liste du compagnon, une lecture, ne le crée pas (`api.portee`).
    projects.personal_inbox(comptes, "alice")

    projets = compagnon.get("/api/projects").json()
    assert {p["owner"] for p in projets} == {"alice"} and projet_b not in {p["id"] for p in projets}
    # Vers un projet d'un autre compte : 404, avant tout travail.
    refus = compagnon.post("/api/transcribe", files={"file": ("a.wav", b"a")},
                           data={"destination_project_id": projet_b})
    assert refus.status_code == 404 and appels == []

    job_id = compagnon.post("/api/transcribe", files={"file": ("a.wav", b"a")}).json()["job_id"]
    assert attendre(compagnon, job_id)["status"] == "done"
    with compagnon.stream("GET", f"/api/jobs/{job_id}/events") as flux:
        assert flux.status_code == 200
    # Le travail est celui d'Alice, visible dans son navigateur, pas chez Bruno.
    assert alice.get(f"/api/jobs/{job_id}").status_code == 200
    assert bruno.get(f"/api/jobs/{job_id}").status_code == 404
    inbox_a = alice.get("/api/config").json()["default_project_id"]
    assert projects.load(appels[0]["output_dir"]).id == inbox_a


# ---------- (3) Provenance d'une capture ----------

def test_la_provenance_est_ecrite_et_relue_dans_la_reunion(comptes, monkeypatch) -> None:
    alice = connecte("alice")
    _jeton, secret = creer_jeton(alice)
    appels: list = []
    faux_pipeline(monkeypatch, appels)

    job_id = porteur(secret).post(
        "/api/transcribe", files={"file": ("visio.wav", b"a")},
        data={"capture_application": "  zoom.us ", "capture_mode": "Visio"}).json()["job_id"]
    nom = attendre(alice, job_id)["result"]["name"]

    assert appels[0]["capture"] == {"application": "zoom.us", "mode": "visio"}
    library.reindex()
    lecture = alice.get(f"/api/transcript/{nom}", params={"view_scope": "all"}).json()
    assert lecture["meta"]["capture"] == {"application": "zoom.us", "mode": "visio"}


def test_sans_provenance_rien_ne_change(comptes, monkeypatch) -> None:
    alice = connecte("alice")
    appels: list = []
    faux_pipeline(monkeypatch, appels)

    job_id = alice.post("/api/transcribe", files={"file": ("a.mp3", b"a")}).json()["job_id"]
    attendre(alice, job_id)

    assert appels[0]["capture"] is None


@pytest.mark.parametrize("donnees", [
    {"capture_mode": "webinar"},
    {"capture_application": "x" * 81},
    {"capture_application": "Zoom\x00"},
])
def test_une_provenance_invalide_est_refusee_avant_tout_travail(comptes, monkeypatch, donnees) -> None:
    alice = connecte("alice")
    appels: list = []
    faux_pipeline(monkeypatch, appels)

    reponse = alice.post("/api/transcribe", files={"file": ("a.wav", b"a")}, data=donnees)

    assert reponse.status_code == 400 and appels == []


# ---------- Audit externe, A3 : le jeton suit un travail sans en lire le texte ----------

def test_avec_un_jeton_le_travail_ne_rend_aucun_texte(comptes) -> None:
    alice = connecte("alice")
    description, secret = creer_jeton(alice)
    compte = auth.verifier_identifiants("alice", MOT_DE_PASSE)
    # Un traitement né de CE jeton (strict nécessaire : le jeton ne suit que ses propres captures).
    job = api_main.jobs.create(owner=compte.id, jeton=description["id"])

    def travail(progres):
        api_main.jobs.push(job, {"type": "partial", "text": "TEXTE PARTIEL SECRET"})
        progres(0.5, "Transcription")
        return {"name": "reunion.md", "content": "CONTENU SECRET DU TRANSCRIPT",
                "warnings": ["registre des voix indisponible"]}

    api_main.jobs.run(job, travail)
    assert attendre(alice, job.id)["status"] == "done"
    compagnon = porteur(secret)

    etat = compagnon.get(f"/api/jobs/{job.id}").json()
    with compagnon.stream("GET", f"/api/jobs/{job.id}/events") as flux:
        evenements = "".join(flux.iter_text())

    assert etat["status"] == "done" and etat["error"] is None
    assert etat["result"] == {"name": "reunion.md", "warnings": ["registre des voix indisponible"]}
    for texte in (str(etat), evenements):
        assert "SECRET" not in texte
    assert '"type": "done"' in evenements and "reunion.md" in evenements
    assert '"type": "progress"' in evenements
    # La session par cookie ne change pas : le navigateur voit tout.
    assert alice.get(f"/api/jobs/{job.id}").json()["result"]["content"] == "CONTENU SECRET DU TRANSCRIPT"
    with alice.stream("GET", f"/api/jobs/{job.id}/events") as flux:
        assert "TEXTE PARTIEL SECRET" in "".join(flux.iter_text())


def test_le_travail_rend_au_jeton_l_identite_de_sa_reunion_et_rien_d_autre(comptes, monkeypatch) -> None:
    """Le compagnon ouvre la réunion elle-même : le jeton reçoit son `meeting_id` et son
    projet, jamais son texte, et seulement pour ses propres travaux."""
    alice, bruno = connecte("alice"), connecte("bruno")
    _ja, secret_a = creer_jeton(alice)
    _jb, secret_b = creer_jeton(bruno)
    faux_pipeline(monkeypatch, [])
    job_a = porteur(secret_a).post("/api/transcribe", files={"file": ("a.wav", b"a")}).json()["job_id"]
    etat = attendre(porteur(secret_a), job_a)

    resultat = etat["result"]
    assert set(resultat) == {"name", "meeting_id", "project_id"}
    meta = next(p for p in comptes.rglob(resultat["name"])).read_text(encoding="utf-8")
    assert f"meeting_id: {resultat['meeting_id']}" in meta and resultat["project_id"] in meta
    assert resultat["project_id"] == alice.get("/api/config").json()["default_project_id"]
    assert "Texte" not in str(resultat), "jamais le texte de la réunion"

    # Le jeton de Bruno ne voit rien du travail d'Alice, identifiants compris.
    refus = porteur(secret_b).get(f"/api/jobs/{job_a}")
    assert refus.status_code == 404
    assert resultat["meeting_id"] not in refus.text and resultat["project_id"] not in refus.text
    with porteur(secret_b).stream("GET", f"/api/jobs/{job_a}/events") as flux:
        assert flux.status_code == 404


def test_l_echec_de_la_diarisation_remonte_dans_les_avertissements_du_travail(comptes, monkeypatch) -> None:
    """Constat 2 du parcours complet : le transcript survit, mais l'échec se dit."""
    import yaml

    def run(path, **options):
        meta = {"id": docid.nouveau(), "meeting_id": docid.nouveau(),
                "project_id": projects.load(options["output_dir"]).id, "title": "sans voix",
                "diarization": False, "diarization_error": "DiarizationError: HF_TOKEN manquant."}
        sortie = options["output_dir"] / "sans-voix.md"
        sortie.write_text(f"---\n{yaml.safe_dump(meta, allow_unicode=True)}---\n\nTexte", encoding="utf-8")
        return sortie, "Texte"

    monkeypatch.setattr(api_main.pipeline, "run", run)
    monkeypatch.setattr(api_main.semantic, "index_name", lambda name: 0)
    monkeypatch.setattr(api_main.voiceprint, "index_name", lambda name: {})
    alice = connecte("alice")
    job = alice.post("/api/transcribe", files={"file": ("a.wav", b"a")}, data={"diarize": "true"}).json()["job_id"]
    etat = attendre(alice, job)
    assert etat["status"] == "done"
    assert etat["result"]["warnings"] == [
        "diarisation indisponible, locuteurs non identifiés : DiarizationError: HF_TOKEN manquant."]


def test_un_jeton_d_appareil_ne_peut_pas_annuler_meme_sa_propre_capture(comptes, monkeypatch) -> None:
    """Chantier 005 : l'annulation est réservée à la session ; la route n'est pas dans `ROUTES_DU_COMPAGNON`."""
    alice = connecte("alice")
    _jeton, secret = creer_jeton(alice)
    compagnon = porteur(secret)
    assert ("POST", "/api/jobs/{job_id}/cancel") in _routes_api(), "ancre : la route existe"
    assert ("POST", "/api/jobs/{job_id}/cancel") not in api_main.ROUTES_DU_COMPAGNON
    propre = api_main.jobs.create(owner=auth.utilisateur_actif("alice").id, nature="transcription")

    reponse = compagnon.post(f"/api/jobs/{propre.id}/cancel")

    assert reponse.status_code == 401
    assert propre.status == "pending"
