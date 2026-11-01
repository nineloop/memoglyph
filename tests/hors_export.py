"""Les fichiers suivis que `.gitattributes` marque `export-ignore`.

Un test qui balaie le dépôt contre une liste fermée (« seuls ces modules ouvrent le
réseau », « chaque appel direct à un moteur est inscrit ») ne regarde que les fichiers
qui partent avec le code. Les fichiers exclus ont leurs propres contrôles, dans des
tests exclus avec eux : la règle vaut des deux côtés, chacun avec sa liste.

Dans un dépôt sans `.gitattributes`, rien n'est exclu, et ces fichiers n'y sont pas.
Hors d'un dépôt git (une archive décompressée), la liste est vide pour la même raison :
les fichiers exclus n'ont pas été archivés.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def exclus_de_l_export(racine: Path) -> frozenset[str]:
    """Chemins relatifs (POSIX) des fichiers suivis marqués `export-ignore`."""
    try:
        suivis = subprocess.run(["git", "ls-files", "-z"], cwd=racine, capture_output=True,
                                text=True, check=True).stdout.split("\0")
    except (OSError, subprocess.CalledProcessError):
        return frozenset()
    suivis = [c for c in suivis if c]
    if not suivis:
        return frozenset()
    champs = subprocess.run(["git", "check-attr", "-z", "--stdin", "export-ignore"], cwd=racine,
                            input="\0".join(suivis) + "\0", capture_output=True, text=True,
                            check=True).stdout.split("\0")
    return frozenset(c for c, _a, v in zip(champs[0::3], champs[1::3], champs[2::3]) if v == "set")
