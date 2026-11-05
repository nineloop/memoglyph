"""FinOps — recette adversariale : réconciliation, portée, pagination, panne/vie privée.

Prouve que le tableau de bord réconcilie EXACTEMENT (rollup == faits bruts), que deux projets à
fournisseurs/étapes qui SE CHEVAUCHENT ne fuient jamais, que la pagination est stable (ni doublon
ni oubli), que le forensique appartient à SON exécution, et que la télémétrie absente/corrompue
dégrade sans jamais mélanger devises/bases ni fabriquer un faux zéro.
"""
from __future__ import annotations

import json

import pytest

from core import usage_store

pytestmark = pytest.mark.integration

P1 = "01PROJECT1ZZZZZZZZZZZZZZZZ"
P2 = "01PROJECT2ZZZZZZZZZZZZZZZZ"


def _snap(conn, *, provider, model, kind="cloud_billed", currency="USD",
          input_rate=2.0, output_rate=6.0, unit="token", unit_rate=None):
    usage_store.insert_pricing_snapshot(
        conn, provider=provider, model=model, kind=kind, currency=currency, unit=unit,
        input_rate=input_rate, output_rate=output_rate, unit_rate=unit_rate,
        effective_from="2000-01-01T00:00:00+00:00", rate_source="configured_manual", source="t")


def _exec(conn, *, eid, project, started_at, calls=(), kind="report",
          provider="openai", model="gpt-x", audio=None, wall=1.0):
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
def db(tmp_path, monkeypatch):
    """Deux projets, fournisseur `openai` et étape `extract` PARTAGÉS ; nuage + local + non tarifé."""
    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(tmp_path / "usage.db"))
    conn = usage_store.connect(tmp_path / "usage.db")
    _snap(conn, provider="openai", model="gpt-x")
    _snap(conn, provider="localbench", model="qwen-x", kind="local_imputed",
          input_rate=0.10, output_rate=0.30)
    # P1 : nuage openai (extract+synthese) + local + un non tarifé.
    _exec(conn, eid="P1-cloud", project=P1, started_at="2026-08-09T10:00:00+00:00",
          calls=[("extract", 1_000_000, 0), ("synthese", 500_000, 0)])
    _exec(conn, eid="P1-local", project=P1, provider="localbench", model="qwen-x",
          started_at="2026-08-09T11:00:00+00:00", calls=[("extract", 1_000_000, 0)])
    _exec(conn, eid="P1-unpriced", project=P1, provider="openai", model="mystere",
          started_at="2026-08-09T12:00:00+00:00", calls=[("extract", 300_000, 0)])
    _exec(conn, eid="P1-stt", project=P1, kind="stt", provider="whisper", model="w-x",
          started_at="2026-08-09T13:00:00+00:00", audio=120.0)
    # P2 : MÊME fournisseur openai, MÊME étape extract (chevauchement) — détecte toute fuite.
    _exec(conn, eid="P2-cloud", project=P2, started_at="2026-08-09T10:30:00+00:00",
          calls=[("extract", 2_000_000, 0)])
    conn.commit()
    conn.close()
    usage_store.reprice(tmp_path / "usage.db")
    return tmp_path / "usage.db"


def _raw(conn, sql, *p):
    return conn.execute(sql, p).fetchone()[0]


# --- 1. Réconciliation rollup == faits bruts (par base, jamais mélangée) ---------------------

