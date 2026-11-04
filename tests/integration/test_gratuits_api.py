"""Les modèles gratuits vus par l'API (chantier 006, tranche F) : un refus par route, à corps plat, avant toute réservation.

Un modèle que le service marque gratuit ne reçoit JAMAIS un document « ultra-confidentiel » (décision du 4/11, 13:55 :
« public · normal » et « confidentiel » passent). Pour chaque route qui envoie le texte d'un document à un modèle de langage :

- un document « ultra-confidentiel » AUTORISÉ au nuage avec un modèle gratuit : 403 `{detail, code: "model.free.restricted"}`, rien de
  créé, rien reçu ;
- l'ultra-confidentiel NON autorisé au nuage garde sa phrase d'avant (`REFUS_CLOUD`, sans code) : le refus du nuage vient d'abord ;
- « public · normal », « confidentiel », un modèle non gratuit, Groq (jamais gratuit) : acceptés.

Le piège de la lecture du code : `_refuser_le_cloud` rend la main tout de suite pour un document « confidentiel » ; la règle des
gratuits est un appel SÉPARÉ, placé juste après lui à chaque point d'appel.

Aucun appel réel : un transport simulé note les requêtes (aucune ne doit partir pour un refus). Les moteurs de transcription
et le pipeline sont remplacés comme dans `test_ultra_confidentiel.py`.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from api import main as api_main
from api.jobs import JobManager
from core import docid, ecl, projects, semantic, sensitivity
from tests.conftest import COMPTE_DE_TEST
from tests.unit.test_llm_nomme import CLE, Transport

pytestmark = pytest.mark.integration

# Le vrai `ecl._modele`, relevé à l'import : `tests/conftest.py` le remplace pour chaque test.
_MODELE_ECL = ecl._modele
GRATUIT = "auteur/un-modele:free"
PAYANT = "auteur/un-modele-payant"
NOMS = ["openrouter", "groq"]


@pytest.fixture(autouse=True)
def _faits_actives(monkeypatch):
    from core import config as _config

    monkeypatch.setattr(_config, "FAITS", True)


@pytest.fixture(autouse=True)
def environnement_propre(monkeypatch):
    for nom in NOMS:
        for variable in (f"{nom.upper()}_API_KEY", f"MEMOGLYPH_{nom.upper()}_MODEL", f"{nom.upper()}_MODEL"):
            monkeypatch.setenv(variable, "x")
            monkeypatch.delenv(variable)


@pytest.fixture
def transport(monkeypatch):
    nomme = importlib.import_module("core.llm_nomme")
    t = Transport(contenu="réponse [1]")
    monkeypatch.setattr(nomme, "client_sans_fuite", t.fabrique)
    return t


def _prets(monkeypatch, modele: str, noms=NOMS) -> None:
    nomme = importlib.import_module("core.llm_nomme")
    for nom in noms:
        monkeypatch.setenv(nomme.PROFILS[nom].variable_cle, CLE)
        monkeypatch.setenv(f"MEMOGLYPH_{nomme.PROFILS[nom].variable_modele}", modele)


@pytest.fixture
def client(monkeypatch, tmp_path, isolated_stores, transport):
    monkeypatch.setattr(api_main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(api_main, "jobs", JobManager())
    return TestClient(api_main.app)


@pytest.fixture
def inbox(isolated_stores):
    return projects.create(isolated_stores["output_dir"], name=projects.INBOX_NAME, kind="inbox",
                           owner=COMPTE_DE_TEST)


def _document(isolated_stores, inbox, nom: str, sensibilite: str, reunion: str | None = None) -> str:
    chemin = isolated_stores["output_dir"] / nom
    chemin.write_text(
        f"---\nid: {docid.nouveau()}\nmeeting_id: {reunion or docid.nouveau()}\nproject_id: {inbox.id}\n"
        f"title: {nom}\nlanguage: fr\nsensitivity: {sensibilite}\n---\n\n## Transcript\n\n"
        "[00:00:01] Le rachat se fait à douze millions.\n",
        encoding="utf-8")
    api_main.library.sync_file(chemin)
    return nom


def _est_le_refus_des_gratuits(reponse) -> None:
    assert reponse.status_code == 403
    corps = reponse.json()
    assert corps["code"] == "model.free.restricted"
    assert corps["detail"] == sensitivity.REFUS_MODELE_GRATUIT
    assert corps["detail"] != sensitivity.REFUS_CLOUD
    assert set(corps) == {"detail", "code"}, "corps plat : une phrase et un code, rien d'autre"


# ---------- Transcription : seulement la partie « modèle de langage » ----------

def _transcrire(client, **champs):
    return client.post("/api/transcribe", files={"file": ("reunion.wav", b"audio")},
                       data={"engine": "whisper", **champs})


@pytest.fixture
def pipeline_inerte(monkeypatch):
    lances: list = []
    monkeypatch.setattr(api_main.pipeline, "run", lambda *a, **k: lances.append(k) or None)
    return lances


@pytest.mark.parametrize("champs", [{"report_on": "true"}, {"report_on": "false", "profile": "true"}],
                         ids=["compte_rendu", "profil"])
def test_la_transcription_refuse_un_gratuit_pour_un_ultra_autorise_sans_rien_recevoir(
        champs, client, inbox, transport, monkeypatch, tmp_path, pipeline_inerte) -> None:
    _prets(monkeypatch, GRATUIT, ["openrouter"])
    monkeypatch.setattr(sensitivity, "autorise", lambda proprietaire: True)       # le cloud est autorisé : c'est la règle des gratuits
    reponse = _transcrire(client, sensitivity_level="ultra_confidential", llm_provider="openrouter", **champs)
    _est_le_refus_des_gratuits(reponse)
    assert not (tmp_path / "uploads").exists(), "rien n'a été reçu ni écrit"
    assert pipeline_inerte == [] and client.get("/api/jobs").json()["traitements"] == []


@pytest.mark.parametrize("champs", [{"report_on": "true"}, {"report_on": "false", "profile": "true"}],
                         ids=["compte_rendu", "profil"])
def test_la_transcription_accepte_un_gratuit_pour_un_confidentiel(
        champs, client, inbox, monkeypatch, pipeline_inerte) -> None:
    _prets(monkeypatch, GRATUIT, ["openrouter"])
    reponse = _transcrire(client, sensitivity_level="confidential", llm_provider="openrouter", **champs)
    assert reponse.status_code == 200 and reponse.json()["job_id"]


def test_la_transcription_sans_compte_rendu_ni_profil_ne_concerne_pas_le_modele(
        client, inbox, monkeypatch, pipeline_inerte) -> None:
    """La règle vise l'envoi de texte à un modèle de langage, jamais le moteur de transcription."""
    _prets(monkeypatch, GRATUIT, ["openrouter"])
    reponse = _transcrire(client, sensitivity_level="confidential", llm_provider="openrouter",
                          report_on="false", profile="false")
    assert reponse.status_code == 200 and reponse.json()["job_id"]


