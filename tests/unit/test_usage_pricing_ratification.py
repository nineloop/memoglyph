"""Ratification de tarifs (FinOps phase 1B) — provenance, bootstrap explicite, aucun tarif inventé.

Invariants protégés :
- provenance structurée (`rate_source` + `source_url`/`retrieved_at` pour l'officiel) ;
- barème VIDE par défaut ; ne se peuple que par un bootstrap/import EXPLICITE ;
- import IDEMPOTENT, versionné, jamais d'écrasement ;
- modèle non ratifié (fictif/alias/ambigu) ⇒ coût NULL, jamais un tarif substitué ;
- sémantique OpenAI cache/raisonnement (pas de double compte) sur tarif officiel ;
- AUCUN tarif monétaire local par défaut ;
- reprice reproductible, provenance conservée.
"""
from __future__ import annotations

import pytest

from core import telemetry, usage_store, pricing_official


@pytest.fixture()
def usage_db(tmp_path, monkeypatch):
    p = tmp_path / "usage.db"
    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(p))
    return p


class _Usage:
    def __init__(self, i, o, cached=0, reasoning=None):
        self.input_tokens = i
        self.output_tokens = o
        self.input_tokens_details = {"cached_tokens": cached}
        self.output_tokens_details = ({"reasoning_tokens": reasoning}
                                      if reasoning is not None else None)


# ----------------------- Manifeste : cohérence ratifié / non ratifié ----------
def test_manifeste_ne_contient_que_des_modeles_ratifies():
    priced = {(e["provider"], e["model"]) for e in pricing_official.OFFICIAL_RATES}
    aliased = {(e["provider"], e["observed_model"]) for e in pricing_official.OFFICIAL_ALIASES}
    for key, info in pricing_official.RATIFICATION.items():
        provider, model = key.split("/", 1)
        if info["ratified"]:
            # ratifié = tarifé DIRECTEMENT ou résolu via un alias AUDITÉ (jamais ni l'un ni l'autre)
            assert (provider, model) in priced or (provider, model) in aliased, \
                f"{key} ratifié mais ni tarifé ni aliasé"
            if info.get("via") == "alias":
                assert (provider, model) in aliased and (provider, model) not in priced
        else:
            assert (provider, model) not in priced and (provider, model) not in aliased, \
                f"{key} NON ratifié mais tarifé/aliasé !"
    # Direct : gpt-5.6-luna, voxtral-mini-2602, mistral-small-2603. Via alias : mistral-small-latest.
    assert ("openai", "gpt-5.6-luna") in priced
    assert ("mistral", "voxtral-mini-2602") in priced
    assert ("mistral", "mistral-small-2603") in priced
    assert ("mistral", "mistral-small-latest") in aliased
    assert ("mistral", "mistral-small-latest") not in priced   # jamais un tarif direct sur l'alias


def test_officiel_porte_source_et_date():
    for e in pricing_official.OFFICIAL_RATES:
        assert e["rate_source"] == "official_published"
        assert e["source_url"].startswith("http") and e["retrieved_at"].startswith("20")


# ----------------------- Provenance : validations ------------------------------
def test_rate_source_enum_valide(usage_db):
    c = usage_store.connect(usage_db)
    with pytest.raises(ValueError):
        usage_store.insert_pricing_snapshot(c, provider="p", model="m", kind="cloud_billed",
            currency="USD", input_rate=1.0, output_rate=1.0, rate_source="bidon")
    c.close()


def test_officiel_exige_source_url_et_date(usage_db):
    c = usage_store.connect(usage_db)
    with pytest.raises(ValueError):
        usage_store.insert_pricing_snapshot(c, provider="p", model="m", kind="cloud_billed",
            currency="USD", input_rate=1.0, output_rate=1.0, rate_source="official_published")
    c.close()


def test_provenance_stockee(usage_db):
    c = usage_store.connect(usage_db)
    usage_store.insert_pricing_snapshot(c, provider="openai", model="gpt-5.6-luna",
        kind="cloud_billed", currency="USD", input_rate=0.2, output_rate=1.2, cached_rate=0.02,
        rate_source="official_published", source_url="https://x", retrieved_at="2026-08-09")
    c.commit()
    row = c.execute("SELECT rate_source, source_url, retrieved_at FROM pricing_snapshot").fetchone()
    c.close()
    assert row["rate_source"] == "official_published"
    assert row["source_url"] == "https://x" and row["retrieved_at"] == "2026-08-09"