def test_reconciliation_cloud_local_unpriced(db):
    conn = usage_store.read_connect(db)
    try:
        # Vérités BRUTES, projet P1.
        raw_cloud = _raw(conn, "SELECT ROUND(SUM(c.cost),10) FROM model_call c JOIN execution e "
                         "ON e.execution_id=c.execution_id WHERE e.project_id=? AND c.cost_basis='cloud_derived'", P1)
        raw_local = _raw(conn, "SELECT ROUND(SUM(c.cost),10) FROM model_call c JOIN execution e "
                         "ON e.execution_id=c.execution_id WHERE e.project_id=? AND c.cost_basis='local_imputed'", P1)
        raw_calls = _raw(conn, "SELECT COUNT(*) FROM model_call c JOIN execution e "
                         "ON e.execution_id=c.execution_id WHERE e.project_id=?", P1)
        raw_tokens = _raw(conn, "SELECT COALESCE(SUM(c.in_tok+c.out_tok),0) FROM model_call c JOIN execution e "
                          "ON e.execution_id=c.execution_id WHERE e.project_id=?", P1)
        raw_audio = _raw(conn, "SELECT COALESCE(SUM(audio_seconds),0) FROM execution WHERE project_id=?", P1)
        raw_wall = _raw(conn, "SELECT COALESCE(SUM(wall_clock_s),0) FROM execution WHERE project_id=?", P1)
        raw_exec = _raw(conn, "SELECT COUNT(*) FROM execution WHERE project_id=?", P1)
    finally:
        conn.close()

    rows = usage_store.usage_rollup(project_id=P1, group_by="cost_basis", path=db)
    by_basis = {(r["cost_basis"], r["currency"]): r for r in rows}
    # Nuage dérivé et local estimé réconcilient — chacun SÉPARÉMENT, jamais additionnés.
    assert by_basis[("cloud_derived", "USD")]["cost"] == raw_cloud
    assert by_basis[("local_imputed", "USD")]["cost"] == raw_local
    # Non tarifé : présent, coût NULL (jamais 0).
    assert (None, None) in by_basis and by_basis[(None, None)]["cost"] is None

    # Totaux transverses (ce que somment les KPIs de l'UI) == brut, sans double compte.
    assert sum(r["n_model_calls"] for r in rows) == raw_calls
    assert sum(r["in_tok"] + r["out_tok"] for r in rows) == raw_tokens
    assert sum(r["audio_seconds"] for r in rows) == raw_audio
    assert sum(r["wall_clock_s"] for r in rows) == raw_wall
    assert sum(r["n_executions"] for r in rows) == raw_exec   # chaque exécution comptée UNE fois


def test_reconciliation_segments_provider_et_stage(db):
    # Segment fournisseur : openai (nuage) réconcilie sur le coût brut openai de P1.
    prov = {(r["bucket"], r["cost_basis"]): r["cost"]
            for r in usage_store.usage_rollup(project_id=P1, group_by="provider", path=db)}
    conn = usage_store.read_connect(db)
    try:
        raw_openai = _raw(conn, "SELECT ROUND(SUM(c.cost),10) FROM model_call c JOIN execution e "
                          "ON e.execution_id=c.execution_id WHERE e.project_id=? AND c.provider='openai' "
                          "AND c.cost_basis='cloud_derived'", P1)
    finally:
        conn.close()
    assert prov[("openai", "cloud_derived")] == raw_openai
    # Segment étape : 'extract' existe en trois bases distinctes (nuage/local/non tarifé) — jamais fondu.
    stage = usage_store.usage_rollup(project_id=P1, group_by="stage", path=db)
    extract_bases = {r["cost_basis"] for r in stage if r["bucket"] == "extract"}
    assert {"cloud_derived", "local_imputed", None} <= extract_bases


# --- 2. Portée : fournisseur/étape partagés, aucune fuite ------------------------------------

def test_portee_projet_chevauchement_sans_fuite(db):
    # openai/extract existe dans P1 ET P2 ; le rollup P1 ne doit compter QUE P1.
    p1 = usage_store.usage_rollup(project_id=P1, provider="openai", stage="extract", group_by="project_id", path=db)
    assert {r["bucket"] for r in p1} == {P1}
    p1_cloud = next(r["cost"] for r in p1 if r["cost_basis"] == "cloud_derived")
    # P1 openai/extract = 1e6 in @2 = 2.0 ; P2 openai/extract = 2e6 @2 = 4.0. Aucun mélange.
    assert p1_cloud == 2.0
    p2_cloud = next(r["cost"] for r in usage_store.usage_rollup(
        project_id=P2, provider="openai", stage="extract", group_by="cost_basis", path=db)
        if r["cost_basis"] == "cloud_derived")
    assert p2_cloud == 4.0
    # Liste d'exécutions bornée P1 : jamais l'exécution P2.
    ids = {e["execution_id"] for e in usage_store.list_executions(project_id=P1, limit=200, path=db)["executions"]}
    assert "P2-cloud" not in ids and "P1-cloud" in ids


