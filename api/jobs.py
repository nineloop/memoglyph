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
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from core import config, erreurs, etapes, messages, resources


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
    # ---- Suivi : de quoi montrer un traitement depuis n'importe quel écran.
    titre: str = ""
    projet_id: Optional[str] = None
    projet_nom: str = ""
    # « étape n sur N » : déduite des CODES de message, selon le PLAN du traitement (ses options).
    plan: list[str] = field(default_factory=list)
    etape: Optional[int] = None
    etapes: Optional[int] = None
    # Vrai tant que le créneau de calcul lourd n'est pas obtenu (le traitement n'a pas commencé).
    en_attente: bool = False
    # Vrai dès que l'annulation est décidée (sous `lock`) : le fil qui obtient plus tard le créneau ne touche à rien.
    annule: bool = False
    # Instant de création, en secondes depuis l'époque (`depuis` reste le « HH:MM » du relevé de `stop.sh`).
    debut: float = field(default_factory=time.time)
    # Identité de la réunion produite (`name`, `meeting_id`, `project_id`), sans le texte.
    meeting: Optional[dict] = None
    # Identifiant PUBLIC du jeton d'appareil qui a lancé le traitement (jamais le secret).
    jeton: Optional[str] = None
    # Identifiant d'idempotence d'une capture.
    capture_id: Optional[str] = None
    # SHA-256 du contenu reçu : sert au refus d'un doublon, JAMAIS exposé (ni liste, ni `snapshot`).
    empreinte: Optional[str] = None
    # Code et paramètres de l'erreur, comme pour les messages : le texte seul perdrait la traduction.
    error_code: Optional[str] = None
    error_params: dict[str, Any] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


GROUPES_COMPTE_RENDU = ["extraction", "organisation", "redaction"]


def plan_etapes(*, diarize: bool, profile: bool, report_on: bool) -> list[str]:
    """Les étapes d'un traitement de transcription, dans l'ordre, selon ses options."""
    plan = ["transcription"]
    if diarize:
        plan.append("locuteurs")
    if profile:
        plan.append("profil")
    if report_on:
        plan.extend(GROUPES_COMPTE_RENDU)
    plan.append("ecriture")
    return plan


class JobsBusy(RuntimeError):
    """Trop de jobs actifs : refuser plutôt que saturer la machine."""


class TraitementAnnule(Exception):
    """Levée quand un traitement annulé obtient enfin le créneau : `resources.heavy` le rend aussitôt."""


