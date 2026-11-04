"""Les pannes et les refus des fournisseurs nommés, vus de bout en bout (chantier 006, tranche C).

- la clé et le corps brut d'une réponse du service n'apparaissent dans AUCUN suivi, en réussite comme en échec (500, 429, délai) ;
- deux comportements de la revérification du cœur, vérifiés dans le code et non supposés :
  (a) régénération / relance des faits / assistant : le refus ou le quota fait ÉCHOUER le traitement, avec son code de message ;
  (b) transcription : les blocs « le transcript reste généré » n'élèvent pas l'erreur — rien n'est envoyé, la transcription est
      écrite, et la cause est un AVERTISSEMENT (profil : `profiling_error` ; compte rendu : `report_error`) ;
- un modèle sans barème est enregistré sans coût (`None`), jamais zéro, et un identifiant de modèle qui ressemble à celui d'un
  autre fournisseur n'emprunte pas son barème.

Aucun appel réel : un transport simulé. La garde réseau de la suite refuserait toute sortie.
"""
from __future__ import annotations

import importlib

import httpx
import pytest

from api import auth
from api import main as api_main
from api.jobs import JobManager
from core import llm, sensitivity, telemetry, usage_store
from core.experimental import app_report
from tests.integration.test_regeneration_job import _sortie, attendre, connecte, lancer, reunion
from tests.unit.test_enrichissements_non_bloquants import _front_matter, _pipeline_bouchonne
from tests.unit.test_llm_nomme import CLE, Transport

pytestmark = [pytest.mark.integration, pytest.mark.sans_session]

MOT_DE_PASSE = "un mot de passe solide"
NOMS = ["openrouter", "groq"]
GRATUIT = "auteur/un-modele:free"
CORPS_BRUT = "corps-brut-du-service"


@pytest.fixture(autouse=True)
def environnement_propre(monkeypatch):
    for nom in NOMS:
        for variable in (f"{nom.upper()}_API_KEY", f"MEMOGLYPH_{nom.upper()}_MODEL", f"{nom.upper()}_MODEL"):
            monkeypatch.setenv(variable, "x")
            monkeypatch.delenv(variable)


@pytest.fixture
def nomme():
    return importlib.import_module("core.llm_nomme")


def _prets(monkeypatch, nomme, modele="auteur/un-modele-payant", noms=NOMS) -> None:
    for nom in noms:
        monkeypatch.setenv(nomme.PROFILS[nom].variable_cle, CLE)
        monkeypatch.setenv(f"MEMOGLYPH_{nomme.PROFILS[nom].variable_modele}", modele)


