"""Fiabilité du compte-rendu par l'API (audit externe, A1) : refus avant tout travail.

Un CR cite `[source HH:MM:SS]` ; sans horodatage il n'aurait rien à citer. La
combinaison est refusée en 400 à la transcription comme à la génération, avant
la réservation de la machine et avant tout appel au modèle.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api import main as api_main
from api.jobs import JobManager
from core import docid, library, projects, report
from tests.conftest import COMPTE_DE_TEST

pytestmark = pytest.mark.integration


@pytest.fixture
def client(monkeypatch, tmp_path, isolated_stores):
    monkeypatch.setattr(api_main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(api_main, "jobs", JobManager())
    sortie = isolated_stores["output_dir"]
    projects.ensure_inbox(sortie)
    _dossier, inbox = projects.personal_inbox(sortie, COMPTE_DE_TEST)
    return TestClient(api_main.app), _dossier, inbox


def _ecrire(dossier, nom, project_id, corps) -> None:
    (dossier / nom).write_text(
        f"---\nid: {docid.nouveau()}\nmeeting_id: {docid.nouveau()}\nproject_id: {project_id}\n"
        f"title: {nom}\n---\n\n## Transcript\n\n{corps}\n", encoding="utf-8")


def test_transcrire_avec_cr_sans_horodatage_est_refuse_avant_le_travail(client, monkeypatch) -> None:
    http, _dossier, _inbox = client
    appels = []
    monkeypatch.setattr(api_main.pipeline, "run", lambda *a, **k: appels.append(k))

    reponse = http.post("/api/transcribe", files={"file": ("a.wav", b"a")},
                        data={"report_on": "true", "timestamps": "false"})

    assert reponse.status_code == 400 and "horodatage" in reponse.json()["detail"]
    assert appels == []
    assert not (api_main.UPLOAD_DIR.exists() and any(api_main.UPLOAD_DIR.iterdir())), "rien d'uploadé"


def test_transcrire_sans_horodatage_ni_cr_reste_permis(client, monkeypatch) -> None:
    http, _dossier, _inbox = client

    def arret(*a, **k):
        raise RuntimeError("arrêt du test après l'acceptation")

    monkeypatch.setattr(api_main.pipeline, "run", arret)

    reponse = http.post("/api/transcribe", files={"file": ("a.wav", b"a")},
                        data={"report_on": "false", "timestamps": "false"})

    assert reponse.status_code == 200


def test_generer_un_cr_sur_un_transcript_sans_horodatage_est_refuse(client, monkeypatch) -> None:
    http, dossier, inbox = client
    _ecrire(dossier, "sans-heure.md", inbox.id, "Bonjour à tous. On fixe le prix.")
    library.reindex()
    appels = []
    monkeypatch.setattr(report, "generate_report", lambda *a, **k: appels.append(a))

    reponse = http.post("/api/transcript/sans-heure.md/report", params={"view_scope": "all"})

    assert reponse.status_code == 400 and "horodatage" in reponse.json()["detail"]
    assert appels == []


def test_generer_un_cr_sur_un_transcript_non_diarise_horodate_est_permis(client, monkeypatch) -> None:
    http, dossier, inbox = client
    _ecrire(dossier, "horodate.md", inbox.id, "[00:00:01] Bonjour à tous.\n\n[00:00:04] On fixe le prix.")
    library.reindex()
    monkeypatch.setattr(report, "generate_report", lambda name, **k: {"name": "cr.md", "content": "CR"})

    reponse = http.post("/api/transcript/horodate.md/report", params={"view_scope": "all"})

    assert reponse.status_code == 200 and reponse.json()["name"] == "cr.md"


def test_une_extraction_vide_rend_un_422_avec_sa_cause(client, monkeypatch) -> None:
    """Audit externe, 4a : le travail a tourné et n'a rien retenu, ce n'est pas un 500."""
    from core.experimental.pipeline import ExtractionVide
    http, dossier, inbox = client
    _ecrire(dossier, "vide.md", inbox.id, "[00:00:01] Bonjour.\n\n[00:00:04] Au revoir.")
    library.reindex()

    def vide(*a, **k):
        raise ExtractionVide(appels=3, refuses=1, premier_refus="quota")

    monkeypatch.setattr(report, "generate_report", vide)
    reponse = http.post("/api/transcript/vide.md/report", params={"view_scope": "all"})

    assert reponse.status_code == 422
    assert reponse.json()["detail"] == ("Extraction vide : aucune unité retenue sur 3 appel(s) "
                                        "au modèle, dont 1 refusé(s) ; premier refus : quota.")
