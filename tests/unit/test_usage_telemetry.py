"""Télémétrie d'usage IA (FinOps, phase 0) — faits durables, jamais de casse produit.

Ces tests protègent les invariants de la fondation dark :

- `usage.db` est un fichier SÉPARÉ, indépendant de `index.db` (index dérivé
  reconstructible) ;
- une exécution et ses appels modèle sont liés (l'appel = unité atomique, une
  reprise = un appel de plus) ;
- l'usage MESURÉ est capté quand le fournisseur l'expose, et n'est JAMAIS inventé ;
- une panne de télémétrie (base absente, charge malformée, écriture en échec) ne
  peut ni lever ni altérer la génération ;
- aucun contenu de prompt/transcript n'est persisté (ids/dimensions/comptes seuls).
"""
from __future__ import annotations

import sqlite3

import pytest

from core import sqlite_db, telemetry, usage_store


@pytest.fixture()
def usage_db(tmp_path, monkeypatch):
    """Isole chaque test sur un `usage.db` temporaire (jamais le fichier partagé)."""
    p = tmp_path / "usage.db"
    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(p))
    return p


class _Usage:
    """Mime un objet d'usage OpenAI/Mistral (attributs, pas dict)."""

    def __init__(self, prompt, completion):
        self.prompt_tokens = prompt
        self.completion_tokens = completion


# --- Séparation durable vs index dérivé --------------------------------------
def test_usage_db_est_un_fichier_separe():
    assert usage_store.usage_db_path() != sqlite_db.DB_PATH
    assert usage_store.usage_db_path().name == "usage.db"


def test_survit_a_la_destruction_de_index_db(usage_db, tmp_path):
    """Effacer/reconstruire `index.db` ne touche pas `usage.db` (fichiers disjoints)."""
    with telemetry.execution(kind="report", document_id="DOC1", provider="mlx", model="qwen"):
        telemetry.record_model_call(provider="mlx", model="qwen", latency_s=0.1,
                                    usage=_Usage(10, 5), usage_source="local_server")
    assert usage_db.exists()

    # Simule le pire d'une réindexation : l'index dérivé disparaît entièrement.
    faux_index = tmp_path / "index.db"
    faux_index.write_bytes(b"")
    faux_index.unlink()

    conn = usage_store.connect(usage_db)
    n = conn.execute("SELECT COUNT(*) FROM execution").fetchone()[0]
    conn.close()
    assert n == 1  # la télémétrie a survécu


# --- Relations exécution / appels, reprises = appels distincts ----------------
def test_execution_et_appels_lies_reprises_distinctes(usage_db):
    with telemetry.execution(kind="report", document_id="DOC2", provider="mlx", model="qwen"):
        with telemetry.stage("extract"):
            telemetry.record_model_call(provider="mlx", model="qwen", latency_s=1.0,
                                        usage=_Usage(100, 40), usage_source="local_server")
        with telemetry.stage("classify"):
            # Deux requêtes dans la même étape : une reprise EST un appel de plus.
            telemetry.record_model_call(provider="mlx", model="qwen", latency_s=0.2,
                                        usage=_Usage(20, 0), usage_source="local_server",
                                        status="truncated")
            telemetry.record_model_call(provider="mlx", model="qwen", latency_s=0.5,
                                        usage=_Usage(20, 30), usage_source="local_server")

    conn = usage_store.connect(usage_db)
    ex = conn.execute("SELECT execution_id, n_calls, in_tok, out_tok FROM execution").fetchone()
    calls = conn.execute(
        "SELECT seq, stage, status, execution_id FROM model_call ORDER BY seq"
    ).fetchall()
    conn.close()

    assert ex["n_calls"] == 3
    assert ex["in_tok"] == 140 and ex["out_tok"] == 70          # rollup mesuré
    assert [c["stage"] for c in calls] == ["extract", "classify", "classify"]
    assert calls[1]["status"] == "truncated"
    assert all(c["execution_id"] == ex["execution_id"] for c in calls)  # liaison


