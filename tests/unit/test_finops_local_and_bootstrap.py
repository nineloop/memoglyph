"""FinOps phase 1D — hypothèses locales + initialisation zéro-touche.

Invariants protégés :
- hypothèses locales : valeurs exactes, provenance `configured_manual`, `local_imputed`, USD ;
- JAMAIS `local_benchmark` ; structurellement SÉPARÉES d'`OFFICIAL_RATES` ;
- `connect()` reste une primitive : n'ensemence RIEN ;
- l'init catalogue (seam app) est idempotente, additive, best-effort ;
- l'init ne REPRICE JAMAIS l'historique ; faits d'usage intacts ;
- coût MLX (in/out/cache) et Whisper (minute d'audio) calculés en `local_imputed` ;
- devises jamais mélangées ; le local ne fuit pas dans le cloud.
"""
from __future__ import annotations

import pytest

from core import telemetry, usage_store, pricing_official


@pytest.fixture()
def usage_db(tmp_path, monkeypatch):
    p = tmp_path / "usage.db"
    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(p))
    return p


class _MlxUsage:
    def __init__(self, i, o, cached=0):
        self.prompt_tokens = i
        self.completion_tokens = o
        self.prompt_tokens_details = {"cached_tokens": cached}
        self.completion_tokens_details = None


# ----------------------- Hypothèses locales : valeurs + provenance ------------
def test_hypotheses_locales_valeurs_et_provenance():
    by = {(e["provider"], e["model"]): e for e in pricing_official.LOCAL_ASSUMPTIONS}
    mlx = by[("mlx", "mlx-community/Qwen3-4B-Instruct-2507-4bit")]
    assert (mlx["input_rate"], mlx["output_rate"], mlx["cached_rate"]) == (0.10, 0.30, 0.02)
    assert mlx["unit"] == "token" and mlx["currency"] == "USD"
    wh = by[("whisper", "mlx-community/whisper-large-v3-turbo")]
    assert wh["unit_rate"] == 0.0015 and wh["unit"] == "audio_minute" and wh["currency"] == "USD"
    for e in pricing_official.LOCAL_ASSUMPTIONS:
        assert e["kind"] == "local_imputed"
        assert e["rate_source"] == "configured_manual"      # PAS local_benchmark
        assert e["rate_source"] != "local_benchmark"
        assert e["effective_from"] == pricing_official.LOCAL_ASSUMED_AT


def test_local_structurellement_separe_des_officiels():
    off = {(e["provider"], e["model"]) for e in pricing_official.OFFICIAL_RATES}
    loc = {(e["provider"], e["model"]) for e in pricing_official.LOCAL_ASSUMPTIONS}
    assert off.isdisjoint(loc)                              # aucun chevauchement
    # aucune hypothèse locale n'a été glissée dans OFFICIAL_RATES
    assert all(e["rate_source"] == "official_published" for e in pricing_official.OFFICIAL_RATES)
    assert all(e["kind"] == "local_imputed" for e in pricing_official.LOCAL_ASSUMPTIONS)


# ----------------------- connect() : aucune fuite d'ensemencement -------------
def test_connect_seul_n_ensemence_rien(usage_db):
    c = usage_store.connect(usage_db)
    assert c.execute("SELECT COUNT(*) FROM pricing_snapshot").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM model_alias").fetchone()[0] == 0
    c.close()


# ----------------------- Init catalogue : seam app, idempotente ---------------
def test_initialize_catalogue_ensemence_tout_une_fois(usage_db):
    n_off = len(pricing_official.OFFICIAL_RATES)
    n_loc = len(pricing_official.LOCAL_ASSUMPTIONS)
    n_al = len(pricing_official.OFFICIAL_ALIASES)
    r1 = usage_store.initialize_catalogue(usage_db)
    assert r1["ok"] and r1["imported"] == n_off and r1["local_imported"] == n_loc \
        and r1["aliases_imported"] == n_al
    c = usage_store.connect(usage_db)
    assert c.execute("SELECT COUNT(*) FROM pricing_snapshot").fetchone()[0] == n_off + n_loc
    assert c.execute("SELECT COUNT(*) FROM model_alias").fetchone()[0] == n_al
    c.close()


def test_initialize_catalogue_idempotent(usage_db):
    usage_store.initialize_catalogue(usage_db)
    r2 = usage_store.initialize_catalogue(usage_db)          # 2e lancement : rien de neuf
    assert r2["ok"] and r2["imported"] == 0 and r2["local_imported"] == 0 and r2["aliases_imported"] == 0
    c = usage_store.connect(usage_db)
    # exactement un exemplaire de chaque
    for prov, mod in [("mlx", "mlx-community/Qwen3-4B-Instruct-2507-4bit"),
                      ("whisper", "mlx-community/whisper-large-v3-turbo"),
                      ("openai", "gpt-5.6-luna"), ("mistral", "mistral-small-2603"),
                      ("mistral", "voxtral-mini-2602")]:
        assert c.execute("SELECT COUNT(*) FROM pricing_snapshot WHERE provider=? AND model=?",
                         (prov, mod)).fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM model_alias").fetchone()[0] == 1
    c.close()