# --- 3. Pagination : ni doublon, ni oubli, ordre stable, filtre persistant -------------------

def test_pagination_complete_stable_sans_doublon(db):
    full = usage_store.list_executions(project_id=P1, limit=200, path=db)["executions"]
    order = [e["execution_id"] for e in full]
    # Réassemble par pages de 2 ; doit reproduire EXACTEMENT la liste complète, même ordre.
    paged, offset = [], 0
    while True:
        page = usage_store.list_executions(project_id=P1, limit=2, offset=offset, path=db)
        paged.extend(e["execution_id"] for e in page["executions"])
        if not page["has_more"]:
            break
        offset += 2
        assert offset < 100                         # garde-fou anti-boucle
    assert paged == order                            # ni doublon, ni oubli, ordre identique
    assert len(paged) == len(set(paged))             # aucun doublon
    # Ordre stable : started_at DÉCROISSANT.
    starts = [e["started_at"] for e in full]
    assert starts == sorted(starts, reverse=True)


def test_pagination_conserve_le_filtre(db):
    # Filtre provider=openai conservé sur toutes les pages.
    seen, offset = [], 0
    while True:
        page = usage_store.list_executions(project_id=P1, provider="openai", limit=1, offset=offset, path=db)
        seen.extend(page["executions"])
        if not page["has_more"]:
            break
        offset += 1
    assert seen and all(e["provider_primary"] == "openai" for e in seen)


def test_limit_clamp_bornes(db):
    assert usage_store.list_executions(project_id=P1, limit=10 ** 9, path=db)["limit"] == usage_store.EXEC_LIST_MAX
    assert usage_store.list_executions(project_id=P1, limit=-5, path=db)["limit"] == 1


def test_forensique_appartient_a_son_execution(db):
    d = usage_store.execution_detail("P1-cloud", path=db)
    # Toutes les lignes viennent de P1-cloud (extract+synthese), jamais d'une autre exécution.
    assert [c["stage"] for c in d["calls"]] == ["extract", "synthese"]
    other = usage_store.execution_detail("P1-local", path=db)
    assert [c["stage"] for c in other["calls"]] == ["extract"]   # 1 seul appel, pas ceux de P1-cloud


# --- 4. Panne / vie privée ------------------------------------------------------------------

def test_usage_db_corrompue_degrade_rollup_et_liste(db):
    db.write_bytes(b"pas du SQLite")                  # corruption franche
    assert usage_store.usage_rollup(project_id=P1, group_by="day", path=db) == []
    res = usage_store.list_executions(project_id=P1, path=db)
    assert res["executions"] == [] and res["has_more"] is False


def test_lecture_sans_texte_ni_secret(db):
    blob = json.dumps({
        "rollup": usage_store.usage_rollup(group_by="stage", path=db),
        "list": usage_store.list_executions(project_id=P1, limit=200, path=db),
        "detail": usage_store.execution_detail("P1-cloud", path=db),
    })
    for forbidden in ("prompt", "transcript", "content", "body", "secret", "sk-", "payload", "message"):
        assert forbidden not in blob


# --- 5. Intégrité usage_daily : delete/rebuild restaure les mêmes résultats VISIBLES ---------

def test_delete_rebuild_restaure_les_memes_series(db):
    before = usage_store.usage_rollup(project_id=P1, group_by="stage", path=db)
    conn = usage_store.connect(db)
    conn.execute("DELETE FROM usage_daily")
    conn.commit()
    conn.close()
    assert usage_store.usage_rollup(project_id=P1, group_by="stage", path=db) == []   # vidé
    usage_store.rebuild_usage_daily(db)
    assert usage_store.usage_rollup(project_id=P1, group_by="stage", path=db) == before  # restauré
