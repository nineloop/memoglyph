"""Les routes adressables par identifiant sont-elles bornées par la portée ?

Piège mesuré de ce lot : les tests d'intégration existants créent presque tout
dans la boîte de réception. Un durcissement qui casserait tout resterait vert.
CHAQUE test ci-dessous crée donc son document dans un SECOND projet, puis
l'atteint depuis la portée d'un AUTRE — le seul chemin qui prouve la borne.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api import main as api_main
from api.jobs import JobManager
from core import docid, projects


pytestmark = pytest.mark.integration


@pytest.fixture
def client(monkeypatch, tmp_path, isolated_stores):
    monkeypatch.setattr(api_main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(api_main, "jobs", JobManager())
    # Ollama est sorti du registre ; ce bouchon visait son sondage
    # réseau au démarrage de la route de configuration, qui n'existe plus.
    return TestClient(api_main.app)


@pytest.fixture
def deux_projets(isolated_stores):
    """Boîte de réception + un SECOND projet contenant l'unique document.

    Le document est écrit dans `output/Client/`, jamais à la racine : c'est ce
    qui rend le test capable d'échouer.
    """
    output = isolated_stores["output_dir"]
    inbox = projects.create(output, name=projects.INBOX_NAME, kind="inbox")
    client_projet = projects.create(output / "Client", name="Client")

    document_id, meeting_id = docid.nouveau(), docid.nouveau()
    (output / "Client").joinpath("client.md").write_text(
        f"---\nid: {document_id}\nmeeting_id: {meeting_id}\n"
        f"project_id: {client_projet.id}\ntitle: Revue Client\n"
        f"source: revue-client.mp3\n---\n\nSpeaker 1: secret du client\n",
        encoding="utf-8",
    )
    api_main.library.reindex()
    return {
        "output": output, "inbox": inbox, "client": client_projet,
        "document_id": document_id, "meeting_id": meeting_id,
    }


# --- Gravité 1 : destruction ------------------------------------------------

def test_suppression_hors_portee_refusee(client, deux_projets) -> None:
    hors = client.delete(
        "/api/transcript/client.md",
        params={"view_scope": deux_projets["inbox"].id},
    )
    assert hors.status_code == 404
    # Et le document est TOUJOURS là : un 404 qui aurait quand même archivé
    # serait le pire des deux mondes.
    assert (deux_projets["output"] / "Client" / "client.md").exists()

    dans = client.delete(
        "/api/transcript/client.md",
        params={"view_scope": deux_projets["client"].id},
    )
    assert dans.status_code == 200
    assert not (deux_projets["output"] / "Client" / "client.md").exists()


def test_suppression_de_reunion_hors_portee_refusee(client, deux_projets) -> None:
    hors = client.delete(
        f"/api/meetings/{deux_projets['meeting_id']}",
        params={"view_scope": deux_projets["inbox"].id},
    )
    assert hors.status_code == 404
    assert (deux_projets["output"] / "Client" / "client.md").exists()

    dans = client.delete(
        f"/api/meetings/{deux_projets['meeting_id']}",
        params={"view_scope": deux_projets["client"].id},
    )
    assert dans.status_code == 200


# --- Gravité 2 : génération de CR (lit tout le transcript, peut partir au cloud)

def test_generation_de_cr_hors_portee_refusee(client, deux_projets, monkeypatch) -> None:
    """Le CR est la fuite la plus grave : il lit le transcript ENTIER et peut
    l'envoyer à un LLM cloud. Le garde doit donc agir AVANT toute lecture."""
    lus: list[str] = []

    def make_report(name, **_options):
        lus.append(name)
        return {"name": f"{name}-cr.md"}

    monkeypatch.setattr("core.report.generate_report", make_report)

    hors = client.post(
        "/api/transcript/client.md/report",
        params={"view_scope": deux_projets["inbox"].id},
    )
    assert hors.status_code == 404
    assert lus == []  # rien n'a été lu, donc rien n'a pu partir


# --- Gravité 3 : lecture ----------------------------------------------------

def test_lecture_hors_portee_refusee(client, deux_projets) -> None:
    inbox_id, client_id = deux_projets["inbox"].id, deux_projets["client"].id
    for route in (
        "/api/transcript/client.md",
        "/api/transcript/client.md/download",
        f"/api/meetings/{deux_projets['meeting_id']}",
    ):
        assert client.get(route, params={"view_scope": inbox_id}).status_code == 404, route
        assert client.get(route, params={"view_scope": client_id}).status_code == 200, route
        # Un lien partagé `?projet=all` reste un élargissement EXPLICITE, livré
        # et à préserver.
        assert client.get(route, params={"view_scope": "all"}).status_code == 200, route