# ----------------------- Bootstrap explicite / idempotent ---------------------
def test_fresh_db_vide_puis_bootstrap_explicite(usage_db):
    c = usage_store.connect(usage_db)
    assert c.execute("SELECT COUNT(*) FROM pricing_snapshot").fetchone()[0] == 0  # vide par défaut
    r1 = usage_store.bootstrap_official_pricing(c)
    c.commit()
    assert r1["imported"] == len(pricing_official.OFFICIAL_RATES)
    r2 = usage_store.bootstrap_official_pricing(c)   # idempotent (tarifs ET alias)
    c.commit()
    assert r2["imported"] == 0 and r2["skipped"] == len(pricing_official.OFFICIAL_RATES)
    assert r2["aliases_imported"] == 0
    assert c.execute("SELECT COUNT(*) FROM pricing_snapshot").fetchone()[0] == len(pricing_official.OFFICIAL_RATES)
    c.close()


def test_import_change_versionne_sans_ecraser(usage_db):
    c = usage_store.connect(usage_db)
    usage_store.bootstrap_official_pricing(c)
    c.commit()
    usage_store.import_pricing(c, [{"provider": "openai", "model": "gpt-5.6-luna",
        "kind": "cloud_billed", "unit": "token", "currency": "USD", "input_rate": 0.25,
        "output_rate": 1.2, "cached_rate": 0.02, "rate_source": "official_published",
        "source_url": "https://x", "retrieved_at": "2026-09-01"}])
    c.commit()
    rows = c.execute("SELECT version, input_rate FROM pricing_snapshot "
                     "WHERE model='gpt-5.6-luna' ORDER BY version").fetchall()
    c.close()
    assert [(r["version"], r["input_rate"]) for r in rows] == [(1, 0.2), (2, 0.25)]  # v1 intacte


# ----------------------- Alias observé -> identité de tarification -------------
def _record_mistral_chat(model="mistral-small-latest", i=300, o=100):
    with telemetry.execution(kind="report", provider="mistral", model=model):
        with telemetry.stage("extract"):
            telemetry.record_model_call(provider="mistral", model=model,
                latency_s=0.5, usage=_Usage(i, o), usage_source="provider_reported")


def test_alias_mistral_small_latest_price_via_2603(usage_db):
    """L'alias `mistral-small-latest` est tarifé via l'identité `mistral-small-2603` — mais le
    `model` enregistré RESTE l'alias (jamais réécrit)."""
    _record_mistral_chat(i=300, o=100)
    c = usage_store.connect(usage_db)
    usage_store.bootstrap_official_pricing(c)
    c.commit()
    c.close()
    usage_store.reprice(usage_db)
    c = usage_store.connect(usage_db)
    call = c.execute("SELECT model, cost, cost_currency, cost_basis, pricing_snapshot_id "
                     "FROM model_call").fetchone()
    snap = usage_store.snapshot_by_id(c, call["pricing_snapshot_id"])
    c.close()
    assert call["model"] == "mistral-small-latest"            # OBSERVÉ, jamais réécrit
    assert call["cost"] == pytest.approx((300 * 0.15 + 100 * 0.60) / 1e6)
    assert call["cost_currency"] == "USD" and call["cost_basis"] == "cloud_derived"
    assert snap.model == "mistral-small-2603"                 # identité de TARIFICATION


def test_resolution_alias_exacte_seulement(usage_db):
    """Correspondance EXACTE : seul `mistral-small-latest` résout ; aucun autre modèle n'hérite."""
    c = usage_store.connect(usage_db)
    usage_store.bootstrap_official_pricing(c)
    c.commit()
    assert usage_store.resolve_pricing_model(
        c, provider="mistral", observed_model="mistral-small-latest", at=None) == "mistral-small-2603"
    for other in ("mistral-medium-latest", "mistral-small", "mistral-small-2603", "mistral-large-latest"):
        assert usage_store.resolve_pricing_model(
            c, provider="mistral", observed_model=other, at=None) == other   # identité
    # même nom, AUTRE provider : pas de résolution croisée
    assert usage_store.resolve_pricing_model(
        c, provider="openai", observed_model="mistral-small-latest", at=None) == "mistral-small-latest"
    c.close()