# ----------------------- Best-effort : ne casse jamais ------------------------
def test_init_sur_chemin_impossible_ne_leve_pas(tmp_path, monkeypatch):
    d = tmp_path / "dir_not_file"
    d.mkdir()
    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(d))
    res = usage_store.initialize_catalogue()                # ne doit PAS lever
    assert res["ok"] is False


def test_init_ne_reprice_jamais(usage_db, monkeypatch):
    """L'init ne doit jamais appeler reprice() : on le fait exploser et l'init reste OK."""
    def _boom(*a, **k):
        raise AssertionError("initialize_catalogue ne doit PAS appeler reprice()")
    monkeypatch.setattr(usage_store, "reprice", _boom)
    res = usage_store.initialize_catalogue(usage_db)
    assert res["ok"] is True


def test_init_ne_touche_pas_le_cout_historique(usage_db):
    """Re-initialiser NE reprice PAS un appel déjà tarifé (linkage historique stable)."""
    with telemetry.execution(kind="report", provider="mlx",
                             model="mlx-community/Qwen3-4B-Instruct-2507-4bit"):
        with telemetry.stage("extract"):
            telemetry.record_model_call(provider="mlx",
                model="mlx-community/Qwen3-4B-Instruct-2507-4bit", latency_s=1.0,
                usage=_MlxUsage(100, 100), usage_source="local_server")
    usage_store.initialize_catalogue(usage_db)
    usage_store.reprice(usage_db)                            # tarifie une 1re fois
    c = usage_store.connect(usage_db)
    before = dict(c.execute("SELECT in_tok, out_tok, cost, pricing_snapshot_id FROM model_call").fetchone())
    c.close()
    assert before["cost"] is not None
    usage_store.initialize_catalogue(usage_db)              # re-init : ne doit RIEN repricer
    c = usage_store.connect(usage_db)
    after = dict(c.execute("SELECT in_tok, out_tok, cost, pricing_snapshot_id FROM model_call").fetchone())
    c.close()
    assert after == before                                  # faits + coût + snapshot intacts


# ----------------------- Coûts locaux ----------------------------------------
def test_mlx_cout_in_out_cache(usage_db):
    with telemetry.execution(kind="report", provider="mlx",
                             model="mlx-community/Qwen3-4B-Instruct-2507-4bit"):
        with telemetry.stage("extract"):
            telemetry.record_model_call(provider="mlx",
                model="mlx-community/Qwen3-4B-Instruct-2507-4bit", latency_s=1.0,
                usage=_MlxUsage(622, 608, cached=3), usage_source="local_server")
    usage_store.initialize_catalogue(usage_db)
    usage_store.reprice(usage_db)
    c = usage_store.connect(usage_db)
    row = c.execute("SELECT cost, cost_basis, cost_currency FROM model_call").fetchone()
    c.close()
    # (622-3)*0.10 + 3*0.02 + 608*0.30, /1e6
    assert row["cost"] == pytest.approx((619 * 0.10 + 3 * 0.02 + 608 * 0.30) / 1e6)
    assert row["cost_basis"] == "local_imputed" and row["cost_currency"] == "USD"


def test_whisper_cout_a_la_minute(usage_db):
    with telemetry.execution(kind="stt", provider="whisper",
                             model="mlx-community/whisper-large-v3-turbo", audio_seconds=90.0):
        pass
    usage_store.initialize_catalogue(usage_db)
    usage_store.reprice(usage_db)
    c = usage_store.connect(usage_db)
    ex = c.execute("SELECT cost, cost_basis, cost_currency FROM execution WHERE kind='stt'").fetchone()
    c.close()
    assert ex["cost"] == pytest.approx((90.0 / 60) * 0.0015)     # 1,5 min × 0,0015
    assert ex["cost_basis"] == "local_imputed" and ex["cost_currency"] == "USD"


