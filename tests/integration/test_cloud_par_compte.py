"""Le cloud pour l'ultra-confidentiel, autorisé compte par compte : prouvé sur la vraie chaîne.

Par défaut rien ne change : un document `ultra_confidential` ne part vers aucun
fournisseur cloud (`test_ultra_confidentiel.py` le prouve toujours). Un compte peut
l'autoriser pour SES documents (`PUT /api/compte/cloud-ultra`). Pour chaque route
cloud, ici :

- réglage inactif : refus, sans aucun appel au fournisseur ;
- réglage actif : l'envoi a lieu et la trace est écrite (exécution FinOps, ligne de
  journal sans contenu, provenance du document produit) ;
- un second compte sans réglage reste refusé ;
- le local est inchangé.

Rien n'est fabriqué entre le disque et le fournisseur : `.md` écrits sur disque, vrai
index, vraies routes, vrais travaux de fond, vraie façade `llm` et sa règle d'envoi.
Seuls les embeddings, le décodage audio et les moteurs (qui notent chaque requête)
sont simulés. Le réglage est celui du PROPRIÉTAIRE du projet, jamais celui de qui lit.
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from api import auth
from api import main as api_main
from api import mcp
from api.jobs import JobManager
from core import docid, ecl, library, llm, pipeline, projects, semantic, sensitivity
from core.config import EMBED_DIM
from core.stt_base import Segment, Transcript

pytestmark = [pytest.mark.integration, pytest.mark.sans_session]


@pytest.fixture(autouse=True)
def _faits_actives(monkeypatch):
    """Ces tests supposent les faits de la réunion activés (`MEMOGLYPH_FAITS=1`)."""
    from core import config as _config

    monkeypatch.setattr(_config, "FAITS", True)

MOT_DE_PASSE = "un mot de passe solide"
CLOUD = ("mistral", "openai", "anthropic")
SECRET = "RACHAT-SECRET"
# Le vrai `ecl._modele`, relevé à l'import : `tests/conftest.py` le remplace pour chaque test.
_MODELE_ECL = ecl._modele


def _vecteur(texte: str) -> list[float]:
    v = [0.0] * EMBED_DIM
    v[0 if "rachat" in texte.lower() else 1] = 1.0
    return v


class FauxMoteur:
    """Un moteur LLM cloud qui note la requête entière et répond à toutes les étapes."""

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


class FauxSTT:
    """Un moteur de transcription qui note chaque fichier audio reçu."""

    def __init__(self, nom: str, envois: list) -> None:
        self.name = nom
        self.model_name = f"faux-{nom}"
        self.envois = envois

    def transcribe(self, wav_path, progress=None, **_k) -> Transcript:
        self.envois.append(self.name)
        return Transcript(segments=[Segment(1.0, 2.0, "Le rachat est validé.")],
                          full_text="Le rachat est validé.", language="fr")


@pytest.fixture
def chaine(isolated_stores, tmp_path, monkeypatch):
    sortie = isolated_stores["output_dir"]
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(auth, "CODE_PATH", tmp_path / ".memoglyph-installation")
    monkeypatch.setattr(api_main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(api_main, "jobs", JobManager())
    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(tmp_path / "usage.db"))
    monkeypatch.setattr(semantic.embed, "embed_documents", lambda textes, **_k: [_vecteur(t) for t in textes])
    monkeypatch.setattr(semantic.embed, "embed_query", lambda t, **_k: _vecteur(t))
    requetes: list = []
    for nom in CLOUD:
        monkeypatch.setitem(llm.MOTEURS, nom, FauxMoteur(nom, requetes))
    # Transcription : décodage audio et moteurs simulés, le reste du pipeline réel.
    audio: list = []
    moteurs_stt = {"mistral": FauxSTT("mistral", audio), "whisper": FauxSTT("whisper", audio)}
    monkeypatch.setattr(pipeline, "get_engine", lambda nom, **_k: moteurs_stt[nom])
    monkeypatch.setattr(pipeline.config, "load_env", lambda: None)
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"wav")
    for fonction, valeur in (("validate_input", None), ("probe_duration", 2.0), ("to_wav16k", wav),
                             ("pcm_sha256", "f" * 64), ("archive_audio", None),
                             ("archive_faithful", None), ("keep_original", None)):
        monkeypatch.setattr(pipeline.media, fonction, lambda *_a, _v=valeur, **_k: _v)
    for compte in ("alice", "bruno"):
        auth.creer_compte(compte, MOT_DE_PASSE)
    projects.ensure_inbox(sortie)
    dossier_a, projet_a = projects.create_owned(sortie, name="Clients", owner="alice")
    dossier_b, projet_b = projects.create_owned(sortie, name="Atelier", owner="bruno")
    return {"sortie": sortie, "requetes": requetes, "audio": audio, "moteurs_stt": moteurs_stt,
            "usage": tmp_path / "usage.db",
            "alice": {"dossier": dossier_a, "projet": projet_a, "client": _connecte("alice")},
            "bruno": {"dossier": dossier_b, "projet": projet_b, "client": _connecte("bruno")}}


def _connecte(identifiant: str) -> TestClient:
    client = TestClient(api_main.app)
    assert client.post("/api/auth/connexion", json={
        "identifiant": identifiant, "mot_de_passe": MOT_DE_PASSE}).status_code == 200
    return client


def _autoriser(chaine, qui: str, valeur: bool = True) -> None:
    reponse = chaine[qui]["client"].put("/api/compte/cloud-ultra", json={"autorise": valeur})
    assert reponse.status_code == 200 and reponse.json()["autorise"] is valeur


def _ecrire(chaine, qui: str, nom: str, sensibilite: str = "ultra_confidential",
            texte: str = f"Le rachat se fera à {SECRET} millions.", reunion: str | None = None) -> dict:
    projet = chaine[qui]["projet"]
    identite, reunion = docid.nouveau(), reunion or docid.nouveau()
    (chaine[qui]["dossier"] / nom).write_text(
        f"---\nid: {identite}\nmeeting_id: {reunion}\nproject_id: {projet.id}\ntitle: {nom}\n"
        f"language: fr\nsensitivity: {sensibilite}\n---\n\n## Transcript\n\n"
        f"**Alice** [00:00:01] {texte}\n", encoding="utf-8")
    return {"nom": nom, "id": identite, "reunion": reunion}


def _indexer() -> None:
    library.reindex()
    semantic.reindex_all()


def _attendre(client: TestClient, job_id: str) -> dict:
    for _ in range(2000):
        etat = client.get(f"/api/jobs/{job_id}").json()
        if etat["status"] in {"done", "error"}:
            return etat
        time.sleep(0.01)
    raise AssertionError("travail sans fin")


def _executions(chaine) -> list[tuple[str, int]]:
    if not chaine["usage"].exists():
        return []
    with closing(sqlite3.connect(chaine["usage"])) as conn:
        return [tuple(r) for r in conn.execute(
            "SELECT kind, envoi_ultra_autorise FROM execution ORDER BY started_at")]


def _lignes(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "memoglyph.envoi"]


def _envoye(chaine, *morceaux: str) -> bool:
    return any(all(m in requete for m in morceaux) for _nom, requete in chaine["requetes"])


# ---------- Le réglage du compte ----------

def test_le_reglage_est_inactif_par_defaut_et_ne_vaut_que_pour_son_compte(chaine) -> None:
    alice, bruno = chaine["alice"]["client"], chaine["bruno"]["client"]
    assert alice.get("/api/compte/cloud-ultra").json() == {"autorise": False, "modifie_le": None}

    _autoriser(chaine, "alice")

    lu = alice.get("/api/compte/cloud-ultra").json()
    assert lu["autorise"] is True and lu["modifie_le"]
    assert bruno.get("/api/compte/cloud-ultra").json()["autorise"] is False
    assert auth.cloud_ultra_autorise("alice") and not auth.cloud_ultra_autorise("bruno")
    _autoriser(chaine, "alice", False)
    assert not auth.cloud_ultra_autorise("alice")
    assert alice.get("/api/compte/cloud-ultra").json()["modifie_le"] >= lu["modifie_le"]


def test_ni_l_administrateur_ni_un_jeton_d_appareil_ne_reglent_un_autre_compte(chaine) -> None:
    admin = auth.creer_compte("admin-cloud", MOT_DE_PASSE, admin=True)
    client = _connecte("admin-cloud")
    # Aucun champ ne désigne un autre compte : un `identifiant` est refusé, pas ignoré.
    refus = client.put("/api/compte/cloud-ultra", json={"autorise": True, "identifiant": "bruno"})
    assert refus.status_code == 422
    assert not auth.cloud_ultra_autorise("bruno") and not auth.cloud_ultra_autorise("admin-cloud")
    # Le jeton d'appareil de Bruno n'ouvre ni la lecture ni le changement.
    _, secret = auth.creer_jeton(auth.verifier_identifiants("bruno", MOT_DE_PASSE), "Mac")
    compagnon = TestClient(api_main.app, headers={"Authorization": f"Bearer {secret}"})
    assert compagnon.get("/api/compte/cloud-ultra").status_code == 401
    assert compagnon.put("/api/compte/cloud-ultra", json={"autorise": True}).status_code == 401
    assert not auth.cloud_ultra_autorise("bruno")
    # Sans session : 401. Et les routes restent hors des listes ouvertes.
    assert TestClient(api_main.app).get("/api/compte/cloud-ultra").status_code == 401
    for methode in ("GET", "PUT"):
        cle = (methode, "/api/compte/cloud-ultra")
        assert cle not in api_main.ROUTES_PUBLIQUES and cle not in api_main.ROUTES_DU_COMPAGNON
    # Le PUT valide de l'administrateur ne règle que le sien.
    assert client.put("/api/compte/cloud-ultra", json={"autorise": True}).status_code == 200
    assert auth.cloud_ultra(admin)["autorise"] and not auth.cloud_ultra_autorise("bruno")


def test_un_magasin_d_avant_le_reglage_est_migre_inactif(tmp_path, monkeypatch) -> None:
    ancien = tmp_path / "ancien-auth.db"
    with closing(sqlite3.connect(ancien)) as conn, conn:
        conn.execute("CREATE TABLE utilisateurs (id TEXT PRIMARY KEY, identifiant TEXT NOT NULL UNIQUE "
                     "COLLATE NOCASE, hachage TEXT NOT NULL, admin INTEGER NOT NULL DEFAULT 0, "
                     "cree_le TEXT NOT NULL)")
        conn.execute("INSERT INTO utilisateurs VALUES ('u1', 'ancien', 'h', 0, '2026-01-01')")
    monkeypatch.setattr(auth, "DB_PATH", ancien)

    assert auth.cloud_ultra_autorise("ancien") is False
    with closing(sqlite3.connect(ancien)) as conn:
        colonnes = {r[1] for r in conn.execute("PRAGMA table_info(utilisateurs)")}
        valeur = conn.execute("SELECT cloud_ultra_autorise FROM utilisateurs").fetchone()[0]
    assert {"actif", "cloud_ultra_autorise", "cloud_ultra_modifie_le"} <= colonnes
    assert valeur == 0


def test_un_compte_desactive_n_autorise_plus_rien(chaine) -> None:
    _autoriser(chaine, "alice")
    auth.creer_compte("admin-cloud", MOT_DE_PASSE, admin=True)
    auth.desactiver_compte("alice")
    assert not sensitivity.autorise("alice")


# ---------- Transcription : audio, puis profil ----------

def _transcrire(chaine, qui: str, **champs):
    donnees = {"sensitivity_level": "ultra_confidential", "destination_project_id": chaine[qui]["projet"].id,
               "report_on": "false", **champs}
    return chaine[qui]["client"].post("/api/transcribe", files={"file": ("rachat.wav", b"audio")},
                                      data=donnees)


def test_transcrire_en_cloud_refuse_sans_le_reglage_puis_part_avec_et_laisse_sa_trace(chaine, caplog) -> None:
    refus = _transcrire(chaine, "alice", engine="mistral")
    assert refus.status_code == 403 and refus.json()["detail"] == sensitivity.REFUS_CLOUD
    assert chaine["audio"] == []

    _autoriser(chaine, "alice")
    with caplog.at_level("INFO", logger="memoglyph.envoi"):
        reponse = _transcrire(chaine, "alice", engine="mistral")
        assert reponse.status_code == 200, reponse.text
        etat = _attendre(chaine["alice"]["client"], reponse.json()["job_id"])

    assert etat["status"] == "done", etat
    assert chaine["audio"] == ["mistral"]
    meta = (library.get_transcript(etat["result"]["name"]) or {})["meta"]
    assert meta["sensitivity"] == "ultra_confidential" and meta["envoi_ultra_autorise"] is True
    assert ("stt", 1) in _executions(chaine)
    [ligne] = _lignes(caplog)
    assert "compte=alice" in ligne and "fournisseur=mistral" in ligne and meta["id"] in ligne
    assert "rachat" not in ligne.lower()


def test_transcrire_l_ultra_d_un_autre_compte_reste_refuse(chaine) -> None:
    _autoriser(chaine, "alice")
    refus = _transcrire(chaine, "bruno", engine="mistral")
    assert refus.status_code == 403
    # Alice ne peut pas non plus déposer chez Bruno : la destination est hors de sa portée.
    assert _transcrire(chaine, "alice", engine="mistral",
                       destination_project_id=chaine["bruno"]["projet"].id).status_code == 404
    assert chaine["audio"] == []


def test_desactiver_vaut_aussitot_pour_une_transcription_deja_lancee(chaine) -> None:
    """Le réglage est relu juste avant l'envoi de l'audio, pas figé au lancement."""
    _autoriser(chaine, "alice")
    reel = pipeline.get_engine

    def get_engine(nom, **k):
        auth.regler_cloud_ultra(auth.verifier_identifiants("alice", MOT_DE_PASSE), False)
        return reel(nom, **k)

    pipeline.get_engine = get_engine
    try:
        reponse = _transcrire(chaine, "alice", engine="mistral")
        etat = _attendre(chaine["alice"]["client"], reponse.json()["job_id"])
    finally:
        pipeline.get_engine = reel
    assert etat["status"] == "error" and sensitivity.REFUS_CLOUD in etat["error"]
    assert chaine["audio"] == []


def test_le_profil_en_cloud_suit_le_reglage_et_le_local_ne_change_pas(chaine) -> None:
    refus = _transcrire(chaine, "alice", engine="whisper", llm_provider="openai", profile="true")
    assert refus.status_code == 403 and chaine["requetes"] == []

    local = _transcrire(chaine, "alice", engine="whisper", llm_provider="mlx", profile="false")
    etat_local = _attendre(chaine["alice"]["client"], local.json()["job_id"])
    assert etat_local["status"] == "done" and chaine["audio"] == ["whisper"]
    assert "envoi_ultra_autorise" not in library.get_transcript(etat_local["result"]["name"])["meta"]

    _autoriser(chaine, "alice")
    reponse = _transcrire(chaine, "alice", engine="whisper", llm_provider="openai", profile="true")
    etat = _attendre(chaine["alice"]["client"], reponse.json()["job_id"])
    assert etat["status"] == "done", etat
    assert _envoye(chaine, "Le rachat est validé.")
    meta = library.get_transcript(etat["result"]["name"])["meta"]
    assert meta["profiled"] is True and meta["envoi_ultra_autorise"] is True
    assert _transcrire(chaine, "bruno", engine="whisper", llm_provider="openai",
                       profile="true").status_code == 403


# ---------- Compte rendu, puis compte rendu relancé ----------

def _cr(chaine, qui: str, nom: str, fournisseur: str):
    return chaine[qui]["client"].post(f"/api/transcript/{nom}/report",
                                      params={"view_scope": chaine[qui]["projet"].id},
                                      json={"llm_provider": fournisseur})


@pytest.mark.parametrize("fournisseur", CLOUD)
def test_le_cr_en_cloud_suit_le_reglage_du_proprietaire(chaine, caplog, fournisseur) -> None:
    doc = _ecrire(chaine, "alice", "rachat.md")
    _ecrire(chaine, "bruno", "moule.md")
    _indexer()
    refus = _cr(chaine, "alice", "rachat.md", fournisseur)
    assert refus.status_code == 403 and refus.json()["detail"] == sensitivity.REFUS_CLOUD
    assert chaine["requetes"] == []

    _autoriser(chaine, "alice")
    with caplog.at_level("INFO", logger="memoglyph.envoi"):
        reponse = _cr(chaine, "alice", "rachat.md", fournisseur)
        # Relancé : une seconde version, même règle, même trace.
        relance = _cr(chaine, "alice", "rachat.md", fournisseur)

    assert reponse.status_code == 200, reponse.text
    assert relance.status_code == 200, relance.text
    assert _envoye(chaine, SECRET) and {n for n, _r in chaine["requetes"]} == {fournisseur}
    for cr in (reponse.json(), relance.json()):
        meta = library.get_transcript(cr["name"])["meta"]
        assert meta["envoi_ultra_autorise"] is True and meta["sensitivity"] == "ultra_confidential"
    assert _executions(chaine).count(("report", 1)) == 2
    lignes = _lignes(caplog)
    assert len(lignes) == 2 and all(doc["id"] in ligne and "compte=alice" in ligne for ligne in lignes)
    assert not any(SECRET in ligne or "rachat.md" in ligne for ligne in lignes)
    # Bruno, sans réglage, reste refusé pour son propre ultra-confidentiel.
    assert _cr(chaine, "bruno", "moule.md", fournisseur).status_code == 403


def test_le_cr_local_ne_change_pas_et_ne_laisse_aucune_trace_d_envoi(chaine, monkeypatch) -> None:
    _ecrire(chaine, "alice", "rachat.md")
    _indexer()
    monkeypatch.setitem(llm.MOTEURS, "mlx", FauxMoteur("mlx", chaine["requetes"]))
    reponse = _cr(chaine, "alice", "rachat.md", "mlx")
    assert reponse.status_code == 200, reponse.text
    assert "envoi_ultra_autorise" not in library.get_transcript(reponse.json()["name"])["meta"]
    assert ("report", 0) in _executions(chaine)


def test_desactiver_pendant_un_cr_arrete_l_appel_suivant(chaine) -> None:
    _ecrire(chaine, "alice", "rachat.md")
    _indexer()
    _autoriser(chaine, "alice")
    moteur = llm.MOTEURS["openai"]
    repondre = moteur.chat_json

    def puis_desactiver(system, user, **options):
        auth.regler_cloud_ultra(auth.verifier_identifiants("alice", MOT_DE_PASSE), False)
        return repondre(system, user, **options)

    moteur.chat_json = puis_desactiver
    reponse = _cr(chaine, "alice", "rachat.md", "openai")
    assert reponse.status_code == 403 and reponse.json()["detail"] == sensitivity.REFUS_CLOUD
    assert len(chaine["requetes"]) == 1, "un seul appel est parti avant la désactivation"
    assert not [d for d in library.list_transcripts() if d.get("type") == "report"]


# ---------- Faits (relance de l'ECL) ----------

def _faits(chaine, qui: str, reunion: str, fournisseur: str):
    return chaine[qui]["client"].post(f"/api/meetings/{reunion}/ecl",
                                      params={"view_scope": chaine[qui]["projet"].id},
                                      json={"provider": fournisseur})


@pytest.mark.parametrize("fournisseur", CLOUD)
def test_relancer_les_faits_en_cloud_suit_le_reglage(chaine, monkeypatch, fournisseur) -> None:
    monkeypatch.setattr(ecl, "_modele", _MODELE_ECL)
    doc = _ecrire(chaine, "alice", "rachat.md")
    autre = _ecrire(chaine, "bruno", "moule.md")
    _indexer()
    refus = _faits(chaine, "alice", doc["reunion"], fournisseur)
    assert refus.status_code == 403 and chaine["requetes"] == []

    _autoriser(chaine, "alice")
    reponse = _faits(chaine, "alice", doc["reunion"], fournisseur)
    etat = _attendre(chaine["alice"]["client"], reponse.json()["job_id"])

    assert etat["status"] == "done", etat
    assert _envoye(chaine, SECRET)
    assert ecl.lire_fichier(doc["id"])["envoi_ultra_autorise"] is True
    assert ("ecl", 1) in _executions(chaine)
    # Un document ordinaire : rien d'ultra n'est parti, la provenance ne le prétend pas.
    ordinaire = _ecrire(chaine, "alice", "point.md", "standard", "Le point est fait.")
    _indexer()
    etat = _attendre(chaine["alice"]["client"],
                     _faits(chaine, "alice", ordinaire["reunion"], fournisseur).json()["job_id"])
    assert etat["status"] == "done" and "envoi_ultra_autorise" not in ecl.lire_fichier(ordinaire["id"])
    assert ("ecl", 0) in _executions(chaine)
    assert _faits(chaine, "bruno", autre["reunion"], fournisseur).status_code == 403


def test_relancer_les_faits_en_local_ne_change_pas(chaine, monkeypatch) -> None:
    lances: list = []
    monkeypatch.setattr(api_main.jobs, "run", lambda job, target: lances.append(job))
    doc = _ecrire(chaine, "alice", "rachat.md")
    _indexer()
    assert _faits(chaine, "alice", doc["reunion"], "mlx").status_code == 200 and len(lances) == 1


# ---------- Assistant ----------

def _demander(chaine, qui: str, fournisseur: str, scope: dict | None = None):
    return chaine[qui]["client"].post("/api/ask", json={
        "question": "Où en est le rachat ?", "llm_provider": fournisseur,
        "scope": scope or {"mode": "project", "project_id": chaine[qui]["projet"].id}})


@pytest.mark.parametrize("fournisseur", CLOUD)
def test_l_assistant_en_cloud_envoie_l_ultra_du_proprietaire_qui_l_autorise(chaine, caplog,
                                                                         fournisseur) -> None:
    doc = _ecrire(chaine, "alice", "rachat.md")
    _ecrire(chaine, "alice", "point.md", "standard", "Le rachat du matériel est reporté.")
    _indexer()
    avant = _demander(chaine, "alice", fournisseur).json()
    assert avant["passages_ecartes"] == 1 and not _envoye(chaine, SECRET)

    _autoriser(chaine, "alice")
    with caplog.at_level("INFO", logger="memoglyph.envoi"):
        corps = _demander(chaine, "alice", fournisseur, {"mode": "all"}).json()

    assert "passages_ecartes" not in corps
    assert sorted(s["name"] for s in corps["sources"]) == ["point.md", "rachat.md"]
    assert _envoye(chaine, SECRET)
    assert ("assistant", 1) in _executions(chaine) and ("assistant", 0) in _executions(chaine)
    [ligne] = _lignes(caplog)
    assert doc["id"] in ligne and f"fournisseur={fournisseur}" in ligne and SECRET not in ligne


def test_l_assistant_decide_passage_par_passage_selon_le_proprietaire(chaine) -> None:
    """Bruno n'autorise pas : son ultra-confidentiel reste écarté, même si Alice autorise le sien."""
    _autoriser(chaine, "alice")
    _ecrire(chaine, "bruno", "moule.md", texte="Le rachat du moule BRUNO-SECRET est prévu.")
    _ecrire(chaine, "bruno", "atelier.md", "standard", "Le rachat de l'atelier est reporté.")
    _indexer()
    corps = _demander(chaine, "bruno", "openai", {"mode": "all"}).json()
    assert corps["passages_ecartes"] == 1 and [s["name"] for s in corps["sources"]] == ["atelier.md"]
    assert not _envoye(chaine, "BRUNO-SECRET")


