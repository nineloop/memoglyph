"""Stockage par compte : propriété des projets, portée, bibliothèque ancienne, maintenance.

Isolation logique : un seul index SQLite, filtré par les projets du compte de la
session. Le test d'étanchéité route par route vit dans `test_etancheite_comptes.py`.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from api import auth, cli
from api import main as api_main
from api.jobs import JobManager
from core import docid, library, projects

pytestmark = [pytest.mark.integration, pytest.mark.sans_session]

MOT_DE_PASSE = "un mot de passe solide"


@pytest.fixture
def bibliotheque(isolated_stores, tmp_path, monkeypatch):
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
    reponse = client.post("/api/auth/connexion",
                          json={"identifiant": identifiant, "mot_de_passe": MOT_DE_PASSE})
    assert reponse.status_code == 200
    return client


def ecrire_document(dossier, nom: str, project_id: str, corps: str = "Texte") -> None:
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / nom).write_text(
        f"---\nid: {docid.nouveau()}\nmeeting_id: {docid.nouveau()}\n"
        f"project_id: {project_id}\ntitle: {nom}\n---\n\n{corps}\n", encoding="utf-8")


# ---------- (1) Propriété des projets ----------

def test_un_projet_cree_appartient_a_son_createur(bibliotheque) -> None:
    alice, bruno = connecte("alice"), connecte("bruno")

    cree_a = alice.post("/api/projects", json={"name": "Clients"}).json()
    cree_b = bruno.post("/api/projects", json={"name": "Clients"}).json()

    assert cree_a["owner"] == "alice" and cree_b["owner"] == "bruno"
    assert cree_a["id"] != cree_b["id"]
    manifeste_a = projects.load(bibliotheque / "Clients")
    assert (manifeste_a.id, manifeste_a.owner, manifeste_a.version) == (cree_a["id"], "alice", 2)
    assert projects.load(bibliotheque / "Clients (bruno)").id == cree_b["id"]
    assert [p["id"] for p in alice.get("/api/projects").json() if p["name"] == "Clients"] == [cree_a["id"]]


def test_chaque_compte_a_son_non_classe_cree_a_la_demande(bibliotheque, monkeypatch) -> None:
    alice, bruno = connecte("alice"), connecte("bruno")
    # La demande est une ÉCRITURE du compte : une lecture ne crée pas le « Non classé ».
    assert alice.get("/api/config").json()["default_project_id"] == ""
    for client in (alice, bruno):
        assert client.post("/api/projects", json={"name": "Premier"}).status_code == 200

    inbox_a = alice.get("/api/config").json()["default_project_id"]
    inbox_b = bruno.get("/api/config").json()["default_project_id"]

    assert inbox_a != inbox_b
    proprietaires = {p.id: (p.kind, p.name, p.owner) for _d, p in projects.all_projects(bibliotheque)}
    assert proprietaires[inbox_a] == ("inbox", projects.INBOX_NAME, "alice")
    assert proprietaires[inbox_b] == ("inbox", projects.INBOX_NAME, "bruno")

    # La destination par défaut d'une transcription est le « Non classé » du compte.
    destinations = []

    def run(path, **options):
        destinations.append(options["output_dir"])
        sortie = options["output_dir"] / "r.md"
        sortie.write_text(f"---\nid: {docid.nouveau()}\nmeeting_id: {docid.nouveau()}\n"
                          f"project_id: {projects.load(options['output_dir']).id}\n---\n\nT",
                          encoding="utf-8")
        return sortie, "T"

    monkeypatch.setattr(api_main.pipeline, "run", run)
    monkeypatch.setattr(api_main.semantic, "index_name", lambda name: 0)
    monkeypatch.setattr(api_main.voiceprint, "index_name", lambda name: {})
    job_id = bruno.post("/api/transcribe", files={"file": ("a.mp3", b"a")}).json()["job_id"]
    for _ in range(400):
        if bruno.get(f"/api/jobs/{job_id}").json()["status"] in {"done", "error"}:
            break
    assert bruno.get(f"/api/jobs/{job_id}").json()["status"] == "done"
    assert projects.load(destinations[0]).id == inbox_b
    # Un job n'est lisible que par le compte qui l'a lancé.
    assert alice.get(f"/api/jobs/{job_id}").status_code == 404


# ---------- (2) Résolveur de portée ----------

def test_tous_les_projets_veut_dire_tous_mes_projets(bibliotheque) -> None:
    alice, bruno = connecte("alice"), connecte("bruno")
    projet_a = alice.post("/api/projects", json={"name": "Alpha"}).json()["id"]
    projet_b = bruno.post("/api/projects", json={"name": "Beta"}).json()["id"]
    ecrire_document(bibliotheque / "Alpha", "a.md", projet_a, "mot partagé")
    ecrire_document(bibliotheque / "Beta", "b.md", projet_b, "mot partagé")
    library.reindex()

    tout_a = alice.get("/api/library", params={"view_scope": "all"}).json()
    recherche_a = alice.get("/api/search", params={"q": "partagé", "view_scope": "all"}).json()

    assert [d["name"] for d in tout_a] == ["a.md"]
    assert [d["name"] for d in recherche_a] == ["a.md"]
    assert {p["id"] for p in alice.get("/api/projects").json()} >= {projet_a}
    assert projet_b not in {p["id"] for p in alice.get("/api/projects").json()}


def test_hors_de_la_portee_c_est_404_jamais_403(bibliotheque) -> None:
    alice, bruno = connecte("alice"), connecte("bruno")
    projet_b = bruno.post("/api/projects", json={"name": "Beta"}).json()["id"]
    ecrire_document(bibliotheque / "Beta", "b.md", projet_b)
    library.reindex()

    for reponse in (
        alice.get("/api/library", params={"view_scope": projet_b}),
        alice.get("/api/transcript/b.md", params={"view_scope": "all"}),
        alice.get("/api/transcript/b.md", params={"view_scope": projet_b}),
        alice.delete("/api/transcript/b.md", params={"view_scope": "all"}),
    ):
        assert reponse.status_code == 404
        assert projet_b not in reponse.text and "b.md" not in reponse.text
    assert (bibliotheque / "Beta" / "b.md").exists()
    assert bruno.get("/api/transcript/b.md", params={"view_scope": projet_b}).status_code == 200


# ---------- (3) Bibliothèque d'avant les comptes ----------

def _bibliotheque_ancienne(racine) -> None:
    """Un corpus d'une version antérieure : manifestes en version 1, sans propriétaire."""
    (racine / "Ancien").mkdir(parents=True)
    projects.initialize(racine, simulation=False)
    for dossier in (racine, racine / "Ancien"):
        chemin = dossier / projects.MANIFEST_NAME
        chemin.write_text(chemin.read_text(encoding="utf-8")
                          .replace("version: 2", "version: 1").replace("owner: null\n", ""),
                          encoding="utf-8")
    ecrire_document(racine / "Ancien", "vieux.md", projects.load(racine / "Ancien").id)


