"""Barème + dérivation de coût (FinOps) — auditable, jamais de coût fabriqué.

Invariants protégés :
- barèmes IMMUABLES et VERSIONNÉS ;
- coût REPRODUCTIBLE contre le barème utilisé (id stocké) + recalcul rétroactif ;
- OpenAI : `output` inclut le raisonnement (pas de double compte) ; cache facturé une fois ;
- Mistral : coût depuis les seuls champs mesurés ;
- local IMPUTÉ séparé du nuage ACTUAL, jamais mélangés ;
- Whisper : unité non-jeton (seconde audio) supportée sans inventer de jetons ;
- barème absent ⇒ coût NULL (jamais un faux zéro) ;
- une panne barème/coût ne casse JAMAIS la persistance des faits ni la génération.
"""
from __future__ import annotations

import pytest

from core import telemetry, usage_store, usage_cost


@pytest.fixture()
def usage_db(tmp_path, monkeypatch):
    p = tmp_path / "usage.db"
    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(p))
    return p


class _Usage:
    """Objet d'usage type OpenAI Responses (in/out + détails cached/reasoning)."""

    def __init__(self, i, o, cached=0, reasoning=None):
        self.input_tokens = i
        self.output_tokens = o
        self.input_tokens_details = {"cached_tokens": cached}
        self.output_tokens_details = ({"reasoning_tokens": reasoning}
                                      if reasoning is not None else None)


def _record(kind="report", provider="openai", model="m", **usage_kwargs):
    with telemetry.execution(kind=kind, provider=provider, model=model, document_id="D"):
        with telemetry.stage("extract"):
            telemetry.record_model_call(
                provider=provider, model=model, latency_s=1.0,
                usage=_Usage(**usage_kwargs), usage_source="provider_reported")


# ----------------------- Fonctions PURES de coût -----------------------------
def test_token_cost_reasoning_non_double_compte():
    # output inclut déjà le raisonnement -> on facture output, pas output+reasoning
    snap = usage_cost.PricingSnapshot(
        "s", "openai", "m", "cloud_billed", "token", "USD",
        input_rate=1.0, output_rate=2.0, cached_rate=0.5)
    # in=1000 (dont 200 en cache), out=100 (dont 40 de raisonnement)
    cost = usage_cost.token_cost(in_tok=1000, out_tok=100, cached_tok=200, snap=snap)
    # 800*1 + 200*0.5 + 100*2 = 1100 /1e6
    assert abs(cost - 0.0011) < 1e-15
    # Le raisonnement N'entre PAS dans le calcul : varier reasoning ne change rien
    # (il n'est même pas un paramètre de token_cost).


def test_token_cost_cache_par_defaut_au_tarif_entree():
    snap = usage_cost.PricingSnapshot("s", "p", "m", "cloud_billed", "token", "USD",
                                      input_rate=3.0, output_rate=6.0, cached_rate=None)
    cost = usage_cost.token_cost(in_tok=100, out_tok=10, cached_tok=40, snap=snap)
    # cached_rate absent -> tarif d'entrée : (60*3 + 40*3 + 10*6)=180+60=... /1e6
    assert abs(cost - (100 * 3.0 + 10 * 6.0) / 1e6) < 1e-15


def test_unit_cost_non_jeton():
    snap = usage_cost.PricingSnapshot("s", "whisper", "w", "local_imputed",
                                      "audio_second", "EUR", unit_rate=0.0001)
    assert usage_cost.unit_cost(quantity=30.0, snap=snap) == pytest.approx(0.003)
    # un barème jeton ne price PAS une unité, et inversement
    assert usage_cost.unit_cost(quantity=30.0, snap=usage_cost.PricingSnapshot(
        "s", "p", "m", "cloud_billed", "token", "USD", input_rate=1.0, output_rate=1.0)) is None


def test_basis_mapping_cloud_vs_local():
    assert usage_cost.cost_basis_for("cloud_billed") == "cloud_derived"
    assert usage_cost.cost_basis_for("local_imputed") == "local_imputed"


