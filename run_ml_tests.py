#!/usr/bin/env python3
"""Supervise les tests ML sans charger les bibliothèques natives.

Le processus parent ne fait que vérifier les prérequis puis lancer un module
pytest par sous-processus. Un crash natif de MLX/Metal reste ainsi contenu et
peut être traduit en diagnostic lisible.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import signal
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import functools
from pathlib import Path

from core.config import getenv_compat


ROOT = Path(__file__).resolve().parent
# Corpus de démo public (`tools/corpus_demo.py`) : la réunion de la vérité
# terrain et l'archive de ses annotations.
AMI_FICHIERS = ("IS1009a.Mix-Headset.wav", "ami_public_manual_1.6.2.zip")
# Cette liste était écrite à la main, et elle avait DÉJÀ divergé.
#
# `tests/unit/test_embed.py` porte le marqueur `ml` et n'y figurait pas : la
# suite rapide le désélectionne, ce lanceur l'ignore. Seul
# contrôle que les vecteurs d'embedding sont unitaires — prémisse du bras dense
# de la fusion RRF — il n'a JAMAIS tourné une seule fois.
#
# On dérive maintenant de la collecte pytest : la réalité, pas une déclaration.
ML_MODULES_DECLARES = (
    "tests/ml/test_stt_real.py",
    "tests/ml/test_ami_reponse_connue.py",
    "tests/ml/test_llm_mlx_reel.py",
)


class CollecteMLImpossible(RuntimeError):
    """La collecte des modules `ml` n'a pas abouti — on ne devine pas."""


class PrerequisNonDeclares(RuntimeError):
    """Un module `ml` collecté dont personne n'a déclaré les prérequis.

    On REFUSE de construire un plan, plutôt que de le supposer exécutable ou
    de l'ignorer. Voir la justification au-dessus de `prerequis` dans
    `preflight()` : c'est la seule issue qui ne puisse pas rendre du vert.
    """