def test_un_projet_sans_proprietaire_n_est_visible_que_des_administrateurs(bibliotheque) -> None:
    _bibliotheque_ancienne(bibliotheque)
    ancien = projects.load(bibliotheque / "Ancien")
    assert (ancien.version, ancien.owner) == (1, None)
    ecrire_document(bibliotheque / "Ancien", "note.md", ancien.id)
    library.reindex()
    admin, alice = connecte("admin"), connecte("alice")

    assert ancien.id in {p["id"] for p in admin.get("/api/projects").json()}
    assert ancien.id not in {p["id"] for p in alice.get("/api/projects").json()}
    assert "note.md" in {d["name"] for d in admin.get("/api/library", params={"view_scope": "all"}).json()}
    assert alice.get("/api/library", params={"view_scope": "all"}).json() == []
    assert alice.get("/api/library", params={"view_scope": ancien.id}).status_code == 404


def test_attribuer_bibliotheque_simule_puis_applique(bibliotheque, capsys) -> None:
    _bibliotheque_ancienne(bibliotheque)
    ancien = projects.load(bibliotheque / "Ancien")
    avant = {p: p.read_bytes() for p in bibliotheque.rglob(projects.MANIFEST_NAME)}
    vieux = bibliotheque / "Ancien" / "vieux.md"
    vieux_avant = (vieux.read_bytes(), vieux.stat().st_mtime_ns)

    assert cli.main(["attribuer-bibliotheque", "Alice"]) == 0
    simulation = capsys.readouterr().out
    assert "Simulation : rien n'est écrit." in simulation
    assert "Projets sans propriétaire : 2" in simulation and ancien.id in simulation
    assert {p: p.read_bytes() for p in bibliotheque.rglob(projects.MANIFEST_NAME)} == avant

    assert cli.main(["attribuer-bibliotheque", "alice", "--appliquer"]) == 0
    assert "2 projet(s) attribué(s) à « alice »" in capsys.readouterr().out
    assert projects.load(bibliotheque / "Ancien").owner == "alice"
    assert projects.load(bibliotheque / "Ancien").id == ancien.id
    assert projects.load(bibliotheque).owner == "alice"
    assert (vieux.read_bytes(), vieux.stat().st_mtime_ns) == vieux_avant
    assert ancien.id in {p["id"] for p in connecte("alice").get("/api/projects").json()}

    assert cli.main(["attribuer-bibliotheque", "inconnu"]) == 1
    assert "Aucun compte" in capsys.readouterr().err


