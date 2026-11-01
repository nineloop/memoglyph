"""Le profilage et l'Assistant dans FinOps : comptés, chiffrés, rattachés à leur projet.

- `profiling.profile`, dans le pipeline, s'exécute dans une exécution `profile` qui porte
  le document, la réunion et le projet de la transcription ; ses appels sont comptés et
  chiffrés comme ceux du compte rendu, en local comme en cloud (simulé, aucun réseau).
- L'Assistant d'un projet porte ce projet et apparaît dans la vue Usage de ce projet ;
  « tous mes projets » ou un partage : sans projet.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from api import auth, partages
from api import main as api_main
from api.jobs import JobManager
from core import docid, library, llm, pipeline, profiling, projects, semantic, stt, telemetry, usage_store
from core.stt_base import Segment, Transcript

pytestmark = pytest.mark.integration

MOT_DE_PASSE = "un mot de passe solide"
IN_TOK, OUT_TOK = 1000, 200


class _Moteur:
    name = "fake-whisper"
    model_name = "fake-model"

    def transcribe(self, wav_path, progress=None):
        return Transcript(segments=[Segment(0, 1, "Bonjour à tous")], full_text="Bonjour à tous",
                          language="fr")


def _appel_mesure(provider: str) -> None:
    """Ce qu'un moteur enregistre après un appel réussi : fournisseur, modèle, jetons."""
    telemetry.record_model_call(provider=provider, model=llm.modele_servi(provider),
                                latency_s=0.1, in_tok=IN_TOK, out_tok=OUT_TOK,
                                usage_source="provider_reported")


@pytest.fixture
def usage_db(tmp_path, monkeypatch):
    chemin = tmp_path / "usage.db"
    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(chemin))
    with closing(usage_store.connect(chemin)) as conn:
        for fournisseur, genre in (("mlx", "local_imputed"), ("mistral", "cloud_billed")):
            usage_store.insert_pricing_snapshot(
                conn, provider=fournisseur, model=llm.modele_servi(fournisseur), kind=genre,
                currency="USD", input_rate=2.0, output_rate=6.0,
                effective_from="2000-01-01T00:00:00+00:00", rate_source="configured_manual",
                source="test")
        conn.commit()
    return chemin


