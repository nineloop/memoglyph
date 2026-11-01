"""Le profil que la CI installe doit suffire à COLLECTER toute la suite.

Panne d'origine : `tests/unit/test_empreintes_bornes.py` a été ajouté
avec `import numpy as np` au niveau module. `numpy` est bien dans
`[project].dependencies` — mais la CI n'installe QUE le groupe `dev`
(`uv sync --only-group dev --locked`), où il ne figurait pas.

Conséquence disproportionnée à la cause : un import raté au niveau module
**interrompt la collecte pytest entière**. Pas un test de moins — *zéro test*.
`collected 896 items / 1 error` puis `Interrupted`. Quinze runs consécutifs
ont rapporté « échec » sans avoir rien exécuté, sur dix-huit heures, pendant
que les exécutions locales restaient vertes.

## Pourquoi ce test et pas `test_imports_declares.py`

Ce dernier a été écrit CONTRE cette classe de défaut, et il **passait** dans
l'environnement où numpy manquait. Il compare les imports à l'UNION de
`[project].dependencies` et de tous les `dependency-groups` : numpy y figurait,
donc « déclaré ». Il ne confronte jamais rien au profil RÉELLEMENT installé.

C'est le motif d'AGENTS.md — un contrôle peut passer en regardant ailleurs — et
il visait pourtant le bon sujet. La bonne question n'était pas « est-ce
déclaré quelque part ? » mais « est-ce installé là où ça tourne ? ».

## Portée délibérément étroite

Seuls les imports de **niveau module** comptent : eux seuls s'exécutent à la
collecte. Un import dans une fonction, ou gardé par `importorskip`, fait au
pire échouer SON test — la CI le dit alors clairement, test par test. C'est la
cécité totale qu'on interdit ici, pas l'échec.

Corollaire souvent ignoré : les marqueurs ne protègent pas. `-m "not ml"`
DÉSÉLECTIONNE après la collecte ; un fichier marqué `ml` est importé quand
même. Un module-level `import torch` dans `tests/ml/` casserait tout.
"""

from __future__ import annotations

import ast
import contextlib
import sys
import tomllib
from pathlib import Path

from tests.hors_export import exclus_de_l_export

RACINE = Path(__file__).parents[2]

# Ce que la CI installe : `.github/workflows/tests.yml` → `--only-group dev`.
GROUPE_CI = "dev"

# Ce que le contrôle BALAIE — et c'était son trou.
#
# Mesuré : les deux boucles faisaient `RACINE.glob("tests/**/*.py")`.
# `core/` et `api/` n'étaient JAMAIS regardés. Or la panne de collecte ne tient
# pas au dossier : elle tient à ce qu'un import de niveau module RATÉ
# interrompt la collecte entière. Un `import torch` posé au niveau module dans
# `core/embed.py` casse la collecte exactement pareil — la suite importe
# `core` partout, à commencer par `tests/conftest.py` — et le garde né de cette
# panne ne l'aurait pas vu.
# `run_ml_tests.py` vit à la RACINE,
# donc aucun des trois motifs de dossier ne l'attrapait — et
# `tests/unit/test_ml_runner.py` l'importe AU NIVEAU MODULE. Ses imports
# s'exécutent donc pendant la collecte CI, à l'endroit exact où un import
# manquant interrompt tout, sans qu'aucune garde ne les confronte au groupe
# `dev`. C'est le trou que cette garde existe pour fermer, laissé ouvert dans
# son propre balayage.
MOTIFS_SURVEILLES = (
    "tests/**/*.py", "core/**/*.py", "api/**/*.py", "run_ml_tests.py",
)

# Modules du dépôt lui-même, jamais des dépendances.
LOCAUX = {"core", "api", "tests", "tools", "emplacement", "run_ml_tests", "conftest"}

