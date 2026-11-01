"""Registre et fabrique de moteurs STT + cache module-level.

Whisper est le SEUL moteur local : Parakeet a été retiré (anglais injecté dans du
français, langue non forçable). La fabrique porte le
cache par (moteur, langue) et le CONTRAT d'artefacts.

## Le registre, et pourquoi il ne se contente pas d'une fabrique

`STTEngine.transcribe(...) -> Transcript` reste inchangé : un moteur tiers ne
doit pas casser. Ce qui devient obligatoire, c'est la **déclaration** faite au
moment de l'enregistrement : fabrique + politique d'artefacts identifiée et
versionnée. Un moteur qui ne déclare rien est refusé — il ne se tait plus.

## Les imports de moteur sont DANS les fabriques, délibérément

La CI n'installe que le groupe `dev`, sans `mlx_whisper` : un import de niveau
module ferait échouer la COLLECTE pytest, la panne qui a laissé la CI à zéro
test pendant 18 heures. Le registre ne doit donc rien importer d'un moteur au
niveau module — les politiques, elles, sont du code pur et local.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from . import artifact_policy
from .artifact_policy import ArtifactPolicy
from .stt_base import STTEngine

# Moteur par défaut
DEFAULT_ENGINE = "whisper"


class MissingArtifactPolicy(RuntimeError):
    """Un moteur enregistré sans politique d'artefacts.

    Faute de CONTRAT, pas aléa d'exécution : elle est levée avant qu'un octet
    d'audio soit lu et avant qu'un modèle soit chargé. Le silence qu'elle
    remplace — un rapport vide indistinguable d'un détecteur qui n'a rien
    trouvé — avait laissé six génériques de sous-titrage entrer dans le corpus.
    """


@dataclass(frozen=True)
class EngineSpec:
    """Ce qu'un moteur doit DÉCLARER pour être servi par la fabrique."""

    factory: Callable[..., STTEngine]
    artifact_policy: ArtifactPolicy

    def __post_init__(self) -> None:
        # Premier des deux verrous : à l'enregistrement. Le second est dans
        # `engine_spec()`, car une entrée peut aussi arriver dans `_REGISTRY`
        # sans passer par ce constructeur (intégration tierce, test, mutation).
        _exiger_une_politique("<enregistrement>", self.artifact_policy)


def _exiger_une_politique(name: str, policy: object) -> ArtifactPolicy:
    """Refuse une politique absente ou incomplète, en disant quoi faire.

    Les TROIS éléments du protocole sont contrôlés, pas seulement `apply`.
   Une politique dont la version n'était pas un
    entier passait, et faisait ensuite tomber son PROPRE rapport d'échec :
    `apply_policy` convertissait la version dans son gestionnaire d'exception.
    Le contrat promettait que le transcript survit à une panne du détecteur, et
    il ne tenait pas dans le cas précis pour lequel il existait.

    Ici, à l'enregistrement, pour la même raison que la politique absente :
    refuser AVANT la fabrique, avant qu'un modèle ne se charge sur un contrat
    déjà rompu.
    """
    if policy is None or not callable(getattr(policy, "apply", None)):
        raise MissingArtifactPolicy(
            f"Le moteur « {name} » n'a pas de politique d'artefacts utilisable. "
            "Chaque entrée de `_REGISTRY` doit en déclarer une explicitement "
            "(voir core/artifact_policy.py) : `ScrubPolicy` si un détecteur "
            "s'applique, `UncheckedPolicy` avec sa raison sinon. Aucun défaut "
            "n'en attribue en douce."
        )
    identifiant = getattr(policy, "id", None)
    # `isinstance` et non `str(...)` : convertir avant de tester le vide
    # laisserait passer un entier `123`. Le vide et le TYPE sont deux
    # propriétés, et `ArtifactPolicy.id` est annoncé `str`, annotation qui
    # n'agit pas à l'exécution.
    if not isinstance(identifiant, str) or not identifiant.strip():
        raise MissingArtifactPolicy(
            f"La politique du moteur « {name} » n'a pas d'identifiant textuel "
            f"utilisable ({identifiant!r}). Il est publié dans le front-matter "
            "et la provenance : un vide y dirait « contrôlé par rien », un "
            "nombre y écrirait `artifact_policy: 123`."
        )
    version = getattr(policy, "version", None)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise MissingArtifactPolicy(
            f"La politique du moteur « {name} » a une version invalide "
            f"({version!r}). Un entier ≥ 1 est exigé : la version voyage dans "
            "la provenance, et sert à savoir quel détecteur a vu quel document."
        )
    return policy  # type: ignore[return-value]


