"""Étanchéité entre comptes, éprouvée sur CHAQUE route de `app.routes`.

Trois comptes ordinaires, chacun avec ses projets, ses documents diarisés et ses
voix nommées. A partage avec B, en lecture seule, un projet et une réunion d'un
autre projet ; C n'a rien. B et C appellent toutes les routes /api, sans
exclusion, avec les identifiants de A : documents (partagés ou non, ultra-
confidentiel compris), réunions, projets, corbeille, job, exécution d'usage,
voix, partages, jetons d'appareil, compte.

- une route qui lit ne rend jamais rien de A à C, et rien d'autre à B que ce qui
  lui est partagé : ni les autres réunions, ni l'ultra-confidentiel (Q16), ni
  corbeille, doublons ou usage ;
- une route qui modifie (POST, PUT, PATCH, DELETE) appelée avec un identifiant de A
  rend 404, et les données de A sont inchangées, partages compris ;
- après le retrait des partages, B ne voit plus rien de A ;
- le jeton d'appareil de B, seul, n'ouvre que les routes du compagnon, et
  aucune ne rend ni ne modifie rien de A ; un jeton retiré ne vaut plus rien ;
- jamais de 403 : il confirmerait que l'objet existe chez un autre compte ;
- tout cela deux fois : A sans puis avec le cloud autorisé pour son ultra-confidentiel
  (`PUT /api/compte/cloud-ultra`). Le réglage n'ouvre aucun droit de plus à B ni à C,
  et ni l'un ni l'autre ne peut le changer.

La liste des routes vient de `app.routes` : une route ajoutée plus tard entre dans
ce test sans qu'on y pense, et le test échoue tant qu'il ne sait pas l'appeler.
"""
from __future__ import annotations

import hashlib
import json
import time

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from api import auth, partages
from api import main as api_main
from api.jobs import JobManager
from core import docid, library, projects, purge, semantic, usage_store, voice_registry, voiceprint

pytestmark = [pytest.mark.integration, pytest.mark.sans_session]

MOT_DE_PASSE = "un mot de passe solide"

# Routes publiques de connexion : elles ne prennent aucun objet de A. Elles sont
# appelées quand même, avec l'identifiant de A quand elles en acceptent un, et
# doivent laisser A intact sans rien dire de lui.
ROUTES_D_AUTHENTIFICATION = frozenset({
    ("GET", "/api/auth/etat"),
    ("POST", "/api/auth/installation"),
    ("POST", "/api/auth/connexion"),
    ("POST", "/api/auth/deconnexion"),
})

# Route qui crée un objet du compte appelant, ou règle ce compte, sans désigner aucun
# objet existant : A doit rester intact (son réglage compris), et rien de A ne doit revenir.
ROUTES_SANS_OBJET = frozenset({("POST", "/api/projects"), ("POST", "/api/jetons"),
                               ("PUT", "/api/compte/cloud-ultra")})

MARQUEUR = "zorglubzephyr"            # réunions de A non partagées
MARQUEUR_PARTAGE = "quokkapartage"     # le projet partagé avec B
MARQUEUR_REUNION = "ornithoreunion"    # la réunion partagée avec B
MARQUEUR_ULTRA = "wombatultra"         # l'ultra-confidentiel du projet partagé
QUESTION = "Quel budget pour le projet ?"


def _empreinte(chemin) -> str:
    return hashlib.sha256(chemin.read_bytes()).hexdigest()


def _vec(n: int) -> list[float]:
    from core.config import EMBED_DIM
    v = [0.0] * EMBED_DIM
    v[n % EMBED_DIM] = 1.0
    return v


def _empreinte_vocale(n: int) -> list[float]:
    v = [0.0] * 256
    v[n] = 1.0
    return v


def _connecte(identifiant: str) -> TestClient:
    client = TestClient(api_main.app)
    assert client.post("/api/auth/connexion", json={
        "identifiant": identifiant, "mot_de_passe": MOT_DE_PASSE}).status_code == 200
    return client


