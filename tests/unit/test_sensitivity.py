"""Vocabulaire de sensibilité — MÉTADONNÉE, aucune règle d'exécution ici.

On vérifie le contrat du vocabulaire fermé et la coercition douce (compat
ascendante : un document sans le champ lit `standard`).
"""
from __future__ import annotations

from core import sensitivity as s


def test_vocabulaire_ferme_et_defaut():
    assert s.SENSIBILITES == ("ultra_confidential", "confidential", "standard")
    assert s.SENSIBILITE_DEFAUT == "standard"
    assert s.SENSIBILITE_DEFAUT in s.SENSIBILITES


def test_normaliser_conserve_les_valeurs_connues():
    for niveau in s.SENSIBILITES:
        assert s.normaliser(niveau) == niveau


def test_normaliser_ramene_inconnu_absent_et_None_au_defaut():
    # Compat ascendante + robustesse : rien de tout cela ne doit lever.
    assert s.normaliser(None) == "standard"
    assert s.normaliser("") == "standard"
    assert s.normaliser("nope") == "standard"
    assert s.normaliser(123) == "standard"
    assert s.normaliser("STANDARD") == "standard"  # sensible à la casse : identifiant stable
