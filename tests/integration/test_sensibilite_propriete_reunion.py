"""La sensibilité est une propriété de la RÉUNION : une source de vérité, une action.

Le niveau d'une réunion vit dans le registre des réunions de la bibliothèque
(`library.registre_sensibilite_path`), un fichier à côté des `.md`. Le changer, depuis
n'importe lequel de ses documents ou depuis la réunion, vaut pour toutes ses versions,
transcripts et comptes rendus, sans réécrire aucun `.md`. Sans entrée dans le
registre, le niveau est le plus strict de ses documents : c'est aussi ce que la
migration (`library.migrer_sensibilites`) inscrit, à la demande.

Prouvé sur la vraie chaîne : `.md` sur disque, vrai index, vraies routes, vraie façade
`llm`, vrai lecteur MCP. Seuls les embeddings et les moteurs sont simulés ; un moteur
cloud note chaque requête, rien ne sort de la machine.
"""
from __future__ import annotations

import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from api import auth, cli
from api import main as api_main
from api import mcp
from api.jobs import JobManager
from core import docid, library, llm, projects, rag, semantic, sensitivity
from core.config import EMBED_DIM
from tests.conftest import COMPTE_DE_TEST, compte_de_test

pytestmark = pytest.mark.integration

ULTRA = sensitivity.ULTRA_CONFIDENTIEL
CONF = "confidential"
STD = "standard"


def _vecteur(texte: str) -> list[float]:
    v = [0.0] * EMBED_DIM
    v[0 if "rachat" in texte.lower() else 1] = 1.0
    return v


class FauxMoteur:
    """Un moteur LLM qui note chaque requête et répond à toutes les étapes du CR."""

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
        self.requetes.append((self.name, f"{system}\n{user}"))
        return "réponse [1]"

    def chat_json(self, system, user, **options):
        self.requetes.append((self.name, f"{system}\n{user}"))
        return {"units": [{"kind": "fact", "text": "Le rachat est validé.", "start": "00:00:01"}],
                "assignments": [], "sentences": [], "sections": [], "thesis": {}, "tensions": [],
                "faits": [], "conversation": {"type": "réunion", "summary": "s", "topics": []},
                "speakers": {}}


def _brancher_moteurs(monkeypatch) -> list:
    monkeypatch.setattr(semantic.embed, "embed_documents", lambda textes, **_k: [_vecteur(t) for t in textes])
    monkeypatch.setattr(semantic.embed, "embed_query", lambda t, **_k: _vecteur(t))
    requetes: list = []
    for nom in ("mistral", "openai", "anthropic", "mlx"):
        monkeypatch.setitem(llm.MOTEURS, nom, FauxMoteur(nom, requetes))
    return requetes


@pytest.fixture
def chaine(isolated_stores, tmp_path, monkeypatch):
    monkeypatch.setattr(api_main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(api_main, "jobs", JobManager())
    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(tmp_path / "usage.db"))
    requetes = _brancher_moteurs(monkeypatch)
    inbox = projects.create(isolated_stores["output_dir"], name=projects.INBOX_NAME, kind="inbox",
                            owner=COMPTE_DE_TEST)
    yield {"client": TestClient(api_main.app), "inbox": inbox, "requetes": requetes,
           "sortie": isolated_stores["output_dir"]}
    auth.regler_cloud_ultra(compte_de_test(), False)


def _ecrire(sortie, projet_id: str, nom: str, sensibilite: str, reunion: str | None = None, *,
            rapport_de: dict | None = None, decalage: float = 0.0) -> dict:
    identite, reunion = docid.nouveau(), reunion or docid.nouveau()
    lignes = [f"id: {identite}", f"meeting_id: {reunion}", f"project_id: {projet_id}",
              f"title: {nom}", "language: fr", f"sensitivity: {sensibilite}"]
    if rapport_de:
        lignes += ["type: report", f"source_transcript: {rapport_de['nom']}",
                   f"source_document_id: {rapport_de['id']}"]
    chemin = sortie / nom
    chemin.write_text("---\n" + "\n".join(lignes) + "\n---\n\n## Transcript\n\n"
                      "**Alice** [00:00:01] Le rachat se fera à RACHAT-SECRET millions.\n"
                      "**Bruno** [00:00:09] Le calendrier du rachat est tenu.\n", encoding="utf-8")
    if decalage:
        moment = time.time() + decalage
        os.utime(chemin, (moment, moment))
    return {"nom": nom, "id": identite, "reunion": reunion, "chemin": chemin}


def _doc(chaine, nom: str, sensibilite: str, reunion: str | None = None, **k) -> dict:
    return _ecrire(chaine["sortie"], chaine["inbox"].id, nom, sensibilite, reunion, **k)


def _indexer() -> None:
    library.reindex()
    semantic.reindex_all()


def _portee(chaine) -> dict:
    return {"view_scope": chaine["inbox"].id}


def _patch_document(chaine, nom: str, valeur: str):
    return chaine["client"].patch(f"/api/transcript/{nom}/sensitivity", params=_portee(chaine),
                                  json={"sensitivity": valeur})


def _patch_reunion(chaine, reunion: str, valeur: str):
    return chaine["client"].patch(f"/api/meetings/{reunion}/sensitivity", params=_portee(chaine),
                                  json={"sensitivity": valeur})


def _cr(chaine, nom: str, fournisseur: str):
    return chaine["client"].post(f"/api/transcript/{nom}/report", params=_portee(chaine),
                                 json={"llm_provider": fournisseur})


def _lecteur() -> mcp.Lecteur:
    return mcp.Lecteur(auth.Utilisateur(id=COMPTE_DE_TEST, identifiant=COMPTE_DE_TEST, admin=False))


def _surfaces(chaine, reunion: str) -> dict[str, str]:
    """Ce que dit chaque surface du niveau de la réunion : l'API (réunion, liste, chaque
    document), la règle d'envoi (relecture par la façade), le RAG et le MCP."""
    client, portee = chaine["client"], _portee(chaine)
    reunion_api = client.get(f"/api/meetings/{reunion}", params=portee).json()
    noms = [d["name"] for d in (*reunion_api["transcripts"], *reunion_api["reports"])]
    vues = {"api.reunion": reunion_api.get("sensitivity"),
            "api.liste_reunions": next(r.get("sensitivity") for r in
                                       client.get("/api/meetings", params=portee).json()
                                       if r["id"] == reunion)}
    for item in client.get("/api/library", params=portee).json():
        if item["name"] in noms:
            vues[f"api.bibliotheque:{item['name']}"] = item.get("sensitivity")
    for nom in noms:
        detail = client.get(f"/api/transcript/{nom}", params=portee).json()
        vues[f"api.document:{nom}"] = detail.get("sensitivity_effective")
        vues[f"regle:{nom}"] = library.sensibilite_du_document(detail["meta"]["id"])
        vues[f"envoi:{nom}"] = library.envoi_du_document(nom).sensibilite
    passages = [p for p in semantic.retrieve("rachat", k=20, project_id=chaine["inbox"].id)
                if p["meeting_id"] == reunion]
    for passage, envoi in zip(passages, rag._envois(passages)):
        vues[f"rag.retrieve:{passage['name']}"] = passage["sensitivity"]
        vues[f"rag.envoi:{passage['name']}"] = envoi.sensibilite
    mcp_reunions = _lecteur().lister_reunions({})
    entree = next((r for r in mcp_reunions["reunions"] if r["meeting_id"] == reunion), None)
    vues["mcp.lister_reunions"] = ULTRA if entree is None else entree.get("sensibilite")
    return vues


