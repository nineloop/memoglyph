"""Tranche G du chantier 006 : l'essai réel a montré (1) une réponse au contenu nul et au motif « length » lue comme « sans contenu
textuel », (2) un compte rendu qui ne dit que « openrouter/free » (le routeur) sans dire quel modèle a répondu, (3) un budget de
sortie mangé par le raisonnement. Ce fichier prouve les trois correctifs ; aucun appel réel, un transport simulé.
"""
from __future__ import annotations

import importlib
import json

import httpx
import openai
import pytest

from core import llm, telemetry, usage_store
from core.llm_base import LLMError

pytestmark = pytest.mark.integration

CLE = "cle-fictive-de-test-0123456789"


@pytest.fixture
def nomme():
    return importlib.import_module("core.llm_nomme")


@pytest.fixture(autouse=True)
def usage_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(tmp_path / "usage.db"))
    for nom in ("OPENROUTER", "GROQ"):
        for variable in (f"{nom}_API_KEY", f"MEMOGLYPH_{nom}_MODEL"):
            monkeypatch.setenv(variable, "x")
            monkeypatch.delenv(variable)
    return tmp_path / "usage.db"


class Service:
    """Un transport simulé : rend les corps demandés tour à tour, note les requêtes."""

    def __init__(self, *corps: dict) -> None:
        self.corps = list(corps)
        self.requetes: list[httpx.Request] = []

    def fabrique(self, base_url: str, *, api_key: str) -> openai.OpenAI:
        def traiter(requete: httpx.Request) -> httpx.Response:
            self.requetes.append(requete)
            return httpx.Response(200, json=self.corps[min(len(self.requetes), len(self.corps)) - 1])
        return openai.OpenAI(base_url=base_url, api_key=api_key, max_retries=0,
                             http_client=httpx.Client(transport=httpx.MockTransport(traiter), trust_env=False))