# ---------- (4) Maintenance de toute la bibliothèque ----------

ROUTES_DE_MAINTENANCE = (
    ("POST", "/api/library/reindex"),
    ("POST", "/api/search/reindex"),
    ("GET", "/api/maintenance"),
    ("POST", "/api/maintenance/reindex/semantic"),
    ("POST", "/api/maintenance/reindex/voices"),
    ("POST", "/api/maintenance/backfill/faithful"),
    ("POST", "/api/voices/reindex"),
    ("POST", "/api/voices/backfill"),
    ("GET", "/api/voices/backfill/preview"),
)


def test_la_maintenance_est_reservee_aux_administrateurs(bibliotheque, monkeypatch) -> None:
    appels = []
    monkeypatch.setattr(api_main.semantic, "reindex_all",
                        lambda project_id=None: appels.append(project_id) or {"indexed": 0})
    monkeypatch.setattr(api_main.library, "reindex", lambda: {"indexed": 0})
    alice, admin = connecte("alice"), connecte("admin")

    for methode, chemin in ROUTES_DE_MAINTENANCE:
        reponse = alice.request(methode, chemin, params={"view_scope": "all"})
        assert reponse.status_code == 404, (methode, chemin, reponse.status_code)
    assert appels == []

    assert admin.post("/api/library/reindex").status_code == 200
    assert admin.get("/api/maintenance", params={"view_scope": "all"}).status_code == 200
    assert admin.post("/api/search/reindex", params={"view_scope": "all"}).status_code == 200
    # `all` désigne ici toute la bibliothèque, tous comptes confondus.
    assert appels == [None]


def _attendre_job(client: TestClient, job_id: str) -> dict:
    for _ in range(400):
        etat = client.get(f"/api/jobs/{job_id}").json()
        if etat["status"] in {"done", "error"}:
            return etat
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} sans fin")


