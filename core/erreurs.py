"""Le code de message d'un traitement en échec : l'interface le traduit, jamais le texte de l'exception.

Un traitement qui échoue garde son texte brut (`job.error`, pour les clients existants et le journal), mais
ce texte est celui d'une exception : français ou anglais selon l'endroit, avec des noms de modèle, de
fichier ou de fonction. L'interface, elle, doit parler une seule langue : elle lit donc un CODE
(`job.error_code`), comme pour les étapes (`core/messages.py`, `core/etapes.py`).

Ce module est à part de `messages.py` : celui-ci ne contient que des constructeurs de messages, et un test
exige que chacun soit couvert par la table de référence des textes.

La table se lit par le NOM de la classe (parcours de la hiérarchie) pour ne rien importer : ce module est
chargé par le gestionnaire de traitements, qui ne dépend ni du moteur de compte rendu ni des modèles. Un test
vérifie la table sur les vraies classes.
"""
from __future__ import annotations

from typing import Any

# Un échec dont la cause n'est dans aucune ligne de la table : une phrase générale, jamais le texte brut.
INCONNU = "step.error.unexpected"

# Nom de classe → code. Du plus précis au plus général n'importe pas : la hiérarchie se parcourt de la
# classe de l'exception vers `object`, et la première classe connue gagne.
PAR_CLASSE: dict[str, str] = {
    "TranscriptNonCitable": "step.error.notCitable",
    "ExtractionVide": "step.error.emptyExtraction",
    "EnvoiRefuse": "step.error.sendRefused",
    # Deux sous-classes, trouvées par leur nom AVANT celui de leur famille (`EnvoiRefuse`, `LLMError`).
    "ModeleGratuitRefuse": "step.error.freeModelRestricted",
    "LimiteDeDemandes": "step.error.rateLimited",
    "LLMError": "step.error.modelUnavailable",
    "LLMMalformedOutputError": "step.error.modelUnavailable",
}

# Tous les codes que ce module peut rendre : l'interface doit avoir `job.<code>` dans les deux langues.
CODES: frozenset[str] = frozenset({INCONNU, *PAR_CLASSE.values()})


def code_de(exc: BaseException) -> tuple[str, dict[str, Any]]:
    """`(code, paramètres)` de l'échec d'un traitement."""
    for classe in type(exc).__mro__:
        code = PAR_CLASSE.get(classe.__name__)
        if code:
            return code, {}
    return INCONNU, {}
