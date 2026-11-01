"""Client LLM pour l'API Mistral AI (Chat Completions & Embeddings)."""

from __future__ import annotations

import re

import json
import os
import time
from typing import Any, Optional

from . import telemetry
from .config import MISTRAL_API_KEY, MISTRAL_CHAT_MODEL, MISTRAL_CHAT_MODEL_DEFAUT
from .llm_base import (
    LLMError,
    LLMMalformedOutputError,
    retirer_enveloppe_markdown,
)



# Refus retentés une fois : débit dépassé ou service saturé. Un 429 n'est pas
# facturé. Un quota épuisé renvoie lui aussi 429 : la seconde tentative échoue
# alors pareil, et c'est le corps de la réponse qui le dit.
STATUTS_A_RETENTER = (429, 503)
_ATTENTE_DEFAUT_S = 10.0
# Borne de l'attente : au-delà, l'utilisateur a meilleur temps de relancer
# lui-même que de regarder une barre figée.
_ATTENTE_MAX_S = 60.0


def attente_avant_reprise(response: Any) -> float:
    """Délai annoncé par `Retry-After` en secondes, borné. Une date HTTP ou une
    valeur illisible retombe sur le défaut."""
    try:
        delai = float(response.headers.get("Retry-After"))
    except (TypeError, ValueError):
        delai = _ATTENTE_DEFAUT_S
    return min(max(delai, 0.0), _ATTENTE_MAX_S)


def _poster(url: str, **kwargs: Any) -> tuple[Any, float]:
    """`requests.post` avec une seule reprise sur 429 ou 503.

    Rend la réponse et l'instant de départ de la DERNIÈRE tentative : la
    latence télémétrée ne doit pas compter l'attente. Les appels du profilage
    et du compte-rendu abandonnaient au premier refus, et un compte-rendu
    perdait ainsi toutes ses tranches.
    """
    import requests

    for tentative in (1, 2):
        t0 = time.perf_counter()
        response = requests.post(url, **kwargs)
        if (response.ok or tentative == 2
                or response.status_code not in STATUTS_A_RETENTER):
            return response, t0
        delai = attente_avant_reprise(response)
        print(f"[llm-mistral] HTTP {response.status_code}, "
              f"nouvelle tentative dans {delai:.0f} s")
        time.sleep(delai)
    raise AssertionError("inatteignable")


def _erreur_sure(
    prefixe: str, brut: object, *actives: Optional[str],
    classe: type[LLMError] = None,  # type: ignore[assignment]
) -> LLMError:
    """Construit une erreur portant une valeur DYNAMIQUE, assainie.

    C'est la SEULE porte par laquelle une donnée non littérale entre dans un
    message d'erreur de ce module. Le test structurel refuse tout le reste.

    ## Pourquoi une règle structurelle plutôt qu'une analyse de flux

    Le garde précédent remontait l'origine de chaque chaîne interpolée dans un
    `raise` — un mini-analyseur de flux maison. Il a fallu l'étendre quatre
    fois en une journée : noms de variables gelés, puis concaténation et
    `.format()`, puis portées de fonction, puis ordre des réaffectations. À
    chaque fois une revue extérieure trouvait la forme suivante, et **chaque
    trou protégeait précisément une fuite de clé** : annotation `msg: str = …`,
    walrus `E(msg := …)`, `"".join([…])` étaient encore invisibles.

    Compléter l'analyseur une dixième fois n'aurait pas fermé le motif.
    Règle : on inverse le problème.

    « Retrouver l'origine de toute chaîne » devient « **refuser toute chaîne
    dont l'origine n'est pas manifestement littérale** ». Le test n'a plus
    besoin de comprendre ni le flux ni la composition — il lit un argument et
    vérifie que c'est une constante.

    Le défaut est conservateur, et c'est le bon sens pour une frontière de
    secrets : une forme légitime refusée coûte un appel à ce constructeur, une
    forme dangereuse acceptée coûte la clé.
    """
    # Le PRÉFIXE est assaini lui aussi.
    #
    # Il ne l'était pas : `f"{prefixe}{_sans_secret(brut, *actives)}"` laissait
    # entrer le premier argument verbatim, alors que le test AST exempte toute
    # cette fonction. Reproduit — `_erreur_sure(f"prefix {cle} : ", "corps",
    # cle)` publiait la clé. La rupture avait fermé la seconde entrée de la
    # fabrique, pas la première.
    #
    # Deux verrous plutôt qu'un : le test AST exige un préfixe LITTÉRAL à
    # l'appel, et l'assainissement ici couvre ce qui passerait quand même —
    # une réflexion, un appel dynamique, un futur appelant hors du module.
    # `classe` ne change RIEN à l'assainissement : elle ne choisit que le type
    # levé, pour distinguer une sortie inexploitable d'une panne de service. Le
    # défaut reste `MistralLLMError`, donc aucun appelant existant ne bouge.
    return (classe or MistralLLMError)(
        f"{_sans_secret(prefixe, *actives)}{_sans_secret(brut, *actives)}")


