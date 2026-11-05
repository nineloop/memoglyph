"""Puits de télémétrie d'usage IA — contexte + capture, provider-agnostique.

Généralise le précédent `llm_openai.USAGE` (liste-globale d'usage réel) en un puits
partagé, sûr en concurrence (`contextvars`), sans coupler la logique de pipeline à un
fournisseur.

## Comment ça s'assemble

- Un orchestrateur ouvre un CONTEXTE d'exécution :  `with telemetry.execution(kind=…):`
- Il balise les étapes :                            `with telemetry.stage("extract"):`
- Chaque MOTEUR, après un appel réseau/local réussi, ENREGISTRE l'appel :
      `telemetry.record_model_call(provider=…, model=…, latency_s=…, usage=<obj>, …)`
  — l'appel s'attache à l'exécution/étape courante via les `contextvars`. Le moteur ne
  connaît RIEN de la persistance ; son contrat de retour est inchangé.
- À la fermeture du contexte, le tampon est écrit dans `usage.db` en UN flush best-effort.

## Contrat de fiabilité (DUR)

Rien ici ne doit altérer la génération. `record_model_call`, `stage` et le flush de
`execution` sont enveloppés pour ne JAMAIS lever dans un moteur ou un pipeline. Le CORPS
d'un `with execution(...)` (la génération elle-même) propage ses exceptions NORMALEMENT :
on marque l'exécution `failed` et on relève — on n'avale que la télémétrie, jamais le métier.

Hors contexte d'exécution, `record_model_call` est un no-op : l'instrumentation n'atteint que
les seams report/STT explicitement ouverts, et un appel isolé (mesure, CLI expérimental) ne
persiste rien.
"""
from __future__ import annotations

import contextvars
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from . import docid

# --- État de contexte (isolé par thread/tâche via contextvars) ---------------
_current_execution: contextvars.ContextVar[Optional["_ExecBuffer"]] = contextvars.ContextVar(
    "memoglyph_execution", default=None
)
_current_stage: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "memoglyph_stage", default=None
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class _ExecBuffer:
    """Tampon EN MÉMOIRE d'une exécution et de ses appels, écrit en un flush à la fin."""

    __slots__ = (
        "execution_id", "kind", "document_id", "meeting_id", "project_id",
        "produced_artifact_id", "provider", "model", "started_at", "ended_at",
        "wall_clock_s", "status", "audio_seconds", "envoi_ultra_autorise", "_t0", "_calls",
        "_envois_notes", "modeles_servis",
    )

    def __init__(self, kind: str, *, document_id=None, meeting_id=None,
                 project_id=None, provider=None, model=None, audio_seconds=None) -> None:
        self.execution_id = docid.nouveau()
        self.kind = kind
        self.document_id = document_id
        self.meeting_id = meeting_id
        self.project_id = project_id
        self.produced_artifact_id = None
        self.provider = provider
        self.model = model
        self.audio_seconds = audio_seconds
        self.started_at = _now_iso()
        self.ended_at: Optional[str] = None
        self.wall_clock_s: Optional[float] = None
        self.status: Optional[str] = None
        # Un document ultra-confidentiel est parti vers le cloud sous l'autorisation de son
        # propriétaire (`sensitivity.tracer`). Faux par défaut : aucune autre voie ne le pose.
        self.envoi_ultra_autorise = False
        self._t0 = time.perf_counter()
        self._calls: list[dict] = []
        self._envois_notes: set = set()
        # Les modèles qui ont RÉELLEMENT répondu, distincts, dans l'ordre d'apparition (un routeur peut en choisir un par appel).
        self.modeles_servis: list[str] = []

    def add_call(self, call: dict) -> None:
        call["seq"] = len(self._calls) + 1
        call["call_id"] = docid.nouveau()
        call["execution_id"] = self.execution_id
        call["created_at"] = _now_iso()
        servi = call.get("model_servi")
        if isinstance(servi, str) and servi and servi not in self.modeles_servis:
            self.modeles_servis.append(servi)
        self._calls.append(call)

    def close(self, status: str) -> None:
        self.ended_at = _now_iso()
        self.wall_clock_s = round(time.perf_counter() - self._t0, 4)
        self.status = status

    # --- Projections pour la persistance (aucune I/O ici) --------------------
    def execution_row(self) -> dict:
        measured = [c for c in self._calls if c.get("in_tok") is not None or c.get("out_tok") is not None]
        in_sum = sum(c["in_tok"] for c in measured if c.get("in_tok") is not None) if measured else None
        out_sum = sum(c["out_tok"] for c in measured if c.get("out_tok") is not None) if measured else None
        return {
            "execution_id": self.execution_id,
            "kind": self.kind,
            "document_id": self.document_id,
            "meeting_id": self.meeting_id,
            "project_id": self.project_id,
            "produced_artifact_id": self.produced_artifact_id,
            "provider_primary": self.provider,
            "model_primary": self.model,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "wall_clock_s": self.wall_clock_s,
            "status": self.status or "ok",
            "n_calls": len(self._calls),
            "in_tok": in_sum,
            "out_tok": out_sum,
            "audio_seconds": self.audio_seconds,
            "envoi_ultra_autorise": int(bool(self.envoi_ultra_autorise)),
            "created_at": self.started_at,
        }

    def call_rows(self) -> list[dict]:
        return list(self._calls)


