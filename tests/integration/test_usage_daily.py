"""FinOps — agrégat DÉRIVÉ `usage_daily` + lecture de séries.

Invariants prouvés : grain exact, idempotence incrémentale, équivalence rebuild complet,
faits bruts intacts, séparation devise/base, non tarifé distinct (jamais faux zéro), filtres
jour/fournisseur/modèle/étape/genre, reconstruction après suppression, la lecture agrège (ne
scanne pas `model_call`), et un échec d'agrégation ne casse jamais la génération.
"""
from __future__ import annotations

import pytest

from core import usage_store

pytestmark = pytest.mark.integration


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(tmp_path / "usage.db"))
    return tmp_path / "usage.db"


def _snap(conn, *, provider, model, kind, currency, unit="token",
          in_rate=2.0, out_rate=6.0, unit_rate=None):
    """Barème de test déterministe. `configured_manual` pour éviter l'exigence de source
    officielle ; `kind` décide la base (cloud_billed→cloud_derived, local_imputed→local_imputed)."""
    usage_store.insert_pricing_snapshot(
        conn, provider=provider, model=model, kind=kind, currency=currency, unit=unit,
        input_rate=in_rate, output_rate=out_rate, unit_rate=unit_rate,
        effective_from="2000-01-01T00:00:00+00:00", rate_source="configured_manual",
        source="test",
    )


def _exec(conn, *, eid, kind, provider, model, started_at, calls=(),
          audio_seconds=None, wall=1.0, doc="D", project="P"):
    conn.execute(
        "INSERT INTO execution(execution_id,kind,document_id,meeting_id,project_id,"
        "produced_artifact_id,provider_primary,model_primary,started_at,ended_at,wall_clock_s,"
        "status,n_calls,in_tok,out_tok,audio_seconds,schema_version,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (eid, kind, doc, "M", project, None, provider, model, started_at, started_at, wall,
         "ok", len(calls), None, None, audio_seconds, usage_store.SCHEMA_VERSION, started_at),
    )
    for i, (stage, itok, otok, cached, reasoning) in enumerate(calls):
        conn.execute(
            "INSERT INTO model_call(call_id,execution_id,seq,stage,provider,model,started_at,"
            "latency_s,status,usage_source,in_tok,out_tok,cached_tok,reasoning_tok,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"{eid}-{i}", eid, i, stage, provider, model, started_at, 1.0, "ok",
             "provider_reported", itok, otok, cached, reasoning, started_at),
        )


def _seed_basic(db):
    """Un CR nuage (2 étapes), un STT local, un CR non tarifé — sur deux jours."""
    conn = usage_store.connect(db)
    _snap(conn, provider="openai", model="gpt-x", kind="cloud_billed", currency="USD")
    _snap(conn, provider="whisper", model="whisper-x", kind="local_imputed",
          currency="USD", unit="audio_minute", unit_rate=0.006)
    _exec(conn, eid="E_CLOUD", kind="report", provider="openai", model="gpt-x",
          started_at="2026-08-09T10:00:00+00:00", wall=5.0,
          calls=[("extract", 1_000_000, 1_000_000, 100, 50),
                 ("synthese", 2_000_000, 500_000, 0, 10)])
    _exec(conn, eid="E_STT", kind="stt", provider="whisper", model="whisper-x",
          started_at="2026-08-09T09:00:00+00:00", wall=4.0, audio_seconds=120.0)
    _exec(conn, eid="E_UNPRICED", kind="report", provider="acme", model="mystere",
          started_at="2026-08-10T08:00:00+00:00", wall=2.0,
          calls=[("extract", 3_000, 1_000, 0, 0)])
    conn.commit()
    conn.close()
    usage_store.reprice(db)   # tarifie + reconstruit usage_daily


def _rows(db):
    conn = usage_store.read_connect(db)
    try:
        cols = ("day", "project_id", "provider", "model", "stage", "exec_kind", "currency",
                "cost_basis", "n_executions", "n_model_calls", "in_tok", "out_tok", "cached_tok",
                "reasoning_tok", "audio_seconds", "wall_clock_s", "cost")
        return [{c: r[c] for c in cols} for r in conn.execute(
            "SELECT * FROM usage_daily "
            "ORDER BY day,project_id,provider,model,stage,exec_kind,currency,cost_basis")]
    finally:
        conn.close()


# --- 1. Grain exact -------------------------------------------------------------------------

