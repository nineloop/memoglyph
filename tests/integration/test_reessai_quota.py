"""Un quota atteint (429) d'un fournisseur nommé est attendu puis réessayé (jusqu'à deux fois) ; tout appel
refusé laisse une trace (le code seulement).

Aucune attente réelle : la pause passe par `core.attente_quota.dormir` (un point de remplacement) que les tests remplacent par
un enregistreur ; la garde de `tests/conftest.py` fait échouer tout test qui dormirait pour de vrai. Aucun appel réel : un
transport simulé joue une suite de réponses.
"""
from __future__ import annotations

import importlib
import json

import httpx
import openai
import pytest

from core import llm, messages, telemetry, usage_store
from core.llm_base import LLMError
from tests.unit.test_llm_nomme import CLE, MODELE, environnement_propre  # noqa: F401 — fixture autouse réutilisée

pytestmark = pytest.mark.integration

CORPS_BRUT = "corps-brut-du-service"
NOMS = ("openrouter", "groq")


@pytest.fixture
def quota():
    return importlib.import_module("core.attente_quota")


@pytest.fixture
def nomme():
    return importlib.import_module("core.llm_nomme")


@pytest.fixture(autouse=True)
def usage_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(tmp_path / "usage.db"))
    return tmp_path / "usage.db"


class Dormeur:
    """Le point de remplacement de la pause : note les durées demandées, n'attend pas."""

    def __init__(self) -> None:
        self.durees: list[float] = []
        self.journal: list[str] = []

    def __call__(self, secondes: float) -> None:
        self.durees.append(secondes)
        self.journal.append(f"dort {secondes:g}")


@pytest.fixture
def dormeur(monkeypatch, quota):
    d = Dormeur()
    monkeypatch.setattr(quota, "dormir", d)
    return d


