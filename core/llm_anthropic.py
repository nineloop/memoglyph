"""Moteur cloud Anthropic (Claude) — implémentation de `LLMEngine` (4e fournisseur).

Appel HTTP de l'API Messages par `httpx`, déjà une dépendance du produit : pas de
SDK, donc aucun paquet à installer. Cloud comme OpenAI et Mistral : `is_available`
ne fait AUCUN aller-retour réseau (présence de clé), la validité réelle se prouve à
la première génération.

Choix imposés par l'API (version `VERSION_API`) et ses modèles récents :
- ni `temperature`, ni `top_p`, ni `top_k` : ils sont refusés (400) depuis la version
  2026-10-21 ; la réflexion et l'effort restent aux réglages du modèle. `seed` n'existe
  pas chez Anthropic : il est accepté par le contrat et non transmis ;
- aucun préremplissage du tour assistant, refusé (400) par les modèles récents : le
  format de la réponse vient de la consigne ;
- pas de mode JSON garanti ici : un `schema` fourni est ajouté à la consigne comme
  directive (comme chez Mistral). La forme n'est donc PAS garantie, et les gardes de
  contenance et de provenance restent obligatoires en aval, inchangées ;
- une sortie coupée (`stop_reason` autre que `end_turn` ou `stop_sequence`) est un
  échec, jamais un JSON partiel ; un refus (`refusal`) aussi ;
- plusieurs objets JSON rendus à la suite (observé sur Sonnet 5.5) voient
  leurs `faits` réunis (`json_reuni`).
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

import httpx

from . import telemetry
from .config import ANTHROPIC_API_KEY, ANTHROPIC_CHAT_MODEL
from .llm_base import LLMError, retirer_enveloppe_markdown

URL = "https://api.anthropic.com/v1/messages"
VERSION_API = "2026-10-21"
MODELE_DEFAUT = "claude-haiku-5-5"
MAX_TOKENS_DEFAUT = 16000
DELAI_S = 600.0
# 429, erreurs serveur et surcharge (529) se reprennent ; le reste (400, 401, 402,
# 403, 404, 413) ne changerait pas en réessayant.
REPRISES_HTTP = 2
REPRENABLES = frozenset({429, 500, 502, 503, 504, 529})
ATTENTE_MAX_S = 30.0
ARRETS_COMPLETS = ("end_turn", "stop_sequence")

# Points remplaçables par les tests : transport HTTP et attente entre deux reprises.
_transport: Optional[httpx.BaseTransport] = None
_dormir = time.sleep


class AnthropicLLMError(LLMError):
    """Panne côté moteur Anthropic (clé absente, API en erreur, sortie inexploitable)."""


def _cle(api_key: Optional[str]) -> str:
    # Lu à l'APPEL, avec repli sur `os.getenv` : la constante `config` est évaluée à
    # l'import, donc périmée si le module a été chargé avant `load_env` (même contrat
    # que `llm_openai._cle`).
    import os
    from . import config
    if api_key is not None:
        return api_key.strip()
    return (config.ANTHROPIC_API_KEY or os.getenv("ANTHROPIC_API_KEY") or ANTHROPIC_API_KEY or "").strip()


def is_available(api_key: Optional[str] = None) -> bool:
    return bool(_cle(api_key))


# Usage RÉEL rapporté par l'API (opt-in), comme `llm_openai.USAGE` : un appelant qui
# mesure lit puis vide cette liste. L'entrée compte les jetons lus et écrits en cache.
USAGE: list[dict[str, int]] = []


def _attente(essai: int, retry_after: Optional[str]) -> float:
    try:
        demandee = float(retry_after) if retry_after else None
    except ValueError:
        demandee = None
    return min(demandee if demandee is not None else 2.0 ** essai, ATTENTE_MAX_S)


def _poster(corps: dict, cle: str) -> httpx.Response:
    entetes = {"x-api-key": cle, "anthropic-version": VERSION_API, "content-type": "application/json"}
    with httpx.Client(transport=_transport, timeout=DELAI_S) as client:
        for essai in range(REPRISES_HTTP + 1):
            try:
                reponse = client.post(URL, headers=entetes, json=corps)
            except httpx.TransportError as exc:
                if essai == REPRISES_HTTP:
                    raise AnthropicLLMError(f"API Anthropic injoignable ({type(exc).__name__}).") from None
                _dormir(_attente(essai, None))
                continue
            if reponse.status_code in REPRENABLES and essai < REPRISES_HTTP:
                _dormir(_attente(essai, reponse.headers.get("retry-after")))
                continue
            return reponse
    raise AnthropicLLMError("API Anthropic : aucune réponse.")  # pragma: no cover — boucle toujours rendue


def _erreur(reponse: httpx.Response) -> AnthropicLLMError:
    """L'erreur de l'API, par son type (`billing_error`, `rate_limit_error`…) ; jamais la clé."""
    try:
        detail = (reponse.json() or {}).get("error") or {}
    except ValueError:
        detail = {}
    genre = detail.get("type") or "erreur"
    message = str(detail.get("message") or reponse.text or "")[:300]
    return AnthropicLLMError(f"Anthropic {reponse.status_code} {genre} : {message}")


def _generer(system: str, user: str, *, model: Optional[str], api_key: Optional[str],
             max_tokens: Optional[int]) -> tuple[str, Optional[str]]:
    """(texte, `stop_reason`) d'un appel à l'API Messages."""
    cle = _cle(api_key)
    if not cle:
        raise AnthropicLLMError("ANTHROPIC_API_KEY manquante. Renseignez la clé API.")
    cible = modele_servi(model)
    corps = {"model": cible, "max_tokens": max_tokens or MAX_TOKENS_DEFAUT, "system": system,
             "messages": [{"role": "user", "content": user}]}
    _t0 = time.perf_counter()
    reponse = _poster(corps, cle)
    if reponse.status_code >= 400:
        raise _erreur(reponse)
    try:
        donnees = reponse.json()
    except ValueError:
        raise AnthropicLLMError("Réponse Anthropic illisible (pas du JSON).") from None
    usage = donnees.get("usage") or {}
    lus = int(usage.get("cache_read_input_tokens") or 0)
    entree = int(usage.get("input_tokens") or 0) + lus + int(usage.get("cache_creation_input_tokens") or 0)
    sortie = int(usage.get("output_tokens") or 0)
    USAGE.append({"in": entree, "out": sortie})
    statut = donnees.get("stop_reason")
    telemetry.record_model_call(
        provider="anthropic", model=cible, latency_s=time.perf_counter() - _t0,
        status="ok" if statut in ARRETS_COMPLETS else "truncated",
        usage_source="provider_reported" if usage else None,
        in_tok=entree if usage else None, out_tok=sortie if usage else None,
        cached_tok=lus if usage else None,
    )
    if statut == "refusal":
        raise AnthropicLLMError("Anthropic a refusé la requête (stop_reason refusal).")
    if statut not in ARRETS_COMPLETS:
        raise AnthropicLLMError(f"Sortie Anthropic coupée (stop_reason {statut}).")
    texte = "".join(b.get("text", "") for b in donnees.get("content") or [] if b.get("type") == "text")
    return texte.strip(), statut


def json_reuni(texte: str) -> dict[str, Any]:
    """L'objet JSON d'une réponse ; plusieurs objets à la suite voient leurs `faits` réunis.

    Un objet sans liste `faits` est rendu tel quel, au contrôle d'en juger.
    """
    decodeur, objets, i = json.JSONDecoder(), [], texte.find("{")
    while 0 <= i < len(texte):
        objet, fin = decodeur.raw_decode(texte, i)
        objets.append(objet)
        i = texte.find("{", fin)
    if not objets:
        raise ValueError("réponse sans objet JSON")
    if len(objets) == 1 or not all(isinstance(o, dict) and isinstance(o.get("faits"), list) for o in objets):
        return objets[0]
    return {"faits": [f for o in objets for f in o["faits"]]}


def modele_servi(model: Optional[str] = None) -> str:
    # MÊME expression que la génération : un seul choix, jamais reformulé.
    return model or ANTHROPIC_CHAT_MODEL or MODELE_DEFAUT


def chat(system: str, user: str, *, model: Optional[str] = None,
         api_key: Optional[str] = None, seed: Optional[int] = None) -> str:
    """Texte seul. `seed` accepté par le contrat mais NON transmis (voir module)."""
    return _generer(system, user, model=model, api_key=api_key, max_tokens=None)[0]


def chat_json(system: str, user: str, *, model: Optional[str] = None,
              api_key: Optional[str] = None, max_tokens: Optional[int] = None,
              schema: Optional[dict] = None, retries: int = 2,
              temperature: float = 0.1, seed: Optional[int] = None) -> dict[str, Any]:
    """Réponse JSON parsée. `retries` redemande une réponse illisible ; une sortie
    coupée ou refusée lève aussitôt. `temperature` et `seed` ne sont pas transmis."""
    if schema is not None:
        system = (system + "\n\nRéponds par un objet JSON conforme à ce schéma :\n"
                  + json.dumps(schema, ensure_ascii=False))
    dernier = ""
    for _ in range(retries + 1):
        brut, _statut = _generer(system, user, model=model, api_key=api_key, max_tokens=max_tokens)
        nettoye = retirer_enveloppe_markdown(brut)
        try:
            donnees = json_reuni(nettoye)
        except ValueError:
            dernier = nettoye
            continue
        if isinstance(donnees, dict):
            return donnees
        raise AnthropicLLMError(f"Le JSON renvoyé n'est pas une table : {type(donnees).__name__}")
    erreur = AnthropicLLMError("Format JSON invalide de l'API Anthropic.")
    erreur.brut = dernier
    raise erreur


class AnthropicEngine:
    """Moteur cloud Anthropic — `LLMEngine`. Pas d'`unload` (service distant)."""

    name = "anthropic"

    def is_available(self, *, api_key: Optional[str] = None) -> bool:
        return is_available(api_key=api_key)

    def pret_declare(self) -> bool:
        from . import config
        return bool(config.ANTHROPIC_API_KEY)

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
