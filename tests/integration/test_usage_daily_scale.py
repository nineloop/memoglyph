"""FinOps phase 3A — échelle : le tableau de bord lit `usage_daily`, jamais un scan du journal.

Marqué `slow` : exclu de `make check` (`-m "not slow and not ml"`), lancé explicitement pour
la preuve d'échelle. Génère ~100k exécutions / ~250k appels par insertion en masse, reconstruit
l'agrégat, et vérifie qu'une lecture de série reste rapide en ne lisant que `usage_daily`.
"""
from __future__ import annotations

import os
import time

import pytest

from core import usage_store

pytestmark = [pytest.mark.integration, pytest.mark.slow]

N_EXEC = int(os.getenv("GB_SCALE_EXEC", "100000"))   # exécutions synthétiques
N_PROJECTS = 12                                        # partitionnement projet
PROVIDERS = [("openai", "gpt-x", "cloud_billed", "USD"),
             ("mistral", "mistral-x", "cloud_billed", "EUR"),
             ("mlx", "qwen-x", "local_imputed", "USD")]
STAGES = ["extract", "classify", "synthese"]


def test_echelle_lecture_agrege_sans_scanner_le_journal(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(tmp_path / "usage.db"))
    db = tmp_path / "usage.db"
    conn = usage_store.connect(db)
    for prov, model, kind, cur in PROVIDERS:
        usage_store.insert_pricing_snapshot(
            conn, provider=prov, model=model, kind=kind, currency=cur, unit="token",
            input_rate=2.0, output_rate=6.0, effective_from="2000-01-01T00:00:00+00:00",
            rate_source="configured_manual", source="scale")

    # Insertion en masse : ~100k exécutions réparties sur 180 jours, 2–3 appels chacune.
    t0 = time.perf_counter()
    execs, calls = [], []
    for i in range(N_EXEC):
        prov, model, kind, cur = PROVIDERS[i % len(PROVIDERS)]
        day = f"2026-{1 + (i % 6):02d}-{1 + (i % 28):02d}T10:00:00+00:00"
        eid = f"E{i}"
        project = f"PROJ{i % N_PROJECTS:02d}"
        ncalls = 2 + (i % 2)
        execs.append((eid, "report", "D", "M", project, None, prov, model, day, day, 1.5, "ok",
                      ncalls, None, None, None, usage_store.SCHEMA_VERSION, day))
        for s in range(ncalls):
            calls.append((f"{eid}-{s}", eid, s, STAGES[s % len(STAGES)], prov, model, day, 1.0,
                          "ok", "provider_reported", 1000, 500, 0, 0, day))
    conn.executemany(
        "INSERT INTO execution(execution_id,kind,document_id,meeting_id,project_id,"
        "produced_artifact_id,provider_primary,model_primary,started_at,ended_at,wall_clock_s,"
        "status,n_calls,in_tok,out_tok,audio_seconds,schema_version,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", execs)
    conn.executemany(
        "INSERT INTO model_call(call_id,execution_id,seq,stage,provider,model,started_at,"
        "latency_s,status,usage_source,in_tok,out_tok,cached_tok,reasoning_tok,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", calls)
    conn.commit()
    n_calls = conn.execute("SELECT COUNT(*) FROM model_call").fetchone()[0]
    conn.close()
    t_insert = time.perf_counter() - t0

    # Tarification (coûts) puis rebuild de l'agrégat.
    t0 = time.perf_counter()
    usage_store.reprice(db)
    t_reprice = time.perf_counter() - t0

    conn = usage_store.read_connect(db)
    daily_rows = conn.execute("SELECT COUNT(*) FROM usage_daily").fetchone()[0]
    conn.close()

    # L'agrégat est des ORDRES DE GRANDEUR plus petit que le journal brut.
    assert daily_rows < n_calls / 50, (daily_rows, n_calls)

    # Une lecture de série (spend over time) doit être rapide ET ne toucher que usage_daily.
    conn = usage_store.read_connect(db)
    plan = conn.execute(
        "EXPLAIN QUERY PLAN SELECT day,currency,cost_basis,SUM(cost) FROM usage_daily "
        "WHERE day>=? AND day<=? GROUP BY day,currency,cost_basis", ("2026-01-01", "2026-12-31")
    ).fetchall()
    conn.close()
    plan_text = " ".join(str(r["detail"]) for r in plan).lower()
    assert "usage_daily" in plan_text and "model_call" not in plan_text

    t0 = time.perf_counter()
    for _ in range(20):
        series = usage_store.usage_rollup(start="2026-01-01", end="2026-12-31",
                                          group_by="day", path=db)
    t_query = (time.perf_counter() - t0) / 20
    assert series and t_query < 0.05, t_query    # < 50 ms par lecture de série

    # Requête BORNÉE PROJET : lit usage_daily via l'index projet, rapide, sans fuite.
    conn = usage_store.read_connect(db)
    pplan = conn.execute(
        "EXPLAIN QUERY PLAN SELECT day,currency,cost_basis,SUM(cost) FROM usage_daily "
        "WHERE project_id=? GROUP BY day,currency,cost_basis", ("PROJ00",)).fetchall()
    conn.close()
    ptext = " ".join(str(r["detail"]) for r in pplan).lower()
    assert "usage_daily" in ptext and "model_call" not in ptext and "index" in ptext

    t0 = time.perf_counter()
    for _ in range(20):
        scoped = usage_store.usage_rollup(project_id="PROJ00", group_by="day", path=db)
    t_proj = (time.perf_counter() - t0) / 20
    assert scoped and t_proj < 0.05, t_proj
    # Le projet borné ne voit QUE ses lignes.
    assert all(r["group_by"] == "day" for r in scoped)
    per_proj = usage_store.usage_rollup(group_by="project_id", path=db)
    assert len({r["bucket"] for r in per_proj}) == N_PROJECTS

    print(f"\n[scale] exec={N_EXEC} calls={n_calls} projects={N_PROJECTS} daily_rows={daily_rows} "
          f"insert={t_insert:.1f}s reprice={t_reprice:.1f}s query={t_query * 1000:.1f}ms "
          f"scoped={t_proj * 1000:.1f}ms")
