"""Moteur LLM local MLX : un serveur `mlx_lm.server` SUPERVISÉ, sur un port épinglé.

Implémentation de `LLMEngine` (core/llm_base.py). Une classe, une entrée de
registre — c'est la promesse de cette interface, et ce module la tient sans
toucher à `core/llm.py` au-delà d'une ligne.

## Pourquoi un serveur persistant, et pas un sous-processus par appel

Mesuré sur cette machine : un sous-processus par appel coûte ~3,2 s de
rechargement à chaud, et un compte-rendu fait ~9 appels — ~29 s ajoutées, pour
rien. Le serveur persistant rend 1,2 à 1,5 s par requête à chaud, du même ordre
qu'Ollama. Son empreinte est de ~5,6 Go, que `unload()` rend en tuant l'enfant.

(`ps rss` SOUS-COMPTE massivement la mémoire MLX — 0,84 Go affiché contre
5602 Mo réels. Toute mesure future doit passer par `phys_footprint`.)

## Pourquoi `/health` n'est JAMAIS interrogé

MESURÉ, avec un modèle introuvable : le chargement est éager dans un thread de
travail, ce thread meurt sur l'exception, **le serveur HTTP continue de
servir**, `GET /health` répond `200 {"status":"ok"}` indéfiniment, et
`POST /v1/chat/completions` ne rend AUCUNE réponse HTTP — pas un 500, pas une
ligne de journal. Serveur définitivement zombie, et vert.

Précision MESURÉE plus tard, et qui a coûté deux conceptions fausses : cette
requête **PEND**, elle ne raccroche pas. Le client en sort par un délai
dépassé (`APITimeoutError`, cause `ReadTimeout`), jamais par une connexion
fermée. Or un délai dépassé est aussi ce que rend une génération saine mais
longue : l'exception ne distingue donc RIEN. Ce qui les sépare est le moment où
elle survient — voir `_prouver_la_generation`.

Le code du serveur le confirme (mlx-lm v0.31.3, `mlx_lm/server.py`) :
`handle_health_check` écrit `200 {"status": "ok"}` sans consulter quoi que ce
soit de l'état du modèle. Un `/health` vert ne prouve donc RIEN, et l'interroger
n'aurait produit que le énième instrument de contrôle défaillant de ce projet.

`is_available()` fait à la place une VRAIE génération, d'un jeton, à délai
borné.

## La SEULE interrogation de `/v1/models`, et pourquoi elle ne contredit rien

Il existe un état plus profond que le zombie vert ci-dessus, MESURÉ sur trois
serveurs orphelins (PPID 1) : le processus tient encore le port en
`LISTEN`, mais **`/v1/models` LUI-MÊME ne répond plus** — `GET` rend 000, la
connexion est acceptée par le noyau puis jamais servie. Ce n'est pas le thread
de chargement qui est mort, c'est le PROCESSUS ENTIER qui est figé (un appel
natif qui ne rend jamais la main, GIL compris). Il a fallu tuer ces trois-là à
la main pour libérer le port.

`_sonde_http_vivante` est le SEUL endroit du module qui interroge `/v1/models`,
et l'en-tête ci-dessus reste vrai : on ne se sert JAMAIS de son VERT. Un
`/v1/models` à 200 ne prouve pas plus la génération que `/health` — le zombie
« thread de chargement mort » répond vert aux deux. On se sert UNIQUEMENT de son
ROUGE, et l'inférence est l'inverse de la précédente, pas la même :

- `handle_models_request` (mlx-lm 0.31.3) scanne le cache Hugging Face et ne
  touche NI le GPU NI la file de génération, et le serveur est un
  `ThreadingHTTPServer` : un serveur sain répond donc `/v1/models` en ~130 ms
  MÊME occupé à générer pour une autre requête (la génération, elle, est
  sérialisée derrière un unique thread + `Queue`). Aucun serveur vivant, même
  saturé, ne rate cette sonde.
- Un `/v1/models` muet signale donc un état qu'AUCUN serveur vivant ne produit —
  le jam du processus entier. C'est le seul cas où l'on se permet de tuer un
  serveur dont la propriété n'est PAS prouvée (voir `_recuperer_serveur_fige`),
  sans trahir « ownership uniquement » : on ne tue pas un serveur d'autrui qui
  travaille, on retire un cadavre qui tient le port.

La conséquence à ne pas oublier : cette sonde n'attrape que le jam TOTAL. Un
serveur dont seul le `_generation_thread` est mort répond vert à `/v1/models` et
lui échappe — il reste indistinguable d'une génération longue, et seul
`_prouver_la_generation` au démarrage (ou la propriété prouvée) le tranche.

## Ce que « le bon modèle » peut et ne peut pas prouver

Toujours dans `mlx_lm/server.py` : `self.requested_model = self.body.get(
"model", "default_model")` puis `"model": self.requested_model` dans la
réponse. Le champ `model` d'une réponse mlx_lm est donc un **écho** de la
requête, pas une déclaration d'état.

Deux conséquences, et il faut dire les deux :

- comparer cet écho à ce qu'on a envoyé ne prouve rien CONTRE un vrai serveur
  mlx_lm — c'est une tautologie, et la traiter en preuve serait exactement le
  motif que ce dépôt traque ;
- il reste un discriminant réel contre un serveur ÉTRANGER assis sur notre
  port : la plupart des serveurs compatibles OpenAI (llama.cpp, vLLM, LM
  Studio) annoncent le modèle qu'ils ont chargé, pas celui qu'on demande. Un
  écho qui ne correspond pas est donc une panne à signaler.

La garantie que le modèle attendu est réellement celui qui génère vient
d'ailleurs : la requête le NOMME, et `mlx_lm` charge le nom demandé
(`model_provider.load(args.model.model)`). Un nom introuvable ne produit pas de
génération — et l'absence de génération est traitée comme une panne moteur.

## Pourquoi le SDK `openai` plutôt qu'un client HTTP écrit à la main

`mlx_lm.server` parle le protocole d'OpenAI ; le SDK officiel le parle aussi, et
il est MAINTENU. L'intérêt n'est pas d'économiser les quarante lignes de
`httpx` qu'il remplace — c'est qu'un même chemin de code atteigne mlx, Ollama
ou un endpoint distant en ne changeant qu'une `base_url`. Sans cela, comparer
la qualité de l'offline à celle d'un modèle distant demanderait deux clients,
donc deux comportements d'erreur, donc une comparaison qui mesure aussi nos
propres différences d'implémentation.

Trois défauts par DÉFAUT du SDK sont neutralisés ici, chacun mesuré :

- `max_retries` vaut **2**. Le SDK réessaierait donc en silence, y compris sur
  un serveur zombie que ce module tue exprès pour ne PAS réessayer. Combiné aux
  `retries` de `chat_json`, cela ferait 9 requêtes, et un délai de 900 s en
  ferait 2700. Il est mis à **0** : la seule boucle de reprise du moteur est
  celle de `chat_json`, visible et bornée.
- Le client REFUSE de se construire sans `api_key` (`OpenAIError: Missing
  credentials`). Un serveur local n'en demande aucune ; on passe donc un
  littéral inerte, jamais un secret, jamais une variable d'environnement.
- `openai.APITimeoutError` **hérite** de `openai.APIConnectionError`, exactement
  comme `httpx.TimeoutException` hérite de `httpx.TransportError`. Le piège
  survit au changement de client, et l'ordre des `except` reste ce qui empêche
  de détruire un serveur sain.

## Pourquoi `response_format` n'est pas envoyé

MESURÉ sur mlx-lm 0.31.3, trois fois de suite et de façon déterministe : un
`response_format: {"type": "json_schema", …, "strict": true}` exigeant les
champs `zorglub` et `wibble` a produit `{"nom": …, "type": …, "origine": …}`.
Aucun champ du schéma, aucune erreur, et une sortie identique à celle obtenue
SANS schéma. Le serveur accepte le champ et l'ignore.

C'est la différence de fond avec Ollama, qui contraint le décodage par sa
grammaire (`format`). Ici la contrainte ne peut être que DEMANDÉE au modèle
puis VÉRIFIÉE au retour — ce que `chat_json` fait, et documente. Envoyer
`response_format` en plus donnerait l'apparence d'une garantie sans en être
une : c'est le motif de l'instrument de mesure décoratif, et il est refusé.
"""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

import httpx
import openai

from . import config
from . import telemetry
from .llm_client_openai import client_sans_fuite
from .llm_base import (
    LLMError,
    LLMMalformedOutputError,
    retirer_enveloppe_markdown,
)

__all__ = ["MlxEngine", "MlxLLMError", "MlxLLMOutputLimitError"]

# Préfixe du journal Memoglyph — même convention que `[llm]`, `[stt-mistral]`,
# `[semantic]`. `start.sh` redirige la sortie de l'API dans `memoglyph-api.log` :
# c'est là que le `stderr` de l'enfant doit atterrir, parce que le chargement du
# modèle échoue AVANT toute requête et n'est visible que là.
_JOURNAL = "[llm-mlx]"