# --- Usage mesuré capté, jamais inventé --------------------------------------
def test_lecture_usage_deux_conventions_et_dict():
    # Chat Completions (Mistral / mlx_lm)
    assert telemetry._read_usage(_Usage(11, 7))[:2] == (11, 7)
    # Responses (OpenAI) via dict + détails imbriqués
    responses_like = {
        "input_tokens": 200, "output_tokens": 80,
        "input_tokens_details": {"cached_tokens": 64},
        "output_tokens_details": {"reasoning_tokens": 25},
    }
    assert telemetry._read_usage(responses_like) == (200, 80, 64, 25)


def test_usage_sans_jetons_ne_se_dit_pas_mesure(usage_db):
    """Un `usage` sans champ de jetons (ex. STT en secondes audio) → source NULL.

    On n'appose pas `provider_reported` sur du vide : ne pas prétendre mesurer ce
    qu'on n'a pas lu.
    """
    with telemetry.execution(kind="stt", provider="mistral", model="voxtral-mini"):
        telemetry.record_model_call(provider="mistral", model="voxtral-mini",
                                    latency_s=3.0, usage={"seconds": 42.0},
                                    usage_source="provider_reported")
    conn = usage_store.connect(usage_db)
    row = conn.execute("SELECT usage_source, in_tok, out_tok FROM model_call").fetchone()
    conn.close()
    assert row["usage_source"] is None
    assert row["in_tok"] is None and row["out_tok"] is None


# --- Fiabilité : la télémétrie ne casse jamais la génération ------------------
def test_record_hors_execution_est_un_noop(usage_db):
    # Aucun contexte ouvert : ne lève pas, n'écrit rien.
    telemetry.record_model_call(provider="mlx", model="qwen", latency_s=0.1,
                                usage=_Usage(1, 1), usage_source="local_server")
    # La base n'a même pas besoin d'exister ; si elle existe, zéro ligne.
    if usage_db.exists():
        conn = usage_store.connect(usage_db)
        assert conn.execute("SELECT COUNT(*) FROM model_call").fetchone()[0] == 0
        conn.close()


def test_persist_sur_chemin_impossible_ne_leve_pas(monkeypatch, tmp_path):
    """Une base injoignable → persist retourne False, sans lever."""
    # Un RÉPERTOIRE au lieu d'un fichier : l'ouverture SQLite échoue.
    mauvais = tmp_path / "je_suis_un_dossier"
    mauvais.mkdir()
    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(mauvais))
    # Ne doit pas lever, et le corps du `with` s'exécute normalement.
    marqueur = {}
    with telemetry.execution(kind="report", provider="mlx", model="qwen"):
        marqueur["corps_execute"] = True
        telemetry.record_model_call(provider="mlx", model="qwen", latency_s=0.1,
                                    usage=_Usage(1, 1), usage_source="local_server")
    assert marqueur["corps_execute"] is True


def test_flush_en_echec_est_avale(usage_db, monkeypatch):
    """Si `persist_execution` lève, la sortie du contexte reste silencieuse."""
    def _boom(*a, **k):
        raise RuntimeError("panne de disque simulée")

    monkeypatch.setattr(usage_store, "persist_execution", _boom)
    # Ne doit PAS propager l'erreur de persistance.
    with telemetry.execution(kind="report", provider="mlx", model="qwen"):
        telemetry.record_model_call(provider="mlx", model="qwen", latency_s=0.1)


def test_exception_metier_propage_et_marque_failed(usage_db):
    """Une erreur DANS le corps se propage (métier intact) ; l'exécution est `failed`."""
    with pytest.raises(ValueError):
        with telemetry.execution(kind="report", document_id="DOC3", provider="mlx", model="qwen"):
            telemetry.record_model_call(provider="mlx", model="qwen", latency_s=0.1,
                                        usage=_Usage(5, 0), usage_source="local_server")
            raise ValueError("échec de génération simulé")

    conn = usage_store.connect(usage_db)
    row = conn.execute("SELECT status, n_calls FROM execution").fetchone()
    conn.close()
    assert row["status"] == "failed"      # persistée malgré l'échec
    assert row["n_calls"] == 1            # l'appel déjà émis compte