@pytest.fixture(params=[False, True], ids=["reglage-inactif", "reglage-actif"])
def comptes(request, isolated_stores, tmp_path, monkeypatch):
    """A, B et C ; projets, réunions, doublons, corbeille, job, usage, voix et partages.

    Paramétrée : A n'autorise pas, puis autorise, le cloud pour son ultra-confidentiel.
    """
    sortie = isolated_stores["output_dir"]
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(auth, "CODE_PATH", tmp_path / ".memoglyph-installation")
    monkeypatch.setattr(api_main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(api_main, "jobs", JobManager())
    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(tmp_path / "usage.db"))
    # Embeddings déterministes : la recherche par le sens et l'assistant tournent
    # pour de vrai, sans modèle.
    compteur = {"n": 0}

    def faux_embed(textes, **_kw):
        sortie_ = []
        for _ in textes:
            sortie_.append(_vec(compteur["n"]))
            compteur["n"] += 1
        return sortie_

    monkeypatch.setattr(semantic.embed, "embed_documents", faux_embed)
    monkeypatch.setattr(semantic.embed, "embed_query", lambda t, **kw: _vec(0))
    # L'assistant répond en recopiant tout son contexte : un passage de A qui
    # entrerait dans la réponse de B se lirait dans le corps.
    from core import rag
    monkeypatch.setattr(rag.llm, "is_available", lambda **_kw: True)
    monkeypatch.setattr(rag.llm, "chat", lambda _sys, prompt, **_kw: prompt)

    for identifiant in ("alice-zephyr", "bruno", "carole"):
        auth.creer_compte(identifiant, MOT_DE_PASSE)
    compte_a = auth.verifier_identifiants("alice-zephyr", MOT_DE_PASSE)

    # A d'abord : sur une bibliothèque vierge, A reçoit le « Non classé » de la
    # racine. B et C ont le leur dans un sous-dossier.
    dossier_inbox_a, inbox_a = projects.personal_inbox(sortie, "alice-zephyr")
    dossier_a, projet_a = projects.create_owned(sortie, name="ProjetZephyrAlice", owner="alice-zephyr")
    dossier_p, projet_p = projects.create_owned(sortie, name="ProjetPartageAlice", owner="alice-zephyr")
    dossier_inbox_b, inbox_b = projects.personal_inbox(sortie, "bruno")
    dossier_b, projet_b = projects.create_owned(sortie, name="ProjetDeBruno", owner="bruno")
    dossier_c, projet_c = projects.create_owned(sortie, name="ProjetDeCarole", owner="carole")

    def document(dossier, nom, projet_id, corps, sensibilite="standard"):
        identite, reunion = docid.nouveau(), docid.nouveau()
        (dossier / nom).write_text(
            f"---\nid: {identite}\nmeeting_id: {reunion}\nproject_id: {projet_id}\n"
            f"title: Titre {nom}\nlanguage: fr\nsensitivity: {sensibilite}\n---\n\n{corps}\n",
            encoding="utf-8")
        return identite, reunion

    def texte(marqueur):
        return (f"Réunion confidentielle {marqueur} sur le budget du projet.\n\n"
                f"Décision {marqueur} : reporter la livraison au trimestre suivant.")

    doc_a, reunion_a = document(dossier_a, "alice-zephyr-1.md", projet_a.id, texte(MARQUEUR))
    doc_a2, reunion_a2 = document(dossier_a, "alice-zephyr-2.md", projet_a.id, texte(MARQUEUR))  # doublon
    doc_a3, _ = document(dossier_inbox_a, "alice-zephyr-corbeille.md", inbox_a.id, texte(MARQUEUR))
    doc_r, reunion_r = document(dossier_a, "alice-reunion.md", projet_a.id, texte(MARQUEUR_REUNION))
    doc_p, reunion_p = document(dossier_p, "alice-partage.md", projet_p.id, texte(MARQUEUR_PARTAGE))
    doc_u, reunion_u = document(dossier_p, "alice-ultra.md", projet_p.id, texte(MARQUEUR_ULTRA),
                                "ultra_confidential")
    doc_b, reunion_b = document(dossier_b, "bruno-1.md", projet_b.id,
                                "Réunion de Bruno sur le budget du projet.\n\nRien d'autre.")
    doc_c, reunion_c = document(dossier_c, "carole-1.md", projet_c.id,
                                "Réunion de Carole sur le budget du projet.\n\nRien d'autre.")
    library.reindex()
    semantic.reindex_all()
    # La même personne (empreinte 0) enregistrée par A, B et C : une voix par compte.
    for identite, nom, voix in ((doc_a, "alice-zephyr-1.md", 0), (doc_a2, "alice-zephyr-2.md", 0),
                                (doc_p, "alice-partage.md", 1), (doc_r, "alice-reunion.md", 1),
                                (doc_u, "alice-ultra.md", 2), (doc_b, "bruno-1.md", 0),
                                (doc_c, "carole-1.md", 0)):
        voiceprint.save_embeddings(identite, {"Speaker 1": _empreinte_vocale(voix)})
        voiceprint.index_name(nom)

    def voix(nom):
        return voiceprint.assignments(nom)["Speaker 1"]["voice_id"]

    assert len({voix("alice-zephyr-1.md"), voix("bruno-1.md"), voix("carole-1.md")}) == 3
    assert voix("alice-zephyr-2.md") == voix("alice-zephyr-1.md")
    assert voix("alice-reunion.md") == voix("alice-partage.md")
    for nom, label in (("alice-zephyr-1.md", "VoixZephyrAlice"), ("alice-partage.md", "VoixPartageeAlice"),
                       ("alice-ultra.md", "VoixUltraAlice"), ("bruno-1.md", "VoixDeBruno"),
                       ("carole-1.md", "VoixDeCarole")):
        voiceprint.rename(voix(nom), label)
    assert purge.archive("alice-zephyr-corbeille.md")["archived"]
    [archive_a] = [i["name"] for i in library.trash_items() if i["project_id"] == inbox_a.id]

    job_a = api_main.jobs.create(owner=compte_a.id)
    api_main.jobs.run(job_a, lambda progres: progres(0.5, f"job {MARQUEUR}")
                      or {"name": "alice-zephyr-1.md", "content": texte(MARQUEUR)})
    for _ in range(200):
        if job_a.status == "done":
            break
        time.sleep(0.005)

    execution_a = "EXEC-ALICE-ZEPHYR"
    conn = usage_store.connect(tmp_path / "usage.db")
    conn.execute(
        "INSERT INTO execution(execution_id,kind,document_id,meeting_id,project_id,"
        "produced_artifact_id,provider_primary,model_primary,started_at,ended_at,wall_clock_s,"
        "status,n_calls,in_tok,out_tok,audio_seconds,schema_version,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (execution_a, "report", doc_p, reunion_p, projet_p.id, None, "openai", "gpt-x",
         "2026-08-09T10:00:00+00:00", "2026-08-09T10:00:00+00:00", 1.0, "ok", 0, None, None,
         None, usage_store.SCHEMA_VERSION, "2026-08-09T10:00:00+00:00"))
    conn.commit()
    conn.close()
    usage_store.rebuild_usage_daily(tmp_path / "usage.db")

    # Les partages passent par l'API, comme A le ferait.
    client_a = _connecte("alice-zephyr")
    if request.param:
        reglage = client_a.put("/api/compte/cloud-ultra", json={"autorise": True})
        assert reglage.status_code == 200 and reglage.json()["autorise"] is True
    partage_p = client_a.post("/api/partages", json={
        "type": "projet", "cible": projet_p.id, "invite": "bruno"}).json()["partage"]["id"]
    partage_r = client_a.post("/api/partages", json={
        "type": "reunion", "cible": reunion_r, "invite": "bruno"}).json()["partage"]["id"]

    jeton_a, secret_a = auth.creer_jeton(compte_a, "MacZephyrAlice")
    commun = {"job": job_a.id, "execution": execution_a, "archive": archive_a,
              "identifiant": "alice-zephyr", "partage": partage_p, "jeton": jeton_a["id"]}
    a = {
        "projets": [inbox_a.id, projet_a.id, projet_p.id],
        "projet": projet_a.id,
        "projet_partage": projet_p.id,
        "inbox": inbox_a.id,
        # Trois jeux d'identifiants de A : non partagé, partagé, ultra-confidentiel.
        "ids": [
            {**commun, "document": "alice-zephyr-1.md", "reunion": reunion_a,
             "voix": str(voix("alice-zephyr-1.md")), "projet": projet_a.id},
            {**commun, "document": "alice-partage.md", "reunion": reunion_r,
             "voix": str(voix("alice-partage.md")), "projet": projet_p.id, "partage": partage_r},
            {**commun, "document": "alice-ultra.md", "reunion": reunion_u,
             "voix": str(voix("alice-ultra.md")), "projet": projet_p.id},
        ],
        "voix": [voix("alice-zephyr-1.md"), voix("alice-partage.md"), voix("alice-ultra.md")],
        "voix_partagee": voix("alice-partage.md"),
        "partages": [partage_p, partage_r],
        "dossiers": [dossier_inbox_a, dossier_a, dossier_p],
        "jeton": auth.ouvrir_session(compte_a),
        "compte": compte_a,
        "secret_appareil": secret_a,
        "client": client_a,
    }
    # Tout ce qui, lu dans une réponse à B ou C, trahirait A.
    a["marqueurs"] = [
        inbox_a.id, projet_a.id, projet_p.id, "ProjetZephyrAlice", "ProjetPartageAlice",
        "alice-zephyr", MARQUEUR, MARQUEUR_PARTAGE, MARQUEUR_REUNION, MARQUEUR_ULTRA,
        doc_a, doc_a2, doc_a3, doc_r, doc_p, doc_u, reunion_a, reunion_a2, reunion_r, reunion_p,
        reunion_u, "alice-zephyr-1.md", "alice-zephyr-2.md", "alice-zephyr-corbeille.md",
        "alice-reunion.md", "alice-partage.md", "alice-ultra.md", archive_a, job_a.id,
        execution_a, "VoixZephyrAlice", "VoixPartageeAlice", "VoixUltraAlice", partage_p, partage_r,
        jeton_a["id"], "MacZephyrAlice",
    ]
    # Ce que B peut lire tant que les partages tiennent : les documents partagés,
    # leurs projets d'origine, leur voix (Q12), le nom de qui partage.
    a["partage_avec_b"] = {
        projet_p.id, "ProjetPartageAlice", projet_a.id, "ProjetZephyrAlice", "alice-zephyr",
        MARQUEUR_PARTAGE, MARQUEUR_REUNION, doc_p, doc_r, reunion_p, reunion_r,
        "alice-partage.md", "alice-reunion.md", "VoixPartageeAlice", partage_p, partage_r,
    }

    def invite(identifiant, ids):
        compte = auth.verifier_identifiants(identifiant, MOT_DE_PASSE)
        return {"client": _connecte(identifiant), "ids": ids,
                "secret_appareil": auth.creer_jeton(compte, f"Mac de {identifiant}")[1]}

    return {
        "a": a,
        "b": invite("bruno", {"projet": projet_b.id, "inbox": inbox_b.id, "document": "bruno-1.md",
                              "reunion": reunion_b, "voix": str(voix("bruno-1.md"))}),
        "c": invite("carole", {"projet": projet_c.id, "document": "carole-1.md",
                               "reunion": reunion_c, "voix": str(voix("carole-1.md"))}),
    }


