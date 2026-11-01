"""Sensibilité effective par réunion, valeur refusée, relue à chaque appel, hôte MLX distant.

Les quatre règles de routage par sensibilité (T1 à T4), prouvées sur la vraie chaîne : `.md` écrits sur disque,
vrai index, vraies routes, vraie façade `llm` et sa règle d'envoi. Seuls les
embeddings et les moteurs sont simulés ; un moteur cloud note chaque requête, rien
ne sort de la machine.

- T1 : un transcript `standard` d'une réunion qui contient un ultra-confidentiel est
  ultra-confidentiel pour le CR et l'ECL, comme pour l'Assistant et le MCP ; les
  quatre lisent le MÊME calcul (`library.sensibilite_effective`).
- T2 : `PATCH /api/transcript/{name}/sensitivity` refuse une valeur inconnue au lieu
  de la ramener à `standard`.
- T3 : un document passé en ultra-confidentiel pendant un travail cloud arrête
  l'appel suivant : la façade relit la sensibilité à chaque appel.
- T4 : un `MLX_HOST` hors de la boucle locale est un fournisseur cloud.
"""
from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient

from api import auth
from api import main as api_main
from api import mcp
from api.jobs import JobManager
from core import config, docid, ecl, library, llm, projects, report, semantic, sensitivity
from core.config import EMBED_DIM
from tests.conftest import COMPTE_DE_TEST, compte_de_test

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _faits_actives(monkeypatch):
    """Ces tests supposent les faits de la réunion activés (`MEMOGLYPH_FAITS=1`)."""
    from core import config as _config

    monkeypatch.setattr(_config, "FAITS", True)

CLOUD = ("mistral", "openai", "anthropic")
ULTRA = sensitivity.ULTRA_CONFIDENTIEL
SECRET = "RACHAT-SECRET"
# Le vrai `ecl._modele`, relevé à l'import : `tests/conftest.py` le remplace pour chaque test.
_MODELE_ECL = ecl._modele


def _vecteur(texte: str) -> list[float]:
    v = [0.0] * EMBED_DIM
    v[0 if "rachat" in texte.lower() else 1] = 1.0
    return v


class FauxMoteur:
    """Un moteur LLM qui note la requête entière et répond à toutes les étapes du CR."""

    def __init__(self, nom: str, requetes: list) -> None:
        self.name = nom
        self.requetes = requetes

    def is_available(self, *, api_key=None) -> bool:
        return True

    def pret_declare(self) -> bool:
        return True

    def modele_servi(self, model=None) -> str:
        return model or f"faux-{self.name}"

    def chat(self, system, user, **options):
        self.requetes.append((self.name, f"{system}\n{user}\n{options!r}"))
        return "réponse [1]"

    def chat_json(self, system, user, **options):
        self.requetes.append((self.name, f"{system}\n{user}\n{options!r}"))
        return {"units": [{"kind": "fact", "text": "Le rachat est validé.", "start": "00:00:01"}],
                "assignments": [], "sentences": [], "sections": [], "thesis": {}, "tensions": [],
                "faits": [], "conversation": {"type": "réunion", "summary": "s", "topics": []},
                "speakers": {}}


@pytest.fixture
def chaine(isolated_stores, tmp_path, monkeypatch):
    monkeypatch.setattr(api_main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(api_main, "jobs", JobManager())
    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(tmp_path / "usage.db"))
    monkeypatch.setattr(semantic.embed, "embed_documents", lambda textes, **_k: [_vecteur(t) for t in textes])
    monkeypatch.setattr(semantic.embed, "embed_query", lambda t, **_k: _vecteur(t))
    requetes: list = []
    for nom in (*CLOUD, "mlx"):
        monkeypatch.setitem(llm.MOTEURS, nom, FauxMoteur(nom, requetes))
    # Le compte de la session n'autorise pas le cloud pour son ultra-confidentiel (défaut).
    inbox = projects.create(isolated_stores["output_dir"], name=projects.INBOX_NAME, kind="inbox",
                            owner=COMPTE_DE_TEST)
    yield {"client": TestClient(api_main.app), "inbox": inbox, "requetes": requetes,
           "sortie": isolated_stores["output_dir"]}
    # Le compte de la session vit toute la session : un réglage activé ici n'en sort pas.
    auth.regler_cloud_ultra(compte_de_test(), False)


