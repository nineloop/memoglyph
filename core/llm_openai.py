"""Moteur cloud OpenAI — implémentation de `LLMEngine` (3e fournisseur de CR).

Cloud, comme Mistral : `is_available` ne fait AUCUN aller-retour réseau (présence
de clé), la génération passe par l'API officielle. On vise l'API **Responses**
(`client.responses.create`) : `gpt-5.6-*` sont des modèles de RAISONNEMENT, et
Responses les porte proprement (jetons de raisonnement, `max_output_tokens`,
`text.format`).

Contrat JSON : OpenAI garantit un JSON valide via `text.format` — `json_schema`
(strict) quand un schéma est fourni, sinon `json_object`. Cela SUPPRIME les
échecs de parse JSON intermittents (le défaut mesuré de mistral-small). ATTENTION :
Structured Outputs garantit la FORME, pas la vérité — les gardes de contenance et
de provenance restent OBLIGATOIRES en aval, inchangés.

Choix défensifs (modèle de raisonnement, API non rejouable localement) :
- pas de `temperature` ni de `seed` transmis : les modèles de raisonnement fixent
  la température et n'exposent pas de graine fiable ; les transmettre risquerait un
  400. La reproductibilité cloud est un EFFORT (comme `random_seed` chez Mistral),
  pas une garantie — on ne prétend donc pas la porter.
- `retries` conservé : un service distant renvoie par intermittence une sortie
  tronquée ; le filet de réparation de `core/reparation_json` n'agit qu'après.
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from . import telemetry
from .config import OPENAI_API_KEY, OPENAI_CHAT_MODEL
from .llm_base import LLMError, retirer_enveloppe_markdown


class OpenAILLMError(LLMError):
    """Panne côté moteur OpenAI (clé absente, API en erreur, sortie inexploitable)."""


def _cle(api_key: Optional[str]) -> str:
    # Lu à l'APPEL, avec repli sur `os.getenv` LIVE : la constante `config` est
    # évaluée à l'import, donc périmée si le module a été chargé AVANT `load_env`
    # (une clé posée dans `.env` sans redémarrage doit se voir — même contrat que
    # `llm_mistral.is_available`).
    import os
    from . import config
    if api_key is not None:
        return api_key.strip()
    return (config.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY") or OPENAI_API_KEY or "").strip()


def is_available(api_key: Optional[str] = None) -> bool:
    # Présence de clé UNIQUEMENT — aucun appel réseau (contrat : route chargée à
    # chaque ouverture d'UI). `pret_declare` en dépend ; la validité réelle est
    # prouvée à la première génération.
    return bool(_cle(api_key))


def _client(api_key: Optional[str] = None):
    import openai  # SDK déjà présent (utilisé aussi par le moteur MLX)
    key = _cle(api_key)
    if not key:
        raise OpenAILLMError("OPENAI_API_KEY manquante. Renseignez la clé API.")
    return openai.OpenAI(api_key=key)


# Télémétrie d'usage RÉEL (opt-in). Vide par défaut ; l'API Responses expose
# `usage.input_tokens/output_tokens`, un vrai décompte (vs l'estimation `len/3.6`
# de la façade). Un appelant qui veut mesurer lit puis vide cette liste.
# N'affecte AUCUN contrat — pur enregistrement.
USAGE: list[dict[str, int]] = []


def _generer(system: str, user: str, *, model: Optional[str], api_key: Optional[str],
             max_tokens: Optional[int], text_format: Optional[dict]) -> tuple[str, Optional[str]]:
    """(texte, statut d'arrêt) via l'API Responses. `text_format` porte le mode JSON."""
    client = _client(api_key)
    target = model or OPENAI_CHAT_MODEL or "gpt-5.6-luna"
    kwargs: dict[str, Any] = {
        "model": target,
        "input": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if max_tokens is not None:
        kwargs["max_output_tokens"] = max_tokens
    if text_format is not None:
        kwargs["text"] = {"format": text_format}
    _t0 = time.perf_counter()
    resp = client.responses.create(**kwargs)
    texte = (getattr(resp, "output_text", "") or "").strip()
    statut = getattr(resp, "status", None)  # "completed" | "incomplete" | …
    usage = getattr(resp, "usage", None)
    if usage is not None:
        USAGE.append({"in": int(getattr(usage, "input_tokens", 0) or 0),
                      "out": int(getattr(usage, "output_tokens", 0) or 0)})
    # Télémétrie FinOps (best-effort, hors contrat) : un appel facturé = un
    # enregistrement. `USAGE` ci-dessus est CONSERVÉ (les appelants qui mesurent en dépendent).
    telemetry.record_model_call(
        provider="openai", model=target,
        latency_s=time.perf_counter() - _t0,
        status="truncated" if (statut and statut != "completed") else "ok",
        usage=usage,
        usage_source="provider_reported" if usage is not None else None,
    )
    return texte, statut


def modele_servi(model: Optional[str] = None) -> str:
    # MÊME expression que la génération : un seul choix, jamais reformulé. Repli
    # obligatoire pour ne pas écrire un champ `model` vide dans un document autoritatif.
    return model or OPENAI_CHAT_MODEL or "gpt-5.6-luna"


def chat(system: str, user: str, *, model: Optional[str] = None,
         api_key: Optional[str] = None, seed: Optional[int] = None) -> str:
    """Texte seul. `seed` accepté par le contrat mais NON transmis (voir module)."""
    return _generer(system, user, model=model, api_key=api_key,
                    max_tokens=None, text_format=None)[0]


def chat_json(system: str, user: str, *, model: Optional[str] = None,
              api_key: Optional[str] = None, max_tokens: Optional[int] = None,
              schema: Optional[dict] = None, retries: int = 2,
              temperature: float = 0.1, seed: Optional[int] = None) -> dict[str, Any]:
    """Réponse JSON parsée. JSON garanti par `text.format` (Structured Outputs).

    `schema` fourni → `json_schema` strict (le décodage EST contraint, contrairement
    à Mistral où le schéma n'est qu'une directive). Sinon → `json_object`. `retries`
    conservé (sortie tronquée intermittente). `temperature`/`seed` non transmis.
    """
    if schema is not None:
        text_format = {"type": "json_schema", "name": "memoglyph",
                       "strict": True, "schema": schema}
    else:
        text_format = {"type": "json_object"}
        # `json_object` exige le mot « json » dans l'invite (règle de l'API).
        system = system + "\n\nCRUCIAL: réponds EXCLUSIVEMENT en JSON valide."

    dernier = ""
    for _ in range(retries + 1):
        raw, _statut = _generer(system, user, model=model, api_key=api_key,
                                 max_tokens=max_tokens, text_format=text_format)
        cleaned = retirer_enveloppe_markdown(raw)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            dernier = cleaned
            continue
        if isinstance(data, dict):
            return data
        raise OpenAILLMError(f"Le JSON renvoyé n'est pas une table : {type(data).__name__}")

    erreur = OpenAILLMError("Format JSON invalide de l'API OpenAI.")
    erreur.brut = dernier
    raise erreur


class OpenAIEngine:
    """Moteur cloud OpenAI — `LLMEngine`. Pas d'`unload` (service distant)."""

    name = "openai"

    def is_available(self, *, api_key: Optional[str] = None) -> bool:
        return is_available(api_key=api_key)

    def pret_declare(self) -> bool:
        from . import config
        return bool(config.OPENAI_API_KEY)

    def modele_servi(self, model: Optional[str] = None) -> str:
        return modele_servi(model)

    def chat(self, system: str, user: str, *, model: Optional[str] = None,
             api_key: Optional[str] = None, seed: Optional[int] = None) -> str:
        return chat(system, user, model=model, api_key=api_key, seed=seed)

    def chat_json(self, system: str, user: str, *, model: Optional[str] = None,
                  api_key: Optional[str] = None, max_tokens: Optional[int] = None,
                  schema: Optional[dict] = None, retries: int = 2,
                  temperature: float = 0.1, seed: Optional[int] = None) -> dict[str, Any]:
        return chat_json(system, user, model=model, api_key=api_key,
                         max_tokens=max_tokens, schema=schema, retries=retries,
                         temperature=temperature, seed=seed)