@contextmanager
def creneau_du_traitement(job: "Job", label: str):
    """Le créneau de calcul lourd d'un traitement, avec une attente qui se VOIT et qui peut être ANNULÉE.

    Un seul assistant pour la transcription et la régénération de compte rendu. Le choix « annulé ou obtenu » se
    fait SOUS `job.lock`, au même endroit que dans `JobManager.annuler` : une annulation qui précède l'obtention
    l'emporte (le fil rend le créneau sans rien exécuter), une obtention qui précède l'annulation la refuse.
    Le délai est lu ICI, à chaque appel (jamais en valeur par défaut, figée à la définition).
    """
    def _en_attente() -> None:
        with job.lock:
            if not job.annule:
                job.en_attente = True

    def _obtenu() -> None:
        with job.lock:
            if job.annule:
                raise TraitementAnnule()
            job.en_attente = False

    with resources.heavy(wait=config.ATTENTE_DE_FOND_API_S, label=label,
                         on_wait=_en_attente, on_acquired=_obtenu):
        yield


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
        max_total: Optional[int] = None,
    ) -> None:
        from core.config import MAX_ACTIVE_JOBS, MAX_JOB_HISTORY, MAX_JOB_HISTORY_TOTAL

        self._jobs: "OrderedDict[str, Job]" = OrderedDict()
        self._max_active = MAX_ACTIVE_JOBS if max_active is None else max_active
        # `max_history` : traitements TERMINÉS gardés PAR COMPTE ; `max_total` : au total.
        self._max_history = MAX_JOB_HISTORY if max_history is None else max_history
        self._max_total = MAX_JOB_HISTORY_TOTAL if max_total is None else max_total
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

    def annuler(self, job: Job) -> str:
        """Annule un traitement EN ATTENTE de place. Rend `"annule"` (fait, ou déjà fait) ou l'état actuel (impossible).

        Annuler = poser l'état final « échec, cause codée annulé » (aucun nouvel état : le compagnon lit tout autre
        état que « terminé » ou « en échec » comme « en cours »), pousser l'événement final et sortir du relevé de
        `stop.sh`. La décision se prend sous `job.lock`, comme l'obtention du créneau (`creneau_du_traitement`) : un
        traitement qui a obtenu sa place n'est jamais annulé, un traitement annulé n'exécute jamais rien.
        """
        with job.lock:
            if job.annule:
                return "annule"
            if not (job.status == "running" and job.en_attente):
                return job.status
            details = messages.traitement_annule()
            job.annule, job.en_attente, job.status = True, False, "error"
            job.error = str(details)
            job.error_code, job.error_params = details.code, dict(details.params)
            self._append_event_locked(job, {"type": "error", "error": job.error,
                                            "code": job.error_code, "params": job.error_params})
        self._ecrire_releve()
        return "annule"

    def active_count(self) -> int:
        return sum(1 for j in self._jobs.values() if j.status in ("pending", "running"))

    def _evict(self) -> None:
        """Oublie les traitements TERMINÉS les plus anciens : d'abord au-delà du plafond de chaque
        compte, puis au-delà du plafond total. Un traitement actif n'est jamais oublié, et un compte
        très actif n'efface pas l'historique d'un autre."""
        par_compte: dict[Optional[str], list[str]] = {}
        for jid, j in self._jobs.items():
            if j.status in ("done", "error"):
                par_compte.setdefault(j.owner, []).append(jid)
        for ids in par_compte.values():
            for jid in ids[:max(0, len(ids) - self._max_history)]:
                self._jobs.pop(jid, None)
        finis = [jid for jid, j in self._jobs.items() if j.status in ("done", "error")]
        for jid in finis[:max(0, len(finis) - self._max_total)]:
            self._jobs.pop(jid, None)

    def _creer_sous_verrou(self, owner: Optional[str], nature: str, **suivi: Any) -> Job:
        if self.active_count() >= self._max_active:
            raise JobsBusy(
                f"{self._max_active} traitement(s) déjà en cours — réessaie "
                "quand l'un d'eux sera terminé."
            )
        plan = list(suivi.pop("plan", None) or [])
        job = Job(id=uuid.uuid4().hex[:12], owner=owner, nature=nature, plan=plan, **suivi)
        job.etapes = len(plan) or None          # le total est connu dès la création
        self._jobs[job.id] = job
        self._evict()
        return job

    def create(self, owner: Optional[str] = None, nature: str = "traitement", **suivi: Any) -> Job:
        """Crée un traitement. `suivi` : titre, projet_id, projet_nom, jeton, capture_id, plan."""
        with self._lock:
            job = self._creer_sous_verrou(owner, nature, **suivi)
        self._ecrire_releve()
        return job

    def create_or_find_capture(self, owner: str, capture_id: str, nature: str = "transcription",
                               **suivi: Any) -> tuple[Job, bool]:
        """Le traitement du compte portant ce `capture_id`, ou un nouveau : `(job, créé)`.

        La recherche et la création se font sous le MÊME verrou : deux envois rapprochés de la même
        capture ne passent pas tous deux. Un traitement en échec ne compte pas : le renvoyer crée un
        nouveau traitement. Un autre compte avec le même identifiant n'est jamais « déjà reçu ».
        """
        with self._lock:
            for job in self._jobs.values():
                if (job.owner == owner and job.capture_id == capture_id
                        and job.status in ("pending", "running", "done")):
                    return job, False
            job = self._creer_sous_verrou(owner, nature, capture_id=capture_id, **suivi)
        self._ecrire_releve()
        return job, True

    def enregistrer_envoi(self, job: Job, empreinte: str) -> Optional[Job]:
        """Reconnaît un doublon ET enregistre l'empreinte, d'un seul geste (sous le verrou du gestionnaire).

        Rend le traitement EXISTANT du même compte, de même contenu et non final (`pending` ou `running`),
        auquel cas rien n'est enregistré ; sinon enregistre `empreinte` sur `job` et rend `None`. Deux envois
        identiques qui arrivent ensemble : un seul passe, parce que la recherche et l'enregistrement ne
        se séparent pas. Un traitement terminé ou en échec, un autre compte ne comptent jamais.
        """
        with self._lock:
            for autre in self._jobs.values():
                if (autre is not job and autre.owner == job.owner and autre.empreinte == empreinte
                        and autre.status in ("pending", "running")):
                    return autre
            job.empreinte = empreinte
            return None

    def list_for(self, owner: Optional[str]) -> list[Job]:
        """Les traitements d'UN compte, du plus récent au plus ancien."""
        with self._lock:
            return [j for j in reversed(list(self._jobs.values())) if j.owner == owner]

    @staticmethod
    def entree_liste(job: Job) -> dict[str, Any]:
        """Un traitement tel que `GET /api/jobs` le décrit : léger, SANS le texte, le jeton ni les événements.

        `snapshot()` porte `result` (le transcript entier) : une liste de cinquante traitements relevée
        toutes les trois secondes en ferait des mégaoctets. Ici, seule l'identité de la réunion produite.
        """
        with job.lock:
            meeting = None
            if job.status == "done" and isinstance(job.result, dict):
                meeting = {k: job.result[k] for k in ("name", "meeting_id", "project_id")
                           if k in job.result} or None
            return {
                "id": job.id, "nature": job.nature, "statut": job.status,
                "en_attente": job.en_attente or job.status == "pending",
                "progression": round(job.progress, 3),
                "titre": job.titre, "projet_id": job.projet_id, "projet_nom": job.projet_nom,
                "etape": job.etape, "etapes": job.etapes,
                "message_code": job.message_code, "message_params": job.message_params,
                "debut": job.debut, "meeting": meeting,
                "erreur": job.error, "erreur_code": job.error_code, "erreur_params": job.error_params,
            }

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
                "error_code": job.error_code,
                "error_params": job.error_params,
                # Suivi : les clés d'avant restent, celles-ci s'y ajoutent.
                "titre": job.titre,
                "projet_id": job.projet_id,
                "projet_nom": job.projet_nom,
                "etape": job.etape,
                "etapes": job.etapes,
                "en_attente": job.en_attente,
                "debut": job.debut,
            }

    @staticmethod
    def _avancer_etape(job: Job, code: Optional[str]) -> None:
        """Place `etape`/`etapes` d'après le groupe du code, dans le plan du traitement (sous le verrou du job)."""
        if not job.plan:
            return
        job.etapes = len(job.plan)
        groupe = etapes.groupe_etape(code)
        if groupe in job.plan:
            job.etape = job.plan.index(groupe) + 1

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
                self._avancer_etape(job, details["code"])
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
                    job.en_attente = False
                    job.result = result
                    self._append_event_locked(job, {"type": "done", "result": result})
            except Exception as exc:  # noqa: BLE001
                with job.lock:
                    if job.annule:
                        # Annulé en attente : l'état final est déjà posé. Ni « trop d'attente » ni
                        # `TraitementAnnule` ne l'écrasent, et il n'y a pas d'échec à journaliser.
                        return
                    job.status = "error"
                    job.en_attente = False
                    job.error = str(exc)
                    # Le code d'une erreur qui en porte un (« trop d'attente ») : `str(exc)` seul le perdrait.
                    cause = exc.args[0] if exc.args else None
                    if isinstance(cause, messages.Message):
                        job.error_code, job.error_params = cause.code, dict(cause.params)
                    else:
                        # Sans message codé, le code de la CAUSE : l'interface ne montre jamais le texte brut.
                        job.error_code, job.error_params = erreurs.code_de(exc)
                    self._append_event_locked(job, {"type": "error", "error": str(exc),
                                                    "code": job.error_code, "params": job.error_params})
                # L'erreur n'existait qu'à l'écran : une fois la page fermée,
                # le journal ne gardait aucune trace de l'échec. Écrite après
                # la mise à jour du job, pour ne pas retarder ce que voit le client.
                print(f"[job {job.id}] échec : {type(exc).__name__}: {exc}\n"
                      f"{traceback.format_exc()}", end="")
            finally:
                self._ecrire_releve()

        threading.Thread(target=_worker, daemon=True).start()
