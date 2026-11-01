"""Une lecture n'écrit rien : ni le « Non classé », ni aucun autre magasin.

Le « Non classé » d'un compte naît à la création du compte (installation comprise)
et sous les routes qui écrivent (envoi, création de projet, déplacement) ; jamais
sous une lecture. Même règle pour l'attribution du « Non classé » de la racine sur
une bibliothèque vierge. Sans « Non classé », les lectures répondent : la liste des
projets est sans lui, aucune erreur.

La garde générale appelle CHAQUE route `GET` de `app.routes`, et chaque outil du
serveur MCP (`api.mcp.Lecteur`), sur une bibliothèque peuplée, pour un compte neuf
(sans « Non classé »), un administrateur neuf et le propriétaire des données. Elle
compare `output/`, `auth.db`, `index.db` et `usage.db` avant et après, les bases par
leur CONTENU (`iterdump`), jamais par leurs octets : en WAL, fermer une connexion
reporte dans le fichier des pages écrites plus tôt sans rien changer aux données.
"""
from __future__ import annotations

import hashlib
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import pytest
import sqlite_vec
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from api import auth, mcp, partages, portee
from api import main as api_main
from api.jobs import JobManager
from core import docid, ecl, library, projects, purge, semantic, usage_store, voiceprint

pytestmark = [pytest.mark.integration, pytest.mark.sans_session]


@pytest.fixture(autouse=True)
def _faits_actives(monkeypatch):
    """Ces tests supposent les faits de la réunion activés (`MEMOGLYPH_FAITS=1`)."""
    from core import config as _config

    monkeypatch.setattr(_config, "FAITS", True)

MOT_DE_PASSE = "un mot de passe solide"
MARQUEUR = "capybaralecture"

# Routes GET qui écrivent légitimement, avec leur raison. Vérifiée dans les deux sens :
# une route qui écrit sans y être fait échouer la garde, une entrée qui n'écrit plus (ou
# dont la route a disparu) aussi.
ROUTES_GET_QUI_ECRIVENT: dict[str, str] = {}


def _vec(n: int) -> list[float]:
    from core.config import EMBED_DIM
    v = [0.0] * EMBED_DIM
    v[n % EMBED_DIM] = 1.0
    return v


def _connecte(identifiant: str) -> TestClient:
    client = TestClient(api_main.app)
    assert client.post("/api/auth/connexion", json={
        "identifiant": identifiant, "mot_de_passe": MOT_DE_PASSE}).status_code == 200
    return client


def _magasins(tmp_path: Path, sortie: Path) -> dict:
    return {"sortie": sortie, "auth": tmp_path / "auth.db", "index": library.DB_PATH,
            "usage": tmp_path / "usage.db"}


def _empreintes(magasins: dict) -> dict[str, frozenset[str]]:
    """Fichier par fichier : un échec nomme ce qui a changé (motif de `test_mcp`)."""
    sortie: dict[str, frozenset[str]] = {}
    for chemin in sorted(magasins["sortie"].rglob("*")):
        if chemin.is_file() and not chemin.name.endswith(("-wal", "-shm")):
            sortie[str(chemin)] = frozenset({hashlib.sha256(chemin.read_bytes()).hexdigest()})
    for cle in ("auth", "index", "usage"):
        chemin = magasins[cle]
        if not chemin.exists():
            sortie[str(chemin)] = frozenset()
            continue
        with closing(sqlite3.connect(chemin)) as con:
            con.enable_load_extension(True)
            sqlite_vec.load(con)  # l'index sémantique a des tables vec0
            sortie[str(chemin)] = frozenset(con.iterdump())
    return sortie


def _ecarts(avant: dict, apres: dict) -> dict[str, list[str]]:
    """Par fichier, ce qui est apparu (+) ou a disparu (-), en quelques lignes."""
    sortie = {}
    for nom in avant.keys() | apres.keys():
        plus = apres.get(nom, frozenset()) - avant.get(nom, frozenset())
        moins = avant.get(nom, frozenset()) - apres.get(nom, frozenset())
        if plus or moins or (nom in avant) != (nom in apres):
            sortie[nom.rsplit("/", 1)[-1]] = ([f"+ {x[:120]}" for x in sorted(plus)][:4]
                                             + [f"- {x[:120]}" for x in sorted(moins)][:4])
    return sortie


