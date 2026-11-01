"""Finitions : sensibilité effective d'un CR, refus de l'Assistant cloud avec une clé factice."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api import main as api_main
from api.jobs import JobManager
from core import config, docid, llm, projects, semantic, sensitivity
from tests.conftest import COMPTE_DE_TEST

pytestmark = pytest.mark.integration

CLE_FACTICE = "cle-factice-de-test"


def _jamais(*_a, **_k):
    raise AssertionError("le fournisseur a été appelé alors que la route devait refuser")


@pytest.fixture
def client(monkeypatch, tmp_path, isolated_stores):
    monkeypatch.setattr(api_main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(api_main, "jobs", JobManager())
    return TestClient(api_main.app)


@pytest.fixture
def inbox(isolated_stores):
    return projects.create(isolated_stores["output_dir"], name=projects.INBOX_NAME, kind="inbox",
                           owner=COMPTE_DE_TEST)


def _document(isolated_stores, inbox, nom: str, sensibilite: str, reunion: str, extra: str = "") -> str:
    chemin = isolated_stores["output_dir"] / nom
    ident = docid.nouveau()
    chemin.write_text(
        f"---\nid: {ident}\nmeeting_id: {reunion}\nproject_id: {inbox.id}\ntitle: {nom}\nlanguage: fr\n"
        f"sensitivity: {sensibilite}\n{extra}---\n\n## Transcript\n\n"
        "[00:00:01] Le rachat se fait à douze millions.\n",
        encoding="utf-8")
    api_main.library.sync_file(chemin)
    return ident


def test_un_cr_affiche_la_sensibilite_effective_de_sa_reunion(client, isolated_stores, inbox) -> None:
    reunion = docid.nouveau()
    _document(isolated_stores, inbox, "rachat.md", "ultra_confidential", reunion)
    _document(isolated_stores, inbox, "rachat-cr.md", "standard", reunion,
              "type: report\nsource_transcript: rachat.md\n")
    corps = client.get("/api/transcript/rachat-cr.md", params={"view_scope": inbox.id}).json()
    assert corps["meta"]["sensitivity"] == "standard", "le niveau stocké du CR ne change pas"
    assert corps["sensitivity_effective"] == "ultra_confidential"


def test_l_assistant_cloud_refuse_une_reunion_ultra_confidentielle_avec_une_cle_factice(
        client, isolated_stores, inbox, monkeypatch) -> None:
    """La clé est là : le refus vient de la sensibilité, pas d'une clé absente."""
    reunion = docid.nouveau()
    _document(isolated_stores, inbox, "rachat.md", "ultra_confidential", reunion)
    ident = _document(isolated_stores, inbox, "suite.md", "standard", reunion)
    monkeypatch.setenv("MISTRAL_API_KEY", CLE_FACTICE)
    monkeypatch.setattr(config, "MISTRAL_API_KEY", CLE_FACTICE)
    monkeypatch.setattr(llm, "chat", _jamais)
    monkeypatch.setattr(llm, "is_available", _jamais)
    monkeypatch.setattr(semantic, "retrieve", lambda q, **k: [{
        "name": "suite.md", "title": "Suite", "ts": "00:00:01", "text": "Le rachat se fait à douze millions.",
        "sensitivity": "standard", "meeting_id": reunion, "document_id": ident, "project": "Non classé"}])
    reponse = client.post("/api/ask", json={"question": "Le rachat ?", "llm_provider": "mistral",
                                            "mistral_api_key": CLE_FACTICE,
                                            "scope": {"mode": "project", "project_id": inbox.id}})
    assert reponse.status_code == 403 and reponse.json()["detail"] == sensitivity.REFUS_CLOUD