def _etat_de_a(a: dict) -> dict:
    """Ce que B et C ne doivent jamais changer : fichiers, index, corbeille, job,
    session, voix et partages de A."""
    fichiers = {}
    for dossier in a["dossiers"]:
        for chemin in sorted(dossier.iterdir()):
            if chemin.is_file():
                fichiers[str(chemin)] = _empreinte(chemin)
    lignes = library.list_transcripts(project_id=frozenset(a["projets"]))
    job = api_main.jobs.get(a["ids"][0]["job"])
    return {
        "fichiers": fichiers,
        "index": sorted(json.dumps(r, sort_keys=True, default=str) for r in lignes),
        "corbeille": sorted(i["name"] for i in library.trash_items()
                            if i.get("project_id") in a["projets"]),
        "job": None if job is None else (job.status, job.owner, job.result),
        "session": auth.utilisateur_de_session(a["jeton"]),
        "comptes": auth.lister_comptes(),
        "voix": voiceprint.list_voices(project_id=frozenset(a["projets"]),
                                       compte=frozenset(a["projets"])),
        "registre": _empreinte(voice_registry.FICHIER),
        "empreintes": voiceprint.load_embeddings(a["ids"][0]["document"]),
        "partages": partages.donnes("alice-zephyr"),
        "jetons": auth.lister_jetons(a["compte"]),
        "cloud_ultra": auth.cloud_ultra(a["compte"]),
    }