# Module QUALIFIÉ -> nom de distribution. La clé garde ses points, et la
# résolution prend le PRÉFIXE LE PLUS LONG.
#
# La version précédente réduisait l'import à son premier segment : un
# `from pyannote.audio import Pipeline` devenait « pyannote », traduit en
# `pyannote.core`, présent dans `dev` — donc accepté. Or `pyannote.audio` n'est
# PAS installé en CI. Reproduit avant correction : le garde annonçait
# « 3 passed » pendant que la CI retombait à ZÉRO test, la panne exacte qu'il
# existe pour empêcher.
#
# `pyannote` est un espace de noms partagé par deux distributions dont une
# seule est légère. Confondre les deux, c'est confondre une structure de
# données et un runtime ML de plusieurs gigaoctets.
DISTRIBUTION = {
    "yaml": "pyyaml",
    "argon2": "argon2-cffi",
    "sqlite_vec": "sqlite-vec",
    "pyannote.core": "pyannote.core",
    "pyannote.audio": "pyannote.audio",
    "mlx_whisper": "mlx-whisper",
}


def _distribution(module_qualifie: str) -> str:
    """Préfixe le plus long connu, sinon le premier segment.

    `pyannote.audio.pipelines` -> `pyannote.audio` (connu, longueur 2)
    `pyannote.core`            -> `pyannote.core`
    `numpy`                    -> `numpy`
    """
    parties = module_qualifie.split(".")
    for i in range(len(parties), 0, -1):
        prefixe = ".".join(parties[:i])
        if prefixe in DISTRIBUTION:
            return DISTRIBUTION[prefixe].lower()
    return parties[0].lower()


def _imports_de_niveau_module(fichier: Path) -> set[str]:
    """Uniquement `arbre.body` : ce qui s'exécute à l'import du module.

    Descendre dans l'AST entier (`ast.walk`) rendrait aussi les imports
    contenus dans les fonctions — or ceux-là ne cassent pas la collecte, et les
    inclure rendrait le test bruyant au point d'être désactivé.
    """
    arbre = ast.parse(fichier.read_text(encoding="utf-8"))
    # Modules QUALIFIÉS : `pyannote.audio` doit rester distinguable de
    # `pyannote.core`. Tronquer au premier point perd exactement l'information
    # qui sépare un paquet léger d'un runtime ML.
    noms: set[str] = set()
    for noeud in arbre.body:
        if isinstance(noeud, ast.Import):
            noms |= {a.name for a in noeud.names}
        elif isinstance(noeud, ast.ImportFrom) and noeud.level == 0 and noeud.module:
            noms.add(noeud.module)
    return {
        n for n in noms
        if n and n.split(".")[0] not in sys.stdlib_module_names
        and n.split(".")[0] not in LOCAUX
    }


def _groupe_ci() -> set[str]:
    with (RACINE / "pyproject.toml").open("rb") as flux:
        manifeste = tomllib.load(flux)
    groupe = manifeste["dependency-groups"][GROUPE_CI]
    return {
        d.split("==")[0].split(">")[0].split("[")[0].strip().lower()
        for d in groupe
    }


def _fichiers_surveilles(racine: Path = RACINE, *, exportes: bool = True) -> list[Path]:
    """Tout ce que la collecte pytest IMPORTE, pas seulement `tests/`.

    Un seul point de vérité pour le balayage : la garde de non-vacuité ci-
    dessous lit la MÊME liste. Deux boucles indépendantes, c'est deux motifs à
    corriger — et une seule des deux l'avait été. Les fichiers exclus de l'export
    (`tests/hors_export.py`, `exportes=False`) ont leur propre contrôle.
    """
    exclus = exclus_de_l_export(racine)
    return sorted({f for motif in MOTIFS_SURVEILLES for f in racine.glob(motif)
                   if (f.relative_to(racine).as_posix() not in exclus) is exportes})


def _manquants(
    fichiers: list[Path], installables: set[str], racine: Path = RACINE
) -> dict[str, list[str]]:
    """Distributions importées au niveau module et absentes du profil CI."""
    manquants: dict[str, list[str]] = {}
    for fichier in fichiers:
        for module in _imports_de_niveau_module(fichier):
            distribution = _distribution(module)
            if distribution not in installables:
                manquants.setdefault(distribution, []).append(
                    str(fichier.relative_to(racine)))
    return manquants