@pytest.mark.parametrize("nom,modele,sensibilite", [
    ("openrouter", GRATUIT, "standard"),          # public · normal : accepté
    ("openrouter", PAYANT, "confidential"),       # non gratuit : règle ordinaire
    ("groq", GRATUIT, "confidential"),            # Groq : jamais gratuit
], ids=["public_normal", "non_gratuit", "groq"])
def test_la_transcription_accepte_ce_que_la_regle_ne_vise_pas(
        nom, modele, sensibilite, client, inbox, monkeypatch, pipeline_inerte) -> None:
    _prets(monkeypatch, modele)
    reponse = _transcrire(client, sensitivity_level=sensibilite, llm_provider=nom, report_on="true")
    assert reponse.status_code == 200 and reponse.json()["job_id"]


def test_l_ultra_non_autorise_garde_la_phrase_du_nuage_et_pas_le_code_des_gratuits(
        client, inbox, monkeypatch, tmp_path, pipeline_inerte) -> None:
    _prets(monkeypatch, GRATUIT, ["openrouter"])
    reponse = _transcrire(client, sensitivity_level="ultra_confidential", llm_provider="openrouter", report_on="true")
    assert reponse.status_code == 403 and reponse.json() == {"detail": sensitivity.REFUS_CLOUD}
    assert not (tmp_path / "uploads").exists()


def test_l_ultra_autorise_au_nuage_est_refuse_quand_meme_par_la_regle_des_gratuits(
        client, inbox, monkeypatch, tmp_path, pipeline_inerte) -> None:
    _prets(monkeypatch, GRATUIT, ["openrouter"])
    monkeypatch.setattr(sensitivity, "autorise", lambda proprietaire: True)
    reponse = _transcrire(client, sensitivity_level="ultra_confidential", llm_provider="openrouter", report_on="true")
    _est_le_refus_des_gratuits(reponse)
    assert not (tmp_path / "uploads").exists()