def _executions(chemin) -> list[sqlite3.Row]:
    with closing(sqlite3.connect(chemin)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM execution ORDER BY started_at").fetchall()


def _appels(chemin, execution_id) -> list[sqlite3.Row]:
    with closing(sqlite3.connect(chemin)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM model_call WHERE execution_id = ?",
                            (execution_id,)).fetchall()


@pytest.mark.parametrize("fournisseur", ["mlx", "mistral"])
def test_un_profil_apparait_dans_usage_avec_son_projet(fournisseur, usage_db, isolated_stores,
                                                      tmp_path, monkeypatch) -> None:
    """Local puis cloud simulé : une exécution `profile`, document, réunion et projet de la
    transcription, appels comptés et chiffrés comme ceux d'un compte rendu."""
    sortie = isolated_stores["output_dir"]
    dossier, projet = projects.create_owned(sortie, name="Projet profilé", owner="alice")
    source = tmp_path / "source.mp3"
    source.write_bytes(b"source")
    wav = tmp_path / "temporary.wav"
    wav.write_bytes(b"wav")
    monkeypatch.setattr(pipeline.config, "load_env", lambda: None)
    monkeypatch.setattr(pipeline.media, "validate_input", lambda path: path)
    monkeypatch.setattr(pipeline.media, "probe_duration", lambda path: 61.2)
    monkeypatch.setattr(pipeline.media, "to_wav16k", lambda path: wav)
    monkeypatch.setattr(pipeline.media, "pcm_sha256", lambda path: "f" * 64)
    for archive in ("archive_audio", "archive_faithful", "keep_original"):
        monkeypatch.setattr(pipeline.media, archive, lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "get_engine", lambda name, **_kw: _Moteur())
    monkeypatch.setattr(pipeline, "_annoncer_modele_local", lambda *a, **k: None)
    monkeypatch.setattr(stt, "unload", lambda: None)
    monkeypatch.setattr(llm, "unload", lambda **_kw: False)
    monkeypatch.setattr(profiling.llm, "is_available", lambda **_kw: True)

    def chat_json(_systeme, _invite, *, provider, **_kw):
        _appel_mesure(provider)
        return {"conversation": {"type": "réunion", "summary": "Un point.", "topics": ["budget"]}}

    monkeypatch.setattr(profiling.llm, "chat_json", chat_json)

    out_path, _contenu = pipeline.run(source, engine_name="whisper", language="fr",
                                      profile_on=True, report_on=False, llm_provider=fournisseur,
                                      output_dir=dossier)

    meta, _ = library._split_frontmatter(out_path.read_text(encoding="utf-8"))
    executions = {e["kind"]: e for e in _executions(usage_db)}
    assert set(executions) == {"stt", "profile"}
    profil = executions["profile"]
    assert (profil["document_id"], profil["meeting_id"], profil["project_id"]) == (
        meta["id"], meta["meeting_id"], projet.id)
    assert (profil["provider_primary"], profil["model_primary"]) == (
        fournisseur, llm.modele_servi(fournisseur))
    assert (profil["status"], profil["n_calls"], profil["in_tok"], profil["out_tok"]) == (
        "ok", 1, IN_TOK, OUT_TOK)
    [appel] = _appels(usage_db, profil["execution_id"])
    assert appel["stage"] == "profile"

    # Chiffré comme le compte rendu : le même appel sous une exécution `report`.
    with telemetry.execution(kind="report", project_id=projet.id, provider=fournisseur,
                             model=llm.modele_servi(fournisseur)):
        _appel_mesure(fournisseur)
    rapport = next(e for e in _executions(usage_db) if e["kind"] == "report")
    assert profil["cost"] is not None and profil["cost"] > 0
    assert (profil["cost"], profil["cost_currency"], profil["cost_basis"]) == (
        rapport["cost"], rapport["cost_currency"], rapport["cost_basis"])
    assert profil["cost_basis"] == ("local_imputed" if fournisseur == "mlx" else "cloud_derived")


# ---------- L'Assistant par projet ----------

def _vec() -> list[float]:
    from core.config import EMBED_DIM
    return [1.0] + [0.0] * (EMBED_DIM - 1)


@pytest.fixture
def assistant(usage_db, isolated_stores, tmp_path, monkeypatch):
    sortie = isolated_stores["output_dir"]
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(auth, "CODE_PATH", tmp_path / ".memoglyph-installation")
    monkeypatch.setattr(api_main, "jobs", JobManager())
    monkeypatch.setattr(semantic.embed, "embed_documents", lambda textes, **_k: [_vec()] * len(textes))
    monkeypatch.setattr(semantic.embed, "embed_query", lambda t, **_k: _vec())
    from core import rag
    monkeypatch.setattr(rag.llm, "is_available", lambda **_kw: True)

    def chat(_systeme, _invite, *, provider, **_kw):
        _appel_mesure(provider)
        return "Réponse [1]."

    monkeypatch.setattr(rag.llm, "chat", chat)
    for compte in ("alice", "bruno"):
        auth.creer_compte(compte, MOT_DE_PASSE)
        projects.personal_inbox(sortie, compte)
    dossier, projet = projects.create_owned(sortie, name="Budget", owner="alice")
    _d2, autre = projects.create_owned(sortie, name="Autre", owner="alice")
    dossier_b, projet_b = projects.create_owned(sortie, name="Atelier", owner="bruno")
    for repertoire, identite in ((dossier, projet.id), (dossier_b, projet_b.id)):
        (repertoire / f"budget-{identite}.md").write_text(
            f"---\nid: {docid.nouveau()}\nmeeting_id: {docid.nouveau()}\nproject_id: {identite}\n"
            "title: Budget\nlanguage: fr\n---\n\n## Transcript\n\n**Speaker 1** [00:00:05] "
            "le budget du projet est validé\n", encoding="utf-8")
    # Bruno partage son projet avec Alice : la portée « partagés avec moi » a un passage.
    partages.partager("bruno", "alice", "projet", projet_b.id)
    library.reindex()
    semantic.reindex_all()
    client = TestClient(api_main.app)
    assert client.post("/api/auth/connexion", json={
        "identifiant": "alice", "mot_de_passe": MOT_DE_PASSE}).status_code == 200
    return {"client": client, "projet": projet.id, "autre": autre.id, "usage_db": usage_db}


@pytest.mark.sans_session
def test_l_assistant_d_un_projet_apparait_dans_la_vue_de_ce_projet(assistant) -> None:
    client, projet = assistant["client"], assistant["projet"]

    reponse = client.post("/api/ask", json={
        "question": "Le budget ?", "scope": {"mode": "project", "project_id": projet},
        "llm_provider": "mlx"})

    assert reponse.status_code == 200, reponse.text
    vue = client.get("/api/usage/executions", params={"view_scope": projet}).json()
    assert [e["kind"] for e in vue["executions"]] == ["assistant"]
    assert vue["executions"][0]["project_id"] == projet
    # Un autre projet du compte ne la voit pas.
    assert client.get("/api/usage/executions",
                      params={"view_scope": assistant["autre"]}).json()["executions"] == []
    cumul = client.get("/api/usage/rollup", params={"view_scope": projet,
                                                     "group_by": "exec_kind"}).json()
    assert "assistant" in {r["bucket"] for r in cumul["rows"]}


@pytest.mark.sans_session
@pytest.mark.parametrize("portee", [{"mode": "all"}, {"mode": "shared"}])
def test_l_assistant_de_plusieurs_projets_reste_sans_projet(assistant, portee) -> None:
    client = assistant["client"]

    reponse = client.post("/api/ask", json={"question": "Le budget ?", "scope": portee,
                                            "llm_provider": "mlx"})

    assert reponse.status_code == 200, reponse.text
    assert reponse.json()["sources"], "un passage au moins : l'Assistant a appelé le modèle"
    executions = [e for e in _executions(assistant["usage_db"]) if e["kind"] == "assistant"]
    assert len(executions) == 1 and executions[0]["project_id"] is None
    assert client.get("/api/usage/executions",
                      params={"view_scope": assistant["projet"]}).json()["executions"] == []