def test_l_assistant_en_local_et_l_assistant_sans_rien_d_autorise_sont_inchanges(chaine) -> None:
    _ecrire(chaine, "alice", "rachat.md")
    _indexer()
    refus = _demander(chaine, "alice", "anthropic")
    assert refus.status_code == 403 and refus.json()["detail"] == sensitivity.REFUS_CLOUD
    assert chaine["requetes"] == []


# ---------- Aucun droit en plus : partages, Q16, sensibilité changée après partage ----------

def _partager(chaine, type_: str, cible: str):
    return chaine["alice"]["client"].post("/api/partages", json={"type": type_, "cible": cible,
                                                                 "invite": "bruno"})


def test_reglage_actif_le_partage_l_ultra_et_q16_sont_inchanges(chaine) -> None:
    _autoriser(chaine, "alice")
    secret = _ecrire(chaine, "alice", "rachat.md")
    ouvert = _ecrire(chaine, "alice", "point.md", "standard", "Le rachat du matériel OUVERT est reporté.")
    _ecrire(chaine, "alice", "annexe.md", "standard", "Le rachat SOEUR-SECRET est annexé.",
            reunion=secret["reunion"])
    _indexer()
    bruno = chaine["bruno"]["client"]
    projet = chaine["alice"]["projet"].id

    # Q16 : une réunion ultra-confidentielle ne se partage toujours pas.
    assert _partager(chaine, "reunion", secret["reunion"]).status_code == 409
    assert _partager(chaine, "projet", projet).status_code == 201
    lus = sorted(d["name"] for d in bruno.get("/api/library", params={"view_scope": "shared"}).json())
    assert lus == ["point.md"]
    assert bruno.get("/api/transcript/rachat.md", params={"view_scope": "shared"}).status_code == 404
    # L'Assistant de Bruno, en cloud, sur ce qui lui est partagé : rien de l'ultra d'Alice,
    # ni de sa réunion, bien qu'Alice autorise le cloud pour le sien.
    corps = _demander(chaine, "bruno", "openai", {"mode": "shared"}).json()
    assert [s["name"] for s in corps["sources"]] == ["point.md"]
    assert not _envoye(chaine, SECRET) and not _envoye(chaine, "SOEUR-SECRET")
    # Bruno ne relance rien chez Alice, ni CR ni faits : 404, hors de sa portée d'écriture.
    assert bruno.post("/api/transcript/point.md/report", params={"view_scope": "shared"},
                      json={"llm_provider": "openai"}).status_code == 404
    assert bruno.post(f"/api/meetings/{ouvert['reunion']}/ecl", params={"view_scope": "shared"},
                      json={"provider": "openai"}).status_code == 404

    # Passé en ultra-confidentiel APRÈS le partage : il disparaît de la vue de Bruno, et
    # rien n'en part vers le cloud depuis l'Assistant de Bruno.
    chaine["requetes"].clear()
    assert chaine["alice"]["client"].patch("/api/transcript/point.md/sensitivity",
                                           params={"view_scope": projet},
                                           json={"sensitivity": "ultra_confidential"}).status_code == 200
    assert bruno.get("/api/library", params={"view_scope": "shared"}).json() == []
    assert bruno.get("/api/transcript/point.md", params={"view_scope": "shared"}).status_code == 404
    reponse = _demander(chaine, "bruno", "openai", {"mode": "shared"})
    assert reponse.status_code == 200 and reponse.json()["sources"] == []
    assert not _envoye(chaine, "OUVERT")
    # Pour Alice elle-même, son réglage vaut : son Assistant envoie son ultra-confidentiel.
    sources = _demander(chaine, "alice", "openai").json()["sources"]
    assert "rachat.md" in [s["name"] for s in sources] and _envoye(chaine, SECRET)


