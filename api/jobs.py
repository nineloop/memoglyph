"""Gestionnaire de jobs en mémoire : exécute une tâche longue dans un thread
et expose sa progression (pour le streaming SSE)."""

from __future__ import annotations

import json
import os
import threading
import time
import traceback
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from core import messages


@dataclass(frozen=True)
class JobEvent:
    """Événement numéroté, rejouable après une reconnexion SSE."""

    id: int
    payload: dict


@dataclass
class Job:
    id: str
    status: str = "pending"  # pending | running | done | error
    progress: float = 0.0
    message: str = ""  # texte anglais — repli lisible sans interface
    # Code stable et paramètres du dernier message. Portés par le job lui-même
    # et pas seulement par le flux : un client qui se reconnecte lit
    # `snapshot()`, et sans eux il retomberait sur l'anglais — précisément le
    # défaut qu'on corrige, déplacé à la reconnexion.
    message_code: Optional[str] = None
    message_params: dict[str, Any] = field(default_factory=dict)
    events: list[JobEvent] = field(default_factory=list)
    event_sequence: int = 0
    result: Optional[dict] = None
    error: Optional[str] = None
    # Compte qui a lancé le job (`auth.Utilisateur.id`) : un job n'est lisible
    # que par lui.
    owner: Optional[str] = None
    # Nature du traitement, lue par `stop.sh` pour le nommer : transcription,
    # faits, voix, archives.
    nature: str = "traitement"
    depuis: str = field(default_factory=lambda: datetime.now().strftime("%H:%M"))
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class JobsBusy(RuntimeError):
    """Trop de jobs actifs : refuser plutôt que saturer la machine."""


# Une écriture du relevé au plus par intervalle pour une simple progression :
# la transcription rappelle sa progression à chaque segment.
RELEVE_INTERVALLE_S = 2.0