def _chemin(route: APIRoute, ids: dict) -> str:
    valeurs = {
        "name": ids["document"], "meeting_id": ids["reunion"], "job_id": ids.get("job", "x"),
        "execution_id": ids.get("execution", "x"), "archived_name": ids.get("archive", "x"),
        "identifiant": ids.get("identifiant", "x"), "kind": "semantic",
        "voice_id": ids.get("voix", "0"), "partage_id": ids.get("partage", "x"),
        "jeton_id": ids.get("jeton", "x"),
    }
    chemin = route.path
    for parametre in route.param_convertors:
        assert parametre in valeurs, f"paramètre inconnu du test : {parametre} ({route.path})"
        chemin = chemin.replace("{" + parametre + "}", valeurs[parametre])
    return chemin


# Routes dont le CORPS porte un identifiant de A dans `_corps`.
ROUTES_A_CORPS_DE_A = frozenset({
    "/api/transcribe", "/api/ask", "/api/admin/utilisateurs", "/api/partages",
})


def _corps(methode: str, chemin: str, ids_a: dict, cible: str) -> dict:
    """Arguments de requête valides pour la route ; `cible` = un projet ou une voix."""
    if chemin == "/api/transcribe":
        return {"files": {"file": ("a.mp3", b"a")}, "data": {"destination_project_id": ids_a["projet"]}}
    if chemin == "/api/ask":
        return {"json": {"question": QUESTION, "scope": {"mode": "project", "project_id": ids_a["projet"]}}}
    if chemin == "/api/partages" and methode == "POST":
        return {"json": {"type": "projet", "cible": ids_a["projet"], "invite": "carole"}}
    if chemin.endswith("/project"):
        return {"json": {"destination_project_id": cible}}
    if chemin.endswith("/merge"):
        return {"json": {"target_id": int(cible)}}
    if chemin == "/api/voices/{voice_id}":
        return {"json": {"label": "Intrus"}}
    if chemin.endswith("/sensitivity"):
        return {"json": {"sensitivity": "confidential"}}
    if chemin == "/api/admin/utilisateurs":
        return {"json": {"identifiant": ids_a["identifiant"], "mot_de_passe": MOT_DE_PASSE}}
    if chemin == "/api/projects":
        return {"json": {"name": "ProjetNeufDeLInvite"}}
    if chemin == "/api/jetons":
        return {"json": {"nom": "JetonNeufDeLInvite"}}
    if chemin == "/api/auth/connexion":
        return {"json": {"identifiant": ids_a["identifiant"], "mot_de_passe": "pas le sien du tout"}}
    if chemin == "/api/auth/installation":
        return {"json": {"code": "AAAA-BBBB-CCCC", "identifiant": ids_a["identifiant"],
                         "mot_de_passe": MOT_DE_PASSE}}
    if methode in {"POST", "PUT", "PATCH"}:
        return {"json": {}}
    return {}


