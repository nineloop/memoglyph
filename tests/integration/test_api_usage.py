"""FinOps — endpoint de LECTURE d'agrégat `/api/usage/rollup` (dark, lecture seule).

Séries agrégées lues depuis `usage_daily`, BORNÉES PAR PROJET : une portée projet ne renvoie que
ce projet ; `view_scope=all` explicite = tous les projets du compte, jamais ceux d'un
autre compte (aucune fuite inter-projets).
Nuage/local et devises toujours séparés ; jamais un journal d'exécutions ; jamais d'écriture.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

OTHER_PROJECT = "01OTHERPROJECTZZZZZZZZZZZZ"   # projet hors du compte -> toujours exclu


def _snap(conn, usage_store, *, provider, model, kind, currency, unit="token",
          in_rate=2.0, out_rate=6.0, unit_rate=None):
    usage_store.insert_pricing_snapshot(
        conn, provider=provider, model=model, kind=kind, currency=currency, unit=unit,
        input_rate=in_rate, output_rate=out_rate, unit_rate=unit_rate,
        effective_from="2000-01-01T00:00:00+00:00", rate_source="configured_manual", source="t")


def _exec(conn, usage_store, *, eid, kind, provider, model, project, started_at, calls=(), audio=None, wall=1.0):
    conn.execute(
        "INSERT INTO execution(execution_id,kind,document_id,meeting_id,project_id,"
        "produced_artifact_id,provider_primary,model_primary,started_at,ended_at,wall_clock_s,"
        "status,n_calls,in_tok,out_tok,audio_seconds,schema_version,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (eid, kind, "D", "M", project, None, provider, model, started_at, started_at, wall,
         "ok", len(calls), None, None, audio, usage_store.SCHEMA_VERSION, started_at))
    for i, (stage, itok, otok) in enumerate(calls):
        conn.execute(
            "INSERT INTO model_call(call_id,execution_id,seq,stage,provider,model,started_at,"
            "latency_s,status,usage_source,in_tok,out_tok,cached_tok,reasoning_tok,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"{eid}-{i}", eid, i, stage, provider, model, started_at, 1.0, "ok",
             "provider_reported", itok, otok, 0, 0, started_at))


@pytest.fixture()
def usage_client(isolated_stores, monkeypatch, tmp_path):
    """Corpus isolé avec un projet « boîte de réception » réel + usage.db isolé.

    Trois exécutions : une sous le projet réel, une sous un second projet du compte (`mine`),
    une sous OTHER_PROJECT, hors du compte. La portée projet n'en voit qu'une ; `view_scope=all`
    révèle les deux du compte, jamais OTHER_PROJECT."""
    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(tmp_path / "usage.db"))
    from core import projects, usage_store
    from tests.conftest import COMPTE_DE_TEST
    out = isolated_stores["output_dir"]
    projects.initialize(out, simulation=False)
    # Bibliothèque vierge : la racine revient au compte de la session, comme à sa création
    # par l'API. Une lecture ne l'attribue pas (`api.portee`).
    projects.personal_inbox(out, COMPTE_DE_TEST)
    proj = projects.load(out)
    mine = projects.create(out / "Second", name="Second")

    conn = usage_store.connect(tmp_path / "usage.db")
    _snap(conn, usage_store, provider="openai", model="gpt-x", kind="cloud_billed", currency="USD")
    _snap(conn, usage_store, provider="whisper", model="whisper-x", kind="local_imputed",
          currency="USD", unit="audio_minute", unit_rate=0.006)
    # Projet réel : un CR nuage (2 étapes = 4.0) + un STT local (0.012), deux jours.
    _exec(conn, usage_store, eid="P_CLOUD", kind="report", provider="openai", model="gpt-x",
          project=proj.id, started_at="2026-08-09T10:00:00+00:00",
          calls=[("extract", 1_000_000, 0), ("synthese", 1_000_000, 0)])
    _exec(conn, usage_store, eid="P_STT", kind="stt", provider="whisper", model="whisper-x",
          project=proj.id, started_at="2026-08-10T09:00:00+00:00", audio=120.0)
    # Second projet du compte : un CR nuage (4.0), exclu d'une portée projet.
    _exec(conn, usage_store, eid="O_CLOUD", kind="report", provider="openai", model="gpt-x",
          project=mine.id, started_at="2026-08-09T10:00:00+00:00",
          calls=[("extract", 2_000_000, 0)])
    # Projet hors du compte : un CR nuage (2.0), exclu de toute portée, `all` comprise.
    _exec(conn, usage_store, eid="F_CLOUD", kind="report", provider="openai", model="gpt-x",
          project=OTHER_PROJECT, started_at="2026-08-09T10:00:00+00:00",
          calls=[("extract", 1_000_000, 0)])
    conn.commit()
    conn.close()
    usage_store.reprice(tmp_path / "usage.db")
    from api import main as api_main
    return {"client": TestClient(api_main.app), "usage_db": tmp_path / "usage.db", "proj": proj.id,
            "mine": mine.id}


def test_global_opt_in_voit_tous_les_projets(usage_client):
    c = usage_client["client"]
    j = c.get("/api/usage/rollup?group_by=project_id&view_scope=all").json()
    assert j["scope"] == "all"
    by_proj = {r["bucket"] for r in j["rows"]}
    assert by_proj == {usage_client["proj"], usage_client["mine"]}   # jamais OTHER_PROJECT


def test_portee_projet_ne_renvoie_que_ce_projet(usage_client):
    c, proj = usage_client["client"], usage_client["proj"]
    j = c.get(f"/api/usage/rollup?group_by=project_id&view_scope={proj}").json()
    assert j["scope"] == proj
    buckets = {r["bucket"] for r in j["rows"]}
    assert buckets == {proj}                      # OTHER_PROJECT EXCLU : aucune fuite inter-projets


def test_portee_projet_cout_sans_l_autre_projet(usage_client):
    c, proj = usage_client["client"], usage_client["proj"]
    j = c.get(f"/api/usage/rollup?group_by=cost_basis&view_scope={proj}").json()
    by_basis = {r["cost_basis"]: r["cost"] for r in j["rows"]}
    assert by_basis["cloud_derived"] == 4.0        # 2 étapes du projet ; PAS les 4.0 de l'autre
    assert by_basis["local_imputed"] == 0.012      # STT compté une fois


def test_defaut_sans_scope_n_est_pas_global(usage_client):
    """Sans `view_scope`, on retombe sur la boîte de réception (le projet réel), jamais global."""
    c, proj = usage_client["client"], usage_client["proj"]
    j = c.get("/api/usage/rollup?group_by=project_id").json()
    assert j["scope"] == proj and {r["bucket"] for r in j["rows"]} == {proj}


def test_headline_cloud_local_separes_global(usage_client):
    c = usage_client["client"]
    j = c.get("/api/usage/rollup?group_by=cost_basis&view_scope=all").json()
    by_basis = {r["cost_basis"]: r for r in j["rows"]}
    assert by_basis["cloud_derived"]["cost"] == 8.0 and by_basis["cloud_derived"]["currency"] == "USD"
    assert by_basis["local_imputed"]["cost"] == 0.012
    assert by_basis["cloud_derived"] is not by_basis["local_imputed"]


def test_filtres_et_group_by_invalide(usage_client):
    c = usage_client["client"]
    j = c.get("/api/usage/rollup?group_by=provider&kind=stt&view_scope=all").json()
    assert {r["bucket"] for r in j["rows"]} == {"whisper"}
    j2 = c.get("/api/usage/rollup?group_by=haha&view_scope=all").json()
    assert j2["group_by"] == "day"
    j3 = c.get("/api/usage/rollup?group_by=day&start=2026-08-10&end=2026-08-10&view_scope=all").json()
    assert {r["bucket"] for r in j3["rows"]} == {"2026-08-10"}


def test_cost_by_stage(usage_client):
    c = usage_client["client"]
    j = c.get("/api/usage/rollup?group_by=stage&view_scope=all").json()
    stages = {r["bucket"] for r in j["rows"]}
    assert {"extract", "synthese", "stt", "(execution)"} <= stages
    stt = next(r for r in j["rows"] if r["bucket"] == "stt")
    assert stt["cost"] == 0.012


def test_endpoint_n_ecrit_pas_usage_db(usage_client):
    import time
    c, db = usage_client["client"], usage_client["usage_db"]
    before = db.stat().st_mtime_ns
    time.sleep(0.01)
    for _ in range(3):
        c.get("/api/usage/rollup?group_by=day&view_scope=all")
    assert db.stat().st_mtime_ns == before


def test_usage_db_absente_degrade(usage_client, monkeypatch, tmp_path):
    c = usage_client["client"]
    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(tmp_path / "gone.db"))
    j = c.get("/api/usage/rollup?group_by=day&view_scope=all").json()
    assert j == {"group_by": "day", "scope": "all", "rows": []}
