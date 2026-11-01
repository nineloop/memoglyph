"""Le code doit survivre du pipeline jusqu'au client — flux ET reconnexion.

Vérifier `core/messages.py` seul ne prouve rien : le défaut à craindre est que
le code se perde en route, dans un maillon qui traite le message comme une
simple chaîne. Ces tests suivent le trajet complet.
"""

from __future__ import annotations

import json
import threading

from api.jobs import JobManager
from core import messages


def _executer(cible):
    """Lance un job et attend sa fin — `run` est asynchrone par construction."""
    gestionnaire = JobManager(max_active=2, max_history=4)
    job = gestionnaire.create()
    fini = threading.Event()

    def enveloppe(progress):
        try:
            return cible(progress)
        finally:
            fini.set()

    gestionnaire.run(job, enveloppe)
    assert fini.wait(timeout=5), "le job n'a pas terminé"
    for _ in range(200):
        if job.status in ("done", "error"):
            break
        threading.Event().wait(0.01)
    return gestionnaire, job


def test_le_code_arrive_dans_le_flux_d_evenements():
    gestionnaire, job = _executer(
        lambda p: (p(0.5, messages.diarisation()), {"ok": True})[1]
    )
    progressions = [e.payload for e in job.events if e.payload["type"] == "progress"]
    assert progressions == [
        {
            "type": "progress",
            "progress": 0.5,
            "code": "step.diarization",
            "params": {},
            "message": "Identifying speakers…",
        }
    ]


def test_le_code_survit_a_une_reconnexion_par_snapshot():
    """Le piège : un client qui reconnecte lit `snapshot()`, pas le flux.

    Sans code dans l'instantané, il retomberait sur l'anglais — le défaut
    corrigé, simplement déplacé à la reconnexion.
    """
    gestionnaire, job = _executer(
        lambda p: (p(0.65, messages.profilage("qwen2.5:7b")), {})[1]
    )
    instantane = gestionnaire.snapshot(job)
    assert instantane["message_code"] == "step.profiling"
    assert instantane["message_params"] == {"model": "qwen2.5:7b"}
    assert instantane["message"] == "Profiling with qwen2.5:7b…"


def test_l_evenement_est_serialisable_en_json_sans_perdre_le_code():
    """Le flux SSE sérialise ; un `Message` brut y perdrait son code."""
    gestionnaire, job = _executer(
        lambda p: (p(1.0, messages.termine("reunion.md")), {})[1]
    )
    charge = [e.payload for e in job.events if e.payload["type"] == "progress"][0]
    relu = json.loads(json.dumps(charge))
    assert relu["code"] == "step.done"
    assert relu["params"] == {"name": "reunion.md"}


def test_une_chaine_nue_passe_sans_code_plutot_que_d_echouer():
    """Une dépendance ou une exception peut émettre un texte sans code.

    Refuser ces cas obligerait à inventer un code par erreur système. Le
    message passe, sans code, et l'interface l'affiche tel quel.
    """
    gestionnaire, job = _executer(lambda p: (p(0.2, "Chargement…"), {})[1])
    charge = [e.payload for e in job.events if e.payload["type"] == "progress"][0]
    assert charge["code"] is None
    assert charge["message"] == "Chargement…"
    assert gestionnaire.snapshot(job)["message_code"] is None


def test_un_job_qui_n_a_rien_emis_n_a_pas_de_code():
    gestionnaire, job = _executer(lambda p: {})
    instantane = gestionnaire.snapshot(job)
    assert instantane["message"] == ""
    assert instantane["message_code"] is None
    assert instantane["message_params"] == {}
