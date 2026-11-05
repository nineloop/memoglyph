"""Où arrive un 429, et ce qui en remonte à la personne.

La décision (option a) : un quota atteint dégrade COMME TOUTE PANNE de moteur — la chaîne ne change pas. Ce fichier mesure ce que
la dégradation montre, chemin par chemin, avec le VRAI moteur nommé sur un transport simulé qui répond 429. Plusieurs tests sont
des CONSTATS : ils décrivent un comportement existant (une tranche perdue n'est montrée que par son numéro) pour que personne ne
le croie meilleur qu'il n'est. Les constats sont nommés comme tels.

Le module `core.llm_nomme` est importé PAR LES TESTS : avant son existence, chaque test échoue pour la même cause.
"""
from __future__ import annotations

import importlib

import pytest

from core import llm
from tests.unit.test_llm_nomme import CLE, MODELE, Transport, environnement_propre  # noqa: F401 — fixture autouse réutilisée

pytestmark = pytest.mark.unit

PHRASE = "Quota atteint chez OpenRouter"


@pytest.fixture
def quota(monkeypatch):
    nomme = importlib.import_module("core.llm_nomme")
    monkeypatch.setenv(nomme.PROFILS["openrouter"].variable_cle, CLE)
    monkeypatch.setenv(f"MEMOGLYPH_{nomme.PROFILS['openrouter'].variable_modele}", MODELE)
    t = Transport()
    t.statut = 429
    monkeypatch.setattr(nomme, "client_sans_fuite", t.fabrique)
    return t


def test_une_tranche_du_map_reduce_perdue_sur_un_429_dit_la_phrase(quota) -> None:
    """Le chemin « historique » (`report.map_summarize_chunks`) garde le texte de l'exception dans la liste des perdues."""
    from core import report
    contexte, perdues = report.map_summarize_chunks(["premier extrait", "second extrait"], "fr",
                                                    provider="openrouter", envoi=None)
    assert contexte == "" or "Tranche" not in contexte
    assert len(perdues) == 2 and all(PHRASE in p for p in perdues)
    assert all(CLE not in p and "corps-brut-du-service" not in p for p in perdues)


def test_le_texte_de_l_echec_d_une_tranche_de_l_ecl_porte_la_phrase(quota) -> None:
    """L'ECL note `f"{type(exc).__name__}: {exc}"` dans son journal : le nom de classe ET la phrase y figurent."""
    with pytest.raises(llm.LLMError) as exc:
        llm.chat_json("s", "u", provider="openrouter", envoi=None)
    assert f"{type(exc.value).__name__}: {exc.value}".startswith(f"LimiteDeDemandes: {PHRASE}")


def test_constat_sur_le_chemin_reel_du_compte_rendu_la_phrase_reste_dans_le_diagnostic(quota) -> None:
    """CONSTAT (à ne pas confondre avec un souhait). `experimental/stages._call` garde l'erreur dans `diag[...]["calls"][i]` ;
    `failed_chunks`, que le front matter publie (`map_chunks_failed`) et que l'écran montre, ne contient que des NUMÉROS.
    La phrase du quota n'atteint donc pas la personne sur ce chemin : elle voit « N tranches non résumées »."""
    from core.experimental import stages
    diag: dict = {}
    unites = stages.extract_atomic(["premier extrait"], provider="openrouter", seed=1, diag=diag, envoi=None)
    etape = diag["stage1_extract"]
    assert unites == []
    assert etape["failed_chunks"] == [1], "des numéros de tranche, rien d'autre"
    assert PHRASE in etape["calls"][0]["error"], "la phrase est dans le diagnostic"
    assert PHRASE not in str(etape["failed_chunks"])


def test_constat_le_classifieur_de_genre_est_muet_sur_un_429(quota) -> None:
    """CONSTAT. `report.classify_genre` absorbe toute panne et rend le gabarit générique, sans rien dire (décision antérieure :
    « le CR doit rester possible sans classifieur »). Un quota atteint y est silencieux comme une panne quelconque."""
    from core import report, templates
    genre = report.classify_genre("[00:00:01] Bonjour tout le monde, on commence la réunion.", "fr",
                                  provider="openrouter", envoi=None)
    assert genre == templates.GENERIC


def test_la_facade_laisse_passer_le_type_de_la_limite(quota) -> None:
    nomme = importlib.import_module("core.llm_nomme")
    with pytest.raises(nomme.LimiteDeDemandes):
        llm.chat("s", "u", provider="openrouter", envoi=None)