@functools.lru_cache(maxsize=1)
def modules_marques_ml() -> tuple[str, ...]:
    """Fichiers réellement porteurs du marqueur `ml`, demandés à pytest.

    PARESSEUX et mémoïsé, et c'est nécessaire : calculé à l'import, il lançait
    un sous-processus pytest à chaque import de ce module — y compris depuis
    `tests/unit/test_ml_runner.py`, ce qui faisait exploser la suite rapide.
    Une correction de motif ne doit pas coûter ce qu'elle fait gagner.
    """
    import subprocess

    sortie = subprocess.run(
        [sys.executable, "-m", "pytest", "-m", "ml", "--collect-only", "-q",
         "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
    )
    # Le code retour était IGNORÉ, et les deux issues étaient muettes :
    #   · collecte en erreur APRÈS quelques nœuds → liste PARTIELLE promue au
    #     rang de réalité ;
    #   · collecte en erreur sans nœud → repli sur `ML_MODULES_DECLARES`,
    #     précisément la liste écrite à la main dont ce correctif voulait sortir.
    # Dans les deux cas le module orphelin restait ignoré, sans un mot.
    if sortie.returncode != 0:
        raise CollecteMLImpossible(
            f"la collecte pytest des tests `ml` a échoué (code "
            f"{sortie.returncode}). Aucun repli sur une liste écrite à la main : "
            f"elle masquerait les modules non déclarés.\n"
            f"stderr : {(sortie.stderr or '').strip()[:500]}"
        )
    fichiers = {
        ligne.split("::", 1)[0].strip()
        for ligne in sortie.stdout.splitlines()
        if "::" in ligne and ligne.strip().startswith("tests/")
    }
    if not fichiers:
        raise CollecteMLImpossible(
            "la collecte pytest n'a rendu AUCUN test `ml`. Soit le marqueur a "
            "disparu, soit la commande ne regarde plus le bon dossier — les "
            "deux méritent un échec, pas un repli."
        )
    return tuple(sorted(fichiers))


def ml_modules() -> tuple[str, ...]:
    """Les modules à exécuter, DÉRIVÉS de la collecte pytest.

    Aucun repli : une collecte qui échoue lève. Un lanceur qui devine sa propre
    liste ne peut pas signaler qu'un module lui échappe — c'est exactement ce
    qui a laissé `tests/unit/test_embed.py` sans exécution.
    """
    return modules_marques_ml()


# Compatibilité : `ML_MODULES` reste le nom lu ailleurs, mais il ne DÉCLARE
# plus rien — il rend ce que pytest a réellement marqué.
ML_MODULES = ML_MODULES_DECLARES
_AUDIO_EXTENSIONS = {".m4a", ".wav", ".mp3", ".flac", ".ogg"}


@dataclass(frozen=True)
class ModulePlan:
    """Décision de préflight pour un module ML isolé."""

    path: str
    missing: tuple[str, ...] = ()

    @property
    def runnable(self) -> bool:
        return not self.missing


def _reference_audio(root: Path, env: Mapping[str, str]) -> Path | None:
    configured = getenv_compat("ML_AUDIO", environ=env)
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = root / path
        return path if path.is_file() else None

    fixtures = root / "tests" / "fixtures" / "audio"
    if fixtures.is_dir():
        return next(
            (
                path
                for path in sorted(fixtures.iterdir())
                if path.is_file() and path.suffix.lower() in _AUDIO_EXTENSIONS
            ),
            None,
        )
    return None


def modele_mlx_dans_le_cache(modele: str, env: Mapping[str, str]) -> bool:
    """Le modèle MLX est-il DÉJÀ téléchargé ? Sans réseau, sans import.

    Sans ce fait, le module LLM/MLX partirait « exécutable », `importorskip`
    passerait (le paquet, lui, est là), et chaque test sauterait faute de poids.
    Le lanceur rendrait alors l'échec « AUCUN TEST EXÉCUTÉ » — bruyant plutôt
    que faux, mais inexploitable en mode optionnel, où l'on veut IGNORER
    proprement ce qui n'est pas installé.

    On lit l'arborescence du cache Hugging Face plutôt que d'appeler l'API :
    un préflight ne doit ni télécharger 5 Go ni dépendre du réseau.
    """
    # Chemin local : c'est un dossier, on le regarde tel quel.
    #
    # Le critère était `"/" not in modele`, ce qui excluait TOUT chemin réel :
    # `~/modeles/qwen` ou `/opt/mlx/qwen` contiennent des barres, partaient donc
    # dans la branche Hugging Face et étaient cherchés sous
    # `models--Users--…`, où ils n'existent évidemment pas. Le module était
    # déclaré non exécutable alors que les poids étaient là — et la docstring
    # promettait l'inverse.
    #
    # Le test « est-ce un dossier ? » est plus sûr que toute heuristique sur la
    # forme du nom : un identifiant Hugging Face ne désigne jamais un dossier
    # existant, et un chemin local, si.
    if Path(modele).expanduser().is_dir():
        return True
    racine = env.get("HF_HUB_CACHE") or (
        f"{env['HF_HOME']}/hub" if env.get("HF_HOME") else None
    ) or f"{env.get('HOME', '~')}/.cache/huggingface/hub"
    depot = Path(racine).expanduser() / f"models--{modele.replace('/', '--')}"
    instantanes = depot / "snapshots"
    return instantanes.is_dir() and any(instantanes.iterdir())


def preflight(
    *,
    root: Path,
    env: Mapping[str, str],
    hf_token: str | None,
    mlx_available: bool,
    transformers_available: bool,
    mlx_lm_available: bool,
    mlx_llm_model: str,
    mlx_llm_model_present: bool,
) -> list[ModulePlan]:
    """Construit le plan sans importer ``mlx_whisper`` ni ``mlx``.

    `embed_backend` et `transformers_available` n'ont PAS de valeur par
    défaut, volontairement : un défaut est exactement ce qui a créé le trou
    nº3 ci-dessous. Le prérequis d'un module se déclare, il ne se suppose pas.

    Les trois faits `mlx_lm_*` suivent la même règle : ils entrent PAR
    ARGUMENT, si bien que `preflight` reste une fonction pure, testable en
    faisant varier un fait à la fois, et indépendante de ce qui est installé
    sur la machine qui la teste.
    """
    stt_missing: list[str] = []
    if _reference_audio(root, env) is None:
        configured = getenv_compat("ML_AUDIO", environ=env)
        if configured:
            stt_missing.append(f"audio MEMOGLYPH_ML_AUDIO absent : {configured}")
        else:
            stt_missing.append(
                "audio absent : définir MEMOGLYPH_ML_AUDIO ou remplir "
                "tests/fixtures/audio/"
            )
    if not mlx_available:
        stt_missing.append("package mlx_whisper absent")

    # Le test AMI à réponse connue lance le pipeline complet (Whisper, pyannote,
    # profilage local) sur le corpus de démo, absent tant qu'on ne l'a pas
    # téléchargé.
    ami_missing = [
        f"corpus de démo absent : demo/ami/{nom} (python tools/corpus_demo.py)"
        for nom in AMI_FICHIERS if not (root / "demo" / "ami" / nom).is_file()
    ]
    if not hf_token:
        ami_missing.append("HF_TOKEN absent")
    if not mlx_available:
        ami_missing.append("package mlx_whisper absent")

    # `tests/unit/test_embed.py` : le test d'un module orphelin, et le seul des
    # trois de `ml-gate.json` dont personne n'avait déclaré le prérequis.
    #
    # Son test `ml` charge un modèle et ne saute PAS tout seul. Le prérequis
    # dépendait du backend, parce que `core/embed.py` routait : en `local` il
    # passe par `transformers`, en `ollama` il n'y touchait jamais.
    #
    # Le repli Ollama est retiré : la vectorisation est
    # LOCALE, sans alternative, et le prérequis est donc inconditionnel. Une
    # branche de moins, et un `elif` qui ne peut plus être pris à tort.
    embed_missing: list[str] = []
    if not transformers_available:
        embed_missing.append("package transformers absent")

    # Les prérequis sont associés par CHEMIN, plus par POSITION.
    #
    # `ML_MODULES[0]` / `[1]` liait le préflight à une liste de longueur deux :
    # trois entrées le cassaient, et un module `ml` non déclaré restait
    # invisible.
    # `tests/ml/test_llm_mlx_reel.py` — le moteur LLM local sur `mlx_lm.server`.
    #
    # Deux prérequis, et aucun n'est devinable depuis le module lui-même : le
    # paquet (`importorskip` le couvre, mais le préflight doit le savoir pour
    # IGNORER proprement) et les poids du modèle, dont l'absence ferait sauter
    # tous les tests sans que pytest rende autre chose que 0.
    mlx_llm_missing: list[str] = []
    if not mlx_lm_available:
        mlx_llm_missing.append("package mlx_lm absent")
    if not mlx_llm_model_present:
        mlx_llm_missing.append(f"modèle MLX absent du cache : {mlx_llm_model}")

    # Le module AMI à réponse connue PROFILE, donc il a besoin du LLM local.
    # Non déclaré, ce prérequis reproduit le « trou nº2 » décrit plus bas : le
    # module part, ses tests sautent faute de moteur, et `make test-ml` n'y
    # voit rien tant que le lanceur ne refuse pas les modules entièrement mués.
    ami_missing.extend(mlx_llm_missing)

    prerequis = {
        "tests/ml/test_stt_real.py": tuple(stt_missing),
        "tests/ml/test_ami_reponse_connue.py": tuple(ami_missing),
        "tests/ml/test_llm_mlx_reel.py": tuple(mlx_llm_missing),
        "tests/unit/test_embed.py": tuple(embed_missing),
    }

    # Trou nº3 : `prerequis.get(chemin, ())` réputait
    # EXÉCUTABLE tout module inconnu. Le commentaire précédent défendait ce
    # défaut — « mieux vaut le lancer et le voir échouer que l'ignorer » — et
    # se trompait sur le comportement réel : le module partait, ses tests
    # sautaient faute du modèle, pytest rendait EXIT=0, et la porte consignait
    # un passage. Le trou nº2 naissait exactement là.
    #
    # Trois issues possibles pour un module non déclaré :
    #   · exécutable par défaut  → le vert sur rien qu'on vient de décrire ;
    #   · bloqué par défaut      → « IGNORÉ » en mode optionnel, puis EXIT=0
    #                              sur les autres modules : silencieux aussi,
    #                              et le module disparaît de `ml-gate.json`
    #                              sans que rien ne le dise ;
    #   · REFUS de construire un plan → la seule qui ne puisse pas rendre du
    #                              vert. Déclarer un prérequis coûte deux
    #                              lignes ; ne pas le déclarer coûtait trois
    #                              jours de porte aveugle.
    #
    # Comme `CollecteMLImpossible`, l'exception remonte : `make test-ml` sort
    # en erreur en nommant le fichier à déclarer.
    non_declares = [chemin for chemin in ml_modules() if chemin not in prerequis]
    if non_declares:
        raise PrerequisNonDeclares(
            "module(s) `ml` collecté(s) sans prérequis déclarés : "
            f"{', '.join(non_declares)}. Déclarez-les dans `prerequis` de "
            "`preflight()` (run_ml_tests.py) — un module dont on ignore les "
            "prérequis part en aveugle, saute tous ses tests et rend EXIT=0, "
            "ce que la porte lirait comme un succès."
        )
    return [ModulePlan(chemin, prerequis[chemin]) for chemin in ml_modules()]


def runtime_preflight(*, strict: bool, root: Path = ROOT) -> list[ModulePlan]:
    """Vérifie fichiers et services avant de seulement localiser le paquet MLX."""
    from core import config

    config.load_env()
    # Pas de sonde Ollama dans le préflight : plus aucun
    # module ML n'en dépend, ni pour le profilage ni pour les embeddings.
    #
    # find_spec localise le paquet sans exécuter son code natif.
    mlx_available = importlib.util.find_spec("mlx_whisper") is not None
    transformers_available = importlib.util.find_spec("transformers") is not None
    # Même précaution pour `mlx_lm` : `find_spec` LOCALISE sans exécuter le code
    # natif, donc sans charger Metal dans le processus de préflight.
    mlx_lm_available = importlib.util.find_spec("mlx_lm") is not None
    return preflight(
        root=root,
        env=os.environ,
        hf_token=config.HF_TOKEN,
        mlx_available=mlx_available,
        transformers_available=transformers_available,
        mlx_lm_available=mlx_lm_available,
        mlx_llm_model=config.MLX_LLM_MODEL,
        mlx_llm_model_present=modele_mlx_dans_le_cache(config.MLX_LLM_MODEL, os.environ),
    )


@dataclass(frozen=True)
class ResultatModule:
    """Ce qu'un module a RENDU — pas seulement s'il a échoué.

    Le code retour ne distingue pas « tout est passé » de « tout a été sauté » :
    les deux valent 0. Le compte est donc porté séparément.
    """

    returncode: int
    executes: int
    sautes: int = 0


def _compte_junit(rapport: Path) -> tuple[int, int]:
    """(exécutés, sautés) lus dans le rapport JUnit écrit par pytest.

    POURQUOI le XML et pas la ligne de résumé de `-q` : cette ligne est un
    format d'AFFICHAGE — libellés traduits, pluriels, couleurs, ordre variable
    selon les versions — et la lire imposerait de troquer `-v`, qui est ce que
    l'humain lit quand un modèle plante. `--junit-xml` est un contrat machine
    produit par pytest lui-même, lisible par la stdlib (`xml.etree`), sans une
    dépendance de plus et sans toucher à la sortie affichée.

    Un rapport ABSENT ou illisible rend (0, 0), donc « rien exécuté », donc un
    échec. Le défaut penche du côté qui ALERTE : un compteur en panne qui
    rendrait « tout va bien » serait le trou qu'on est en train de boucher.
    """
    import xml.etree.ElementTree as ET

    try:
        racine = ET.parse(rapport).getroot()
    except Exception:  # noqa: BLE001 — fichier absent, tronqué, illisible
        return (0, 0)
    suites = [racine] if racine.tag == "testsuite" else racine.findall("testsuite")
    total = sum(int(s.get("tests", 0)) for s in suites)
    sautes = sum(int(s.get("skipped", 0)) for s in suites)
    return (max(total - sautes, 0), sautes)


def run_pytest_module(
    path: str,
    *,
    subprocess_run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> ResultatModule:
    """Lance un seul module ML dans un interpréteur jetable."""
    import tempfile

    with tempfile.TemporaryDirectory() as dossier:
        # Le rapport vit HORS du dépôt : la porte ne doit rien y laisser.
        rapport = Path(dossier) / "junit.xml"
        command = [sys.executable, "-m", "pytest", path, "-m", "ml", "-v",
                   f"--junit-xml={rapport}"]
        result = subprocess_run(command, cwd=ROOT, check=False)
        executes, sautes = _compte_junit(rapport)
    return ResultatModule(result.returncode, executes, sautes)


def vide_diagnostic(path: str, sautes: int) -> str:
    """Un module qui n'a rien exécuté, expliqué à qui lit la sortie."""
    return (
        f"AUCUN TEST EXÉCUTÉ dans {path} ({sautes} sauté(s), pytest a rendu 0). "
        "Un module entièrement sauté est INDISCERNABLE d'un succès pour une "
        "machine — ce n'en est pas un. Un prérequis manque et le préflight ne "
        f"l'a pas vu : compléter `prerequis` dans preflight(), ou installer ce "
        f"qui manque. Détail des skips : {sys.executable} -m pytest {path} "
        "-m ml -v -rs"
    )


def _signal_name(returncode: int) -> str | None:
    signum = -returncode if returncode < 0 else returncode - 128
    if signum <= 0:
        return None
    try:
        return signal.Signals(signum).name
    except ValueError:
        return None


def failure_diagnostic(path: str, returncode: int) -> str:
    """Traduit un retour pytest, notamment un crash natif, en aide concrète."""
    signal_name = _signal_name(returncode)
    if signal_name:
        return (
            f"ÉCHEC NATIF {signal_name} dans {path} (code {returncode}). "
            "Le processus parent est resté actif. Vérifier les logs juste au-dessus, "
            "la mémoire disponible et la compatibilité Metal/MLX ; relancer ensuite "
            f"ce module seul avec : {sys.executable} -m pytest {path} -m ml -v"
        )
    return f"ÉCHEC pytest dans {path} (code {returncode})."


def execute(
    plans: Sequence[ModulePlan],
    *,
    strict: bool,
    run_module: Callable[[str], ResultatModule] = run_pytest_module,
) -> int:
    """Applique le plan : all-or-nothing en strict, best effort en optionnel."""
    blocked = [plan for plan in plans if not plan.runnable]
    for plan in plans:
        if plan.runnable:
            print(f"PRÊT    {plan.path}")
        else:
            label = "BLOQUÉ" if strict else "IGNORÉ"
            print(f"{label}  {plan.path} — {' ; '.join(plan.missing)}")

    if strict and blocked:
        print("\nPRÉFLIGHT STRICT EN ÉCHEC — aucun processus ML n'a été démarré.")
        return 2

    runnable = [plan for plan in plans if plan.runnable]
    if not runnable:
        # `return 0` ici était INDISCERNABLE, pour une machine, de « tous les
        # tests ML sont passés ». Reproduit sur un clone frais : avec les
        # modules rendus inimportables, EXIT=0. Un portail qui rend « succès »
        # après zéro test n'est pas un portail.
        print("\nAucun test ML exécutable dans cet environnement.")
        print("AUCUN TEST N'A ÉTÉ EXÉCUTÉ — ce n'est pas un succès.")
        return 3

    failed = False
    for plan in runnable:
        print(f"\n=== {plan.path} (processus isolé) ===", flush=True)
        resultat = run_module(plan.path)
        if resultat.returncode:
            failed = True
            print(failure_diagnostic(plan.path, resultat.returncode), flush=True)
        elif resultat.executes == 0:
            # La règle du cas zéro-module, appliquée au grain du MODULE.
            #
            # Mesuré : un module dont tous les tests font
            # `pytest.importorskip(...)` rend « 1 skipped » et EXIT=0. Le
            # lanceur concluait au succès, `main()` écrivait `ml-gate.json`, et
            # la CI lisait un passage de tests ML qui n'avait jamais eu lieu.
            failed = True
            print(vide_diagnostic(plan.path, resultat.sautes), flush=True)

    return 1 if failed else 0


# ---------- Le marqueur du dernier passage ----------
#
# La CI ne PEUT PAS exécuter ces tests : son profil léger n'a ni torch ni
# mlx-whisper. Elle peut en revanche dire « le code du pipeline a dérivé depuis
# le dernier passage ». C'est « rien ne disparaît en silence » appliqué à une
# porte qu'on ne sait pas automatiser.
#
# Le marqueur retient l'EMPREINTE DU CONTENU surveillé, pas un SHA de commit.
# Un SHA de commit de branche disparaît de l'historique au squash-merge : la
# porte se mettait alors à échouer sur main et sur toute branche qui en dérive,
# alors qu'AUCUN test ML n'avait régressé. Le contenu, lui, survit au squash —
# c'est donc LUI qu'on surveille. La porte ne dépend plus de la stratégie de
# merge ni de la profondeur du clone.
#
# Le marqueur est VERSIONNÉ — sinon la CI ne le verrait pas. Il ne contient ni
# chemin de média ni contenu de fichier : une date, la liste des modules, et une
# empreinte SHA-256 par fichier surveillé.

MARQUEUR = "ml-gate.json"

# Les chemins dont le CONTENU invalide un passage ML : le code du pipeline réel.
# Un commit de doc ou de tests UI n'invalide pas un passage ML.
CHEMINS_SURVEILLES = ("core/", "run_ml_tests.py")


def _git_env() -> dict[str, str]:
    """Environnement sans les variables ``GIT_*``.

    Un ``git`` lancé sous un hook (pre-push) ou depuis un worktree lié hérite
    d'un ``GIT_DIR`` absolu qui ÉCRASE ``cwd`` : l'appel viserait alors le mauvais
    dépôt (« fatal: this operation must be run in a work tree »). Les retirer rend
    l'appel HERMÉTIQUE — il obéit à ``cwd``, comme en CI où aucun ``GIT_*`` n'est
    posé. Sans quoi la porte ML lisait l'index de l'arbre du hook, pas celui visé.
    """
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def fichiers_surveilles(root: Path) -> list[str]:
    """Fichiers SUIVIS sous les chemins surveillés, triés.

    `git ls-files` lit l'INDEX, pas l'historique : indépendant de la profondeur
    du clone comme de la stratégie de merge — c'est précisément la robustesse
    qui manquait au comptage de commits.

    Un fichier marqué `export-ignore` (`.gitattributes`) n'est pas surveillé : il ne
    sert pas le pipeline, et l'empreinter ferait différer le marqueur d'un dépôt
    qui le porte à un dépôt qui ne le porte pas."""
    sortie = subprocess.run(
        ["git", "ls-files", "-z", "--", *CHEMINS_SURVEILLES],
        cwd=root, capture_output=True, text=True, check=True,
        env=_git_env()).stdout
    suivis = [p for p in sortie.split("\0") if p]
    if not suivis:
        return []
    attributs = subprocess.run(
        ["git", "check-attr", "-z", "--stdin", "export-ignore"],
        cwd=root, input="\0".join(suivis) + "\0", capture_output=True, text=True,
        check=True, env=_git_env()).stdout.split("\0")
    ignores = {c for c, _a, v in zip(attributs[0::3], attributs[1::3], attributs[2::3])
               if v == "set"}
    return sorted(p for p in suivis if p not in ignores)


def empreintes_surveillees(root: Path) -> dict[str, str]:
    """{chemin relatif → sha256 du contenu} pour chaque fichier surveillé.

    MESURE PARTAGÉE par l'écriture du marqueur (`ecrire_marqueur`) et son
    contrôle (`tests/unit/test_porte_ml.py`). Les faire diverger rendrait la
    porte incohérente avec elle-même : elles doivent lire le même code."""
    import hashlib

    return {
        rel: hashlib.sha256((root / rel).read_bytes()).hexdigest()
        for rel in fichiers_surveilles(root)
    }


def ecrire_marqueur(modules: list[str], *, root: Path | None = None) -> None:
    """Consigne le passage réussi. Best-effort : ne fait jamais échouer la porte.

    `root` est résolu à l'APPEL, pas à la définition. Un défaut `root=ROOT`
    figeait la valeur au chargement du module : un test qui remplace `ROOT`
    n'avait aucun effet, et le marqueur partait dans le vrai dépôt.

    Retient l'EMPREINTE du contenu surveillé, pas un SHA de commit : ce dernier
    disparaît de l'historique au squash-merge, l'empreinte de contenu non.
    """
    import json
    from datetime import datetime, timezone

    root = root if root is not None else ROOT

    try:
        watched = empreintes_surveillees(root)
    except Exception as exc:  # noqa: BLE001
        print(f"[porte ML] marqueur non écrit (git indisponible : {exc})")
        return

    (root / MARQUEUR).write_text(
        json.dumps(
            {
                "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "modules": sorted(modules),
                "watched": watched,
            },
            ensure_ascii=False, indent=2,
        ) + "\n",
        encoding="utf-8")
    print(f"\n[porte ML] passage consigné dans {MARQUEUR} "
          f"({len(watched)} fichiers du pipeline empreintés).")
    print("[porte ML] committez-le : sans lui, la CI signalera un retard.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="échoue avant MLX si le moindre prérequis d'un module manque",
    )
    args = parser.parse_args(argv)
    plans = runtime_preflight(strict=args.strict)
    code = execute(plans, strict=args.strict)

    # L'écriture vit ICI, pas dans `execute()`, et ce n'est pas un détail.
    #
    # Placée dans `execute()`, elle s'est déclenchée depuis un test existant
    # qui appelle cette fonction avec des modules fictifs — et a ÉCRASÉ le vrai
    # `ml-gate.json` du dépôt avec `["tests/ml/ready.py"]`. Un effet de bord
    # qui s'échappe dans le monde réel depuis une suite de tests.
    #
    # `execute()` reste pure : elle applique un plan et rend un code. Seul le
    # point d'entrée réel consigne quoi que ce soit.
    if code == 0:
        ecrire_marqueur([plan.path for plan in plans if plan.runnable])
    return code


if __name__ == "__main__":
    raise SystemExit(main())