def test_local_ne_fuit_pas_dans_le_cloud(usage_db):
    """Un appel cloud reste `cloud_derived` ; un appel local reste `local_imputed` ; agrégation
    par base = deux totaux distincts (jamais mélangés)."""
    class _CloudUsage:
        def __init__(self, i, o):
            self.input_tokens = i
            self.output_tokens = o
            self.input_tokens_details = {"cached_tokens": 0}
            self.output_tokens_details = None
    with telemetry.execution(kind="report", provider="openai", model="gpt-5.6-luna"):
        with telemetry.stage("extract"):
            telemetry.record_model_call(provider="openai", model="gpt-5.6-luna", latency_s=1.0,
                usage=_CloudUsage(100, 100), usage_source="provider_reported")
    with telemetry.execution(kind="report", provider="mlx",
                             model="mlx-community/Qwen3-4B-Instruct-2507-4bit"):
        with telemetry.stage("extract"):
            telemetry.record_model_call(provider="mlx",
                model="mlx-community/Qwen3-4B-Instruct-2507-4bit", latency_s=1.0,
                usage=_MlxUsage(100, 100), usage_source="local_server")
    usage_store.initialize_catalogue(usage_db)
    usage_store.reprice(usage_db)
    c = usage_store.connect(usage_db)
    bases = {r["provider"]: r["cost_basis"] for r in c.execute(
        "SELECT provider, cost_basis FROM model_call")}
    agg = {r["cost_basis"] for r in c.execute("SELECT DISTINCT cost_basis FROM model_call")}
    c.close()
    assert bases["openai"] == "cloud_derived" and bases["mlx"] == "local_imputed"
    assert agg == {"cloud_derived", "local_imputed"}


def test_pas_de_local_benchmark_dans_le_magasin(usage_db):
    usage_store.initialize_catalogue(usage_db)
    c = usage_store.connect(usage_db)
    assert c.execute("SELECT COUNT(*) FROM pricing_snapshot WHERE rate_source='local_benchmark'").fetchone()[0] == 0
    c.close()


# ----------------------- Temporalité des hypothèses locales (garde DURE) ------
def test_hypothese_locale_est_datee():
    """Les hypothèses locales portent une `effective_from` DATÉE (jamais NULL) : un import futur
    ne peut donc pas les supplanter rétroactivement (une v1 datée bat une v2 NULL)."""
    for e in pricing_official.LOCAL_ASSUMPTIONS:
        assert e.get("effective_from") == pricing_official.LOCAL_ASSUMED_AT
        assert e["effective_from"] is not None


def test_hypothese_locale_future_ne_reprice_pas_le_passe(usage_db):
    """Gate A/B/C/D : un appel local ancien ne prend JAMAIS un tarif local FUTUR.

    - v1 (0,10/0,30/0,02, effective 2026-08-09) tarifie l'appel ;
    - import d'une v2 FUTURE (effective 2099) : sans reprice, snapshot/coût inchangés ;
    - après reprice, toujours v1 (le tarif futur ne s'applique pas au passé) ; faits intacts.
    """
    mlx = "mlx-community/Qwen3-4B-Instruct-2507-4bit"
    with telemetry.execution(kind="report", provider="mlx", model=mlx):
        with telemetry.stage("extract"):
            telemetry.record_model_call(provider="mlx", model=mlx, latency_s=1.0,
                usage=_MlxUsage(1000, 1000, cached=100), usage_source="local_server")
    usage_store.initialize_catalogue(usage_db)
    usage_store.reprice(usage_db)
    c = usage_store.connect(usage_db)
    before = dict(c.execute("SELECT in_tok, out_tok, cached_tok, cost, pricing_snapshot_id FROM model_call").fetchone())
    assert before["cost"] == pytest.approx((900 * 0.10 + 100 * 0.02 + 1000 * 0.30) / 1e6)
    # v2 FUTURE
    usage_store.import_pricing(c, [{"provider": "mlx", "model": mlx, "kind": "local_imputed",
        "unit": "token", "currency": "USD", "input_rate": 9.0, "output_rate": 9.0,
        "cached_rate": 9.0, "rate_source": "configured_manual",
        "effective_from": "2099-01-01T00:00:00+00:00"}])
    c.commit()
    without = dict(c.execute("SELECT cost, pricing_snapshot_id FROM model_call").fetchone())
    c.close()
    assert without["cost"] == before["cost"] and without["pricing_snapshot_id"] == before["pricing_snapshot_id"]
    usage_store.reprice(usage_db)
    c = usage_store.connect(usage_db)
    after = dict(c.execute("SELECT in_tok, out_tok, cached_tok, cost, pricing_snapshot_id FROM model_call").fetchone())
    snap = usage_store.snapshot_by_id(c, after["pricing_snapshot_id"])
    # resolution par instant : 2026 -> v1 ; 2099 -> v2
    r_now = usage_store.effective_snapshot(c, provider="mlx", model=mlx, unit="token", at="2026-12-01T00:00:00+00:00")
    r_fut = usage_store.effective_snapshot(c, provider="mlx", model=mlx, unit="token", at="2099-06-01T00:00:00+00:00")
    c.close()
    assert after["pricing_snapshot_id"] == before["pricing_snapshot_id"] and after["cost"] == before["cost"]
    assert (after["in_tok"], after["out_tok"], after["cached_tok"]) == (1000, 1000, 100)  # faits intacts
    assert snap.input_rate == 0.10 and r_now.input_rate == 0.10 and r_fut.input_rate == 9.0
