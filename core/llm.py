"""Façade LLM : un REGISTRE de moteurs, une frontière, un seul type d'erreur.

Ce module était une façade par `if/else` répété quatre fois, chaque branche
sachant nommer les deux implémentations — donc quatre endroits à modifier pour
ajouter un moteur, et quatre occasions d'oublier la normalisation des erreurs.
Il ne connaît plus que le contrat de `core/llm_base.py` : ajouter un moteur,
c'est écrire une classe et ajouter une ligne au registre.
"""

from __future__ import annotations

import ipaddress
from typing import Any, Callable, Literal, Optional, TypeVar, get_args

from . import config, llm_anthropic, llm_compatible, llm_mistral, llm_mlx, llm_nomme, llm_openai, modeles_gratuits, sensitivity
from .llm_base import LLMEngine, LLMError, LLMMalformedOutputError

# Ré-exporté ici parce que la production l'attrape sous ce nom (`llm.LLMError`
# dans `profiling`, `rag`, `report`). C'est la classe NEUTRE, pas celle d'un
# moteur : `llm_ollama.LLMError` est le même objet, pour les appelants restés
# sur l'ancien nom.
__all__ = [
    "FOURNISSEURS", "Fournisseur", "FournisseurInconnu", "HORS_INTERFACE", "LLMError",
    "LLMMalformedOutputError", "MOTEURS",
    "chat", "chat_json", "is_available", "moteur", "unload",
    "valider_fournisseur",
]

# Type FERMÉ. Auparavant, tout ce qui n'était pas « mistral » ou « cloud »
# retombait sur Ollama : une valeur inconnue ou périmée dans le `localStorage`
# de l'interface changeait donc silencieusement de moteur, et l'utilisateur
# croyait parler à Mistral en parlant à Qwen. Une faute de frappe produisait le
# même effet, sans un mot.
#
# `ollama` est SORTI de la cible produit : le local tourne sur MLX, et aucun
# moteur Ollama n'est au registre.
Fournisseur = Literal["mistral", "cloud", "mlx", "openai", "anthropic", "compatible", "openrouter", "groq"]
FOURNISSEURS: tuple[str, ...] = get_args(Fournisseur)

# Moteurs au registre que l'interface ne propose pas : `/api/config` les
# tait et la relance des faits les refuse. Vide : l'écran propose tout le registre.
HORS_INTERFACE: frozenset[str] = frozenset()

# LE fournisseur par défaut, en UN seul endroit.
#
# Il était écrit `"ollama"` en dur dans huit signatures — six ici, plus
# `pipeline`, `profiling`, `rag` et `report`. Huit endroits qui répondent à la
# même question finissent par diverger : c'est la classe de défaut déjà
# fermée pour le modèle servi et pour la disponibilité. Le changer se fait
# désormais ici, et la CI vérifie qu'aucune signature ne le recopie.
FOURNISSEUR_DEFAUT = "mlx"

# LE registre. Une instance par moteur, sans état de requête : la clé API et le
# modèle voyagent par appel, parce qu'un utilisateur peut apporter la sienne
# (BYOK) sans qu'on reconstruise quoi que ce soit.
#
# Chaque valeur est construite à l'IMPORT : un constructeur qui ouvrirait une
# connexion, importerait un runtime ML ou lancerait un processus paierait ce
# coût dans toute la suite de tests et dans chaque script. `MlxEngine.__init__`
# ne fait donc rien d'autre que retenir sa configuration.
MOTEURS: dict[str, LLMEngine] = {
    "mistral": llm_mistral.MistralEngine(),
    "mlx": llm_mlx.MlxEngine(),
    "openai": llm_openai.OpenAIEngine(),
    "anthropic": llm_anthropic.AnthropicEngine(),
    "compatible": llm_compatible.CompatibleEngine(),
    # Fournisseurs en ligne NOMMÉS : deux préréglages d'un même moteur, un par profil. Jamais locaux, jamais
    # « auto-hébergés » : leur identité dit « en ligne », pas leur adresse (`llm_nomme.py`).
    "openrouter": llm_nomme.MoteurNomme(llm_nomme.PROFILS["openrouter"]),
    "groq": llm_nomme.MoteurNomme(llm_nomme.PROFILS["groq"]),
}

# « cloud » est un alias historique de « mistral », annoncé par l'API et encore
# présent dans les réglages enregistrés côté navigateur. Il vit ICI, en une
# ligne, plutôt que dans quatre tests d'appartenance à un tuple.
_ALIAS = {"cloud": "mistral"}

T = TypeVar("T")


