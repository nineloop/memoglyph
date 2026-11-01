from __future__ import annotations

import threading

import pytest

from api.jobs import JobManager


pytestmark = pytest.mark.unit


def test_unknown_job_returns_none() -> None:
    assert JobManager().get("missing") is None


def test_job_records_progress_and_result() -> None:
    manager = JobManager()
    job = manager.create()
    finished = threading.Event()

    def target(progress):
        progress(0.5, "Milieu")
        finished.set()
        return {"ok": True}

    manager.run(job, target)
    assert finished.wait(timeout=1)
    # Le worker peut positionner l'événement juste avant son état final.
    for _ in range(1000):
        if job.status == "done":
            break

    assert job.status == "done"
    assert job.progress == 0.5
    assert job.message == "Milieu"
    assert job.result == {"ok": True}
    assert job.events[-1].payload == {"type": "done", "result": {"ok": True}}


def test_job_records_errors_without_leaking_exception() -> None:
    manager = JobManager()
    job = manager.create()
    finished = threading.Event()

    def target(progress):
        try:
            raise ValueError("cassé")
        finally:
            finished.set()

    manager.run(job, target)
    assert finished.wait(timeout=1)
    for _ in range(1000):
        if job.status == "error":
            break

    assert job.status == "error"
    assert job.error == "cassé"
    assert job.events[-1].payload == {"type": "error", "error": "cassé"}


def test_job_events_are_monotonic_and_resumable() -> None:
    manager = JobManager()
    job = manager.create()

    manager.push(job, {"type": "partial", "text": "premier"})
    manager.push(job, {"type": "partial", "text": "second"})

    events, status = manager.events_after(job, 1)
    assert status == "pending"
    assert [(event.id, event.payload) for event in events] == [
        (2, {"type": "partial", "text": "second"}),
    ]


def test_job_failure_is_written_to_the_server_log(capsys) -> None:
    """L'erreur n'existait qu'à l'écran : une fois la page fermée, le journal
    de l'API ne gardait aucune trace de l'échec."""
    manager = JobManager()
    job = manager.create()

    def target(progress):
        raise RuntimeError("Transcription refusée par Mistral (HTTP 429)")

    manager.run(job, target)
    # Le journal est écrit après la mise à jour du job : on attend la ligne.
    sortie = ""
    for _ in range(200):
        sortie += capsys.readouterr().out
        if "Traceback" in sortie:
            break
        threading.Event().wait(0.01)

    assert job.status == "error"
    assert f"[job {job.id}] échec : RuntimeError: Transcription refusée" in sortie
    assert "Traceback" in sortie


# ---------- Relevé des traitements en cours, lu par `stop.sh` ----------

def _attendre(condition, delai: float = 5.0) -> bool:
    import time
    fin = time.monotonic() + delai
    while time.monotonic() < fin:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def test_le_releve_nomme_les_traitements_en_cours_puis_disparait(tmp_path) -> None:
    """Les jobs ne vivent qu'en mémoire : sans ce fichier, `stop.sh` coupait
    une transcription sans le savoir. Il n'existe que tant qu'un job tourne."""
    import json
    import os
    import stat

    releve = tmp_path / ".memoglyph-traitements.json"
    manager = JobManager(releve=releve)
    job = manager.create(nature="transcription")
    libere = threading.Event()

    def target(progress):
        progress(0.4, "En cours")
        libere.wait(timeout=5)
        return {}

    contenu = json.loads(releve.read_text(encoding="utf-8"))
    assert contenu["pid"] == os.getpid()
    assert [t["nature"] for t in contenu["traitements"]] == ["transcription"]
    assert stat.S_IMODE(releve.stat().st_mode) == 0o600

    manager.run(job, target)
    assert _attendre(lambda: json.loads(releve.read_text(encoding="utf-8"))
                     ["traitements"][0]["statut"] == "running")
    libere.set()
    assert _attendre(lambda: not releve.exists()), "le relevé survit à la fin du job"


def test_le_releve_disparait_apres_un_echec_et_un_abandon(tmp_path) -> None:
    releve = tmp_path / ".memoglyph-traitements.json"
    manager = JobManager(releve=releve)

    abandonne = manager.create(nature="voix")
    assert releve.exists()
    manager.discard(abandonne)
    assert not releve.exists()

    def target(_progress):
        raise RuntimeError("boum")

    manager.run(manager.create(nature="faits"), target)
    assert _attendre(lambda: not releve.exists())


def test_sans_releve_le_gestionnaire_n_ecrit_rien(tmp_path, monkeypatch) -> None:
    """Le défaut des tests et des essais : aucun fichier écrit nulle part."""
    monkeypatch.chdir(tmp_path)
    # Ancre : le balayage voit le dossier, il ne rend pas vide par défaut.
    (tmp_path / "temoin").write_text("", encoding="utf-8")
    manager = JobManager()
    manager.create(nature="transcription")
    assert manager.active_count() == 1
    assert [p.name for p in tmp_path.iterdir()] == ["temoin"]


def test_l_api_tient_son_releve_a_cote_des_fichiers_pid() -> None:
    from api import main as api_main
    from core import sqlite_db

    assert api_main.TRAITEMENTS_PATH == sqlite_db.ROOT / ".memoglyph-traitements.json"
    assert api_main.jobs._releve == api_main.TRAITEMENTS_PATH