def test_modele_non_mappe_reste_null(usage_db):
    """Un modèle Mistral SANS alias ni tarif direct reste NULL (pas de substitution)."""
    _record_mistral_chat(model="mistral-medium-latest", i=100, o=100)
    c = usage_store.connect(usage_db)
    usage_store.bootstrap_official_pricing(c)
    c.commit()
    c.close()
    usage_store.reprice(usage_db)
    c = usage_store.connect(usage_db)
    row = c.execute("SELECT cost, pricing_snapshot_id FROM model_call").fetchone()
    c.close()
    assert row["cost"] is None and row["pricing_snapshot_id"] is None


def test_alias_null_sans_bootstrap(usage_db):
    """Sans import explicite, ni alias ni tarif : l'appel reste NULL (fresh DB vide)."""
    _record_mistral_chat()
    c = usage_store.connect(usage_db)
    assert c.execute("SELECT COUNT(*) FROM model_alias").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM pricing_snapshot").fetchone()[0] == 0
    c.close()
    usage_store.reprice(usage_db)
    c = usage_store.connect(usage_db)
    assert c.execute("SELECT cost FROM model_call").fetchone()["cost"] is None
    c.close()


def test_alias_immuable_versionne_et_provenance(usage_db):
    """Alias IMMUABLE/versionné/provenancé : ré-import identique = no-op ; cible changée =
    nouvelle version (l'ancienne intacte) ; provenance (source/url/date) conservée."""
    c = usage_store.connect(usage_db)
    r1 = usage_store.bootstrap_official_pricing(c)
    c.commit()
    r2 = usage_store.bootstrap_official_pricing(c)            # idempotent
    c.commit()
    assert r1["aliases_imported"] == 1 and r2["aliases_imported"] == 0 and r2["aliases_skipped"] == 1
    row = c.execute("SELECT source, source_url, retrieved_at, version FROM model_alias "
                    "WHERE observed_model='mistral-small-latest'").fetchone()
    assert row["source"] and row["source_url"].startswith("https://docs.mistral.ai") and row["retrieved_at"]
    # cible changée -> nouvelle version, ancienne immuable
    usage_store.import_aliases(c, [{"provider": "mistral", "observed_model": "mistral-small-latest",
        "pricing_model": "mistral-small-2699", "source": "test",
        "source_url": "https://x", "retrieved_at": "2027-01-01"}])
    c.commit()
    versions = c.execute("SELECT version, pricing_model FROM model_alias "
                         "WHERE observed_model='mistral-small-latest' ORDER BY version").fetchall()
    c.close()
    assert [(v["version"], v["pricing_model"]) for v in versions] == \
        [(1, "mistral-small-2603"), (2, "mistral-small-2699")]


def test_alias_source_obligatoire(usage_db):
    c = usage_store.connect(usage_db)
    with pytest.raises(ValueError):
        usage_store.insert_model_alias(c, provider="mistral", observed_model="x",
            pricing_model="y", source="")
    c.close()


# ----------------------- Temporalité de l'alias (garde DURE) ------------------
def test_alias_toujours_date_jamais_null(usage_db):
    """Un alias porte TOUJOURS une `effective_from` (jamais NULL) : sans date -> instant d'import.
    Deux versions ne peuvent donc jamais se départager par le seul numéro (résolution temporelle)."""
    c = usage_store.connect(usage_db)
    usage_store.bootstrap_official_pricing(c)
    # le barème shippé porte une date PASSÉE explicite
    shipped = c.execute("SELECT effective_from FROM model_alias "
                        "WHERE observed_model='mistral-small-latest'").fetchone()
    assert shipped["effective_from"] == pricing_official.RETRIEVED_AT
    # un import sans date -> non NULL (instant d'import)
    usage_store.insert_model_alias(c, provider="p", observed_model="o", pricing_model="q",
        source="test")
    row = c.execute("SELECT effective_from FROM model_alias WHERE observed_model='o'").fetchone()
    c.close()
    assert row["effective_from"] is not None


