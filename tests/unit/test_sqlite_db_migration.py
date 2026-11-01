"""Nom canonique GÉNÉRIQUE de l'index dérivé.

Une base nommée d'après le produit se scinde au premier renommage dès qu'un module
oublie de suivre (registre vocal d'un côté, index de l'autre). Le nom générique
`index.db`, défini une seule fois dans `sqlite_db`, écarte cette classe de défaut.
"""

import pytest

from core import library, sqlite_db, voiceprint

pytestmark = pytest.mark.unit


def test_le_nom_canonique_est_generique():
    """Ne contient pas le nom du produit, et chaque module lit le même chemin."""
    nom = sqlite_db.DB_PATH.name
    assert "memoglyph" not in nom.lower()
    assert nom == "index.db"
    assert library.DB_PATH == voiceprint.DB_PATH == sqlite_db.DB_PATH