def test_le_groupe_ci_couvre_tout_import_de_niveau_module_des_tests() -> None:
    # `rglob`, pas `glob` : `tests/unit/`, `tests/integration/` et `tests/ml/`
    # sont tous collectés. Le contrôle voisin utilise `glob`, qui n'aurait vu
    # aucun sous-dossier.
    manquants = _manquants(_fichiers_surveilles(), _groupe_ci())
    assert manquants == {}, (
        "import(s) de niveau module absent(s) du groupe `dev` : la COLLECTE "
        "pytest s'interrompra en CI et AUCUN test ne s'exécutera — "
        f"{manquants}"
    )


def test_le_controle_porte_bien_sur_quelque_chose() -> None:
    """Garde contre un test vert sur un balayage vide.

    Si `rglob` cessait de trouver des fichiers, ou si l'extraction d'imports
    rendait toujours l'ensemble vide, le test précédent passerait sans rien
    vérifier. C'est exactement la panne qu'on répare ici — appliquée à son
    propre instrument.
    """
    fichiers = _fichiers_surveilles()
    assert len(fichiers) > 40, f"balayage suspect : {len(fichiers)} fichier(s)"

    tous = set()
    for fichier in fichiers:
        tous |= _imports_de_niveau_module(fichier)
    assert "pytest" in tous, "aucun import tiers détecté — l'extraction est cassée"
    assert len(tous) >= 3, f"seulement {tous} — l'extraction rate des imports"


def test_les_sous_dossiers_de_tests_sont_bien_balayes() -> None:
    """`glob('tests/*.py')` ne verrait ni `unit/`, ni `integration/`, ni `ml/`.

    Le fichier fautif de la panne de collecte était dans `tests/unit/` : un balayage
    non récursif serait resté vert sur la panne exacte qu'il doit empêcher.
    """
    # Dérivé du balayage RÉEL, pas d'un `glob` parallèle : un motif retiré de
    # `MOTIFS_SURVEILLES` doit faire tomber ce test aussi, sinon il resterait
    # vert en regardant une liste que le contrôle n'utilise plus.
    dossiers = {
        f.parent.name for f in _fichiers_surveilles()
        if f.parent != RACINE / "tests" and RACINE / "tests" in f.parents
    }
    assert {"unit", "integration"} <= dossiers, f"sous-dossiers vus : {dossiers}"


# ---------- Le trou : `core/` et `api/` n'étaient pas balayés ----------

@contextlib.contextmanager
def _sonde(chemin_relatif: str, source: str):
    """Écrit un module JETABLE dans le vrai dépôt, le temps d'un balayage.

    Le contrôle glob la racine RÉELLE : le seul moyen de prouver qu'il regarde
    `core/` est d'y poser un fichier et de vérifier qu'il le voit. Un test qui
    passerait une liste synthétique à `_manquants()` prouverait que
    `_manquants()` fonctionne — pas que `core/` est balayé. C'est exactement la
    substitution qui a rendu `test_imports_declares.py` vert sur la panne.

    Nom préfixé d'un underscore : pytest ne le collecte pas, et rien ne
    l'importe. Retiré dans un `finally`.
    """
    fichier = RACINE / chemin_relatif
    assert not fichier.exists(), f"{chemin_relatif} existe déjà — sonde annulée"
    fichier.write_text(source, encoding="utf-8")
    try:
        yield
    finally:
        fichier.unlink()


def test_un_import_lourd_de_NIVEAU_MODULE_dans_core_est_vu() -> None:
    """LE cas exact : la panne de collecte déplacée dans `core/`.

    `torch` est dans `[project].dependencies` et PAS dans le groupe `dev` —
    la situation de `numpy` au mot près. Un `import torch` au niveau module
    dans `core/` interrompt la collecte pytest entière en CI : zéro test
    exécuté, dix-huit heures de vert sur rien.

    Mesuré avant correction : le garde ne globait que `tests/**/*.py` et cette
    sonde lui était INVISIBLE.
    """
    with _sonde("core/_sonde_profil_ci.py", "import torch\n"):
        manquants = _manquants(_fichiers_surveilles(), _groupe_ci())
    assert "torch" in manquants, (
        "un import de niveau module absent du profil CI, posé dans `core/`, "
        f"n'a pas été vu — balayage : {MOTIFS_SURVEILLES}")
    assert manquants["torch"] == ["core/_sonde_profil_ci.py"]


