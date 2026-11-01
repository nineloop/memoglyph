"""Aucun fichier de données personnelles ne doit être suivi par git.

Ces tests regardent ce que git suit RÉELLEMENT, et pas ce qu'une règle prétend
exclure : une liste de noms dans `.gitignore` n'attrape que ce qu'elle a déjà vu.
"""

from __future__ import annotations

import subprocess

from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]


def _fichiers_suivis(sous_dossier: str, racine: Path = RACINE) -> list[str]:
    sortie = subprocess.run(
        ["git", "ls-files", sous_dossier],
        cwd=racine, capture_output=True, text=True, check=True,
    )
    return [ligne for ligne in sortie.stdout.splitlines() if ligne.strip()]


# `inputs/` et `output/` : RIEN. Enregistrements sources et transcripts.
DOSSIERS_INTERDITS = ("inputs", "output")


def test_aucun_fichier_de_output_ni_d_inputs_n_est_suivi():
    """`output/` porte les transcripts, `inputs/` les enregistrements SOURCES.

    Règle permanente : ni l'un ni l'autre n'est commité. Deux
    transcripts d'`output/` ont pourtant vécu huit jours dans l'historique
    sans que personne ne les voie. Ce test regarde donc les deux dossiers.
    """
    for dossier in DOSSIERS_INTERDITS:
        assert _fichiers_suivis(dossier) == [], f"{dossier}/ ne doit rien suivre"


def test_aucun_fichier_d_environnement_n_est_suivi():
    """Règle permanente : `output/` ET `.env` ne sont JAMAIS commités.

    La moitié `.env` n'était gardée par aucun test, et `.gitignore` ne couvrait
    que le nom EXACT `.env` — `.env.local`, `.env.production` et `.env.bak`
    étaient suivables.

    Le contrôle interroge git plutôt qu'une liste de noms : une liste de noms
    ne protège que du cas déjà vu.
    """
    sortie = subprocess.run(
        ["git", "ls-files"], cwd=RACINE, capture_output=True, text=True, check=True)
    intrus = [
        ligne for ligne in sortie.stdout.splitlines()
        if Path(ligne).name.startswith(".env") and Path(ligne).name != ".env.example"
    ]
    assert intrus == [], f"fichier(s) d'environnement suivi(s) par git : {intrus}"


def test_le_gitignore_refuse_les_variantes_de_env():
    """Le test précédent constate ; celui-ci empêche. Sans lui, un `.env.local`
    créé demain serait proposé par `git status` et un `git add -A` le prendrait.
    """
    for nom in (".env", ".env.local", ".env.production", ".env.bak", ".env.2026"):
        verdict = subprocess.run(
            ["git", "check-ignore", "-q", nom], cwd=RACINE, capture_output=True)
        assert verdict.returncode == 0, f"{nom} n'est PAS ignoré"
    # …mais l'exemple, lui, doit rester versionné.
    verdict = subprocess.run(
        ["git", "check-ignore", "-q", ".env.example"], cwd=RACINE, capture_output=True)
    assert verdict.returncode != 0, ".env.example doit rester versionné"


def test_le_balayage_de_ce_fichier_VOIT_bien_quelque_chose():
    """ANCRE DE NON-VACUITÉ — motif nº2, et signalée par le test-méta.

    Les assertions de ce fichier ont la forme `assert x == []` sur un balayage
    `git ls-files`. Si `git` cessait de répondre, si le dépôt était vide, ou si
    les chemins changeaient, elles resteraient VERTES en ne regardant plus rien —
    le défaut même que ce fichier existe pour empêcher, appliqué à lui-même.

    L'ancre porte sur le code, relevé par le même helper.
    """
    assert len(_fichiers_suivis("core")) >= 20, (
        "git ne rend plus le code : `git ls-files` a cessé de fonctionner")