def test_cout_nuage_est_derive_PAS_actual_ni_reconcilie():
    """Vérité d'usage ≠ vérité de coût : un coût dérivé d'un catalogue n'est PAS
    un coût facturé réconcilié. La base ne doit jamais se nommer « actual »."""
    b = usage_cost.cost_basis_for("cloud_billed")
    assert b == "cloud_derived"
    assert "actual" not in b and b != "invoice_reconciled"
    assert usage_cost.BASIS_CLOUD_DERIVED == "cloud_derived"
    assert not hasattr(usage_cost, "BASIS_CLOUD_ACTUAL")   # l'ancien nom a disparu


def test_cout_none_sans_bareme_ou_incomplet():
    assert usage_cost.token_cost(in_tok=10, out_tok=1, cached_tok=0, snap=None) is None
    incomplet = usage_cost.PricingSnapshot("s", "p", "m", "cloud_billed", "token", "USD",
                                           input_rate=None, output_rate=2.0)
    assert usage_cost.token_cost(in_tok=10, out_tok=1, cached_tok=0, snap=incomplet) is None


# ----------------------- Barème immuable / versionné -------------------------
def test_snapshots_immuables_et_versionnes(usage_db):
    c = usage_store.connect(usage_db)
    a = usage_store.insert_pricing_snapshot(c, provider="openai", model="m",
        kind="cloud_billed", currency="USD", input_rate=1.0, output_rate=2.0, source="v1")
    b = usage_store.insert_pricing_snapshot(c, provider="openai", model="m",
        kind="cloud_billed", currency="USD", input_rate=1.5, output_rate=3.0, source="v2")
    c.commit()
    rows = c.execute("SELECT pricing_snapshot_id,version,input_rate FROM pricing_snapshot "
                     "WHERE provider='openai' AND model='m' AND unit='token' ORDER BY version").fetchall()
    c.close()
    assert [r["version"] for r in rows] == [1, 2]           # versionné
    assert a != b                                            # deux lignes distinctes
    assert rows[0]["input_rate"] == 1.0 and rows[1]["input_rate"] == 1.5  # v1 jamais mutée


def test_reproductibilite_par_snapshot_id(usage_db):
    _record(provider="mistral", model="mistral-small", i=500, o=200)
    c = usage_store.connect(usage_db)
    sid = usage_store.insert_pricing_snapshot(c, provider="mistral", model="mistral-small",
        kind="cloud_billed", currency="EUR", input_rate=0.2, output_rate=0.6, source="ref")
    c.commit()
    c.close()
    usage_store.reprice(usage_db)
    c = usage_store.connect(usage_db)
    call = c.execute("SELECT cost,pricing_snapshot_id FROM model_call").fetchone()
    snap = usage_store.snapshot_by_id(c, call["pricing_snapshot_id"])
    c.close()
    assert call["pricing_snapshot_id"] == sid
    # recalcul à la main depuis le snapshot stocké -> identique (reproductible)
    recomputed = usage_cost.token_cost(in_tok=500, out_tok=200, cached_tok=0, snap=snap)
    assert abs(call["cost"] - recomputed) < 1e-15
    assert abs(call["cost"] - (500 * 0.2 + 200 * 0.6) / 1e6) < 1e-15