def test_audio_hors_portee_refuse(client, deux_projets, monkeypatch) -> None:
    from core import config, media

    audio = deux_projets["output"] / ".audio"
    audio.mkdir(parents=True, exist_ok=True)
    # `audio_path` lit `config.AUDIO_DIR` À L'APPEL, et `isolated_stores` ne le
    # redirige pas : sans ce monkeypatch le test écrirait dans le corpus RÉEL.
    monkeypatch.setattr(config, "AUDIO_DIR", audio)
    piste = audio / (media.audio_path("client.md").stem + ".mp3")
    piste.write_bytes(b"ID3 audio du client")

    hors = client.get(
        "/api/transcript/client.md/audio",
        params={"view_scope": deux_projets["inbox"].id},
    )
    assert hors.status_code == 404
    assert b"audio du client" not in hors.content

    dans = client.get(
        "/api/transcript/client.md/audio",
        params={"view_scope": deux_projets["client"].id},
    )
    assert dans.status_code == 200


# --- Gravité 4 : déplacement ------------------------------------------------

def test_deplacement_hors_portee_refuse(client, deux_projets) -> None:
    """Sans borne, une portée A pouvait aspirer chez elle un document de B."""
    inbox_id = deux_projets["inbox"].id
    hors = client.patch(
        "/api/transcript/client.md/project",
        params={"view_scope": inbox_id},
        json={"destination_project_id": inbox_id},
    )
    assert hors.status_code == 404
    assert (deux_projets["output"] / "Client" / "client.md").exists()

    hors_reunion = client.patch(
        f"/api/meetings/{deux_projets['meeting_id']}/project",
        params={"view_scope": inbox_id},
        json={"destination_project_id": inbox_id},
    )
    assert hors_reunion.status_code == 404
    assert (deux_projets["output"] / "Client" / "client.md").exists()

    dans = client.patch(
        "/api/transcript/client.md/project",
        params={"view_scope": deux_projets["client"].id},
        json={"destination_project_id": inbox_id},
    )
    assert dans.status_code == 200
    assert (deux_projets["output"] / "client.md").exists()


# --- Gravité 5 : énumération ------------------------------------------------

def test_plan_archivage_hors_portee_refuse(client, deux_projets) -> None:
    hors = client.get(
        "/api/transcript/client.md/archive-plan",
        params={"view_scope": deux_projets["inbox"].id},
    )
    assert hors.status_code == 404
    dans = client.get(
        "/api/transcript/client.md/archive-plan",
        params={"view_scope": deux_projets["client"].id},
    )
    assert dans.status_code == 200


# --- Les QUATRE clés de transcript_path -------------------------------------

def test_les_quatre_cles_de_transcript_path_sont_toutes_bornees(
    client, deux_projets,
) -> None:
    """`transcript_path` accepte nom, document_id, source ET titre. Un garde qui
    ne filtrerait que sur le nom laisserait trois portes ouvertes."""
    inbox_id, client_id = deux_projets["inbox"].id, deux_projets["client"].id
    for cle in (
        "client.md",                     # nom
        deux_projets["document_id"],     # document_id
        "revue-client.mp3",              # source
        "Revue Client",                  # titre
    ):
        hors = client.get(f"/api/transcript/{cle}", params={"view_scope": inbox_id})
        assert hors.status_code == 404, cle
        assert "secret du client" not in hors.text, cle
        dans = client.get(f"/api/transcript/{cle}", params={"view_scope": client_id})
        assert dans.status_code == 200, cle


# --- Le fail-open des deux blocs dupliqués ----------------------------------

def test_document_sans_project_id_declare_reste_borne(
    client, isolated_stores,
) -> None:
    """L'ancien garde était fail-OPEN : `doc_project_id and ...` laissait passer
    tout document dont le front-matter n'annonçait pas de `project_id`.

    Le dossier fait foi : ce document vit dans `Client/`, il n'appartient donc
    pas à la boîte de réception, quoi que dise (ou taise) son front-matter.
    """
    output = isolated_stores["output_dir"]
    inbox = projects.create(output, name=projects.INBOX_NAME, kind="inbox")
    projects.create(output / "Client", name="Client")
    muet = (output / "Client") / "muet.md"
    muet.write_text(
        f"---\nid: {docid.nouveau()}\nmeeting_id: {docid.nouveau()}\n"
        f"title: Sans portee declaree\n---\n\nSpeaker 1: secret du client\n",
        encoding="utf-8",
    )
    # `reindex()` est tout-ou-rien et REFUSE un corpus sans `project_id` ; on
    # passe donc par `sync_file`, le chemin par lequel un document entre
    # réellement dans l'index après une transcription.
    api_main.library.sync_file(muet)

    lecture = client.get("/api/transcript/muet.md", params={"view_scope": inbox.id})
    assert lecture.status_code == 404
    assert "secret du client" not in lecture.text
    assert client.get(
        "/api/transcript/muet.md/archive-plan", params={"view_scope": inbox.id},
    ).status_code == 404
    assert client.delete(
        "/api/transcript/muet.md", params={"view_scope": inbox.id},
    ).status_code == 404
    assert (output / "Client" / "muet.md").exists()


