"""E2E de la capture d'usage : le VRAI code moteur écrit un fait dans `usage.db`.

Contrairement aux tests unitaires (qui appellent `record_model_call` directement),
ceux-ci exercent le CHEMIN MOTEUR réel — `llm_openai._generer`, `llm_mistral._completer`
— avec seul le TRANSPORT réseau bouchonné. Ils prouvent que l'usage fournisseur est
capté À L'INTÉRIEUR du moteur, avant que la façade ne normalise, et qu'aucun texte de
prompt ne fuit dans le magasin.

Aucun appel réseau, aucun coût : le SDK est remplacé par un objet local.
"""
from __future__ import annotations

import pytest

from core import telemetry, usage_store


@pytest.fixture()
def usage_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(tmp_path / "usage.db"))
    return tmp_path / "usage.db"


@pytest.mark.integration
def test_openai_seam_capture_usage_reel(usage_db, monkeypatch):
    """`llm_openai.chat_json` (VRAI moteur) → 1 appel avec usage MESURÉ dans usage.db."""
    import openai
    import core.llm_openai as O

    class _Det:
        def __init__(self, **k):
            self.__dict__.update(k)

    class _Usage:
        input_tokens = 1234
        output_tokens = 88
        input_tokens_details = _Det(cached_tokens=256)
        output_tokens_details = _Det(reasoning_tokens=40)

    class _Resp:
        output_text = '{"ok": true}'
        status = "completed"
        usage = _Usage()

    class _Client:
        class responses:  # noqa: N801
            @staticmethod
            def create(**kwargs):
                return _Resp()

    monkeypatch.setattr(openai, "OpenAI", lambda *a, **k: _Client())
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")

    with telemetry.execution(kind="report", document_id="E2EDOC", provider="openai",
                             model="gpt-5.6-luna"):
        with telemetry.stage("structure"):
            out = O.chat_json("systeme SECRET", "transcript SECRET",
                              schema={"type": "object"}, retries=2)
    assert out == {"ok": True}

    conn = usage_store.connect(usage_db)
    ex = conn.execute(
        "SELECT kind, provider_primary, n_calls, in_tok, out_tok FROM execution"
    ).fetchone()
    call = conn.execute(
        "SELECT stage, usage_source, in_tok, out_tok, cached_tok, reasoning_tok "
        "FROM model_call"
    ).fetchone()
    # Aucun texte de prompt ne doit apparaître nulle part.
    leaked = [str(v) for r in conn.execute("SELECT * FROM model_call") for v in r]
    leaked += [str(v) for r in conn.execute("SELECT * FROM execution") for v in r]
    conn.close()

    assert ex["kind"] == "report" and ex["n_calls"] == 1
    assert ex["in_tok"] == 1234 and ex["out_tok"] == 88
    assert call["stage"] == "structure" and call["usage_source"] == "provider_reported"
    assert call["cached_tok"] == 256 and call["reasoning_tok"] == 40
    assert not any("SECRET" in v for v in leaked)


@pytest.mark.integration
def test_mistral_seam_capture_et_reprise(usage_db, monkeypatch):
    """`llm_mistral.chat_json` (VRAI moteur, repli HTTP) : usage capté ; JSON invalide
    puis valide ⇒ DEUX appels distincts (une reprise = un appel de plus)."""
    import core.llm_mistral as M

    # Force le repli `requests` (chemin sans SDK) pour bouchonner le transport.
    import builtins
    vrai_import = builtins.__import__

    def _sans_sdk(name, *a, **k):
        if name == "mistralai":
            raise ImportError("SDK masqué pour le test")
        return vrai_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _sans_sdk)

    reponses = [
        # 1er appel : JSON invalide → déclenche une reprise (mais l'appel A ÉTÉ facturé)
        {"choices": [{"message": {"content": "pas du json"}, "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 300, "completion_tokens": 10}},
        # 2e appel : JSON valide
        {"choices": [{"message": {"content": '{"ok": 1}'}, "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 300, "completion_tokens": 20}},
    ]

    class _FakeResp:
        def __init__(self, body):
            self._body = body
            self.ok = True
            self.status_code = 200

        def json(self):
            return self._body

    import requests
    it = iter(reponses)
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResp(next(it)))
    monkeypatch.setenv("MISTRAL_API_KEY", "test-not-real")

    with telemetry.execution(kind="report", provider="mistral", model="mistral-small"):
        with telemetry.stage("classify"):
            out = M.chat_json("sys", "usr", retries=2)
    assert out == {"ok": 1}

    conn = usage_store.connect(usage_db)
    ncalls = conn.execute("SELECT COUNT(*) FROM model_call").fetchone()[0]
    rows = conn.execute(
        "SELECT seq, stage, usage_source, in_tok, out_tok FROM model_call ORDER BY seq"
    ).fetchall()
    exec_rollup = conn.execute("SELECT n_calls, in_tok, out_tok FROM execution").fetchone()
    conn.close()

    assert ncalls == 2, "la reprise doit être un appel distinct"
    assert all(r["stage"] == "classify" for r in rows)
    assert all(r["usage_source"] == "provider_reported" for r in rows)
    assert exec_rollup["n_calls"] == 2
    assert exec_rollup["in_tok"] == 600  # 300 + 300, l'appel raté compte
    assert exec_rollup["out_tok"] == 30  # 10 + 20