@pytest.fixture
def comptes(isolated_stores, tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(auth, "CODE_PATH", tmp_path / ".memoglyph-installation")
    monkeypatch.setattr(api_main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(api_main, "jobs", JobManager())
    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(tmp_path / "usage.db"))
    auth.creer_compte("admin", MOT_DE_PASSE, admin=True)
    auth.creer_compte("alice", MOT_DE_PASSE)
    return isolated_stores["output_dir"]


@pytest.fixture
def appelant(comptes, monkeypatch):
    """`run_stages` remplacé par un appelant qui passe par la VRAIE façade et le VRAI moteur nommé (transport simulé)."""
    from core import report
    vus: list = []

    def run(name, item, **options):
        llm.chat("système", "texte du document", provider=options["provider"],
                 envoi=sensitivity.Envoi(sensibilite="standard", proprietaire="alice", document_id="doc-1"))
        vus.append(options["provider"])
        return _sortie()

    monkeypatch.setattr(app_report._pipeline, "run_stages", run)
    monkeypatch.setattr(report, "classify_genre", lambda *a, **k: "projet")
    monkeypatch.setattr(app_report.semantic, "index_name", lambda name: 0)
    return vus


def _tout_ce_qui_se_voit(client, job_id: str, usage) -> str:
    morceaux = [client.get(f"/api/jobs/{job_id}").text, client.get("/api/jobs").text]
    try:
        conn = usage_store.connect(usage)
        morceaux += [str(tuple(ligne)) for table in ("model_call", "execution")
                     for ligne in conn.execute(f"SELECT * FROM {table}")]
        conn.close()
    except Exception:  # noqa: BLE001 — pas de base d'usage : rien de plus à lire
        pass
    return "\n".join(morceaux)


@pytest.fixture
def dormeur(monkeypatch):
    """Tranche H : un 429 est attendu puis réessayé une fois dans un traitement ; la pause est remplacée (rien ne dort)."""
    from core import attente_quota
    durees: list = []
    monkeypatch.setattr(attente_quota, "dormir", durees.append)
    return durees


# ---------- T046 : la clé et le corps brut ne sont dans aucun suivi ----------

@pytest.mark.parametrize("nom", NOMS)
@pytest.mark.parametrize("scenario", ["reussite", "500", "429", "delai"])
def test_la_cle_et_le_corps_brut_ne_figurent_dans_aucun_suivi(nom, scenario, comptes, appelant, nomme, monkeypatch, tmp_path,
                                                              dormeur) -> None:
    _prets(monkeypatch, nomme)
    t = Transport()
    if scenario in ("500", "429"):
        t.statut = int(scenario)
    elif scenario == "delai":
        t.exception = httpx.ReadTimeout("délai")
    monkeypatch.setattr(nomme, "client_sans_fuite", t.fabrique)
    alice = connecte("alice")
    document = reunion(comptes, "alice")
    job = lancer(alice, document, llm_provider=nom).json()["job_id"]
    etat = attendre(alice, job)
    assert etat["status"] == ("done" if scenario == "reussite" else "error")
    # Un 429 est réessayé jusqu'à deux fois (tranches H et H2) : trois requêtes ; tout autre scénario n'en fait qu'une.
    assert len(t.requetes) == (3 if scenario == "429" else 1), "ancre : le service simulé a bien été joint"
    visible = _tout_ce_qui_se_voit(alice, job, tmp_path / "usage.db")
    assert CLE not in visible and CORPS_BRUT not in visible


# ---------- T043 (a) : le traitement échoue avec son code ----------

@pytest.mark.parametrize("nom", NOMS)
def test_un_quota_atteint_fait_echouer_la_regeneration_avec_son_code(nom, comptes, appelant, nomme, monkeypatch, dormeur) -> None:
    _prets(monkeypatch, nomme)
    t = Transport()
    t.statut = 429
    monkeypatch.setattr(nomme, "client_sans_fuite", t.fabrique)
    alice = connecte("alice")
    document = reunion(comptes, "alice")
    etat = attendre(alice, lancer(alice, document, llm_provider=nom).json()["job_id"])
    assert etat["status"] == "error" and etat["error_code"] == "step.error.rateLimited"
    assert "Quota atteint" in etat["error"] and CORPS_BRUT not in etat["error"]


def test_un_document_passe_en_ultra_autorise_pendant_le_travail_arrete_la_regeneration_avec_son_code(
        comptes, appelant, nomme, monkeypatch) -> None:
    """La revérification du cœur, comportement (a) : le refus TRAVERSE les `except` de la chaîne et le traitement échoue."""
    _prets(monkeypatch, nomme, GRATUIT, ["openrouter"])
    t = Transport()
    monkeypatch.setattr(nomme, "client_sans_fuite", t.fabrique)
    monkeypatch.setattr(sensitivity, "_resolveur_sensibilite", lambda document_id: "ultra_confidential")
    monkeypatch.setattr(sensitivity, "autorise", lambda proprietaire: True)         # le cloud est autorisé : c'est la règle des gratuits
    alice = connecte("alice")
    document = reunion(comptes, "alice")
    etat = attendre(alice, lancer(alice, document, llm_provider="openrouter").json()["job_id"])
    assert etat["status"] == "error" and etat["error_code"] == "step.error.freeModelRestricted"
    assert t.requetes == [], "rien n'est parti"


# ---------- T043 (b) : la transcription écrit le transcript et dit la cause comme AVERTISSEMENT ----------

def test_le_profil_refuse_par_la_regle_des_gratuits_est_un_avertissement_et_rien_ne_part(nomme, monkeypatch, tmp_path) -> None:
    from core import profiling
    pipeline, source, sortie = _pipeline_bouchonne(monkeypatch, tmp_path)
    _prets(monkeypatch, nomme, GRATUIT, ["openrouter"])
    t = Transport()
    monkeypatch.setattr(nomme, "client_sans_fuite", t.fabrique)
    assert profiling.profile is not None
    monkeypatch.setattr(sensitivity, "autorise", lambda proprietaire: True)         # le cloud est autorisé : c'est la règle des gratuits
    messages_vus: list = []
    chemin, contenu = pipeline.run(
        source, engine_name="whisper", llm_provider="openrouter", profile_on=True, report_on=False,
        output_dir=sortie, timestamps=True, sensitivity="ultra_confidential",
        progress=lambda p, m: messages_vus.append(getattr(m, "code", str(m))))
    assert chemin.exists() and "bonjour tout le monde" in contenu, "la transcription est écrite"
    assert t.requetes == []
    meta = _front_matter(contenu)
    assert meta["profiled"] is False
    assert "ModeleGratuitRefuse" in meta["profiling_error"] and "gratuit" in meta["profiling_error"]
    assert "warn.profiling.failed" in messages_vus


def test_le_compte_rendu_refuse_par_la_regle_des_gratuits_est_un_avertissement_durable(nomme, monkeypatch, tmp_path) -> None:
    from core import profiling, report as report_mod
    pipeline, source, sortie = _pipeline_bouchonne(monkeypatch, tmp_path)
    _prets(monkeypatch, nomme, GRATUIT, ["openrouter"])
    t = Transport()
    monkeypatch.setattr(nomme, "client_sans_fuite", t.fabrique)
    monkeypatch.setattr(profiling, "profile", lambda *a, **k: {"speakers": {}})
    monkeypatch.setattr(sensitivity, "autorise", lambda proprietaire: True)         # le cloud est autorisé : c'est la règle des gratuits

    def generer(name, **k):
        llm.chat("s", "u", provider=k["provider"],
                 envoi=sensitivity.Envoi(sensibilite="ultra_confidential", proprietaire=None, document_id="d"))
    monkeypatch.setattr(report_mod, "generate_report", generer)
    chemin, contenu = pipeline.run(
        source, engine_name="whisper", llm_provider="openrouter", profile_on=True, report_on=True,
        output_dir=sortie, timestamps=True, sensitivity="ultra_confidential")
    assert chemin.exists() and "bonjour tout le monde" in contenu
    assert t.requetes == []
    meta = _front_matter(chemin.read_text(encoding="utf-8"))
    assert "gratuit" in meta["report_error"], "la cause est durable, dans le front matter"


def test_un_quota_atteint_pendant_la_transcription_est_dit_dans_l_avertissement(nomme, monkeypatch, tmp_path) -> None:
    pipeline, source, sortie = _pipeline_bouchonne(monkeypatch, tmp_path)
    _prets(monkeypatch, nomme, noms=["groq"])
    t = Transport()
    t.statut = 429
    monkeypatch.setattr(nomme, "client_sans_fuite", t.fabrique)
    chemin, contenu = pipeline.run(
        source, engine_name="whisper", llm_provider="groq", profile_on=True, report_on=False,
        output_dir=sortie, timestamps=True)
    meta = _front_matter(contenu)
    assert chemin.exists() and "Quota atteint chez Groq" in meta["profiling_error"]
    assert CLE not in contenu and CORPS_BRUT not in contenu


# ---------- T047 : le coût d'un modèle sans barème est inconnu, jamais zéro ----------

def _enregistrer_un_appel(nom, nomme, monkeypatch, base, modele):
    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(base))
    _prets(monkeypatch, nomme, modele, [nom])
    t = Transport()
    t.reponse_ok = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}}
    monkeypatch.setattr(nomme, "client_sans_fuite", t.fabrique)
    with telemetry.execution(kind="report", document_id="DOC", provider=nom, model=modele):
        with telemetry.stage("structure"):
            llm.chat("s", "u", provider=nom, envoi=None)