# --- Lecture d'usage TOLÉRANTE (jamais d'invention de sémantique) -------------
def _attr(obj: Any, name: str) -> Any:
    try:
        if isinstance(obj, dict):
            return obj.get(name)
        return getattr(obj, name, None)
    except Exception:  # noqa: BLE001
        return None


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except Exception:  # noqa: BLE001
        return None


def _read_usage(usage: Any) -> tuple[Optional[int], Optional[int], Optional[int], Optional[int]]:
    """(in, out, cached, reasoning) depuis un objet OU un dict d'usage, défensivement.

    Lit les DEUX conventions réellement émises par les fournisseurs sans en inventer :
    Responses OpenAI → `input_tokens`/`output_tokens` (+ *_details) ;
    Chat Completions (Mistral, mlx_lm) → `prompt_tokens`/`completion_tokens`.
    Un champ absent reste `None` : on ne fabrique JAMAIS un compte.
    """
    if usage is None:
        return (None, None, None, None)
    try:
        in_tok = _as_int(_attr(usage, "input_tokens"))
        if in_tok is None:
            in_tok = _as_int(_attr(usage, "prompt_tokens"))
        out_tok = _as_int(_attr(usage, "output_tokens"))
        if out_tok is None:
            out_tok = _as_int(_attr(usage, "completion_tokens"))
        cached = None
        idet = _attr(usage, "input_tokens_details") or _attr(usage, "prompt_tokens_details")
        if idet is not None:
            cached = _as_int(_attr(idet, "cached_tokens"))
        reasoning = None
        odet = _attr(usage, "output_tokens_details") or _attr(usage, "completion_tokens_details")
        if odet is not None:
            reasoning = _as_int(_attr(odet, "reasoning_tokens"))
        return (in_tok, out_tok, cached, reasoning)
    except Exception:  # noqa: BLE001
        return (None, None, None, None)


# --- API publique -------------------------------------------------------------
def record_model_call(
    *,
    provider: Optional[str],
    model: Optional[str],
    latency_s: Optional[float] = None,
    status: str = "ok",
    usage: Any = None,
    usage_source: Optional[str] = None,
    stage: Optional[str] = None,
    in_tok: Optional[int] = None,
    out_tok: Optional[int] = None,
    cached_tok: Optional[int] = None,
    reasoning_tok: Optional[int] = None,
    served_model: Optional[str] = None,
    error_code: Optional[str] = None,
) -> None:
    """Enregistre UN appel fournisseur/modèle sur l'exécution courante. Ne lève JAMAIS.

    No-op hors d'un `with execution(...)`. Une reprise = un nouvel appel = un nouvel
    enregistrement (le moteur appelle cette fonction une fois par requête réseau/locale).
    `usage` (objet ou dict) est décomposé tolérant ; des comptes explicites l'emportent.
    """
    try:
        buf = _current_execution.get()
        if buf is None:
            return  # dark : rien à quoi rattacher
        u_in, u_out, u_cached, u_reason = _read_usage(usage)
        f_in = in_tok if in_tok is not None else u_in
        f_out = out_tok if out_tok is not None else u_out
        f_cached = cached_tok if cached_tok is not None else u_cached
        f_reason = reasoning_tok if reasoning_tok is not None else u_reason
        # `usage_source` ne vaut que si AU MOINS un compte a réellement été mesuré.
        # Une réponse dont l'`usage` n'expose pas de jetons (ex. STT en secondes
        # audio) ne doit pas se faire passer pour « provider_reported » à vide :
        # on ne prétend pas mesurer ce qu'on n'a pas lu.
        if f_in is None and f_out is None and f_cached is None and f_reason is None:
            usage_source = None
        buf.add_call({
            "stage": stage if stage is not None else _current_stage.get(),
            "provider": provider,
            "model": model,
            "model_servi": served_model,      # celui qui a répondu ; `model` reste le DEMANDÉ (le barème s'apparie dessus)
            "started_at": _now_iso(),
            "latency_s": round(latency_s, 4) if isinstance(latency_s, (int, float)) else None,
            "status": status,
            "error_code": error_code,         # le CODE seul d'un appel refusé ou inutilisable (429, 404, timeout, length…)
            "usage_source": usage_source,
            "in_tok": f_in,
            "out_tok": f_out,
            "cached_tok": f_cached,
            "reasoning_tok": f_reason,
        })
    except Exception:  # noqa: BLE001 — un moteur ne doit jamais échouer sur la télémétrie
        return


