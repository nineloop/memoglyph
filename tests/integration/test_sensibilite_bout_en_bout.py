"""T3 de bout en bout : la provenance du CR et la transcription cloud ordinaire.

- Un document devenu ultra-confidentiel PENDANT un CR cloud, réglage du propriétaire
  actif, part avec autorisation : le front-matter du CR le dit (`envoi_ultra_autorise`),
  pas seulement le journal des envois.
- Une transcription cloud `standard` passe par la relecture de la sensibilité : le
  document n'est pas encore indexé, le résolveur rend None, la valeur portée vaut et
  l'audio part. Non-régression : traiter None comme un échec refuserait toute
  transcription cloud.

Vraie route, vrai pipeline, vraie façade ; seuls les moteurs, le décodage audio et les
embeddings sont simulés. Rien ne sort de la machine.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from api import auth
from api import main as api_main
from api.jobs import JobManager
from core import docid, library, llm, pipeline, projects, semantic, sensitivity
from core.config import EMBED_DIM
from core.stt_base import Segment, Transcript
from tests.conftest import COMPTE_DE_TEST, compte_de_test

pytestmark = pytest.mark.integration

ULTRA = sensitivity.ULTRA_CONFIDENTIEL


def _vecteur(texte: str) -> list[float]:
    v = [0.0] * EMBED_DIM
    v[0 if "rachat" in texte.lower() else 1] = 1.0
    return v


class FauxMoteur:
    """Un moteur LLM cloud qui note chaque requête et répond à toutes les étapes du CR."""

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
        self.requetes.append(user)
        return "réponse [1]"

    def chat_json(self, system, user, **options):
        self.requetes.append(user)
        return {"units": [{"kind": "fact", "text": "Le rachat est validé.", "start": "00:00:01"}],
                "assignments": [], "sentences": [], "sections": [], "thesis": {}, "tensions": [],
                "faits": [], "conversation": {"type": "réunion", "summary": "s", "topics": []},
                "speakers": {}}


class FauxSTT:
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
    monkeypatch.setattr(api_main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(api_main, "jobs", JobManager())
    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(tmp_path / "usage.db"))
    monkeypatch.setattr(semantic.embed, "embed_documents", lambda textes, **_k: [_vecteur(t) for t in textes])
    monkeypatch.setattr(semantic.embed, "embed_query", lambda t, **_k: _vecteur(t))
    requetes: list = []
    for nom in ("mistral", "openai", "anthropic"):
        monkeypatch.setitem(llm.MOTEURS, nom, FauxMoteur(nom, requetes))
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
    inbox = projects.create(isolated_stores["output_dir"], name=projects.INBOX_NAME, kind="inbox",
                            owner=COMPTE_DE_TEST)
    yield {"client": TestClient(api_main.app), "inbox": inbox, "requetes": requetes, "audio": audio,
           "sortie": isolated_stores["output_dir"]}
    # Le compte de la session vit toute la session : un réglage activé ici n'en sort pas.
    auth.regler_cloud_ultra(compte_de_test(), False)


def _attendre(client: TestClient, job_id: str) -> dict:
    for _ in range(2000):
        etat = client.get(f"/api/jobs/{job_id}").json()
        if etat["status"] in {"done", "error"}:
            return etat
        time.sleep(0.01)
    raise AssertionError("travail sans fin")


def test_un_cr_devenu_ultra_et_autorise_en_cours_le_dit_dans_sa_provenance(chaine) -> None:
    nom = "point.md"
    (chaine["sortie"] / nom).write_text(
        f"---\nid: {docid.nouveau()}\nmeeting_id: {docid.nouveau()}\nproject_id: {chaine['inbox'].id}\n"
        f"title: Point\nlanguage: fr\nsensitivity: standard\n---\n\n## Transcript\n\n"
        "**Alice** [00:00:01] Le rachat est validé.\n", encoding="utf-8")
    library.reindex()
    semantic.reindex_all()
    reglage = chaine["client"].put("/api/compte/cloud-ultra", json={"autorise": True})
    assert reglage.status_code == 200 and reglage.json()["autorise"] is True
    moteur = llm.MOTEURS["openai"]
    repondre = moteur.chat_json

    def puis_basculer(system, user, **options):
        if not chaine["requetes"]:   # au premier appel du CR, le document passe en ultra
            assert library.set_sensitivity(nom, ULTRA) == ULTRA
        return repondre(system, user, **options)

    moteur.chat_json = puis_basculer
    reponse = chaine["client"].post(f"/api/transcript/{nom}/report", params={"view_scope": chaine["inbox"].id},
                                    json={"llm_provider": "openai"})
    assert reponse.status_code == 200, reponse.text
    assert len(chaine["requetes"]) > 1, "des appels sont partis après la bascule, avec autorisation"
    meta = library.get_transcript(reponse.json()["name"])["meta"]
    assert meta.get("envoi_ultra_autorise") is True, "la provenance du CR dit l'envoi ultra autorisé"
    # Sans aucune ECL : découplage ECL / CR, le CR n'extrait plus de faits.
    assert not (chaine["sortie"] / ".ecl").exists(), "aucune extraction de faits pendant le CR"


def test_une_transcription_cloud_standard_passe_par_la_relecture_et_part(chaine, monkeypatch) -> None:
    relues: list = []
    resoudre = sensitivity._resolveur_sensibilite

    def temoin(document_id):
        valeur = resoudre(document_id)
        relues.append((document_id, valeur))
        return valeur

    monkeypatch.setattr(sensitivity, "_resolveur_sensibilite", temoin)
    reponse = chaine["client"].post("/api/transcribe", files={"file": ("point.wav", b"audio")},
                                    data={"engine": "mistral", "sensitivity_level": "standard",
                                          "report_on": "false", "profile": "false",
                                          "destination_project_id": chaine["inbox"].id})
    assert reponse.status_code == 200, reponse.text
    etat = _attendre(chaine["client"], reponse.json()["job_id"])

    assert etat["status"] == "done", etat
    assert chaine["audio"] == ["mistral"], "l'audio est parti vers le fournisseur cloud"
    identite = library.get_transcript(etat["result"]["name"])["meta"]["id"]
    assert relues and {d for d, _v in relues} == {identite}, "la relecture a porté sur ce document"
    assert all(v is None for _d, v in relues), "pas encore indexé : le résolveur rend None"