def _ecrire(chaine, nom: str, sensibilite: str, reunion: str | None = None,
            texte: str = f"Le rachat se fera à {SECRET} millions.", decalage: float = 0.0) -> dict:
    identite, reunion = docid.nouveau(), reunion or docid.nouveau()
    chemin = chaine["sortie"] / nom
    chemin.write_text(
        f"---\nid: {identite}\nmeeting_id: {reunion}\nproject_id: {chaine['inbox'].id}\n"
        f"title: {nom}\nlanguage: fr\nsensitivity: {sensibilite}\n---\n\n## Transcript\n\n"
        f"**Alice** [00:00:01] {texte}\n**Bruno** [00:00:09] Le calendrier du rachat est tenu.\n",
        encoding="utf-8")
    if decalage:
        moment = time.time() + decalage
        os.utime(chemin, (moment, moment))
    return {"nom": nom, "id": identite, "reunion": reunion}


def _reunion_mixte(chaine) -> dict:
    """Une réunion à deux transcripts : l'ancien ultra-confidentiel, le plus récent standard.

    C'est ce que produit la migration d'identité, qui regroupe plusieurs transcripts
    d'une même source dans une réunion (`core/backfill_identity.py`).
    """
    ultra = _ecrire(chaine, "rachat-v1.md", ULTRA)
    standard = _ecrire(chaine, "rachat-v2.md", "standard", reunion=ultra["reunion"], decalage=60)
    _indexer()
    transcripts = library.get_meeting(ultra["reunion"])["transcripts"]
    # Précondition : la route de l'ECL retient `transcripts[0]`, ici le standard.
    assert [t["name"] for t in transcripts] == ["rachat-v2.md", "rachat-v1.md"]
    return {"ultra": ultra, "standard": standard, "reunion": ultra["reunion"]}


def _indexer() -> None:
    library.reindex()
    semantic.reindex_all()


def _cr(chaine, nom: str, fournisseur: str):
    return chaine["client"].post(f"/api/transcript/{nom}/report", params={"view_scope": chaine["inbox"].id},
                                 json={"llm_provider": fournisseur})


def _faits(chaine, reunion: str, fournisseur: str):
    return chaine["client"].post(f"/api/meetings/{reunion}/ecl", params={"view_scope": chaine["inbox"].id},
                                 json={"provider": fournisseur})


def _demander(chaine, fournisseur: str):
    return chaine["client"].post("/api/ask", json={
        "question": "Où en est le rachat ?", "llm_provider": fournisseur,
        "scope": {"mode": "project", "project_id": chaine["inbox"].id}})


def _lecteur() -> mcp.Lecteur:
    return mcp.Lecteur(auth.Utilisateur(id=COMPTE_DE_TEST, identifiant=COMPTE_DE_TEST, admin=False))


def _patch(chaine, nom: str, valeur: object):
    return chaine["client"].patch(f"/api/transcript/{nom}/sensitivity", params={"view_scope": chaine["inbox"].id},
                                  json={"sensitivity": valeur})


def _envoye(chaine, *morceaux: str) -> bool:
    return any(all(m in requete for m in morceaux) for _nom, requete in chaine["requetes"])


# ---------- T1 : une sensibilité effective par réunion ----------