# Sonde de VIVACITÉ du processus (`_sonde_http_vivante`), distincte de la sonde
# de GÉNÉRATION (`_delai_sonde`, 60 s). Elle ne fait qu'un `GET /v1/models`, une
# route sans GPU servie sur son propre thread : un serveur sain répond en
# ~130 ms même saturé. Le délai est donc COURT — assez pour couvrir un
# `scan_cache_dir()` sur un cache HF volumineux, bien trop pour laisser passer un
# processus figé. Plusieurs tentatives parce que la conséquence d'un ROUGE est un
# arrêt forcé : on n'y consent qu'après un échec RÉPÉTÉ, jamais sur un raté seul.
# Modifiables par les tests (`monkeypatch.setattr(llm_mlx, …)`).
_SONDE_HTTP_DELAI = 2.0
_SONDE_HTTP_TENTATIVES = 3


class MlxLLMError(LLMError):
    """Panne du moteur MLX local.

    Hérite du type NEUTRE : `api/main.py` traduit `LLMError` en 503, et un
    moteur qui lèverait autre chose rendrait un 500 brut sur une simple panne de
    service.

    Aucun secret ne transite ici — le serveur est local et sans clé — mais les
    messages restent composés de littéraux et de valeurs que nous produisons
    (port, code de sortie, nom de modèle configuré).
    """


class _MlxSortieInexploitable(MlxLLMError, LLMMalformedOutputError):
    """Sortie inexploitable du moteur MLX, réessayable par l'appelant.

    Double parenté VOULUE : `MlxLLMError` pour que les `except` existants du
    moteur continuent de l'attraper, `LLMMalformedOutputError` pour que
    `report.py` la distingue d'une panne de service. Retirer l'une des deux
    casserait un des deux usages.
    """