class Suite:
    """Un transport qui joue des réponses dans l'ordre : `(statut, en-têtes)`, la dernière se répète. Un `Exception` est levé."""

    def __init__(self, *reponses, contenu: str = "réponse de test") -> None:
        self.reponses = list(reponses)
        self.contenu = contenu
        self.requetes: list[httpx.Request] = []

    def fabrique(self, base_url: str, *, api_key: str) -> openai.OpenAI:
        def traiter(requete: httpx.Request) -> httpx.Response:
            n = len(self.requetes)
            self.requetes.append(requete)
            r = self.reponses[min(n, len(self.reponses) - 1)]
            if isinstance(r, Exception):
                raise r
            if isinstance(r, dict):          # un corps 200 particulier
                return httpx.Response(200, json=r)
            statut, entetes = r
            if statut != 200:
                return httpx.Response(statut, headers=entetes, json={"error": {"message": CORPS_BRUT}})
            return httpx.Response(200, json={"model": "vendeur/servi", "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                                             "choices": [{"message": {"content": self.contenu}, "finish_reason": "stop"}]})
        return openai.OpenAI(base_url=base_url, api_key=api_key, max_retries=0,
                             http_client=httpx.Client(transport=httpx.MockTransport(traiter), trust_env=False))


def _pret(monkeypatch, nomme, suite, nom="openrouter"):
    profil = nomme.PROFILS[nom]
    monkeypatch.setenv(profil.variable_cle, CLE)
    monkeypatch.setenv(f"MEMOGLYPH_{profil.variable_modele}", MODELE)
    monkeypatch.setattr(nomme, "client_sans_fuite", suite.fabrique)


def RA(secondes) -> tuple:
    """Un 429 avec `Retry-After`."""
    return (429, {"retry-after": str(secondes)})


SANS_ENTETE = (429, {})
OK = (200, {})


# ---------- Le réessai ----------

@pytest.mark.parametrize("nom", NOMS)
def test_un_429_avec_retry_after_attend_ce_delai_puis_reessaie_une_fois(nom, nomme, quota, dormeur, monkeypatch) -> None:
    suite = Suite(RA(5), OK)
    _pret(monkeypatch, nomme, suite, nom)
    annonces: list = []
    with quota.ouvrir(annonces.append):
        assert llm.chat("s", "u", provider=nom, envoi=None) == "réponse de test"
    assert len(suite.requetes) == 2
    assert dormeur.durees == [5]
    assert annonces == [5]


def test_l_attente_est_annoncee_avant_de_dormir(nomme, quota, dormeur, monkeypatch) -> None:
    _pret(monkeypatch, nomme, Suite(RA(7), OK))
    with quota.ouvrir(lambda s: dormeur.journal.append(f"annonce {s:g}")):
        llm.chat("s", "u", provider="openrouter", envoi=None)
    assert dormeur.journal == ["annonce 7", "dort 7"]


@pytest.mark.parametrize("entetes", [{}, {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}, {"retry-after": "bientot"},
                                      {"retry-after": "-3"}, {"retry-after": ""}, {"retry-after": "inf"}, {"retry-after": "nan"}],
                         ids=["absent", "date", "texte", "negatif", "vide", "infini", "nan"])
def test_sans_delai_lisible_en_secondes_on_attend_vingt_secondes(entetes, nomme, quota, dormeur, monkeypatch) -> None:
    """Aucune des pages de documentation lues (OpenRouter, Groq) ne donne un délai par défaut : 20 s est le repli choisi, pas une valeur
    documentée. Une date HTTP, un texte ou un négatif comptent comme un en-tête absent."""
    _pret(monkeypatch, nomme, Suite((429, entetes), OK))
    with quota.ouvrir():
        llm.chat("s", "u", provider="openrouter", envoi=None)
    assert dormeur.durees == [20]


def test_un_notifieur_qui_leve_n_empeche_ni_l_attente_ni_le_reessai(nomme, quota, dormeur, monkeypatch) -> None:
    """Dire l'attente ne doit jamais la casser (la progression peut échouer à publier)."""
    def casse(secondes):
        raise RuntimeError("publication impossible")
    _pret(monkeypatch, nomme, Suite(RA(5), OK))
    with quota.ouvrir(casse):
        assert llm.chat("s", "u", provider="openrouter", envoi=None) == "réponse de test"
    assert dormeur.durees == [5]


def test_un_delai_decimal_est_lu(nomme, quota, dormeur, monkeypatch) -> None:
    _pret(monkeypatch, nomme, Suite(RA("2.5"), OK), "groq")
    with quota.ouvrir():
        llm.chat("s", "u", provider="groq", envoi=None)
    assert dormeur.durees == [2.5]


def test_soixante_secondes_s_attendent_soixante_et_une_non(nomme, quota, dormeur, monkeypatch) -> None:
    _pret(monkeypatch, nomme, Suite(RA(60), OK))
    with quota.ouvrir():
        llm.chat("s", "u", provider="openrouter", envoi=None)
    assert dormeur.durees == [60]

    suite = Suite(RA(61), OK)
    _pret(monkeypatch, nomme, suite)
    with quota.ouvrir():
        with pytest.raises(nomme.LimiteDeDemandes):
            llm.chat("s", "u", provider="openrouter", envoi=None)
    assert len(suite.requetes) == 1, "un délai annoncé trop long n'est pas attendu : on garde la panne dite"
    assert dormeur.durees == [60], "aucune pause de plus"


def test_jusqu_a_deux_reessais_un_second_429_est_encore_attendu(nomme, quota, dormeur, monkeypatch) -> None:
    """Deux réessais : une limite par minute ne se vide pas en quelques secondes ; l'essai réel a perdu une
    tranche sur un second 429 après 8 s."""
    suite = Suite(RA(5), RA(5), OK)
    _pret(monkeypatch, nomme, suite)
    with quota.ouvrir():
        assert llm.chat("s", "u", provider="openrouter", envoi=None) == "réponse de test"
    assert len(suite.requetes) == 3 and dormeur.durees == [5, 5]


def test_un_troisieme_429_est_la_panne_dite(nomme, quota, dormeur, monkeypatch) -> None:
    suite = Suite(RA(5), RA(5), RA(5), OK)
    _pret(monkeypatch, nomme, suite)
    with quota.ouvrir():
        with pytest.raises(nomme.LimiteDeDemandes) as erreur:
            llm.chat("s", "u", provider="openrouter", envoi=None)
    assert len(suite.requetes) == 3 and dormeur.durees == [5, 5], "trois requêtes, deux attentes, pas de quatrième"
    assert CORPS_BRUT not in str(erreur.value) and CLE not in str(erreur.value)


def test_la_borne_des_reessais_tient_en_une_constante(quota) -> None:
    assert quota.REESSAIS_MAX == 2


def test_le_cumul_est_plafonne_a_trois_cents_secondes_par_traitement(nomme, quota, dormeur, monkeypatch) -> None:
    """Cinq attentes de 60 s font 300 s, permises (la borne est incluse) ; une sixième de 1 s seulement dépasserait de 1 s : on
    revient à la panne dite."""
    _pret(monkeypatch, nomme, Suite(RA(60), OK, RA(60), OK, RA(60), OK, RA(60), OK, RA(60), OK, RA(1), OK))
    with quota.ouvrir():
        for _ in range(5):
            llm.chat("s", "u", provider="openrouter", envoi=None)
        assert dormeur.durees == [60] * 5
        with pytest.raises(nomme.LimiteDeDemandes):
            llm.chat("s", "u", provider="openrouter", envoi=None)
    assert dormeur.durees == [60] * 5, "la sixième attente n'a pas eu lieu"


def test_un_quota_sans_delai_annonce_attend_vingt_secondes_par_appel_jusqu_au_plafond(nomme, quota, dormeur, monkeypatch) -> None:
    """Un quota épuisé qui ne dit jamais quand revenir (le cas d'un quota journalier) fait attendre 20 s par appel refusé, jusqu'au
    plafond de 300 s : quinze attentes, puis la panne dite sans attendre. C'est le coût réel de ce cas, dit dans les documents."""
    _pret(monkeypatch, nomme, Suite(SANS_ENTETE))        # la dernière réponse se répète : chaque requête est refusée
    with quota.ouvrir():
        for _ in range(16):
            with pytest.raises(nomme.LimiteDeDemandes):
                llm.chat("s", "u", provider="openrouter", envoi=None)
    assert dormeur.durees == [20] * 15


def test_le_cumul_repart_a_zero_dans_un_nouveau_traitement(nomme, quota, dormeur, monkeypatch) -> None:
    _pret(monkeypatch, nomme, Suite(RA(60), OK, RA(60), OK))
    for _ in range(2):
        with quota.ouvrir():
            llm.chat("s", "u", provider="openrouter", envoi=None)
    assert dormeur.durees == [60, 60]


def test_hors_d_un_traitement_rien_n_attend(nomme, quota, dormeur, monkeypatch) -> None:
    """L'Assistant (`/api/ask`) n'a pas de progression où dire l'attente : il garde la panne immédiate (503 `rateLimited`)."""
    suite = Suite(RA(5), OK)
    _pret(monkeypatch, nomme, suite)
    with pytest.raises(nomme.LimiteDeDemandes):
        llm.chat("s", "u", provider="openrouter", envoi=None)
    assert len(suite.requetes) == 1 and dormeur.durees == []


def test_chat_json_avec_graine_ne_fait_qu_un_reessai_en_tout(nomme, quota, dormeur, monkeypatch) -> None:
    """L'extraction passe une graine : `chat_json` n'a alors qu'un essai, le réessai du 429 est le seul."""
    suite = Suite(RA(5), (200, {}))
    suite.contenu = '{"units": []}'
    _pret(monkeypatch, nomme, suite)
    with quota.ouvrir():
        assert llm.chat_json("s", "u", provider="openrouter", envoi=None, seed=1) == {"units": []}
    assert len(suite.requetes) == 2


def test_le_vrai_client_de_production_ne_reessaie_pas_tout_seul() -> None:
    """Le SDK réessaie deux fois par défaut et honore `Retry-After` : empilé sur le réessai du moteur, il casserait « un seul
    réessai » et ses tentatives ne laisseraient aucune trace. Lu sur le VRAI constructeur, pas sur un client de test."""
    from core.llm_client_openai import client_sans_fuite
    assert client_sans_fuite("https://exemple.invalide/v1", api_key="x").max_retries == 0


def test_seuls_les_fournisseurs_nommes_lisent_le_contexte_d_attente() -> None:
    """Le réessai ne concerne ni `compatible`, ni MLX, ni Mistral, OpenAI, Anthropic : seul le moteur nommé importe le module."""
    from pathlib import Path
    racine = Path(__file__).resolve().parents[2]
    lecteurs = sorted(str(p.relative_to(racine)) for p in (racine / "core").rglob("*.py")
                      if "attente_quota" in p.read_text(encoding="utf-8") and p.name != "attente_quota.py")
    assert lecteurs == ["core/experimental/app_report.py", "core/llm_nomme.py"], lecteurs


# ---------- La progression : une seule aide pour tous les traitements, le message périmé est republié ----------

def _etape(code="step.report.extract"):
    return messages.Message(code, "Reading the transcript in detail…")


def test_la_pause_se_dit_a_la_derniere_fraction_puis_le_dernier_vrai_message_est_republie(nomme, quota, dormeur, monkeypatch) -> None:
    _pret(monkeypatch, nomme, Suite(RA(5), OK))
    evenements: list = []
    with quota.suivre(lambda f, m: evenements.append((f, m))) as progression:
        progression(0.3, _etape())
        llm.chat("s", "u", provider="openrouter", envoi=None)
    assert [(f, m.code) for f, m in evenements] == [
        (0.3, "step.report.extract"), (0.3, "step.provider.quota.pause"), (0.3, "step.report.extract")]
    assert evenements[1][1].params == {"delay_s": 5}


def test_deux_pauses_de_suite_republient_chacune_la_vraie_etape_jamais_une_pause(nomme, quota, dormeur, monkeypatch) -> None:
    _pret(monkeypatch, nomme, Suite(RA(5), RA(5), OK))
    evenements: list = []
    with quota.suivre(lambda f, m: evenements.append((f, m))) as progression:
        progression(0.3, _etape())
        llm.chat("s", "u", provider="openrouter", envoi=None)
    assert [m.code for _, m in evenements] == [
        "step.report.extract", "step.provider.quota.pause", "step.report.extract",
        "step.provider.quota.pause", "step.report.extract"]


def test_sans_etape_connue_rien_n_est_republie(nomme, quota, dormeur, monkeypatch) -> None:
    _pret(monkeypatch, nomme, Suite(RA(5), OK))
    evenements: list = []
    with quota.suivre(lambda f, m: evenements.append((f, m))):
        llm.chat("s", "u", provider="openrouter", envoi=None)
    assert [m.code for _, m in evenements] == ["step.provider.quota.pause"]


def test_une_republication_qui_leve_ne_casse_pas_le_reessai(nomme, quota, dormeur, monkeypatch) -> None:
    _pret(monkeypatch, nomme, Suite(RA(5), OK))
    vus: list = []

    def progression_cassee(fraction, message):
        vus.append(message.code)
        if len(vus) >= 3:
            raise RuntimeError("publication impossible")
    with quota.suivre(progression_cassee) as progression:
        progression(0.3, _etape())
        assert llm.chat("s", "u", provider="openrouter", envoi=None) == "réponse de test"
    assert vus == ["step.report.extract", "step.provider.quota.pause", "step.report.extract"]


def test_suivre_sans_progression_ne_fait_rien_et_reste_utilisable(nomme, quota, dormeur, monkeypatch) -> None:
    _pret(monkeypatch, nomme, Suite(RA(5), OK))
    with quota.suivre(None) as progression:
        progression(0.3, _etape())
        assert llm.chat("s", "u", provider="openrouter", envoi=None) == "réponse de test"
    assert dormeur.durees == [5]


def test_les_contextes_emboites_partagent_le_meme_plafond_de_trois_cents_secondes(nomme, quota, dormeur, monkeypatch) -> None:
    """Une transcription fait son profilage puis son compte rendu dans le même fil : le plafond est celui du TRAITEMENT, pas
    celui de chaque étape."""
    _pret(monkeypatch, nomme, Suite(RA(60), OK, RA(60), OK, RA(60), OK, RA(60), OK, RA(60), OK, RA(1), RA(1), OK))
    with quota.ouvrir():
        for _ in range(3):
            llm.chat("s", "u", provider="openrouter", envoi=None)
        with quota.ouvrir():                    # le compte rendu ouvre le sien dans le traitement déjà ouvert
            for _ in range(2):
                llm.chat("s", "u", provider="openrouter", envoi=None)
            with pytest.raises(nomme.LimiteDeDemandes):
                llm.chat("s", "u", provider="openrouter", envoi=None)
        with pytest.raises(nomme.LimiteDeDemandes):
            llm.chat("s", "u", provider="openrouter", envoi=None)
    assert dormeur.durees == [60] * 5, "300 s en tout, pas 300 s par contexte"


def test_un_contexte_emboite_dit_ses_pauses_a_sa_propre_progression(nomme, quota, dormeur, monkeypatch) -> None:
    _pret(monkeypatch, nomme, Suite(RA(5), OK))
    dehors: list = []
    dedans: list = []
    with quota.suivre(lambda f, m: dehors.append(m.code)):
        with quota.suivre(lambda f, m: dedans.append(m.code)):
            llm.chat("s", "u", provider="openrouter", envoi=None)
    assert dehors == [] and dedans == ["step.provider.quota.pause"]


def _fonctions_de_api_main(nom: str) -> list[str]:
    """Le texte source de chaque fonction (même imbriquée) appelée `nom` dans `api/main.py`, lu par l'arbre syntaxique : une
    réécriture de commentaires ne peut pas décaler la recherche, ce que faisait une fenêtre de caractères."""
    import ast
    from pathlib import Path
    texte = (Path(__file__).resolve().parents[2] / "api" / "main.py").read_text(encoding="utf-8")
    return [ast.get_source_segment(texte, n) for n in ast.walk(ast.parse(texte))
            if isinstance(n, ast.FunctionDef) and n.name == nom]


def test_la_transcription_ouvre_le_contexte_autour_du_profilage_et_du_compte_rendu() -> None:
    """Le profilage de la transcription appelle le fournisseur (écran Transcrire, le seul qui affiche la pause) : le contexte est
    ouvert UNE fois autour de `pipeline.run`, donc autour du profilage et du compte rendu automatique."""
    corps = _fonctions_de_api_main("target_locked")
    assert len(corps) == 1
    assert "attente_quota.suivre(cb)" in corps[0]
    assert "pipeline.run(" in corps[0]
    assert "progress=progression," in corps[0], "la progression ENVELOPPÉE est celle que reçoit le traitement"


# ---------- Le message dit dans la progression ----------

def test_le_message_de_pause_a_son_code_ses_parametres_et_ses_deux_langues() -> None:
    from pathlib import Path
    msg = messages.pause_quota_fournisseur(20)
    assert msg.code == "step.provider.quota.pause" and msg.params == {"delay_s": 20}
    assert "20" in str(msg)
    catalogue = (Path(__file__).resolve().parents[2] / "ui/src/lib/messages.ts").read_text(encoding="utf-8")
    assert catalogue.count("'job.step.provider.quota.pause'") == 2, "une entrée française et une anglaise"
    assert catalogue.count("{delay_s} s") >= 2


def test_make_local_report_dit_l_attente_dans_la_progression_sans_reculer_la_barre(monkeypatch, tmp_path, isolated_stores,
                                                                                  nomme, quota, dormeur) -> None:
    from core import report as report_mod
    from core.experimental import app_report
    from tests.unit.test_cr_fiabilite import _meta, _rendu, _sortie
    source = tmp_path / "essai.md"
    source.write_text("---\ntitle: Essai\n---\n\n" + _rendu(diarise=False, horodatage=True), encoding="utf-8")
    item = {"meta": _meta(), "body": source.read_text(encoding="utf-8")}
    sortie = _sortie({"g1": "T1"}, [{"theme": "T1", "label": "Prix", "unit_count": 1,
                                      "sentences": [{"text": "Prix fixé.", "support": ["g1"]}]}])
    sortie["diag"]["totals"].update(kept_sentences=1)
    suite = Suite(RA(5), OK)
    _pret(monkeypatch, nomme, suite)

    def _etapes(*a, progress=None, **k):
        progress(0.37, messages.compte_rendu_extraction())
        llm.chat("s", "u", provider="openrouter", envoi=None)
        return sortie
    monkeypatch.setattr(app_report.library, "get_transcript", lambda name: item)
    monkeypatch.setattr(app_report.library, "transcript_path", lambda name: source)
    monkeypatch.setattr(app_report.library, "sync_file", lambda path: None)
    monkeypatch.setattr(app_report.semantic, "index_name", lambda name: 0)
    monkeypatch.setattr(app_report._pipeline, "run_stages", _etapes)
    monkeypatch.setattr(report_mod, "classify_genre", lambda *a, **k: "projet")
    evenements: list = []

    app_report.make_local_report("essai.md", provider="openrouter", progress=lambda f, m: evenements.append((f, m)))

    codes = [(f, getattr(m, "code", None)) for f, m in evenements]
    assert codes[:3] == [(0.37, "step.report.extract"), (0.37, "step.provider.quota.pause"), (0.37, "step.report.extract")], codes
    pause = next(m for f, m in evenements if getattr(m, "code", None) == "step.provider.quota.pause")
    assert pause.params == {"delay_s": 5}
    assert dormeur.durees == [5]


def test_la_relance_des_faits_ouvre_aussi_le_contexte_d_attente() -> None:
    """Le traitement « faits » appelle un fournisseur nommé : sans contexte, son 429 n'attendrait jamais."""
    faits = [c for c in _fonctions_de_api_main("target") if "ecl.extraire(" in c]
    assert len(faits) == 1
    assert "attente_quota.suivre(cb)" in faits[0]


# ---------- L'annulation reste ce qu'elle était ----------

def test_un_traitement_qui_attend_son_quota_n_est_pas_annulable_et_un_traitement_en_file_l_est(nomme, quota, monkeypatch) -> None:
    """Lecture simple : « en attente » = en file pour le créneau. Le traitement qui dort sur un 429 tient le
    créneau : il est « en cours » et refuse l'annulation comme avant. Un traitement encore en file reste annulable PENDANT
    cette pause."""
    from api import jobs as jobs_mod
    gestionnaire = jobs_mod.JobManager()
    en_pause = gestionnaire.create(owner="a", nature="compte-rendu")
    en_file = gestionnaire.create(owner="a", nature="compte-rendu")
    en_pause.status, en_pause.en_attente = "running", False
    en_file.status, en_file.en_attente = "running", True
    reponses: dict = {}

    def dormir(secondes: float) -> None:
        reponses["en_pause"] = gestionnaire.annuler(en_pause)
        reponses["en_file"] = gestionnaire.annuler(en_file)
    monkeypatch.setattr(quota, "dormir", dormir)
    _pret(monkeypatch, nomme, Suite(RA(5), OK))
    with quota.ouvrir():
        assert llm.chat("s", "u", provider="openrouter", envoi=None) == "réponse de test"
    assert reponses == {"en_pause": "running", "en_file": "annule"}
    assert en_pause.annule is False and en_file.annule is True


# ---------- La trace d'un appel refusé : le code seulement ----------

def _appels(usage_db):
    conn = usage_store.connect(usage_db)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT status, error_code, model, model_servi, in_tok, out_tok FROM model_call ORDER BY seq")]
    finally:
        conn.close()


@pytest.mark.parametrize("reponse,statut,code", [
    ((404, {}), "error", "404"),
    ((500, {}), "error", "500"),
    ((401, {}), "error", "401"),
    (httpx.ReadTimeout("trop long"), "error", "timeout"),
    (httpx.ConnectError("injoignable"), "error", "connection"),
], ids=["404", "500", "401", "delai", "connexion"])
def test_un_appel_refuse_laisse_une_ligne_avec_le_code_seulement(reponse, statut, code, nomme, monkeypatch, usage_db) -> None:
    _pret(monkeypatch, nomme, Suite(reponse))
    with telemetry.execution(kind="report", document_id="D", provider="openrouter", model="openrouter/free"):
        with pytest.raises(LLMError):
            llm.chat("s", "u", provider="openrouter", envoi=None)
    lignes = _appels(usage_db)
    assert len(lignes) == 1
    assert lignes[0]["status"] == statut and lignes[0]["error_code"] == code
    assert lignes[0]["model"] == "openrouter/free" or lignes[0]["model"] == MODELE
    assert CORPS_BRUT not in json.dumps(lignes) and CLE not in json.dumps(lignes)


def test_un_429_puis_un_succes_laissent_deux_lignes(nomme, quota, dormeur, monkeypatch, usage_db) -> None:
    _pret(monkeypatch, nomme, Suite(RA(5), OK))
    with telemetry.execution(kind="report", document_id="D", provider="openrouter", model="openrouter/free"):
        with quota.ouvrir():
            llm.chat("s", "u", provider="openrouter", envoi=None)
    lignes = _appels(usage_db)
    assert [(ligne["status"], ligne["error_code"]) for ligne in lignes] == [("error", "429"), ("ok", None)]
    assert lignes[1]["model_servi"] == "vendeur/servi" and lignes[0]["model_servi"] is None


@pytest.mark.parametrize("corps,code,statut", [
    ({"choices": [{"message": {"content": "début"}, "finish_reason": "length"}]}, "length", "truncated"),
    ({"choices": [{"message": {"content": None}, "finish_reason": "length"}]}, "length", "truncated"),
    ({"choices": [{"message": {"content": "  "}, "finish_reason": "stop"}]}, "empty", "error"),
    ({"choices": [{"message": {"content": None}, "finish_reason": "stop"}]}, "no_text", "error"),
], ids=["coupee", "coupee_sans_texte", "vide", "sans_contenu"])
def test_une_reponse_inutilisable_laisse_son_code(corps, code, statut, nomme, monkeypatch, usage_db) -> None:
    _pret(monkeypatch, nomme, Suite(corps))
    with telemetry.execution(kind="report", document_id="D", provider="openrouter", model="openrouter/free"):
        with pytest.raises(LLMError):
            llm.chat("s", "u", provider="openrouter", envoi=None)
    lignes = _appels(usage_db)
    assert [(ligne["status"], ligne["error_code"]) for ligne in lignes] == [(statut, code)]


def test_une_usage_db_d_avant_la_tranche_h_est_mise_a_niveau_et_garde_ses_lignes(nomme, monkeypatch, usage_db) -> None:
    """Comme pour `model_servi` : une base installée n'a pas la colonne `error_code`. Le test échoue si la colonne
    n'est pas rattrapée sur une base existante."""
    conn = usage_store.connect(usage_db)
    conn.execute("ALTER TABLE model_call DROP COLUMN error_code")
    conn.commit()
    assert "error_code" not in {r["name"] for r in conn.execute("PRAGMA table_info(model_call)")}
    conn.close()
    _pret(monkeypatch, nomme, Suite((404, {})))
    with telemetry.execution(kind="report", document_id="D", provider="openrouter", model="openrouter/free") as ex:
        with pytest.raises(LLMError):
            llm.chat("s", "u", provider="openrouter", envoi=None)
        identifiant = ex.execution_id
    detail = usage_store.execution_detail(identifiant, path=usage_db)
    assert [c["error_code"] for c in detail["calls"]] == ["404"]


def test_les_appels_refuses_comptent_dans_les_appels_sans_fausser_le_cout(nomme, monkeypatch, usage_db) -> None:
    """Une tentative refusée est un appel de plus (`n_model_calls`), mais n'a aucun jeton ni aucun coût : elle ne fabrique
    ni zéro ni jetons."""
    _pret(monkeypatch, nomme, Suite((404, {})))
    with telemetry.execution(kind="report", document_id="D", provider="openrouter", model="openrouter/free"):
        with pytest.raises(LLMError):
            llm.chat("s", "u", provider="openrouter", envoi=None)
    ligne = _appels(usage_db)[0]
    assert ligne["in_tok"] is None and ligne["out_tok"] is None