# --- La portée est OBLIGATOIRE, pas défaillante par défaut -------------------

def test_portee_absente_rend_422_pas_un_404_muet(client, deux_projets) -> None:
    """Décision tranchée : pas de valeur par défaut. `_project_id_from_view_scope("")`
    rend la boîte de réception, pas « tout » — un défaut ferait de chaque appel
    étourdi un 404 muet sur les documents classés. 422 se voit."""
    meeting_id = deux_projets["meeting_id"]
    for methode, route in (
        (client.get, "/api/transcript/client.md"),
        (client.get, "/api/transcript/client.md/download"),
        (client.get, "/api/transcript/client.md/audio"),
        (client.get, "/api/transcript/client.md/archive-plan"),
        (client.delete, "/api/transcript/client.md"),
        (client.post, "/api/transcript/client.md/report"),
        (client.get, f"/api/meetings/{meeting_id}"),
        (client.delete, f"/api/meetings/{meeting_id}"),
    ):
        assert methode(route).status_code == 422, route
    for route in (
        "/api/transcript/client.md/project",
        f"/api/meetings/{meeting_id}/project",
    ):
        reponse = client.patch(
            route, json={"destination_project_id": deux_projets["inbox"].id},
        )
        assert reponse.status_code == 422, route


# --- Les deux propriétés que rien ne gardait --------------------------------
#
# Muter le garde laissait les 160 tests d'intégration verts dans les deux cas.
# Un contrôle qu'on croit avoir est pire qu'un contrôle absent.

def test_la_portee_ALL_reste_honoree_sur_toutes_les_routes_bornees(
    client, deux_projets
) -> None:
    """`all` doit traverser le garde — sinon les liens partagés meurent.

    Les URL `?projet=all&reunion=X` viennent d'être livrées, et c'est la seule
    portée qui contient à coup sûr un document qu'on ne sait pas classer. Muter
    `if project_id is None: return True` en `return False` ne faisait rougir
    AUCUN des 160 tests d'intégration avant celui-ci.
    """
    nom = "client.md"
    reunion = deux_projets["meeting_id"]

    lectures = {
        f"/api/transcript/{nom}": 200,
        f"/api/transcript/{nom}/download": 200,
        f"/api/transcript/{nom}/archive-plan": 200,
        f"/api/meetings/{reunion}": 200,
    }
    for route, attendu in lectures.items():
        reponse = client.get(route, params={"view_scope": "all"})
        assert reponse.status_code == attendu, f"{route} → {reponse.status_code}"

    # Ancre de non-vacuité : ces mêmes routes REFUSENT bien depuis une autre
    # portée. Sans elle, un garde qui laisserait tout passer rendrait ce test
    # vert exactement de la même façon.
    for route in lectures:
        refus = client.get(route, params={"view_scope": deux_projets["inbox"].id})
        assert refus.status_code == 404, f"{route} accessible hors portée !"


def test_le_DOSSIER_fait_foi_et_non_le_front_matter(client, deux_projets) -> None:
    """Un `project_id` déclaré qui ment ne doit rien ouvrir.

    Le front-matter est DÉCLARATIF et voyage avec le fichier : un .md recopié
    depuis un autre projet emporte le `project_id` de son origine. S'en servir
    comme frontière rendrait le document visible depuis le projet qu'il
    prétend, pas depuis celui où il est réellement rangé.

    Muter le garde pour lire le front-matter au lieu du dossier ne faisait
    rougir aucun test avant celui-ci.
    """
    output = deux_projets["output"]
    inbox_id = deux_projets["inbox"].id
    document_id = docid.nouveau()

    # Le fichier est DANS `Client/`, mais son front-matter annonce la boîte de
    # réception. C'est exactement l'état qu'un copier-coller produit.
    (output / "Client").joinpath("menteur.md").write_text(
        f"---\nid: {document_id}\nmeeting_id: {docid.nouveau()}\n"
        f"project_id: {inbox_id}\ntitle: Menteur\nsource: menteur.mp3\n---\n\n"
        "Speaker 1: contenu du client\n",
        encoding="utf-8",
    )
    api_main.library.sync_file(output / "Client" / "menteur.md")

    # Depuis la portée qu'il DÉCLARE : refusé, parce qu'il n'y est pas.
    depuis_declaree = client.get(
        "/api/transcript/menteur.md", params={"view_scope": inbox_id})
    assert depuis_declaree.status_code == 404, (
        "le front-matter déclaré suffit à ouvrir le document — "
        "un .md recopié serait visible depuis son projet d'origine")

    # Depuis la portée où il EST rangé : accepté.
    depuis_reelle = client.get(
        "/api/transcript/menteur.md",
        params={"view_scope": deux_projets["client"].id})
    assert depuis_reelle.status_code == 200, depuis_reelle.text