def test_resolution_par_instant_dappel(usage_db):
    """Gate A/B : la résolution utilise le mapping EN VIGUEUR à l'instant `at`, pas le plus récent."""
    c = usage_store.connect(usage_db)
    usage_store.bootstrap_official_pricing(c)                    # v1 latest->2603 @ 2026-08-09
    usage_store.import_aliases(c, [{"provider": "mistral", "observed_model": "mistral-small-latest",
        "pricing_model": "mistral-FUTURE", "source": "futur",
        "effective_from": "2099-01-01T00:00:00+00:00"}])         # v2 latest->FUTURE @ 2099
    c.commit()
    # avant la date de v2 -> v1 ; après -> v2 ; le simple « plus récent » ne gouverne pas
    assert usage_store.resolve_pricing_model(c, provider="mistral",
        observed_model="mistral-small-latest", at="2026-12-31T00:00:00+00:00") == "mistral-small-2603"
    assert usage_store.resolve_pricing_model(c, provider="mistral",
        observed_model="mistral-small-latest", at="2099-06-01T00:00:00+00:00") == "mistral-FUTURE"
    c.close()


def test_re_ciblage_futur_ne_reprice_pas_le_passe(usage_db):
    """Gate B/D/E, scénario T1/T2 : un appel ancien ne résout JAMAIS via un mapping futur.

    T1 : appel sous l'alias, tarifé via snapshot A (2603).
    T2 : import d'un re-ciblage FUTUR + d'un nouveau barème.
    Sans reprice : modèle observé, snapshot A et coût INCHANGÉS.
    Avec reprice : toujours snapshot A (le mapping futur ne s'applique pas au passé) ; faits
    d'usage jamais réécrits.
    """
    # T1
    with telemetry.execution(kind="report", provider="mistral", model="mistral-small-latest"):
        with telemetry.stage("extract"):
            telemetry.record_model_call(provider="mistral", model="mistral-small-latest",
                latency_s=0.5, usage=_Usage(300, 100), usage_source="provider_reported")
    c = usage_store.connect(usage_db)
    usage_store.bootstrap_official_pricing(c)
    c.commit()
    c.close()
    usage_store.reprice(usage_db)
    c = usage_store.connect(usage_db)
    t1 = dict(c.execute("SELECT model, in_tok, out_tok, cost, pricing_snapshot_id FROM model_call").fetchone())
    c.close()
    assert t1["cost"] == pytest.approx((300 * 0.15 + 100 * 0.60) / 1e6)

    # T2 : re-ciblage futur + nouveau barème pour la cible future
    c = usage_store.connect(usage_db)
    usage_store.import_aliases(c, [{"provider": "mistral", "observed_model": "mistral-small-latest",
        "pricing_model": "mistral-FUTURE", "source": "futur",
        "effective_from": "2099-01-01T00:00:00+00:00"}])
    usage_store.import_pricing(c, [{"provider": "mistral", "model": "mistral-FUTURE",
        "kind": "cloud_billed", "unit": "token", "currency": "USD", "input_rate": 9.0,
        "output_rate": 9.0, "rate_source": "official_published", "source_url": "https://x",
        "retrieved_at": "2099-01-01"}])
    c.commit()
    # SANS reprice : rien ne bouge
    without = dict(c.execute("SELECT model, cost, pricing_snapshot_id FROM model_call").fetchone())
    c.close()
    assert without == {"model": t1["model"], "cost": t1["cost"],
                       "pricing_snapshot_id": t1["pricing_snapshot_id"]}

    # AVEC reprice : re-résolution à l'instant d'appel (passé) -> toujours 2603 ; faits intacts
    usage_store.reprice(usage_db)
    c = usage_store.connect(usage_db)
    after = dict(c.execute("SELECT model, in_tok, out_tok, cost, pricing_snapshot_id FROM model_call").fetchone())
    snap = usage_store.snapshot_by_id(c, after["pricing_snapshot_id"])
    c.close()
    assert after["model"] == "mistral-small-latest"                 # observé, jamais réécrit
    assert (after["in_tok"], after["out_tok"]) == (300, 100)        # faits d'usage intacts
    assert after["cost"] == t1["cost"]                              # coût inchangé
    assert after["pricing_snapshot_id"] == t1["pricing_snapshot_id"]  # même snapshot A
    assert snap.model == "mistral-small-2603"                       # jamais mistral-FUTURE