@pytest.mark.parametrize("fournisseur", CLOUD)
def test_t1_le_cr_d_un_standard_d_une_reunion_ultra_est_refuse_en_cloud(chaine, monkeypatch,
                                                                         fournisseur) -> None:
    lances: list = []
    generer = report.generate_report
    monkeypatch.setattr(report, "generate_report", lambda *a, **k: lances.append(a) or generer(*a, **k))
    reunion = _reunion_mixte(chaine)
    reponse = _cr(chaine, reunion["standard"]["nom"], fournisseur)
    assert reponse.status_code == 403 and reponse.json()["detail"] == sensitivity.REFUS_CLOUD
    assert lances == [], "la route refuse avant de lancer le compte rendu"
    assert chaine["requetes"] == [], "rien n'est parti vers le fournisseur"


def test_t1_le_cr_d_un_standard_d_une_reunion_ultra_reste_possible_en_local(chaine) -> None:
    reunion = _reunion_mixte(chaine)
    reponse = _cr(chaine, reunion["standard"]["nom"], "mlx")
    assert reponse.status_code == 200, reponse.text
    assert {nom for nom, _r in chaine["requetes"]} == {"mlx"}


@pytest.mark.parametrize("fournisseur", CLOUD)
def test_t1_le_coeur_du_cr_refuse_aussi_sans_la_garde_de_route(chaine, fournisseur) -> None:
    reunion = _reunion_mixte(chaine)
    assert library.envoi_du_document(reunion["standard"]["nom"]).sensibilite == ULTRA
    with pytest.raises(sensitivity.EnvoiRefuse):
        report.generate_report(reunion["standard"]["nom"], provider=fournisseur)
    assert chaine["requetes"] == []


@pytest.mark.parametrize("fournisseur", CLOUD)
def test_t1_l_ecl_d_un_standard_d_une_reunion_ultra_est_refusee_en_cloud(chaine, monkeypatch,
                                                                           fournisseur) -> None:
    monkeypatch.setattr(ecl, "_modele", _MODELE_ECL)
    lances: list = []
    monkeypatch.setattr(api_main.jobs, "run", lambda job, target: lances.append(job))
    reunion = _reunion_mixte(chaine)
    reponse = _faits(chaine, reunion["reunion"], fournisseur)
    assert reponse.status_code == 403 and reponse.json()["detail"] == sensitivity.REFUS_CLOUD
    assert lances == [] and chaine["requetes"] == []
    # Le cœur refuse aussi, sans la garde de route.
    with pytest.raises(sensitivity.EnvoiRefuse):
        ecl.extraire(reunion["standard"]["nom"], provider=fournisseur)
    assert chaine["requetes"] == []


def test_t1_l_ecl_d_un_standard_d_une_reunion_ultra_reste_possible_en_local(chaine, monkeypatch) -> None:
    lances: list = []
    monkeypatch.setattr(api_main.jobs, "run", lambda job, target: lances.append(job))
    reunion = _reunion_mixte(chaine)
    assert _faits(chaine, reunion["reunion"], "mlx").status_code == 200 and len(lances) == 1