# ---------- Compte rendu : la route synchrone, la route par traitement ----------

def _routes_compte_rendu(nom):
    return [
        ("synchrone", f"/api/transcript/{nom}/report"),
        ("traitement", f"/api/transcript/{nom}/report/jobs"),
    ]


@pytest.mark.parametrize("route", ["synchrone", "traitement"])
def test_le_compte_rendu_accepte_un_gratuit_pour_un_confidentiel(
        route, client, isolated_stores, inbox, monkeypatch) -> None:
    from core import report
    vus: list = []
    monkeypatch.setattr(report, "generate_report", lambda nom, **k: vus.append(k["provider"]) or {"ok": 1})
    _prets(monkeypatch, GRATUIT, ["openrouter"])
    nom = _document(isolated_stores, inbox, "reunion.md", "confidential")
    chemin = dict(_routes_compte_rendu(nom))[route]
    reponse = client.post(chemin, params={"view_scope": inbox.id}, json={"llm_provider": "openrouter"})
    assert reponse.status_code == 200, reponse.text


@pytest.mark.parametrize("route", ["synchrone", "traitement"])
def test_le_compte_rendu_ultra_non_autorise_garde_la_phrase_du_nuage(
        route, client, isolated_stores, inbox, monkeypatch) -> None:
    _prets(monkeypatch, GRATUIT, ["openrouter"])
    nom = _document(isolated_stores, inbox, "reunion.md", "ultra_confidential")
    reponse = client.post(dict(_routes_compte_rendu(nom))[route], params={"view_scope": inbox.id},
                          json={"llm_provider": "openrouter"})
    assert reponse.status_code == 403 and reponse.json() == {"detail": sensitivity.REFUS_CLOUD}


@pytest.mark.parametrize("route", ["synchrone", "traitement"])
def test_le_compte_rendu_ultra_autorise_est_refuse_quand_meme_par_la_regle_des_gratuits(
        route, client, isolated_stores, inbox, monkeypatch) -> None:
    _prets(monkeypatch, GRATUIT, ["openrouter"])
    monkeypatch.setattr(sensitivity, "autorise", lambda proprietaire: True)
    nom = _document(isolated_stores, inbox, "reunion.md", "ultra_confidential")
    reponse = client.post(dict(_routes_compte_rendu(nom))[route], params={"view_scope": inbox.id},
                          json={"llm_provider": "openrouter"})
    _est_le_refus_des_gratuits(reponse)


@pytest.mark.parametrize("nom_fournisseur,modele,sensibilite", [
    ("openrouter", GRATUIT, "standard"), ("openrouter", PAYANT, "confidential"), ("groq", GRATUIT, "confidential"),
], ids=["public_normal", "non_gratuit", "groq"])
def test_le_compte_rendu_accepte_ce_que_la_regle_ne_vise_pas(
        nom_fournisseur, modele, sensibilite, client, isolated_stores, inbox, monkeypatch) -> None:
    from core import report
    vus: list = []
    monkeypatch.setattr(report, "generate_report", lambda nom, **k: vus.append(k["provider"]) or {"ok": 1})
    _prets(monkeypatch, modele)
    nom = _document(isolated_stores, inbox, "reunion.md", sensibilite)
    reponse = client.post(f"/api/transcript/{nom}/report", params={"view_scope": inbox.id},
                          json={"llm_provider": nom_fournisseur})
    assert reponse.status_code == 200 and vus == [nom_fournisseur]


# ---------- Relance des faits ----------

def test_la_relance_des_faits_accepte_un_gratuit_pour_un_confidentiel(
        client, isolated_stores, inbox, monkeypatch) -> None:
    lances: list = []
    monkeypatch.setattr(api_main.jobs, "run", lambda job, target: lances.append(job))
    _prets(monkeypatch, GRATUIT, ["openrouter"])
    reunion = docid.nouveau()
    _document(isolated_stores, inbox, "rachat.md", "confidential", reunion=reunion)
    reponse = client.post(f"/api/meetings/{reunion}/ecl", params={"view_scope": inbox.id},
                          json={"provider": "openrouter"})
    assert reponse.status_code == 200 and len(lances) == 1