def test_reprice_retroactif_ne_touche_pas_aux_faits(usage_db):
    _record(provider="openai", model="m", i=1000, o=100, cached=0, reasoning=40)
    c = usage_store.connect(usage_db)
    usage_store.insert_pricing_snapshot(c, provider="openai", model="m", kind="cloud_billed",
        currency="USD", input_rate=1.0, output_rate=2.0,
        effective_from="2020-01-01T00:00:00+00:00", source="v1")
    c.commit()
    c.close()
    usage_store.reprice(usage_db)
    c = usage_store.connect(usage_db)
    before = c.execute("SELECT in_tok,out_tok,reasoning_tok,cost FROM model_call").fetchone()
    c.close()
    assert abs(before["cost"] - (1000 * 1.0 + 100 * 2.0) / 1e6) < 1e-15
    # barème corrigé (toujours dans le passé) -> recalcul rétroactif
    c = usage_store.connect(usage_db)
    usage_store.insert_pricing_snapshot(c, provider="openai", model="m", kind="cloud_billed",
        currency="USD", input_rate=2.0, output_rate=4.0,
        effective_from="2020-06-01T00:00:00+00:00", source="v2 corrigé")
    c.commit()
    c.close()
    usage_store.reprice(usage_db)
    c = usage_store.connect(usage_db)
    after = c.execute("SELECT in_tok,out_tok,reasoning_tok,cost FROM model_call").fetchone()
    c.close()
    # FAITS d'usage inchangés ; seul le coût bouge
    assert (after["in_tok"], after["out_tok"], after["reasoning_tok"]) == \
           (before["in_tok"], before["out_tok"], before["reasoning_tok"])
    assert abs(after["cost"] - (1000 * 2.0 + 100 * 4.0) / 1e6) < 1e-15


# ----------------------- Cloud actual vs local imputed -----------------------
def test_local_impute_separe_du_cloud_derived(usage_db):
    _record(provider="openai", model="luna", i=100, o=50)     # cloud
    _record(provider="mlx", model="qwen", i=300, o=200)       # local
    c = usage_store.connect(usage_db)
    usage_store.insert_pricing_snapshot(c, provider="openai", model="luna", kind="cloud_billed",
        currency="USD", input_rate=1.0, output_rate=2.0, source="cloud")
    usage_store.insert_pricing_snapshot(c, provider="mlx", model="qwen", kind="local_imputed",
        currency="USD", input_rate=0.1, output_rate=0.1, source="tarif local — non ratifié")
    c.commit()
    c.close()
    usage_store.reprice(usage_db)
    c = usage_store.connect(usage_db)
    rows = {r["provider"]: dict(r) for r in c.execute(
        "SELECT provider,cost_basis FROM model_call")}
    # agrégation par base : deux totaux distincts, jamais additionnés en un seul
    agg = {r["cost_basis"]: r["n"] for r in c.execute(
        "SELECT cost_basis, COUNT(*) n FROM model_call GROUP BY cost_basis")}
    c.close()
    assert rows["openai"]["cost_basis"] == "cloud_derived"
    assert rows["mlx"]["cost_basis"] == "local_imputed"
    assert set(agg) == {"cloud_derived", "local_imputed"}


def test_devises_jamais_additionnees_dans_un_total(usage_db):
    """USD et EUR ne sont JAMAIS sommés en un seul montant (aucune conversion de change).

    Deux appels de devises différentes dans UNE exécution : les coûts PAR APPEL restent
    écrits dans leur devise, mais le rollup d'exécution REFUSE de les additionner (NULL,
    base `mixed`).
    """
    with telemetry.execution(kind="report", provider="mixte", model="m", document_id="D"):
        with telemetry.stage("a"):
            telemetry.record_model_call(provider="openai", model="luna", latency_s=1.0,
                usage=_Usage(100, 50), usage_source="provider_reported")
        with telemetry.stage("b"):
            telemetry.record_model_call(provider="mistral", model="small", latency_s=1.0,
                usage=_Usage(100, 50), usage_source="provider_reported")
    c = usage_store.connect(usage_db)
    usage_store.insert_pricing_snapshot(c, provider="openai", model="luna", kind="cloud_billed",
        currency="USD", input_rate=1.0, output_rate=2.0, source="usd")
    usage_store.insert_pricing_snapshot(c, provider="mistral", model="small", kind="cloud_billed",
        currency="EUR", input_rate=1.0, output_rate=2.0, source="eur")
    c.commit()
    c.close()
    usage_store.reprice(usage_db)
    c = usage_store.connect(usage_db)
    per_call = {r["cost_currency"]: r["cost"] for r in c.execute(
        "SELECT cost_currency, cost FROM model_call")}
    ex = c.execute("SELECT cost, cost_currency, cost_basis FROM execution").fetchone()
    c.close()
    # Coûts par appel présents, chacun dans SA devise
    assert set(per_call) == {"USD", "EUR"} and all(v is not None for v in per_call.values())
    # Rollup d'exécution : PAS de somme inter-devises
    assert ex["cost"] is None and ex["cost_currency"] is None and ex["cost_basis"] == "mixed"