def _routes() -> list[tuple[str, APIRoute]]:
    return [
        (methode, route)
        for route in api_main.app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api/")
        for methode in sorted(route.methods - {"HEAD", "OPTIONS"})
    ]


def _sans_trace_de_a(reponse, interdits, quoi: str) -> None:
    assert reponse.status_code != 403, f"{quoi} : 403 trahit l'existence de l'objet"
    texte = reponse.text
    fuites = [m for m in interdits if m in texte]
    assert not fuites, f"{quoi} : la réponse contient {fuites} (statut {reponse.status_code})"


def _interdits(comptes: dict, qui: str, partages_actifs: bool = True) -> list[str]:
    a = comptes["a"]
    if qui == "b" and partages_actifs:
        return [m for m in a["marqueurs"] if m not in a["partage_avec_b"]]
    return list(a["marqueurs"])


def _lire_tout(comptes: dict, qui: str, interdits: list[str]) -> int:
    """Chaque route GET, appelée par `qui` avec chaque jeu d'identifiants de A et
    chaque portée ; plus l'assistant, qui lit en POST. Rend le nombre de routes."""
    a, invite = comptes["a"], comptes[qui]
    client = invite["client"]
    portees = [a["projet"], a["inbox"], a["projet_partage"], "all", "", "shared",
               invite["ids"]["projet"]]
    lues = set()
    for methode, route in _routes():
        if methode != "GET":
            continue
        avec_q = "q" in {p.name for p in route.dependant.query_params}
        extras = ({}, {"q": MARQUEUR}, {"q": MARQUEUR_ULTRA, "mode": "semantic"}) if avec_q else ({},)
        for ids in a["ids"]:
            chemin = _chemin(route, ids)
            for portee in portees:
                for extra in extras:
                    reponse = client.get(chemin, params={"view_scope": portee, **extra})
                    _sans_trace_de_a(reponse, interdits, f"{qui} GET {chemin} view_scope={portee!r} {extra}")
        lues.add(route.path)
    for scope in ({"mode": "all"}, {"mode": "shared"}, {"mode": "project", "project_id": a["projet"]}):
        reponse = client.post("/api/ask", json={"question": QUESTION, "scope": scope})
        _sans_trace_de_a(reponse, interdits, f"{qui} POST /api/ask {scope}")
    # Les identifiants de voix sont des entiers, trop courts pour une recherche de
    # texte : on les cherche dans la structure des réponses.
    lisibles = {a["voix_partagee"]} if interdits != a["marqueurs"] and qui == "b" else set()
    for portee in ("all", "", "shared", invite["ids"]["projet"]):
        vues = {v["id"] for v in client.get("/api/voices", params={"view_scope": portee}).json()}
        assert not (vues & set(a["voix"])) - lisibles, (qui, portee)
    lecture = client.get(f"/api/transcript/{invite['ids']['document']}", params={"view_scope": "all"}).json()
    assert {v["voice_id"] for v in lecture["voices"].values()} == {int(invite["ids"]["voix"])}
    assert "/api/transcript/{name}" in lues and "/api/search" in lues and "/api/trash" in lues
    return len(lues)