def test_la_relance_des_faits_ultra_non_autorisee_garde_la_phrase_du_nuage(
        client, isolated_stores, inbox, monkeypatch) -> None:
    _prets(monkeypatch, GRATUIT, ["openrouter"])
    reunion = docid.nouveau()
    _document(isolated_stores, inbox, "rachat.md", "ultra_confidential", reunion=reunion)
    reponse = client.post(f"/api/meetings/{reunion}/ecl", params={"view_scope": inbox.id},
                          json={"provider": "openrouter"})
    assert reponse.status_code == 403 and reponse.json() == {"detail": sensitivity.REFUS_CLOUD}


def test_la_relance_des_faits_ultra_autorisee_est_refusee_quand_meme_par_la_regle_des_gratuits(
        client, isolated_stores, inbox, monkeypatch) -> None:
    _prets(monkeypatch, GRATUIT, ["openrouter"])
    monkeypatch.setattr(sensitivity, "autorise", lambda proprietaire: True)
    reunion = docid.nouveau()
    _document(isolated_stores, inbox, "rachat.md", "ultra_confidential", reunion=reunion)
    reponse = client.post(f"/api/meetings/{reunion}/ecl", params={"view_scope": inbox.id},
                          json={"provider": "openrouter"})
    _est_le_refus_des_gratuits(reponse)


@pytest.mark.parametrize("nom,modele,sensibilite", [
    ("openrouter", GRATUIT, "standard"), ("openrouter", PAYANT, "confidential"), ("groq", GRATUIT, "confidential"),
], ids=["public_normal", "non_gratuit", "groq"])
def test_la_relance_des_faits_accepte_ce_que_la_regle_ne_vise_pas(
        nom, modele, sensibilite, client, isolated_stores, inbox, monkeypatch) -> None:
    lances: list = []
    monkeypatch.setattr(api_main.jobs, "run", lambda job, target: lances.append(job))
    _prets(monkeypatch, modele)
    reunion = docid.nouveau()
    _document(isolated_stores, inbox, "rachat.md", sensibilite, reunion=reunion)
    reponse = client.post(f"/api/meetings/{reunion}/ecl", params={"view_scope": inbox.id}, json={"provider": nom})
    assert reponse.status_code == 200 and len(lances) == 1


# ---------- L'assistant : le refus vient de la façade (pas de filtrage des passages) ----------

PASSAGE_CONFIDENTIEL = {"name": "point.md", "title": "Point", "ts": "00:00:05", "text": "Le budget est validé",
                        "sensitivity": "confidential", "project": "Non classé"}
PASSAGE_ORDINAIRE = dict(PASSAGE_CONFIDENTIEL, sensitivity="standard")
PASSAGE_ULTRA = dict(PASSAGE_CONFIDENTIEL, sensitivity="ultra_confidential")


def _demander(client, inbox, fournisseur):
    return client.post("/api/ask", json={"question": "Le budget ?", "llm_provider": fournisseur,
                                         "scope": {"mode": "project", "project_id": inbox.id}})


def test_l_assistant_refuse_un_gratuit_des_qu_un_passage_est_ultra_autorise_sans_le_filtrer(
        client, inbox, transport, monkeypatch) -> None:
    """Décision : pas de filtrage silencieux (une réponse sans le passage dirait autre chose que les sources). Un refus dit.
    Le passage « ultra-confidentiel » est autorisé au nuage : il passe le filtre de `rag.py` (qui ne retire que l'ultra non
    autorisé) et c'est la façade qui refuse."""
    _prets(monkeypatch, GRATUIT, ["openrouter"])
    monkeypatch.setattr(sensitivity, "autorise", lambda proprietaire: True)
    monkeypatch.setattr(semantic, "retrieve", lambda q, **k: [dict(PASSAGE_ORDINAIRE), dict(PASSAGE_ULTRA, name="b.md")])
    reponse = _demander(client, inbox, "openrouter")
    _est_le_refus_des_gratuits(reponse)
    assert transport.requetes == [], "aucune requête n'est partie vers le service"


