"""Relecture fermée en cas d'échec, une règle de réunion lue par trois surfaces, transcription stricte.

- T3 : pour un envoi cloud, un résolveur de sensibilité qui LÈVE refuse l'envoi. Un
  document qu'il ne connaît pas (`None`, pas encore indexé) ou un envoi sans identité
  gardent la valeur portée. En local, rien ne change.
- Garde-fou d'équivalence : le niveau de la réunion (Q16) a un seul calcul, celui de
  `library` (`sensibilite_effective`), que lisent `semantic.retrieve` (la sensibilité de
  chaque passage) et `library.documents_lisibles` (les partages). Vert dès son
  écriture : il est là pour qu'une divergence future rougisse.
- T2 étendu à `/api/transcribe` : une sensibilité inconnue rend 422 avant toute
  réservation et tout envoi ; absente (ou vide), elle vaut `standard`.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api import main as api_main
from api.jobs import JobManager
from core import docid, library, llm, projects, semantic, sensitivity
from core.config import EMBED_DIM
from tests.conftest import COMPTE_DE_TEST

pytestmark = pytest.mark.integration

ULTRA = sensitivity.ULTRA_CONFIDENTIEL


class _FauxMoteur:
    def __init__(self, nom: str) -> None:
        self.name = nom
        self.appels: list[str] = []

    def chat(self, system, user, **_k):
        self.appels.append(user)
        return "réponse"


@pytest.fixture
def moteurs(monkeypatch):
    faux = {nom: _FauxMoteur(nom) for nom in ("openai", "mlx")}
    for nom, moteur in faux.items():
        monkeypatch.setitem(llm.MOTEURS, nom, moteur)
    return faux


# ---------- T3 : la relecture en échec ferme l'envoi cloud ----------

def test_t3_un_resolveur_qui_leve_refuse_en_cloud_et_laisse_passer_le_local(moteurs, monkeypatch) -> None:
    def illisible(_document_id):
        raise RuntimeError("index illisible")

    monkeypatch.setattr(sensitivity, "_resolveur_sensibilite", illisible)
    envoi = sensitivity.Envoi(sensibilite="standard", proprietaire="alice", document_id="D1")
    with pytest.raises(sensitivity.EnvoiRefuse):
        llm.chat("s", "TEXTE", provider="openai", envoi=envoi)
    assert moteurs["openai"].appels == [], "le moteur cloud n'a pas été appelé"
    assert llm.chat("s", "TEXTE", provider="mlx", envoi=envoi) == "réponse"
    assert moteurs["mlx"].appels == ["TEXTE"]


def test_t3_un_document_inconnu_ou_sans_identite_garde_la_valeur_portee(moteurs, monkeypatch) -> None:
    lus: list = []

    def inconnu(document_id):
        lus.append(document_id)
        return None

    monkeypatch.setattr(sensitivity, "_resolveur_sensibilite", inconnu)
    monkeypatch.setattr(sensitivity, "_resolveur", sensitivity._jamais)
    standard = sensitivity.Envoi(sensibilite="standard", proprietaire="alice", document_id="D-neuf")
    assert llm.chat("s", "TEXTE", provider="openai", envoi=standard) == "réponse"
    assert lus == ["D-neuf"]
    # Valeur portée ultra-confidentielle, sans autorisation : refusée, comme avant.
    with pytest.raises(sensitivity.EnvoiRefuse):
        llm.chat("s", "TEXTE", provider="openai",
                 envoi=sensitivity.Envoi(sensibilite=ULTRA, proprietaire="alice", document_id="D-neuf"))
    # Sans identité (transcription d'un document pas encore écrit) : rien n'est relu.
    assert llm.chat("s", "TEXTE", provider="openai", envoi=sensitivity.Envoi(sensibilite="standard")) == "réponse"
    assert lus == ["D-neuf"] and moteurs["openai"].appels == ["TEXTE", "TEXTE"]


# ---------- Garde-fou : trois calculs de la règle de réunion ----------

def _vecteur(_texte: str) -> list[float]:
    v = [0.0] * EMBED_DIM
    v[0] = 1.0
    return v


REUNIONS = {
    "seul-standard": ("standard",),
    "standard-et-ultra": ("standard", ULTRA),
    "seul-ultra": (ULTRA,),
    "confidentiel-et-standard": ("confidential", "standard"),
}
ATTENDU = {
    "seul-standard": ("standard",),
    "standard-et-ultra": (ULTRA, ULTRA),
    "seul-ultra": (ULTRA,),
    # Une réunion a UN niveau, le plus strict de ses documents tant qu'elle n'est pas réglée.
    "confidentiel-et-standard": ("confidential", "confidential"),
}


def test_les_trois_calculs_de_la_regle_de_reunion_concordent(isolated_stores, monkeypatch) -> None:
    monkeypatch.setattr(semantic.embed, "embed_documents", lambda textes, **_k: [_vecteur(t) for t in textes])
    monkeypatch.setattr(semantic.embed, "embed_query", lambda t, **_k: _vecteur(t))
    sortie = isolated_stores["output_dir"]
    inbox = projects.create(sortie, name=projects.INBOX_NAME, kind="inbox", owner=COMPTE_DE_TEST)
    documents = {}
    for reunion, niveaux in REUNIONS.items():
        meeting_id = docid.nouveau()
        for i, niveau in enumerate(niveaux):
            nom, identite = f"{reunion}-{i}.md", docid.nouveau()
            (sortie / nom).write_text(
                f"---\nid: {identite}\nmeeting_id: {meeting_id}\nproject_id: {inbox.id}\ntitle: {nom}\n"
                f"language: fr\nsensitivity: {niveau}\n---\n\n## Transcript\n\n"
                f"**Alice** [00:00:01] Le budget de {reunion} est discuté.\n", encoding="utf-8")
            documents[nom] = {"reunion": reunion, "rang": i, "id": identite, "meeting_id": meeting_id,
                              "niveau": niveau}
    library.reindex()
    semantic.reindex_all()

    par_retrieve = {c["name"]: c["sensitivity"]
                    for c in semantic.retrieve("budget", k=50, project_id=inbox.id)}
    lisibles = library.documents_lisibles(frozenset({inbox.id}), frozenset(), frozenset()).documents
    assert set(par_retrieve) == set(documents), "chaque document est retrouvé"
    for nom, doc in documents.items():
        effective = library.sensibilite_effective(doc["niveau"], doc["meeting_id"])
        assert effective == ATTENDU[doc["reunion"]][doc["rang"]], nom
        assert par_retrieve[nom] == effective, f"{nom} : `semantic.retrieve` diverge"
        assert (doc["id"] in lisibles) == (effective != ULTRA), f"{nom} : `documents_lisibles` diverge"


# ---------- /api/transcribe : une sensibilité inconnue est refusée ----------

class _Arret(Exception):
    pass


@pytest.fixture
def transcription(isolated_stores, tmp_path, monkeypatch):
    projects.create(isolated_stores["output_dir"], name=projects.INBOX_NAME, kind="inbox", owner=COMPTE_DE_TEST)
    monkeypatch.setattr(api_main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(api_main, "jobs", JobManager())
    recus: list = []

    def run(*_a, **options):
        recus.append(options)
        raise _Arret()

    def executer(job, target):
        with pytest.raises(_Arret):
            target(lambda *_a, **_k: None)
        api_main.jobs.discard(job)   # le créneau rendu : la transcription suivante passe

    monkeypatch.setattr(api_main.pipeline, "run", run)
    monkeypatch.setattr(api_main.jobs, "run", executer)
    return {"client": TestClient(api_main.app), "recus": recus, "uploads": tmp_path / "uploads"}


@pytest.mark.parametrize("valeur", ["ultra-confidential", "ULTRA_CONFIDENTIAL", "secret", " standard"])
def test_transcrire_refuse_une_sensibilite_inconnue_avant_tout(transcription, valeur) -> None:
    reponse = transcription["client"].post("/api/transcribe", files={"file": ("r.wav", b"audio")},
                                           data={"sensitivity_level": valeur, "engine": "whisper",
                                                 "llm_provider": "mlx"})
    assert reponse.status_code == 422, reponse.text
    assert transcription["recus"] == [] and not transcription["uploads"].exists(), "rien n'a été reçu"


def test_transcrire_sans_sensibilite_vaut_standard_et_une_valeur_connue_passe(transcription) -> None:
    client = transcription["client"]
    # Un champ vide est lu comme absent par FastAPI : le défaut, comme sans le champ.
    for donnees, attendue in (({}, "standard"), ({"sensitivity_level": ""}, "standard"),
                              ({"sensitivity_level": "confidential"}, "confidential")):
        reponse = client.post("/api/transcribe", files={"file": ("r.wav", b"audio")},
                              data={"engine": "whisper", "llm_provider": "mlx", **donnees})
        assert reponse.status_code == 200, reponse.text
        assert transcription["recus"][-1]["sensitivity"] == attendue