def test_les_routes_exclues_existent_encore() -> None:
    """Aucune route n'est exclue : celles des voix et des partages comprises.

    Les routes traitées à part existent encore : une entrée qui ne correspondrait
    plus à aucune route serait un trou silencieux.
    """
    existantes = {(m, r.path) for r in api_main.app.routes if isinstance(r, APIRoute)
                  for m in r.methods}
    assert ROUTES_D_AUTHENTIFICATION <= existantes and ROUTES_SANS_OBJET <= existantes
    appelees = {(m, r.path) for m, r in _routes()}
    assert {("GET", "/api/voices"), ("PATCH", "/api/voices/{voice_id}"),
            ("POST", "/api/voices/{voice_id}/merge"), ("POST", "/api/partages"),
            ("GET", "/api/partages"), ("DELETE", "/api/partages/{partage_id}"),
            ("GET", "/api/jetons"), ("POST", "/api/jetons"),
            ("DELETE", "/api/jetons/{jeton_id}")} <= appelees
    assert api_main.ROUTES_DU_COMPAGNON <= appelees
    assert {r for r in ROUTES_A_CORPS_DE_A} <= {r.path for _m, r in _routes()}


def test_aucune_lecture_de_b_ne_rend_rien_de_a(comptes) -> None:
    for qui in ("b", "c"):
        n = _lire_tout(comptes, qui, _interdits(comptes, qui))
        print(f"\n{qui} : {n} routes GET lues, rien de A hors de ce qui lui est partagé")
    # Témoin : les mêmes appels faits par A rendent bien ses marqueurs. Sans lui,
    # un test qui ne saurait rien détecter passerait aussi.
    a = comptes["a"]
    client_a = a["client"]
    for chemin, params in (("/api/search", {"q": MARQUEUR, "view_scope": "all"}),
                           ("/api/search", {"q": MARQUEUR, "view_scope": "all", "mode": "semantic"}),
                           ("/api/projects", {}), ("/api/trash", {"view_scope": "all"}),
                           (f"/api/jobs/{a['ids'][0]['job']}", {})):
        assert any(m in client_a.get(chemin, params=params).text for m in a["marqueurs"]), chemin
    reponse_a = client_a.post("/api/ask", json={"question": QUESTION, "scope": {"mode": "all"}})
    assert MARQUEUR in reponse_a.text
    assert "VoixZephyrAlice" in client_a.get("/api/voices", params={"view_scope": "all"}).text


