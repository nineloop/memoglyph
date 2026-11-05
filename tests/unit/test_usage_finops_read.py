"""FinOps — lecture seule pour l'affichage (résumé + détail forensique).

Invariants : liaison par ID STABLE (CR via ULID, transcript via document_id), versions
historiques distinctes, lecture qui n'ÉCRIT jamais, champs SÛRS uniquement (aucun texte ni
secret), coût NULL exposé tel quel (jamais 0), indisponibilité gracieuse.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from core import telemetry, usage_store, docid


@pytest.fixture()
def usage_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(tmp_path / "usage.db"))
    return tmp_path / "usage.db"


class _U:
    def __init__(self, i, o, cached=0, reasoning=None):
        self.input_tokens = i
        self.output_tokens = o
        self.input_tokens_details = {"cached_tokens": cached}
        self.output_tokens_details = ({"reasoning_tokens": reasoning}
                                      if reasoning is not None else None)


def _report(artifact_id, provider="openai", model="gpt-5.6-luna", i=547, o=452, r=68):
    with telemetry.execution(kind="report", provider=provider, model=model, document_id="SRC"):
        with telemetry.stage("extract"):
            telemetry.record_model_call(provider=provider, model=model, latency_s=1.0,
                usage=_U(i, o, reasoning=r), usage_source="provider_reported")
        telemetry.set_produced_artifact(artifact_id)


def test_read_connect_est_lecture_seule(usage_db):
    usage_store.initialize_catalogue(usage_db)
    c = usage_store.read_connect(usage_db)
    with pytest.raises(sqlite3.OperationalError):
        c.execute("CREATE TABLE x(a)")
    c.close()


def test_read_connect_base_absente_leve(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(tmp_path / "nope.db"))
    with pytest.raises(sqlite3.OperationalError):
        usage_store.read_connect()          # base absente -> l'appelant rend « indisponible »


def test_resume_cr_lie_par_ulid(usage_db):
    rid = docid.nouveau()
    _report(rid)
    usage_store.initialize_catalogue(usage_db)
    usage_store.reprice(usage_db)
    s = usage_store.execution_summary(artifact_id=rid, kind="report")
    assert s is not None
    assert s["produced_artifact_id"] == rid and s["kind"] == "report"
    assert s["provider_primary"] == "openai" and s["cost_basis"] == "cloud_derived"
    assert s["cost"] is not None and s["pricing_provenance"] == "official_published"


def test_versions_historiques_distinctes(usage_db):
    """Deux CR (deux ULID) → chacun résout SA propre exécution, jamais « la plus récente »."""
    a, b = docid.nouveau(), docid.nouveau()
    _report(a, i=100, o=100)
    _report(b, i=999, o=999)
    usage_store.initialize_catalogue(usage_db)
    usage_store.reprice(usage_db)
    sa = usage_store.execution_summary(artifact_id=a, kind="report")
    sb = usage_store.execution_summary(artifact_id=b, kind="report")
    assert sa["in_tok"] == 100 and sb["in_tok"] == 999
    assert sa["execution_id"] != sb["execution_id"]


def test_resume_stt_lie_par_document_id(usage_db):
    with telemetry.execution(kind="stt", provider="whisper",
                             model="mlx-community/whisper-large-v3-turbo",
                             document_id="TXID", audio_seconds=30.015):
        pass
    usage_store.initialize_catalogue(usage_db)
    usage_store.reprice(usage_db)
    s = usage_store.execution_summary(document_id="TXID", kind="stt")
    assert s["kind"] == "stt" and s["audio_seconds"] == 30.015
    assert s["cost_basis"] == "local_imputed" and s["n_calls"] == 0


def test_cout_null_reste_null(usage_db):
    """Un modèle non tarifé → coût NULL dans le résumé (jamais 0)."""
    rid = docid.nouveau()
    _report(rid, provider="openai", model="modele-inconnu")
    usage_store.initialize_catalogue(usage_db)   # ne tarife pas ce modèle
    usage_store.reprice(usage_db)
    s = usage_store.execution_summary(artifact_id=rid, kind="report")
    assert s["cost"] is None and s["cost_basis"] is None


def test_detail_forensique(usage_db):
    rid = docid.nouveau()
    with telemetry.execution(kind="report", provider="openai", model="gpt-5.6-luna", document_id="S"):
        with telemetry.stage("extract"):
            telemetry.record_model_call(provider="openai", model="gpt-5.6-luna", latency_s=16.3,
                usage=_U(547, 452, reasoning=68), usage_source="provider_reported")
        with telemetry.stage("classify"):
            telemetry.record_model_call(provider="openai", model="gpt-5.6-luna", latency_s=3.9,
                usage=_U(355, 256, reasoning=169), usage_source="provider_reported")
        telemetry.set_produced_artifact(rid)
    usage_store.initialize_catalogue(usage_db)
    usage_store.reprice(usage_db)
    s = usage_store.execution_summary(artifact_id=rid, kind="report")
    d = usage_store.execution_detail(s["execution_id"])
    assert [c["stage"] for c in d["calls"]] == ["extract", "classify"]
    assert d["calls"][0]["reasoning_tok"] == 68 and d["calls"][0]["cost"] is not None
    assert set(d["calls"][0]) == set(usage_store._CALL_PUBLIC)   # SEULEMENT les champs publics


def test_reponses_sans_texte_ni_secret(usage_db):
    rid = docid.nouveau()
    _report(rid)
    usage_store.initialize_catalogue(usage_db)
    usage_store.reprice(usage_db)
    s = usage_store.execution_summary(artifact_id=rid, kind="report")
    d = usage_store.execution_detail(s["execution_id"])
    blob = json.dumps({"s": s, "d": d})
    for forbidden in ("prompt", "system", "user", "transcript", "content", "body",
                      "message", "payload", "secret", "sk-", "_details"):
        assert forbidden not in blob


def test_indisponible_sur_id_inconnu(usage_db):
    usage_store.initialize_catalogue(usage_db)
    assert usage_store.execution_summary(artifact_id="ZZZ", kind="report") is None
    assert usage_store.execution_detail("ZZZ") is None