def _cout(base):
    conn = usage_store.connect(base)
    appel = conn.execute("SELECT provider, model, in_tok, out_tok, cost FROM model_call").fetchone()
    conn.close()
    return appel


@pytest.mark.parametrize("nom", NOMS)
def test_un_usage_sans_bareme_est_enregistre_sans_cout(nom, nomme, monkeypatch, tmp_path) -> None:
    """Le VRAI moteur nommé écrit le fait dans `usage.db` ; aucun barème n'existe pour ce couple (fournisseur, modèle)."""
    base = tmp_path / "usage.db"
    _enregistrer_un_appel(nom, nomme, monkeypatch, base, "auteur/modele-sans-bareme")
    appel = _cout(base)
    assert (appel["provider"], appel["model"], appel["in_tok"], appel["out_tok"]) == (nom, "auteur/modele-sans-bareme", 120, 30)
    assert appel["cost"] is None, "coût INCONNU : jamais zéro"


def test_un_identifiant_qui_ressemble_a_celui_d_un_autre_fournisseur_n_emprunte_pas_son_bareme(nomme, monkeypatch, tmp_path) -> None:
    """`openai/gpt-5.6-luna` chez OpenRouter n'est pas `openai/gpt-5.6-luna` chez OpenAI, ni `gpt-5.6-luna` chez OpenRouter :
    l'appariement est exact sur (fournisseur, modèle, unité), jamais approché."""
    base = tmp_path / "usage.db"
    modele = "openai/gpt-5.6-luna"
    _enregistrer_un_appel("openrouter", nomme, monkeypatch, base, modele)
    conn = usage_store.connect(base)
    for fournisseur, nom_modele in (("openai", modele), ("openai", "gpt-5.6-luna"), ("openrouter", "gpt-5.6-luna")):
        usage_store.insert_pricing_snapshot(conn, provider=fournisseur, model=nom_modele, kind="cloud_billed",
                                            currency="USD", input_rate=1.0, output_rate=2.0,
                                            effective_from="2020-01-01T00:00:00+00:00", source="barème de test")
    conn.commit()
    conn.close()
    usage_store.reprice(base)
    assert _cout(base)["cost"] is None, "aucun de ces trois barèmes n'est celui de (openrouter, openai/gpt-5.6-luna)"


