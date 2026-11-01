"""Aucun identifiant de décision dans les fichiers suivis : la raison technique seulement.

Un commentaire, une docstring ou un document dit *quoi* et *pourquoi technique* ;
l'historique d'une décision vit hors du dépôt. Le motif est construit en morceaux
pour que ce fichier ne se signale pas lui-même.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from tests.hors_export import exclus_de_l_export

RACINE = Path(__file__).resolve().parents[2]
MOTIF = re.compile(("D" + "-" + r"\d{3}").encode())

# Liste fermée : fichiers où l'identifiant est une VALEUR que le test exécute
# (donnée lue par le code testé), pas un commentaire. Le retirer changerait un
# comportement : c'est à trancher à part. Chaque entrée donne le nombre exact
# d'occurrences tolérées et sa raison.
LISTE_FERMEE: dict[str, tuple[int, str]] = {}


def _fichiers_suivis() -> list[str]:
    """Les fichiers suivis qui partent avec le code.

    Les fichiers de développement marqués `export-ignore` (`tests/hors_export.py`) ont
    leurs propres contrôles : dans un dépôt sans `.gitattributes`, la liste exclue est vide.
    """
    sortie = subprocess.run(
        ["git", "ls-files", "-z"], cwd=RACINE, capture_output=True, check=True
    ).stdout
    exclus = exclus_de_l_export(RACINE)
    return [nom for nom in sortie.decode("utf-8").split("\0") if nom and nom not in exclus]


def _occurrences(nom: str) -> int:
    chemin = RACINE / nom
    if chemin.is_symlink() or not chemin.is_file():
        return 0
    return len(MOTIF.findall(chemin.read_bytes()))


def test_aucun_fichier_suivi_ne_cite_un_identifiant_de_decision() -> None:
    fautifs = {
        nom: n
        for nom in _fichiers_suivis()
        if (n := _occurrences(nom)) and LISTE_FERMEE.get(nom, (0, ""))[0] != n
    }
    assert not fautifs, (
        "identifiants de décision trouvés (garder la raison technique, retirer "
        f"l'identifiant) : {fautifs}"
    )


def test_la_liste_fermee_reste_juste() -> None:
    """Une entrée devenue inutile se retire : la liste ne couvre que l'existant."""
    suivis = set(_fichiers_suivis())
    for nom, (attendu, raison) in LISTE_FERMEE.items():
        assert raison, nom
        assert nom in suivis, f"{nom} n'est plus suivi : retirer l'entrée"
        assert _occurrences(nom) == attendu, f"{nom} : {_occurrences(nom)} au lieu de {attendu}"