def _concordent(chaine, reunion: str, attendu: str) -> None:
    vues = _surfaces(chaine, reunion)
    ecarts = {surface: niveau for surface, niveau in vues.items() if niveau != attendu}
    assert not ecarts, f"attendu {attendu} partout, divergent : {ecarts}"
    assert any(s.startswith("rag.retrieve") for s in vues), "ancre : le RAG voit la réunion"


def _octets(chaine) -> dict:
    return {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in chaine["sortie"].glob("*.md")}


# ---------- Une transcription : montée et baisse ----------

def test_une_transcription_monte_puis_baisse(chaine) -> None:
    doc = _doc(chaine, "point.md", STD)
    _indexer()
    _concordent(chaine, doc["reunion"], STD)
    avant = _octets(chaine)

    monte = _patch_reunion(chaine, doc["reunion"], ULTRA)
    assert monte.status_code == 200, monte.text
    assert monte.json()["sensitivity"] == ULTRA and monte.json()["documents"] == ["point.md"]
    _concordent(chaine, doc["reunion"], ULTRA)
    assert _cr(chaine, "point.md", "openai").status_code == 403
    assert chaine["requetes"] == []

    baisse = _patch_document(chaine, "point.md", STD)
    assert baisse.status_code == 200 and baisse.json() == {"name": "point.md", "sensitivity": STD}
    _concordent(chaine, doc["reunion"], STD)
    assert _cr(chaine, "point.md", "openai").status_code == 200
    assert _octets(chaine)["point.md"] == avant["point.md"], "aucun .md réécrit"


# ---------- Plusieurs transcriptions de niveaux différents ----------

def test_plusieurs_transcriptions_de_niveaux_differents(chaine) -> None:
    """L'ancienne ultra-confidentielle, la plus récente standard : la réunion est
    ultra-confidentielle partout, et la baisser depuis le transcript retenu la baisse
    pour toutes ses versions."""
    v1 = _doc(chaine, "rachat-v1.md", ULTRA)
    _doc(chaine, "rachat-v2.md", STD, reunion=v1["reunion"], decalage=60)
    _doc(chaine, "rachat-v3.md", CONF, reunion=v1["reunion"], decalage=30)
    _indexer()
    reunion = chaine["client"].get(f"/api/meetings/{v1['reunion']}", params=_portee(chaine)).json()
    assert reunion["preferred_transcript"]["name"] == "rachat-v2.md", "ancre : le retenu est le standard"
    _concordent(chaine, v1["reunion"], ULTRA)

    # Ce que fait l'écran : le changement part du transcript retenu.
    baisse = _patch_document(chaine, "rachat-v2.md", CONF)
    assert baisse.status_code == 200 and baisse.json()["sensitivity"] == CONF
    _concordent(chaine, v1["reunion"], CONF)

    # Remontée depuis une autre version : la réunion entière suit.
    assert _patch_document(chaine, "rachat-v3.md", ULTRA).json()["sensitivity"] == ULTRA
    _concordent(chaine, v1["reunion"], ULTRA)
    assert _patch_reunion(chaine, v1["reunion"], STD).json()["sensitivity"] == STD
    _concordent(chaine, v1["reunion"], STD)


# ---------- Ultra-confidentiel, compte rendu, puis baisse ----------

def test_ultra_puis_compte_rendu_puis_baisse(chaine) -> None:
    doc = _doc(chaine, "rachat.md", ULTRA)
    _indexer()
    assert _cr(chaine, "rachat.md", "mlx").status_code == 200
    rapports = [d for d in library.list_transcripts() if d.get("type") == "report"]
    assert len(rapports) == 1, "ancre : le compte rendu local existe"
    assert library.get_transcript(rapports[0]["name"])["meta"]["sensitivity"] == ULTRA
    assert _cr(chaine, "rachat.md", "openai").status_code == 403

    baisse = _patch_document(chaine, "rachat.md", STD)

    # La réponse positive dit ce qui s'applique : plus aucune version ne retient la réunion.
    assert baisse.status_code == 200 and baisse.json()["sensitivity"] == STD
    for nom in ("rachat.md", rapports[0]["name"]):
        lu = chaine["client"].get(f"/api/transcript/{nom}", params=_portee(chaine)).json()
        assert lu["sensitivity_effective"] == STD, f"{nom} retient encore la réunion"
    chaine["requetes"].clear()
    assert _cr(chaine, "rachat.md", "openai").status_code == 200
    assert {nom for nom, _r in chaine["requetes"]} == {"openai"}
    assert _lecteur().lire_compte_rendu({"reunion": doc["reunion"]})["compte_rendu"]
    _concordent(chaine, doc["reunion"], STD)


# ---------- Changement pendant un traitement ----------

def test_monter_la_reunion_pendant_un_cr_cloud_arrete_l_appel_suivant(chaine) -> None:
    doc = _doc(chaine, "point.md", STD)
    _indexer()
    moteur = llm.MOTEURS["openai"]
    repondre = moteur.chat_json

    def puis_monter(system, user, **options):
        if not chaine["requetes"]:   # au premier appel, l'action de la réunion, pendant le travail
            assert _patch_reunion(chaine, doc["reunion"], ULTRA).status_code == 200
        return repondre(system, user, **options)

    moteur.chat_json = puis_monter
    reponse = _cr(chaine, "point.md", "openai")
    assert reponse.status_code == 403 and reponse.json()["detail"] == sensitivity.REFUS_CLOUD
    assert len(chaine["requetes"]) == 1, "un seul appel est parti avant le changement"


def test_monter_depuis_un_compte_rendu_ferme_aussi_le_transcript(chaine) -> None:
    """Le niveau appartient à la réunion : le monter depuis son CR ferme l'envoi du transcript."""
    doc = _doc(chaine, "point.md", STD)
    _doc(chaine, "point-cr.md", STD, reunion=doc["reunion"], rapport_de=doc)
    _indexer()
    envoi = library.envoi_du_document("point.md")
    assert llm.chat("s", "TEXTE", provider="openai", envoi=envoi) == "réponse [1]"
    assert _patch_document(chaine, "point-cr.md", ULTRA).json()["sensitivity"] == ULTRA
    with pytest.raises(sensitivity.EnvoiRefuse):
        llm.chat("s", "TEXTE", provider="openai", envoi=envoi)
    assert len(chaine["requetes"]) == 1