def _manifestes(sortie: Path) -> dict[str, str]:
    """Chaque manifeste de projet de la bibliothèque, et son contenu."""
    return {str(p.relative_to(sortie)): p.read_text(encoding="utf-8")
            for p in sortie.rglob(projects.MANIFEST_NAME)}


def _job_termine(compte: auth.Utilisateur):
    job = api_main.jobs.create(owner=compte.id)
    api_main.jobs.run(job, lambda progres: progres(0.5, "à moitié") or {"name": "x.md"})
    for _ in range(400):
        if job.status == "done":
            break
        time.sleep(0.005)
    assert job.status == "done"
    return job


@pytest.fixture
def environnement(isolated_stores, tmp_path, monkeypatch):
    """Magasins isolés, embeddings et assistant déterministes, sans modèle."""
    sortie = isolated_stores["output_dir"]
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(auth, "CODE_PATH", tmp_path / ".memoglyph-installation")
    monkeypatch.setattr(api_main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(api_main, "jobs", JobManager())
    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(tmp_path / "usage.db"))
    compteur = {"n": 0}

    def faux_embed(textes, **_kw):
        vecteurs = []
        for _ in textes:
            vecteurs.append(_vec(compteur["n"]))
            compteur["n"] += 1
        return vecteurs

    monkeypatch.setattr(semantic.embed, "embed_documents", faux_embed)
    monkeypatch.setattr(semantic.embed, "embed_query", lambda t, **kw: _vec(0))
    from core import rag
    monkeypatch.setattr(rag.llm, "is_available", lambda **_kw: True)
    monkeypatch.setattr(rag.llm, "chat", lambda _sys, prompt, **_kw: prompt)
    return {"sortie": sortie, "tmp": tmp_path}


@pytest.fixture
def bibliotheque(environnement):
    """Bibliothèque peuplée par Alice ; Nora (ordinaire) et Ophélie (administratrice),
    créées sans « Non classé », comme un compte neuf qui n'a encore rien écrit."""
    sortie, tmp_path = environnement["sortie"], environnement["tmp"]
    # Le démarrage de l'API donne son identité à la racine (`_startup_reindex`).
    projects.ensure_inbox(sortie)
    auth.creer_compte("alice", MOT_DE_PASSE)
    # Le « Non classé » d'Alice, celui qu'un envoi lui aurait créé.
    dossier_inbox, inbox = projects.personal_inbox(sortie, "alice")
    dossier_p, projet_p = projects.create_owned(sortie, name="ProjetAlice", owner="alice")
    dossier_q, projet_q = projects.create_owned(sortie, name="ProjetPartage", owner="alice")
    auth.creer_compte("nora", MOT_DE_PASSE)
    auth.creer_compte("ophelie", MOT_DE_PASSE, admin=True)

    def document(dossier, nom, projet_id):
        identite, reunion = docid.nouveau(), docid.nouveau()
        (dossier / nom).write_text(
            f"---\nid: {identite}\nmeeting_id: {reunion}\nproject_id: {projet_id}\n"
            f"title: Titre {nom}\nlanguage: fr\nsensitivity: standard\n---\n\n"
            f"## Transcript\n\n**Speaker 1** [00:00:05] réunion {MARQUEUR} sur le budget du projet\n",
            encoding="utf-8")
        return identite, reunion

    doc_p, reunion_p = document(dossier_p, "alice-1.md", projet_p.id)
    document(dossier_p, "alice-2.md", projet_p.id)  # doublon
    doc_q, reunion_q = document(dossier_q, "alice-partage.md", projet_q.id)
    document(dossier_inbox, "alice-corbeille.md", inbox.id)
    library.reindex()
    semantic.reindex_all()
    voiceprint.save_embeddings(doc_p, {"Speaker 1": [1.0] + [0.0] * 255})
    voiceprint.index_name("alice-1.md")
    voix = voiceprint.assignments("alice-1.md")["Speaker 1"]["voice_id"]
    voiceprint.rename(voix, "VoixAlice")
    assert purge.archive("alice-corbeille.md")["archived"]
    [archive] = [i["name"] for i in library.trash_items()]

    execution = "EXEC-LECTURE"
    conn = usage_store.connect(tmp_path / "usage.db")
    conn.execute(
        "INSERT INTO execution(execution_id,kind,document_id,meeting_id,project_id,"
        "produced_artifact_id,provider_primary,model_primary,started_at,ended_at,wall_clock_s,"
        "status,n_calls,in_tok,out_tok,audio_seconds,schema_version,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (execution, "report", doc_p, reunion_p, projet_p.id, None, "openai", "gpt-x",
         "2026-08-09T10:00:00+00:00", "2026-08-09T10:00:00+00:00", 1.0, "ok", 0, None, None,
         None, usage_store.SCHEMA_VERSION, "2026-08-09T10:00:00+00:00"))
    conn.commit()
    conn.close()
    usage_store.rebuild_usage_daily(tmp_path / "usage.db")

    partage = partages.partager("alice", "nora", "projet", projet_q.id)
    comptes = {nom: auth.verifier_identifiants(nom, MOT_DE_PASSE)
               for nom in ("alice", "nora", "ophelie")}
    jeton, _secret = auth.creer_jeton(comptes["nora"], "MacDeNora")
    jobs = {nom: _job_termine(compte) for nom, compte in comptes.items()}
    # Une installation en service a déjà ses tables de partages et de faits : leur
    # création à la première lecture est un schéma, pas une donnée.
    for compte in comptes.values():
        portee.partages_actifs(compte)
    ecl.faits(frozenset({projet_p.id}))
    return {
        "sortie": sortie, "tmp": tmp_path, "comptes": comptes, "jobs": jobs,
        "projets": {"inbox_alice": inbox.id, "alice": projet_p.id, "partage": projet_q.id},
        "documents": [
            {"name": "alice-1.md", "meeting_id": reunion_p, "voice_id": str(voix)},
            {"name": "alice-partage.md", "meeting_id": reunion_q, "voice_id": str(voix)},
        ],
        "archive": archive, "execution": execution, "partage": partage["id"],
        "jeton": jeton["id"],
    }