def test_le_meme_import_dans_api_est_vu() -> None:
    """`api/` est importé par les tests de routes : même conséquence."""
    with _sonde("api/_sonde_profil_ci.py", "from torch import nn\n"):
        manquants = _manquants(_fichiers_surveilles(), _groupe_ci())
    assert manquants.get("torch") == ["api/_sonde_profil_ci.py"], manquants


def test_l_import_DIFFERE_de_core_reste_vert() -> None:
    """Contrepartie indispensable, sinon le contrôle serait retiré.

    `core/stt_whisper.py`, `core/diarization.py` et `core/embed.py` importent
    leurs dépendances lourdes DANS les fonctions, exprès. Ce code est correct :
    il ne s'exécute pas à la collecte. Un garde qui rougirait dessus punirait
    la bonne pratique du dépôt et finirait désactivé.
    """
    with _sonde("core/_sonde_profil_ci.py",
                "def charger():\n    import torch\n    return torch\n"):
        manquants = _manquants(_fichiers_surveilles(), _groupe_ci())
    assert manquants == {}, f"import différé signalé à tort : {manquants}"


def test_un_import_de_niveau_module_PRESENT_dans_dev_reste_vert() -> None:
    """`sqlite_vec` est au niveau module dans `core/sqlite_db.py` et figure
    dans `dev` : le contrôle doit le laisser passer, sans quoi il rougirait sur
    l'état sain du dépôt."""
    with _sonde("core/_sonde_profil_ci.py", "import sqlite_vec\n"):
        manquants = _manquants(_fichiers_surveilles(), _groupe_ci())
    assert manquants == {}, manquants


def test_core_et_api_sont_REELLEMENT_dans_le_balayage() -> None:
    """Ancre de non-vacuité : les sondes ci-dessus passeraient aussi si le
    balayage voyait `core/` mais plus `tests/`."""
    vus = {p.parts[0] for p in
           (f.relative_to(RACINE) for f in _fichiers_surveilles())}
    assert {"tests", "core", "api"} <= vus, f"racines balayées : {vus}"


def test_deux_distributions_du_meme_espace_de_noms_ne_se_confondent_pas() -> None:
    """`pyannote.audio` (runtime ML lourd) vs `pyannote.core` (structures).

    Une seule des deux est dans le profil CI. Les confondre laissait passer
    l'import qui remet la collecte à zéro — vérifié avant correction.
    """
    assert _distribution("pyannote.core") == "pyannote.core"
    assert _distribution("pyannote.audio") == "pyannote.audio"
    assert _distribution("pyannote.audio.pipelines") == "pyannote.audio"
    assert _distribution("pyannote.audio") not in _groupe_ci(), (
        "pyannote.audio ne doit PAS être dans le profil CI — s'il y entrait, "
        "ce test devrait être revu, pas supprimé")
    assert _distribution("pyannote.core") in _groupe_ci()


def test_la_resolution_par_prefixe_ne_casse_pas_les_cas_simples() -> None:
    assert _distribution("numpy") == "numpy"
    assert _distribution("yaml") == "pyyaml"
    assert _distribution("sqlite_vec") == "sqlite-vec"
    assert _distribution("fastapi.testclient") == "fastapi"


# ---------- Le portail CI : ce qu'il exécute RÉELLEMENT ----------

def _makefile() -> str:
    return (RACINE / "Makefile").read_text(encoding="utf-8")


def _workflow() -> str:
    return (RACINE / ".github/workflows/tests.yml").read_text(encoding="utf-8")


def _etapes_ci() -> list[dict]:
    """Étapes RÉELLES du workflow, lues par le parseur YAML.

    Les commentaires n'en font pas partie — et c'est tout l'intérêt. Une garde
    qui cherche une sous-chaîne dans le fichier BRUT est satisfaite par les
    commentaires du workflow : le compte de `if: always()` annonçait 6 là où la
    réalité était 5, la sixième occurrence étant le commentaire écrit au-dessus
    de l'option.

    Même défaut que `assert col in _SCHEMA`, et que le premier `fetch-depth`
    cherché comme sous-chaîne.
    """
    import yaml

    return yaml.safe_load(_workflow())["jobs"]["test"]["steps"]