# ---------- Le chemin réel du compte rendu, de bout en bout ----------

def test_un_quota_atteint_sur_tous_les_appels_d_extraction_nomme_la_panne_et_garde_le_code_general(quota) -> None:
    """CONSTAT sur le VRAI `run_stages` : si TOUS les appels d'extraction sont refusés, la phrase du quota arrive dans le texte
    d'une `LLMError` (« Extraction impossible… Premier refus : LimiteDeDemandes: Quota atteint chez… »). Son code est celui de la
    famille (`step.error.modelUnavailable`) et non `rateLimited` : l'absorbeur de `stages._call` a déjà converti l'exception en
    texte. La personne voit la phrase générale, et le détail technique (texte brut gardé à part) dit le quota."""
    from core import erreurs
    from core.experimental import pipeline
    corps = ("## Transcript\n\n**Speaker 1** [00:00:01] On fixe le prix de vente.\n"
             "**Speaker 2** [00:00:05] D'accord pour vingt-cinq euros.\n")
    with pytest.raises(llm.LLMError) as erreur:
        pipeline.run_stages("reunion.md", {"meta": {}, "body": corps}, provider="openrouter", seed=42)
    assert PHRASE in str(erreur.value) and "refusé" in str(erreur.value)
    assert CLE not in str(erreur.value)
    assert erreurs.code_de(erreur.value)[0] == "step.error.modelUnavailable"


def test_quota_atteint_sur_une_tranche_le_front_matter_declare_le_numero_et_pas_la_phrase(monkeypatch, quota) -> None:
    """CONSTAT, de l'appel du moteur au front matter : une tranche sur deux refusée par un 429 donne `map_chunks_failed: [2]`
    dans le document produit (les numéros, jamais la phrase). Les étapes suivantes du compte rendu sont remplacées par la sortie
    synthétique de `test_local_report_seam.py` : seule l'extraction est réelle, avec le vrai moteur."""
    from core.experimental import app_report, stages
    from tests.unit.test_local_report_seam import _meta, _synthetic_out
    # Une tranche sur deux : le transport répond 200 (JSON d'unités) puis 429, en alternance.
    appel = {"n": 0}
    ancien_statut = quota.statut

    def alterner(requete=None):
        appel["n"] += 1
        quota.statut = 200 if appel["n"] % 2 else ancien_statut
        quota.contenu = '{"units": [{"kind": "fact", "text": "Le prix est fixé.", "start": "00:00:01"}]}'
    nomme = importlib.import_module("core.llm_nomme")
    monkeypatch.setattr(nomme, "client_sans_fuite",
                        lambda base_url, *, api_key: _client_alterne(quota, alterner, base_url, api_key))
    diag: dict = {}
    stages.extract_atomic(["premier extrait", "second extrait"], provider="openrouter", seed=1, diag=diag, envoi=None)
    echecs = diag["stage1_extract"]["failed_chunks"]
    assert echecs == [2]
    sortie = _synthetic_out()
    sortie["diag"]["stages"]["stage1_extract"]["failed_chunks"] = echecs
    contenu = app_report._assemble_app_report("m.md", _meta(), "generique", sortie)
    assert "map_chunks_failed:" in contenu and "en échec : [2]" in contenu
    assert PHRASE not in contenu


def _client_alterne(quota, alterner, base_url, api_key):
    """Un client dont le transport applique `alterner` avant chaque réponse (200 puis 429 en alternance)."""
    import httpx
    import openai

    def traiter(requete: httpx.Request) -> httpx.Response:
        quota.requetes.append(requete)
        alterner(requete)
        if quota.statut != 200:
            return httpx.Response(quota.statut, json={"error": {"message": "corps-brut-du-service"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": quota.contenu}, "finish_reason": "stop"}]})
    return openai.OpenAI(base_url=base_url, api_key=api_key, max_retries=0,
                         http_client=httpx.Client(transport=httpx.MockTransport(traiter), trust_env=False))


def test_constat_le_tableau_des_codes_ne_connait_pas_l_extraction_en_panne_de_l_ecl() -> None:
    """CONSTAT de mécanisme seulement : `ExtractionEnPanne` n'est pas au tableau, elle se lit donc « erreur inattendue ».
    (Le comportement de bout en bout est dans `test_gratuits_api.py` ; l'Assistant aussi.)"""
    from core import ecl, erreurs
    assert erreurs.code_de(ecl.ExtractionEnPanne("rien"))[0] == "step.error.unexpected"
