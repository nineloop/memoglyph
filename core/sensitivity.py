"""Sensibilité d'un document — MÉTADONNÉE choisie par l'utilisateur.

Vocabulaire FERMÉ (même idiome que `Fournisseur` dans `core/llm.py` :
`Literal` + `get_args`), ramené au défaut quand il est inconnu.

Une seule règle d'exécution en dépend, `verifier_envoi` : un document
ultra-confidentiel ne part vers aucun fournisseur cloud, ni pour sa
transcription, ni pour son compte rendu, ni pour ses faits, ni dans les extraits
de l'Assistant, SAUF si son propriétaire l'a autorisé par le réglage de son
compte (`cloud_ultra_autorise`, dans `auth.db`). Sans autorisation, la règle
vaut `cloud_refuse`. Elle est appliquée deux fois : par les routes, avant toute
lecture ou tout envoi, avec le même message (`REFUS_CLOUD`) ; et par les
façades du cœur (`llm.chat`, `llm.chat_json`, `pipeline.run`) juste avant
d'appeler un moteur, à partir de ce que porte `Envoi`.

L'autorisation est lue au moment de l'envoi par un résolveur que l'API
enregistre (`enregistrer_resolveur`) : le cœur n'importe pas `api/`, et un
réglage désactivé vaut dès l'envoi suivant, y compris dans un travail lancé
avant. Sans résolveur, ou s'il échoue, la réponse est non.

La sensibilité est relue de même à chaque envoi vers le cloud, par un second
résolveur que la bibliothèque enregistre (`enregistrer_resolveur_sensibilite`) : un
document passé en ultra-confidentiel pendant un compte rendu arrête l'appel suivant
(T3). Une relecture en échec ferme l'envoi cloud. La relecture relève aussi vers « confidentiel » : aucune règle ne lit ce niveau
aujourd'hui (la règle des modèles gratuits, `core/modeles_gratuits.py`, ne ferme plus que l'ultra) ; ce relèvement est gardé.

Le partage a sa propre règle (`library.reunion_ultra_confidentielle`), que le
réglage ne change pas. Rien ici ne classe automatiquement : le niveau reste
celui que l'utilisateur a choisi.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, replace
from typing import Callable, Iterable, Literal, Optional, Union, get_args

# Trois niveaux, du plus sensible au plus ouvert. Les valeurs STOCKÉES sont ces
# identifiants stables ; les libellés affichés vivent dans l'UI (i18n).
Sensibilite = Literal["ultra_confidential", "confidential", "standard"]
SENSIBILITES: tuple[str, ...] = get_args(Sensibilite)

# Défaut NEUTRE côté exécution : « standard » ne restreint rien aujourd'hui. Les
# documents créés AVANT ce champ n'en portent pas — ils sont donc `standard` à
# la lecture (compatibilité ascendante, via `normaliser`).
SENSIBILITE_DEFAUT: Sensibilite = "standard"


def normaliser(valeur: object) -> str:
    """Ramène toute valeur inconnue ou absente au défaut.

    Ne lève JAMAIS : un tag douteux ne doit pas empêcher une transcription ni
    casser une lecture. C'est aussi la frontière de compatibilité ascendante —
    un document sans le champ lit `standard`.
    """
    return valeur if valeur in SENSIBILITES else SENSIBILITE_DEFAUT


ULTRA_CONFIDENTIEL: Sensibilite = "ultra_confidential"
REFUS_CLOUD = "Un document ultra-confidentiel ne part pas vers un fournisseur cloud"


class EnvoiRefuse(Exception):
    """Un envoi ultra-confidentiel vers le cloud, sans l'autorisation du propriétaire.

    Hors de `LLMError` : ce n'est pas une panne de service, et les `except` qui
    absorbent les pannes d'un moteur (tranche perdue, classifieur en repli) ne
    doivent pas l'absorber. Ni `PermissionError` : c'est un `OSError`, qu'un
    `except OSError` autour d'une lecture de fichier avalerait. L'API la rend en
    403 avec `REFUS_CLOUD`.
    """

    def __init__(self, message: str = REFUS_CLOUD) -> None:
        super().__init__(message)


REFUS_MODELE_GRATUIT = ("Un modèle gratuit ne reçoit pas de document ultra-confidentiel : choisissez un modèle payant "
                        "ou un autre fournisseur.")


class ModeleGratuitRefuse(EnvoiRefuse):
    """Un document « ultra-confidentiel » destiné à un modèle que le service marque gratuit.

    Règle PLUS STRICTE que celle du nuage (`core/modeles_gratuits.py`) : elle ne regarde pas l'autorisation du compte (un ultra
    autorisé au cloud est refusé quand même). Une
    sous-classe d'`EnvoiRefuse` : tous les `except` qui laissent traverser un refus du cœur la laissent traverser aussi, et
    l'API la rend en 403 à corps plat avec son propre code (`code`), jamais avec la phrase de l'ultra-confidentiel.
    """

    code = "model.free.restricted"

    def __init__(self, message: str = REFUS_MODELE_GRATUIT) -> None:
        super().__init__(message)


def verifier_envoi(sensibilite: object, *, local: bool, autorise: bool = False) -> None:
    """LA règle : lève `EnvoiRefuse` pour de l'ultra-confidentiel envoyé en cloud sans autorisation.

    `autorise` vaut non par défaut : un appelant qui l'oublie retombe sur « jamais ».
    """
    if normaliser(sensibilite) == ULTRA_CONFIDENTIEL and not local and autorise is not True:
        raise EnvoiRefuse()


def cloud_refuse(valeur: object, *, local: bool) -> bool:
    """Vrai si un document de cette sensibilité ne part pas vers ce fournisseur, sans autorisation."""
    try:
        verifier_envoi(valeur, local=local, autorise=False)
    except EnvoiRefuse:
        return True
    return False


@dataclass(frozen=True)
class Envoi:
    """Ce qu'un appel au modèle emporte, pour la règle : sensibilité et propriétaire.

    `proprietaire` : l'identifiant du compte propriétaire du PROJET du document
    (manifeste du dossier, `library.proprietaire_du_document`), jamais celui du
    compte qui lit. `None` (projet d'avant les comptes, document introuvable) :
    aucune autorisation possible. `document_id` ne sert qu'à la trace.
    """
    sensibilite: object = SENSIBILITE_DEFAUT
    proprietaire: Optional[str] = None
    document_id: Optional[str] = None


# Ce que les façades acceptent pour `envoi=` : un document, plusieurs (les passages
# de l'Assistant), ou `None` pour un appel qui ne porte aucun document (mesures, tests).
Envois = Union[Envoi, Iterable[Envoi], None]
Resolveur = Callable[[str], bool]


def _jamais(_proprietaire: str) -> bool:
    return False


_resolveur: Resolveur = _jamais


def enregistrer_resolveur(resolveur: Optional[Resolveur]) -> None:
    """Branche la lecture du réglage de compte ; `None` rétablit « jamais ».

    Une variable de module et non une `ContextVar` : les travaux de fond tournent
    dans des fils (`api/jobs.py`), où un contexte ne se propage pas de lui-même.
    """
    global _resolveur
    _resolveur = resolveur or _jamais


def autorise(proprietaire: Optional[str]) -> bool:
    """Le propriétaire a-t-il autorisé le cloud pour son ultra-confidentiel, maintenant ?

    Relu à chaque appel. Tout ce qui n'est pas un `True` explicite vaut non :
    pas de propriétaire, résolveur absent, base illisible.
    """
    if not isinstance(proprietaire, str) or not proprietaire.strip():
        return False
    try:
        return _resolveur(proprietaire) is True
    except Exception:  # noqa: BLE001 — fermé en cas de doute
        return False


ResolveurSensibilite = Callable[[str], Optional[str]]


def _inconnue(_document_id: str) -> Optional[str]:
    return None


_resolveur_sensibilite: ResolveurSensibilite = _inconnue


def enregistrer_resolveur_sensibilite(resolveur: Optional[ResolveurSensibilite]) -> None:
    """Branche la relecture de la sensibilité d'un document par son identifiant ; `None` la retire."""
    global _resolveur_sensibilite
    _resolveur_sensibilite = resolveur or _inconnue


def sensibilite_actuelle(envoi: Envoi) -> object:
    """La sensibilité de cet envoi MAINTENANT : celle qu'il porte, RELEVÉE par celle relue (T3).

    Appelée pour un envoi CLOUD seulement (`_controler`) : en local, rien n'est relu.
    `Envoi` est figé au début d'un travail ; le document peut devenir plus sensible pendant. La relecture ne fait que RELEVER
    (ultra-confidentiel > confidentiel > public · normal) : un document qui le serait devenu moins garde ce que porte l'envoi,
    contrôlé par la route au départ. Le niveau « confidentiel » relu n'est lu par aucune règle
    aujourd'hui (le nuage et les modèles gratuits ne ferment que l'ultra-confidentiel) ; le relèvement est gardé, il est testé.

    - Résolveur qui LÈVE (index illisible…) : `EnvoiRefuse`. Fermé en cas de doute,
      comme l'autorisation du compte : on ne sait pas ce que le document est devenu.
    - Document inconnu du résolveur (`None` : pas encore indexé, comme pendant la
      transcription) ou envoi sans `document_id` : ce n'est pas un échec, la valeur
      portée vaut. Sans cela, toute transcription cloud serait refusée.
    """
    if normaliser(envoi.sensibilite) == ULTRA_CONFIDENTIEL:
        return envoi.sensibilite
    if not isinstance(envoi.document_id, str) or not envoi.document_id:
        return envoi.sensibilite
    try:
        relue = normaliser(_resolveur_sensibilite(envoi.document_id))
    except Exception as exc:  # noqa: BLE001 — fermé en cas de doute
        raise EnvoiRefuse() from exc
    plus_sensible = SENSIBILITES.index(relue) < SENSIBILITES.index(normaliser(envoi.sensibilite))   # du plus sensible au plus ouvert
    return relue if plus_sensible else envoi.sensibilite


def _envois(envoi: Envois) -> tuple[Envoi, ...]:
    if envoi is None:
        return ()
    if isinstance(envoi, Envoi):
        return (envoi,)
    return tuple(envoi)


def controler(envoi: Envois, *, local: bool) -> bool:
    """Applique la règle à chaque document de l'envoi, sensibilité et autorisation lues maintenant.

    Lève `EnvoiRefuse` au premier refus. Rend vrai si au moins un document
    ultra-confidentiel part vers le cloud avec autorisation : c'est ce qu'il faut tracer.
    """
    return _controler(envoi, local=local)[0]


def _controler(envoi: Envois, *, local: bool) -> tuple[bool, tuple[Envoi, ...]]:
    """`controler`, et les envois avec leur sensibilité actuelle, pour la trace."""
    envois = _envois(envoi)
    if local:  # rien ne sort de la machine : rien à relire
        return False, envois
    actuels = tuple(replace(e, sensibilite=sensibilite_actuelle(e)) for e in envois)
    ultra = False
    for e in actuels:
        if normaliser(e.sensibilite) != ULTRA_CONFIDENTIEL:
            continue
        verifier_envoi(e.sensibilite, local=local, autorise=autorise(e.proprietaire))
        ultra = True
    return ultra, actuels


# Journal des envois autorisés : compte, fournisseur, identifiant du document. Jamais
# un titre ni un extrait. Sur l'erreur standard : la sortie standard du serveur MCP
# porte son protocole.
journal = logging.getLogger("memoglyph.envoi")
if not journal.handlers:
    _sortie = logging.StreamHandler(sys.stderr)
    _sortie.setFormatter(logging.Formatter("[envoi] %(message)s"))
    journal.addHandler(_sortie)
journal.setLevel(logging.INFO)


def tracer(envoi: Envois, *, fournisseur: str) -> None:
    """Marque l'exécution FinOps en cours et écrit une ligne de journal par document.

    Une ligne par document et par exécution, pas une par appel : un compte rendu
    fait des dizaines d'appels. Hors exécution, chaque appel écrit sa ligne.
    """
    from . import telemetry  # noqa: PLC0415 — `telemetry` n'a rien à faire ici hors trace

    for e in _envois(envoi):
        if normaliser(e.sensibilite) != ULTRA_CONFIDENTIEL:
            continue
        if telemetry.noter_envoi_ultra_autorise((e.document_id, fournisseur)):
            journal.info("ultra-confidentiel envoyé avec autorisation : compte=%s fournisseur=%s document=%s",
                         e.proprietaire, fournisseur, e.document_id or "-")


def autoriser_envoi(envoi: Envois, *, local: bool, fournisseur: str,
                    avant_trace: Optional[Callable[[tuple[Envoi, ...]], None]] = None) -> bool:
    """`controler`, puis `tracer` si un ultra-confidentiel part ; rend ce que rend `controler`.

    `avant_trace` : une règle SUPPLÉMENTAIRE de l'appelant (celle des modèles gratuits), appliquée aux envois avec leur
    sensibilité ACTUELLE, après le refus du nuage et AVANT la trace : un envoi refusé ne laisse jamais la ligne « envoyé avec
    autorisation ». Un rappel et non un import : ce module ignore tout des modèles, et `controler` (que `rag.py` filtre) n'en
    porte aucune."""
    ultra, actuels = _controler(envoi, local=local)
    if avant_trace is not None:
        avant_trace(actuels)
    if ultra:
        tracer(actuels, fournisseur=fournisseur)
    return ultra