def test_grain_exact_par_dimension(db):
    _seed_basic(db)
    rows = _rows(db)
    key = {(r["day"], r["project_id"], r["provider"], r["model"], r["stage"], r["exec_kind"],
            r["currency"], r["cost_basis"]) for r in rows}
    # `project_id` (fait d'exécution) fait partie du grain.
    assert ("2026-08-09", "P", "openai", "gpt-x", "extract", "report", "USD", "cloud_derived") in key
    assert ("2026-08-09", "P", "openai", "gpt-x", "synthese", "report", "USD", "cloud_derived") in key
    assert ("2026-08-09", "P", "openai", "gpt-x", "(execution)", "report", "USD", "cloud_derived") in key
    # STT : une seule ligne 'stt' portant l'audio + le coût audio.
    assert ("2026-08-09", "P", "whisper", "whisper-x", "stt", "stt", "USD", "local_imputed") in key
    # Non tarifé : base ET devise NULL, distinctes.
    assert ("2026-08-10", "P", "acme", "mystere", "extract", "report", None, None) in key
    # Pas de double compte : exactement une unité d'exécution par exécution -> 3 au total.
    assert sum(r["n_executions"] for r in rows) == 3
    # Jetons/appels seulement sur les lignes d'étape.
    extract = next(r for r in rows if r["stage"] == "extract" and r["provider"] == "openai")
    assert extract["in_tok"] == 1_000_000 and extract["out_tok"] == 1_000_000
    assert extract["cached_tok"] == 100 and extract["reasoning_tok"] == 50
    assert extract["n_model_calls"] == 1 and extract["n_executions"] == 0
    # wall_clock/audio seulement sur les unités d'exécution.
    stt = next(r for r in rows if r["stage"] == "stt")
    assert stt["audio_seconds"] == 120.0 and stt["wall_clock_s"] == 4.0 and stt["n_executions"] == 1


def test_cout_par_etape_et_total_sans_double_compte(db):
    _seed_basic(db)
    rows = _rows(db)
    # extract : 1e6 in @2/1e6 + 1e6 out @6/1e6 = 2 + 6 = 8 ; synthese : 2*2 + 0.5*6 = 7.
    extract = next(r for r in rows if r["stage"] == "extract" and r["provider"] == "openai")
    synth = next(r for r in rows if r["stage"] == "synthese")
    assert extract["cost"] == 8.0 and synth["cost"] == 7.0
    # La ligne '(execution)' du CR ne porte AUCUN coût (déjà sur les étapes) -> pas de double compte.
    exrow = next(r for r in rows if r["stage"] == "(execution)" and r["provider"] == "openai")
    assert exrow["cost"] is None
    # STT audio : 120 s = 2 min @0.006 = 0.012.
    stt = next(r for r in rows if r["stage"] == "stt")
    assert stt["cost"] == 0.012


# --- 2. Devise/base séparées, non tarifé jamais faux zéro -----------------------------------

def test_non_tarife_reste_null_jamais_zero(db):
    _seed_basic(db)
    unpriced = [r for r in _rows(db) if r["provider"] == "acme"]
    assert unpriced and all(r["cost_basis"] is None and r["cost"] is None for r in unpriced)
    # Les jetons du non tarifé restent MESURÉS malgré le coût inconnu.
    call = next(r for r in unpriced if r["stage"] == "extract")
    assert call["in_tok"] == 3_000 and call["n_model_calls"] == 1


def test_devises_jamais_melangees(db):
    conn = usage_store.connect(db)
    _snap(conn, provider="p_usd", model="m", kind="cloud_billed", currency="USD")
    _snap(conn, provider="p_eur", model="m", kind="cloud_billed", currency="EUR")
    _exec(conn, eid="U", kind="report", provider="p_usd", model="m",
          started_at="2026-08-09T10:00:00+00:00", calls=[("extract", 1_000_000, 0, 0, 0)])
    _exec(conn, eid="E", kind="report", provider="p_eur", model="m",
          started_at="2026-08-09T11:00:00+00:00", calls=[("extract", 1_000_000, 0, 0, 0)])
    conn.commit()
    conn.close()
    usage_store.reprice(db)
    # Un seau par (devise, base) : jamais un seul montant USD+EUR.
    series = usage_store.usage_rollup(group_by="cost_basis", path=db)
    by_cur = {r["currency"]: r["cost"] for r in series}
    assert by_cur["USD"] == 2.0 and by_cur["EUR"] == 2.0


# --- 3. Idempotence incrémentale & équivalence rebuild --------------------------------------

def test_incremental_idempotent_et_equivaut_au_rebuild(db):
    _seed_basic(db)
    baseline = _rows(db)          # peuplé par reprice (rebuild complet ; sans `rebuilt_at`)
    # Rejouer l'incrémental de chaque exécution ne change RIEN (idempotent).
    conn = usage_store.connect(db)
    for eid in ("E_CLOUD", "E_STT", "E_UNPRICED"):
        usage_store._update_daily_for(conn, eid)
    conn.commit()
    conn.close()
    assert _rows(db) == baseline
    # Rebuild complet -> mêmes seaux.
    usage_store.rebuild_usage_daily(db)
    assert _rows(db) == baseline