def _routes_get() -> list[APIRoute]:
    return [route for route in api_main.app.routes
            if isinstance(route, APIRoute) and route.path.startswith("/api/")
            and "GET" in route.methods]


def _chemin(route: APIRoute, valeurs: dict) -> str:
    chemin = route.path
    for parametre in route.param_convertors:
        assert parametre in valeurs, f"paramètre inconnu du test : {parametre} ({route.path})"
        chemin = chemin.replace("{" + parametre + "}", valeurs[parametre])
    return chemin


def _lire_chaque_route(biblio: dict, qui: str) -> tuple[dict[str, dict], int]:
    """Chaque route GET, appelée par `qui` avec chaque jeu d'identifiants et chaque
    portée. Rend, par route, ce qu'elle a écrit, et le nombre d'appels."""
    client = _connecte(qui)
    magasins = _magasins(biblio["tmp"], biblio["sortie"])
    portees = ["", "all", "shared", biblio["projets"]["alice"], biblio["projets"]["partage"]]
    ecrits: dict[str, dict] = {}
    appels = 0
    for route in _routes_get():
        avant = _empreintes(magasins)
        avec_q = "q" in {p.name for p in route.dependant.query_params}
        extras = ({}, {"q": MARQUEUR}, {"q": MARQUEUR, "mode": "semantic"}) if avec_q else ({},)
        for document in biblio["documents"]:
            valeurs = {**document, "job_id": biblio["jobs"][qui].id,
                       "execution_id": biblio["execution"], "archived_name": biblio["archive"],
                       "identifiant": qui, "kind": "semantic", "partage_id": biblio["partage"],
                       "jeton_id": biblio["jeton"]}
            chemin = _chemin(route, valeurs)
            for view_scope in portees:
                for extra in extras:
                    reponse = client.get(chemin, params={"view_scope": view_scope, **extra})
                    assert reponse.status_code < 500 and reponse.status_code != 409, (
                        f"{qui} GET {chemin} view_scope={view_scope!r} {extra} : "
                        f"{reponse.status_code} {reponse.text[:200]}")
                    appels += 1
        ecarts = _ecarts(avant, _empreintes(magasins))
        if ecarts:
            ecrits[route.path] = ecarts
    return ecrits, appels