def test_ancre_un_bareme_exact_donne_bien_un_cout(nomme, monkeypatch, tmp_path) -> None:
    """L'ancre du test précédent : avec le barème exact, le même appel est tarifé (le test n'est pas vrai par défaut)."""
    base = tmp_path / "usage.db"
    modele = "openai/gpt-5.6-luna"
    _enregistrer_un_appel("openrouter", nomme, monkeypatch, base, modele)
    conn = usage_store.connect(base)
    usage_store.insert_pricing_snapshot(conn, provider="openrouter", model=modele, kind="cloud_billed", currency="USD",
                                        input_rate=1.0, output_rate=2.0,
                                        effective_from="2020-01-01T00:00:00+00:00", source="barème de test")
    conn.commit()
    conn.close()
    usage_store.reprice(base)
    assert _cout(base)["cost"] == pytest.approx((120 * 1.0 + 30 * 2.0) / 1e6)


@pytest.mark.parametrize("nom", NOMS)
def test_la_lecture_des_executions_dit_un_cout_inconnu_et_non_zero(nom, nomme, monkeypatch, tmp_path) -> None:
    """Ce que l'écran d'usage lit (`usage_store.list_executions`, que la route `/api/usage/executions` rend tel quel) : le coût
    d'une exécution sans barème est `None`. L'affichage « — non tarifé » de `None` est testé côté interface
    (`FinopsSection.test.tsx`, « NULL => unpriced, jamais zéro »)."""
    base = tmp_path / "usage.db"
    _enregistrer_un_appel(nom, nomme, monkeypatch, base, "auteur/modele-sans-bareme")
    lues = usage_store.list_executions(project_id=None, start=None, end=None, provider=nom, model=None,
                                       stage=None, kind=None, limit=10, offset=0)
    lignes = lues.get("executions") or lues.get("rows") or []
    assert len(lignes) == 1, lues
    assert lignes[0]["cost"] is None