class JobManager:
    """Jobs en mémoire, **bornés** : sans plafond, plusieurs transcriptions
    simultanées (STT + diarisation + LLM) saturent la RAM unifiée ; et sans
    éviction, l'historique et ses événements grossissent indéfiniment.

    `releve` : fichier où l'API recopie ses traitements en cours, pour que
    `stop.sh` refuse de les couper. Les jobs ne vivent qu'en mémoire : sans ce
    relevé, un arrêt perdait une transcription d'une heure sans un mot. Le
    fichier n'existe que tant qu'un traitement tourne.
    """

    def __init__(
        self,
        max_active: Optional[int] = None,
        max_history: Optional[int] = None,
        releve: Optional[Path] = None,
    ) -> None:
        from core.config import MAX_ACTIVE_JOBS, MAX_JOB_HISTORY

        self._jobs: "OrderedDict[str, Job]" = OrderedDict()
        self._max_active = MAX_ACTIVE_JOBS if max_active is None else max_active
        self._max_history = MAX_JOB_HISTORY if max_history is None else max_history
        self._lock = threading.Lock()
        self._releve = releve
        self._releve_lock = threading.Lock()
        self._releve_instant = 0.0

    def _ecrire_releve(self, force: bool = True) -> None:
        """Recopie les traitements actifs dans `releve`, ou le retire s'il n'y en a plus.

        Lisible par le seul propriétaire, comme le code d'installation ; écrit
        par remplacement atomique, pour que `stop.sh` ne lise jamais un
        fichier à moitié écrit. Un échec d'écriture n'interrompt jamais un
        traitement : le relevé ne fait que prévenir.
        """
        if self._releve is None:
            return
        with self._releve_lock:
            maintenant = time.monotonic()
            if not force and maintenant - self._releve_instant < RELEVE_INTERVALLE_S:
                return
            self._releve_instant = maintenant
            actifs = [j for j in list(self._jobs.values()) if j.status in ("pending", "running")]
            try:
                if not actifs:
                    self._releve.unlink(missing_ok=True)
                    return
                contenu = {"pid": os.getpid(), "traitements": [
                    {"id": j.id, "nature": j.nature, "statut": j.status,
                     "progression": round(j.progress, 3), "depuis": j.depuis}
                    for j in actifs]}
                provisoire = self._releve.with_name(self._releve.name + ".tmp")
                descripteur = os.open(provisoire, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(descripteur, "w", encoding="utf-8") as f:
                    json.dump(contenu, f, ensure_ascii=False)
                os.chmod(provisoire, 0o600)
                os.replace(provisoire, self._releve)
            except OSError as exc:
                print(f"[jobs] relevé des traitements non écrit : {exc}")

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def discard(self, job: Job) -> None:
        """Abandonne un job jamais démarré (upload refusé, validation échouée).

        Sans ça il resterait « pending » à vie et occuperait un slot : deux
        uploads rejetés suffisaient à bloquer toutes les transcriptions.
        """
        with self._lock:
            if job.status == "pending":
                self._jobs.pop(job.id, None)
        self._ecrire_releve()

    def active_count(self) -> int:
        return sum(1 for j in self._jobs.values() if j.status in ("pending", "running"))

    def _evict(self) -> None:
        """Oublie les jobs terminés les plus anciens au-delà du plafond."""
        finished = [jid for jid, j in self._jobs.items() if j.status in ("done", "error")]
        excess = len(self._jobs) - self._max_history
        for jid in finished[:max(0, excess)]:
            self._jobs.pop(jid, None)

    def create(self, owner: Optional[str] = None, nature: str = "traitement") -> Job:
        with self._lock:
            if self.active_count() >= self._max_active:
                raise JobsBusy(
                    f"{self._max_active} traitement(s) déjà en cours — réessaie "
                    "quand l'un d'eux sera terminé."
                )
            job = Job(id=uuid.uuid4().hex[:12], owner=owner, nature=nature)
            self._jobs[job.id] = job
            self._evict()
        self._ecrire_releve()
        return job

    def push(self, job: Job, event: dict) -> None:
        """Émet un événement libre vers le flux SSE (ex. texte partiel)."""
        with job.lock:
            self._append_event_locked(job, event)

    @staticmethod
    def _append_event_locked(job: Job, payload: dict) -> None:
        job.event_sequence += 1
        job.events.append(JobEvent(job.event_sequence, dict(payload)))

    def events_after(self, job: Job, event_id: int) -> tuple[list[JobEvent], str]:
        """Snapshot atomique des événements postérieurs au curseur et du statut."""
        with job.lock:
            return [event for event in job.events if event.id > event_id], job.status

    @staticmethod
    def snapshot(job: Job) -> dict[str, Any]:
        """État cohérent utilisé pour récupérer un résultat hors du flux SSE."""
        with job.lock:
            return {
                "id": job.id,
                "status": job.status,
                "progress": job.progress,
                "message": job.message,
                "message_code": job.message_code,
                "message_params": job.message_params,
                "result": job.result,
                "error": job.error,
            }

    def run(self, job: Job, target: Callable[[Callable], dict]) -> None:
        """Lance `target(progress_cb)` dans un thread. `target` retourne le résultat."""

        def _cb(frac: float, msg: str) -> None:
            # `msg` porte un code quand il vient de `core.messages` ; sinon c'est
            # une chaîne nue (message d'exception, texte d'une dépendance) et
            # elle passe sans code. On construit l'événement champ par champ :
            # sérialiser l'objet directement rendrait la chaîne et PERDRAIT le
            # code, puisqu'un `Message` est un `str`.
            details = messages.evenement_message(msg)
            with job.lock:
                job.progress = frac
                job.message = details["message"]
                job.message_code = details["code"]
                job.message_params = details["params"]
                self._append_event_locked(
                    job,
                    {"type": "progress", "progress": frac, **details},
                )
            self._ecrire_releve(force=False)

        def _worker() -> None:
            with job.lock:
                job.status = "running"
            self._ecrire_releve()
            try:
                result = target(_cb)
                with job.lock:
                    job.status = "done"
                    job.result = result
                    self._append_event_locked(job, {"type": "done", "result": result})
            except Exception as exc:  # noqa: BLE001
                with job.lock:
                    job.status = "error"
                    job.error = str(exc)
                    self._append_event_locked(job, {"type": "error", "error": str(exc)})
                # L'erreur n'existait qu'à l'écran : une fois la page fermée,
                # le journal ne gardait aucune trace de l'échec. Écrite après
                # la mise à jour du job, pour ne pas retarder ce que voit le client.
                print(f"[job {job.id}] échec : {type(exc).__name__}: {exc}\n"
                      f"{traceback.format_exc()}", end="")
            finally:
                self._ecrire_releve()

        threading.Thread(target=_worker, daemon=True).start()