def test_voxtral_2602_ratifie_prix_a_la_minute(usage_db):
    """`voxtral-mini-2602` (id DÉJÀ épinglé) est ratifié : STT tarifé à la MINUTE d'audio,
    base `cloud_derived`, USD. La durée mesurée (secondes) est convertie en minutes."""
    with telemetry.execution(kind="stt", provider="mistral", model="voxtral-mini-2602",
                             audio_seconds=90.0):
        with telemetry.stage("stt"):
            telemetry.record_model_call(provider="mistral", model="voxtral-mini-2602",
                latency_s=1.0, usage={"seconds": 90.0}, usage_source="provider_reported")
    c = usage_store.connect(usage_db)
    usage_store.bootstrap_official_pricing(c)
    c.commit()
    c.close()
    usage_store.reprice(usage_db)
    c = usage_store.connect(usage_db)
    ex = c.execute("SELECT audio_seconds, cost, cost_currency, cost_basis, pricing_snapshot_id "
                   "FROM execution WHERE kind='stt'").fetchone()
    snap = c.execute("SELECT unit, unit_rate, rate_source, source_url, retrieved_at "
                     "FROM pricing_snapshot WHERE model='voxtral-mini-2602'").fetchone()
    c.close()
    # 90 s = 1,5 min ; 1,5 × 0,003 = 0,0045 USD
    assert ex["cost"] == pytest.approx(0.0045) and ex["cost_currency"] == "USD"
    assert ex["cost_basis"] == "cloud_derived" and ex["pricing_snapshot_id"] is not None
    assert snap["unit"] == "audio_minute" and snap["unit_rate"] == 0.003
    assert snap["rate_source"] == "official_published"
    assert snap["source_url"].startswith("https://docs.mistral.ai") and snap["retrieved_at"]


def test_config_identifiants_inchanges(usage_db):
    """Sécurité historique : aucun identifiant configuré n'est modifié en phase 1C.
    voxtral était DÉJÀ épinglé (rien à changer). Le chat est passé de l'alias
    mistral-small-latest, refusé au palier par défaut (plafond nul), à l'id épinglé
    ministral-14b-2512 : un changement de produit délibéré, qui ne réécrit aucune
    télémétrie (l'alias reste résolu pour l'usage déjà enregistré)."""
    from core import config
    assert config.MISTRAL_STT_MODEL == "voxtral-mini-2602"
    assert config.MISTRAL_CHAT_MODEL == "ministral-14b-2512"
    assert "-latest" not in config.MISTRAL_CHAT_MODEL


def test_le_chat_par_defaut_a_un_tarif_a_confirmer_jamais_importe(usage_db):
    """Le tarif de ministral-14b-2512 n'a pas été relevé sur la grille officielle : il est
    consigné, mais ni importé ni ratifié, donc son coût reste NULL tant qu'il n'est pas promu."""
    from core import config
    cle = ("mistral", config.MISTRAL_CHAT_MODEL_DEFAUT)
    a_confirmer = {(e["provider"], e["model"]): e for e in pricing_official.RATES_TO_CONFIRM}
    priced = {(e["provider"], e["model"]) for e in pricing_official.OFFICIAL_RATES}
    assert cle in a_confirmer and cle not in priced
    entree = a_confirmer[cle]
    assert (entree["unit"], entree["currency"]) == ("token", "USD")
    assert entree["input_rate"] == 0.20 and entree["output_rate"] == 0.20
    assert entree["retrieved_at"] is None and "CONFIRMER" in entree["source"]
    assert pricing_official.RATIFICATION["mistral/ministral-14b-2512"]["ratified"] is False
    # Même forme qu'un tarif officiel : la promotion n'aura qu'à dater le relevé.
    officiel = next(e for e in pricing_official.OFFICIAL_RATES if e["model"] == "mistral-small-2603")
    assert set(entree) == set(officiel)
    # Le bootstrap ne le voit pas : aucun tarif de ce modèle dans une usage.db neuve.
    c = usage_store.connect(usage_db)
    usage_store.bootstrap_official_pricing(c)
    c.commit()
    assert c.execute("SELECT COUNT(*) FROM pricing_snapshot WHERE model=?",
                     (config.MISTRAL_CHAT_MODEL_DEFAUT,)).fetchone()[0] == 0
    c.close()