def test_sans_non_classe_une_maintenance_sans_portee_ne_traite_aucun_document(
        bibliotheque, monkeypatch) -> None:
    """Portée vide, administrateur sans « Non classé » : `portee.AUCUN_PROJET`, jamais
    `""` ni `None`, que ces fonctions liraient comme « toute la bibliothèque »."""
    from api import portee
    from core import backfill, faithful_backfill, maintenance, semantic, voiceprint
    from core.config import EMBED_DIM

    alice = connecte("alice")
    projet_a = alice.post("/api/projects", json={"name": "Alpha"}).json()["id"]
    for nom in ("a1.md", "a2.md", "a3.md"):
        ecrire_document(bibliotheque / "Alpha", nom, projet_a,
                        f"## Transcript\n\n**Speaker 1** [00:00:01] Parole de {nom}.")
    library.reindex()
    admin = connecte("admin")
    assert portee.non_classe(auth.Utilisateur(id="admin", identifiant="admin", admin=True)) is None
    embarques: list[str] = []
    monkeypatch.setattr(semantic.embed, "embed_documents",
                        lambda textes, **_k: embarques.extend(textes) or [[1.0] + [0.0] * (EMBED_DIM - 1)] * len(textes))
    portees: dict[str, list] = {}

    def espion(module, nom_fonction):
        reelle = getattr(module, nom_fonction)

        def appel(*args, **kwargs):
            # `backfill.eligible(project_id)` est appelée par position, les autres par nom.
            portees.setdefault(nom_fonction, []).append(
                kwargs["project_id"] if "project_id" in kwargs else args[0])
            return reelle(*args, **kwargs)
        monkeypatch.setattr(module, nom_fonction, appel)

    espion(semantic, "reindex_all")
    espion(voiceprint, "reindex_all")
    espion(backfill, "eligible")
    espion(backfill, "backfill_all")
    espion(faithful_backfill, "run")
    espion(maintenance, "status")

    resultats = {}
    for methode, chemin in ROUTES_DE_MAINTENANCE:
        if chemin == "/api/library/reindex":
            continue  # réindexation de la bibliothèque elle-même : sans portée par construction
        reponse = admin.request(methode, chemin)
        assert reponse.status_code == 200, (methode, chemin, reponse.text)
        corps = reponse.json()
        if "job_id" in corps:
            corps = _attendre_job(admin, corps["job_id"])
            assert corps["status"] == "done", (chemin, corps)
        resultats[chemin] = corps

    # Chaque fonction de maintenance a reçu `AUCUN_PROJET`, jamais `""` ni `None`.
    assert set(portees) == {"reindex_all", "eligible", "backfill_all", "run", "status"}
    toutes = [p for appels in portees.values() for p in appels]
    assert len(toutes) >= 8
    assert set(toutes) == {portee.AUCUN_PROJET}
    # Et rien n'a été traité : aucun passage embarqué, aucune voix, aucun document vu.
    assert embarques == []
    assert resultats["/api/voices/reindex"]["indexed"] == 0
    assert resultats["/api/maintenance/reindex/voices"]["indexed"] == 0
    assert resultats["/api/voices/backfill/preview"]["eligible"] == []
    etat = resultats["/api/maintenance"]
    assert (etat["documents"], etat["diarized_documents"], etat["semantic_documents"]) == (0, 0, 0)
    # Ancre : la chaîne vide aurait tout pris, les trois documents d'Alpha compris.
    assert maintenance.status(project_id="")["documents"] == 3
    semantic.reindex_all(project_id="")
    assert len([t for t in embarques if "Parole de" in t]) == 3


def test_vider_la_corbeille_ne_touche_que_les_projets_du_compte(bibliotheque) -> None:
    from core import purge

    alice, bruno = connecte("alice"), connecte("bruno")
    projet_a = alice.post("/api/projects", json={"name": "Alpha"}).json()["id"]
    projet_b = bruno.post("/api/projects", json={"name": "Beta"}).json()["id"]
    ecrire_document(bibliotheque / "Alpha", "a.md", projet_a)
    ecrire_document(bibliotheque / "Beta", "b.md", projet_b)
    library.reindex()
    assert purge.archive("a.md")["archived"] and purge.archive("b.md")["archived"]

    vide = bruno.delete("/api/trash", params={"view_scope": "all"})

    assert vide.status_code == 200 and vide.json()["removed"] == 1
    assert [i["origin"] for i in library.trash_items()] == ["a.md"]
    assert [i["origin"] for i in alice.get("/api/trash", params={"view_scope": "all"}).json()] == ["a.md"]


def test_le_libelle_d_un_document_est_le_nom_du_projet_pas_son_dossier(bibliotheque) -> None:
    alice, bruno = connecte("alice"), connecte("bruno")
    alice.post("/api/projects", json={"name": "Clients"})
    projet_b = bruno.post("/api/projects", json={"name": "Clients"}).json()["id"]
    ecrire_document(bibliotheque / "Clients (bruno)", "b.md", projet_b)
    library.reindex()

    [document] = bruno.get("/api/library", params={"view_scope": projet_b}).json()
    assert document["project"] == "Clients"