def test_aucune_route_get_n_ecrit(bibliotheque) -> None:
    """Chaque route GET, pour un compte neuf, une administratrice neuve et la
    propriétaire : rien n'est écrit, hors des routes inscrites avec leur raison."""
    routes = {route.path for route in _routes_get()}
    assert len(routes) >= 30 and "/api/projects" in routes and "/api/config" in routes
    observees: dict[str, dict] = {}
    for qui in ("nora", "ophelie", "alice"):
        ecrits, appels = _lire_chaque_route(bibliotheque, qui)
        assert appels >= len(routes) * 2 * 5, (qui, appels)
        for chemin, ecarts in ecrits.items():
            observees.setdefault(chemin, {})[qui] = ecarts
    non_inscrites = {c: e for c, e in observees.items() if c not in ROUTES_GET_QUI_ECRIVENT}
    assert non_inscrites == {}, f"routes GET qui écrivent sans y être inscrites : {non_inscrites}"
    assert set(ROUTES_GET_QUI_ECRIVENT) <= routes, "une route inscrite n'existe plus"
    sans_objet = set(ROUTES_GET_QUI_ECRIVENT) - set(observees)
    assert sans_objet == set(), f"routes inscrites qui n'écrivent plus : {sans_objet}"


def test_aucun_outil_mcp_n_ecrit(bibliotheque) -> None:
    """Chaque outil de `Lecteur`, pour le compte neuf comme pour la propriétaire."""
    magasins = _magasins(bibliotheque["tmp"], bibliotheque["sortie"])
    reunion = bibliotheque["documents"][0]["meeting_id"]
    reunion_partagee = bibliotheque["documents"][1]["meeting_id"]
    arguments = {
        "lister_projets": [{}],
        "lister_reunions": [{}, {"projet": bibliotheque["projets"]["partage"]}],
        "rechercher": [{"requete": MARQUEUR}, {"requete": MARQUEUR,
                                               "projet": bibliotheque["projets"]["partage"]}],
        "lire_compte_rendu": [{"reunion": reunion}, {"reunion": reunion_partagee}],
        "lire_faits": [{"reunion": reunion}, {"reunion": reunion_partagee}],
    }
    assert set(arguments) == set(mcp.OUTILS)
    appels = 0
    for qui in ("nora", "alice"):
        lecteur = mcp.Lecteur(bibliotheque["comptes"][qui])
        avant = _empreintes(magasins)
        for outil, jeux in arguments.items():
            for params in jeux:
                try:
                    getattr(lecteur, outil)(params)
                except mcp.Introuvable:
                    pass
                appels += 1
        assert _ecarts(avant, _empreintes(magasins)) == {}, qui
    assert appels == 2 * sum(len(jeux) for jeux in arguments.values())


# ---------- Le « Non classé » : jamais créé par une lecture ----------

LECTURES = (
    ("/api/config", {}), ("/api/projects", {}), ("/api/library", {"view_scope": "all"}),
    ("/api/library", {"view_scope": ""}), ("/api/meetings", {"view_scope": ""}),
    ("/api/search", {"view_scope": "all", "q": MARQUEUR}), ("/api/voices", {"view_scope": "all"}),
    ("/api/trash", {"view_scope": ""}), ("/api/usage/rollup", {"view_scope": "all"}),
    ("/api/duplicates", {"view_scope": ""}), ("/api/library/languages", {}),
)


def test_la_premiere_lecture_d_un_compte_neuf_n_ecrit_aucun_manifeste(bibliotheque) -> None:
    sortie = bibliotheque["sortie"]
    avant = _manifestes(sortie)
    assert len(avant) == 3  # la racine (le « Non classé » d'Alice) et ses deux projets
    nora = _connecte("nora")
    for chemin, params in LECTURES:
        assert nora.get(chemin, params=params).status_code == 200, chemin
    assert nora.post("/api/ask", json={"question": "Le budget ?", "scope": {"mode": "all"}}
                     ).status_code in (200, 404)
    assert _manifestes(sortie) == avant