def test_cout_historique_reste_lie_au_snapshot_sauf_reprice(usage_db):
    """Un coût déjà calculé NE bouge PAS quand un nouveau barème est importé — tant que
    `reprice()` n'est pas invoqué explicitement. Le tarif officiel est une preuve DATÉE."""
    with telemetry.execution(kind="report", provider="openai", model="gpt-5.6-luna"):
        with telemetry.stage("extract"):
            telemetry.record_model_call(provider="openai", model="gpt-5.6-luna", latency_s=1.0,
                usage=_Usage(547, 452, reasoning=68), usage_source="provider_reported")
    c = usage_store.connect(usage_db)
    usage_store.bootstrap_official_pricing(c)      # v1 : 0.20 / 1.20
    c.commit()
    c.close()
    usage_store.reprice(usage_db)                  # price la call @v1
    c = usage_store.connect(usage_db)
    priced_v1 = dict(c.execute("SELECT cost, pricing_snapshot_id FROM model_call").fetchone())
    # importe un tarif CHANGÉ (nouvelle version) — SANS reprice
    usage_store.import_pricing(c, [{"provider": "openai", "model": "gpt-5.6-luna",
        "kind": "cloud_billed", "unit": "token", "currency": "USD", "input_rate": 0.30,
        "output_rate": 1.50, "cached_rate": 0.03, "rate_source": "official_published",
        "source_url": "https://x", "retrieved_at": "2026-12-01"}])
    c.commit()
    stable = dict(c.execute("SELECT cost, pricing_snapshot_id FROM model_call").fetchone())
    c.close()
    assert stable == priced_v1, "le coût doit rester lié à son snapshot tant que reprice n'a pas tourné"
    # reprice explicite -> recalcule contre la nouvelle version
    usage_store.reprice(usage_db)
    c = usage_store.connect(usage_db)
    repriced = dict(c.execute("SELECT cost, pricing_snapshot_id FROM model_call").fetchone())
    v1_row = c.execute("SELECT input_rate FROM pricing_snapshot WHERE version=1").fetchone()
    c.close()
    assert repriced["pricing_snapshot_id"] != priced_v1["pricing_snapshot_id"]
    assert repriced["cost"] != priced_v1["cost"]
    assert v1_row["input_rate"] == 0.2      # ancien snapshot IMMUABLE


def test_bootstrap_ne_price_pas_un_autre_modele_openai(usage_db):
    # un modèle openai DIFFÉRENT (non ratifié) ne doit pas hériter du tarif de luna
    with telemetry.execution(kind="report", provider="openai", model="gpt-autre-modele"):
        with telemetry.stage("extract"):
            telemetry.record_model_call(provider="openai", model="gpt-autre-modele",
                latency_s=1.0, usage=_Usage(100, 100), usage_source="provider_reported")
    c = usage_store.connect(usage_db)
    usage_store.bootstrap_official_pricing(c)
    c.commit()
    c.close()
    usage_store.reprice(usage_db)
    c = usage_store.connect(usage_db)
    assert c.execute("SELECT cost FROM model_call").fetchone()["cost"] is None
    c.close()


# ----------------------- Sémantique cache/raisonnement (tarif officiel) -------
def test_reconciliation_officielle_gpt56luna(usage_db):
    """Usage RÉEL relevé recalculé contre la formule OFFICIELLE."""
    with telemetry.execution(kind="report", provider="openai", model="gpt-5.6-luna"):
        with telemetry.stage("extract"):
            telemetry.record_model_call(provider="openai", model="gpt-5.6-luna", latency_s=1.0,
                usage=_Usage(547, 452, cached=0, reasoning=68), usage_source="provider_reported")
    c = usage_store.connect(usage_db)
    usage_store.bootstrap_official_pricing(c)
    c.commit()
    c.close()
    usage_store.reprice(usage_db)
    c = usage_store.connect(usage_db)
    row = c.execute("SELECT cost, cost_currency, cost_basis, reasoning_tok FROM model_call").fetchone()
    c.close()
    # 547 in @ $0.20/1M + 452 out @ $1.20/1M ; le raisonnement (68) est DANS out, pas ajouté.
    expected = (547 * 0.20 + 452 * 1.20) / 1e6
    assert abs(row["cost"] - expected) < 1e-12
    assert row["cost_currency"] == "USD" and row["cost_basis"] == "cloud_derived"


def test_cache_facture_au_tarif_reduit(usage_db):
    with telemetry.execution(kind="report", provider="openai", model="gpt-5.6-luna"):
        with telemetry.stage("extract"):
            telemetry.record_model_call(provider="openai", model="gpt-5.6-luna", latency_s=1.0,
                usage=_Usage(1000, 200, cached=400), usage_source="provider_reported")
    c = usage_store.connect(usage_db)
    usage_store.bootstrap_official_pricing(c)
    c.commit()
    c.close()
    usage_store.reprice(usage_db)
    c = usage_store.connect(usage_db)
    cost = c.execute("SELECT cost FROM model_call").fetchone()["cost"]
    c.close()
    # 600 non-cache @0.20 + 400 cache @0.02 + 200 out @1.20, /1e6
    expected = (600 * 0.20 + 400 * 0.02 + 200 * 1.20) / 1e6
    assert abs(cost - expected) < 1e-12