def test_usage_malforme_ne_leve_pas(usage_db):
    """Un `usage` biscornu est ignoré proprement (comptes NULL), sans lever."""
    class _Piege:
        @property
        def prompt_tokens(self):  # accès qui explose
            raise RuntimeError("usage piégé")

    with telemetry.execution(kind="report", provider="mlx", model="qwen"):
        telemetry.record_model_call(provider="mlx", model="qwen", latency_s=0.1,
                                    usage=_Piege(), usage_source="local_server")
    conn = usage_store.connect(usage_db)
    row = conn.execute("SELECT in_tok, out_tok FROM model_call").fetchone()
    conn.close()
    assert row["in_tok"] is None and row["out_tok"] is None


# --- Confidentialité : aucun texte de prompt/transcript persisté --------------
def test_schema_ne_stocke_aucun_contenu(usage_db):
    """Le schéma ne porte QUE des ids, dimensions et comptes — pas de texte libre."""
    conn = usage_store.connect(usage_db)
    # Mots qui trahiraient du CONTENU de prompt/transcript. On ne met pas
    # « input/output/completion » : ce sont des dimensions légitimes de jetons et
    # de barème (`in_tok`, `input_rate`…), pas du texte libre.
    interdits = {"prompt", "system", "user", "text", "body", "content", "transcript",
                 "message", "payload"}
    for table in ("execution", "model_call", "pricing_snapshot"):
        cols = {r["name"].lower() for r in conn.execute(f"PRAGMA table_info({table})")}
        fautifs = {c for c in cols if any(mot in c for mot in interdits)}
        assert not fautifs, f"{table} porte des colonnes suspectes : {fautifs}"
    conn.close()


def test_record_n_accepte_pas_de_texte_de_prompt(usage_db):
    """`record_model_call` n'a aucun paramètre par lequel un prompt entrerait."""
    import inspect
    params = set(inspect.signature(telemetry.record_model_call).parameters)
    for interdit in ("system", "user", "prompt", "text", "body", "content"):
        assert interdit not in params


# --- Coût NON fabriqué : colonnes présentes (phase 1A) mais NULL sans barème ---
def test_pricing_snapshot_vide_par_defaut_et_cout_null(usage_db):
    with telemetry.execution(kind="report", provider="mlx", model="qwen"):
        telemetry.record_model_call(provider="mlx", model="qwen", latency_s=0.1,
                                    usage=_Usage(1, 1), usage_source="local_server")
    conn = usage_store.connect(usage_db)
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"execution", "model_call", "pricing_snapshot", "usage_meta"} <= tables
    # Barème VIDE par défaut : aucun coût n'est fabriqué sans barème saisi.
    assert conn.execute("SELECT COUNT(*) FROM pricing_snapshot").fetchone()[0] == 0
    # La phase 1A AJOUTE des colonnes de coût — mais elles restent NULL (jamais un
    # faux zéro) tant qu'aucun barème ne s'applique.
    call = conn.execute("SELECT cost, cost_basis, pricing_snapshot_id FROM model_call").fetchone()
    assert call["cost"] is None and call["cost_basis"] is None and call["pricing_snapshot_id"] is None
    conn.close()


def test_garde_de_version_refuse_une_base_plus_recente(usage_db):
    """Une base écrite par un schéma futur est refusée (compat ascendante)."""
    conn = usage_store.connect(usage_db)  # crée le schéma v courant
    conn.execute("UPDATE usage_meta SET version = ? WHERE component = 'usage_schema'",
                 (usage_store.SCHEMA_VERSION + 1,))
    conn.commit()
    conn.close()
    with pytest.raises(sqlite3.DatabaseError):
        usage_store.connect(usage_db)