def test_l_assistant_accepte_un_gratuit_pour_des_passages_public_normal_ou_confidentiels(client, inbox, transport, monkeypatch) -> None:
    _prets(monkeypatch, GRATUIT, ["openrouter"])
    monkeypatch.setattr(semantic, "retrieve", lambda q, **k: [dict(PASSAGE_ORDINAIRE), dict(PASSAGE_CONFIDENTIEL, name="b.md")])
    reponse = _demander(client, inbox, "openrouter")
    assert reponse.status_code == 200 and len(transport.requetes) == 1


def test_l_assistant_groq_ou_modele_payant_garde_les_passages_confidentiels(client, inbox, transport, monkeypatch) -> None:
    _prets(monkeypatch, PAYANT, ["openrouter"])
    _prets(monkeypatch, GRATUIT, ["groq"])
    monkeypatch.setattr(semantic, "retrieve", lambda q, **k: [dict(PASSAGE_CONFIDENTIEL)])
    assert _demander(client, inbox, "openrouter").status_code == 200
    assert _demander(client, inbox, "groq").status_code == 200
    assert len(transport.requetes) == 2


# ---------- /api/config : inchangé par la règle ----------

def test_la_configuration_n_annonce_aucune_gratuite(client, monkeypatch) -> None:
    """La règle s'applique à l'envoi, pas à l'affichage : `/api/config` rend la même chose avec un modèle gratuit ou payant,
    au nom du modèle près."""
    _prets(monkeypatch, GRATUIT)
    gratuit = client.get("/api/config").json()
    _prets(monkeypatch, PAYANT)
    payant = client.get("/api/config").json()
    assert "openrouter" in gratuit["llm_providers"] and "groq" in gratuit["llm_providers"]
    assert gratuit.keys() == payant.keys()
    assert {k: v for k, v in gratuit.items() if k != "llm_models"} == {k: v for k, v in payant.items() if k != "llm_models"}


# ---------- En cours de travail : le refus du cœur garde SON code, jamais la phrase de l'ultra-confidentiel ----------

def _attendre(client, job_id: str) -> dict:
    import time
    for _ in range(2000):
        etat = client.get(f"/api/jobs/{job_id}").json()
        if etat["status"] in {"done", "error"}:
            return etat
        time.sleep(0.005)
    raise AssertionError("traitement sans fin")


def test_la_relance_des_faits_devenue_ultra_autorisee_pendant_le_travail_echoue_avec_le_code(
        client, isolated_stores, inbox, transport, monkeypatch) -> None:
    _prets(monkeypatch, GRATUIT, ["openrouter"])
    monkeypatch.setattr(ecl, "_modele", _MODELE_ECL)
    reunion = docid.nouveau()
    _document(isolated_stores, inbox, "rachat.md", "standard", reunion=reunion)
    monkeypatch.setattr(sensitivity, "_resolveur_sensibilite", lambda document_id: "ultra_confidential")
    monkeypatch.setattr(sensitivity, "autorise", lambda proprietaire: True)       # le cloud est autorisé : c'est la règle des gratuits
    reponse = client.post(f"/api/meetings/{reunion}/ecl", params={"view_scope": inbox.id}, json={"provider": "openrouter"})
    assert reponse.status_code == 200, reponse.text
    etat = _attendre(client, reponse.json()["job_id"])
    assert etat["status"] == "error" and etat["error_code"] == "step.error.freeModelRestricted"
    assert transport.requetes == [], "rien n'est parti vers le service"


def test_ancre_sans_changement_de_sensibilite_la_relance_des_faits_atteint_le_service(
        client, isolated_stores, inbox, transport, monkeypatch) -> None:
    """L'ancre du test précédent : même document, même modèle gratuit, sans relecture « confidentiel » — l'appel PART."""
    _prets(monkeypatch, GRATUIT, ["openrouter"])
    monkeypatch.setattr(ecl, "_modele", _MODELE_ECL)
    reunion = docid.nouveau()
    _document(isolated_stores, inbox, "rachat.md", "standard", reunion=reunion)
    _attendre(client, client.post(f"/api/meetings/{reunion}/ecl", params={"view_scope": inbox.id},
                                  json={"provider": "openrouter"}).json()["job_id"])
    assert len(transport.requetes) >= 1