def _whisper(*, language: Optional[str] = None, **_ignores) -> STTEngine:
    from .stt_whisper import WhisperEngine

    return WhisperEngine(language=language)


def _mistral(
    *,
    language: Optional[str] = None,
    api_key: Optional[str] = None,
    diarize: bool = True,
    **_ignores,
) -> STTEngine:
    from .stt_mistral import MistralSTTEngine

    # `language` était ignoré ici : un utilisateur forçant « fr » sur le moteur
    # Mistral le voyait silencieusement retomber en détection auto.
    return MistralSTTEngine(api_key=api_key, language=language, diarize=diarize)


# Les moteurs qui transcrivent sur la machine ; tout autre envoie l'audio au
# cloud (`sensitivity.cloud_refuse`).
MOTEURS_LOCAUX = frozenset({"whisper"})


def est_local(nom: str) -> bool:
    return (nom or "").strip().lower() in MOTEURS_LOCAUX


_REGISTRY: dict[str, EngineSpec] = {
    "whisper": EngineSpec(
        factory=_whisper,
        # Le détecteur est calibré sur les hallucinations de CE moteur, à partir
        # de son corpus d'entraînement. C'est aussi pourquoi on ne le prête pas
        # aux autres. Version 2 : les suites sans espace (« 입입입… ») sont des boucles.
        artifact_policy=artifact_policy.ScrubPolicy(id="artifacts.scrub", version=2),
    ),
    "mistral": EngineSpec(
        factory=_mistral,
        # `unchecked`, PAS `not_applicable` — et la nuance est le sujet.
        # Trois sorties propres ne prouvent pas qu'un moteur n'hallucine jamais ;
        # affirmer « aucun nettoyage requis » serait une sur-affirmation, pire
        # que de se taire. On dit ce qu'on a mesuré, et ce qu'on n'a pas fait.
        artifact_policy=artifact_policy.UncheckedPolicy(
            id="voxtral.non-mesure",
            version=1,
            reason=(
                "Aucun détecteur calibré pour Voxtral : zéro artefact observé "
                "sur trois échantillons (26/07/2026), ce qui ne prouve pas "
                "qu'il n'en produit jamais."
            ),
        ),
    ),
}

# Dérivé du registre, jamais recopié : deux listes divergent toujours un jour.
ENGINES = tuple(_REGISTRY)

# Alias d'interface : « Voxtral » est le nom du modèle, « mistral » celui du
# moteur. Les deux arrivent depuis l'UI et l'API.
_ALIAS = {"voxtral": "mistral"}

# Cache : évite de recharger le modèle à chaque fichier.
_cache: dict[str, STTEngine] = {}


def engine_spec(name: str = DEFAULT_ENGINE) -> EngineSpec:
    """Spécification du moteur, refusée si elle ne déclare pas de politique.

    C'est le point de contrôle du contrat, et il est SÉPARÉ de l'instanciation
    pour que le pipeline puisse l'appeler avant d'extraire l'audio : une faute
    de déclaration ne doit pas coûter une conversion ffmpeg, encore moins un
    chargement de modèle.
    """
    key = (name or DEFAULT_ENGINE).lower()
    key = _ALIAS.get(key, key)
    spec = _REGISTRY.get(key)
    if spec is None:
        raise ValueError(f"Moteur STT inconnu : '{key}'. Choix : {', '.join(ENGINES)}")
    _exiger_une_politique(key, getattr(spec, "artifact_policy", None))
    return spec