# --- La composition : toute cible dérivée doit être dans la portée -----------

@pytest.fixture
def menteur_avec_cr(deux_projets):
    """Un transcript recopié dans « Client » qui DÉCLARE la boîte de réception,
    et son compte-rendu qui vit RÉELLEMENT dans la boîte de réception.

    C'est le cas que le garde de portée doit couvrir jusqu'aux cibles DÉRIVÉES
    (voir `api/main.py`, `_plan_dans_la_portee`), et pas seulement en lecture.
    """
    output = deux_projets["output"]
    inbox_id = deux_projets["inbox"].id
    source_id = docid.nouveau()
    meeting_id = docid.nouveau()

    (output / "Client").joinpath("source.md").write_text(
        f"---\nid: {source_id}\nmeeting_id: {meeting_id}\n"
        f"project_id: {inbox_id}\ntitle: Source\nsource: source.mp3\n---\n\n"
        "Speaker 1: contenu\n",
        encoding="utf-8",
    )
    (output).joinpath("report.md").write_text(
        f"---\nid: {docid.nouveau()}\nmeeting_id: {meeting_id}\n"
        f"project_id: {inbox_id}\ntitle: CR\ntype: report\n"
        f"source_document_id: {source_id}\nsource: source.mp3\n---\n\n# CR\n",
        encoding="utf-8",
    )
    api_main.library.sync_file(output / "Client" / "source.md")
    api_main.library.sync_file(output / "report.md")
    return {**deux_projets, "client_id": deux_projets["client"].id}


def test_le_PLAN_ne_divulgue_aucune_cible_hors_portee(client, menteur_avec_cr) -> None:
    """Le GET fuit déjà : il rend le nom et le titre du CR hors portée.

    Garder le seul document DEMANDÉ ne suffit pas : `archive_plan` dérive
    ses cibles du `project_id` DÉCLARÉ — donc du projet que le document
    prétend, pas de celui où il est.
    """
    portee = menteur_avec_cr["client_id"]
    reponse = client.get(
        "/api/transcript/source.md/archive-plan", params={"view_scope": portee})
    assert reponse.status_code == 200, reponse.text

    noms = [doc["name"] for doc in reponse.json()["documents"]]
    assert "source.md" in noms, noms
    assert "report.md" not in noms, (
        "le plan divulgue un document que cette portée ne peut pas voir")


def test_la_SUPPRESSION_ne_touche_aucune_cible_hors_portee(
    client, menteur_avec_cr
) -> None:
    """Et le DELETE, lui, ne divulgue pas : il détruit.

    Le CR vit réellement dans la boîte de réception. Une suppression demandée
    depuis « Client » ne doit pas l'emporter — l'utilisateur de Client n'a
    jamais eu le droit de le voir, il n'a pas celui de l'effacer.
    """
    output = menteur_avec_cr["output"]
    portee = menteur_avec_cr["client_id"]

    reponse = client.delete(
        "/api/transcript/source.md", params={"view_scope": portee})
    assert reponse.status_code == 200, reponse.text

    assert not (output / "Client" / "source.md").exists(), "la source n'a pas été archivée"
    assert (output / "report.md").exists(), (
        "un document HORS PORTÉE a été archivé par une action venue d'ailleurs")


def test_le_plan_ne_COMPTE_pas_non_plus_les_exclus(client, menteur_avec_cr) -> None:
    """Règle : pas de compte des exclus (voir `api/main.py`,
    `transcript_archive_plan`).

    Un compte des documents retirés voudrait « dire combien sans dire quoi ».
    Il est incohérent : ces routes refusent en 404 plutôt qu'en 403 précisément
    pour ne pas confirmer qu'un objet existe ailleurs. Un compte le confirme.

    L'anomalie reste visible là où elle appartient — `/api/maintenance`, du
    diagnostic — et non dans une réponse rendue au lecteur.
    """
    reponse = client.get(
        "/api/transcript/source.md/archive-plan",
        params={"view_scope": menteur_avec_cr["client_id"]})
    assert reponse.status_code == 200, reponse.text

    corps = reponse.json()
    assert "excluded" not in corps, (
        "le nombre d'exclus confirme l'existence de documents hors portée")
    assert set(corps) == {"requested", "documents"}, sorted(corps)
