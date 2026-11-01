"""Moteur « serveur compatible OpenAI » : Ollama, LM Studio, vLLM… joints par leur URL.

Réglé par `COMPATIBLE_BASE_URL` (l'URL de l'API, `…/v1` compris), `COMPATIBLE_MODEL`
et une clé facultative `COMPATIBLE_API_KEY`. Le moteur se CONNECTE seulement : il ne
démarre, n'arrête ni ne décharge aucun serveur (ce serveur n'est pas à nous), d'où
l'absence de `unload`.

Partagé avec le moteur MLX : la construction du client (`llm_client_openai`, sans
proxy du système, sans en-tête d'organisation, sans reprise du SDK) et le déballage
d'une sortie JSON (`llm_base.retirer_enveloppe_markdown`). Non partagé : tout le
cycle de vie du serveur `mlx_lm` et ses diagnostics (port, zombie, modèle à
raisonnement), qui n'ont pas de sens chez un serveur que l'on ne gouverne pas.

Le schéma JSON est passé dans la DIRECTIVE, comme pour MLX, et non par
`response_format` : les serveurs compatibles n'acceptent pas tous le même (LM Studio
refuse `json_object`, Ollama ignore `json_schema` selon les versions). Ce qui reste
garanti est ce que l'on VÉRIFIE au retour : du JSON bien formé, et une table.

La clé n'apparaît dans aucun message : les erreurs du SDK sont résumées par leur
type et leur statut HTTP, jamais par leur corps, et l'URL affichée perd ses
identifiants (`utilisateur:motdepasse@`).
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

import openai

from . import config, telemetry
from .llm_base import LLMError, LLMMalformedOutputError, retirer_enveloppe_markdown
from .llm_client_openai import client_sans_fuite

NOM = "compatible"
# Délai d'une génération, en secondes. Long par défaut : un modèle servi sur la
# machine peut mettre plusieurs minutes à rendre un compte rendu.
DELAI_S = float(config.getenv_compat("COMPATIBLE_TIMEOUT_S", "600") or "600")
# Le SDK refuse de se construire sans clé ; un serveur sans authentification
# ignore celle-ci.
_CLE_INERTE = "sans-cle"
# Le nom rendu tant qu'aucun modèle n'est configuré : `modele_servi` rend toujours un
# nom, et celui-ci ne passe jamais pour un vrai modèle (le fournisseur n'est alors
# ni proposé ni appelable).
MODELE_NON_CONFIGURE = "serveur-compatible-non-configure"


class CompatibleLLMError(LLMError):
    """Panne du serveur compatible : non configuré, injoignable, délai dépassé, réponse hors contrat."""


class _SortieInexploitable(CompatibleLLMError, LLMMalformedOutputError):
    """Le modèle a répondu, mais pas du JSON exploitable après tous les essais."""


def base_url() -> str:
    return (config.COMPATIBLE_BASE_URL or "").strip().rstrip("/")


def modele() -> str:
    return (config.COMPATIBLE_MODEL or "").strip()


def configure() -> bool:
    """Une URL et un modèle : sans les deux, le fournisseur n'est pas proposé."""
    return bool(base_url() and modele())


def hote() -> Optional[str]:
    """L'hôte de `COMPATIBLE_BASE_URL`, pour la règle d'envoi (`llm.est_local`)."""
    try:
        return urlsplit(base_url()).hostname
    except ValueError:
        return None


def url_affichable() -> str:
    """L'URL sans identifiants : elle sert aux messages d'erreur."""
    try:
        morceaux = urlsplit(base_url())
        netloc = morceaux.hostname or ""
        if morceaux.port:
            netloc = f"{netloc}:{morceaux.port}"
        return urlunsplit((morceaux.scheme, netloc, morceaux.path, "", ""))
    except ValueError:
        return "(URL illisible)"


