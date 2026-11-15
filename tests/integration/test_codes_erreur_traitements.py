"""Les refus de l'envoi, de l'Assistant et de la règle d'envoi en ligne portent un code stable, en plus de leur phrase.

Le code s'ajoute : statut, phrase (`detail`) et en-têtes sont ceux d'avant. Un code qui existe déjà (quota atteint, modèle
gratuit, doublon, annulation impossible) n'est jamais remplacé.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api import main as api_main
from api.jobs import JobManager, JobsBusy
from core import config, llm, projects, rag, resources, sensitivity
from tests.conftest import COMPTE_DE_TEST

pytestmark = pytest.mark.integration


@pytest.fixture
def client(monkeypatch, tmp_path, isolated_stores) -> TestClient:
    monkeypatch.setattr(api_main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(api_main, "jobs", JobManager())
    projects.ensure_inbox(isolated_stores["output_dir"])
    projects.personal_inbox(isolated_stores["output_dir"], COMPTE_DE_TEST)
    return TestClient(api_main.app, raise_server_exceptions=False)


def _refus(reponse, statut: int, code: str) -> dict:
    assert reponse.status_code == statut
    corps = reponse.json()
    assert corps["code"] == code
    assert set(corps) == {"detail", "code"}, "une phrase et un code, rien d'autre"
    return corps


def _demander(client: TestClient, question: str = "Le budget ?"):
    return client.post("/api/ask", json={"question": question, "scope": {"mode": "all"}})


# ---------- Transcrire ----------

def test_un_compte_rendu_sans_horodatage_porte_son_code(client, monkeypatch) -> None:
    monkeypatch.setattr(api_main.pipeline, "run", lambda *a, **k: None)
    reponse = client.post("/api/transcribe", files={"file": ("a.wav", b"a")},
                          data={"report_on": "true", "timestamps": "false"})
    assert "horodatage" in _refus(reponse, 400, "transcribe.timestamps.required")["detail"]


def test_un_fichier_trop_volumineux_porte_son_code(client, monkeypatch) -> None:
    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 0)
    reponse = client.post("/api/transcribe", files={"file": ("a.wav", b"abc")})
    assert _refus(reponse, 413, "transcribe.file.tooLarge")["detail"].startswith("Fichier trop volumineux")


def test_une_machine_occupee_a_l_envoi_porte_son_code(client, monkeypatch) -> None:
    def occupe(*_a, **_k):
        raise JobsBusy("Un autre traitement est en cours.")

    monkeypatch.setattr(api_main.jobs, "create", occupe)
    reponse = client.post("/api/transcribe", files={"file": ("a.wav", b"abc")})
    assert _refus(reponse, 429, "resources.busy")["detail"] == "Un autre traitement est en cours."


# ---------- Assistant ----------

def test_une_question_vide_porte_son_code(client) -> None:
    corps = _refus(_demander(client, "   "), 400, "ask.question.empty")
    assert corps["detail"] == "Question vide"


def test_un_assistant_indisponible_porte_son_code(client, monkeypatch) -> None:
    def panne(*_a, **_k):
        raise llm.LLMError("panne du moteur")

    monkeypatch.setattr(rag, "ask", panne)
    corps = _refus(_demander(client), 503, "ask.unavailable")
    assert corps["detail"].startswith("Assistant indisponible : ")


def test_un_assistant_sur_une_machine_occupee_porte_son_code(client, monkeypatch) -> None:
    def occupe(*_a, **_k):
        raise resources.ResourcesBusy("Ressources occupées.")

    monkeypatch.setattr(rag, "ask", occupe)
    assert _refus(_demander(client), 429, "resources.busy")["detail"] == "Ressources occupées."


def test_des_passages_ultra_confidentiels_pour_un_fournisseur_en_ligne_portent_leur_code(client, monkeypatch) -> None:
    def refuse(*_a, **_k):
        raise rag.PassagesUltraConfidentiels("Les passages trouvés sont ultra-confidentiels.")

    monkeypatch.setattr(rag, "ask", refuse)
    corps = _refus(_demander(client), 403, "ask.ultra.blocked")
    assert corps["detail"] == "Les passages trouvés sont ultra-confidentiels."


# ---------- règle d'envoi en ligne ----------

def test_la_garde_de_route_refuse_l_ultra_confidentiel_au_nuage_avec_son_code(monkeypatch) -> None:
    monkeypatch.setattr(sensitivity, "autorise", lambda _proprietaire: False)
    with pytest.raises(api_main.RefusAvecCode) as refus:
        api_main._refuser_le_cloud("ultra_confidential", local=False, proprietaire=None)
    assert (refus.value.status_code, refus.value.code) == (403, "cloud.refused")
    assert refus.value.detail == sensitivity.REFUS_CLOUD


def test_le_refus_du_coeur_juste_avant_l_envoi_porte_le_meme_code(client, monkeypatch) -> None:
    def refuse(*_a, **_k):
        raise sensitivity.EnvoiRefuse(sensitivity.REFUS_CLOUD)

    monkeypatch.setattr(rag, "ask", refuse)
    corps = _refus(_demander(client), 403, "cloud.refused")
    assert corps["detail"] == sensitivity.REFUS_CLOUD