def test_l_invite_lit_ce_qui_lui_est_partage(comptes) -> None:
    """Témoin positif du partage : B lit bien ce qui lui est partagé, C rien."""
    a, b, c = comptes["a"], comptes["b"]["client"], comptes["c"]["client"]
    shared = {"view_scope": "shared"}

    assert sorted(d["name"] for d in b.get("/api/library", params=shared).json()) \
        == ["alice-partage.md", "alice-reunion.md"]
    assert MARQUEUR_PARTAGE in b.get("/api/transcript/alice-partage.md", params=shared).text
    assert MARQUEUR_REUNION in b.get("/api/search", params={**shared, "q": MARQUEUR_REUNION}).text
    assert MARQUEUR_PARTAGE in b.post("/api/ask", json={"question": QUESTION,
                                                         "scope": {"mode": "shared"}}).text
    assert [v["display"] for v in b.get("/api/voices", params=shared).json()] == ["VoixPartageeAlice"]
    assert len(b.get("/api/partages").json()["recus"]) == 2
    assert c.get("/api/library", params=shared).json() == []
    assert c.get("/api/partages").json() == {"donnes": [], "recus": []}
    assert a["projet_partage"] in {p["cible"] for p in a["client"].get("/api/partages").json()["donnes"]}


def test_aucune_modification_de_b_n_atteint_a(comptes) -> None:
    a = comptes["a"]
    avant = _etat_de_a(a)
    appels = 0
    for qui in ("b", "c"):
        invite = comptes[qui]
        for methode, route in _routes():
            if methode == "GET":
                continue
            cle = (methode, route.path)
            fusion = route.path.endswith("/merge")
            variantes = []
            for ids in a["ids"][:2]:   # non partagé, puis partagé
                cible_invite = invite["ids"]["voix"] if fusion else invite["ids"]["projet"]
                for portee in (ids["projet"], "all", "shared"):
                    variantes.append((_chemin(route, ids), portee, cible_invite, ids))
                # L'objet de l'invité envoyé vers un projet ou une voix de A.
                if route.path.endswith("/project") or fusion:
                    cible_a = ids["voix"] if fusion else ids["projet"]
                    variantes.append((_chemin(route, invite["ids"]), invite["ids"]["projet"], cible_a, ids))
            for chemin, portee, cible, ids in variantes:
                arguments = _corps(methode, route.path, ids, cible)
                params = {"view_scope": portee, "hard": "true"}
                # Sans identifiant de A ni dans le chemin, ni dans la portée, ni
                # dans le corps, la route agit sur l'invité seul (vider SA corbeille).
                sans_a = (not route.param_convertors and portee == "all"
                          and route.path not in ROUTES_A_CORPS_DE_A)
                if cle in ROUTES_D_AUTHENTIFICATION:
                    client = TestClient(api_main.app)  # la déconnexion ne coupe que ce client
                    reponse = client.request(methode, chemin, params=params, **arguments)
                    assert reponse.status_code != 200 or route.path == "/api/auth/deconnexion", (
                        methode, chemin, reponse.status_code)
                else:
                    reponse = invite["client"].request(methode, chemin, params=params, **arguments)
                    if not sans_a and cle not in ROUTES_SANS_OBJET:
                        assert reponse.status_code == 404, (
                            f"{qui} {methode} {chemin} view_scope={portee} cible={cible} : "
                            f"{reponse.status_code} {reponse.text[:200]}")
                _sans_trace_de_a(reponse, _interdits(comptes, qui), f"{qui} {methode} {chemin}")
                assert _etat_de_a(a) == avant, f"{qui} {methode} {chemin} a modifié les données de A"
                appels += 1
    assert appels >= 2 * 2 * 25
    print(f"\n{appels} appels modifiants de B et C : 404 sur chaque objet de A, A inchangé")