def _sans_secret(brut: object, *actives: Optional[str], limite: int = 300) -> str:
    """Rend un extrait d'erreur sûr à publier dans une réponse HTTP.

    `response.text` et `data` sont des corps de réponse d'API. Ils remontaient
    verbatim jusqu'au 503 rendu à l'utilisateur, via `MistralLLMError` puis
    `LLMError`. Un assainissement placé dans le seul `except Exception` de la
    façade ne suffit pas : il n'est atteint qu'APRÈS le `except
    MistralLLMError`, celui-là recopiant tout.

    Trois précautions, dans cet ordre : effacer la clé configurée si elle a été
    renvoyée en écho, effacer tout ce qui ressemble à un jeton porteur, puis
    borner. La borne compte : un corps d'erreur peut faire des kilo-octets, et
    un message d'erreur n'est pas un journal.
    """
    texte = str(brut)
    # Toutes les clés susceptibles d'être ACTIVES, pas seulement la globale.
    # Une première correction était incomplète : `chat()` et `embed()`
    # acceptent un `api_key` par requête (BYOK). Une clé passée ainsi, courte ou
    # ponctuée, échappait à la fois au remplacement de la globale et aux motifs
    # génériques — et l'amont peut la réimprimer dans `response.text`.
    for cle in (*actives, MISTRAL_API_KEY, os.getenv("MISTRAL_API_KEY")):
        if cle:
            texte = texte.replace(cle, "***")
    texte = re.sub(r"(?i)(bearer\s+|api[_-]?key[\"'\s:=]+)[A-Za-z0-9_\-]{8,}",
                   r"\1***", texte)
    texte = re.sub(r"\b[A-Za-z0-9_\-]{32,}\b", "***", texte)
    return texte[:limite] + ("…" if len(texte) > limite else "")


class _MistralSortieInexploitable(LLMMalformedOutputError):
    """Sortie inexploitable de l'API Mistral, réessayable par l'appelant.

    Pendant exact de `_MlxSortieInexploitable`. Elle n'hérite PAS de
    `MistralLLMError` : rien dans le dépôt n'attrape ce type-là spécifiquement,
    et la parenté avec `LLMError` — via `LLMMalformedOutputError` — suffit à
    conserver la traduction en 503.
    """


class MistralLLMError(LLMError):
    """Erreur générique lors d'une interaction avec l'API Mistral LLM.

    Hérite désormais du type NEUTRE, et non plus de `RuntimeError`. La façade
    faisait la conversion à la main — `except MistralLLMError: raise
    LLMError(str(exc))` — ce qui la rendait dépendante du moteur : ajouter un
    moteur, c'était ajouter une branche, et l'oublier c'était rendre un 500
    brut là où une panne de service doit rendre 503.

    Le message, lui, traverse la façade tel quel : il est composé par
    `_erreur_sure`, la seule porte par laquelle une valeur dynamique entre dans
    une erreur de ce module, et il est donc déjà assaini.
    """


def is_available(api_key: Optional[str] = None) -> bool:
    key = api_key if api_key is not None else (MISTRAL_API_KEY or os.getenv("MISTRAL_API_KEY"))
    return bool(key and key.strip())


