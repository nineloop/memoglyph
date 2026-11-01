"""FinOps phase 3B — drill-down bornée : liste d'exécutions + détail forensique (lecture seule).

Invariants : bornée par projet (aucune fuite), paginée/plafonnée, requête indexée sur les faits
bruts (jamais un journal géant), détail forensique refusé hors portée projet, champs SÛRS
uniquement (aucun prompt/CR/secret), jamais d'écriture.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from core import usage_store

pytestmark = pytest.mark.integration

OTHER = "01OTHERPROJECTZZZZZZZZZZZZ"


def _snap(conn, **kw):
    usage_store.insert_pricing_snapshot(
        conn, kind="cloud_billed", currency="USD", input_rate=2.0, output_rate=6.0,
        effective_from="2000-01-01T00:00:00+00:00", rate_source="configured_manual", source="t", **kw)


def _exec(conn, *, eid, project, started_at, calls=(), kind="report",
          provider="openai", model="gpt-x", audio=None):
    conn.execute(
        "INSERT INTO execution(execution_id,kind,document_id,meeting_id,project_id,"
        "produced_artifact_id,provider_primary,model_primary,started_at,ended_at,wall_clock_s,"
        "status,n_calls,in_tok,out_tok,audio_seconds,schema_version,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (eid, kind, "D", "M", project, None, provider, model, started_at, started_at, 1.0,
         "ok", len(calls), None, None, audio, usage_store.SCHEMA_VERSION, started_at))
    for i, (stage, itok, otok) in enumerate(calls):
        conn.execute(
            "INSERT INTO model_call(call_id,execution_id,seq,stage,provider,model,started_at,"
            "latency_s,status,usage_source,in_tok,out_tok,cached_tok,reasoning_tok,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"{eid}-{i}", eid, i, stage, provider, model, started_at, 1.0, "ok",
             "provider_reported", itok, otok, 0, 0, started_at))


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(tmp_path / "usage.db"))
    conn = usage_store.connect(tmp_path / "usage.db")
    _snap(conn, provider="openai", model="gpt-x")
    for k in range(7):
        _exec(conn, eid=f"P1-{k}", project="PROJ1", started_at=f"2026-08-{k + 1:02d}T10:00:00+00:00",
              calls=[("extract", 1_000_000, 0)])
    _exec(conn, eid="OTHER-1", project=OTHER, started_at="2026-08-09T10:00:00+00:00",
          calls=[("extract", 2_000_000, 0)])
    _exec(conn, eid="STT-1", project="PROJ1", kind="stt", provider="whisper", model="whisper-x",
          started_at="2026-08-10T10:00:00+00:00", audio=90.0)
    conn.commit()
    conn.close()
    usage_store.reprice(tmp_path / "usage.db")
    return tmp_path / "usage.db"


# --- Store : bornes, pagination, portée, index ----------------------------------------------

def test_liste_bornee_et_paginee(db):
    page1 = usage_store.list_executions(project_id="PROJ1", limit=3, offset=0, path=db)
    assert len(page1["executions"]) == 3 and page1["has_more"] is True
    page3 = usage_store.list_executions(project_id="PROJ1", limit=3, offset=6, path=db)
    assert len(page3["executions"]) == 2 and page3["has_more"] is False   # 8 PROJ1 (7 report + 1 stt)


def test_limit_plafonne(db):
    assert usage_store.list_executions(project_id="PROJ1", limit=99999, path=db)["limit"] == usage_store.EXEC_LIST_MAX
    assert usage_store.list_executions(project_id="PROJ1", limit=0, path=db)["limit"] == 1


def test_portee_projet_aucune_fuite(db):
    only1 = usage_store.list_executions(project_id="PROJ1", limit=200, path=db)["executions"]
    assert only1 and all(e["project_id"] == "PROJ1" for e in only1)     # jamais OTHER
    other = usage_store.list_executions(project_id=OTHER, limit=200, path=db)["executions"]
    assert len(other) == 1 and other[0]["project_id"] == OTHER


def test_filtre_stage_et_kind(db):
    stt = usage_store.list_executions(project_id="PROJ1", kind="stt", limit=50, path=db)["executions"]
    assert len(stt) == 1 and stt[0]["kind"] == "stt"
    extract = usage_store.list_executions(project_id="PROJ1", stage="extract", limit=50, path=db)["executions"]
    assert extract and all(e["kind"] == "report" for e in extract)      # les 7 rapports


def test_liste_indexee_pas_de_scan_du_journal(db):
    conn = usage_store.read_connect(db)
    try:
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM execution WHERE project_id=? "
            "ORDER BY started_at DESC LIMIT 50", ("PROJ1",)).fetchall()
    finally:
        conn.close()
    text = " ".join(str(r["detail"]) for r in plan).lower()
    assert "execution" in text and "index" in text and "model_call" not in text


def test_champs_surs_sans_texte_ni_secret(db):
    blob = json.dumps(usage_store.list_executions(project_id="PROJ1", limit=200, path=db))
    for forbidden in ("prompt", "transcript", "content", "body", "secret", "sk-", "payload"):
        assert forbidden not in blob


# --- API : portée, drill-down forensique, refus hors projet, lecture seule ------------------

@pytest.fixture()
def client(db, isolated_stores, monkeypatch, tmp_path):
    """Un projet réel (boîte de réception) + usage.db du fixture `db`, réétiqueté sur ce projet.

    OTHER-1 passe sous un second projet du compte : `view_scope=all` = tous les projets du compte.
   """
    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(db))
    from core import projects
    out = isolated_stores["output_dir"]
    projects.initialize(out, simulation=False)
    proj = projects.load(out)
    # Rattache les exécutions PROJ1 au vrai projet pour tester la portée via view_scope.
    conn = usage_store.connect(db)
    conn.execute("UPDATE execution SET project_id=? WHERE project_id='PROJ1'", (proj.id,))
    second = projects.create(out / "Second", name="Second")
    conn.execute("UPDATE execution SET project_id=? WHERE project_id=?", (second.id, OTHER))
    conn.commit()
    conn.close()
    usage_store.rebuild_usage_daily(db)
    from api import main as api_main
    return {"client": TestClient(api_main.app), "proj": proj.id, "second": second.id, "db": db}


def test_api_liste_bornee_projet(client):
    c, proj = client["client"], client["proj"]
    j = c.get(f"/api/usage/executions?view_scope={proj}&limit=3").json()
    assert j["scope"] == proj and len(j["executions"]) == 3 and j["has_more"] is True
    assert all(e["project_id"] == proj for e in j["executions"])   # aucune fuite OTHER


def test_api_forensique_borne_projet_refuse_hors_portee(client):
    c, proj = client["client"], client["proj"]
    # Une exécution CR du projet : détail OK, avec ses lignes model_call.
    ex = c.get(f"/api/usage/executions?view_scope={proj}&kind=report&limit=1").json()["executions"][0]
    d = c.get(f"/api/usage/executions/{ex['execution_id']}?view_scope={proj}")
    assert d.status_code == 200 and d.json()["available"] and d.json()["calls"]
    # L'exécution d'un AUTRE projet, demandée sous ce projet -> 404 (pas de lecture inter-projets).
    x = c.get(f"/api/usage/executions/OTHER-1?view_scope={proj}")
    assert x.status_code == 404


def test_api_global_voit_tout_mais_explicite(client):
    c = client["client"]
    allj = c.get("/api/usage/executions?view_scope=all&limit=200").json()
    projects_seen = {e["project_id"] for e in allj["executions"]}
    assert projects_seen == {client["proj"], client["second"]}   # tous les projets du compte


def test_api_detail_sans_texte_ni_secret(client):
    c, proj = client["client"], client["proj"]
    ex = c.get(f"/api/usage/executions?view_scope={proj}&limit=1").json()["executions"][0]
    blob = c.get(f"/api/usage/executions/{ex['execution_id']}?view_scope={proj}").text
    for forbidden in ("prompt", "transcript", "content", "body", "secret", "sk-", "payload"):
        assert forbidden not in blob


def test_api_drilldown_n_ecrit_pas_usage_db(client):
    import time
    c, proj, dbp = client["client"], client["proj"], client["db"]
    before = dbp.stat().st_mtime_ns
    time.sleep(0.01)
    for _ in range(3):
        c.get(f"/api/usage/executions?view_scope={proj}&limit=5")
    assert dbp.stat().st_mtime_ns == before


def test_api_execution_absente_degrade(client):
    c, proj = client["client"], client["proj"]
    # Inconnue ou d'un autre compte : le même 404, rien ne dit si l'identifiant existe.
    r = c.get(f"/api/usage/executions/ZZZINCONNU?view_scope={proj}")
    assert r.status_code == 404
    assert r.json() == c.get(f"/api/usage/executions/OTHER-1?view_scope={proj}").json()