def test_apres_le_retrait_l_invite_ne_voit_plus_rien(comptes) -> None:
    a = comptes["a"]
    for partage_id in a["partages"]:
        assert a["client"].delete(f"/api/partages/{partage_id}").status_code == 200

    n = _lire_tout(comptes, "b", _interdits(comptes, "b", partages_actifs=False))

    assert comptes["b"]["client"].get("/api/partages").json()["recus"] == []
    print(f"\nb après retrait : {n} routes GET lues, plus rien de A")


def test_le_jeton_d_appareil_de_b_n_ouvre_rien_de_a(comptes) -> None:
    """Le jeton de B, sans cookie, sur chaque route et chaque identifiant de A.

    Hors des routes du compagnon : 401. Sur elles : 404 pour un objet de A,
    jamais une trace de A, partages compris (le jeton ne lit aucune réunion),
    et A inchangé.
    """
    a, b = comptes["a"], comptes["b"]
    compagnon = TestClient(api_main.app, headers={
        "Authorization": f"Bearer {b['secret_appareil']}"})
    avant = _etat_de_a(a)
    du_compagnon = 0
    for methode, route in _routes():
        cle = (methode, route.path)
        for ids in a["ids"]:
            chemin = _chemin(route, ids)
            for portee in (ids["projet"], "all", "shared"):
                cible = ids["voix"] if route.path.endswith("/merge") else ids["projet"]
                arguments = _corps(methode, route.path, ids, cible)
                reponse = compagnon.request(methode, chemin, params={"view_scope": portee},
                                            **arguments)
                quoi = f"jeton de b : {methode} {chemin} view_scope={portee}"
                if cle in api_main.ROUTES_DU_COMPAGNON:
                    attendu = 200 if route.path == "/api/projects" else 404
                    assert reponse.status_code == attendu, (quoi, reponse.status_code, reponse.text)
                    du_compagnon += 1
                elif cle not in api_main.ROUTES_PUBLIQUES:
                    assert reponse.status_code == 401, (quoi, reponse.status_code)
                _sans_trace_de_a(reponse, a["marqueurs"], quoi)
                assert _etat_de_a(a) == avant, f"{quoi} a modifié les données de A"
    assert du_compagnon == len(api_main.ROUTES_DU_COMPAGNON) * len(a["ids"]) * 3
    # Témoin : le même jeton ouvre bien les projets de B.
    assert comptes["b"]["ids"]["projet"] in compagnon.get("/api/projects").text


def test_un_jeton_d_appareil_retire_ne_vaut_plus_rien(comptes) -> None:
    a = comptes["a"]
    compagnon = TestClient(api_main.app, headers={"Authorization": f"Bearer {a['secret_appareil']}"})
    assert compagnon.get("/api/projects").status_code == 200
    [jeton] = a["client"].get("/api/jetons").json()

    assert a["client"].delete(f"/api/jetons/{jeton['id']}").status_code == 200

    assert compagnon.get("/api/projects").status_code == 401
    assert compagnon.get(f"/api/jobs/{a['ids'][0]['job']}").status_code == 401