class FournisseurInconnu(ValueError):
    """Fournisseur LLM hors du type fermé. Refusé, jamais retombé sur un défaut."""


def valider_fournisseur(provider: str) -> str:
    """Refuse ce qu'on ne connaît pas plutôt que de choisir à la place de l'appelant."""
    if provider not in FOURNISSEURS:
        raise FournisseurInconnu(
            f"Fournisseur LLM inconnu : {provider!r}. "
            f"Connus : {', '.join(FOURNISSEURS)}"
        )
    return provider


def moteur(provider: str = FOURNISSEUR_DEFAUT) -> LLMEngine:
    """Le moteur demandé. Lève `FournisseurInconnu` plutôt que de deviner."""
    return MOTEURS[_ALIAS.get(valider_fournisseur(provider), provider)]


def hote_local(hote: object) -> bool:
    """L'hôte est-il sur cette machine : `localhost`, 127.0.0.0/8 ou ::1 ?

    Tout le reste, nom de machine compris, est distant : un nom qui se résoudrait
    vers la boucle locale n'est pas résolu ici (pas de DNS dans la règle d'envoi).
    """
    if not isinstance(hote, str):
        return False
    nom = hote.strip().strip("[]").lower()
    if nom == "localhost":
        return True
    try:
        return ipaddress.ip_address(nom).is_loopback
    except ValueError:
        return False


def est_local(provider: str = FOURNISSEUR_DEFAUT) -> bool:
    """Le fournisseur tourne-t-il sur CETTE machine ? Seul « mlx », et seulement si
    l'hôte qu'il joint est sur la boucle locale (`hote_local`).

    C'est la frontière de la règle d'envoi (`sensitivity.verifier_envoi`) : un
    `MLX_HOST` distant reçoit le texte comme un fournisseur cloud, il est donc
    compté comme tel (T4), sans
    être interdit. L'hôte est celui du moteur au registre (`MlxEngine.host`), sinon
    celui de la configuration. Sert aussi à annoncer un modèle local au premier
    usage. Ne lève pas : un fournisseur non reconnu n'est simplement « pas local ».
    """
    cible = _ALIAS.get(provider, provider)
    if cible == "mlx":
        return hote_local(getattr(MOTEURS.get("mlx"), "host", None) or config.MLX_HOST)
    if cible == "compatible":
        # L'hôte de `COMPATIBLE_BASE_URL` : un serveur compatible sur la machine est
        # local, ailleurs il reçoit le texte comme un fournisseur cloud.
        return hote_local(getattr(MOTEURS.get("compatible"), "host", None) or llm_compatible.hote())
    return False


def auto_heberge(provider: str) -> bool:
    """Le fournisseur est-il un serveur que l'utilisateur héberge (MLX, compatible) ?

    Hors de la boucle locale, c'est un « cloud privé » à l'écran. Ne lève pas.
    """
    return bool(getattr(MOTEURS.get(_ALIAS.get(provider, provider)), "auto_heberge", False))


def profil_prudent(provider: str) -> bool:
    """Profil d'exécution prudent : local, ou moteur qui le demande (capacité inconnue)."""
    return est_local(provider) or bool(
        getattr(MOTEURS.get(_ALIAS.get(provider, provider)), "profil_prudent", False))


def propose(provider: str) -> bool:
    """Le fournisseur est-il proposé à l'écran ? Hors `HORS_INTERFACE`, et prêt s'il le
    faut : un serveur compatible sans URL ni modèle n'a rien à proposer."""
    if provider in HORS_INTERFACE:
        return False
    cible = MOTEURS.get(_ALIAS.get(provider, provider))
    return not getattr(cible, "propose_si_pret", False) or bool(cible.pret_declare())


def _normalise(nom: str, appel: Callable[[], T]) -> T:
    """LA frontière : toute erreur qui sort d'ici est une `LLMError`.

    Propriété de SÉCURITÉ, à ne pas relâcher : le message de l'exception
    d'origine n'est JAMAIS recopié, seul son `type(exc).__name__` l'est. Un
    moteur laisse passer les erreurs de son SDK ou de son client HTTP — réseau,
    401, 429, timeout, JSON inattendu — et leur texte peut contenir un corps de
    réponse d'API, donc la clé. Le type suffit à distinguer une panne de
    service d'un bug ; il ne peut rien publier.

    Cette normalisation ne vivait que sur la branche Mistral : une exception
    inattendue du moteur local ressortait telle quelle, et le prochain moteur
    aurait rouvert le trou en oubliant sa branche. Elle est ici une seule fois,
    et s'applique donc à TOUT moteur du registre, y compris ceux à venir.

    Une `LLMError` passe en revanche telle quelle : elle est déjà à la
    frontière, son message a été composé par le moteur qui sait ce qu'il peut
    publier (`_erreur_sure` côté Mistral), et le remplacer priverait le 503 de
    son seul diagnostic utile.
    """
    try:
        return appel()
    except LLMError:
        raise
    except Exception as exc:  # noqa: BLE001 — voir la docstring
        raise LLMError(f"Appel {nom} impossible ({type(exc).__name__})") from exc


