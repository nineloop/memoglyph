from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from core import messages

from api import main as api_main
from api.jobs import JobManager
from core import docid, llm_base, projects, purge, resources, voiceprint
from tests.conftest import COMPTE_DE_TEST, compte_de_test


pytestmark = pytest.mark.integration


@pytest.fixture
def client(monkeypatch, tmp_path, isolated_stores):
    monkeypatch.setattr(api_main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(api_main, "jobs", JobManager())
    # Le bouchon `api_main.llm_ollama.is_available -> False` a disparu avec
    # Ollama, et il n'est REMPLACÉ PAR RIEN — délibérément.
    #
    # Il existait parce que `/api/config` faisait un GET réseau vers le démon
    # local à chaque appel. La route interroge désormais `pret_declare`,
    # qui ne fait aucun appel réseau : Mistral lit une clé de configuration,
    # MLX localise un paquet. Rien à isoler.
    #
    # Le remplacer par un bouchon de façade serait pire que l'absence : il
    # rendrait TOUS les moteurs indisponibles, et les contrôles de
    # disponibilité par fournisseur ne mesureraient plus que le bouchon.
    return TestClient(api_main.app)


# Attente maximale d'un travail de test : une seconde ne suffisait pas sur un runner
# chargé (échecs intermittents en CI). Un travail fini rend la main tout de suite.
ATTENTE_MAX_S = 10.0


def _wait_job(client: TestClient, job_id: str) -> dict:
    limite = time.monotonic() + ATTENTE_MAX_S
    while time.monotonic() < limite:
        payload = client.get(f"/api/jobs/{job_id}").json()
        if payload["status"] in {"done", "error"}:
            return payload
        time.sleep(0.005)
    raise AssertionError("le job n'a pas terminé")


def test_health_config_and_unknown_job(client, isolated_stores) -> None:
    # Le « Non classé » du compte, tel que sa création par l'API le donne : une lecture
    # ne le crée pas (`api.portee`).
    projects.personal_inbox(isolated_stores["output_dir"], COMPTE_DE_TEST)
    sante = client.get("/api/health").json()
    assert sante["status"] == "ok"
    # La santé porte le détail par composant. `None` signifie
    # « pas encore terminé » — le backfill sémantique est asynchrone — et c'est
    # une troisième valeur, distincte de « échoué ».
    assert set(sante["composants"]) == {"index", "voix", "semantique"}
    assert sante["details"] == {}
    config = client.get("/api/config").json()
    assert config["default_engine"] == "whisper"
    assert docid.est_valide(config["default_project_id"])
    assert config["max_upload_mb"] == api_main.config.MAX_UPLOAD_MB
    assert isinstance(config["llm_available"], bool)
    # Vocabulaire fermé de sensibilité, exposé pour l'UI (MÉTADONNÉE, pas de
    # routage) : trois niveaux stables + un défaut qui en fait partie.
    assert config["sensitivity_levels"] == [
        "ultra_confidential", "confidential", "standard",
    ]
    assert config["default_sensitivity"] in config["sensitivity_levels"]
    assert client.get("/api/jobs/missing").status_code == 404


def test_projects_have_stable_manifest_identities(client, isolated_stores) -> None:
    created = client.post("/api/projects", json={"name": "Produit"})

    assert created.status_code == 200
    payload = created.json()
    assert docid.est_valide(payload["id"])
    assert payload["project"] == "Produit"
    assert payload["kind"] == "regular"
    assert projects.load(isolated_stores["output_dir"] / "Produit").id == payload["id"]

    listed = client.get("/api/projects").json()
    assert [(item["project"], item["kind"]) for item in listed] == [
        ("Non classé", "inbox"), ("Produit", "regular"),
    ]
    assert docid.est_valide(listed[0]["id"])


def test_library_view_scope_is_safe_explicit_and_id_based(client, isolated_stores) -> None:
    output = isolated_stores["output_dir"]
    inbox = projects.create(output, name=projects.INBOX_NAME, kind="inbox", owner=COMPTE_DE_TEST)
    client_project = projects.create(output / "Client", name="Client")
    for directory, name, project_id in (
        (output, "inbox.md", inbox.id),
        (output / "Client", "client.md", client_project.id),
    ):
        directory.mkdir(parents=True, exist_ok=True)
        directory.joinpath(name).write_text(
            f"---\nid: {docid.nouveau()}\nmeeting_id: {docid.nouveau()}\n"
            f"project_id: {project_id}\ntitle: {name}\n---\n\nTexte\n",
            encoding="utf-8",
        )
    api_main.library.reindex()

    assert [item["name"] for item in client.get("/api/library").json()] == ["inbox.md"]
    assert {item["name"] for item in client.get(
        "/api/library", params={"view_scope": "all"},
    ).json()} == {"inbox.md", "client.md"}
    assert [item["name"] for item in client.get(
        "/api/library", params={"view_scope": client_project.id},
    ).json()] == ["client.md"]
    # Hors de la portée du compte, ou inconnu : 404, comme un document.
    assert client.get("/api/library", params={"view_scope": "Client"}).status_code == 404


def test_transcribe_destination_is_a_project_id_never_all(
    client, isolated_stores, monkeypatch,
) -> None:
    output = isolated_stores["output_dir"]
    inbox = projects.create(output, name=projects.INBOX_NAME, kind="inbox", owner=COMPTE_DE_TEST)
    client_project = projects.create(output / "Client", name="Client")
    destinations = []

    def run(path, **options):
        destinations.append(options["output_dir"])
        out = options["output_dir"] / f"result-{len(destinations)}.md"
        out.write_text(
            f"---\nid: {docid.nouveau()}\nmeeting_id: {docid.nouveau()}\n"
            f"project_id: {projects.load(options['output_dir']).id}\ntitle: result\n---\n\nTexte",
            encoding="utf-8",
        )
        return out, "contenu"

    monkeypatch.setattr(api_main.pipeline, "run", run)
    monkeypatch.setattr(api_main.semantic, "index_name", lambda name: 0)
    monkeypatch.setattr(api_main.voiceprint, "index_name", lambda name: {})

    default = client.post("/api/transcribe", files={"file": ("a.mp3", b"a")})
    assert _wait_job(client, default.json()["job_id"])["status"] == "done"
    explicit = client.post(
        "/api/transcribe",
        files={"file": ("b.mp3", b"b")},
        data={"destination_project_id": client_project.id},
    )
    assert _wait_job(client, explicit.json()["job_id"])["status"] == "done"
    refused = client.post(
        "/api/transcribe",
        files={"file": ("c.mp3", b"c")},
        data={"destination_project_id": "all"},
    )

    assert destinations == [output, output / "Client"]
    assert projects.load(destinations[0]).id == inbox.id
    assert refused.status_code == 400


def test_transcribe_sanitizes_filename_streams_result_and_cleans_upload(
    client, monkeypatch
) -> None:
    received = {}

    def run(path, **options):
        received.update(path=path, options=options)
        options["progress"](0.5, "En cours")
        out = api_main.library.OUTPUT_DIR / "result.md"
        out.write_text("---\ntitle: result\n---\n\nTexte", encoding="utf-8")
        return out, "contenu"

    monkeypatch.setattr(api_main.pipeline, "run", run)
    monkeypatch.setattr(api_main.semantic, "index_name", lambda name: 0)
    monkeypatch.setattr(api_main.voiceprint, "index_name", lambda name: {})

    response = client.post(
        "/api/transcribe",
        files={"file": ("../../escape.mp3", b"audio", "audio/mpeg")},
        data={"engine": "whisper", "language": "fr", "timestamps": "true"},
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    payload = _wait_job(client, job_id)

    assert payload["status"] == "done"
    assert payload["result"] == {"name": "result.md", "content": "contenu"}
    assert received["path"].name == "escape.mp3"
    assert received["path"].parent.parent == api_main.UPLOAD_DIR
    assert received["options"]["language"] == "fr"
    assert not received["path"].exists()

    with client.stream("GET", f"/api/jobs/{job_id}/events") as stream:
        body = "".join(stream.iter_text())
    assert '"type": "progress"' in body
    assert '"type": "done"' in body
    assert "id: 1" in body


def test_job_events_resume_after_last_id_and_keep_the_connection_alive(
    client, monkeypatch,
) -> None:
    monkeypatch.setattr(api_main, "SSE_HEARTBEAT_SECONDS", 0.0)
    job = api_main.jobs.create(owner=compte_de_test().id)
    first_sent = threading.Event()
    release = threading.Event()

    def target(progress):
        progress(0.1, "déjà reçu")
        first_sent.set()
        assert release.wait(timeout=ATTENTE_MAX_S)
        progress(0.5, "à rejouer")
        return {"name": "result.md", "content": "fini"}

    api_main.jobs.run(job, target)
    assert first_sent.wait(timeout=ATTENTE_MAX_S)

    # Le travail reprend quand le flux le trouve en cours sans rien de neuf : ce tour-là
    # émet un battement. Une minuterie le relâchait parfois avant que le flux, lent à
    # démarrer sur une machine chargée, ait eu à attendre : aucun battement n'était émis.
    events_after = api_main.jobs.events_after

    def puis_relacher(job_, cursor):
        new, status = events_after(job_, cursor)
        if not new and status not in ("done", "error"):
            release.set()
        return new, status
    monkeypatch.setattr(api_main.jobs, "events_after", puis_relacher)

    with client.stream(
        "GET",
        f"/api/jobs/{job.id}/events",
        headers={"Last-Event-ID": "1"},
    ) as stream:
        body = "".join(stream.iter_text())

    assert ": heartbeat" in body
    assert "déjà reçu" not in body
    assert "id: 2" in body and "à rejouer" in body
    assert "id: 3" in body and '"type": "done"' in body


def test_job_events_reject_an_invalid_resume_cursor(client) -> None:
    job = api_main.jobs.create(owner=compte_de_test().id)
    response = client.get(
        f"/api/jobs/{job.id}/events",
        headers={"Last-Event-ID": "pas-un-nombre"},
    )
    assert response.status_code == 400


def test_transcription_and_its_post_indexing_share_one_heavy_lock(
    client, isolated_stores, monkeypatch,
) -> None:
    output = isolated_stores["output_dir"]
    states: list[tuple[str, bool]] = []

    def run(_path, **options):
        states.append(("pipeline", resources.busy()))
        out = options["output_dir"] / "locked.md"
        out.write_text(
            f"---\nid: {docid.nouveau()}\nmeeting_id: {docid.nouveau()}\n"
            f"project_id: {projects.load(output).id}\ntitle: locked\n---\n\nTexte",
            encoding="utf-8",
        )
        return out, "contenu"

    monkeypatch.setattr(api_main.pipeline, "run", run)
    monkeypatch.setattr(
        api_main.semantic, "index_name",
        lambda _name: states.append(("semantic", resources.busy())) or 1,
    )
    monkeypatch.setattr(
        api_main.voiceprint, "index_name",
        lambda _name: states.append(("voices", resources.busy())) or {},
    )

    response = client.post("/api/transcribe", files={"file": ("a.mp3", b"a")})
    assert _wait_job(client, response.json()["job_id"])["status"] == "done"
    assert states == [("pipeline", True), ("semantic", True), ("voices", True)]


def test_transcribe_job_reports_pipeline_error_and_cleans_upload(client, monkeypatch) -> None:
    received = {}

    def fail(path, **options):
        received["path"] = path
        raise RuntimeError("pipeline cassé")

    monkeypatch.setattr(api_main.pipeline, "run", fail)

    response = client.post(
        "/api/transcribe",
        files={"file": ("broken.mp3", b"audio", "audio/mpeg")},
    )
    payload = _wait_job(client, response.json()["job_id"])

    assert payload["status"] == "error"
    assert payload["error"] == "pipeline cassé"
    assert not received["path"].exists()


def test_library_transcript_download_and_archive(client, isolated_stores) -> None:
    output = isolated_stores["output_dir"]
    inbox = projects.create(output, name=projects.INBOX_NAME, kind="inbox", owner=COMPTE_DE_TEST)
    path = output / "note.md"
    path.write_text(
        f"---\nid: {docid.nouveau()}\nmeeting_id: {docid.nouveau()}\n"
        f"project_id: {inbox.id}\ntitle: Note\nlanguage: fr\n---\n\nCorps",
        encoding="utf-8",
    )
    api_main.library.sync_file(path)

    # `view_scope` est désormais OBLIGATOIRE sur les routes adressées par
    # identifiant : le dire ici documente la règle au lieu de la contourner.
    portee = {"view_scope": inbox.id}
    assert client.get("/api/library").json()[0]["name"] == "note.md"
    assert client.get(
        "/api/transcript/note.md", params=portee,
    ).json()["body"] == "Corps"
    download = client.get("/api/transcript/note.md/download", params=portee)
    assert download.status_code == 200
    assert download.text.endswith("Corps")

    archived = client.delete("/api/transcript/note.md", params=portee)
    assert archived.json() == {"archived": "note.md", "documents": ["note.md"]}
    assert client.get("/api/transcript/note.md", params=portee).status_code == 404
    assert client.delete("/api/transcript/note.md", params=portee).status_code == 404


def test_sensitivity_patch_round_trips_et_refuse_l_inconnu(client, isolated_stores) -> None:
    output = isolated_stores["output_dir"]
    inbox = projects.create(output, name=projects.INBOX_NAME, kind="inbox", owner=COMPTE_DE_TEST)
    path = output / "note.md"
    path.write_text(
        f"---\nid: {docid.nouveau()}\nmeeting_id: {docid.nouveau()}\n"
        f"project_id: {inbox.id}\ntitle: Note\nlanguage: fr\n"
        "sensitivity: standard\n---\n\nCorps",
        encoding="utf-8",
    )
    api_main.library.sync_file(path)
    portee = {"view_scope": inbox.id}

    # Le transcript expose sa sensibilité dans `meta` (front-matter tel quel).
    assert client.get(
        "/api/transcript/note.md", params=portee,
    ).json()["meta"]["sensitivity"] == "standard"

    # Aller-retour : on la passe à « confidential ».
    patched = client.patch(
        "/api/transcript/note.md/sensitivity",
        params=portee, json={"sensitivity": "confidential"},
    )
    assert patched.json() == {"name": "note.md", "sensitivity": "confidential"}
    # Le niveau est celui de la réunion (registre), lu comme `sensitivity_effective` ; le
    # `.md` n'est pas réécrit.
    assert client.get(
        "/api/transcript/note.md", params=portee,
    ).json()["sensitivity_effective"] == "confidential"

    # Valeur inconnue : refusée (422), le document garde sa sensibilité. La ramener au
    # défaut rétrogradait un ultra-confidentiel sur une faute de frappe (T2) ;
    # la lecture reste douce.
    coerce = client.patch(
        "/api/transcript/note.md/sensitivity",
        params=portee, json={"sensitivity": "bogus"},
    )
    assert coerce.status_code == 422
    assert client.get(
        "/api/transcript/note.md", params=portee,
    ).json()["sensitivity_effective"] == "confidential"

    # Document absent → 404.
    assert client.patch(
        "/api/transcript/absent.md/sensitivity",
        params=portee, json={"sensitivity": "confidential"},
    ).status_code == 404


def test_search_and_ask_validation_and_service_errors(
    client, isolated_stores, monkeypatch,
) -> None:
    inbox = projects.create(
        isolated_stores["output_dir"], name=projects.INBOX_NAME, kind="inbox",
        owner=COMPTE_DE_TEST,
    )
    scope = {"mode": "project", "project_id": inbox.id}
    assert client.get("/api/search", params={"q": ""}).json() == []
    assert client.post(
        "/api/ask", json={"question": "   ", "scope": scope},
    ).status_code == 400

    from core import rag

    monkeypatch.setattr(
        rag,
        "ask",
        lambda question, **kw: (_ for _ in ()).throw(llm_base.LLMError("offline")),
    )
    response = client.post(
        "/api/ask", json={"question": "Une question", "scope": scope},
    )
    assert response.status_code == 503
    assert "offline" in response.text


def test_ask_requires_structured_scope_and_all_is_explicit(
    client, isolated_stores, monkeypatch,
) -> None:
    from core import rag

    inbox = projects.create(
        isolated_stores["output_dir"], name=projects.INBOX_NAME, kind="inbox",
        owner=COMPTE_DE_TEST,
    )
    received = []
    monkeypatch.setattr(
        rag, "ask",
        lambda question, **options: received.append(options.get("project_id"))
        or {"answer": "ok", "sources": []},
    )

    assert client.post("/api/ask", json={"question": "Question"}).status_code == 422
    assert client.post(
        "/api/ask",
        json={
            "question": "Question",
            "scope": {"mode": "project", "project_id": inbox.id},
        },
    ).status_code == 200
    assert client.post(
        "/api/ask", json={"question": "Question", "scope": {"mode": "all"}},
    ).status_code == 200
    assert client.post(
        "/api/ask",
        json={"question": "Question", "scope": {"mode": "project", "project_id": "inconnu"}},
    ).status_code == 404
    # `all` = tous les projets du compte, jamais le corpus entier.
    assert received == [inbox.id, frozenset({inbox.id})]


def test_l_assistant_montre_dans_l_extrait_le_chiffre_qu_il_cite(
    client, isolated_stores, monkeypatch,
) -> None:
    # Constat 7 du parcours : l'extrait gardait les 220 premiers caractères du passage,
    # et le prix cité par la réponse tombait après ; le vérifier obligeait à ouvrir la source.
    from core import llm, rag, semantic

    inbox = projects.create(
        isolated_stores["output_dir"], name=projects.INBOX_NAME, kind="inbox",
        owner=COMPTE_DE_TEST,
    )
    passage = ("Okay so we talked about the design and the buttons, and the marketing people said "
               "that the target group is young people who like trendy things and who want a simple "
               "remote. Then the finance part: after a long discussion of every component we "
               "settled on the selling price, which is 25 euro for the remote.")
    monkeypatch.setattr(semantic, "retrieve", lambda q, **k: [{
        "name": "is1009a.md", "title": "IS1009a", "ts": "00:06:13", "text": passage,
        "rang": 1, "project": "Non classé"}])
    monkeypatch.setattr(llm, "is_available", lambda **k: True)
    monkeypatch.setattr(llm, "chat", lambda system, prompt, **k:
                        "The selling price decided for the remote control was 25 euro [1].")

    corps = client.post("/api/ask", json={
        "question": "How much should the remote cost?", "llm_provider": "mlx",
        "scope": {"mode": "project", "project_id": inbox.id}}).json()

    (source,) = corps["sources"]
    assert passage.index("25 euro") > rag.EXTRAIT_MAX
    assert "25 euro" in source["excerpt"]
    assert len(source["excerpt"]) <= rag.EXTRAIT_MAX + 2


def test_meeting_routes_return_stable_parent_with_all_children(
    client, isolated_stores,
) -> None:
    output = isolated_stores["output_dir"]
    inbox = projects.create(output, name=projects.INBOX_NAME, kind="inbox", owner=COMPTE_DE_TEST)
    meeting_id = docid.nouveau()
    transcript_id = docid.nouveau()
    for name, document_id, extra in (
        ("meeting.md", transcript_id, "diarization: true\n"),
        (
            "meeting-cr.md", docid.nouveau(),
            f"type: report\nsource_document_id: {transcript_id}\nsource_transcript: meeting.md\n",
        ),
    ):
        (output / name).write_text(
            f"---\nid: {document_id}\nmeeting_id: {meeting_id}\nproject_id: {inbox.id}\n"
            f"{extra}title: {name}\n---\n\nCorps\n",
            encoding="utf-8",
        )
    api_main.library.reindex()

    listed = client.get("/api/meetings", params={"diarized": "true"})
    # `view_scope` est désormais OBLIGATOIRE sur les routes adressées par
    # identifiant : le dire ici documente la règle au lieu de la contourner.
    detail = client.get(f"/api/meetings/{meeting_id}", params={"view_scope": inbox.id})

    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [meeting_id]
    assert [item["name"] for item in listed.json()[0]["reports"]] == ["meeting-cr.md"]
    assert detail.status_code == 200
    assert detail.json()["preferred_transcript"]["id"] == transcript_id
    assert client.get(
        f"/api/meetings/{docid.nouveau()}", params={"view_scope": inbox.id},
    ).status_code == 404


def test_meeting_routes_move_and_archive_the_whole_parent(
    client, isolated_stores,
) -> None:
    output = isolated_stores["output_dir"]
    inbox = projects.create(output, name=projects.INBOX_NAME, kind="inbox", owner=COMPTE_DE_TEST)
    target = projects.create(output / "Client", name="Client")
    meeting_id = docid.nouveau()
    transcript_id = docid.nouveau()
    for name, document_id, extra in (
        ("meeting.md", transcript_id, ""),
        (
            "meeting-cr.md", docid.nouveau(),
            f"type: report\nsource_document_id: {transcript_id}\nsource_transcript: meeting.md\n",
        ),
    ):
        (output / name).write_text(
            f"---\nid: {document_id}\nmeeting_id: {meeting_id}\nproject_id: {inbox.id}\n"
            f"{extra}title: {name}\n---\n\nCorps\n",
            encoding="utf-8",
        )
    api_main.library.reindex()

    # Le déplacement part de la portée SOURCE (la réunion est encore dans la
    # boîte de réception) ; l'archivage, lui, devra viser la portée d'ARRIVÉE.
    moved = client.patch(
        f"/api/meetings/{meeting_id}/project",
        params={"view_scope": inbox.id},
        json={"destination_project_id": target.id},
    )

    assert moved.status_code == 200
    assert moved.json()["project_id"] == target.id
    assert {path.name for path in (output / "Client").glob("*.md")} == {
        "meeting.md", "meeting-cr.md",
    }

    # La réunion vit maintenant dans « Client » : la supprimer depuis la portée
    # de la boîte de réception n'a plus de sens et rendrait 404. Passer la portée
    # d'arrivée DOCUMENTE la règle ; l'omettre la trahirait.
    archived = client.delete(
        f"/api/meetings/{meeting_id}", params={"view_scope": target.id},
    )

    assert archived.status_code == 200
    assert archived.json() == {
        "archived_meeting": meeting_id,
        "documents": ["meeting-cr.md", "meeting.md"],
    }
    assert api_main.library.get_meeting(meeting_id) is None
    assert {item["origin"] for item in api_main.library.trash_items()} == {
        "meeting.md", "meeting-cr.md",
    }


def test_duplicate_archive_plan_cascades_only_linked_reports(
    client, isolated_stores,
) -> None:
    output = isolated_stores["output_dir"]
    inbox = projects.create(output, name=projects.INBOX_NAME, kind="inbox", owner=COMPTE_DE_TEST)
    meeting_id = docid.nouveau()
    source_id = docid.nouveau()
    kept_id = docid.nouveau()
    documents = (
        ("source.md", source_id, ""),
        ("kept.md", kept_id, ""),
        (
            "source-cr.md", docid.nouveau(),
            f"type: report\nsource_document_id: {source_id}\n"
            "source_transcript: source.md\n",
        ),
        (
            "kept-cr.md", docid.nouveau(),
            f"type: report\nsource_document_id: {kept_id}\n"
            "source_transcript: kept.md\n",
        ),
    )
    for name, document_id, extra in documents:
        (output / name).write_text(
            f"---\nid: {document_id}\nmeeting_id: {meeting_id}\n"
            f"project_id: {inbox.id}\n{extra}title: {name}\n---\n\nBody\n",
            encoding="utf-8",
        )
    api_main.library.reindex()

    plan = client.get(
        "/api/transcript/source.md/archive-plan",
        params={"view_scope": inbox.id},
    )
    archived = client.delete(
        "/api/transcript/source.md",
        params={"view_scope": inbox.id},
    )

    assert plan.status_code == 200
    assert [item["name"] for item in plan.json()["documents"]] == [
        "source-cr.md", "source.md",
    ]
    assert archived.status_code == 200
    assert archived.json()["documents"] == ["source-cr.md", "source.md"]
    assert api_main.library.get_transcript("source.md") is None
    assert api_main.library.get_transcript("source-cr.md") is None
    assert api_main.library.get_transcript("kept.md") is not None
    assert api_main.library.get_transcript("kept-cr.md") is not None


def test_trash_routes_are_scoped_restorable_and_individually_purgeable(
    client, isolated_stores, monkeypatch,
) -> None:
    output = isolated_stores["output_dir"]
    inbox = projects.create(output, name=projects.INBOX_NAME, kind="inbox", owner=COMPTE_DE_TEST)
    target = projects.create(output / "Client", name="Client")
    for directory, name, project_id in (
        (output, "inbox.md", inbox.id),
        (output / "Client", "client.md", target.id),
    ):
        directory.mkdir(parents=True, exist_ok=True)
        directory.joinpath(name).write_text(
            f"---\nid: {docid.nouveau()}\nmeeting_id: {docid.nouveau()}\n"
            f"project_id: {project_id}\ntitle: {name}\n---\n\nBody\n",
            encoding="utf-8",
        )
    api_main.library.reindex()
    monkeypatch.setattr(api_main.semantic, "index_name", lambda name: 1)
    monkeypatch.setattr(api_main.voiceprint, "index_name", lambda name: {})
    out_of_scope = client.delete(
        "/api/transcript/client.md",
        params={"hard": True, "view_scope": inbox.id},
    )
    assert out_of_scope.status_code == 404
    assert api_main.library.get_transcript("client.md") is not None
    assert client.delete(
        "/api/transcript/inbox.md", params={"view_scope": inbox.id},
    ).status_code == 200
    assert client.delete(
        "/api/transcript/client.md", params={"view_scope": target.id},
    ).status_code == 200

    scoped = client.get("/api/trash", params={"view_scope": inbox.id})
    assert [item["origin"] for item in scoped.json()] == ["inbox.md"]
    inbox_archived = scoped.json()[0]["name"]
    assert client.post(
        f"/api/trash/{inbox_archived}/restore",
        params={"view_scope": target.id},
    ).status_code == 404

    restored = client.post(
        f"/api/trash/{inbox_archived}/restore",
        params={"view_scope": inbox.id},
    )
    assert restored.status_code == 200
    assert restored.json()["restored"] == "inbox.md"
    assert restored.json()["warnings"] == []

    assert client.delete(
        "/api/transcript/inbox.md", params={"view_scope": inbox.id},
    ).status_code == 200
    inbox_archived = client.get(
        "/api/trash", params={"view_scope": inbox.id},
    ).json()[0]["name"]
    deleted = client.delete(
        f"/api/trash/{inbox_archived}", params={"view_scope": inbox.id},
    )
    assert deleted.status_code == 200
    assert deleted.json()["clean"] is True
    assert purge.is_clean("inbox.md")
    assert [item["origin"] for item in client.get(
        "/api/trash", params={"view_scope": "all"},
    ).json()] == ["client.md"]


def test_empty_trash_only_purges_the_explicit_scope(
    client, isolated_stores,
) -> None:
    output = isolated_stores["output_dir"]
    inbox = projects.create(output, name=projects.INBOX_NAME, kind="inbox", owner=COMPTE_DE_TEST)
    target = projects.create(output / "Client", name="Client")
    for directory, name, project_id in (
        (output, "inbox.md", inbox.id),
        (output / "Client", "client.md", target.id),
    ):
        directory.mkdir(parents=True, exist_ok=True)
        directory.joinpath(name).write_text(
            f"---\nid: {docid.nouveau()}\nmeeting_id: {docid.nouveau()}\n"
            f"project_id: {project_id}\ntitle: {name}\n---\n\nBody\n",
            encoding="utf-8",
        )
    api_main.library.reindex()
    client.delete("/api/transcript/inbox.md", params={"view_scope": inbox.id})
    client.delete("/api/transcript/client.md", params={"view_scope": target.id})

    emptied = client.delete("/api/trash", params={"view_scope": inbox.id})

    assert emptied.status_code == 200
    assert emptied.json()["removed"] == 1
    assert [item["origin"] for item in client.get(
        "/api/trash", params={"view_scope": "all"},
    ).json()] == ["client.md"]


def test_voice_and_maintenance_routes_are_project_scoped(
    client, isolated_stores, monkeypatch,
) -> None:
    output = isolated_stores["output_dir"]
    inbox = projects.create(output, name=projects.INBOX_NAME, kind="inbox", owner=COMPTE_DE_TEST)
    target = projects.create(output / "Client", name="Client")
    ids = [docid.nouveau(), docid.nouveau()]
    for directory, name, document_id, project_id in (
        (output, "inbox.md", ids[0], inbox.id),
        (output / "Client", "client.md", ids[1], target.id),
    ):
        directory.mkdir(parents=True, exist_ok=True)
        directory.joinpath(name).write_text(
            f"---\nid: {document_id}\nmeeting_id: {docid.nouveau()}\n"
            f"project_id: {project_id}\ntitle: {name}\n"
            "diarization: true\n---\n\nTexte",
            encoding="utf-8",
        )
    api_main.library.reindex()
    inbox_voice = voiceprint.index_file(
        ids[0], {"Speaker 1": [1.0] + [0.0] * 255}, name="inbox.md",
    )["Speaker 1"]
    client_voice = voiceprint.index_file(
        ids[1], {"Speaker 1": [0.0, 1.0] + [0.0] * 254}, name="client.md",
    )["Speaker 1"]

    default_voices = client.get("/api/voices").json()
    global_voices = client.get("/api/voices", params={"view_scope": "all"}).json()
    assert [item["id"] for item in default_voices] == [inbox_voice]
    assert {item["id"] for item in global_voices} == {inbox_voice, client_voice}
    assert default_voices[0]["occurrences"][0]["project_id"] == inbox.id

    refused = client.patch(
        f"/api/voices/{client_voice}", params={"view_scope": inbox.id},
        json={"label": "Intruse"},
    )
    assert refused.status_code == 404
    assert client.patch(
        f"/api/voices/{inbox_voice}", params={"view_scope": inbox.id},
        json={"label": "Alice"},
    ).status_code == 200
    assert client.post(
        f"/api/voices/{inbox_voice}/merge", params={"view_scope": inbox.id},
        json={"target_id": client_voice},
    ).status_code == 404
    assert client.delete(
        "/api/transcript/client.md", params={"view_scope": inbox.id},
    ).status_code == 404
    assert (output / "Client" / "client.md").exists()

    status = client.get("/api/maintenance", params={"view_scope": inbox.id}).json()
    assert status["documents"] == 1
    assert status["diarized_documents"] == 1
    assert status["voice_documents"] == 1
    assert status["voice_occurrences"] == 1
    assert status["faithful_backfill_eligible"] == []
    assert status["faithful_backfill_missing_originals"] == ["inbox.md"]
    # Un nom n'est pas une identité ; hors de la portée : 404.
    assert client.get("/api/maintenance", params={"view_scope": "Client"}).status_code == 404

    seen = []
    monkeypatch.setattr(
        api_main.voiceprint, "reindex_all",
        lambda *, project_id=None: seen.append(project_id) or {"indexed": 1},
    )
    rebuilt = client.post(
        "/api/maintenance/reindex/voices", params={"view_scope": inbox.id},
    )
    assert rebuilt.json() == {"indexed": 1}
    assert seen == [inbox.id]

    from core import backfill
    eligible_scopes = []
    monkeypatch.setattr(
        backfill, "eligible",
        lambda project_id=None: eligible_scopes.append(project_id) or [],
    )
    assert client.get(
        "/api/voices/backfill/preview", params={"view_scope": inbox.id},
    ).json() == {"eligible": []}
    assert eligible_scopes == [inbox.id]


def test_faithful_backfill_job_conserve_la_portee_projet(
    client, isolated_stores, monkeypatch,
) -> None:
    inbox = projects.create(
        isolated_stores["output_dir"], name=projects.INBOX_NAME, kind="inbox",
        owner=COMPTE_DE_TEST,
    )
    seen = []

    def run(**options):
        seen.append(options)
        options["progress"](0.5, "Vérification PCM")
        return {"done": 1, "errors": 0, "results": []}

    monkeypatch.setattr(api_main.faithful_backfill, "run", run)

    response = client.post(
        "/api/maintenance/backfill/faithful",
        params={"view_scope": inbox.id},
    )
    assert response.status_code == 200
    payload = _wait_job(client, response.json()["job_id"])

    assert payload["status"] == "done"
    assert payload["result"]["done"] == 1
    assert seen[0]["root"] == isolated_stores["output_dir"]
    assert seen[0]["project_id"] == inbox.id


@pytest.mark.parametrize(
    "path",
    [
        "/api/search/reindex",
        "/api/voices/reindex",
        "/api/maintenance/reindex/voices",
    ],
)
def test_reindex_routes_report_resource_contention_as_429(client, path) -> None:
    with resources.heavy(label="occupant"):
        response = client.post(path)
    assert response.status_code == 429
    assert "occup" in response.json()["detail"].lower()


def test_un_index_derive_en_echec_est_signale_pas_avale(client, monkeypatch) -> None:
    """L'index sémantique passe TOUJOURS par Ollama (semantic.py appelle
    llm_ollama.embed sans routage par fournisseur). En mode Mistral Cloud sans
    Ollama, la transcription réussissait puis l'indexation échouait — avalée par
    un `pass` nu. « Recherche par le sens » et l'Assistant restaient alors muets
    sans que rien ne l'explique.

    Le Markdown fait foi et doit survivre à cet échec ; l'échec, lui, doit être DIT.
    """
    def run(path, **options):
        options["progress"](0.5, "En cours")
        out = api_main.library.OUTPUT_DIR / "result.md"
        out.write_text("---\ntitle: result\n---\n\nTexte", encoding="utf-8")
        return out, "contenu"

    def ollama_absent(name):
        raise RuntimeError("Ollama injoignable")

    monkeypatch.setattr(api_main.pipeline, "run", run)
    monkeypatch.setattr(api_main.semantic, "index_name", ollama_absent)
    monkeypatch.setattr(api_main.voiceprint, "index_name", lambda name: {})

    response = client.post(
        "/api/transcribe",
        files={"file": ("reunion.mp3", b"audio", "audio/mpeg")},
        data={"engine": "mistral", "llm_provider": "mistral", "timestamps": "true"},
    )
    job_id = response.json()["job_id"]
    payload = _wait_job(client, job_id)

    # Le transcript est bien produit : un index dérivé ne doit pas le perdre.
    assert payload["status"] == "done"
    assert payload["result"]["name"] == "result.md"
    # …mais l'échec est explicite, et nomme ce qui ne marchera pas.
    avertissements = " ".join(payload["result"].get("warnings") or [])
    assert "recherche par le sens" in avertissements
    assert "Ollama injoignable" in avertissements


def test_aucun_avertissement_quand_tout_reussit(client, monkeypatch) -> None:
    """Pas de bruit inutile : la clé n'apparaît que s'il y a quelque chose à dire."""
    def run(path, **options):
        out = api_main.library.OUTPUT_DIR / "result.md"
        out.write_text("---\ntitle: result\n---\n\nTexte", encoding="utf-8")
        return out, "contenu"

    monkeypatch.setattr(api_main.pipeline, "run", run)
    monkeypatch.setattr(api_main.semantic, "index_name", lambda name: 0)
    monkeypatch.setattr(api_main.voiceprint, "index_name", lambda name: {})

    response = client.post(
        "/api/transcribe",
        files={"file": ("reunion.mp3", b"audio", "audio/mpeg")},
        data={"engine": "whisper", "timestamps": "true"},
    )
    payload = _wait_job(client, response.json()["job_id"])
    assert "warnings" not in payload["result"]


def test_le_compte_rendu_reserve_la_ressource_lourde(
    client, isolated_stores, monkeypatch,
) -> None:
    """Le Map-Reduce d'un CR est un travail LOURD. Sans réservation il tournait
    en concurrence d'une transcription, les deux se disputant la RAM unifiée.
    L'endpoint attrapait déjà `ResourcesBusy` — mais ne prenait jamais le verrou."""
    from core import resources

    # Le transcript est désormais RÉEL et dans la portée : le garde de portée
    # passe avant la réservation — réserver la machine pour un travail qu'on
    # refuse ensuite serait absurde. Le test doit donc emprunter le chemin qui
    # mène vraiment au travail lourd, sinon il ne mesure plus rien.
    output = isolated_stores["output_dir"]
    inbox = projects.create(output, name=projects.INBOX_NAME, kind="inbox", owner=COMPTE_DE_TEST)
    (output / "note.md").write_text(
        f"---\nid: {docid.nouveau()}\nmeeting_id: {docid.nouveau()}\n"
        # Horodaté : un transcript sans horodatage est refusé (400) avant même
        # la réservation, puisqu'un CR n'y pourrait rien citer.
        f"project_id: {inbox.id}\ntitle: Note\n---\n\n## Transcript\n\n[00:00:01] Corps\n",
        encoding="utf-8",
    )
    api_main.library.reindex()

    appels: list[str] = []
    reel = resources.heavy

    def trace(*, wait=0.0, label="traitement"):
        appels.append(label)
        return reel(wait=wait, label=label)

    monkeypatch.setattr(api_main.resources, "heavy", trace)
    monkeypatch.setattr(
        "core.report.generate_report", lambda name, **kw: {"name": f"{name}-cr.md"},
    )

    client.post("/api/transcript/note.md/report", params={"view_scope": inbox.id})

    assert "compte-rendu" in appels, (
        "l'endpoint /report doit réserver un créneau de calcul lourd"
    )


def test_le_diagnostic_signale_un_corpus_qui_bloque_la_reindexation(
    client, monkeypatch,
) -> None:
    """`library.reindex()` est tout-ou-rien : un seul .md invalide laisse le
    corpus entier non réindexé. C'est un bon choix — un index à moitié
    reconstruit serait pire — mais l'échec ne partait qu'en `print` au
    démarrage. L'application servait ensuite un index figé sans le dire."""
    from core import maintenance

    # On simule le VERDICT plutôt que d'écrire un .md invalide : un fichier
    # corrompu à la racine fait échouer `ensure_inbox` en amont et l'endpoint
    # répond 409 avant tout diagnostic. Le scan lui-même est couvert par les
    # tests de `library.audit_corpus`.
    monkeypatch.setattr(
        maintenance, "_etat_corpus",
        lambda: {"ok": False, "issues": {"invalid_yaml": ["casse.md"]}},
    )

    corpus = client.get("/api/maintenance").json()["corpus"]

    assert corpus["ok"] is False
    assert corpus["issues"]["invalid_yaml"] == ["casse.md"], (
        "le diagnostic doit NOMMER le fichier en cause, pas seulement compter"
    )


def test_un_echec_de_reindexation_au_demarrage_reste_visible(client) -> None:
    """L'échec ne doit pas s'évaporer après le démarrage : tant que l'index est
    figé, l'interface doit pouvoir le dire."""
    api_main.ETAT_DEMARRAGE["index_ok"] = False
    api_main.ETAT_DEMARRAGE["detail"] = "base verrouillée"
    try:
        corpus = client.get("/api/maintenance").json()["corpus"]
        assert corpus["reindexation_demarrage_ok"] is False
        assert "verrouillée" in corpus["reindexation_demarrage_detail"]
    finally:
        api_main.ETAT_DEMARRAGE["index_ok"] = True
        api_main.ETAT_DEMARRAGE["detail"] = ""


def test_un_corpus_sain_ne_declenche_aucune_alerte(client) -> None:
    """Pas de faux positif : l'alerte doit rester silencieuse quand tout va bien."""
    corpus = client.get("/api/maintenance").json()["corpus"]
    assert corpus["ok"] is True
    assert corpus["issues"] == {}
    assert corpus["reindexation_demarrage_ok"] is True


def test_le_diagnostic_survit_a_un_markdown_corrompu(client, monkeypatch) -> None:
    """Un diagnostic ne doit jamais mourir de ce qu'il diagnostique.

    `faithful_backfill.prepare` relit les .md et lève sur un fichier illisible ;
    l'écran de maintenance partait alors en 500 — précisément quand
    l'utilisateur vient y chercher la cause du blocage.
    """
    from core import faithful_backfill, maintenance

    def illisible(*args, **kwargs):
        raise faithful_backfill.FaithfulBackfillError("Markdown illisible : casse.md")

    monkeypatch.setattr(maintenance.faithful_backfill, "prepare", illisible)
    monkeypatch.setattr(
        maintenance, "_etat_corpus",
        lambda: {"ok": False, "issues": {"invalid_yaml": ["casse.md"]}},
    )

    reponse = client.get("/api/maintenance")

    assert reponse.status_code == 200, "l'écran de diagnostic doit rester accessible"
    corps = reponse.json()
    assert corps["corpus"]["issues"]["invalid_yaml"] == ["casse.md"]
    assert "faithful" in corps["analyses_indisponibles"], (
        "une analyse tombée doit être DÉCLARÉE, sinon sa liste vide passe pour "
        "« rien à signaler »"
    )


def test_aucune_analyse_indisponible_sur_un_corpus_sain(client) -> None:
    corps = client.get("/api/maintenance").json()
    assert corps["analyses_indisponibles"] == {}


def test_la_sante_signale_un_composant_en_echec(client, monkeypatch) -> None:
    """`/api/health` ne doit pas répondre « ok » quoi qu'il arrive.

    Un index figé, un backfill vocal raté ou une recherche sémantique jamais
    construite passaient inaperçus pendant des heures.
    """
    monkeypatch.setitem(api_main.ETAT_DEMARRAGE, "semantic_ok", False)
    monkeypatch.setitem(api_main.ETAT_DEMARRAGE, "semantic_detail", "poids introuvables")

    sante = client.get("/api/health").json()

    # « degraded » et non « error » : l'application RÉPOND, une partie de ses
    # fonctions est seulement dégradée. Confondre les deux ferait redémarrer un
    # service qui marche.
    assert sante["status"] == "degraded"
    assert sante["composants"]["semantique"] is False
    assert sante["details"]["semantique"] == "poids introuvables"


def test_un_backfill_non_termine_n_est_pas_un_echec(client, monkeypatch) -> None:
    """`None` doit rester distinct de `False` : le backfill sémantique tourne
    en tâche de fond et n'a pas forcément fini quand on interroge la santé."""
    monkeypatch.setitem(api_main.ETAT_DEMARRAGE, "semantic_ok", None)

    sante = client.get("/api/health").json()

    assert sante["status"] == "ok"
    assert sante["composants"]["semantique"] is None


def test_un_fournisseur_llm_inconnu_est_refuse(client, tmp_path) -> None:
    """Une valeur périmée dans le `localStorage` faisait changer de
    moteur en silence — l'utilisateur croyait parler à Mistral et parlait à
    Qwen. Un défaut permissif est ici un mensonge, pas une commodité."""
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"ID3")
    reponse = client.post(
        "/api/transcribe",
        files={"file": ("a.mp3", audio.read_bytes(), "audio/mpeg")},
        data={"llm_provider": "gpt-maison"},
    )

    assert reponse.status_code == 400
    assert "gpt-maison" in reponse.json()["detail"]


def test_les_fournisseurs_annonces_sont_ceux_que_le_backend_accepte(client) -> None:
    from core import llm

    annonces = client.get("/api/config").json()["llm_providers"]

    assert set(annonces) <= set(llm.FOURNISSEURS)
    for p in annonces:
        assert llm.valider_fournisseur(p) == p


def test_anthropic_est_propose_a_l_ecran_et_un_moteur_hors_interface_ne_l_est_pas(client, monkeypatch) -> None:
    """Anthropic est proposé comme les autres ; un moteur de `HORS_INTERFACE` reste au registre
    sans être proposé."""
    from core import llm

    config = client.get("/api/config").json()
    assert "anthropic" in config["llm_providers"] and "anthropic" not in config["local_providers"]
    assert "anthropic" in config["llm_ready"] and config["llm_models"]["anthropic"] == llm.modele_servi("anthropic")
    assert config["default_llm_provider"] == "mlx"
    monkeypatch.setattr(llm, "HORS_INTERFACE", frozenset({"anthropic"}))
    config = client.get("/api/config").json()
    assert "anthropic" not in config["llm_providers"] and "anthropic" in config["llm_ready"]


def test_la_config_annonce_la_disponibilite_de_CHAQUE_moteur(client) -> None:
    """MLX pouvait être au registre et rester invisible dans l'interface.

    La route calculait `llm_ollama.is_available() or bool(MISTRAL_API_KEY)` :
    deux implémentations nommées en dur, le troisième moteur nulle part. Un
    utilisateur ne pouvait donc pas voir que le local MLX était disponible.
    """
    from core import llm

    config = client.get("/api/config").json()

    assert set(config["llm_ready"]) == set(llm.MOTEURS)
    assert all(isinstance(v, bool) for v in config["llm_ready"].values())
    assert config["llm_available"] == any(config["llm_ready"].values())
    # Les clés héritées `ollama_available` / `mistral_api_key_configured` sont
    # RETIRÉES : elles nommaient deux moteurs dans une charge utile qui
    # décrit le registre entier, et les garder aurait obligé à publier une clé
    # `ollama` pour un fournisseur qui n'existe plus.
    assert "ollama_available" not in config
    assert "mistral_api_key_configured" not in config


def test_le_mode_local_par_defaut_est_MLX(client) -> None:
    """Le local tourne sur MLX, Ollama sort de la cible : voir `core/llm.py`
    (`FOURNISSEUR_DEFAUT`).

    Une mutation l'a exigé : remettre `"ollama"` en défaut ne faisait rougir
    aucun test.

    Le défaut est aussi vérifié comme fournisseur RÉEL du registre — sans quoi
    une faute de frappe passerait, et l'interface demanderait un moteur qui
    n'existe pas.
    """
    from core import llm

    config = client.get("/api/config").json()

    assert config["default_llm_provider"] == "mlx"
    assert config["default_llm_provider"] in llm.MOTEURS


def test_la_config_annonce_le_modele_de_CHAQUE_moteur(client) -> None:
    """L'interface n'avait aucun nom à afficher pour MLX.

    Elle ne connaissait que `llm_model` (le modèle Ollama) et
    `mistral_chat_model` : elle aurait nommé Qwen-Ollama devant un utilisateur
    servi par MLX — un modèle annoncé qui n'est pas celui qui tourne.
    """
    from core import llm

    config = client.get("/api/config").json()

    assert set(config["llm_models"]) == set(llm.MOTEURS)
    for nom in llm.MOTEURS:
        assert config["llm_models"][nom] == llm.modele_servi(nom)
    # Le modèle MLX ne doit surtout pas être celui d'Ollama.
    # Deux moteurs distincts ne servent pas le même modèle.
    assert len(set(config["llm_models"].values())) == len(config["llm_models"])


def test_MLX_seul_disponible_suffit_a_declarer_un_LLM(client, monkeypatch) -> None:
    """Le cas qui distingue `any(...)` d'un OU entre deux moteurs nommés.

    L'assertion du test précédent passe à vide sur ce poste : Mistral y est
    configuré, donc `any(...)` et `ollama or mistral` valent tous deux vrai.
    Une mutation l'a montré — il faut CONSTRUIRE la seule configuration qui
    les sépare, celle d'une machine MLX-only.

    C'est exactement la machine cible (`core/llm.py`, `FOURNISSEUR_DEFAUT`) :
    local MLX, sans Ollama.
    Y annoncer « aucun LLM » désactiverait les enrichissements alors que tout
    fonctionne.
    """
    from core import llm

    monkeypatch.setattr(llm.MOTEURS["mistral"], "pret_declare", lambda: False)
    monkeypatch.setattr(llm.MOTEURS["mlx"], "pret_declare", lambda: True)
    monkeypatch.setattr(llm.MOTEURS["openai"], "pret_declare", lambda: False)
    monkeypatch.setattr(llm.MOTEURS["anthropic"], "pret_declare", lambda: False)
    monkeypatch.setattr(llm.MOTEURS["compatible"], "pret_declare", lambda: False)

    config = client.get("/api/config").json()

    assert config["llm_ready"] == {"mistral": False, "mlx": True, "openai": False, "anthropic": False,
                                   "compatible": False}
    assert config["llm_available"] is True, (
        "une machine MLX-only s'annonce sans LLM — c'est la cible produit"
    )


def test_la_config_ne_demarre_aucun_moteur(client, monkeypatch) -> None:
    """La route est chargée à CHAQUE ouverture de l'interface.

    Sonder MLX par la preuve y démarrerait un `mlx_lm.server` — 2,1 Go — à
    chaque affichage de page. Le piège porte sur la supervision : c'est le coût
    qu'on vérifie, pas la valeur rendue.
    """
    from core import llm

    # Le piège ENREGISTRE, il ne lève pas : `is_available()` porte un
    # `except Exception` qui avalerait une `AssertionError` et rendrait `False`.
    # La route répondrait 200, le test passerait, et le serveur aurait démarré.
    appels: list[str] = []
    moteur_mlx = llm.MOTEURS["mlx"]
    for methode in ("_assurer_serveur", "_demarrer", "_completion"):
        monkeypatch.setattr(
            type(moteur_mlx), methode,
            lambda *a, _m=methode, **k: appels.append(_m),
        )

    assert client.get("/api/config").status_code == 200
    assert appels == [], f"/api/config a démarré un moteur : {appels}"


def test_la_route_ask_refuse_un_fournisseur_inconnu(client) -> None:
    """La validation du fournisseur ne doit pas couvrir que `/api/transcribe`.

    `FournisseurInconnu` hérite de `ValueError` et n'était capturé par aucun
    `except` de cette route — un fournisseur inconnu sortait en 500 brut,
    indistinguable d'un bug de notre code.
    """
    r = client.post("/api/ask", json={
        "question": "test", "scope": {"mode": "all"}, "llm_provider": "gpt-maison"})

    assert r.status_code == 400, f"reçu {r.status_code}, attendu 400"
    assert "gpt-maison" in r.json()["detail"]


def test_la_route_report_ne_confond_pas_fournisseur_et_transcript_absent(client) -> None:
    """Même cause, symptôme pire : `except ValueError` rendait **404**, donc
    « transcript introuvable » pour un fournisseur mal orthographié. Un code de
    statut faux envoie chercher le problème au mauvais endroit."""
    # Fournisseur invalide ET transcript absent : le 400 doit l'emporter. La
    # validation du fournisseur est PURE, elle passe donc avant le garde de
    # portée — sans quoi le 404 trompeur reviendrait par la fenêtre.
    r = client.post("/api/transcript/inexistant.md/report",
                    params={"view_scope": "all"},
                    json={"llm_provider": "gpt-maison"})

    assert r.status_code == 400, f"reçu {r.status_code}, attendu 400"
    assert "gpt-maison" in r.json()["detail"]


def test_le_code_de_message_traverse_la_vraie_route_sse(client) -> None:
    """Dernier maillon : ce qui part réellement sur le fil.

    Les tests de `core/messages.py` et `api/jobs.py` s'arrêtent à l'objet
    Python. Entre les deux et le navigateur il reste un `json.dumps` — et un
    `Message` sérialisé tel quel y perdrait son code sans un bruit. Cette
    vérification passe par la route HTTP réelle.
    """
    job = api_main.jobs.create(owner=compte_de_test().id)

    def target(progress):
        progress(0.65, messages.profilage("qwen2.5:7b"))
        return {"name": "r.md", "content": "ok"}

    api_main.jobs.run(job, target)
    with client.stream("GET", f"/api/jobs/{job.id}/events") as stream:
        corps = "".join(stream.iter_text())

    charges = [
        json.loads(ligne[len("data: "):])
        for ligne in corps.splitlines()
        if ligne.startswith("data: ")
    ]
    progressions = [c for c in charges if c.get("type") == "progress"]
    assert progressions == [
        {
            "type": "progress",
            "progress": 0.65,
            "code": "step.profiling",
            "params": {"model": "qwen2.5:7b"},
            "message": "Profiling with qwen2.5:7b…",
        }
    ]


def test_le_statut_json_publie_aussi_le_code(client) -> None:
    """Le chemin de RECONNEXION : `GET /api/jobs/{id}` et non le flux."""
    job = api_main.jobs.create(owner=compte_de_test().id)
    fini = threading.Event()

    def target(progress):
        progress(0.5, messages.diarisation())
        fini.set()
        return {"name": "r.md", "content": "ok"}

    api_main.jobs.run(job, target)
    assert fini.wait(timeout=2)
    corps = client.get(f"/api/jobs/{job.id}").json()
    assert corps["message_code"] == "step.diarization"
    assert corps["message_params"] == {}
    assert corps["message"] == "Identifying speakers…"