def chat(
    system: str,
    user: str,
    *,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: Optional[int] = None,
    seed: Optional[int] = None,
) -> str:
    """Le texte seul — la surface publique du contrat `LLMEngine`."""
    return _completer(system, user, model=model, api_key=api_key,
                      temperature=temperature, max_tokens=max_tokens,
                      seed=seed)[0]


def _completer(
    system: str,
    user: str,
    *,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: Optional[int] = None,
    seed: Optional[int] = None,
) -> tuple[str, Optional[str]]:
    """(texte, `finish_reason`) — le motif d'arrêt, que `chat` ne peut pas rendre.

    Il est remonté pour une raison précise : le filet de réparation de
    `core/reparation_json.py` n'a le droit d'agir que sur une sortie COMPLÈTE.
    Sans ce champ, le chemin cloud n'aurait pas le filet — non parce que le
    service ne dit rien, mais parce que notre façade jetait ce qu'il disait.

    `None` reste possible (repli `requests` sur une réponse sans le champ), et
    signifie alors « on ne sait pas » : le réparateur refuse dans ce cas.
    """
    key = api_key if api_key is not None else (MISTRAL_API_KEY or os.getenv("MISTRAL_API_KEY"))
    if not key:
        raise MistralLLMError("MISTRAL_API_KEY manquante. Renseignez la clé API.")

    target_model = model or MISTRAL_CHAT_MODEL or MISTRAL_CHAT_MODEL_DEFAUT

    # `max_tokens` n'est ajouté que s'il est demandé : l'absence laisse le
    # défaut du service, une valeur explicite le borne. C'est le pendant cloud
    # de `num_predict` côté Ollama, même contrainte, autre vocabulaire.
    borne: dict[str, Any] = {} if max_tokens is None else {"max_tokens": max_tokens}
    # L'API Mistral nomme la graine `random_seed`, là où mlx_lm.server dit
    # `seed`. La façade expose le nom neutre ; la traduction vit ici, comme
    # pour toute autre différence de vocabulaire entre moteurs.
    if seed is not None:
        borne["random_seed"] = seed

    try:
        from mistralai import Mistral  # type: ignore[import-untyped]

        with Mistral(api_key=key) as client:
            _t0 = time.perf_counter()
            res = client.chat.complete(
                model=target_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                **borne,
            )
            choice = res.choices[0]
            _fr = getattr(choice, "finish_reason", None)
            # Télémétrie FinOps (best-effort, hors contrat) : `res.usage` était
            # jeté ; ici on l'enregistre sans toucher au contrat de retour.
            _usage = getattr(res, "usage", None)
            telemetry.record_model_call(
                provider="mistral", model=target_model,
                latency_s=time.perf_counter() - _t0,
                status="truncated" if (_fr and _fr != "stop") else "ok",
                usage=_usage,
                usage_source="provider_reported" if _usage is not None else None,
            )
            return (str(choice.message.content or "").strip(), _fr)
    except ImportError:

        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": target_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            **borne,
        }
        response, _t0 = _poster(
            "https://api.mistral.ai/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=180,
        )
        if not response.ok:
            raise _erreur_sure(
                "Erreur API Mistral : ",
                {"status": response.status_code, "corps": response.text}, key)
        data = response.json()
        try:
            choix = data["choices"][0]
            _fr = choix.get("finish_reason")
            _usage = data.get("usage")
            telemetry.record_model_call(
                provider="mistral", model=target_model,
                latency_s=time.perf_counter() - _t0,
                status="truncated" if (_fr and _fr != "stop") else "ok",
                usage=_usage,
                usage_source="provider_reported" if _usage is not None else None,
            )
            return (str(choix["message"]["content"]).strip(), _fr)
        except (KeyError, IndexError) as exc:
            raise _erreur_sure("Réponse API Mistral inattendue : ", data, key) from exc