# ----------------------- Whisper : unité non-jeton ---------------------------
def test_whisper_audio_second(usage_db):
    with telemetry.execution(kind="stt", provider="whisper",
                             model="whisper-large-v3-turbo", audio_seconds=30.0):
        pass
    c = usage_store.connect(usage_db)
    usage_store.insert_pricing_snapshot(c, provider="whisper", model="whisper-large-v3-turbo",
        kind="local_imputed", currency="EUR", unit="audio_second", unit_rate=0.0001,
        source="placeholder local — non ratifié")
    c.commit()
    c.close()
    usage_store.reprice(usage_db)
    c = usage_store.connect(usage_db)
    st = c.execute("SELECT audio_seconds,cost,cost_basis,cost_currency FROM execution "
                   "WHERE kind='stt'").fetchone()
    c.close()
    assert st["audio_seconds"] == 30.0
    assert st["cost"] == pytest.approx(0.003) and st["cost_basis"] == "local_imputed"


# ----------------------- Barème absent -> NULL, pas 0 ------------------------
def test_bareme_absent_cout_null_pas_zero(usage_db):
    _record(provider="inconnu", model="x", i=100, o=100)   # aucun barème pour ce fournisseur
    c = usage_store.connect(usage_db)
    call = c.execute("SELECT cost,cost_basis,pricing_snapshot_id FROM model_call").fetchone()
    ex = c.execute("SELECT cost FROM execution").fetchone()
    c.close()
    assert call["cost"] is None and call["cost_basis"] is None and call["pricing_snapshot_id"] is None
    assert ex["cost"] is None            # NULL, jamais 0.0


# ----------------------- Fiabilité : coût ne casse jamais --------------------
def test_bareme_incomplet_cout_null_faits_intacts(usage_db):
    _record(provider="mistral", model="m", i=100, o=50)
    c = usage_store.connect(usage_db)
    # barème 'token' SANS tarifs -> token_cost renvoie None
    usage_store.insert_pricing_snapshot(c, provider="mistral", model="m", kind="cloud_billed",
        currency="EUR", input_rate=None, output_rate=None, source="incomplet")
    c.commit()
    c.close()
    usage_store.reprice(usage_db)
    c = usage_store.connect(usage_db)
    row = c.execute("SELECT in_tok,out_tok,cost FROM model_call").fetchone()
    c.close()
    assert (row["in_tok"], row["out_tok"]) == (100, 50)   # faits intacts
    assert row["cost"] is None                            # coût inconnu, pas faux


def test_pricing_qui_leve_ne_casse_pas_la_persistance(usage_db, monkeypatch):
    # _price_execution qui explose ne doit PAS empêcher l'écriture des faits
    def _boom(conn, execution_id):
        raise RuntimeError("barème corrompu simulé")
    monkeypatch.setattr(usage_store, "_price_execution", _boom)
    _record(provider="mistral", model="m", i=10, o=5)     # ne doit pas lever
    c = usage_store.connect(usage_db)
    row = c.execute("SELECT in_tok, out_tok FROM model_call").fetchone()
    ncalls = c.execute("SELECT n_calls FROM execution").fetchone()["n_calls"]
    c.close()
    assert row["in_tok"] == 10 and row["out_tok"] == 5 and ncalls == 1  # faits persistés


def test_reprice_sur_base_injoignable_ne_leve_pas(tmp_path, monkeypatch):
    d = tmp_path / "not_a_file"
    d.mkdir()
    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(d))
    res = usage_store.reprice()          # ne doit pas lever
    assert res["errors"] >= 1


def test_kind_bareme_invalide_refuse(usage_db):
    c = usage_store.connect(usage_db)
    with pytest.raises(ValueError):
        usage_store.insert_pricing_snapshot(c, provider="p", model="m", kind="bidon",
            currency="USD", input_rate=1.0, output_rate=1.0)
    c.close()