def cle_mistral(provider: str, cle: Optional[str]) -> Optional[str]:
    """La clé Mistral de l'utilisateur, pour Mistral SEULEMENT (alias `cloud` compris).

    Passée telle quelle à un autre moteur, elle partirait chez lui dans l'en-tête
    d'authentification : chaque moteur prend la clé qu'on lui donne. Les appelants
    qui relaient la clé saisie à l'écran passent donc par ici.
    """
    return cle if moteur(provider).name == "mistral" else None


def is_available(provider: str = FOURNISSEUR_DEFAUT, api_key: Optional[str] = None) -> bool:
    """Le moteur peut-il répondre ?

    Hors de `_normalise` volontairement : le contrat dit qu'un moteur RÉPOND
    faux au lieu de lever, et la route de santé de l'API appelle ceci sans
    `try`. Une exception normalisée y deviendrait un 500 là où il n'y en avait
    pas.
    """
    return moteur(provider).is_available(api_key=api_key)


def pret_declare(provider: str) -> bool:
    """Ce moteur peut-il être PROPOSÉ ? Voir `LLMEngine.pret_declare`.

    Hors de `_normalise`, comme `is_available` : la route de configuration
    l'appelle sans `try`, pour tous les moteurs d'un coup.
    """
    return moteur(provider).pret_declare()


def disponibilite_declaree() -> dict[str, bool]:
    """`{fournisseur → prêt}` pour TOUS les moteurs annoncés.

    Existe pour que la couche API cesse de nommer des implémentations.
    `api/main.py` calculait `llm_ollama.is_available() or bool(MISTRAL_API_KEY)`
    : deux moteurs cités en dur, le troisième absent de la disponibilité
    annoncée — donc invisible dans l'interface quoi qu'en dise le registre.
    C'est le partage binaire du modèle servi, un étage plus haut.

    Construit depuis `FOURNISSEURS`, donc un moteur ajouté au registre apparaît
    ici sans qu'on y pense. Les alias sont exclus : `cloud` n'est pas un moteur,
    et l'annoncer ferait compter Mistral deux fois.
    """
    return {nom: pret_declare(nom) for nom in MOTEURS}


def modele_servi(provider: str = FOURNISSEUR_DEFAUT, model: Optional[str] = None) -> str:
    """Le modèle que ce fournisseur servira RÉELLEMENT — LA source unique.

    Hors de `_normalise`, comme `is_available` et pour une raison plus forte
    encore : les appelants écrivent le résultat dans le front-matter et le
    fingerprint de documents autoritatifs. Une `LLMError` y transformerait une
    question de provenance en panne de publication.

    Passe par `moteur()`, donc par `valider_fournisseur()` : un fournisseur
    inconnu lève au lieu de retomber sur un défaut. C'est ce repli silencieux
    qui faisait annoncer `qwen3.5:9b` à des documents produits ailleurs.
    """
    return moteur(provider).modele_servi(model)


def _controler_envoi(cible: LLMEngine, provider: str, envoi: sensitivity.Envois, model: Optional[str] = None) -> None:
    """La règle de sensibilité, AVANT tout appel au moteur (`sensitivity.verifier_envoi`).

    Ici et non dans chaque appelant : une étape ajoutée au tronc passe par cette
    porte, et `tests/unit/test_envoi_par_la_facade.py` vérifie que chaque appel du
    cœur dit ce qu'il envoie. L'autorisation du propriétaire est relue à chaque
    appel : un réglage désactivé pendant un compte rendu arrête l'appel suivant.
    Hors de `_normalise` : un refus n'est pas une panne de moteur.

    La règle des modèles gratuits (`core/modeles_gratuits.py`) passe par le même point : après le refus du nuage, avant la
    trace, avec la sensibilité relue. Elle ne fait rien pour un fournisseur sans profil nommé.
    """
    sensitivity.autoriser_envoi(
        envoi, local=est_local(provider), fournisseur=cible.name,
        avant_trace=lambda actuels: modeles_gratuits.verifier(
            cible.name, lambda: cible.modele_servi(model), [e.sensibilite for e in actuels]))