def test_rebuild_apres_suppression(db):
    _seed_basic(db)
    baseline = _rows(db)
    conn = usage_store.connect(db)
    conn.execute("DELETE FROM usage_daily")
    conn.commit()
    conn.close()
    assert _rows(db) == []                          # agrégat supprimable…
    usage_store.rebuild_usage_daily(db)
    assert _rows(db) == baseline                    # …et reconstructible depuis les faits bruts.


def test_faits_bruts_intacts_apres_agregation(db):
    _seed_basic(db)
    conn = usage_store.read_connect(db)
    execs = conn.execute("SELECT COUNT(*) FROM execution").fetchone()[0]
    calls = conn.execute("SELECT COUNT(*) FROM model_call").fetchone()[0]
    tok = conn.execute("SELECT SUM(in_tok) FROM model_call").fetchone()[0]
    conn.close()
    usage_store.rebuild_usage_daily(db)
    usage_store.rebuild_usage_daily(db)             # plusieurs fois
    conn = usage_store.read_connect(db)
    assert conn.execute("SELECT COUNT(*) FROM execution").fetchone()[0] == execs
    assert conn.execute("SELECT COUNT(*) FROM model_call").fetchone()[0] == calls
    assert conn.execute("SELECT SUM(in_tok) FROM model_call").fetchone()[0] == tok
    conn.close()


# --- 4. Filtres de lecture ------------------------------------------------------------------

def test_filtres_date_provider_model_stage_kind(db):
    _seed_basic(db)
    # Plage de dates : seul le 2026-08-10 (le CR non tarifé).
    only_10 = usage_store.usage_rollup(start="2026-08-10", end="2026-08-10", group_by="provider", path=db)
    assert {r["bucket"] for r in only_10} == {"acme"}
    # Fournisseur.
    wh = usage_store.usage_rollup(provider="whisper", group_by="stage", path=db)
    assert {r["bucket"] for r in wh} == {"stt"}
    # Étape.
    ex = usage_store.usage_rollup(stage="synthese", group_by="model", path=db)
    assert {r["bucket"] for r in ex} == {"gpt-x"}
    # Genre d'exécution.
    stt = usage_store.usage_rollup(kind="stt", group_by="provider", path=db)
    assert {r["bucket"] for r in stt} == {"whisper"}
    # Modèle.
    gx = usage_store.usage_rollup(model="gpt-x", group_by="stage", path=db)
    assert {r["bucket"] for r in gx} == {"extract", "synthese", "(execution)"}


def test_rollup_group_by_invalide_retombe_sur_day(db):
    _seed_basic(db)
    rows = usage_store.usage_rollup(group_by="project_id; DROP TABLE execution", path=db)
    assert all(r["group_by"] == "day" for r in rows)   # liste blanche : pas d'injection


# --- 5. La lecture agrège (ne scanne pas model_call) ----------------------------------------

def test_la_lecture_lit_usage_daily_pas_model_call(db):
    _seed_basic(db)
    conn = usage_store.read_connect(db)
    try:
        plan = conn.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT day, currency, cost_basis, SUM(cost) FROM usage_daily "
            "WHERE day>=? AND day<=? GROUP BY day, currency, cost_basis",
            ("2026-08-01", "2026-08-31"),
        ).fetchall()
        proj_plan = conn.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT day, currency, cost_basis, SUM(cost) FROM usage_daily "
            "WHERE project_id=? GROUP BY day, currency, cost_basis",
            ("P",),
        ).fetchall()
    finally:
        conn.close()
    text = " ".join(str(r["detail"]) for r in plan).lower()
    assert "usage_daily" in text
    assert "model_call" not in text and "execution" not in text   # jamais le journal brut
    assert "index" in text                                         # via un index, pas un full scan
    # La requête bornée projet lit aussi usage_daily via un index (jamais le journal brut).
    ptext = " ".join(str(r["detail"]) for r in proj_plan).lower()
    assert "usage_daily" in ptext and "index" in ptext
    assert "model_call" not in ptext and "execution" not in ptext


# --- 6. Isolation des pannes ----------------------------------------------------------------