def test_t1_les_quatre_chemins_lisent_le_meme_calcul(chaine, monkeypatch) -> None:
    """CR, ECL, Assistant et MCP : remplacer le calcul les change tous les quatre."""
    lances: list = []
    monkeypatch.setattr(api_main.jobs, "run", lambda job, target: lances.append(job))
    doc = _ecrire(chaine, "point.md", "standard", texte="Le rachat du matériel est reporté.")
    _ecrire(chaine, "autre.md", "standard", texte="Le rachat des locaux avance.")
    _indexer()
    lecteur = _lecteur()

    # Sans témoin : un document standard, seul dans sa réunion, part partout.
    assert _cr(chaine, "point.md", "openai").status_code == 200
    assert _faits(chaine, doc["reunion"], "openai").status_code == 200 and len(lances) == 1
    assert _demander(chaine, "openai").status_code == 200
    assert lecteur.rechercher({"requete": "rachat"})["ecartes_ultra_confidentiels"] == 0
    assert lecteur.lister_reunions({})["ecartees_ultra_confidentielles"] == 0
    assert lecteur.lire_compte_rendu({"reunion": doc["reunion"]})["compte_rendu"]
    assert "faits" in lecteur.lire_faits({"reunion": doc["reunion"]})

    appels: list = []

    def temoin(sensibilite, meeting_id, project_id=None):
        """Ultra-confidentiel pour la seule réunion de `point.md`, le reste inchangé."""
        appels.append((sensibilite, meeting_id))
        return ULTRA if meeting_id == doc["reunion"] else sensitivity.normaliser(sensibilite)

    monkeypatch.setattr(library, "sensibilite_effective", temoin)
    chaine["requetes"].clear()

    def appele(etape) -> None:
        avant = len(appels)
        etape()
        assert len(appels) > avant, "ce chemin ne lit pas `library.sensibilite_effective`"

    appele(lambda: _verifier(_cr(chaine, "point.md", "openai").status_code == 403))
    appele(lambda: _verifier(_faits(chaine, doc["reunion"], "openai").status_code == 403))
    # L'Assistant écarte le passage AVANT l'invite, passage par passage : l'autre part.
    appele(lambda: _verifier(_assistant_ecarte(chaine, garde="autre.md")))
    appele(lambda: _verifier(lecteur.rechercher({"requete": "rachat"})["ecartes_ultra_confidentiels"] >= 1))
    appele(lambda: _verifier(lecteur.lister_reunions({})["ecartees_ultra_confidentielles"] == 1))
    for outil in (lecteur.lire_compte_rendu, lecteur.lire_faits):
        appele(lambda outil=outil: _verifier(_introuvable(outil, {"reunion": doc["reunion"]})))
    assert len(lances) == 1
    assert all(doc["nom"] not in requete and "matériel" not in requete for _n, requete in chaine["requetes"])

    # Le même calcul désigne aussi le propriétaire à consulter : autorisé, le passage part.
    assert chaine["client"].put("/api/compte/cloud-ultra", json={"autorise": True}).status_code == 200
    appele(lambda: _verifier("point.md" in {s["name"] for s in _demander(chaine, "openai").json()["sources"]}))


def _assistant_ecarte(chaine, *, garde: str) -> bool:
    reponse = _demander(chaine, "openai")
    corps = reponse.json()
    return (reponse.status_code == 200 and corps.get("passages_ecartes", 0) >= 1
            and {s["name"] for s in corps["sources"]} == {garde})


def _verifier(condition: bool) -> None:
    assert condition


def _introuvable(outil, params: dict) -> bool:
    try:
        outil(params)
    except mcp.Introuvable:
        return True
    return False


# ---------- T2 : une valeur inconnue est refusée ----------

def test_t2_le_patch_refuse_une_sensibilite_inconnue(chaine) -> None:
    doc = _ecrire(chaine, "rachat.md", ULTRA)
    _indexer()
    for valeur in ("ultra-confidential", "ULTRA_CONFIDENTIAL", "", None, 3):
        reponse = _patch(chaine, doc["nom"], valeur)
        assert reponse.status_code in (400, 422), (valeur, reponse.status_code)
        assert library.get_transcript(doc["nom"])["meta"]["sensitivity"] == ULTRA, "rien n'a été rétrogradé"
    reponse = _patch(chaine, doc["nom"], "confidential")
    assert reponse.status_code == 200 and reponse.json()["sensitivity"] == "confidential"
    # La lecture reste douce : une valeur inconnue sur le disque se lit `standard`, sans lever.
    assert sensitivity.normaliser("ultra-confidential") == "standard"


# ---------- T3 : la sensibilité relue à chaque appel ----------