def chat(
    system: str,
    user: str,
    *,
    provider: str = FOURNISSEUR_DEFAUT,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    seed: Optional[int] = None,
    envoi: sensitivity.Envois = None,
) -> str:
    """Une complétion texte. `seed` porte la REPRODUCTIBILITÉ de la CHAÎNE.

    Il ne suffit pas de fixer la génération finale. MESURÉ sur deux
    exécutions du même transcript au même commit avec la même graine : le prompt
    du compte-rendu faisait 13 257 jetons dans l'une et 13 071 dans l'autre, et
    les sorties n'avaient rien à voir — 18 affirmations contre 8.

    L'écart venait d'AMONT. Les synthèses Map-Reduce et la classification de
    genre passent par ici, et ne portaient aucune graine ; or leur sortie entre
    dans le prompt final. Fixer `chat_json` seul ne rend donc PAS le pipeline
    rejouable : il rend rejouable une étape dont l'entrée varie.

    `envoi` : le ou les documents dont le texte part dans `user` ; lève
    `sensitivity.EnvoiRefuse` sans appeler le moteur si la règle le refuse.
    """
    cible = moteur(provider)
    _controler_envoi(cible, provider, envoi, model)
    return _normalise(
        cible.name,
        lambda: cible.chat(system, user, model=model, api_key=api_key,
                           seed=seed),
    )


def chat_json(
    system: str,
    user: str,
    *,
    provider: str = FOURNISSEUR_DEFAUT,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    max_tokens: Optional[int] = None,
    schema: Optional[dict] = None,
    retries: int = 2,
    temperature: float = 0.1,
    seed: Optional[int] = None,
    envoi: sensitivity.Envois = None,
) -> dict[str, Any]:
    """Réponse JSON parsée, avec ses contraintes de sortie.

    `max_tokens`, `schema` et `retries` sont ici parce que `core/report.py`
    court-circuitait cette façade pour les obtenir : tant qu'un contournement
    existe, l'interface n'est pas l'unique porte, et changer de moteur touche
    plus qu'une classe.

    `seed` porte la REPRODUCTIBILITÉ, et chaque moteur le nomme différemment —
    `seed` chez `mlx_lm.server`, `random_seed` chez Mistral. La façade expose le
    nom neutre ; la traduction appartient au moteur, comme pour le schéma.

    Sans lui, deux exécutions du même prompt sur le même modèle rendent des
    sorties différentes, et toute comparaison entre modèles porte sur des
    tirages non reproductibles. MESURÉ : le même transcript a échoué
    sur trois modes distincts en trois tirages.

    `envoi` : comme pour `chat`, vérifié avant tout appel au moteur.
    """
    cible = moteur(provider)
    _controler_envoi(cible, provider, envoi, model)
    return _normalise(
        cible.name,
        lambda: cible.chat_json(
            system, user, model=model, api_key=api_key,
            max_tokens=max_tokens, schema=schema, retries=retries,
            temperature=temperature, seed=seed,
        ),
    )


def unload(provider: str = FOURNISSEUR_DEFAUT) -> bool:
    """Rend la mémoire tenue par le moteur, quand il en tient.

    Capacité OPTIONNELLE du contrat : un moteur cloud n'a rien à rendre, et son
    absence de méthode n'est pas une panne. Best-effort par construction —
    l'appel arrive en fin de pipeline, transcript déjà écrit, et ne doit jamais
    faire échouer ce qui a réussi. C'est aussi pourquoi un fournisseur inconnu
    ne lève pas ici : il ne fait rien, et le dit par sa valeur de retour.

    `False` signifie « rien n'a été rendu » — capacité absente, fournisseur
    inconnu, ou tentative échouée. Aucun appelant n'en fait autre chose qu'une
    trace.
    """
    try:
        cible = moteur(provider)
    except FournisseurInconnu:
        return False
    liberer = getattr(cible, "unload", None)
    if liberer is None:
        return False
    try:
        return bool(liberer())
    except Exception:  # noqa: BLE001
        # La garde vit ICI et non dans chaque moteur. Une propriété
        # qu'il faut réimplémenter à chaque implémentation est une propriété
        # qu'on oubliera à la suivante, et le contrat ci-dessus la promet au
        # nom de TOUS les moteurs.
        #
        # `BaseException` n'est PAS rattrapée : une interruption ou un arrêt
        # demandé doivent traverser. On absorbe la panne d'un moteur, pas la
        # décision de s'arrêter.
        return False