def get_engine(
    name: str = DEFAULT_ENGINE,
    *,
    language: Optional[str] = None,
    api_key: Optional[str] = None,
    diarize: bool = True,
) -> STTEngine:
    """Retourne le moteur demandé (instancié une seule fois par configuration).

    `language` : code forcé (ex. « fr ») ou None pour la détection automatique.
    `api_key` : clé API optionnelle pour les moteurs cloud (ex. Mistral AI).
    `diarize` : pour les moteurs qui diarisent EUX-MÊMES (Voxtral). Sans lui,
    décocher « Identifier les locuteurs » restait sans effet en mode cloud.
    Ignoré par Whisper, dont la diarisation est une étape séparée du pipeline.
    """
    name = (name or DEFAULT_ENGINE).lower()
    name = _ALIAS.get(name, name)

    # AVANT le cache, et c'est un piège déjà identifié : une politique retirée
    # après un premier appel réussi serait servie indéfiniment depuis le cache.
    spec = engine_spec(name)

    # `diarize` entre dans la clé : sans lui, un premier appel diarisant
    # servirait un moteur figé à tous les suivants, option décochée comprise.
    key = f"{name}:{language or 'auto'}:{api_key or ''}:{int(diarize)}"
    if key in _cache:
        return _cache[key]

    engine = spec.factory(language=language, api_key=api_key, diarize=diarize)
    _cache[key] = engine
    return engine


def unload() -> None:
    """Libère les moteurs STT ET les poids qu'ils tiennent.

    `_cache.clear()` seul ne rendait que **4 % de l'empreinte physique** —
    mesuré : 2 650 Mo pris par une transcription Whisper, 2 560 Mo
    encore résidents après l'appel. Ce dictionnaire ne contient que des
    enveloppes ; les poids vivent dans `mlx_whisper.transcribe.ModelHolder`,
    un singleton de CLASSE que rien dans ce dépôt ne référençait.

    La docstring promettait donc « la mémoire MLX/GPU » et rendait un
    vingt-cinquième. Une lecture statique dit la référence forte, pas
    le coût réel : il faut mesurer.

    On touche ici à l'intérieur d'une dépendance, ce qui est une dette assumée :
    `mlx-whisper` n'expose aucune API de déchargement. La garde est donc large,
    et son ABSENCE d'effet ne doit jamais faire échouer un pipeline — mais elle
    est DITE, pour qu'une version future qui renomme `ModelHolder` ne se traduise
    pas par un retour silencieux à 4 %.
    """
    _cache.clear()
    try:
        # `importlib.import_module` et rien d'autre. Le `__init__` de
        # `mlx_whisper` réexporte la FONCTION `transcribe` sous le nom du
        # sous-module et le masque : `from mlx_whisper import transcribe` ET
        # `import mlx_whisper.transcribe as _mw` rendent tous deux la fonction,
        # car la forme `as` résout par attribut sur le paquet. Seul
        # `import_module` va chercher le vrai module dans `sys.modules`.
        #
        # Piège : la libération peut SEMBLER marcher — 35 % rendus par `gc` et
        # le cache MLX masquent le fait que le mécanisme principal n'a pas
        # tourné. Seul l'avertissement ci-dessous le dit.
        import importlib

        _mw = importlib.import_module("mlx_whisper.transcribe")
        detenteur = getattr(_mw, "ModelHolder", None)
        if detenteur is None:
            print("[stt] mlx_whisper.ModelHolder introuvable — poids NON libérés, "
                  "la mémoire restera prise jusqu'à la fin du processus")
        else:
            detenteur.model = None
            detenteur.model_path = None
    except ImportError:
        pass  # moteur MLX absent : il n'y a rien à libérer
    try:
        import gc

        import mlx.core as mx

        gc.collect()
        mx.clear_cache()
    except Exception:  # noqa: BLE001 — libérer est un bonus, jamais bloquant
        pass