def test_t3_passer_en_ultra_pendant_l_ecl_arrete_l_appel_suivant(chaine, monkeypatch) -> None:
    monkeypatch.setattr(ecl, "_modele", _MODELE_ECL)
    monkeypatch.setattr(ecl, "BORNE_TRANCHE", 330)   # une réplique par tranche : plusieurs appels
    doc = _ecrire(chaine, "point.md", "standard")
    _indexer()
    moteur = llm.MOTEURS["openai"]
    repondre = moteur.chat_json

    def puis_basculer(system, user, **options):
        if not chaine["requetes"]:   # au premier appel, la route de modification, pendant le travail
            assert _patch(chaine, doc["nom"], ULTRA).status_code == 200
        return repondre(system, user, **options)

    moteur.chat_json = puis_basculer
    with pytest.raises(sensitivity.EnvoiRefuse):
        ecl.extraire(doc["nom"], provider="openai")
    assert len(chaine["requetes"]) == 1, "un seul appel est parti avant la bascule"


def test_t3_passer_en_ultra_pendant_un_cr_arrete_l_appel_suivant(chaine) -> None:
    doc = _ecrire(chaine, "point.md", "standard")
    _indexer()
    moteur = llm.MOTEURS["openai"]
    repondre = moteur.chat_json

    def puis_basculer(system, user, **options):
        if not chaine["requetes"]:
            assert library.set_sensitivity(doc["nom"], ULTRA) == ULTRA
        return repondre(system, user, **options)

    moteur.chat_json = puis_basculer
    reponse = _cr(chaine, doc["nom"], "openai")
    assert reponse.status_code == 403 and reponse.json()["detail"] == sensitivity.REFUS_CLOUD
    assert len(chaine["requetes"]) == 1, "un seul appel est parti avant la bascule"
    assert not [d for d in library.list_transcripts() if d.get("type") == "report"]


def test_t3_un_autre_document_de_la_reunion_passe_en_ultra_arrete_aussi(chaine) -> None:
    doc = _ecrire(chaine, "point.md", "standard")
    voisin = _ecrire(chaine, "point-v2.md", "standard", reunion=doc["reunion"])
    _indexer()
    envoi = library.envoi_du_document(doc["nom"])
    assert llm.chat("s", "TEXTE", provider="openai", envoi=envoi) == "réponse [1]"
    library.set_sensitivity(voisin["nom"], ULTRA)
    with pytest.raises(sensitivity.EnvoiRefuse):
        llm.chat("s", "TEXTE", provider="openai", envoi=envoi)
    assert len(chaine["requetes"]) == 1


def test_t3_un_envoi_devenu_ultra_et_autorise_part_et_laisse_sa_trace(chaine, caplog) -> None:
    doc = _ecrire(chaine, "point.md", "standard")
    _indexer()
    reglage = chaine["client"].put("/api/compte/cloud-ultra", json={"autorise": True})
    assert reglage.status_code == 200 and reglage.json()["autorise"] is True
    envoi = library.envoi_du_document(doc["nom"])
    library.set_sensitivity(doc["nom"], ULTRA)
    with caplog.at_level("INFO", logger="memoglyph.envoi"):
        assert llm.chat("s", "TEXTE", provider="openai", envoi=envoi) == "réponse [1]"
    lignes = [r.getMessage() for r in caplog.records if r.name == "memoglyph.envoi"]
    assert len(lignes) == 1 and doc["id"] in lignes[0] and "fournisseur=openai" in lignes[0]


# ---------- T4 : un hôte MLX distant est un fournisseur cloud ----------

def test_t4_un_ultra_confidentiel_ne_part_pas_vers_un_mlx_distant(chaine, monkeypatch) -> None:
    doc = _ecrire(chaine, "rachat.md", ULTRA)
    _indexer()
    monkeypatch.setattr(config, "MLX_HOST", "10.20.30.40")
    reponse = _cr(chaine, doc["nom"], "mlx")
    assert reponse.status_code == 403 and reponse.json()["detail"] == sensitivity.REFUS_CLOUD
    assert chaine["requetes"] == []
    assert "mlx" not in chaine["client"].get("/api/config").json()["local_providers"]
    # Sur la boucle locale, inchangé.
    monkeypatch.setattr(config, "MLX_HOST", "127.0.0.1")
    assert "mlx" in chaine["client"].get("/api/config").json()["local_providers"]
    assert _cr(chaine, doc["nom"], "mlx").status_code == 200