def test_echec_agregation_ne_casse_pas_la_persistance(db, monkeypatch):
    """Un `_daily_insert` qui lève ne perd aucun fait et ne propage aucune exception."""
    from core import telemetry

    def boom(*a, **k):
        raise RuntimeError("agrégat cassé")

    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(db))
    usage_store.initialize_catalogue(db)
    monkeypatch.setattr(usage_store, "_daily_insert", boom)
    # La génération (contexte d'exécution -> persist) ne doit PAS lever.
    with telemetry.execution(kind="report", provider="openai", model="gpt-5.6-luna", document_id="D"):
        with telemetry.stage("extract"):
            class _U:
                input_tokens = 10
                output_tokens = 5
                input_tokens_details = {"cached_tokens": 0}
                output_tokens_details = None
            telemetry.record_model_call(provider="openai", model="gpt-5.6-luna",
                                        latency_s=1.0, usage=_U(), usage_source="provider_reported")
        telemetry.set_produced_artifact("R")
    # Les FAITS bruts sont persistés malgré l'agrégat cassé.
    conn = usage_store.read_connect(db)
    n = conn.execute("SELECT COUNT(*) FROM execution").fetchone()[0]
    conn.close()
    assert n == 1


def test_rebuild_best_effort_ne_leve_pas(tmp_path):
    """usage.db absente/illisible -> rebuild renvoie une erreur comptée, ne lève jamais."""
    missing = tmp_path / "nexiste-pas" / "usage.db"   # dossier parent absent
    out = usage_store.rebuild_usage_daily(missing)
    assert out["errors"] >= 1 and out["rows"] == 0


# --- 7. Portée projet (grain project_id) ----------------------------------------------------

def _seed_projects(db):
    """Deux projets + une exécution héritée (project_id NULL), tous le même jour/fournisseur."""
    conn = usage_store.connect(db)
    _snap(conn, provider="openai", model="gpt-x", kind="cloud_billed", currency="USD")
    _exec(conn, eid="A", kind="report", provider="openai", model="gpt-x", project="PROJ1",
          started_at="2026-08-09T10:00:00+00:00", calls=[("extract", 1_000_000, 0, 0, 0)])   # 2.0
    _exec(conn, eid="B", kind="report", provider="openai", model="gpt-x", project="PROJ2",
          started_at="2026-08-09T11:00:00+00:00", calls=[("extract", 2_000_000, 0, 0, 0)])   # 4.0
    _exec(conn, eid="LEG", kind="report", provider="openai", model="gpt-x", project=None,
          started_at="2026-08-09T12:00:00+00:00", calls=[("extract", 3_000_000, 0, 0, 0)])   # 6.0, NULL
    conn.commit()
    conn.close()
    usage_store.reprice(db)


def test_project_id_dans_le_grain(db):
    _seed_projects(db)
    projects = {r["project_id"] for r in _rows(db)}
    assert projects == {"PROJ1", "PROJ2", None}      # NULL hérité gardé EXPLICITE, jamais remappé


def test_deux_projets_ne_se_melangent_jamais(db):
    _seed_projects(db)
    series = usage_store.usage_rollup(group_by="project_id", path=db)
    by_proj = {r["bucket"]: r["cost"] for r in series}
    assert by_proj["PROJ1"] == 2.0 and by_proj["PROJ2"] == 4.0 and by_proj[None] == 6.0
    # Aucune ligne ne fusionne deux projets : chaque seau a UN project_id.
    assert all("project_id" in r for r in _rows(db))


def test_filtre_projet_ne_renvoie_que_ce_projet(db):
    _seed_projects(db)
    only1 = usage_store.usage_rollup(project_id="PROJ1", group_by="day", path=db)
    assert len(only1) == 1 and only1[0]["cost"] == 2.0
    only2 = usage_store.usage_rollup(project_id="PROJ2", group_by="provider", path=db)
    assert {r["bucket"] for r in only2} == {"openai"} and only2[0]["cost"] == 4.0


def test_projet_null_hérité_ciblable_explicitement(db):
    _seed_projects(db)
    # `project_id=None` cible EXPLICITEMENT les exécutions héritées (IS NULL), pas « tout ».
    legacy = usage_store.usage_rollup(project_id=None, group_by="day", path=db)
    assert len(legacy) == 1 and legacy[0]["cost"] == 6.0
    # La vue globale (UNSCOPED) additionne bien les trois — opt-in explicite.
    glob = usage_store.usage_rollup(group_by="day", path=db)
    assert len(glob) == 1 and glob[0]["cost"] == 12.0


def test_project_survit_rebuild_suppression_reprice(db):
    _seed_projects(db)
    baseline = _rows(db)
    # delete + rebuild
    conn = usage_store.connect(db)
    conn.execute("DELETE FROM usage_daily")
    conn.commit()
    conn.close()
    usage_store.rebuild_usage_daily(db)
    assert _rows(db) == baseline
    # reprice rebâtit en conservant le partitionnement projet
    usage_store.reprice(db)
    assert _rows(db) == baseline


def test_incremental_par_projet_equivaut_au_rebuild(db):
    _seed_projects(db)
    baseline = _rows(db)
    conn = usage_store.connect(db)
    for eid in ("A", "B", "LEG"):
        usage_store._update_daily_for(conn, eid)
    conn.commit()
    conn.close()
    assert _rows(db) == baseline