def set_produced_artifact(artifact_id: Optional[str]) -> None:
    """Rattache l'artefact produit (nom du CR…) à l'exécution courante. Best-effort."""
    try:
        buf = _current_execution.get()
        if buf is not None:
            buf.produced_artifact_id = artifact_id
    except Exception:  # noqa: BLE001
        return


def noter_envoi_ultra_autorise(cle: object) -> bool:
    """Marque l'exécution courante : un ultra-confidentiel est parti avec autorisation.

    Rend vrai la première fois que `cle` est vue dans cette exécution, et toujours
    hors exécution : c'est ce qui décide d'une ligne de journal. Ne lève jamais ;
    en cas de doute, vrai (mieux vaut une ligne de trop qu'une de moins).
    """
    try:
        buf = _current_execution.get()
        if buf is None:
            return True
        buf.envoi_ultra_autorise = True
        if cle in buf._envois_notes:
            return False
        buf._envois_notes.add(cle)
        return True
    except Exception:  # noqa: BLE001
        return True


def current_execution_id() -> Optional[str]:
    try:
        buf = _current_execution.get()
        return buf.execution_id if buf is not None else None
    except Exception:  # noqa: BLE001
        return None


@contextmanager
def stage(name: Optional[str]):
    """Balise l'étape courante (dimension `stage` des appels). Best-effort, jamais bloquant."""
    token = None
    try:
        token = _current_stage.set(name)
    except Exception:  # noqa: BLE001
        token = None
    try:
        yield
    finally:
        if token is not None:
            try:
                _current_stage.reset(token)
            except Exception:  # noqa: BLE001
                pass


@contextmanager
def execution(
    kind: str,
    *,
    document_id: Optional[str] = None,
    meeting_id: Optional[str] = None,
    project_id: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    audio_seconds: Optional[float] = None,
):
    """Ouvre une exécution ; à la sortie, flush best-effort vers `usage.db`.

    Le CORPS propage ses exceptions NORMALEMENT (la génération n'est jamais avalée) ;
    on marque seulement l'exécution `failed` puis on relève. Si l'ouverture même du
    contexte échoue, on exécute le corps SANS télémétrie plutôt que de le casser.
    """
    buf = None
    token = None
    try:
        buf = _ExecBuffer(kind, document_id=document_id, meeting_id=meeting_id,
                          project_id=project_id, provider=provider, model=model,
                          audio_seconds=audio_seconds)
        token = _current_execution.set(buf)
    except Exception:  # noqa: BLE001 — jamais bloquer la génération sur l'init télémétrie
        buf, token = None, None

    failed = False
    try:
        yield buf
    except BaseException:
        failed = True
        raise
    finally:
        # Bloc entièrement gardé : un échec de flush ne masque pas l'exception métier
        # en cours et ne lève pas de son côté.
        try:
            if token is not None:
                _current_execution.reset(token)
            if buf is not None:
                exc_active = sys.exc_info()[0] is not None
                buf.close("failed" if (failed or exc_active) else "ok")
                from . import usage_store  # import paresseux : pas de coût à l'import moteur
                usage_store.persist_execution(buf)
        except Exception:  # noqa: BLE001
            pass