def chat_json(
    system: str,
    user: str,
    *,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    temperature: float = 0.1,
    max_tokens: Optional[int] = None,
    schema: Optional[dict] = None,
    seed: Optional[int] = None,
    retries: int = 2,
) -> dict[str, Any]:
    """Réponse JSON parsée. Honore les trois contraintes du contrat commun.

    Chacune est honorée par le moyen dont ce moteur dispose, et la différence
    est DITE plutôt que subie :

    - `max_tokens` est un vrai paramètre de l'API ;
    - `retries` est une boucle, exactement comme côté Ollama — un service
      distant renvoie lui aussi des réponses tronquées ou non-JSON de façon
      intermittente ;
    - `schema` ne peut PAS contraindre le décodage : l'API n'a pas d'équivalent
      du `format` d'Ollama, qui impose la grammaire pendant la génération. Il
      est donc transmis au modèle dans la directive. C'est plus faible, et il
      fallait choisir : accepter l'argument pour le jeter en silence aurait
      produit un compte-rendu hors contrat sans le moindre signal — le motif
      exact que ce dépôt traque.
    """
    json_system = system + "\n\nCRUCIAL: Réponds EXCLUSIVEMENT au format JSON valide."
    if schema is not None:
        json_system += (
            "\n\nSchéma JSON à respecter STRICTEMENT :\n"
            + json.dumps(schema, ensure_ascii=False)
        )

    dernier = ""
    dernier_motif: Optional[str] = None
    # La boucle est CONSERVÉE ici, là où MLX la supprime dès qu'une graine est
    # posée. L'asymétrie est délibérée, et elle repose sur une différence de
    # nature entre les deux backends — pas sur une dette à uniformiser.
    #
    #     MLX + graine fixe    → reprise interne SUPPRIMÉE
    #     Mistral + random_seed → reprise interne CONSERVÉE
    #
    # Côté MLX, le serveur applique `mx.random.seed(seed)` avant de générer :
    # rejouer le même prompt rend la même sortie, octet pour octet : la seconde
    # sortie porte l'empreinte SHA de la première, et la reprise jette des
    # milliers de jetons et des minutes de calcul.
    #
    # Côté Mistral, `random_seed` est un EFFORT du service, pas un générateur
    # local qu'on réamorce. Rien ne démontre l'identité stricte, et l'affirmer
    # sans mesure serait exactement le motif que ce dépôt traque. Tant qu'elle
    # n'est pas démontrée, la reprise peut réellement récupérer.
    #
    # ⚠️ CE QU'ELLE COÛTE, mesuré par le chemin intégré :
    #
    #     sortie réparable      → 2 appels facturés au lieu de 1
    #     sortie irrécupérable  → 6 appels facturés au lieu de 3
    #
    # Le filet de réparation n'entre en jeu qu'APRÈS l'épuisement de cette
    # boucle : sur le chemin cloud, il épargne la reprise MÉTIER mais pas la
    # reprise MOTEUR. Le gain de coût y est donc la moitié de celui mesuré en
    # local. Supprimer la boucle demanderait d'abord de démontrer que
    # `random_seed` rend deux sorties identiques — c'est un smoke payant, il
    # n'a pas été fait, et on ne préjuge pas de son résultat.
    for _ in range(retries + 1):
        raw, motif = _completer(
            json_system, user, model=model, api_key=api_key,
            temperature=temperature, max_tokens=max_tokens, seed=seed,
        )

        # Même déballage que côté MLX, et par le MÊME code : voir
        # `llm_base.retirer_enveloppe_markdown`.
        cleaned = retirer_enveloppe_markdown(raw)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            dernier = cleaned  # réponse non-JSON → on retente
            dernier_motif = motif
            continue
        if isinstance(data, dict):
            return data
        # Une réponse BIEN formée mais de mauvais type ne s'améliorera pas en
        # la redemandant : le modèle a compris la consigne et rendu autre chose.
        raise _erreur_sure("Le JSON renvoyé n'est pas une table: ", type(data))

    # Même distinction que côté MLX : le service a répondu, c'est sa SORTIE qui
    # est inexploitable. `report.py` peut donc la réessayer sans réessayer aussi
    # une clé révoquée ou un quota épuisé.
    #
    # La sortie entière et le motif d'arrêt sont posés APRÈS construction, et
    # non passés à `_erreur_sure` : cette fabrique est la frontière des secrets,
    # elle n'accepte que ce qui va dans le MESSAGE, et l'élargir pour un besoin
    # qui n'est pas le sien rouvrirait exactement ce qu'elle ferme.
    #
    # Ces deux champs ne sont jamais affichés — ils servent au filet de
    # `core/reparation_json.py`, qui a besoin du texte VERBATIM : l'assainir le
    # modifierait, donc invaliderait la comparaison des littéraux qui garantit
    # qu'aucun contenu n'est touché.
    erreur = _erreur_sure(
        "Format JSON invalide de l'API Mistral : ", dernier, api_key,
        classe=_MistralSortieInexploitable)
    erreur.brut = dernier
    erreur.finish_reason = dernier_motif
    raise erreur


