"""La règle des modèles gratuits : UN seul endroit.

Un modèle que le SERVICE marque comme gratuit ne reçoit pas de document « ultra-confidentiel », même si le compte a autorisé le
cloud ; « public · normal » et « confidentiel » passent (la règle d'origine réservait ces modèles au
« public · normal » ; elle a été assouplie). La règle du nuage (`sensitivity.verifier_envoi`), elle, accepte
l'ultra autorisé par le compte : ici l'autorisation du compte n'est pas consultée, et ni le nuage ni l'adresse ne comptent. La
raison est celle des services eux-mêmes : les réglages d'entraînement ne sont pas les mêmes pour un modèle gratuit et pour un
modèle payant (à lire sur leur page de politique de données, citée dans `docs/local-et-en-ligne.md`).

Pour assouplir ou retirer la règle : `SENSIBILITES_PERMISES` ci-dessous, ou le crochet d'`llm._controler_envoi`. Rien d'autre.

Où elle s'applique :
- le cœur, par `llm._controler_envoi`, juste avant CHAQUE appel au moteur (sensibilité relue, comme la règle du nuage) ;
- les routes, par `api/main._refuser_modele_gratuit`, pour refuser avant toute réservation ou réception de fichier.
Elle n'est PAS dans `sensitivity.controler` : `rag.py` y FILTRE les passages refusés, et un filtre silencieux serait un autre
comportement que le refus dit (décision : l'assistant refuse).
"""
from __future__ import annotations

from typing import Callable, Iterable, Union

from . import llm_nomme, sensitivity

# Les seules sensibilités qu'un modèle gratuit reçoit. Une valeur absente ou inconnue se lit « standard » (`normaliser`).
SENSIBILITES_PERMISES: frozenset[str] = frozenset({"standard", "confidential"})


def est_gratuit(fournisseur: str, modele: str) -> bool:
    """Le service marque-t-il ce modèle comme gratuit ? Faux pour tout fournisseur sans profil nommé."""
    profil = llm_nomme.PROFILS.get(fournisseur)
    return profil is not None and llm_nomme.est_gratuit(profil, modele)


def verifier(fournisseur: str, modele: Union[str, Callable[[], str]], sensibilites: Iterable[object]) -> None:
    """Lève `ModeleGratuitRefuse` si ce modèle est gratuit et qu'une des sensibilités n'est pas permise.

    Rend aussitôt, sans lire ni modèle ni sensibilité, pour tout fournisseur sans profil nommé : le comportement des autres
    fournisseurs est INCHANGÉ. `modele` peut être un appel (résolu seulement si le fournisseur est concerné).
    Un envoi sans document (liste vide) ne porte aucun texte : accepté."""
    if fournisseur not in llm_nomme.PROFILS:
        return
    nom = modele() if callable(modele) else modele
    if not est_gratuit(fournisseur, nom):
        return
    for sensibilite in sensibilites:
        if sensitivity.normaliser(sensibilite) not in SENSIBILITES_PERMISES:
            raise sensitivity.ModeleGratuitRefuse()