def test_la_couverture_a_un_PLANCHER_et_pas_seulement_un_affichage() -> None:
    """Sans `--cov-fail-under`, `make test-cov` rendait 0 quel que soit le chiffre.

    Un module de 1600 lignes devenu mort faisait tomber la couverture de 82 à
    68 % — exit 0. Le plancher n'est pas une cible à atteindre, c'est une alarme
    de CHUTE. Règle : plancher à 80, sous une mesure réelle de 82 %.
    """
    assert "--cov-fail-under=" in _makefile(), (
        "le plancher de couverture a disparu du Makefile — `test-cov` n'est "
        "plus qu'un affichage")


def test_le_plancher_de_couverture_reste_SOUS_la_mesure_reelle() -> None:
    """Garde sur l'instrument : un plancher au-dessus du réel casserait la CI
    à chaque exécution, et finirait retiré pour cette raison."""
    import re

    correspondance = re.search(r"--cov-fail-under=(\d+)", _makefile())
    assert correspondance, "plancher introuvable"
    plancher = int(correspondance.group(1))
    assert 50 <= plancher <= 90, (
        f"plancher à {plancher} % — trop bas il ne dit rien, trop haut il "
        f"bloque tout")


def test_la_CI_appelle_REELLEMENT_les_cibles_du_Makefile() -> None:
    """Une cible que rien n'appelle ne vérifie rien.

    `make test-cov` existait depuis longtemps et n'était appelé ni par la CI,
    ni par le hook, ni par un script : `pytest-cov` était installé en CI pour
    rien. Une cible orpheline est une porte morte.
    """
    commandes = " \n".join(str(e.get("run", "")) for e in _etapes_ci())
    for cible in ("make lock-check", "make lint-python", "make test-python",
                  "make test-ui", "make test-cov"):
        assert cible in commandes, (
            f"`{cible}` n'apparaît dans aucun champ `run` — la cible existe "
            f"mais rien ne la franchit. Un commentaire ne compte pas.")


def test_les_etapes_de_CI_ne_se_masquent_pas() -> None:
    """Un échec Python effaçait toute preuve côté interface : 113 tests, le
    typecheck et le build n'ont pas tourné pendant 19 push."""
    # Vérification étape par ÉTAPE, pas par décompte global.
    #
    # Un décompte satisfait par un commentaire annonçait 6 pour 5 réelles, et
    # retirer l'option d'une étape le laissait vert. On nomme les étapes qui
    # doivent survivre à un échec précédent.
    par_commande = {
        str(e.get("run", "")).strip(): e for e in _etapes_ci() if e.get("run")
    }
    for commande in ("make lint-python", "make test-python", "make test-cov",
                     "make test-ui"):
        etape = next((e for c, e in par_commande.items() if commande in c), None)
        assert etape is not None, f"étape `{commande}` introuvable"
        assert str(etape.get("if", "")).strip() == "always()", (
            f"l'étape `{commande}` n'est pas en `if: always()` — un échec "
            f"précédent la masquerait, et l'on ne saurait rien d'elle")


def test_la_CI_recupere_l_HISTORIQUE_git() -> None:
    """`tests/unit/test_porte_ml.py` compte les commits depuis le dernier
    passage ML. Le défaut d'`actions/checkout` est un clone d'un seul commit.

    Le YAML est PARSÉ, pas cherché comme sous-chaîne.

    Une version naïve faisait `"fetch-depth: 0" in _workflow()` — et le
    commentaire écrit juste au-dessus de l'option la satisfaisait à lui seul.
    Passer la valeur à 1 ne faisait tomber aucun test. Même défaut que
    `assert col in _SCHEMA`, qu'un commentaire du DDL satisfaisait.
    """
    import yaml

    config = yaml.safe_load(_workflow())
    etapes = config["jobs"]["test"]["steps"]
    checkout = [e for e in etapes if "checkout" in str(e.get("uses", ""))]
    assert checkout, "aucune étape `actions/checkout` dans le workflow"
    profondeur = (checkout[0].get("with") or {}).get("fetch-depth")
    assert profondeur == 0, (
        f"`fetch-depth: {profondeur}` — clone superficiel, le contrôle de la "
        f"porte ML ne peut rien mesurer")