# ---------- MCP : outils de lecture ----------

def _lecteur(identifiant: str) -> mcp.Lecteur:
    return mcp.Lecteur(auth.Utilisateur(id=identifiant, identifiant=identifiant, admin=False))


def test_le_mcp_rend_l_ultra_du_compte_seulement_avec_son_reglage(chaine, caplog) -> None:
    secret = _ecrire(chaine, "alice", "rachat.md")
    (chaine["alice"]["dossier"] / "rachat-cr.md").write_text(
        f"---\nid: {docid.nouveau()}\nmeeting_id: {secret['reunion']}\n"
        f"project_id: {chaine['alice']['projet'].id}\ntitle: Rachat\ntype: report\n"
        f"source_transcript: rachat.md\nsource_document_id: {secret['id']}\n"
        f"sensitivity: ultra_confidential\n---\n\n## Décisions\n\n- {SECRET} [source 00:00:01]\n",
        encoding="utf-8")
    _indexer()
    alice = _lecteur("alice")
    assert alice.lister_reunions({})["ecartees_ultra_confidentielles"] == 1
    assert alice.rechercher({"requete": "rachat"})["ecartes_ultra_confidentiels"] >= 1
    with pytest.raises(mcp.Introuvable):
        alice.lire_compte_rendu({"reunion": secret["reunion"]})

    _autoriser(chaine, "alice")
    with caplog.at_level("INFO", logger="memoglyph.envoi"):
        reunions = alice.lister_reunions({})
        resultats = alice.rechercher({"requete": "rachat"})
        cr = alice.lire_compte_rendu({"reunion": secret["reunion"]})
        alice.lire_faits({"reunion": secret["reunion"]})

    assert reunions["ecartees_ultra_confidentielles"] == 0
    assert [r["meeting_id"] for r in reunions["reunions"]] == [secret["reunion"]]
    assert resultats["ecartes_ultra_confidentiels"] == 0 and resultats["resultats"]
    assert SECRET in cr["compte_rendu"]
    lignes = _lignes(caplog)
    assert lignes and all("fournisseur=mcp" in ligne and "compte=alice" in ligne for ligne in lignes)
    assert not any(SECRET in ligne or "Rachat" in ligne for ligne in lignes)

    # Désactivé : fermé de nouveau dès l'appel suivant.
    _autoriser(chaine, "alice", False)
    with pytest.raises(mcp.Introuvable):
        alice.lire_compte_rendu({"reunion": secret["reunion"]})