@pytest.mark.parametrize("modele, entree, sortie, cache", [
    ("claude-haiku-5-5", 0.10, 0.50, 0.01),     # compte rendu et Assistant
    ("claude-sonnet-5-5", 2.00, 10.00, 0.10),   # faits de la réunion (ECL)
])
def test_anthropic_est_tarife_comme_les_autres(usage_db, modele, entree, sortie, cache):
    """Un appel Anthropic noté comme le moteur le note (jetons déjà comptés) reçoit son coût."""
    with telemetry.execution(kind="report", provider="anthropic", model=modele):
        with telemetry.stage("ecl"):
            telemetry.record_model_call(provider="anthropic", model=modele, latency_s=1.0,
                                        in_tok=1000, out_tok=2000, cached_tok=200,
                                        usage_source="provider_reported")
    c = usage_store.connect(usage_db)
    usage_store.bootstrap_official_pricing(c)
    c.commit()
    c.close()
    usage_store.reprice(usage_db)
    c = usage_store.connect(usage_db)
    row = c.execute("SELECT cost, cost_currency, cost_basis FROM model_call").fetchone()
    c.close()
    expected = (800 * entree + 200 * cache + 2000 * sortie) / 1e6
    assert abs(row["cost"] - expected) < 1e-12
    assert row["cost_currency"] == "USD" and row["cost_basis"] == "cloud_derived"


# ----------------------- Aucun tarif LOCAL par défaut -------------------------
def test_aucun_tarif_local_par_defaut(usage_db):
    # MLX + Whisper : bootstrap officiel ne leur donne AUCUN tarif -> coût NULL
    with telemetry.execution(kind="report", provider="mlx",
                             model="mlx-community/Qwen3-4B-Instruct-2507-4bit"):
        with telemetry.stage("extract"):
            telemetry.record_model_call(provider="mlx",
                model="mlx-community/Qwen3-4B-Instruct-2507-4bit", latency_s=1.0,
                usage=_Usage(500, 300), usage_source="local_server")
    with telemetry.execution(kind="stt", provider="whisper",
                             model="mlx-community/whisper-large-v3-turbo", audio_seconds=30.0):
        pass
    c = usage_store.connect(usage_db)
    usage_store.bootstrap_official_pricing(c)
    c.commit()
    c.close()
    usage_store.reprice(usage_db)
    c = usage_store.connect(usage_db)
    mlx = c.execute("SELECT cost FROM model_call WHERE provider='mlx'").fetchone()
    wh = c.execute("SELECT cost FROM execution WHERE provider_primary='whisper'").fetchone()
    # aucune ligne de barème local n'a été livrée
    n_local = c.execute("SELECT COUNT(*) FROM pricing_snapshot WHERE kind='local_imputed'").fetchone()[0]
    c.close()
    assert mlx["cost"] is None and wh["cost"] is None and n_local == 0


def test_local_benchmark_seulement_si_importe_explicitement(usage_db):
    """Un tarif local n'existe QUE si on l'importe comme `local_benchmark` — jamais un défaut."""
    with telemetry.execution(kind="stt", provider="whisper",
                             model="mlx-community/whisper-large-v3-turbo", audio_seconds=60.0):
        pass
    c = usage_store.connect(usage_db)
    # import EXPLICITE d'un tarif local imputé (provenance local_benchmark, pas de source officielle)
    usage_store.import_pricing(c, [{"provider": "whisper",
        "model": "mlx-community/whisper-large-v3-turbo", "kind": "local_imputed",
        "unit": "audio_second", "currency": "EUR", "unit_rate": 0.0001,
        "rate_source": "local_benchmark",
        "source": "équivalent hébergé, non ratifié — exploitant"}])
    c.commit()
    c.close()
    usage_store.reprice(usage_db)
    c = usage_store.connect(usage_db)
    wh = c.execute("SELECT cost, cost_basis FROM execution WHERE provider_primary='whisper'").fetchone()
    src = c.execute("SELECT rate_source FROM pricing_snapshot").fetchone()["rate_source"]
    c.close()
    assert wh["cost"] == pytest.approx(0.006) and wh["cost_basis"] == "local_imputed"
    assert src == "local_benchmark"