def embed(texts: list[str], *, api_key: Optional[str] = None) -> list[list[float]]:
    key = api_key or MISTRAL_API_KEY or os.getenv("MISTRAL_API_KEY")
    if not key:
        raise MistralLLMError("MISTRAL_API_KEY manquante.")

    if not texts:
        return []

    try:
        from mistralai import Mistral  # type: ignore[import-untyped]

        with Mistral(api_key=key) as client:
            res = client.embeddings.create(
                model="mistral-embed",
                inputs=texts,
            )
            return [data.embedding for data in res.data]
    except ImportError:

        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "mistral-embed",
            "input": texts,
        }
        response, _ = _poster(
            "https://api.mistral.ai/v1/embeddings",
            headers=headers,
            json=payload,
            timeout=120,
        )
        if not response.ok:
            raise _erreur_sure(
                "Erreur Embeddings Mistral : ",
                {"status": response.status_code, "corps": response.text}, key)
        data = response.json()
        try:
            return [item["embedding"] for item in data["data"]]
        except (KeyError, IndexError) as exc:
            raise _erreur_sure("Réponse Embeddings Mistral inattendue : ", data, key) from exc


class MistralEngine:
    """Moteur cloud Mistral — implémentation de `LLMEngine`.

    Pas de méthode `unload` : la capacité est optionnelle, et un service
    distant ne tient aucune mémoire ici. `embed` reste une fonction du module,
    hors du contrat : les embeddings sont un autre métier (préfixes, pooling,
    dimension, espace vectoriel scellé), que `core/embed.py` route lui-même.
    """

    name = "mistral"

    def is_available(self, *, api_key: Optional[str] = None) -> bool:
        return is_available(api_key=api_key)

    def pret_declare(self) -> bool:
        # AUCUN appel réseau : la contrainte nº 3 du contrat interdit un
        # aller-retour distant sur une route que l'interface charge à chaque
        # ouverture. Une clé posée ne prouve pas qu'elle est valide — c'est
        # précisément ce que « déclaré » veut dire, et `is_available` reste là
        # pour le prouver quand ça compte.
        #
        # Lu depuis `config` à l'APPEL, pas depuis l'import : `load_env()`
        # rafraîchit `MISTRAL_API_KEY` après lecture du `.env`, et une clé
        # ajoutée sans redémarrage doit se voir.
        from . import config

        return bool(config.MISTRAL_API_KEY)

    def modele_servi(self, model: Optional[str] = None) -> str:
        # La MÊME expression que `chat()` à la ligne du `target_model`, et pas
        # une reformulation : deux écritures du même choix finissent par
        # diverger, ce qui est précisément l'histoire que ce contrat vient de
        # clore. Le repli final est obligatoire — sans lui, une configuration
        # vide rendrait une chaîne vide, donc un champ `model` vide dans un
        # document autoritatif.
        return model or MISTRAL_CHAT_MODEL or MISTRAL_CHAT_MODEL_DEFAUT

    def chat(
        self,
        system: str,
        user: str,
        *,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        seed: Optional[int] = None,
    ) -> str:
        return chat(system, user, model=model, api_key=api_key, seed=seed)

    def chat_json(
        self,
        system: str,
        user: str,
        *,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        max_tokens: Optional[int] = None,
        schema: Optional[dict] = None,
        retries: int = 2,
        temperature: float = 0.1,
        seed: Optional[int] = None,
    ) -> dict[str, Any]:
        return chat_json(
            system, user, model=model, api_key=api_key,
            max_tokens=max_tokens, schema=schema, retries=retries,
            temperature=temperature, seed=seed,
        )