def test_le_mcp_d_un_lecteur_de_partage_ne_rend_jamais_l_ultra_du_proprietaire(chaine) -> None:
    _autoriser(chaine, "alice")
    _autoriser(chaine, "bruno")
    secret = _ecrire(chaine, "alice", "rachat.md")
    _ecrire(chaine, "alice", "point.md", "standard", "Le rachat du matériel est reporté.")
    _indexer()
    assert _partager(chaine, "projet", chaine["alice"]["projet"].id).status_code == 201
    bruno = _lecteur("bruno")
    noms = [r["name"] for r in bruno.rechercher({"requete": "rachat"})["resultats"]]
    assert noms == ["point.md"]
    with pytest.raises(mcp.Introuvable):
        bruno.lire_compte_rendu({"reunion": secret["reunion"]})
    assert secret["reunion"] not in str(bruno.lister_reunions({}))
    # L'autorisation est lue chez le propriétaire du projet (manifeste du dossier).
    assert library.proprietaire_du_document("rachat.md") == "alice"
    assert not sensitivity.autorise(None), "sans propriétaire, aucune autorisation"


# ---------- La trace dans `usage.db` ----------

def test_une_base_d_usage_existante_recoit_la_colonne_a_zero(tmp_path) -> None:
    from core import telemetry, usage_store

    ancienne = tmp_path / "usage-v6.db"
    with closing(sqlite3.connect(ancienne)) as conn, conn:
        conn.execute("CREATE TABLE execution (execution_id TEXT PRIMARY KEY, kind TEXT NOT NULL, "
                     "document_id TEXT, meeting_id TEXT, project_id TEXT, produced_artifact_id TEXT, "
                     "provider_primary TEXT, model_primary TEXT, started_at TEXT NOT NULL, ended_at TEXT, "
                     "wall_clock_s REAL, status TEXT, n_calls INTEGER DEFAULT 0, in_tok INTEGER, "
                     "out_tok INTEGER, schema_version INTEGER NOT NULL, created_at TEXT NOT NULL)")
        conn.execute("INSERT INTO execution (execution_id, kind, started_at, schema_version, created_at) "
                     "VALUES ('ANCIENNE', 'report', '2026-01-01', 6, '2026-01-01')")

    with closing(usage_store.connect(ancienne)):
        pass
    tampon = telemetry._ExecBuffer("assistant", provider="openai")
    tampon.envoi_ultra_autorise = True
    tampon.close("ok")
    assert usage_store.persist_execution(tampon, path=ancienne)

    with closing(sqlite3.connect(ancienne)) as conn:
        lignes = dict(conn.execute("SELECT execution_id, envoi_ultra_autorise FROM execution"))
    assert lignes == {"ANCIENNE": 0, tampon.execution_id: 1}