# ---------- Ordre et contenu des versions ----------

def test_l_ordre_et_le_contenu_des_versions_ne_changent_pas(chaine) -> None:
    t1 = _doc(chaine, "t1.md", STD, decalage=-400)
    t2 = _doc(chaine, "t2.md", STD, reunion=t1["reunion"], decalage=-300)
    _doc(chaine, "r1.md", STD, reunion=t1["reunion"], rapport_de=t1, decalage=-200)
    _doc(chaine, "r2.md", STD, reunion=t1["reunion"], rapport_de=t2, decalage=-100)
    _indexer()

    def versions() -> tuple:
        r = library.get_meeting(t1["reunion"])
        return ([d["name"] for d in r["transcripts"]], [d["name"] for d in r["reports"]],
                r["preferred_transcript"]["name"], r["modified"])

    ordre, contenu = versions(), _octets(chaine)
    assert ordre[:3] == (["t2.md", "t1.md"], ["r2.md", "r1.md"], "t2.md"), "ancre"
    for agir in (lambda: _patch_document(chaine, "t2.md", ULTRA),
                 lambda: _patch_document(chaine, "r1.md", CONF),
                 lambda: _patch_reunion(chaine, t1["reunion"], STD),
                 lambda: _patch_document(chaine, "t1.md", ULTRA)):
        assert agir().status_code == 200
        library.reindex()
        assert versions() == ordre, "un ancien document passe pour le plus récent"
        assert _octets(chaine) == contenu, "un .md a été réécrit"


# ---------- Autorisation cloud de l'ultra-confidentiel : distincte ----------

def test_l_autorisation_cloud_de_l_ultra_reste_distincte_du_niveau(chaine) -> None:
    doc = _doc(chaine, "rachat.md", STD)
    _indexer()
    assert _patch_reunion(chaine, doc["reunion"], ULTRA).status_code == 200
    assert _cr(chaine, "rachat.md", "openai").status_code == 403
    assert chaine["client"].put("/api/compte/cloud-ultra", json={"autorise": True}).status_code == 200
    assert _cr(chaine, "rachat.md", "openai").status_code == 200, "le propriétaire l'autorise"
    # Baisser puis remonter la réunion ne touche pas au réglage du compte.
    _patch_reunion(chaine, doc["reunion"], STD)
    _patch_reunion(chaine, doc["reunion"], ULTRA)
    assert chaine["client"].get("/api/compte/cloud-ultra").json()["autorise"] is True
    assert chaine["client"].put("/api/compte/cloud-ultra", json={"autorise": False}).status_code == 200
    assert _cr(chaine, "rachat.md", "openai").status_code == 403


def test_une_valeur_inconnue_est_refusee_par_la_route_de_la_reunion(chaine) -> None:
    doc = _doc(chaine, "rachat.md", ULTRA)
    _indexer()
    for valeur in ("ultra-confidential", "", None, 3):
        assert _patch_reunion(chaine, doc["reunion"], valeur).status_code == 422, valeur
    assert library.sensibilite_de_reunion(doc["reunion"]) == ULTRA, "rien n'a été rétrogradé"
    assert _patch_reunion(chaine, docid.nouveau(), STD).status_code == 404


# ---------- Invité en lecture seule ; restriction des partages ----------

MOT_DE_PASSE = "un mot de passe solide"