def _corps(contenu, *, motif="stop", modele="vendeur/modele-servi", **message):
    c = {"choices": [{"message": {"content": contenu, **message}, "finish_reason": motif}],
         "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    if modele is not None:
        c["model"] = modele
    return c


def _pret(monkeypatch, nomme, service, nom="openrouter", modele="openrouter/free"):
    profil = nomme.PROFILS[nom]
    monkeypatch.setenv(profil.variable_cle, CLE)
    monkeypatch.setenv(f"MEMOGLYPH_{profil.variable_modele}", modele)
    monkeypatch.setattr(nomme, "client_sans_fuite", service.fabrique)


# ---------- G1 : un contenu nul avec « length » est la limite de jetons ----------

@pytest.mark.parametrize("raisonnement", [{}, {"reasoning": "je réfléchis longuement"}], ids=["sans_champ", "avec_champ_reasoning"])
def test_un_contenu_nul_avec_length_dit_la_limite_de_jetons(raisonnement, nomme, monkeypatch) -> None:
    service = Service(_corps(None, motif="length", **raisonnement))
    _pret(monkeypatch, nomme, service)
    with pytest.raises(LLMError) as erreur:
        llm.chat("s", "u", provider="openrouter", envoi=None)
    texte = str(erreur.value)
    assert "limite de jetons" in texte and "sans contenu textuel" not in texte
    assert "raisonnement" in texte, "la cause probable est dite (le budget mangé par le raisonnement)"
    assert CLE not in texte and "je réfléchis" not in texte


def test_un_contenu_nul_avec_stop_reste_sans_contenu_textuel(nomme, monkeypatch) -> None:
    """L'ancre : sans motif de coupure, le message d'avant est inchangé."""
    _pret(monkeypatch, nomme, Service(_corps(None, motif="stop")))
    with pytest.raises(LLMError, match="sans contenu textuel"):
        llm.chat("s", "u", provider="openrouter", envoi=None)


# ---------- G2 : le modèle servi ----------

def _lignes(usage_db):
    conn = usage_store.connect(usage_db)
    try:
        return [dict(r) for r in conn.execute("SELECT model, model_servi FROM model_call ORDER BY seq")]
    finally:
        conn.close()


def test_le_modele_servi_est_relevé_a_cote_du_modele_demande(nomme, monkeypatch, usage_db) -> None:
    _pret(monkeypatch, nomme, Service(_corps("ok", modele="vendeur/un-modele-servi")))
    with telemetry.execution(kind="report", document_id="D", provider="openrouter", model="openrouter/free"):
        llm.chat("s", "u", provider="openrouter", envoi=None)
    assert _lignes(usage_db) == [{"model": "openrouter/free", "model_servi": "vendeur/un-modele-servi"}], \
        "`model` reste le modèle DEMANDÉ : le barème s'apparie exactement sur (fournisseur, modèle)"


def test_une_reponse_sans_nom_de_modele_laisse_le_champ_vide(nomme, monkeypatch, usage_db) -> None:
    _pret(monkeypatch, nomme, Service(_corps("ok", modele=None)))
    with telemetry.execution(kind="report", document_id="D", provider="openrouter", model="openrouter/free"):
        llm.chat("s", "u", provider="openrouter", envoi=None)
    assert _lignes(usage_db) == [{"model": "openrouter/free", "model_servi": None}]


def test_l_execution_collecte_les_modeles_servis_distincts_dans_l_ordre(nomme, monkeypatch) -> None:
    _pret(monkeypatch, nomme, Service(_corps("a", modele="v/a"), _corps("b", modele="v/b"), _corps("c", modele="v/a")))
    with telemetry.execution(kind="report", document_id="D", provider="openrouter", model="openrouter/free") as ex:
        for _ in range(3):
            llm.chat("s", "u", provider="openrouter", envoi=None)
        assert ex.modeles_servis == ["v/a", "v/b"]


def test_le_detail_lu_de_l_execution_porte_le_modele_servi(nomme, monkeypatch, usage_db) -> None:
    _pret(monkeypatch, nomme, Service(_corps("ok", modele="vendeur/servi")))
    with telemetry.execution(kind="report", document_id="D", provider="openrouter", model="openrouter/free") as ex:
        llm.chat("s", "u", provider="openrouter", envoi=None)
        identifiant = ex.execution_id
    detail = usage_store.execution_detail(identifiant, path=usage_db)
    assert detail["calls"][0]["model_servi"] == "vendeur/servi"


def test_le_compte_rendu_dit_les_modeles_servis_dans_sa_provenance_et_pas_quand_il_n_y_en_a_pas() -> None:
    from core.experimental import app_report
    from tests.unit.test_local_report_seam import _meta, _synthetic_out
    avec = app_report._assemble_app_report("m.md", _meta(), "generique", _synthetic_out(), modeles_servis=["v/a", "v/b"])
    sans = app_report._assemble_app_report("m.md", _meta(), "generique", _synthetic_out())
    assert "models_served:" in avec and "v/a" in avec and "v/b" in avec
    assert "models_served" not in sans


# ---------- G3 : le paramètre de raisonnement documenté, envoyé par OpenRouter seulement ----------

def _corps_envoye(service) -> dict:
    return json.loads(service.requetes[0].content)


def test_openrouter_demande_un_raisonnement_borne_et_groq_n_envoie_rien(nomme, monkeypatch) -> None:
    """Documenté (page « reasoning tokens ») : `reasoning.effort` alloue une PART de la limite de sortie (« low » : environ 20 %),
    pour les modèles qui le gèrent. `exclude` ne ferait que cacher le raisonnement : il n'est pas envoyé."""
    for nom, attendu in (("openrouter", {"effort": "low"}), ("groq", None)):
        service = Service(_corps("ok"))
        _pret(monkeypatch, nomme, service, nom=nom, modele="un-modele")
        llm.chat("s", "u", provider=nom, envoi=None)
        corps = _corps_envoye(service)
        assert corps.get("reasoning") == attendu, nom
        assert "exclude" not in json.dumps(corps)


def test_le_parametre_vient_du_profil_et_aucun_autre_champ_inconnu_n_est_envoye(nomme, monkeypatch) -> None:
    assert nomme.PROFILS["openrouter"].parametres_requete == {"reasoning": {"effort": "low"}}
    assert nomme.PROFILS["groq"].parametres_requete == {}
    service = Service(_corps("ok"))
    _pret(monkeypatch, nomme, service)
    llm.chat("s", "u", provider="openrouter", envoi=None)
    assert set(_corps_envoye(service)) == {"model", "messages", "temperature", "stream", "reasoning"}


# ---------- G2 : du transport au document, par le VRAI chemin make_local_report ----------

def test_le_compte_rendu_ecrit_dit_le_modele_qui_a_repondu(monkeypatch, tmp_path, isolated_stores, nomme) -> None:
    """Bout en bout : un appel réel du moteur nommé (transport simulé) pendant `run_stages` pose le modèle servi dans l'exécution ;
    `make_local_report` l'écrit dans le front matter du document produit (`models_served`)."""
    from core import report as report_mod
    from core.experimental import app_report
    from tests.unit.test_cr_fiabilite import _meta, _rendu, _sortie
    source = tmp_path / "essai.md"
    source.write_text("---\ntitle: Essai\n---\n\n" + _rendu(diarise=False, horodatage=True), encoding="utf-8")
    item = {"meta": _meta(), "body": source.read_text(encoding="utf-8")}
    sortie = _sortie({"g1": "T1"}, [{"theme": "T1", "label": "Prix", "unit_count": 1,
                                      "sentences": [{"text": "Prix fixé.", "support": ["g1"]}]}])
    sortie["diag"]["totals"].update(kept_sentences=1)
    service = Service({"model": "vendeur/modele-reel:free", "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})
    _pret(monkeypatch, nomme, service, modele="openrouter/free")

    def _etapes(*a, **k):
        llm.chat("s", "u", provider="openrouter", envoi=None)
        return sortie
    monkeypatch.setattr(app_report.library, "get_transcript", lambda name: item)
    monkeypatch.setattr(app_report.library, "transcript_path", lambda name: source)
    monkeypatch.setattr(app_report.library, "sync_file", lambda path: None)
    monkeypatch.setattr(app_report.semantic, "index_name", lambda name: 0)
    monkeypatch.setattr(app_report._pipeline, "run_stages", _etapes)
    monkeypatch.setattr(report_mod, "classify_genre", lambda *a, **k: "projet")

    resultat = app_report.make_local_report("essai.md", provider="openrouter")

    texte = (tmp_path / resultat["name"]).read_text(encoding="utf-8")
    assert "models_served:" in texte and "vendeur/modele-reel:free" in texte


def test_une_usage_db_d_avant_la_tranche_g_est_mise_a_niveau_et_garde_ses_lignes(nomme, monkeypatch, usage_db) -> None:
    """Une base déjà installée n'a pas la colonne `model_servi` (le `CREATE` ne la rattrape pas) : la migration additive doit
    l'ajouter, sans perdre les lignes existantes, sinon chaque enregistrement d'appel échouerait sans bruit (la télémétrie ne
    lève jamais). Écrit APRÈS le code ; sa preuve est le mutant « colonne retirée de `_ADDED_COLUMNS` »."""
    conn = usage_store.connect(usage_db)
    conn.execute("ALTER TABLE model_call DROP COLUMN model_servi")     # l'état d'une base d'avant G
    conn.commit()
    colonnes = {r["name"] for r in conn.execute("PRAGMA table_info(model_call)")}
    conn.close()
    assert "model_servi" not in colonnes, "le point de départ doit être l'ancien schéma"

    _pret(monkeypatch, nomme, Service(_corps("ok", modele="vendeur/modele-apres-migration")))
    with telemetry.execution(kind="report", document_id="D", provider="openrouter", model="openrouter/free") as ex:
        llm.chat("s", "u", provider="openrouter", envoi=None)
        identifiant = ex.execution_id

    detail = usage_store.execution_detail(identifiant, path=usage_db)
    assert [c["model_servi"] for c in detail["calls"]] == ["vendeur/modele-apres-migration"]
    assert [c["model"] for c in detail["calls"]] == ["openrouter/free"]