class CompatibleEngine:
    """Un serveur compatible OpenAI, joint par son URL. Implémente `LLMEngine`."""

    name = NOM
    # Profil d'exécution prudent par défaut (tranches et lots du modèle local) : la
    # capacité du modèle servi est inconnue.
    profil_prudent = True
    # Hébergé par l'utilisateur : hors de la boucle locale, c'est un « cloud privé ».
    auto_heberge = True
    # Proposé à l'écran seulement s'il est configuré (`llm.propose`).
    propose_si_pret = True

    def __init__(self) -> None:
        self._client: Optional[openai.OpenAI] = None
        self._client_pour: Optional[tuple[str, str]] = None

    @property
    def host(self) -> Optional[str]:
        return hote()

    def _sdk(self) -> openai.OpenAI:
        cle = (config.COMPATIBLE_API_KEY or "").strip() or _CLE_INERTE
        if self._client is None or self._client_pour != (base_url(), cle):
            self._client = client_sans_fuite(base_url(), api_key=cle)
            self._client_pour = (base_url(), cle)
        return self._client

    # ---------- Contrat ----------

    def is_available(self, *, api_key: Optional[str] = None) -> bool:
        # Configuration seulement, aucun appel réseau : la route de configuration
        # l'appelle à chaque ouverture de l'interface. Le serveur est prouvé à la
        # première génération, et une panne y devient un 503 lisible.
        return configure()

    def pret_declare(self) -> bool:
        return configure()

    def modele_servi(self, model: Optional[str] = None) -> str:
        return model or modele() or MODELE_NON_CONFIGURE

    def _completion(self, system: str, user: str, *, model: Optional[str], max_tokens: Optional[int],
                    temperature: float, seed: Optional[int]) -> tuple[str, Optional[str]]:
        """(texte, motif d'arrêt). Chaque panne devient une `CompatibleLLMError` lisible."""
        if not configure():
            raise CompatibleLLMError(
                "Serveur compatible OpenAI non configuré : renseignez COMPATIBLE_BASE_URL et "
                "COMPATIBLE_MODEL dans le fichier .env.")
        cible = self.modele_servi(model)
        options: dict[str, Any] = {}
        if max_tokens is not None:
            options["max_tokens"] = max_tokens
        if seed is not None:
            options["seed"] = seed
        _t0 = time.perf_counter()
        try:
            reponse = self._sdk().chat.completions.create(
                model=cible,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=temperature, stream=False, timeout=DELAI_S, **options)
        except openai.APITimeoutError as exc:   # avant APIConnectionError, dont il hérite
            raise CompatibleLLMError(
                f"Serveur compatible OpenAI ({url_affichable()}) : aucune réponse en "
                f"{DELAI_S:g} s, délai dépassé.") from exc
        except openai.APIConnectionError as exc:
            raise CompatibleLLMError(
                f"Serveur compatible OpenAI injoignable à {url_affichable()} : vérifiez qu'il "
                f"est lancé et que COMPATIBLE_BASE_URL est juste.") from exc
        except openai.APIStatusError as exc:
            raise CompatibleLLMError(
                f"Le serveur compatible OpenAI ({url_affichable()}) a répondu {exc.status_code} "
                f"pour le modèle {cible!r}.") from exc
        except openai.APIError as exc:
            raise CompatibleLLMError(
                f"Le serveur compatible OpenAI ({url_affichable()}) a rendu une réponse hors "
                f"contrat ({type(exc).__name__}).") from exc
        usage = getattr(reponse, "usage", None)
        telemetry.record_model_call(provider=NOM, model=cible, latency_s=time.perf_counter() - _t0,
                                    status="ok", usage=usage,
                                    usage_source="provider_reported" if usage is not None else None)
        choix = (reponse.model_dump().get("choices") or [{}])[0]
        message = choix.get("message") if isinstance(choix, dict) else None
        contenu = message.get("content") if isinstance(message, dict) else None
        if not isinstance(contenu, str):
            raise CompatibleLLMError("Réponse du serveur compatible OpenAI sans contenu textuel.")
        motif = choix.get("finish_reason") if isinstance(choix, dict) else None
        if motif == "length":
            raise CompatibleLLMError(
                "Réponse du serveur compatible OpenAI coupée par la limite de jetons (max_tokens).")
        return contenu, motif

    def chat(self, system: str, user: str, *, model: Optional[str] = None,
             api_key: Optional[str] = None, seed: Optional[int] = None) -> str:
        return self._completion(system, user, model=model, max_tokens=None, temperature=0.2,
                                seed=seed)[0].strip()

    def chat_json(self, system: str, user: str, *, model: Optional[str] = None,
                  api_key: Optional[str] = None, max_tokens: Optional[int] = None,
                  schema: Optional[dict] = None, retries: int = 2, temperature: float = 0.1,
                  seed: Optional[int] = None) -> dict[str, Any]:
        directive = system + "\n\nCRUCIAL : réponds EXCLUSIVEMENT en JSON valide."
        if schema is not None:
            directive += "\n\nSchéma JSON à respecter STRICTEMENT :\n" + json.dumps(schema, ensure_ascii=False)
        # Avec une graine, le serveur rejoue la même sortie : un nouvel essai ne changerait rien.
        essais = 1 if seed is not None else retries + 1
        dernier, motif = "", None
        for _ in range(essais):
            contenu, motif = self._completion(directive, user, model=model, max_tokens=max_tokens,
                                              temperature=temperature, seed=seed)
            dernier = retirer_enveloppe_markdown(contenu)
            try:
                donnees = json.loads(dernier)
            except json.JSONDecodeError:
                continue
            if isinstance(donnees, dict):
                return donnees
            raise CompatibleLLMError(
                f"Le JSON du serveur compatible OpenAI n'est pas une table : {type(donnees).__name__}.")
        raise _SortieInexploitable(
            f"Réponse du serveur compatible OpenAI invalide après {essais} essai(s) : {dernier[:300]!r}",
            brut=dernier, finish_reason=motif)