@pytest.fixture
def deux_comptes(isolated_stores, tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(auth, "CODE_PATH", tmp_path / ".memoglyph-installation")
    _brancher_moteurs(monkeypatch)
    for compte in ("alice", "bruno"):
        auth.creer_compte(compte, MOT_DE_PASSE)
    sortie = isolated_stores["output_dir"]
    projects.ensure_inbox(sortie)
    dossier, clients = projects.create_owned(sortie, name="Clients", owner="alice")
    v1 = _ecrire(dossier, clients.id, "rachat-v1.md", ULTRA)
    _ecrire(dossier, clients.id, "rachat-v2.md", STD, reunion=v1["reunion"], decalage=60)
    _indexer()

    def connecte(identifiant: str) -> TestClient:
        client = TestClient(api_main.app)
        assert client.post("/api/auth/connexion", json={
            "identifiant": identifiant, "mot_de_passe": MOT_DE_PASSE}).status_code == 200
        return client

    return {"alice": connecte("alice"), "bruno": connecte("bruno"), "clients": clients,
            "reunion": v1["reunion"], "sortie": sortie}


def _partager_reunion(client, reunion: str):
    return client.post("/api/partages", json={"type": "reunion", "cible": reunion, "invite": "bruno"})


def _lus(client) -> list[str]:
    return sorted(d["name"] for d in client.get("/api/library", params={"view_scope": "shared"}).json())


@pytest.mark.sans_session
def test_le_partage_suit_le_niveau_de_la_reunion_et_l_invite_ne_le_change_pas(deux_comptes) -> None:
    alice, bruno, reunion = deux_comptes["alice"], deux_comptes["bruno"], deux_comptes["reunion"]
    p = {"view_scope": deux_comptes["clients"].id}

    # Une version ultra-confidentielle retient la réunion : elle ne se partage pas.
    assert _partager_reunion(alice, reunion).status_code == 409
    # Baissée depuis le transcript retenu, la réunion entière se partage.
    assert alice.patch("/api/transcript/rachat-v2.md/sensitivity", params=p,
                       json={"sensitivity": STD}).json()["sensitivity"] == STD
    assert _partager_reunion(alice, reunion).status_code == 201
    assert _lus(bruno) == ["rachat-v1.md", "rachat-v2.md"]

    # L'invité lit, il ne change pas le niveau, par aucune des deux routes ni aucune portée.
    for portee in ("shared", deux_comptes["clients"].id, "all"):
        for reponse in (
            bruno.patch(f"/api/meetings/{reunion}/sensitivity", params={"view_scope": portee},
                        json={"sensitivity": ULTRA}),
            bruno.patch("/api/transcript/rachat-v1.md/sensitivity", params={"view_scope": portee},
                        json={"sensitivity": ULTRA}),
        ):
            assert reponse.status_code == 404, reponse.request.url
    assert library.sensibilite_de_reunion(reunion) == STD

    # Remontée par sa propriétaire : la réunion sort aussitôt de la vue de l'invité.
    assert alice.patch(f"/api/meetings/{reunion}/sensitivity", params=p,
                       json={"sensitivity": ULTRA}).status_code == 200
    assert _lus(bruno) == []
    assert bruno.get(f"/api/meetings/{reunion}", params={"view_scope": "shared"}).status_code == 404


# ---------- Migration : le plus strict, idempotente, aucun .md réécrit ----------

def _migration_corpus(chaine) -> dict:
    a = _doc(chaine, "a-t.md", STD)
    _doc(chaine, "a-t2.md", CONF, reunion=a["reunion"])
    _doc(chaine, "a-cr.md", ULTRA, reunion=a["reunion"], rapport_de=a)
    b = _doc(chaine, "b-t.md", STD)
    _doc(chaine, "b-t2.md", CONF, reunion=b["reunion"])
    c = _doc(chaine, "c-t.md", STD)
    d = _doc(chaine, "d-t.md", ULTRA)
    _indexer()
    return {"a": a["reunion"], "b": b["reunion"], "c": c["reunion"], "d": d["reunion"]}


def test_la_migration_retient_le_plus_strict_sans_reecrire_aucun_md(chaine) -> None:
    reunions = _migration_corpus(chaine)
    # Une réunion déjà réglée par sa propriétaire garde son niveau, même si un document dit plus.
    assert _patch_reunion(chaine, reunions["d"], STD).status_code == 200
    registre = library.registre_sensibilite_path()
    avant_registre = registre.read_bytes()
    avant = _octets(chaine)
    niveaux_avant = {k: library.sensibilite_de_reunion(r) for k, r in reunions.items()}
    assert niveaux_avant == {"a": ULTRA, "b": CONF, "c": STD, "d": STD}, "ancre : le repli dit déjà le plus strict"

    simulation = library.migrer_sensibilites(appliquer=False)
    projet = chaine["inbox"].id
    assert simulation["inscrites"] == {(projet, reunions["a"]): ULTRA, (projet, reunions["b"]): CONF,
                                       (projet, reunions["c"]): STD}
    assert registre.read_bytes() == avant_registre, "la simulation n'écrit rien"

    bilan = library.migrer_sensibilites(appliquer=True)
    assert bilan["inscrites"] == simulation["inscrites"] and bilan["deja_reglees"] == 1
    assert library.lire_registre_sensibilite() == {**simulation["inscrites"], (projet, reunions["d"]): STD}
    assert {k: library.sensibilite_de_reunion(r) for k, r in reunions.items()} == niveaux_avant
    assert _octets(chaine) == avant, "aucun .md réécrit"
    _concordent(chaine, reunions["b"], CONF)


def test_la_migration_est_idempotente(chaine) -> None:
    _migration_corpus(chaine)
    library.migrer_sensibilites(appliquer=True)
    registre = library.registre_sensibilite_path()
    octets, moment = registre.read_bytes(), registre.stat().st_mtime_ns
    second = library.migrer_sensibilites(appliquer=True)
    assert second["inscrites"] == {} and second["deja_reglees"] == 4
    assert (registre.read_bytes(), registre.stat().st_mtime_ns) == (octets, moment), "rien n'est réécrit"


class _FilSynchrone:
    """Le fil de la reprise sémantique du démarrage, exécuté sur place : rien ne survit au test."""

    def __init__(self, target, daemon=None) -> None:
        self._cible = target

    def start(self) -> None:
        self._cible()


def test_la_migration_ne_tourne_qu_a_la_demande(chaine, capsys, monkeypatch) -> None:
    reunions = _migration_corpus(chaine)
    registre = library.registre_sensibilite_path()
    # La racine d'essai déclare ses propres ports (libres, hors de la plage de l'instance
    # de l'utilisateur) : sans ce fichier, « l'application tourne » se lirait sur les ports
    # par défaut 8770 et 8771, c'est-à-dire sur une instance réelle étrangère à ce test.
    (library.OUTPUT_DIR.parent / ".memoglyph-ports").write_text("UI_PORT=9210\nAPI_PORT=9211\n", encoding="utf-8")
    # Le démarrage de l'application (réindexation comprise) et ses lectures ne migrent rien.
    monkeypatch.setattr(api_main, "ETAT_DEMARRAGE", dict(api_main.ETAT_DEMARRAGE))
    monkeypatch.setattr(api_main.threading, "Thread", _FilSynchrone)
    monkeypatch.setattr(api_main, "_startup_finops", lambda: None)
    with TestClient(api_main.app) as client:
        assert client.get("/api/meetings", params=_portee(chaine)).status_code == 200
    assert not registre.exists()

    assert cli.main(["sensibilite-reunions"]) == 0
    assert "Simulation" in capsys.readouterr().out and not registre.exists()
    assert cli.main(["sensibilite-reunions", "--appliquer"]) == 0
    sortie = capsys.readouterr().out
    assert "3 réunion(s) inscrite(s)" not in sortie and "4 réunion(s) inscrite(s)" in sortie
    assert library.lire_registre_sensibilite()[(chaine["inbox"].id, reunions["a"])] == ULTRA


# ---------- Un document arrivé après le réglage ne peut que relever la réunion ----------
#
# Le réglage couvre les documents présents quand il est fait ; un document arrivé ensuite
# (nouvelle transcription, compte rendu importé, document regroupé), marqué plus strict à
# sa création, relève la réunion au lieu d'être abaissé en silence.

def test_un_nouveau_transcript_ultra_releve_une_reunion_baissee_puis_une_baisse_explicite_vaut(chaine) -> None:
    t1 = _doc(chaine, "point.md", STD)
    _indexer()
    assert _patch_reunion(chaine, t1["reunion"], STD).json()["sensitivity"] == STD

    _doc(chaine, "point-v2.md", ULTRA, reunion=t1["reunion"], decalage=60)
    _indexer()

    _concordent(chaine, t1["reunion"], ULTRA)
    assert _cr(chaine, "point.md", "openai").status_code == 403 and chaine["requetes"] == []
    # Une nouvelle baisse explicite par sa propriétaire couvre le nouveau document : elle vaut.
    assert _patch_document(chaine, "point.md", STD).json()["sensitivity"] == STD
    _concordent(chaine, t1["reunion"], STD)
    assert _cr(chaine, "point.md", "openai").status_code == 200


def test_un_compte_rendu_ultra_cree_apres_la_baisse_releve_la_reunion(chaine) -> None:
    t1 = _doc(chaine, "rachat.md", ULTRA)
    _indexer()
    assert _patch_reunion(chaine, t1["reunion"], STD).json()["sensitivity"] == STD
    _concordent(chaine, t1["reunion"], STD)

    _doc(chaine, "rachat-cr.md", ULTRA, reunion=t1["reunion"], rapport_de=t1, decalage=60)
    _indexer()

    _concordent(chaine, t1["reunion"], ULTRA)
    assert _cr(chaine, "rachat.md", "openai").status_code == 403 and chaine["requetes"] == []


def test_un_document_ultra_regroupe_dans_une_reunion_baissee_la_releve(chaine) -> None:
    from core import backfill_identity

    t1 = _doc(chaine, "rachat.md", STD)
    _indexer()
    assert _patch_reunion(chaine, t1["reunion"], STD).status_code == 200
    # Une transcription du même média, d'avant les identités, marquée ultra-confidentielle.
    (chaine["sortie"] / "rachat-v2.md").write_text(
        f"---\nid: {docid.nouveau()}\nproject_id: {chaine['inbox'].id}\ntitle: rachat-v2.md\n"
        f"language: fr\nsensitivity: {ULTRA}\n---\n\n## Transcript\n\n"
        "**Alice** [00:00:01] Le rachat se fera à RACHAT-SECRET millions.\n", encoding="utf-8")

    backfill_identity.migrate(chaine["sortie"], simulation=False,
                              confirmed_groups=[["rachat.md", "rachat-v2.md"]])
    _indexer()

    assert library.get_transcript("rachat-v2.md")["meta"]["meeting_id"] == t1["reunion"], "ancre : regroupé"
    _concordent(chaine, t1["reunion"], ULTRA)
    assert _cr(chaine, "rachat.md", "openai").status_code == 403 and chaine["requetes"] == []


def test_baisser_une_reunion_qui_contient_un_ancien_transcript_ultra_vaut_toujours(chaine) -> None:
    v1 = _doc(chaine, "rachat-v1.md", ULTRA)
    _doc(chaine, "rachat-v2.md", STD, reunion=v1["reunion"], decalage=60)
    _indexer()
    _concordent(chaine, v1["reunion"], ULTRA)
    assert _patch_document(chaine, "rachat-v2.md", STD).json()["sensitivity"] == STD
    library.reindex()   # une réindexation ne fait pas des documents couverts des nouveaux venus
    _concordent(chaine, v1["reunion"], STD)
    assert _cr(chaine, "rachat-v2.md", "openai").status_code == 200


def test_un_registre_au_format_precedent_est_lu_et_la_migration_le_complete(chaine) -> None:
    """Une entrée sans liste de documents couverts (format précédent) vaut pour tous les
    documents présents ; la migration l'écrit sous la forme qui les nomme, une seule fois."""
    ancienne = _doc(chaine, "ancienne.md", ULTRA)
    neuve = _doc(chaine, "neuve.md", CONF)
    _indexer()
    registre = library.registre_sensibilite_path()
    registre.write_text('{"version": 1, "reunions": {"%s": "standard"}}\n' % ancienne["reunion"],
                        encoding="utf-8")
    projet = chaine["inbox"].id
    assert library.lire_registre_sensibilite() == {(projet, ancienne["reunion"]): STD}
    _concordent(chaine, ancienne["reunion"], STD)

    bilan = library.migrer_sensibilites(appliquer=True)
    assert bilan["inscrites"] == {(projet, neuve["reunion"]): CONF}
    assert bilan["completees"] == [(projet, ancienne["reunion"])]
    assert library.lire_registre_sensibilite() == {(projet, ancienne["reunion"]): STD,
                                                   (projet, neuve["reunion"]): CONF}
    _concordent(chaine, ancienne["reunion"], STD)
    octets = registre.read_bytes()
    second = library.migrer_sensibilites(appliquer=True)
    assert second["inscrites"] == {} and second["completees"] == [] and registre.read_bytes() == octets
    # Désormais, un document arrivé après le réglage relève la réunion.
    _doc(chaine, "ancienne-v2.md", ULTRA, reunion=ancienne["reunion"])
    _indexer()
    _concordent(chaine, ancienne["reunion"], ULTRA)


# ---------- Le réglage vit dans la portée du projet : deux réunions homonymes ne le partagent pas ----------
#
# Deux comptes (ou deux projets) peuvent porter des documents au même `meeting_id`. Le
# réglage est celui de la réunion DANS son projet : ni l'écriture ni la lecture d'un
# compte ne touche le document de l'autre.

@pytest.fixture
def homonymes(isolated_stores, tmp_path, monkeypatch):
    """Alice et Bruno possèdent chacun un projet, et un document au MÊME `meeting_id`."""
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(auth, "CODE_PATH", tmp_path / ".memoglyph-installation")
    _brancher_moteurs(monkeypatch)
    for compte in ("alice", "bruno"):
        auth.creer_compte(compte, MOT_DE_PASSE)
    sortie = isolated_stores["output_dir"]
    projects.ensure_inbox(sortie)
    d_a, projet_a = projects.create_owned(sortie, name="Chez Alice", owner="alice")
    d_b, projet_b = projects.create_owned(sortie, name="Chez Bruno", owner="bruno")
    reunion = docid.nouveau()

    def connecte(identifiant: str) -> TestClient:
        client = TestClient(api_main.app)
        assert client.post("/api/auth/connexion", json={
            "identifiant": identifiant, "mot_de_passe": MOT_DE_PASSE}).status_code == 200
        return client

    def ecrire(niveau_a: str, niveau_b: str) -> dict:
        a = _ecrire(d_a, projet_a.id, "a.md", niveau_a, reunion)
        b = _ecrire(d_b, projet_b.id, "b.md", niveau_b, reunion)
        _indexer()
        return {"a": a, "b": b}

    return {"alice": connecte("alice"), "bruno": connecte("bruno"), "reunion": reunion,
            "projet_a": projet_a, "projet_b": projet_b, "ecrire": ecrire, "sortie": sortie,
            "dossier_a": d_a}


def _niveau_de_b(h, doc_b: dict) -> str:
    """Ce que voit Bruno de son document, et ce qu'applique la règle d'envoi : le même niveau."""
    lu = h["bruno"].get("/api/transcript/b.md", params={"view_scope": h["projet_b"].id}).json()
    regle = library.sensibilite_du_document(doc_b["id"])
    envoi = library.envoi_du_document("b.md").sensibilite
    assert lu["sensitivity_effective"] == regle == envoi, (lu["sensitivity_effective"], regle, envoi)
    return regle


def _par_le_document(h, niveau: str):
    return h["alice"].patch("/api/transcript/a.md/sensitivity", params={"view_scope": h["projet_a"].id},
                            json={"sensitivity": niveau})


def _par_la_reunion(h, niveau: str):
    return h["alice"].patch(f"/api/meetings/{h['reunion']}/sensitivity",
                            params={"view_scope": h["projet_a"].id}, json={"sensitivity": niveau})


@pytest.mark.sans_session
@pytest.mark.parametrize("route", [_par_le_document, _par_la_reunion], ids=["document", "reunion"])
def test_baisser_sa_reunion_ne_baisse_pas_le_document_homonyme_d_un_autre_compte(homonymes, route) -> None:
    docs = homonymes["ecrire"](ULTRA, ULTRA)
    octets_b = docs["b"]["chemin"].read_bytes()

    reponse = route(homonymes, STD)

    assert reponse.status_code == 200, reponse.text
    assert reponse.json()["sensitivity"] == STD
    a = homonymes["alice"].get("/api/transcript/a.md", params={"view_scope": homonymes["projet_a"].id}).json()
    assert a["sensitivity_effective"] == STD, "la réunion d'Alice est baissée"
    assert _niveau_de_b(homonymes, docs["b"]) == ULTRA, "le document de Bruno est resté ultra-confidentiel"
    assert docs["b"]["chemin"].read_bytes() == octets_b


@pytest.mark.sans_session
@pytest.mark.parametrize("route", [_par_le_document, _par_la_reunion], ids=["document", "reunion"])
def test_monter_sa_reunion_ne_monte_pas_le_document_homonyme_d_un_autre_compte(homonymes, route) -> None:
    docs = homonymes["ecrire"](STD, STD)

    reponse = route(homonymes, ULTRA)

    assert reponse.status_code == 200 and reponse.json()["sensitivity"] == ULTRA, reponse.text
    assert library.sensibilite_du_document(docs["a"]["id"]) == ULTRA
    assert _niveau_de_b(homonymes, docs["b"]) == STD, "le réglage d'Alice ne vaut pas chez Bruno"


@pytest.mark.sans_session
def test_le_niveau_d_un_document_ne_depend_ni_des_documents_ni_du_reglage_d_un_autre_compte(homonymes) -> None:
    docs = homonymes["ecrire"](ULTRA, STD)
    # Sans réglage : le plus strict des documents de SA réunion, pas de l'homonyme.
    assert _niveau_de_b(homonymes, docs["b"]) == STD
    assert library.sensibilite_du_document(docs["a"]["id"]) == ULTRA
    # Bruno monte la sienne, Alice baisse la sienne : chacun ne lit que son réglage.
    assert homonymes["bruno"].patch("/api/transcript/b.md/sensitivity",
                                    params={"view_scope": homonymes["projet_b"].id},
                                    json={"sensitivity": CONF}).json()["sensitivity"] == CONF
    assert _par_le_document(homonymes, STD).json()["sensitivity"] == STD
    assert _niveau_de_b(homonymes, docs["b"]) == CONF
    assert library.sensibilite_du_document(docs["a"]["id"]) == STD
    # Le RAG et le MCP lisent la même portée : chaque passage, chaque réunion du compte.
    retrouves = semantic.retrieve("rachat", k=10)
    passages = {p["name"]: p["sensitivity"] for p in retrouves}
    assert passages == {"a.md": STD, "b.md": CONF}
    assert {p["name"]: e.sensibilite for p, e in zip(retrouves, rag._envois(retrouves))} == passages
    lecteur_b = mcp.Lecteur(auth.Utilisateur(id="bruno", identifiant="bruno", admin=False))
    assert [r["sensibilite"] for r in lecteur_b.lister_reunions({})["reunions"]] == [CONF]


@pytest.mark.sans_session
def test_un_invite_du_projet_partage_ne_change_pas_le_niveau(homonymes) -> None:
    docs = homonymes["ecrire"](ULTRA, STD)
    alice, bruno = homonymes["alice"], homonymes["bruno"]
    assert alice.post("/api/partages", json={"type": "projet", "cible": homonymes["projet_a"].id,
                                             "invite": "bruno"}).status_code == 201
    for portee in ("shared", homonymes["projet_a"].id, "all"):
        for reponse in (
            bruno.patch("/api/transcript/a.md/sensitivity", params={"view_scope": portee},
                        json={"sensitivity": STD}),
            bruno.patch(f"/api/meetings/{homonymes['reunion']}/sensitivity", params={"view_scope": portee},
                        json={"sensitivity": STD}),
        ):
            # Sa propre réunion homonyme peut répondre 200 par la route de la réunion
            # (portée « all ») : elle seule change, jamais celle d'Alice.
            assert reponse.status_code in (200, 404), reponse.request.url
    assert library.sensibilite_du_document(docs["a"]["id"]) == ULTRA, "la réunion d'Alice n'a pas bougé"


def test_deplacer_une_reunion_reglee_emporte_son_reglage(chaine) -> None:
    doc = _doc(chaine, "rachat.md", STD)
    _indexer()
    assert _patch_reunion(chaine, doc["reunion"], ULTRA).json()["sensitivity"] == ULTRA
    autre = chaine["client"].post("/api/projects", json={"name": "Archives"}).json()["id"]
    deplace = chaine["client"].patch(f"/api/meetings/{doc['reunion']}/project", params=_portee(chaine),
                                     json={"destination_project_id": autre})
    assert deplace.status_code == 200, deplace.text
    assert library.sensibilite_du_document(doc["id"]) == ULTRA, "relevée par sa propriétaire, elle le reste"


@pytest.mark.sans_session
def test_une_entree_par_reunion_seule_est_placee_dans_chaque_projet_qu_elle_couvre(homonymes) -> None:
    """Le format précédent `{niveau, documents}` rangé par `meeting_id` seul : lu dans chaque
    projet où vivent les documents qu'il couvre, et la migration en fait une entrée par
    projet, au niveau de l'ancienne, sans réécrire aucun `.md`."""
    docs = homonymes["ecrire"](ULTRA, ULTRA)
    reunion, a, b = homonymes["reunion"], homonymes["projet_a"].id, homonymes["projet_b"].id
    registre = library.registre_sensibilite_path()
    registre.write_text(json.dumps({"version": 1, "reunions": {reunion: {
        "niveau": STD, "documents": [docs["a"]["id"], docs["b"]["id"]]}}}), encoding="utf-8")
    octets = {nom: d["chemin"].read_bytes() for nom, d in docs.items()}
    assert library.lire_registre_sensibilite() == {(a, reunion): STD, (b, reunion): STD}
    niveaux = {nom: library.sensibilite_du_document(d["id"]) for nom, d in docs.items()}
    assert niveaux == {"a": STD, "b": STD}

    bilan = library.migrer_sensibilites(appliquer=True)

    assert bilan["inscrites"] == {} and bilan["completees"] == []
    ecrit = json.loads(registre.read_text(encoding="utf-8"))
    assert ecrit["version"] == 2 and "par_reunion" not in ecrit
    assert ecrit["reunions"] == {a: {reunion: {"niveau": STD, "documents": [docs["a"]["id"]]}},
                                 b: {reunion: {"niveau": STD, "documents": [docs["b"]["id"]]}}}
    assert {nom: library.sensibilite_du_document(d["id"]) for nom, d in docs.items()} == niveaux
    assert {nom: d["chemin"].read_bytes() for nom, d in docs.items()} == octets
    avant = registre.read_bytes()
    library.migrer_sensibilites(appliquer=True)
    assert registre.read_bytes() == avant, "idempotente"
    # Désormais chaque projet a son réglage : Alice remonte la sienne, Bruno n'en voit rien.
    assert _par_le_document(homonymes, ULTRA).json()["sensitivity"] == ULTRA
    assert _niveau_de_b(homonymes, docs["b"]) == STD



# ---------- Aucune route n'agit hors de la portée de l'appelant ; un homonyme ne bloque rien ----------
#
# Pour l'appelant, une réunion = ses documents dans UN projet de sa portée (propriétaire
# ou partage). Le document homonyme d'un autre compte n'en fait jamais partie : il n'est
# ni déplacé, ni archivé, ni lu, ni montré, et il n'empêche pas Alice d'agir sur la sienne.

def _empreinte(doc: dict) -> tuple:
    return (doc["chemin"].exists(), doc["chemin"].read_bytes() if doc["chemin"].exists() else None)


def _intact(h, doc_b: dict, avant: tuple, niveau: str) -> None:
    assert _empreinte(doc_b) == avant, "le document de Bruno a bougé"
    assert library.transcript_path("b.md") == doc_b["chemin"]
    assert _niveau_de_b(h, doc_b) == niveau


@pytest.mark.sans_session
@pytest.mark.parametrize("par_la_reunion", [False, True], ids=["document", "reunion"])
def test_alice_deplace_sa_reunion_sans_emporter_le_document_homonyme_de_bruno(homonymes, par_la_reunion) -> None:
    docs = homonymes["ecrire"](STD, ULTRA)
    avant = _empreinte(docs["b"])
    _d, archives = projects.create_owned(homonymes["sortie"], name="Archives", owner="alice")
    p = {"view_scope": homonymes["projet_a"].id}
    if par_la_reunion:
        reponse = homonymes["alice"].patch(f"/api/meetings/{homonymes['reunion']}/project", params=p,
                                           json={"destination_project_id": archives.id})
    else:
        reponse = homonymes["alice"].patch("/api/transcript/a.md/project", params=p,
                                           json={"destination_project_id": archives.id})
    assert reponse.status_code == 200, reponse.text
    assert library.transcript_path("a.md").parent.name == "Archives"
    _intact(homonymes, docs["b"], avant, ULTRA)


@pytest.mark.sans_session
def test_alice_ne_deplace_pas_le_document_de_bruno(homonymes) -> None:
    docs = homonymes["ecrire"](STD, ULTRA)
    avant = _empreinte(docs["b"])
    for portee in (homonymes["projet_a"].id, "all", "shared"):
        reponse = homonymes["alice"].patch("/api/transcript/b.md/project", params={"view_scope": portee},
                                           json={"destination_project_id": homonymes["projet_a"].id})
        assert reponse.status_code == 404, portee
    _intact(homonymes, docs["b"], avant, ULTRA)


@pytest.mark.sans_session
@pytest.mark.parametrize("definitif", [False, True], ids=["archivage", "suppression"])
def test_alice_archive_sa_reunion_sans_toucher_l_homonyme_de_bruno(homonymes, definitif) -> None:
    docs = homonymes["ecrire"](STD, ULTRA)
    avant = _empreinte(docs["b"])
    reponse = homonymes["alice"].delete(f"/api/meetings/{homonymes['reunion']}",
                                        params={"view_scope": homonymes["projet_a"].id,
                                                "hard": str(definitif).lower()})
    assert reponse.status_code == 200, reponse.text
    assert reponse.json()["documents"] == ["a.md"] and not docs["a"]["chemin"].exists()
    _intact(homonymes, docs["b"], avant, ULTRA)


@pytest.mark.sans_session
def test_alice_relance_les_faits_de_sa_reunion_malgre_l_homonyme(homonymes, monkeypatch) -> None:
    from core import config

    monkeypatch.setattr(config, "FAITS", True)
    monkeypatch.setattr(api_main, "jobs", JobManager())
    lances: list = []
    monkeypatch.setattr(api_main.jobs, "run", lambda job, target: lances.append(job))
    homonymes["ecrire"](STD, ULTRA)
    p = {"view_scope": homonymes["projet_a"].id}
    reponse = homonymes["alice"].post(f"/api/meetings/{homonymes['reunion']}/ecl", params=p,
                                      json={"provider": "openai"})
    assert reponse.status_code == 200, reponse.text
    assert len(lances) == 1, "le document ultra de Bruno n'entre pas dans la réunion d'Alice"
    lue = homonymes["alice"].get(f"/api/meetings/{homonymes['reunion']}/ecl", params=p)
    assert lue.status_code == 200 and lue.json()["faits"] == []


@pytest.mark.sans_session
def test_les_lectures_d_une_reunion_ne_montrent_rien_de_l_homonyme(homonymes, monkeypatch) -> None:
    from core import config

    monkeypatch.setattr(config, "FAITS", True)
    homonymes["ecrire"](STD, ULTRA)
    reunion, a, b = homonymes["reunion"], homonymes["projet_a"].id, homonymes["projet_b"].id
    alice, bruno = homonymes["alice"], homonymes["bruno"]
    # Bruno a des faits sur SA réunion homonyme (extraction locale simulée).
    from core import ecl
    monkeypatch.setattr(ecl, "_modele", lambda *_a, **_k: {"faits": [{
        "type": "decision", "horodatage": "00:00:01", "citation": "Le rachat se fera à RACHAT-SECRET millions",
        "enonce": "Le rachat est chiffré."}]})
    monkeypatch.setattr(ecl, "moteur_local", lambda: (None, None))
    ecl.extraire("b.md", provider="mlx")
    faits_b = bruno.get(f"/api/meetings/{reunion}/ecl", params={"view_scope": b}).json()["faits"]
    assert [f["document"] for f in faits_b] == ["b.md"], "ancre : Bruno a un fait"
    assert alice.get(f"/api/meetings/{reunion}/ecl", params={"view_scope": a}).json()["faits"] == []

    def noms(r: dict) -> list:
        return sorted(d["name"] for d in (*r["transcripts"], *r["reports"]))

    for portee in (a, "all"):
        lue = alice.get(f"/api/meetings/{reunion}", params={"view_scope": portee})
        assert lue.status_code == 200 and noms(lue.json()) == ["a.md"], (portee, lue.text)
    assert noms(bruno.get(f"/api/meetings/{reunion}", params={"view_scope": b}).json()) == ["b.md"]
    # La liste des réunions d'Alice, et la bibliothèque sans portée (administrateur) : deux
    # réunions, jamais mélangées ; le compte des réunions de chaque projet n'en voit qu'une.
    liste = alice.get("/api/meetings", params={"view_scope": "all"}).json()
    assert [noms(r) for r in liste if r["id"] == reunion] == [["a.md"]]
    assert sorted(noms(r) for r in library.list_meetings() if r["id"] == reunion) == [["a.md"], ["b.md"]]
    assert {p["id"]: p["count"] for p in library.projects() if p["id"] in (a, b)} == {a: 1, b: 1}
    with pytest.raises(library.ReunionAmbigue):
        library.get_meeting(reunion)
    assert noms(library.reunion_dans_la_portee(reunion, frozenset({a}))) == ["a.md"]
    # Partager SA réunion : l'homonyme de Bruno n'empêche rien et ne part pas avec elle.
    partage = alice.post("/api/partages", json={"type": "reunion", "cible": reunion, "invite": "bruno"})
    assert partage.status_code == 201, partage.text
    assert partage.json()["partage"]["nom"] == "a.md", "le nom décrit la réunion d'Alice"
    partagee = bruno.get(f"/api/meetings/{reunion}", params={"view_scope": "shared"})
    assert partagee.status_code == 200 and noms(partagee.json()) == ["a.md"]
    # Le MCP d'Alice : sa réunion, sans rien de Bruno.
    lecteur = mcp.Lecteur(auth.Utilisateur(id="alice", identifiant="alice", admin=False))
    assert [r["meeting_id"] for r in lecteur.lister_reunions({})["reunions"]] == [reunion]
    assert lecteur.lire_faits({"reunion": reunion})["faits"] == []
    assert [r["name"] for r in lecteur.rechercher({"requete": "rachat"})["resultats"]] == ["a.md"]


@pytest.mark.sans_session
def test_deux_projets_du_meme_compte_portant_la_reunion_rendent_un_409_explicite(homonymes) -> None:
    homonymes["ecrire"](STD, STD)
    d2, second = projects.create_owned(homonymes["sortie"], name="Second", owner="alice")
    _ecrire(d2, second.id, "a2.md", STD, homonymes["reunion"])
    _indexer()
    for reponse in (
        homonymes["alice"].get(f"/api/meetings/{homonymes['reunion']}", params={"view_scope": "all"}),
        homonymes["alice"].delete(f"/api/meetings/{homonymes['reunion']}", params={"view_scope": "all"}),
    ):
        assert reponse.status_code == 409, reponse.text
    assert library.transcript_path("a.md") is not None and library.transcript_path("a2.md") is not None
    # Désignée par son projet, chacune se lit.
    lue = homonymes["alice"].get(f"/api/meetings/{homonymes['reunion']}", params={"view_scope": second.id})
    assert lue.status_code == 200 and [d["name"] for d in lue.json()["transcripts"]] == ["a2.md"]


@pytest.mark.sans_session
def test_un_invite_ne_deplace_ni_n_archive_la_reunion_partagee(homonymes) -> None:
    docs = homonymes["ecrire"](STD, STD)
    alice, bruno = homonymes["alice"], homonymes["bruno"]
    assert alice.post("/api/partages", json={"type": "projet", "cible": homonymes["projet_a"].id,
                                             "invite": "bruno"}).status_code == 201
    avant = _empreinte(docs["a"])
    for portee in ("shared", homonymes["projet_a"].id):
        p = {"view_scope": portee}
        for reponse in (
            bruno.patch(f"/api/meetings/{homonymes['reunion']}/project", params=p,
                        json={"destination_project_id": homonymes["projet_b"].id}),
            bruno.patch("/api/transcript/a.md/project", params=p,
                        json={"destination_project_id": homonymes["projet_b"].id}),
            bruno.delete(f"/api/meetings/{homonymes['reunion']}", params={**p, "hard": "true"}),
        ):
            assert reponse.status_code == 404, reponse.request.url
    assert _empreinte(docs["a"]) == avant


@pytest.mark.sans_session
def test_le_compte_rendu_mcp_d_alice_n_est_jamais_celui_de_l_homonyme(homonymes) -> None:
    docs = homonymes["ecrire"](STD, STD)
    _ecrire(homonymes["dossier_a"], homonymes["projet_a"].id, "a-cr.md", STD, homonymes["reunion"],
            rapport_de=docs["a"], decalage=-60)
    dossier_b = docs["b"]["chemin"].parent
    _ecrire(dossier_b, homonymes["projet_b"].id, "b-cr.md", STD, homonymes["reunion"],
            rapport_de=docs["b"], decalage=60)   # plus récent : il passerait en tête d'un mélange
    _indexer()
    lecteur = mcp.Lecteur(auth.Utilisateur(id="alice", identifiant="alice", admin=False))
    assert lecteur.lire_compte_rendu({"reunion": homonymes["reunion"]})["document"] == "a-cr.md"
    lecteur_b = mcp.Lecteur(auth.Utilisateur(id="bruno", identifiant="bruno", admin=False))
    assert lecteur_b.lire_compte_rendu({"reunion": homonymes["reunion"]})["document"] == "b-cr.md"


@pytest.mark.sans_session
def test_une_cle_libre_designe_la_reunion_de_l_appelant_jamais_celle_d_un_homonyme(homonymes) -> None:
    """Une clé qui n'est pas un ULID (ici un titre commun aux deux comptes) : chacun lit la
    sienne, de façon déterministe, et le document de l'autre ne le bloque pas."""
    sortie = homonymes["sortie"]
    for compte, dossier, projet, nom in (("alice", homonymes["dossier_a"], homonymes["projet_a"], "ra.md"),
                                         ("bruno", None, homonymes["projet_b"], "rb.md")):
        dossier = dossier or (sortie / "Chez Bruno")
        (dossier / nom).write_text(
            f"---\nid: {docid.nouveau()}\nmeeting_id: {docid.nouveau()}\nproject_id: {projet.id}\n"
            "title: Rachat commun\nlanguage: fr\nsensitivity: standard\n---\n\nTexte\n", encoding="utf-8")
    _indexer()
    for compte, attendu in (("alice", "ra.md"), ("bruno", "rb.md")):
        lue = homonymes[compte].get("/api/meetings/Rachat commun", params={"view_scope": "all"})
        assert lue.status_code == 200, (compte, lue.text)
        assert [d["name"] for d in lue.json()["transcripts"]] == [attendu]


@pytest.mark.sans_session
def test_les_faits_rendus_sont_ceux_des_seuls_documents_de_la_reunion(homonymes, monkeypatch) -> None:
    """Défense en profondeur : même si la lecture des faits (`ecl.faits`) rendait aussi le fait
    d'un document hors de la réunion (ici celui de l'homonyme de Bruno, forcé dans la
    réponse), ni l'API ni le MCP ne le rendent à Alice."""
    from core import config, ecl

    monkeypatch.setattr(config, "FAITS", True)
    homonymes["ecrire"](STD, STD)
    reunion, a = homonymes["reunion"], homonymes["projet_a"].id
    monkeypatch.setattr(ecl, "_modele", lambda *_a, **_k: {"faits": [{
        "type": "decision", "horodatage": "00:00:01", "citation": "Le rachat se fera à RACHAT-SECRET millions",
        "enonce": "Le rachat est chiffré."}]})
    monkeypatch.setattr(ecl, "moteur_local", lambda: (None, None))
    for nom in ("a.md", "b.md"):
        ecl.extraire(nom, provider="mlx")
    lire = ecl.faits
    tous = lire(None, meeting_id=reunion)
    assert sorted(f["document"] for f in tous) == ["a.md", "b.md"], "ancre : un fait par document"
    monkeypatch.setattr(ecl, "faits", lambda portee, **k: lire(None, **k))   # la garde amont contournée

    par_l_api = homonymes["alice"].get(f"/api/meetings/{reunion}/ecl", params={"view_scope": a}).json()
    assert [f["document"] for f in par_l_api["faits"]] == ["a.md"]
    lecteur = mcp.Lecteur(auth.Utilisateur(id="alice", identifiant="alice", admin=False))
    par_le_mcp = lecteur.lire_faits({"reunion": reunion})["faits"]
    assert [f["citation"] for f in par_le_mcp] == [tous[0]["citation"]] and len(par_le_mcp) == 1