def _empreinte_de_boucle(texte: str) -> tuple[int, int, str] | None:
    """Une répétition TERMINALE ? Rend (période, répétitions, motif), sinon None.

    ## Pourquoi ce détecteur existe

    MESURÉ : un compte-rendu légitime coûte 1 100 à 2 500 jetons, et le
    contrat en autorise 3 300 au maximum théorique — six sections de cinq
    affirmations. Or trois modèles de trois éditeurs différents en brûlent 6 144
    sans fermer leur JSON.

    Ils ne produisent donc pas un long document : ils DÉGÉNÈRENT. La littérature
    le nomme boucle de décodage à basse entropie, et le message d'erreur ne peut
    pas continuer à conseiller « traite un extrait plus court » — le document
    n'est pas en cause, et l'utilisateur perdrait son temps.

    ## La méthode, et ses limites

    On cherche la plus PETITE période dont la fin du texte est la répétition. Un
    seuil de trois répétitions écarte les coïncidences ; la fenêtre de 3 000
    caractères permet d'attraper une boucle STRUCTURELLE — un objet affirmation
    entier qui se répète — et pas seulement un jeton bégayé.

    Ce détecteur ne prouve rien : il décrit. Une sortie tronquée sans boucle
    reste possible, et rend `None`.
    """
    queue = (texte or "")[-3000:]
    if len(queue) < 30:
        return None
    for periode in range(1, len(queue) // 3 + 1):
        motif = queue[-periode:]
        repetitions = 1
        while (queue[-periode * (repetitions + 1):-periode * repetitions]
               == motif):
            repetitions += 1
        if repetitions >= 3:
            return periode, repetitions, motif
    return None


class MlxLLMOutputLimitError(MlxLLMError):
    """La génération a été coupée à `max_tokens` : le contenu est INCOMPLET.

    Jumelle de `llm_ollama.LLMOutputLimitError`, et délibérément une classe
    DISTINCTE plutôt qu'un import : `llm_mlx` ne doit nommer aucun autre moteur,
    sans quoi retirer Ollama emporterait ce type. Le contrat commun n'exige que
    la parenté avec `LLMError` (`test_le_marqueur_503_est_neutre_et_partage`),
    qui passe par `MlxLLMError`.

    Sans elle, une réponse tronquée se présentait comme un JSON malformé : on
    réessayait trois fois le même appel condamné par son budget de jetons, puis
    on rendait « réponse invalide » — un diagnostic faux, qui envoyait chercher
    le défaut du côté du modèle plutôt que du côté de `max_tokens`.
    """


def _journaliser(message: str) -> None:
    """Une seule porte vers le journal, pour que les tests puissent l'observer."""
    print(f"{_JOURNAL} {message}", flush=True)


def _diagnostic_reponse(corps: dict[str, Any], brut: str) -> str:
    """Les faits qui distinguent trois pannes que « réponse invalide » confond.

    `_completion` rend déjà le corps COMPLET ; rien de tout cela n'était
    seulement regardé. Or trois causes produisent la même sortie inexploitable
    et appellent trois corrections différentes :

    - le serveur a **détourné** la sortie. `mlx_lm.server` déduit les capacités
      du modèle de son vocabulaire et de son gabarit, jamais de son
      comportement : `<think>` et `<tool_call>` présents suffisent à installer
      des transitions d'état. Seul le texte en état normal alimente `content` ;
      le reste part dans `reasoning` ou `tool_calls`, et `finish_reason` vaut
      alors `stop` ou `tool_calls`, JAMAIS `length`. `content` est donc un
      préfixe coupé, avec une fin déclarée normale ;
    - le plafond de génération a été atteint mais mal déclaré ;
    - le modèle s'est réellement arrêté seul, et sa taille est en cause.

    On relève aussi `prompt_tokens` : une entrée qui frôle la fenêtre de
    contexte produirait une fin prématurée non signalée. Sans ce champ, ce cas
    resterait supposé écarté au lieu d'être fermé.
    """
    choix = corps.get("choices")
    premier = choix[0] if isinstance(choix, list) and choix else {}
    message = premier.get("message") if isinstance(premier, dict) else {}
    message = message if isinstance(message, dict) else {}
    usage = corps.get("usage") if isinstance(corps.get("usage"), dict) else {}
    detournements = [
        cle for cle in ("reasoning", "reasoning_content", "thinking", "tool_calls")
        if message.get(cle)
    ]
    return (
        f"finish_reason={premier.get('finish_reason')!r} "
        f"detournement={','.join(detournements) or 'aucun'} "
        f"prompt_tokens={usage.get('prompt_tokens')} "
        f"completion_tokens={usage.get('completion_tokens')} "
        f"len_contenu={len(brut)} "
        # La FIN, et pas seulement le début : un JSON coupé net se distingue
        # d'un JSON complet noyé dans de la prose par sa dernière ligne, jamais
        # par sa première. N'afficher que la tête a coûté un diagnostic faux.
        f"fin={brut[-120:]!r}"
    )


class MlxEngine:
    """Moteur local `mlx_lm` — implémentation de `LLMEngine` et de `unload()`.

    L'instance est créée à l'import de `core/llm.py` (elle est dans le registre)
    et ne doit donc RIEN faire de coûteux : ni import de `mlx_lm`, ni processus,
    ni requête. Tout est différé au premier appel.

    Les paramètres sont résolus à l'appel et non dans la signature
    (`port=None` puis `config.MLX_PORT`) : une valeur par défaut est liée à la
    définition, et un test qui change la configuration ne la verrait jamais.
    """

    name = "mlx"

    def __init__(
        self,
        *,
        model: Optional[str] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        delai_sonde: Optional[float] = None,
        delai_generation: Optional[float] = None,
        delai_demarrage: Optional[float] = None,
        max_tokens: Optional[int] = None,
        gabarit: Optional[dict] = None,
    ) -> None:
        # `chat_template_kwargs` envoyés à chaque requête (ex. `enable_thinking: False`
        # pour un modèle qui réfléchit par défaut, comme Granite 4.2).
        self._gabarit = dict(gabarit) if gabarit else None
        self._max_tokens_configure = max_tokens
        self._model_configure = model
        self._host_configure = host
        self._port_configure = port
        self._delai_sonde_configure = delai_sonde
        self._delai_generation_configure = delai_generation
        self._delai_demarrage_configure = delai_demarrage
        self._processus: Optional[subprocess.Popen] = None
        # Client SDK construit PARESSEUSEMENT et mémorisé avec l'URL qui l'a
        # produit : le construire ici lirait l'environnement à l'import de
        # `core/llm.py`, et le reconstruire à chaque appel jetterait le pool de
        # connexions. La clé d'URL le fait suivre `host`/`port`, résolus tard.
        self._client: Optional[openai.OpenAI] = None
        self._client_url: Optional[str] = None
        # Sérialise la SUPERVISION (démarrage, arrêt), pas les générations : deux
        # jobs peuvent tourner de front (`MAX_ACTIVE_JOBS`), et tenir le verrou
        # pendant une génération de plusieurs minutes les mettrait en file.
        self._verrou = threading.Lock()

    # ---------- Configuration, résolue tard ----------

    @property
    def model(self) -> str:
        return self._model_configure or config.MLX_LLM_MODEL

    @property
    def host(self) -> str:
        return self._host_configure or config.MLX_HOST

    @property
    def port(self) -> int:
        return self._port_configure if self._port_configure is not None else config.MLX_PORT

    @property
    def _delai_sonde(self) -> float:
        if self._delai_sonde_configure is not None:
            return self._delai_sonde_configure
        return config.MLX_SONDE_TIMEOUT

    @property
    def _delai_generation(self) -> float:
        if self._delai_generation_configure is not None:
            return self._delai_generation_configure
        return config.MLX_TIMEOUT

    @property
    def _max_tokens(self) -> int:
        return (
            self._max_tokens_configure
            if self._max_tokens_configure is not None
            else config.MLX_MAX_TOKENS
        )

    @property
    def _delai_demarrage(self) -> float:
        if self._delai_demarrage_configure is not None:
            return self._delai_demarrage_configure
        return config.MLX_DEMARRAGE_TIMEOUT

    def _url(self, chemin: str) -> str:
        return f"http://{self.host}:{self.port}{chemin}"

    def _sdk(self) -> openai.OpenAI:
        """Le client, construit une fois et refait si l'adresse a changé.

        `max_retries=0` est le réglage PORTEUR, pas une optimisation : voir
        l'entête du module. Le SDK réessaie deux fois par défaut, ce qui
        annulerait la décision « on ne réessaie pas contre un zombie, on le
        remplace » et multiplierait par trois l'attente d'un délai dépassé.

        `api_key` est un littéral inerte : `mlx_lm.server` n'authentifie rien,
        mais le SDK refuse de se construire sans. Une vraie clé n'a rien à faire
        sur une boucle locale, et l'y voir passer serait une fuite.

        `trust_env=False` est la garde de CONFIDENTIALITÉ de ce module, et elle
        n'est pas théorique. MESURÉ : sans elle, le SDK monte un `httpx.Client`
        par défaut, `httpx` lit `urllib.request.getproxies()` et route
        `http://127.0.0.1:8772/…` vers le proxy trouvé — httpx n'exempte PAS la
        boucle locale. Le corps complet de la requête, directive système et
        TRANSCRIPT compris, part alors chez ce proxy, et l'appel réussit : rien
        ne signale la fuite. Sur macOS c'est pire encore, `getproxies()` lit les
        Réglages Système en plus des variables d'environnement, donc un VPN
        d'entreprise ou un Proxyman suffit, sans qu'aucune variable ne soit
        posée ; et httpx ignore la liste d'exceptions du système, qui contient
        pourtant localhost.
        C'est inacceptable pour un produit local-first dont la matière est
        l'enregistrement personnel de ses utilisateurs.

        Les en-têtes d'organisation sont RETIRÉS, et par `Omit()` seulement.
        MESURÉ SUR LE FIL, contre un serveur qui note ce qu'il reçoit :

          sans rien             -> OpenAI-Organization: org-secret  (fuite)
          organization=""       -> OpenAI-Organization: ""          (vide, mais posé)
          default_headers=Omit  -> aucun en-tête                    (correct)

        Passer `organization=None` ne sert à RIEN : `openai/_client.py` fait
        `if organization is None: organization = os.environ.get("OPENAI_ORG_ID")`
        — le `None` explicite déclenche précisément le repli qu'on veut éviter.
        Vérifier la valeur dans `client.default_headers` ne prouve rien non
        plus : `Omit()` y reste visible et n'est retiré qu'à la construction de
        la requête. Seul le fil fait foi.
        """
        base = self._url("/v1")
        if self._client is None or self._client_url != base:
            # La construction est commune avec le serveur compatible OpenAI
            # (`llm_client_openai`) : les gardes ci-dessus valent pour les deux.
            self._client = client_sans_fuite(base, api_key="mlx-local-sans-authentification")
            self._client_url = base
        return self._client

    # Hébergé par l'utilisateur : sur un hôte distant, un « cloud privé » à l'écran.
    auto_heberge = True

    # ---------- Le processus enfant ----------

    def commande_serveur(self) -> list[str]:
        """`argv` du serveur enfant. PUBLIQUE parce qu'elle est TESTÉE.

        `--port` est le point du lot : le défaut de `mlx_lm.server` est 8080, un
        port de développement banal et très souvent pris. Une collision ne se
        voit pas — le serveur d'en face répond, et le moteur devient
        silencieusement faux.

        `sys.executable -m` et non l'entrée `mlx_lm.server` du PATH : le serveur
        doit tourner dans le MÊME interpréteur que l'application, sans quoi il
        chargerait un autre MLX que celui qu'on a figé.

        `--max-tokens` n'est pas un confort : le défaut de `mlx_lm.server` est
        **512 jetons**, et une requête qui omet `max_tokens` en hérite. Or
        `LLMEngine.chat()` n'a pas ce paramètre — le contrat ne l'expose pas, et
        Ollama n'en a pas besoin. MESURÉ sans ce drapeau : un compte-rendu de
        réunion ordinaire est coupé à 512 jetons et le moteur le refuse en
        conseillant d'augmenter une valeur que l'appelant ne peut pas toucher.

        `--prompt-cache-bytes` n'a AUCUN défaut chez `mlx_lm.server` : seul le
        NOMBRE de caches est borné, à dix. MESURÉ sur cinq
        comptes-rendus enchaînés, c'est une borne sans effet — dix caches de
        prompts à 24 074 jetons pèsent 8,97 Go, et la génération suivante meurt
        sur `[METAL] Insufficient Memory` sans jamais répondre. Voir
        `config.MLX_PROMPT_CACHE_BYTES` pour le détail de la mesure et le
        raisonnement sur la valeur.
        """
        return [
            sys.executable, "-m", "mlx_lm.server",
            "--model", self.model,
            "--host", self.host,
            "--port", str(self.port),
            "--max-tokens", str(self._max_tokens),
            "--prompt-cache-bytes", str(config.MLX_PROMPT_CACHE_BYTES),
        ]

    def _port_ouvert(self) -> bool:
        """Quelqu'un écoute-t-il ? Une connexion TCP, PAS une requête `/health`.

        On ne demande rien au serveur ici : la seule chose qu'on veut savoir est
        si le port est pris, pour ne pas lancer un second processus par-dessus.
        La vivacité, elle, ne se prouve que par une génération.
        """
        try:
            with socket.create_connection((self.host, self.port), timeout=0.5):
                return True
        except OSError:
            return False

    def _sonde_http_vivante(self) -> bool:
        """Le processus derrière le port RÉPOND-il ? `GET /v1/models`, jamais une génération.

        À NE PAS confondre avec `is_available` : celle-ci prouve la GÉNÉRATION et
        peut donc bloquer derrière un job en cours ; celle-là prouve seulement
        que le PROCESSUS n'est pas figé. Voir l'en-tête du module (« La SEULE
        interrogation de `/v1/models` ») : on ne lit que son ROUGE.

        `True` dès qu'une réponse HTTP arrive — n'importe quel code, y compris un
        404 d'un serveur étranger : ce qu'on teste est la vivacité du processus,
        pas la justesse de la route. `False` seulement si TOUTES les tentatives
        échouent au transport (timeout ou connexion coupée), car la conséquence
        d'un `False` est un arrêt forcé et on ne l'inflige pas sur un raté seul.

        `trust_env=False` comme dans `_sdk` : même sur une requête sans corps, ce
        module ne laisse jamais un proxy système capter une boucle locale.
        """
        url = self._url("/v1/models")
        for tentative in range(_SONDE_HTTP_TENTATIVES):
            try:
                with httpx.Client(trust_env=False, timeout=_SONDE_HTTP_DELAI) as client:
                    client.get(url)
                return True
            except httpx.HTTPError as exc:
                _journaliser(
                    f"sonde /v1/models sans réponse ({tentative + 1}/"
                    f"{_SONDE_HTTP_TENTATIVES}) sur le port {self.port} : "
                    f"{type(exc).__name__}")
        return False

    def _drainer(self, flux) -> None:
        """Recopie le `stderr` de l'enfant dans le journal, puis récolte son code.

        Sans ceci, l'échec de chargement — qui arrive AVANT toute requête — est
        invisible : le tube se remplit, l'enfant finit par bloquer, et le seul
        symptôme observable côté Memoglyph est une requête sans réponse.

        La récolte du code de sortie vit ICI parce que la fin du flux est le
        signal le plus précoce dont on dispose ; `_assurer_serveur` la refait de
        son côté pour le chemin synchrone.
        """
        try:
            for ligne in iter(flux.readline, ""):
                texte = ligne.rstrip()
                if texte:
                    _journaliser(f"serveur : {texte}")
        except (OSError, ValueError):  # tube fermé sous nos pieds
            pass

    def _verifier_paquet(self) -> None:
        """`mlx-lm` est-il là ? `find_spec` LOCALISE sans exécuter le code natif.

        Même choix que `run_ml_tests.py` pour `mlx_whisper`, et pour la même
        raison : on ne veut pas payer un import MLX dans le processus PARENT
        alors que c'est l'ENFANT qui doit tenir les poids. C'est aussi pourquoi
        ce module n'importe `mlx_lm` nulle part — ni au niveau module, ni dans
        une méthode.

        Méthode à part parce qu'elle est le seul point de ce module qui dépende
        de l'environnement : les tests la substituent pour exercer la
        supervision du processus enfant sans exiger le paquet.
        """
        if importlib.util.find_spec("mlx_lm") is None:
            raise MlxLLMError(
                "Le paquet `mlx-lm` est absent de cet environnement. "
                "Installe-le :  uv sync   (il est déclaré dans pyproject.toml)."
            )

    def _demarrer(self) -> None:
        """Lance l'enfant et attend l'ouverture du port, en surveillant sa mort."""
        self._verifier_paquet()

        _journaliser(f"démarrage du serveur sur {self.host}:{self.port} — {self.model}")
        try:
            processus = subprocess.Popen(  # noqa: S603 — argv construit ici, sans shell
                self.commande_serveur(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise MlxLLMError(
                f"Le serveur mlx_lm n'a pas pu être lancé ({type(exc).__name__})."
            ) from exc

        self._processus = processus
        self._inscrire_propriete(processus.pid)
        threading.Thread(
            target=self._drainer, args=(processus.stderr,),
            name="memoglyph-mlx-stderr", daemon=True,
        ).start()

        limite = time.monotonic() + self._delai_demarrage
        while time.monotonic() < limite:
            code = processus.poll()
            if code is not None:
                # Code de sortie SURVEILLÉ : l'enfant est mort au démarrage
                # (modèle introuvable, port pris, paquet cassé). Son `stderr`
                # est déjà dans le journal ; on le dit aussi à l'appelant.
                #
                # Le témoin est retiré À LA MAIN sur cette sortie, et c'est la
                # seule qui l'exige : les deux autres passent par `_arreter()`,
                # qui s'en charge. L'y déléguer ici serait pourtant faux — il
                # `terminate()` un processus déjà mort, et son journal
                # annoncerait « mémoire rendue » pour un serveur qui n'a jamais
                # tenu un octet.
                #
                # Sans ce retrait, tout enfant mort-né laissait sa preuve de
                # propriété derrière lui. Aucun danger — `_orphelin_nous_appartient`
                # exige un PID vivant, donc un témoin périmé ne fait tuer
                # personne — mais un dossier de preuves qui se remplit de preuves
                # mortes finit par ne plus être lu, et c'est sa lisibilité qui
                # fait toute la valeur de ce mécanisme.
                self._processus = None
                self._oublier_propriete()
                raise MlxLLMError(
                    f"Le serveur mlx_lm s'est arrêté au démarrage (code {code}). "
                    f"Voir le journal {_JOURNAL} pour la sortie d'erreur."
                )
            if self._port_ouvert():
                self._prouver_la_generation()
                return
            time.sleep(0.1)

        self._arreter()
        raise MlxLLMError(
            f"Le serveur mlx_lm n'a pas ouvert le port {self.port} en "
            f"{self._delai_demarrage:.0f} s."
        )

    def _prouver_la_generation(self) -> None:
        """Le serveur qu'on vient de lancer GÉNÈRE, ou il est tué sur-le-champ.

        C'est ici, et NULLE PART AILLEURS, que le cas zombie est attrapé.

        MESURÉ contre un vrai `mlx_lm.server` lancé sur un modèle introuvable :
        le port s'ouvre en 4 s, `/health` répond `200 {"status":"ok"}`, et
        `POST /v1/chat/completions` **PEND** — le client sort en
        `APITimeoutError` (cause `ReadTimeout`) au bout de son délai. Le serveur
        ne raccroche JAMAIS.

        Cette mesure a invalidé la conception précédente. Le moteur croyait
        reconnaître un zombie à un raccrochage et le tuait à ce signal ; or ce
        signal n'existe pas, et le zombie produisait un délai dépassé —
        c'est-à-dire exactement ce que produit aussi une génération saine mais
        longue. Traiter les deux pareil, c'était soit tuer des serveurs en plein
        travail, soit ne jamais remplacer un cadavre. Les deux ont été écrits.

        Ce qui les sépare n'est pas l'exception, c'est le MOMENT :

        - AU DÉMARRAGE, on demande DEUX jetons. Un serveur chargé les rend en
          quelques millisecondes ; la seule inconnue est le temps de chargement
          des poids. Ne pas y arriver dans `_delai_sonde` signifie que le
          thread de chargement est mort. On tue, et l'appelant l'apprend tout
          de suite plutôt qu'au bout de `MLX_TIMEOUT` au milieu d'un
          compte-rendu.

        - EN GÉNÉRATION, la durée légitime est bornée par le travail demandé, pas
          par nous. Un délai dépassé n'y prouve rien, et `_completion` ne tue
          donc pas.

        Le coût est d'une génération de deux jetons par serveur lancé, une seule
        fois dans sa vie. `is_available()` refera la sienne : elle doit aussi
        pouvoir juger un serveur qu'on n'a PAS démarré, que `_assurer_serveur`
        laisse passer sans jamais entrer ici.
        """
        try:
            # Sonde de vivacité : c'est une VRAIE génération (2 jetons), donc un vrai appel
            # mesuré. On la TAGUE `liveness_probe` (dimension `stage`) plutôt que de la laisser
            # anonyme : son usage reste visible et honnête, mais nettement séparable du travail
            # de rapport dans toute agrégation de coût. On ne supprime jamais un usage réel.
            with telemetry.stage("liveness_probe"):
                corps = self._completion(
                    "Réponds par un mot.", "Dis OK.",
                    max_tokens=2, delai=self._delai_sonde,
                )
            contenu = self._contenu(corps)
        except Exception as exc:  # noqa: BLE001 — on requalifie et on tue
            self._arreter()
            raise MlxLLMError(
                f"Le serveur mlx_lm a ouvert le port {self.port} mais n'a pas su "
                f"générer deux jetons en {self._delai_sonde:.0f} s "
                f"({type(exc).__name__}) : son thread de chargement est mort — "
                f"`/health` répondrait pourtant 200. Modèle introuvable ou "
                f"illisible ? Voir {_JOURNAL} pour la sortie du serveur. Serveur "
                f"arrêté."
            ) from exc
        if not contenu:
            self._arreter()
            raise MlxLLMError(
                f"Le serveur mlx_lm du port {self.port} a répondu sans générer un "
                f"seul jeton. Serveur arrêté."
            )

    # ---------- La propriété d'un serveur, et sa PREUVE ----------
    #
    # Règle : « ownership uniquement ».
    # Memoglyph n'arrête automatiquement un serveur que s'il PROUVE l'avoir
    # lancé. Servir le même modèle sur le même port ne suffit pas — un
    # utilisateur peut lancer cette combinaison exacte à la main, et la tuer
    # sous ses pieds serait pire que la panne qu'on répare.
    #
    # Le PID SEUL ne prouve rien : il se recycle, et sur une machine qui tourne
    # depuis des jours, le PID d'hier appartient à quelqu'un d'autre aujourd'hui.
    # On enregistre donc PID **et heure de démarrage**, et l'on exige les deux.

    @property
    def _temoin_propriete(self) -> Path:
        """Un fichier par PORT : deux moteurs sur deux ports sont indépendants."""
        return config.CACHE_DIR / f"mlx-server-{self.port}.owner"

    @staticmethod
    def _demarrage_du_processus(pid: int) -> Optional[str]:
        """L'heure de démarrage vue par le système, ou None si le PID est mort.

        C'est elle qui distingue « notre processus » d'« un PID recyclé ». Sans
        elle, un numéro réutilisé par un travail de l'utilisateur serait pris
        pour le nôtre — exactement le risque que la décision écarte.
        """
        try:
            sortie = subprocess.run(  # noqa: S603 — argv fixe, sans shell
                ["/bin/ps", "-o", "lstart=", "-p", str(pid)],
                capture_output=True, text=True, timeout=5,
            )
        except Exception:  # noqa: BLE001 — un contrôle ne doit jamais faire échouer
            return None
        valeur = sortie.stdout.strip()
        return valeur or None

    def _inscrire_propriete(self, pid: int) -> None:
        """Best-effort : ne pas pouvoir écrire le témoin ne casse pas le démarrage.

        La conséquence d'un témoin manquant est CONNUE et acceptable — un
        orphelin futur tombera dans la branche « propriété inconnue » et sera
        refusé au lieu d'être tué. Jamais l'inverse.
        """
        try:
            self._temoin_propriete.parent.mkdir(parents=True, exist_ok=True)
            self._temoin_propriete.write_text(
                json.dumps({
                    "pid": pid,
                    "demarrage": self._demarrage_du_processus(pid),
                    "port": self.port,
                    "model": self.model,
                }),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            _journaliser(f"témoin de propriété non écrit : {type(exc).__name__}")

    def _orphelin_nous_appartient(self) -> Optional[int]:
        """Le PID de l'orphelin s'il est PROUVÉ nôtre, sinon None.

        Trois conditions, toutes nécessaires : un témoin lisible, un PID encore
        vivant, et la MÊME heure de démarrage. Une seule qui manque et l'on
        répond None — c'est-à-dire « propriété inconnue », donc on ne tue pas.
        """
        try:
            temoin = json.loads(self._temoin_propriete.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — absent, illisible, corrompu : inconnu
            return None
        pid = temoin.get("pid")
        if not isinstance(pid, int) or temoin.get("port") != self.port:
            return None
        demarrage = self._demarrage_du_processus(pid)
        if demarrage is None or demarrage != temoin.get("demarrage"):
            # PID mort, ou recyclé par un AUTRE processus. Dans les deux cas on
            # ne tient pas la preuve.
            return None
        return pid

    def _recuperer_orphelin(self) -> bool:
        """Tue l'orphelin dont la propriété est prouvée. Rend False sinon.

        Le cas réel qui a motivé ceci : un traitement meurt en cours, son
        serveur survit avec PPID 1 et garde le port. Le traitement suivant le
        trouve, échoue, et n'a AUCUN moyen de s'en sortir — mesuré trois fois
        en douze heures, dont un mode local resté mort 11 heures.

        Aucun `finally` ne peut couvrir ce cas : un `SIGKILL` ne l'exécute pas.
        Seule une preuve écrite AVANT survit à la mort du parent.
        """
        pid = self._orphelin_nous_appartient()
        if pid is None:
            return False
        _journaliser(f"orphelin {pid} reconnu comme nôtre sur {self.port} — arrêt")
        try:
            os.kill(pid, signal.SIGTERM)
            for _ in range(50):
                if self._demarrage_du_processus(pid) is None:
                    break
                time.sleep(0.1)
            else:
                os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # mort entre-temps, c'est le résultat voulu
        except Exception as exc:  # noqa: BLE001
            _journaliser(f"arrêt de l'orphelin impossible : {type(exc).__name__}")
            return False
        self._oublier_propriete()
        return True

    def _oublier_propriete(self) -> None:
        try:
            self._temoin_propriete.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

    def _recuperer_serveur_fige(self) -> bool:
        """Tue un serveur NON prouvé nôtre, mais SEULEMENT s'il est démontrablement FIGÉ.

        La branche que « ownership uniquement » laissait sans issue :
        un serveur que nous n'avons pas lancé, dont le témoin de propriété est
        absent ou périmé (cache effacé, session tuée sans `finally`), qui tient
        le port en `LISTEN` sans plus rien servir — `/v1/models` compris.

        On ne tue ICI que sur la preuve d'un JAM DU PROCESSUS ENTIER : un
        `/v1/models` muet, état qu'aucun serveur vivant ne produit, même saturé
        (voir l'en-tête et `_sonde_http_vivante`). Un serveur étranger SAIN — ou
        un zombie « thread de chargement mort » resté vert — passe la sonde et
        n'est PAS touché : il retombe dans le refus de `_assurer_serveur`. C'est
        la limite EXACTE de « ownership uniquement » : on ne retire qu'un cadavre
        qui bloque le port, jamais un serveur d'autrui qui pourrait travailler.

        Le motif d'arrêt est celui de `_recuperer_orphelin` : `SIGTERM`, puis
        `SIGKILL` si le processus survit — un jam natif peut ignorer le premier,
        jamais le second.
        """
        if self._sonde_http_vivante():
            return False
        pids = self._pids_du_port()
        if not pids:
            # Figé mais innommable (course avec sa mort, permission `lsof`) : on
            # ne tue pas ce qu'on ne peut pas désigner. Le refus prend le relais.
            return False
        _journaliser(
            f"serveur inconnu FIGÉ sur le port {self.port} (aucune réponse "
            f"/v1/models) — arrêt forcé de {pids}")
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
                for _ in range(50):
                    if self._demarrage_du_processus(pid) is None:
                        break
                    time.sleep(0.1)
                else:
                    os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # mort entre-temps, c'est le résultat voulu
            except Exception as exc:  # noqa: BLE001
                _journaliser(
                    f"arrêt du serveur figé {pid} impossible : {type(exc).__name__}")
                return False
        return True

    def _pids_du_port(self) -> list[int]:
        """Les PID qui ÉCOUTENT sur notre port, via `lsof`. Vide si aucun, ou si `lsof` échoue.

        `-sTCP:LISTEN` restreint aux SERVEURS et exclut les clients connectés :
        on ne veut nommer que celui qui tient le port, jamais une requête en
        cours. Un relevé qui échoue (lsof absent, timeout) rend une liste vide,
        pas une exception — nommer un PID est un service, pas une garantie.
        """
        try:
            sortie = subprocess.run(  # noqa: S603 — argv fixe, sans shell
                ["/usr/sbin/lsof", "-nP", f"-iTCP:{self.port}", "-sTCP:LISTEN", "-t"],
                capture_output=True, text=True, timeout=5,
            )
        except Exception:  # noqa: BLE001 — un relevé n'est jamais une panne
            return []
        pids: list[int] = []
        for jeton in sortie.stdout.split():
            try:
                pids.append(int(jeton))
            except ValueError:
                pass
        return pids

    def _refus_port_occupe(self) -> MlxLLMError:
        """Le refus, quand la propriété n'est PAS prouvée — et il donne la sortie.

        Le message d'avant affirmait « Serveur arrêté ; le prochain appel en
        démarrera un neuf ». Les deux moitiés étaient fausses : rien n'était
        arrêté, et l'appel suivant retombait sur le même serveur. Promettre une
        réparation qui n'a pas lieu est pire que d'avouer la panne — l'utilisateur
        relance indéfiniment au lieu de chercher.
        """
        pids = self._pids_du_port()
        occupant = f" (PID {', '.join(str(p) for p in pids)})" if pids else ""
        return MlxLLMError(
            f"Le port {self.port} est occupé par un serveur que Memoglyph n'a pas "
            f"lancé{occupant}. Il n'est PAS arrêté automatiquement : sa propriété "
            f"n'est pas prouvée, et l'arrêter pourrait tuer un processus qui ne "
            f"nous appartient pas. Libère-le à la main "
            f"(lsof -nP -iTCP:{self.port} -sTCP:LISTEN) ou change "
            f"MEMOGLYPH_MLX_PORT."
        )

    def _arreter(self) -> bool:
        """Tue l'enfant qu'on possède. Ne lève JAMAIS, et le dit par son retour.

        Ne touche pas à un serveur qu'on n'a pas lancé : sur un port partagé,
        tuer le processus d'autrui serait pire que le symptôme.
        """
        processus, self._processus = self._processus, None
        if processus is None:
            return False
        try:
            processus.terminate()
            try:
                processus.wait(timeout=5)
            except subprocess.TimeoutExpired:
                processus.kill()
                processus.wait(timeout=5)
        except Exception as exc:  # noqa: BLE001 — libérer ne doit jamais faire échouer
            _journaliser(f"arrêt du serveur impossible : {type(exc).__name__}")
            return False
        self._oublier_propriete()
        _journaliser("serveur arrêté, mémoire rendue")
        return True

    def _assurer_serveur(self) -> None:
        """Un serveur EST là, ou on le lance. Sous verrou : deux jobs de front."""
        with self._verrou:
            processus = self._processus
            if processus is not None and processus.poll() is not None:
                # Code de sortie surveillé aussi sur le chemin synchrone : sans
                # ce relevé, un enfant mort entre deux appels se lirait comme un
                # simple « port fermé » et l'on perdrait la cause.
                _journaliser(
                    f"le serveur s'est arrêté seul (code {processus.returncode}) — relance")
                self._processus = None
            if self._port_ouvert():
                if self._processus is not None:
                    return  # notre serveur, en marche : rien à faire
                # Port occupé, et aucun enfant à nous. TROIS cas, et deux seuls
                # autorisent à tuer — « ownership uniquement », étendue
                # au seul cadavre qui bloque le port :
                #
                #   1. prouvé nôtre        → `_recuperer_orphelin`      → tué + remplacé
                #   2. non nôtre mais FIGÉ → `_recuperer_serveur_fige`  → cadavre retiré + remplacé
                #   3. non nôtre et VIVANT → refus (sain, saturé, ou zombie vert : intouchable)
                #
                # L'ordre du `or` court-circuité EST la priorité de la preuve :
                # la propriété d'abord (elle ne sonde rien), le jam ensuite (il
                # coûte un `GET /v1/models`, inutile si l'orphelin est déjà nôtre).
                #
                # Avant, on RETOURNAIT ici : le moteur parlait à un serveur
                # inconnu en le croyant sien. Sur un orphelin, la génération
                # échouait sans issue, indéfiniment.
                if not (self._recuperer_orphelin() or self._recuperer_serveur_fige()):
                    raise self._refus_port_occupe()
                # Le port met un instant à se libérer après la mort du
                # processus : démarrer trop tôt ferait échouer la liaison de
                # l'enfant neuf, et l'on rendrait une panne pour un succès.
                limite = time.monotonic() + 5.0
                while self._port_ouvert() and time.monotonic() < limite:
                    time.sleep(0.1)
            self._demarrer()

    # ---------- Le dialogue HTTP ----------

    def _completion(
        self,
        system: str,
        user: str,
        *,
        max_tokens: Optional[int],
        delai: float,
        temperature: float = 0.2,
        seed: Optional[int] = None,
    ) -> dict[str, Any]:
        """Une complétion. « Aucune réponse » a DEUX causes, qu'on ne confond pas.

        Le modèle est NOMMÉ dans la requête : `mlx_lm` charge le nom demandé, si
        bien qu'une génération réussie a bien eu lieu avec lui.

        Ce bloc attrapait `httpx.TransportError` d'un seul tenant et TUAIT le
        serveur. C'était destructeur. La correction suivante a séparé délai
        dépassé et raccrochage, et n'a plus tué que sur le second — c'était
        faux dans l'autre sens, et pour une raison qui n'a été MESURÉE
        qu'après : le zombie mlx_lm ne raccroche jamais, il PEND (voir
        `_prouver_la_generation`). Cette version-là ne tuait donc plus aucun
        zombie.

        Ce qui vaut ici, désormais :

        - `APITimeoutError` — le serveur a accepté la connexion et n'a rien
          rendu dans le délai. On ne tue PAS. Un zombie donne ce symptôme, mais
          une génération longue et parfaitement saine aussi, et rien dans cette
          exception ne les sépare. Le cas zombie, lui, est déjà écarté :
          `_prouver_la_generation` a exigé deux jetons au démarrage, donc tout
          serveur qui arrive jusqu'ici a DÉJÀ généré.

        - `APIConnectionError` non-timeout — la connexion refusée ou coupée.
          Ce n'est PAS le zombie mesuré ; c'est un serveur qui meurt en cours
          de route, ou un tiers sur notre port. Rien ne le réparera : on tue,
          et le prochain appel repart sur un processus neuf.

        L'ordre des deux `except` reste porteur, `APITimeoutError` héritant de
        `APIConnectionError` exactement comme `httpx.TimeoutException` héritait
        de `httpx.TransportError`. Le piège a survécu au changement de client.
        """
        options: dict[str, Any] = {}
        if max_tokens is not None:
            options["max_tokens"] = max_tokens
        # `seed` est RÉELLEMENT honoré, contrairement à `response_format` :
        # `mlx_lm/server.py` le VALIDE (`self._validate("seed", int)`) puis
        # l'applique (`mx.random.seed(args.seed)`). La distinction est tout le
        # sujet — un paramètre accepté n'est pas un paramètre appliqué.
        #
        # Effet de bord documenté dans le serveur : une requête avec `seed`
        # n'est plus « batchable » (`is_batchable and args.seed is None`). Elle
        # est donc servie seule, ce qui est sans conséquence pour nous — on
        # n'envoie jamais qu'une requête à la fois.
        if seed is not None:
            options["seed"] = seed
        if self._gabarit:
            options["extra_body"] = {"chat_template_kwargs": dict(self._gabarit)}

        _t0 = time.perf_counter()
        try:
            reponse = self._sdk().chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                stream=False,
                timeout=delai,
                **options,
            )
        except openai.APITimeoutError as exc:
            # AVANT `APIConnectionError`, DONT IL HÉRITE : l'ordre de ces deux
            # `except` EST la distinction, et l'inverser rétablit le défaut qui
            # détruisait des serveurs sains. On ne tue TOUJOURS PAS sur un timeout
            # NU : une génération saine mais longue le produit à l'identique.
            #
            # RAFFINEMENT : on peut désormais poser UNE question
            # indépendante que le timeout, seul, ne pose pas — le PROCESSUS
            # répond-il encore ? `/v1/models` est servi sur son propre thread sans
            # toucher le GPU (voir l'en-tête) : une génération longue mais saine y
            # répond, un processus figé non. Un `/v1/models` muet prouve donc un
            # jam TOTAL, jamais un simple travail long — et LÀ SEULEMENT on retire
            # le serveur, pour que l'appel suivant reparte à neuf au lieu de
            # repayer `MLX_TIMEOUT` sur un cadavre. La sonde ne coûte rien au
            # chemin sain : elle ne tourne que sur ce timeout, déjà lent.
            if not self._sonde_http_vivante():
                _journaliser(
                    f"timeout ET /v1/models muet sur le port {self.port} : jam du "
                    f"processus entier — arrêt")
                # `_processus` est le nôtre dès qu'on arrive ici (aucun chemin
                # n'atteint `_completion` sans un `_assurer_serveur` réussi, qui
                # ou bien réutilise notre enfant, ou bien en démarre un). On
                # l'arrête par son handle ; le repli `lsof` ne sert qu'à une
                # course rarissime où le handle aurait déjà été rendu.
                if not self._arreter():
                    self._recuperer_serveur_fige()
                raise MlxLLMError(
                    f"Le serveur mlx_lm du port {self.port} est FIGÉ : ni "
                    f"génération en {delai:.0f} s, ni réponse à `/v1/models` "
                    f"({type(exc).__name__}). Ce n'est pas une génération longue — "
                    f"le processus entier ne répond plus. Il a été arrêté ; le "
                    f"prochain appel en démarrera un neuf."
                ) from exc
            raise MlxLLMError(
                f"Le serveur mlx_lm n'a rien rendu en {delai:.0f} s sur le port "
                f"{self.port} ({type(exc).__name__}), mais `/v1/models` répond "
                f"encore : le processus est VIVANT. Soit la génération est plus "
                f"longue que ce délai — augmente MEMOGLYPH_MLX_TIMEOUT — soit seul "
                f"son thread de génération est mort. Il est LAISSÉ EN VIE : on ne "
                f"détruit pas un serveur qui travaille peut-être."
            ) from exc
        except openai.APIConnectionError as exc:
            # Le serveur a raccroché ou refusé la connexion. C'est le cas zombie
            # MESURÉ, et il est immédiat.
            self._arreter()
            raise MlxLLMError(
                f"Le serveur mlx_lm a raccroché sans répondre "
                f"({type(exc).__name__}) sur le port {self.port}. Panne moteur : "
                f"le thread de chargement est mort et le serveur est zombie — "
                f"`/health` répondrait 200. Serveur arrêté ; le prochain appel "
                f"en démarrera un neuf."
            ) from exc
        except openai.APIStatusError as exc:
            # Une VRAIE réponse HTTP, hors 2xx : serveur étranger sans la route,
            # modèle introuvable (404 du serveur mlx_lm), requête refusée. Ni un
            # zombie ni un succès — donc ni tuer, ni se taire.
            #
            # La CAUSE est dans le corps, et le serveur ne l'écrit dans aucun
            # journal : sans ce relevé elle est perdue des deux côtés. Elle va
            # au JOURNAL et non dans le message d'exception — le corps n'est pas
            # composé par nous, et la frontière de `core/llm.py` existe
            # précisément pour ne jamais republier un texte d'origine inconnue.
            _journaliser(f"réponse {exc.status_code} du serveur : {str(exc)[:400]}")
            raise MlxLLMError(
                f"Le serveur mlx_lm a répondu {exc.status_code} sur le port "
                f"{self.port}. Cause détaillée dans le journal {_JOURNAL}."
            ) from exc
        except openai.APIError as exc:
            # Filet APRÈS les trois cas nommés, pour tout ce que le SDK peut
            # encore lever — `APIResponseValidationError` en tête, qu'un 200
            # au corps illisible produit. Sans lui, ces pannes traversaient le
            # moteur en exception non typée : `core/llm.py` les aurait bien
            # normalisées, mais en perdant le seul diagnostic utile, et
            # `is_available()` les aurait comptées comme n'importe quel bug.
            raise MlxLLMError(
                f"Le serveur du port {self.port} a rendu une réponse hors "
                f"contrat ({type(exc).__name__}) — ce n'est probablement pas un "
                f"serveur mlx_lm."
            ) from exc

        # Retour au dictionnaire DÈS LA FRONTIÈRE : tout l'aval — détection d'un
        # modèle à raisonnement, refus d'une réponse tronquée, extraction du
        # contenu — travaille sur la forme brute, et continue de fonctionner si
        # ce client change encore. MESURÉ : `model_dump()` préserve les champs
        # hors norme OpenAI, `reasoning` compris, sans quoi la détection du
        # modèle pensant serait devenue aveugle en silence.
        # Un `if not isinstance(corps, dict)` figurait ici. Il ne pouvait JAMAIS
        # être vrai — `model_dump()` d'un modèle pydantic rend toujours une
        # table — et il donnait l'apparence d'une garde là où il n'y en avait
        # aucune : le cas qu'il prétendait couvrir sort en amont, à l'appel de
        # `model_dump()` lui-même, et c'est l'`except openai.APIError` ci-dessus
        # qui le rattrape. Relevé en revue ; retiré plutôt que déplacé.
        #
        # Télémétrie FinOps (best-effort, hors contrat) : le serveur local
        # `mlx_lm` compte de VRAIS jetons (`usage.prompt_tokens`/`completion_tokens`)
        # jusqu'ici jetés — usage MESURÉ (`local_server`), pas estimé.
        _usage = getattr(reponse, "usage", None)
        telemetry.record_model_call(
            provider="mlx", model=self.model,
            latency_s=time.perf_counter() - _t0,
            status="ok",
            usage=_usage,
            usage_source="local_server" if _usage is not None else None,
        )
        return reponse.model_dump()

    @staticmethod
    def _contenu(corps: dict[str, Any]) -> str:
        """Le texte généré, ou une `MlxLLMError` — jamais une chaîne vide subie.

        Le cas « pas de `content` » a deux causes, et les confondre a coûté des
        serveurs sains. MESURÉ sur Qwen3.5-9B-4bit : un modèle à raisonnement
        rend `content: null` et met sa réflexion dans un champ `reasoning` hors
        norme OpenAI. La sonde de vivacité y lisait « aucune génération », donc
        « serveur zombie », et TUAIT un serveur qui répondait parfaitement.

        Le modèle par défaut ne réfléchit pas (voir `config.MLX_LLM_MODEL`), mais
        rien n'empêche d'en configurer un qui le fasse. On le NOMME donc au lieu
        de le diagnostiquer à tort : la configuration est en cause, pas le
        serveur, et le message doit envoyer au bon endroit.
        """
        choix = corps.get("choices")
        if not isinstance(choix, list) or not choix:
            raise MlxLLMError("Réponse mlx_lm sans `choices` : aucune génération.")
        message = choix[0].get("message") if isinstance(choix[0], dict) else None
        contenu = message.get("content") if isinstance(message, dict) else None
        if not isinstance(contenu, str):
            pensees = [
                cle for cle in ("reasoning", "reasoning_content", "thinking")
                if isinstance(message, dict) and message.get(cle)
            ]
            if pensees:
                raise MlxLLMError(
                    f"Le modèle configuré RÉFLÉCHIT : sa sortie est dans "
                    f"{pensees[0]!r} et `content` est vide. Le serveur va bien — "
                    f"c'est MEMOGLYPH_MLX_LLM_MODEL qui est en cause. Choisis un "
                    f"modèle sans raisonnement (la famille Qwen3-*-Instruct-2507 "
                    f"n'en fait pas)."
                )
            raise MlxLLMError("Réponse mlx_lm sans contenu textuel.")
        return contenu

    @staticmethod
    def _refuser_une_reponse_tronquee(corps: dict[str, Any]) -> None:
        """Coupée à `max_tokens` ? Alors elle est INCOMPLÈTE, et on le dit.

        Pendant exact du contrôle `done_reason == "length"` d'Ollama, et pour la
        même raison : un contenu tronqué qu'on rendrait comme un contenu normal
        se retrouverait dans un compte-rendu, amputé, sans un signal.

        Volontairement HORS de `_contenu`, que la sonde de `is_available()`
        partage : celle-ci demande deux jetons et récolte donc toujours
        `finish_reason == "length"`. Le contrôle y aurait rendu le moteur
        définitivement indisponible — un verrou qui condamne ce qu'il protège.

        ## Le message s'adresse à qui le reçoit

        Il disait « Augmente `max_tokens` ». Or ce message REMONTE : la route du
        compte-rendu le rend tel quel dans un 503, à un utilisateur qui n'a
        aucun moyen de toucher ce paramètre — il n'est exposé nulle part dans
        l'interface.

        Le dépôt avait déjà relevé cet anti-motif pour le plafond de 512 jetons
        du serveur : « un message qui conseille d'augmenter une valeur que
        l'appelant ne peut pas toucher ». Il avait été réintroduit ici.

        Ce que l'utilisateur PEUT faire, lui, c'est basculer sur le moteur
        cloud, ou traiter un document plus court. Le compte de jetons reste
        dans le message, mais pour le diagnostic — pas comme une consigne.
        """
        choix = corps.get("choices")
        premier = choix[0] if isinstance(choix, list) and choix else None
        if not (isinstance(premier, dict) and premier.get("finish_reason") == "length"):
            return

        usage = corps.get("usage") if isinstance(corps.get("usage"), dict) else {}
        produits = usage.get("completion_tokens")
        compte = f" après {produits} jetons produits" if produits else ""

        message = premier.get("message") if isinstance(premier, dict) else None
        tronque = message.get("content") if isinstance(message, dict) else None
        tronque = tronque if isinstance(tronque, str) else ""
        boucle = _empreinte_de_boucle(tronque)

        if boucle:
            periode, repetitions, motif = boucle
            cause = (
                f"Le modèle local a BOUCLÉ{compte} : il a répété {repetitions} "
                f"fois de suite un motif de {periode} caractères, jusqu'à "
                f"épuiser son budget sans fermer sa réponse. Le document n'est "
                f"pas en cause — le raccourcir n'y changerait rien. Bascule sur "
                f"Mistral pour celui-ci."
            )
        else:
            cause = (
                f"Le modèle local a atteint sa limite de sortie{compte} sans "
                f"avoir terminé : la réponse est amputée et ne sera pas "
                f"utilisée. Bascule sur Mistral pour celui-ci, ou traite un "
                f"extrait plus court."
            )

        erreur = MlxLLMOutputLimitError(cause)
        # La sortie amputée est CONSERVÉE sur l'exception, jamais rendue comme
        # un contenu valide. On la jetait, et il fallait la reproduire pour
        # savoir ce qui s'était passé — sept minutes à chaque fois.
        erreur.contenu_tronque = tronque
        erreur.boucle = boucle
        _journaliser(
            f"sortie tronquée : {len(tronque)} caractères, "
            + (f"BOUCLE période={boucle[0]} ×{boucle[1]} motif={boucle[2][:60]!r}"
               if boucle else "aucune boucle terminale détectée")
        )
        raise erreur

    # ---------- Le contrat ----------

    def is_available(self, *, api_key: Optional[str] = None) -> bool:
        """Le moteur peut-il répondre ? Prouvé par une GÉNÉRATION, jamais par `/health`.

        `api_key` est ignoré : un serveur local n'en demande pas. L'argument
        appartient au contrat commun, pas au moteur.

        Ne lève jamais — le contrat dit qu'un moteur RÉPOND faux, et la route de
        santé de l'API appelle ceci sans `try`.
        """
        try:
            self._assurer_serveur()
            # Deux jetons, pas un : un modèle qui n'en produit qu'un peut rendre
            # un fragment vide (espace initial d'un tokenizer BPE), et l'on
            # conclurait à tort à l'absence de génération.
            #
            # Sonde de vivacité TAGUÉE `liveness_probe` : `is_available` est
            # appelée dans le tronc de génération (run_stages), donc sa génération
            # de contrôle est enregistrée. On la catégorise explicitement — usage
            # réel, visible, mais séparable du travail de rapport — plutôt que
            # `stage=None`. On ne supprime jamais un usage réel.
            with telemetry.stage("liveness_probe"):
                corps = self._completion(
                    "Réponds par un mot.", "Dis OK.",
                    max_tokens=2, delai=self._delai_sonde,
                )
            contenu = self._contenu(corps)
        except Exception as exc:  # noqa: BLE001 — le contrat interdit de lever
            _journaliser(f"indisponible : {type(exc).__name__} — {exc}")
            return False

        if not contenu:
            _journaliser("indisponible : le serveur a répondu sans générer un seul jeton")
            return False

        servi = corps.get("model")
        if servi != self.model:
            # Voir l'entête du module : chez mlx_lm ce champ est un ÉCHO, donc
            # ce contrôle ne dit rien contre lui — il discrimine un serveur
            # ÉTRANGER assis sur notre port, qui annoncerait SON modèle.
            _journaliser(
                f"indisponible : le port {self.port} annonce le modèle {servi!r}, "
                f"attendu {self.model!r} — ce n'est pas notre serveur")
            return False
        return True

    def pret_declare(self) -> bool:
        """Le paquet est-il là ? Rien de plus, et surtout rien de démarré.

        `_verifier_paquet` s'appuie sur `find_spec`, qui LOCALISE `mlx_lm` sans
        exécuter son code natif : aucun poids chargé, aucun processus, aucun
        Metal touché. C'est la même précaution que `run_ml_tests.py`.

        Ce que cette réponse ne dit PAS, et qu'il faut assumer : le premier
        usage réel démarrera un serveur, ce qui prend quelques secondes. Un
        moteur déclaré prêt peut donc encore échouer — sur un modèle
        introuvable, un port pris par un autre serveur, une machine sans Metal.
        `is_available()` reste seul à prouver.

        On ne regarde délibérément PAS si le port est déjà ouvert : un serveur
        chaud rendrait `True` et un serveur froid `False`, alors que le moteur
        est utilisable dans les deux cas. La sonde décrirait l'instant plutôt
        que l'équipement, et l'interface clignoterait au gré des déchargements.
        """
        try:
            self._verifier_paquet()
        except Exception:  # noqa: BLE001 — le contrat interdit de lever
            return False
        return True

    def modele_servi(self, model: Optional[str] = None) -> str:
        """Toujours `self.model` — cet argument ne peut RIEN changer ici.

        `_refuser_un_autre_modele` lève sur toute demande différente : un
        serveur `mlx_lm` sert le modèle avec lequel il a été lancé, et prétendre
        en servir un autre produirait une provenance fausse. L'argument est donc
        ignoré, et ce n'est pas un raccourci — un `model or self.model` rendrait
        ici le nom d'un modèle que le prochain appel va REFUSER, donc une
        provenance qui décrit ce qui n'aura pas lieu.
        """
        return self.model

    def chat(
        self,
        system: str,
        user: str,
        *,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        seed: Optional[int] = None,
    ) -> str:
        self._refuser_un_autre_modele(model)
        self._assurer_serveur()
        corps = self._completion(
            system, user, max_tokens=None, delai=self._delai_generation,
            seed=seed)
        self._refuser_une_reponse_tronquee(corps)
        return self._contenu(corps).strip()

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
        """Les trois contraintes du contrat, dans les moyens de CE moteur.

        - `max_tokens` est un vrai paramètre du serveur (`server.py` le lit dans
          le corps de la requête) ;
        - `retries` est une boucle, comme chez les deux autres moteurs : un petit
          modèle rend par intermittence une réponse vide ou un JSON malformé ;
        - `schema` est transmis DANS LA DIRECTIVE, et non comme une grammaire de
          décodage. Ce n'est pas un choix par prudence : c'est une MESURE. Le
          `response_format: json_schema` de mlx-lm 0.31.3 est accepté puis
          ignoré — un schéma exigeant deux champs inventés a rendu trois fois
          une sortie qui n'en contenait aucun, identique à celle obtenue sans
          schéma (voir l'entête du module). L'envoyer aurait donné l'apparence
          d'une garantie, sans en être une.

          La directive est donc plus faible que la grammaire d'Ollama, et c'est
          DIT plutôt que subi. Ce qui reste garanti ici est ce qu'on VÉRIFIE au
          retour : du JSON bien formé, et une table. Accepter l'argument pour le
          jeter en silence produirait un compte-rendu hors contrat sans le
          moindre signal.

        Une réponse TRONQUÉE ne provoque pas de nouvel essai : le budget de
        jetons ne changerait pas d'un essai à l'autre, et la boucle ne ferait
        que payer trois fois le même échec avant de rendre un diagnostic faux
        (« JSON invalide » là où il faut lire « max_tokens trop bas »).
        """
        self._refuser_un_autre_modele(model)
        directive = system + "\n\nCRUCIAL : réponds EXCLUSIVEMENT en JSON valide."
        if schema is not None:
            directive += (
                "\n\nSchéma JSON à respecter STRICTEMENT :\n"
                + json.dumps(schema, ensure_ascii=False)
            )

        self._assurer_serveur()
        dernier = ""
        dernier_diagnostic = ""
        dernier_motif: Optional[str] = None
        # Une GRAINE FIXE rend cette boucle inutile, et coûteuse.
        #
        # Elle rejoue `directive`, `user`, `temperature` et `seed` à
        # l'identique : le serveur applique `mx.random.seed(seed)` avant de
        # générer, donc la sortie est la MÊME, octet pour octet.
        #
        # Observé : quand la première génération rend un JSON invalide, la
        # seconde a la même empreinte SHA-256 que la première, et la reprise
        # rejoue en pure perte des milliers de jetons et des minutes de calcul.
        #
        # Sans graine, la boucle garde son sens : une virgule manquante ne doit
        # pas coûter le cycle métier complet. C'est ce cas-là qu'elle sert.
        #
        # La reprise MÉTIER, elle, reste utile quoi qu'il arrive : elle change
        # le PROMPT (voir `report._rappel_correctif`), donc la sortie.
        essais = 1 if seed is not None else retries + 1
        for _ in range(essais):
            corps = self._completion(
                directive, user,
                max_tokens=max_tokens, delai=self._delai_generation,
                temperature=temperature, seed=seed,
            )
            self._refuser_une_reponse_tronquee(corps)
            # Le déballage vit dans `llm_base`, que ne possède aucun moteur :
            # les deux pipelines doivent accepter exactement les mêmes sorties.
            brut = retirer_enveloppe_markdown(self._contenu(corps))
            try:
                donnees = json.loads(brut)
            except json.JSONDecodeError:
                dernier = brut  # non-JSON → on retente
                dernier_diagnostic = _diagnostic_reponse(corps, brut)
                # Le motif d'arrêt décide si `report.py` a le droit de tenter
                # une réparation de balance : un document COMPLET mal fermé se
                # répare, un document coupé jamais. Relevé ici parce que c'est
                # le seul endroit qui voit encore la réponse du serveur.
                premier = (corps.get("choices") or [{}])[0]
                dernier_motif = (premier.get("finish_reason")
                                 if isinstance(premier, dict) else None)
                continue
            if isinstance(donnees, dict):
                return donnees
            # Bien formé mais du mauvais type : le modèle a compris la consigne
            # et rendu autre chose. Le redemander ne l'améliorera pas.
            raise MlxLLMError(
                f"Le JSON renvoyé par mlx_lm n'est pas une table : "
                f"{type(donnees).__name__}.")

        # Le message porte désormais de quoi DIAGNOSTIQUER, pas seulement de
        # quoi constater. Sans ces champs, « réponse invalide » ne distingue pas
        # un modèle qui s'arrête tout seul d'une sortie détournée par le serveur
        # vers `reasoning`/`tool_calls`, ni d'un plafond de génération atteint —
        # trois pannes qui exigent trois corrections différentes.
        # `LLMMalformedOutputError`, et non `MlxLLMError` : le modèle a RÉPONDU,
        # sa sortie est seulement inexploitable. C'est ce qui permet à
        # `report.py` de la réessayer sans réessayer aussi un service mort.
        # La sortie ENTIÈRE voyage avec l'erreur, là où le message la tronque à
        # 300 caractères. C'est ce qui permet à `report.py` de réparer une faute
        # de balance au lieu de payer une génération complète — voir
        # `core/reparation_json.py`. Le message, lui, reste court : il va dans
        # un journal, pas dans un algorithme.
        raise _MlxSortieInexploitable(
            f"Réponse mlx_lm invalide après {retries + 1} essais "
            f"[{dernier_diagnostic}] : {dernier[:300]!r}",
            brut=dernier, finish_reason=dernier_motif)

    def unload(self) -> bool:
        """Capacité optionnelle : rend les ~5,6 Go tenus par l'enfant.

        Ne lève JAMAIS — `core/llm.py` porte déjà la garde, mais l'appel arrive
        en fin de pipeline, transcript déjà écrit, et ce moteur n'a aucune raison
        de s'en remettre à elle pour une opération qu'il maîtrise.

        `False` signifie « rien n'a été rendu » : aucun serveur à nous, ou arrêt
        raté. Un serveur qu'on n'a pas lancé n'est pas tué : il n'est pas à nous.
        """
        with self._verrou:
            return self._arreter()

    # ---------- Garde ----------

    def _refuser_un_autre_modele(self, model: Optional[str]) -> None:
        """Un modèle différent de celui servi est REFUSÉ, jamais servi en silence.

        Le serveur tient UN modèle en mémoire. Honorer un autre nom imposerait
        un rechargement de plusieurs dizaines de secondes au milieu d'un
        compte-rendu ; l'ignorer rendrait un texte produit par un modèle que
        l'appelant n'a pas demandé, sans qu'aucun signal ne l'annonce. C'est le
        motif du type fermé `Fournisseur` de `core/llm.py` — jamais de repli
        silencieux sur un défaut.

        Aucun appelant de production ne passe `model` aujourd'hui (`report.py`,
        `rag.py`, `profiling.py` ne le font pas) : ce garde protège l'avenir.
        """
        if model is not None and model != self.model:
            raise MlxLLMError(
                f"Le serveur mlx_lm sert {self.model!r} ; {model!r} a été demandé. "
                f"Change MEMOGLYPH_MLX_LLM_MODEL et relance plutôt que de servir "
                f"un modèle que l'appelant n'a pas demandé."
            )