def test_la_route_synchrone_arretee_en_cours_de_travail_rend_le_code_des_gratuits_pas_la_phrase_du_nuage(
        client, isolated_stores, inbox, transport, monkeypatch) -> None:
    from core import report

    def generer(nom, **k):
        llm_facade = importlib.import_module("core.llm")
        llm_facade.chat("s", "u", provider=k["provider"],
                        envoi=sensitivity.Envoi(sensibilite="standard", proprietaire=None, document_id="doc-1"))
        return {"ok": 1}

    monkeypatch.setattr(report, "generate_report", generer)
    _prets(monkeypatch, GRATUIT, ["openrouter"])
    nom = _document(isolated_stores, inbox, "reunion.md", "standard")
    monkeypatch.setattr(sensitivity, "_resolveur_sensibilite", lambda document_id: "ultra_confidential")
    monkeypatch.setattr(sensitivity, "autorise", lambda proprietaire: True)       # le cloud est autorisé : c'est la règle des gratuits
    reponse = client.post(f"/api/transcript/{nom}/report", params={"view_scope": inbox.id}, json={"llm_provider": "openrouter"})
    _est_le_refus_des_gratuits(reponse)
    assert transport.requetes == []


def test_l_assistant_sur_un_quota_atteint_rend_un_503_a_corps_plat_avec_son_code(
        client, inbox, transport, monkeypatch) -> None:
    """Chemin réel de l'Assistant : un quota atteint arrive en 503 « Assistant indisponible : Quota atteint chez… » ET porte
    `code: "step.error.rateLimited"` (corps plat : une phrase et un code), que l'interface traduit. Jamais un 429 : le 429
    veut dire « occupé » partout ailleurs. (Test modifié à la retouche 2 : il ne vérifiait que la phrase.)"""
    _prets(monkeypatch, PAYANT, ["openrouter"])
    transport.statut = 429
    monkeypatch.setattr(semantic, "retrieve", lambda q, **k: [dict(PASSAGE_ORDINAIRE)])
    reponse = _demander(client, inbox, "openrouter")
    assert reponse.status_code == 503
    corps = reponse.json()
    assert corps["detail"].startswith("Assistant indisponible : Quota atteint chez OpenRouter")
    assert corps["code"] == "step.error.rateLimited" and set(corps) == {"detail", "code"}
    assert "corps-brut-du-service" not in reponse.text


@pytest.mark.parametrize("statut", [500, 503])
def test_une_autre_panne_du_moteur_reste_un_503_sans_code(statut, client, inbox, transport, monkeypatch) -> None:
    """Le code n'est posé que pour un quota : toute autre panne garde le 503 d'avant, sans `code`."""
    _prets(monkeypatch, PAYANT, ["openrouter"])
    transport.statut = statut
    monkeypatch.setattr(semantic, "retrieve", lambda q, **k: [dict(PASSAGE_ORDINAIRE)])
    reponse = _demander(client, inbox, "openrouter")
    assert reponse.status_code == 503
    assert set(reponse.json()) == {"detail"} and reponse.json()["detail"].startswith("Assistant indisponible : ")
    assert "Quota atteint" not in reponse.text


def test_constat_la_relance_des_faits_sur_un_quota_total_echoue_en_erreur_inattendue_sans_la_phrase(
        client, isolated_stores, inbox, transport, monkeypatch) -> None:
    from core import attente_quota                     # tranche H : le 429 est attendu puis réessayé ; la pause est remplacée
    monkeypatch.setattr(attente_quota, "dormir", lambda secondes: None)
    """CONSTAT (chemin réel de la relance des faits, vrai moteur, vraie façade, tous les appels refusés par un 429) :
    `ecl._extraire` absorbe chaque tranche puis lève `ExtractionEnPanne`, qui n'est pas au tableau des codes : le traitement
    échoue avec `step.error.unexpected`, et son texte ne dit pas le quota (les causes sont dans le journal de l'ECL)."""
    _prets(monkeypatch, PAYANT, ["openrouter"])
    monkeypatch.setattr(ecl, "_modele", _MODELE_ECL)
    transport.statut = 429
    reunion = docid.nouveau()
    _document(isolated_stores, inbox, "rachat.md", "standard", reunion=reunion)
    reponse = client.post(f"/api/meetings/{reunion}/ecl", params={"view_scope": inbox.id}, json={"provider": "openrouter"})
    assert reponse.status_code == 200, reponse.text
    etat = _attendre(client, reponse.json()["job_id"])
    assert etat["status"] == "error" and etat["error_code"] == "step.error.unexpected"
    assert "Quota atteint" not in (etat["error"] or "")
    assert len(transport.requetes) >= 1, "ancre : le service simulé a bien été joint"
