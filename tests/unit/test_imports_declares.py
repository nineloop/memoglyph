"""Tout ce que `core/` et `api/` importent doit être déclaré.

Défaut d'origine, élargi par ce balayage :
`transformers` — moteur d'embedding PAR DÉFAUT — n'était dans aucun des trois
manifestes, et `requests` — qui porte réellement tous les appels Mistral, le SDK
n'étant pas installé — n'y était que par transitivité. Les deux fonctionnaient
sur la machine de développement et auraient cassé sur `uv sync --locked` propre.

`make check` ne pouvait pas le voir : les seuls tests qui chargent ces chemins
sont marqués `ml` et sortent du portail. C'est le motif « un contrôle peut
passer en regardant ailleurs » d'AGENTS.md, appliqué aux dépendances.

Ce test est STATIQUE : il lit les imports par AST et les manifestes par tomllib,
sans rien importer. Il tourne donc dans la CI légère, là où les paquets ML sont
justement absents.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[2]
SOURCES = ("core", "api")
# `tools` : paquet du dépôt (`tools/__init__.py`) ; `api/cli.py` y reprend le relevé
# « l'application tourne-t-elle ? » de `tools/sauvegarder.py`.
LOCAUX = {"core", "api", "tests", "tools"}

# Nom d'import -> nom de distribution, quand les deux diffèrent.
DISTRIBUTION = {
    "yaml": "pyyaml",
    "argon2": "argon2-cffi",
    "sqlite_vec": "sqlite-vec",
    "pyannote": "pyannote.audio",
    "mlx_whisper": "mlx-whisper",
    # `core/llm_mlx.py` LANCE `mlx_lm.server` en sous-processus et ne l'importe
    # pas — il n'a aucune raison de payer un runtime MLX dans le processus
    # parent alors que c'est l'enfant qui tient les poids. L'entrée est
    # néanmoins là, et c'est un GARDE : le jour où quelqu'un écrira
    # `import mlx_lm` dans une méthode, ce balayage (qui descend l'AST entier)
    # le verra, et sans cette ligne il chercherait une distribution nommée
    # « mlx_lm » — qui n'existe pas — et rougirait sur un import légitime.
    "mlx_lm": "mlx-lm",
}

# Imports volontairement NON déclarés, avec leur raison. Chacun doit être gardé
# par un `try/except ImportError` menant à un chemin de repli réel — un import
# optionnel non gardé est un plantage qui attend son heure.
OPTIONNELS = {
    "mistralai": "SDK Mistral : jamais installé ici, repli HTTP via `requests` "
                 "dans core/stt_mistral.py et core/llm_mistral.py",
    "huggingface_hub": "lecture SEULE du cache pour ANNONCER un téléchargement "
                       "de modèle au premier usage ; présent "
                       "transitivement avec la stack ML, repli gardé si absent "
                       "dans core/stt_whisper.py et core/pipeline.py",
}


def _imports_tiers() -> dict[str, set[str]]:
    trouves: dict[str, set[str]] = {}
    for dossier in SOURCES:
        for fichier in sorted((ROOT / dossier).glob("*.py")):
            arbre = ast.parse(fichier.read_text(encoding="utf-8"))
            for noeud in ast.walk(arbre):
                if isinstance(noeud, ast.Import):
                    noms = [a.name.split(".")[0] for a in noeud.names]
                elif isinstance(noeud, ast.ImportFrom) and noeud.level == 0 and noeud.module:
                    noms = [noeud.module.split(".")[0]]
                else:
                    continue
                for nom in noms:
                    if nom and nom not in sys.stdlib_module_names and nom not in LOCAUX:
                        trouves.setdefault(nom, set()).add(
                            f"{dossier}/{fichier.name}")
    return trouves


def _declarees() -> set[str]:
    with (ROOT / "pyproject.toml").open("rb") as flux:
        manifeste = tomllib.load(flux)
    brut = list(manifeste["project"]["dependencies"])
    for groupe in manifeste.get("dependency-groups", {}).values():
        brut += list(groupe)
    return {d.split("==")[0].split(">")[0].split("[")[0].strip().lower() for d in brut}


def test_aucun_import_tiers_non_declare() -> None:
    declarees = _declarees()
    manquants = {}
    for module, fichiers in _imports_tiers().items():
        if module in OPTIONNELS:
            continue
        distribution = DISTRIBUTION.get(module, module).lower()
        if distribution not in declarees:
            manquants[module] = (distribution, sorted(fichiers))

    assert not manquants, (
        "importés par le code mais absents de pyproject.toml :\n"
        + "\n".join(f"  {m} (distribution « {d} ») — {', '.join(f)}"
                    for m, (d, f) in sorted(manquants.items()))
        + "\nDéclarer la dépendance, ou l'ajouter à OPTIONNELS avec sa raison "
          "ET un repli gardé par try/except ImportError."
    )


def test_les_optionnels_sont_gardes() -> None:
    """Un optionnel non gardé n'est pas optionnel, c'est un plantage différé."""
    for module in OPTIONNELS:
        fichiers = _imports_tiers().get(module, set())
        assert fichiers, f"« {module} » listé comme optionnel mais jamais importé"
        for chemin in fichiers:
            source = (ROOT / chemin).read_text(encoding="utf-8")
            arbre = ast.parse(source)
            gardes = []
            for noeud in ast.walk(arbre):
                if not isinstance(noeud, ast.Try):
                    continue
                attrape = any(
                    h.type is not None and "ImportError" in ast.dump(h.type)
                    for h in noeud.handlers
                )
                if attrape and module in ast.dump(ast.Module(noeud.body, [])):
                    gardes.append(noeud)
            assert gardes, (
                f"{chemin} importe « {module} » sans try/except ImportError. "
                "Sans repli, l'absence du paquet plante au lieu de dégrader."
            )


def test_le_manifeste_pip_reste_synchrone() -> None:
    """`requirements.txt` doit suivre — c'est le manifeste que lit un humain."""
    direct = []
    for ligne in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        nue = ligne.split("#", 1)[0].strip()
        if nue and not nue.startswith("-r "):
            direct.append(nue.split("==")[0].strip().lower())
    with (ROOT / "pyproject.toml").open("rb") as flux:
        attendu = {d.split("==")[0].strip().lower()
                   for d in tomllib.load(flux)["project"]["dependencies"]}

    assert set(direct) == attendu


def test_le_balayage_des_imports_VOIT_bien_quelque_chose() -> None:
    """ANCRE DE NON-VACUITÉ — motif nº2, signalée par le test-méta.

    `assert manquants == {}` reste vert si `_imports_tiers()` cesse de trouver
    quoi que ce soit : dossier renommé, `glob` non récursif, AST changé. Ce
    fichier a DÉJÀ été pris en défaut — il comparait à l'union de tous
    les groupes de dépendances au lieu du profil réellement installé, et
    passait pendant que la CI n'exécutait aucun test.
    """
    trouves = _imports_tiers()
    assert len(trouves) >= 8, (
        f"seulement {len(trouves)} import(s) tiers vu(s) dans {SOURCES} — "
        f"le balayage ne regarde plus le bon code : {sorted(trouves)}")
    for attendu in ("numpy", "requests", "yaml"):
        assert attendu in trouves, (
            f"{attendu} n'est plus vu — l'extraction d'imports est cassée")
    assert len(_declarees()) >= 15
