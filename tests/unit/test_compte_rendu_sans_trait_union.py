"""« compte rendu » s'écrit sans trait d'union dans tout ce que le serveur affiche (écrit au nouveau format, jamais relu en ancien).

Le catalogue de l'interface est gardé par `ui/src/test/regleEcriture.test.ts`. Ce test garde le côté serveur : aucun TEXTE
(littéral de chaîne, docstrings exclues) de `core/` et `api/` ne contient « compte-rendu » ou « Compte-rendu », sauf les
identifiants et les formes lues pour reconnaître les anciens documents, nommés un à un avec leur raison.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from core import messages

pytestmark = pytest.mark.unit

RACINE = Path(__file__).resolve().parents[2]
FORME = re.compile(r"compte-rendu", re.IGNORECASE)

# (fichier, texte exact) -> raison. Un identifiant ou un contrat de données ne se change pas dans un lot de textes.
PERMIS = {
    ("core/semantic.py", "compte-rendu"): "valeur de `origine` lue par l'interface : contrat de l'API",
    ("core/library.py", " — compte-rendu"): "ancien suffixe de titre, lu pour reconnaître les documents déjà écrits",
    ("core/library.py", "Compte-rendu"): "ancien nom de H1, reconnu pour être lu au nouveau format",
    ("core/library.py", r"^# (Compte rendu|Compte-rendu|Report) — (.+)$"): "anciens et nouveaux H1, lus à la lecture",
    ("core/rag.py", "compte-rendu"): "valeur de `origine` comparée (contrat de l'API)",
    ("core/rag.py", "compte-rendu généré"): "étiquette donnée au modèle dans l'invite de l'Assistant, pas un texte affiché (laissée telle quelle volontairement)",
}
# Instructions données au modèle (pas affichées) : les changer modifie ce que le modèle reçoit, donc ses réponses ; hors d'un lot de textes.
PERMIS_DEBUT = {
    ("core/report.py", "IMPÉRATIF — rédige TOUT le compte-rendu en FRANÇAIS"): "consigne de langue de l'invite du modèle",
}


def _textes(chemin: Path):
    """(ligne, texte, est_une_nature) : `est_une_nature` marque la valeur d'un argument `nature=` (clé du relevé, contrat de données)."""
    arbre = ast.parse(chemin.read_text(encoding="utf-8"))
    docstrings, natures = set(), set()
    for noeud in ast.walk(arbre):
        if isinstance(noeud, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and noeud.body:
            premier = noeud.body[0]
            if isinstance(premier, ast.Expr) and isinstance(premier.value, ast.Constant) and isinstance(premier.value.value, str):
                docstrings.add(id(premier.value))
        if isinstance(noeud, ast.keyword) and noeud.arg == "nature" and isinstance(noeud.value, ast.Constant):
            natures.add(id(noeud.value))
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Constant) and isinstance(noeud.value, str) and id(noeud) not in docstrings:
            yield noeud.lineno, noeud.value, id(noeud) in natures


def test_aucun_texte_du_serveur_n_ecrit_compte_rendu_avec_un_trait_d_union() -> None:
    restes, vus, au_nouveau_format = [], [], []
    for dossier in ("core", "api"):
        for chemin in sorted((RACINE / dossier).rglob("*.py")):
            relatif = chemin.relative_to(RACINE).as_posix()
            for ligne, texte, nature in _textes(chemin):
                vus.append(texte)
                if "compte rendu" in texte.lower():
                    au_nouveau_format.append(texte)
                if not FORME.search(texte) or nature:
                    continue
                if (relatif, texte) in PERMIS or any(
                        relatif == f and texte.startswith(debut) for f, debut in PERMIS_DEBUT):
                    continue
                restes.append(f"{relatif}:{ligne}: {texte[:70]!r}")
    # Ancres : le balayage voit des milliers de textes, dont ceux déjà écrits au nouveau format.
    assert len(vus) > 1000, "le balayage ne voit plus les textes du serveur"
    assert len(au_nouveau_format) >= 5, "plus aucun texte au nouveau format : le balayage ne regarde plus au bon endroit"
    assert restes == [], "ancienne forme dans un texte affiché :\n" + "\n".join(restes)


def test_chaque_exception_nommee_existe_encore() -> None:
    """Une exception sans objet se retire : sinon la liste cache des cas que plus personne ne regarde."""
    trouves = {(fichier, contenu) for fichier in {f for f, _ in PERMIS}
               for _ligne, contenu, _nature in _textes(RACINE / fichier)}
    assert set(PERMIS) - trouves == set(), set(PERMIS) - trouves


def test_le_refus_d_attente_nomme_un_compte_rendu_sans_trait_d_union() -> None:
    """`resources.heavy(label=...)` entre dans le texte « un autre {label} est en cours » : le libellé affiché ne porte pas l'ancienne forme."""
    source = (RACINE / "api" / "main.py").read_text(encoding="utf-8")
    assert 'label="compte rendu"' in source
    assert 'label="compte-rendu"' not in source
    assert "un autre compte rendu est en cours" in messages.trop_d_attente("compte rendu")