def test_sans_non_classe_les_lectures_repondent_sans_lui(bibliotheque) -> None:
    """Un compte sans « Non classé » : liste des projets sans lui, aucune erreur, et la
    configuration ne désigne aucun projet par défaut qui n'existe pas."""
    nora = _connecte("nora")
    projets = nora.get("/api/projects").json()
    assert all(p.get("kind") != "inbox" for p in projets), projets
    assert [p["id"] for p in projets] == []
    config = nora.get("/api/config").json()
    assert config["default_project_id"] == ""
    assert nora.get("/api/library", params={"view_scope": ""}).json() == []
    # Ancre : la même liste, pour la propriétaire, n'est pas vide.
    assert len(_connecte("alice").get("/api/library", params={"view_scope": "all"}).json()) >= 3
    # Ce qui est partagé avec Nora reste lisible.
    partages_lus = nora.get("/api/library", params={"view_scope": "shared"}).json()
    assert [d["name"] for d in partages_lus] == ["alice-partage.md"]


def test_un_envoi_cree_le_non_classe_s_il_manque(bibliotheque, monkeypatch) -> None:
    monkeypatch.setattr(api_main.jobs, "run", lambda job, target: None)
    sortie = bibliotheque["sortie"]
    nora = _connecte("nora")
    assert not any("nora" in m for m in _manifestes(sortie).values())
    reponse = nora.post("/api/transcribe", files={"file": ("a.mp3", b"a")},
                        data={"destination_project_id": "", "report_on": "false"})
    assert reponse.status_code == 200, reponse.text
    [inbox] = [p for _d, p in projects.all_projects(sortie)
               if p.kind == "inbox" and p.owner == "nora"]
    assert nora.get("/api/config").json()["default_project_id"] == inbox.id
    assert inbox.id in {p["id"] for p in nora.get("/api/projects").json()}


@pytest.mark.parametrize("ecriture", ["envoi", "projet"])
def test_bibliotheque_vierge_l_attribution_n_a_lieu_qu_a_l_ecriture(environnement, monkeypatch,
                                                                     ecriture) -> None:
    """Le « Non classé » de la racine, sans propriétaire sur une bibliothèque vierge,
    revient au premier compte qui ÉCRIT, jamais au premier qui lit."""
    monkeypatch.setattr(api_main.jobs, "run", lambda job, target: None)
    sortie = environnement["sortie"]
    projects.ensure_inbox(sortie)  # comme au démarrage
    auth.creer_compte("premier", MOT_DE_PASSE)
    avant = _manifestes(sortie)
    assert len(avant) == 1 and projects.load(sortie).owner is None
    client = _connecte("premier")
    for chemin, params in LECTURES:
        assert client.get(chemin, params=params).status_code == 200, chemin
    assert _manifestes(sortie) == avant and projects.load(sortie).owner is None

    if ecriture == "envoi":
        reponse = client.post("/api/transcribe", files={"file": ("a.mp3", b"a")},
                              data={"destination_project_id": "", "report_on": "false"})
    else:
        reponse = client.post("/api/projects", json={"name": "Premier projet"})
    assert reponse.status_code == 200, reponse.text
    assert projects.load(sortie).owner == "premier"


def test_la_creation_d_un_compte_lui_donne_son_non_classe(bibliotheque) -> None:
    ophelie = _connecte("ophelie")
    reponse = ophelie.post("/api/admin/utilisateurs", json={
        "identifiant": "paul", "mot_de_passe": MOT_DE_PASSE})
    assert reponse.status_code == 201, reponse.text
    inbox = [p for _d, p in projects.all_projects(bibliotheque["sortie"])
             if p.kind == "inbox" and p.owner == "paul"]
    assert len(inbox) == 1


def test_l_installation_donne_la_racine_vierge_a_l_administrateur(environnement) -> None:
    sortie = environnement["sortie"]
    projects.ensure_inbox(sortie)  # comme au démarrage
    code = auth.preparer_installation()
    assert code
    client = TestClient(api_main.app)
    reponse = client.post("/api/auth/installation", json={
        "code": code, "identifiant": "admin", "mot_de_passe": MOT_DE_PASSE})
    assert reponse.status_code == 200, reponse.text
    assert projects.load(sortie).owner == "admin"
    assert len(_manifestes(sortie)) == 1
