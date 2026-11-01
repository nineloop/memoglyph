"""L'ultra-confidentiel ne part vers aucun fournisseur cloud (`sensitivity.cloud_refuse`).

Chaque route refuse avant toute lecture destinée au modèle, toute réservation et tout
envoi. La preuve qu'aucun appel n'a lieu : le moteur est remplacé par un faux qui
échoue s'il est appelé. Le local reste toujours accepté.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api import main as api_main
from api.jobs import JobManager
from core import docid, llm, projects, semantic, sensitivity
from tests.conftest import COMPTE_DE_TEST

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _faits_actives(monkeypatch):
    """Ces tests supposent les faits de la réunion activés (`MEMOGLYPH_FAITS=1`)."""
    from core import config as _config

    monkeypatch.setattr(_config, "FAITS", True)

CLOUD = ("mistral", "openai", "anthropic", "cloud")


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


def _document(isolated_stores, inbox, nom: str, sensibilite: str, reunion: str | None = None) -> str:
    chemin = isolated_stores["output_dir"] / nom
    chemin.write_text(
        f"---\nid: {docid.nouveau()}\nmeeting_id: {reunion or docid.nouveau()}\nproject_id: {inbox.id}\n"
        f"title: {nom}\nlanguage: fr\nsensitivity: {sensibilite}\n---\n\n## Transcript\n\n"
        "[00:00:01] Le rachat se fait à douze millions.\n",
        encoding="utf-8")
    api_main.library.sync_file(chemin)
    return nom


def test_la_regle_unique() -> None:
    assert sensitivity.cloud_refuse("ultra_confidential", local=False)
    assert not sensitivity.cloud_refuse("ultra_confidential", local=True)
    assert not sensitivity.cloud_refuse("confidential", local=False)
    assert not sensitivity.cloud_refuse(None, local=False), "défaut : standard"


# ---------- Transcription ----------

@pytest.mark.parametrize("champs", [
    {"engine": "mistral"},                                         # transcription cloud
    *({"llm_provider": p, "report_on": "true"} for p in CLOUD),    # CR cloud
    *({"llm_provider": p, "report_on": "false", "profile": "true"} for p in CLOUD),  # profil cloud
])
def test_transcrire_un_ultra_confidentiel_en_cloud_est_refuse_avant_tout(client, inbox, monkeypatch,
                                                                       tmp_path, champs) -> None:
    monkeypatch.setattr(api_main.pipeline, "run", _jamais)
    reponse = client.post("/api/transcribe", files={"file": ("rachat.wav", b"audio")},
                          data={"sensitivity_level": "ultra_confidential", **champs})
    assert reponse.status_code == 403
    assert reponse.json()["detail"] == sensitivity.REFUS_CLOUD
    assert not (tmp_path / "uploads").exists(), "rien n'a été reçu ni écrit"


def test_transcrire_un_ultra_confidentiel_en_local_est_accepte(client, inbox, monkeypatch) -> None:
    appels: list = []
    monkeypatch.setattr(api_main.pipeline, "run", lambda *a, **k: appels.append(k) or _jamais())
    reponse = client.post("/api/transcribe", files={"file": ("rachat.wav", b"audio")},
                          data={"sensitivity_level": "ultra_confidential", "engine": "whisper",
                                "llm_provider": "mlx"})
    assert reponse.status_code == 200 and reponse.json()["job_id"]
    # Un CR désactivé laisse le fournisseur sans objet : il n'est pas un motif de refus.
    reponse = client.post("/api/transcribe", files={"file": ("r.wav", b"audio")},
                          data={"sensitivity_level": "ultra_confidential", "engine": "whisper",
                                "llm_provider": "openai", "report_on": "false", "profile": "false"})
    assert reponse.status_code == 200


# ---------- Compte rendu ----------

@pytest.mark.parametrize("fournisseur", CLOUD)
def test_le_cr_d_un_ultra_confidentiel_en_cloud_est_refuse(client, isolated_stores, inbox, monkeypatch,
                                                          fournisseur) -> None:
    from core import report
    monkeypatch.setattr(report, "generate_report", _jamais)
    nom = _document(isolated_stores, inbox, "rachat.md", "ultra_confidential")
    reponse = client.post(f"/api/transcript/{nom}/report", params={"view_scope": inbox.id},
                          json={"llm_provider": fournisseur})
    assert reponse.status_code == 403 and reponse.json()["detail"] == sensitivity.REFUS_CLOUD


def test_le_cr_reste_possible_en_local_et_pour_un_document_ordinaire(client, isolated_stores, inbox,
                                                                    monkeypatch) -> None:
    from core import report
    vus: list = []
    monkeypatch.setattr(report, "generate_report", lambda nom, **k: vus.append((nom, k["provider"])) or {"ok": 1})
    ultra = _document(isolated_stores, inbox, "rachat.md", "ultra_confidential")
    ordinaire = _document(isolated_stores, inbox, "point.md", "standard")
    assert client.post(f"/api/transcript/{ultra}/report", params={"view_scope": inbox.id},
                       json={"llm_provider": "mlx"}).status_code == 200
    assert client.post(f"/api/transcript/{ordinaire}/report", params={"view_scope": inbox.id},
                       json={"llm_provider": "openai"}).status_code == 200
    assert vus == [(ultra, "mlx"), (ordinaire, "openai")]


# ---------- Assistant ----------

PASSAGES = [
    {"name": "rachat.md", "title": "Rachat", "ts": "00:00:01", "text": "RACHAT SECRET à douze millions",
     "sensitivity": "ultra_confidential", "project": "Non classé"},
    {"name": "point.md", "title": "Point", "ts": "00:00:05", "text": "Le budget est validé",
     "sensitivity": "standard", "project": "Non classé"},
]


@pytest.fixture
def assistant(monkeypatch):
    invites: list[str] = []
    monkeypatch.setattr(llm, "is_available", lambda **k: True)
    monkeypatch.setattr(llm, "chat", lambda system, prompt, **k: invites.append(prompt) or "réponse [1]")
    return invites


@pytest.mark.parametrize("fournisseur", CLOUD)
def test_l_assistant_en_cloud_n_envoie_jamais_un_passage_ultra_confidentiel(client, inbox, monkeypatch,
                                                                          assistant, fournisseur) -> None:
    monkeypatch.setattr(semantic, "retrieve", lambda q, **k: [dict(p) for p in PASSAGES])
    reponse = client.post("/api/ask", json={"question": "Le budget ?", "llm_provider": fournisseur,
                                            "scope": {"mode": "project", "project_id": inbox.id}})
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["passages_ecartes"] == 1
    assert [s["name"] for s in corps["sources"]] == ["point.md"]
    assert len(assistant) == 1 and "RACHAT SECRET" not in assistant[0] and "budget" in assistant[0]


def test_l_assistant_en_cloud_refuse_s_il_ne_reste_rien(client, inbox, monkeypatch, assistant) -> None:
    monkeypatch.setattr(semantic, "retrieve", lambda q, **k: [dict(PASSAGES[0])])
    monkeypatch.setattr(llm, "chat", _jamais)
    reponse = client.post("/api/ask", json={"question": "Le rachat ?", "llm_provider": "openai",
                                            "scope": {"mode": "project", "project_id": inbox.id}})
    assert reponse.status_code == 403 and reponse.json()["detail"] == sensitivity.REFUS_CLOUD


def test_l_assistant_en_local_garde_tous_les_passages(client, inbox, monkeypatch, assistant) -> None:
    monkeypatch.setattr(semantic, "retrieve", lambda q, **k: [dict(p) for p in PASSAGES])
    corps = client.post("/api/ask", json={"question": "Le rachat ?", "llm_provider": "mlx",
                                          "scope": {"mode": "project", "project_id": inbox.id}}).json()
    assert "passages_ecartes" not in corps and len(corps["sources"]) == 2
    assert "RACHAT SECRET" in assistant[0]


# ---------- Relance des faits (ECL) ----------

@pytest.mark.parametrize("fournisseur", [p for p in CLOUD if p != "cloud"])   # « cloud » : alias non proposé
def test_relancer_les_faits_d_un_ultra_confidentiel_en_cloud_est_refuse(client, isolated_stores, inbox,
                                                                       monkeypatch, fournisseur) -> None:
    from core import ecl
    monkeypatch.setattr(ecl, "extraire", _jamais)
    monkeypatch.setattr(api_main.jobs, "run", _jamais)
    reunion = docid.nouveau()
    _document(isolated_stores, inbox, "rachat.md", "ultra_confidential", reunion=reunion)
    reponse = client.post(f"/api/meetings/{reunion}/ecl", params={"view_scope": inbox.id},
                          json={"provider": fournisseur})
    assert reponse.status_code == 403 and reponse.json()["detail"] == sensitivity.REFUS_CLOUD


def test_relancer_les_faits_d_un_ultra_confidentiel_en_local_est_accepte(client, isolated_stores, inbox,
                                                                        monkeypatch) -> None:
    lances: list = []
    monkeypatch.setattr(api_main.jobs, "run", lambda job, target: lances.append(job))
    reunion = docid.nouveau()
    _document(isolated_stores, inbox, "rachat.md", "ultra_confidential", reunion=reunion)
    reponse = client.post(f"/api/meetings/{reunion}/ecl", params={"view_scope": inbox.id},
                          json={"provider": "mlx"})
    assert reponse.status_code == 200 and len(lances) == 1


def test_la_recherche_porte_la_sensibilite_et_reste_locale(client, isolated_stores, inbox, monkeypatch) -> None:
    """La recherche n'appelle aucun fournisseur : FTS et index sémantique sont locaux."""
    monkeypatch.setattr(llm, "chat", _jamais)
    monkeypatch.setattr(llm, "chat_json", _jamais)
    _document(isolated_stores, inbox, "rachat.md", "ultra_confidential")
    reponse = client.get("/api/search", params={"q": "rachat", "mode": "fts", "view_scope": inbox.id})
    assert reponse.status_code == 200 and reponse.json()
