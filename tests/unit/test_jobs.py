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
    # Le texte brut reste ; le code de la cause s'y AJOUTE (l'interface traduit le code, jamais le texte).
    assert job.events[-1].payload == {"type": "error", "error": "cassé",
                                      "code": "step.error.unexpected", "params": {}}


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


# ---------- Chantier 003, tranche B : traitements visibles partout ----------

def _attendre_statut(job, statut: str) -> None:
    import time
    for _ in range(400):
        if job.status == statut:
            return
        time.sleep(0.005)
    raise AssertionError(f"{job.id} n'est jamais passé à {statut}")


def test_le_job_gagne_les_champs_de_suivi_et_snapshot_garde_ses_cles() -> None:
    manager = JobManager()
    job = manager.create(owner="alice", nature="transcription", titre="visio du 2 novembre",
                         projet_id="P1", projet_nom="Réunions AMI", jeton="J1",
                         plan=["transcription", "ecriture"])
    assert (job.titre, job.projet_id, job.projet_nom, job.jeton) == (
        "visio du 2 novembre", "P1", "Réunions AMI", "J1")
    assert job.en_attente is False and job.meeting is None and job.capture_id is None
    assert isinstance(job.debut, float) and job.debut > 1_700_000_000
    instantane = manager.snapshot(job)
    # Les clés d'avant restent, avec les mêmes valeurs ; les nouvelles s'y ajoutent.
    for cle in ("id", "status", "progress", "message", "message_code", "message_params", "result", "error"):
        assert cle in instantane
    for cle in ("titre", "projet_id", "projet_nom", "etape", "etapes", "en_attente", "debut"):
        assert cle in instantane
    # Jamais le jeton (ni le secret) dans ce qu'on rend à un client.
    assert "jeton" not in instantane


def test_les_etapes_suivent_les_codes_de_message_du_plan() -> None:
    from core import messages
    manager = JobManager()
    job = manager.create(owner="a", nature="transcription",
                         plan=["transcription", "locuteurs", "ecriture"])
    fini = threading.Event()

    def target(progress):
        progress(0.1, messages.transcription("whisper"))
        assert (job.etape, job.etapes) == (1, 3)
        progress(0.6, messages.diarisation())
        assert (job.etape, job.etapes) == (2, 3)
        # Un code d'une étape que ce traitement n'a pas demandée ne fait pas reculer le compteur.
        progress(0.7, messages.profilage("m"))
        assert (job.etape, job.etapes) == (2, 3)
        progress(0.95, messages.ecriture_markdown())
        assert (job.etape, job.etapes) == (3, 3)
        fini.set()
        return {}

    manager.run(job, target)
    assert fini.wait(timeout=2)
    _attendre_statut(job, "done")


def test_un_job_sans_plan_n_a_pas_d_etape() -> None:
    manager = JobManager()
    job = manager.create(owner="a", nature="archives")
    assert job.etape is None and job.etapes is None


def test_list_for_ne_rend_que_les_traitements_du_proprietaire() -> None:
    manager = JobManager(max_active=10)
    a1 = manager.create(owner="alice", nature="transcription", titre="A1")
    manager.create(owner="bruno", nature="transcription", titre="B1")
    a2 = manager.create(owner="alice", nature="faits", titre="A2")
    assert {j.id for j in manager.list_for("alice")} == {a1.id, a2.id}
    assert manager.list_for("carole") == []


def test_l_eviction_est_par_compte_et_ne_touche_jamais_un_job_actif() -> None:
    manager = JobManager(max_active=500, max_history=3, max_total=100)
    bruno = [manager.create(owner="bruno") for _ in range(2)]
    for j in bruno:
        j.status = "done"
    alice = [manager.create(owner="alice") for _ in range(8)]
    for j in alice[:6]:
        j.status = "done"          # six terminés, deux encore actifs
    manager.create(owner="alice")   # déclenche l'éviction
    restes_alice = [j for j in manager.list_for("alice") if j.status == "done"]
    assert len(restes_alice) == 3                                # plafond par compte
    assert all(j.status in ("pending", "running") for j in manager.list_for("alice")
               if j not in restes_alice)                         # les actifs sont intacts
    assert len([j for j in manager.list_for("alice") if j.status == "pending"]) == 3
    # Un compte actif n'efface pas l'historique d'un autre.
    assert {j.id for j in manager.list_for("bruno")} == {j.id for j in bruno}


def test_le_plafond_total_s_applique_apres_le_plafond_par_compte() -> None:
    manager = JobManager(max_active=500, max_history=50, max_total=5)
    for i in range(4):
        for compte in ("alice", "bruno", "carole"):
            manager.create(owner=compte).status = "done"
    manager.create(owner="alice")
    finis = [j for j in manager._jobs.values() if j.status == "done"]
    assert len(finis) <= 5


def test_le_code_d_une_erreur_survit_au_texte() -> None:
    from core import messages
    manager = JobManager()
    job = manager.create(owner="a")
    fini = threading.Event()

    def target(progress):
        try:
            raise RuntimeError(messages.trop_d_attente("traitement"))
        finally:
            fini.set()

    manager.run(job, target)
    assert fini.wait(timeout=2)
    _attendre_statut(job, "error")
    assert job.error_code == "step.error.waitTooLong"
    assert job.error_params == {"label": "traitement"}
    assert "Ressources" in (job.error or "")            # le texte reste, pour les clients existants


def test_creer_ou_retrouver_une_capture_est_atomique() -> None:
    manager = JobManager(max_active=50)
    resultats: list[tuple[str, bool]] = []
    barriere = threading.Barrier(8)

    def envoi() -> None:
        barriere.wait()
        job, cree = manager.create_or_find_capture("alice", "cap-1", nature="transcription")
        resultats.append((job.id, cree))

    fils = [threading.Thread(target=envoi) for _ in range(8)]
    for f in fils:
        f.start()
    for f in fils:
        f.join()
    assert len({jid for jid, _ in resultats}) == 1                # un seul job
    assert sum(1 for _, cree in resultats if cree) == 1           # un seul créateur
    # Un autre compte, même identifiant : jamais « déjà reçue ».
    autre, cree = manager.create_or_find_capture("bruno", "cap-1", nature="transcription")
    assert cree and autre.id != resultats[0][0]


def test_un_traitement_en_echec_puis_renvoye_cree_un_nouveau_traitement() -> None:
    manager = JobManager()
    premier, cree = manager.create_or_find_capture("alice", "cap-2", nature="transcription")
    premier.status = "error"
    second, cree2 = manager.create_or_find_capture("alice", "cap-2", nature="transcription")
    assert cree and cree2 and second.id != premier.id
