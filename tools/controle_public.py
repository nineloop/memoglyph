#!/usr/bin/env python3
"""Contrôle des termes interdits avant publication : texte, noms, messages, images.

Un seul contrôle, lu par le crochet de poussée et par la CI du dépôt public, qui cherche
ce que le dépôt public ne doit jamais porter : des termes de la recherche, des anciens noms,
des clés, des chemins personnels, des références à un historique privé.

Les termes interdits ne sont PAS écrits ici : le fichier d'empreintes ne contient que les
SHA-256 de leur forme normalisée (minuscules, accents retirés, séparateurs réduits à une
espace). Le contrôle calcule l'empreinte de chaque fenêtre glissante du texte contrôlé et la
compare : un terme est donc trouvé n'importe où (identifiant composé, nom de fichier), sans
qu'aucun mot interdit ne soit lisible dans ce dépôt. Limite assumée : ce n'est pas du
chiffrement. Un mot court se retrouve par essai ; l'objectif est qu'aucun terme ne figure en
clair, pas qu'il soit impossible à deviner.

Le contrôle lit les OBJETS git d'un commit (jamais l'arbre de travail) :

    python tools/controle_public.py arbre <commit>
    python tools/controle_public.py messages <ancienne>..<nouvelle>
    python tools/controle_public.py texte <fichier|->
    python tools/controle_public.py tout --commit <c> [--base <b>] [--texte <f>] [--nom <n>]

Codes de sortie : 0 rien trouvé ; 1 au moins une violation ; 2 usage ou lecture impossible
(jamais un succès par défaut). Bibliothèque standard seulement (Python 3.11).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

VERSION_NORMALISATION = 1
IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico"}
POLICES = {".woff", ".woff2", ".ttf", ".otf"}
STYLES = {".css", ".scss", ".svg"}
DEFAUT_EMPREINTES = Path(__file__).with_name("controle_public_empreintes.json")
DEFAUT_IMAGES = Path(__file__).with_name("images_approuvees.json")


@dataclass(frozen=True)
class Violation:
    type: str
    lieu: str
    detail: str

    def __str__(self) -> str:
        return f"{self.type:<9} {self.lieu}  {self.detail}"


class ErreurDeLecture(Exception):
    """Entrée illisible : le contrôle ne conclut pas (code de sortie 2)."""


# --- normalisation et empreintes -------------------------------------------------------------

def normaliser(texte: str) -> str:
    """Minuscules, accents retirés, tout ce qui n'est ni lettre ni chiffre réduit à une espace."""
    sans_accents = "".join(c for c in unicodedata.normalize("NFKD", texte) if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^0-9a-z]+", " ", sans_accents.lower()).split())


def _sha(texte: str) -> str:
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()


def fabriquer(termes: list[str], motifs: list[dict] | None = None, exceptions: list[dict] | None = None,
              motifs_messages: list[dict] | None = None) -> dict:
    """Construit le contenu du fichier d'empreintes depuis des termes LISIBLES.

    Un terme préfixé par `=` est un mot entier. Sert au générateur privé et aux tests ; le
    résultat ne contient aucun terme lisible.
    """
    vus: dict[tuple[str, bool], dict] = {}
    debuts: set[str] = set()
    for brut in termes:
        entier = brut.startswith("=")
        n = normaliser(brut[1:] if entier else brut)
        if not n:
            raise ValueError("terme vide après normalisation")
        vus[(_sha(n), entier, False)] = {"sha256": _sha(n), "longueur": len(n), "mot_entier": entier,
                                         "compact": False, "terme": _sha(n)}
        debuts.add(n[0])
        if " " in n and not entier:
            # Un terme à plusieurs mots se retrouve aussi collé (`MonTermeComposé`, `moncomposé`).
            compact = n.replace(" ", "")
            vus[(_sha(compact), False, True)] = {"sha256": _sha(compact), "longueur": len(compact),
                                                 "mot_entier": False, "compact": True, "terme": _sha(n)}
    liste = sorted(vus.values(), key=lambda e: (e["longueur"], e["sha256"], e["mot_entier"], e["compact"]))
    return {
        "normalisation": VERSION_NORMALISATION,
        "empreintes": liste,
        "debuts": "".join(sorted(debuts)),
        "motifs": motifs or [],
        "motifs_messages": motifs_messages or [],
        "exceptions": [
            {"empreinte": _sha(normaliser(e["terme"].lstrip("="))), "chemin": e["chemin"],
             "occurrences": e["occurrences"], "raison": e["raison"]}
            for e in (exceptions or [])
        ],
    }


class _Regles:
    """La configuration prête à servir."""

    def __init__(self, config: dict) -> None:
        if config.get("normalisation") != VERSION_NORMALISATION:
            raise ErreurDeLecture("version de normalisation inconnue dans le fichier d'empreintes")
        # fenêtre -> (mot entier, identifiant du terme), par longueur ; formes normale et compacte
        self.par_longueur: dict[int, dict[str, tuple[bool, str]]] = {}
        self.compact: dict[int, dict[str, tuple[bool, str]]] = {}
        for e in config.get("empreintes", []):
            groupe = self.compact if e.get("compact") else self.par_longueur
            groupe.setdefault(int(e["longueur"]), {})[e["sha256"]] = (bool(e["mot_entier"]), e.get("terme", e["sha256"]))
        self.debuts = set(config.get("debuts", ""))
        self.motifs = [(m["nom"], re.compile(m["expression"]), set(m.get("ignorer_suffixes", [])))
                       for m in config.get("motifs", [])]
        self.motifs_messages = [(m["nom"], re.compile(m["expression"])) for m in config.get("motifs_messages", [])]
        self.exceptions = {(e["empreinte"], e["chemin"]): int(e["occurrences"]) for e in config.get("exceptions", [])}


def _fenetres(n: str, regles: _Regles):
    """(identifiant du terme, position, longueur) des termes trouvés dans le texte normalisé `n`.

    Deux passes : le texte normalisé (mots séparés par une espace) et sa forme compacte (sans
    espaces) pour les termes à plusieurs mots. Un terme n'est rapporté qu'une fois par ligne.
    """
    deja: set[str] = set()
    for texte, groupes in ((n, regles.par_longueur), (n.replace(" ", ""), regles.compact)):
        for longueur, groupe in groupes.items():
            for i in range(len(texte) - longueur + 1):
                if texte[i] not in regles.debuts:
                    continue
                trouve = groupe.get(_sha(texte[i:i + longueur]))
                if trouve is None:
                    continue
                entier, terme = trouve
                if entier and not ((i == 0 or texte[i - 1] == " ") and
                                   (i + longueur == len(texte) or texte[i + longueur] == " ")):
                    continue
                if terme in deja:
                    continue
                deja.add(terme)
                yield terme, i, longueur


def _controler(texte: str, lieu: str, regles: _Regles, *, chemin: str | None = None, suffixe: str = "",
               messages: bool = False) -> list[Violation]:
    restantes = {cle: nb for cle, nb in regles.exceptions.items() if cle[1] == (chemin if chemin is not None else lieu)}
    violations: list[Violation] = []
    for numero, ligne in enumerate(texte.splitlines() or [texte], 1):
        place = f"{lieu}:{numero}"
        for h, i, longueur in _fenetres(normaliser(ligne), regles):
            if restantes.get((h, chemin if chemin is not None else lieu), 0) > 0:
                restantes[(h, chemin if chemin is not None else lieu)] -= 1
                continue
            violations.append(Violation("terme", place, f"terme interdit (empreinte, {longueur} caractères)"))
        for nom, motif, ignores in regles.motifs:
            if suffixe in ignores:
                continue
            if motif.search(ligne):
                violations.append(Violation(nom, place, f"motif « {nom} »"))
        if messages:
            for nom, motif in regles.motifs_messages:
                if motif.search(ligne):
                    violations.append(Violation(nom, place, f"motif « {nom} »"))
    return violations


# --- API utilisée par les tests, le crochet, la CI -------------------------------------------

def controler_texte(texte: str, lieu: str, config: dict, *, messages: bool = False) -> list[Violation]:
    return _controler(texte, lieu, _Regles(config), chemin=lieu, suffixe=PurePosixPath(lieu).suffix.lower(),
                      messages=messages)


def controler_nom(nom: str, config: dict) -> list[Violation]:
    """Un nom de fichier, de branche ou d'étiquette : un terme dedans est une violation."""
    regles = _Regles(config)
    resultat = []
    for h, i, longueur in _fenetres(normaliser(nom), regles):
        resultat.append(Violation("terme", nom, f"terme interdit dans le nom (empreinte, {longueur} caractères)"))
    return resultat


def _git(depot: Path, *args: str, entree: bytes | None = None) -> bytes:
    try:
        return subprocess.run(["git", *args], cwd=depot, input=entree, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        detail = e.stderr.decode("utf-8", "replace").strip() if isinstance(e, subprocess.CalledProcessError) else str(e)
        raise ErreurDeLecture(f"git {' '.join(args)} : {detail}") from e


def _blobs(depot: Path, oids: list[str]) -> dict[str, bytes]:
    """Lit des objets par `git cat-file --batch` (un seul processus)."""
    sortie = _git(depot, "cat-file", "--batch", entree=("\n".join(oids) + "\n").encode())
    contenus: dict[str, bytes] = {}
    position = 0
    while position < len(sortie):
        fin = sortie.index(b"\n", position)
        entete = sortie[position:fin].decode().split()
        if len(entete) != 3:
            raise ErreurDeLecture(f"objet introuvable : {' '.join(entete)}")
        taille = int(entete[2])
        contenus[entete[0]] = sortie[fin + 1:fin + 1 + taille]
        position = fin + 1 + taille + 1
    return contenus


def controler_arbre(depot: Path, commit: str, config: dict, images: dict[str, str]) -> list[Violation]:
    """Chemins et contenu de chaque fichier du COMMIT (objets git, jamais l'arbre de travail)."""
    regles = _Regles(config)
    liste = _git(depot, "ls-tree", "-r", "-z", "--full-tree", commit)
    fichiers: list[tuple[str, str, str]] = []
    for ligne in liste.split(b"\0"):
        if not ligne:
            continue
        meta, chemin = ligne.split(b"\t", 1)
        mode, type_, oid = meta.decode().split(" ")
        if type_ == "blob":
            fichiers.append((mode, oid, chemin.decode("utf-8", "replace")))
    contenus = _blobs(depot, sorted({oid for _, oid, _ in fichiers}))
    violations: list[Violation] = []
    for _mode, oid, chemin in fichiers:
        violations += [Violation(v.type, chemin, v.detail) for v in controler_nom(chemin, config)]
        suffixe = PurePosixPath(chemin).suffix.lower()
        donnees = contenus[oid]
        if suffixe in IMAGES:
            if hashlib.sha256(donnees).hexdigest() not in images:
                violations.append(Violation("image", chemin, "image non approuvée (empreinte absente du manifeste)"))
            continue
        if suffixe in POLICES:
            continue
        if b"\0" in donnees[:8000]:
            violations.append(Violation("binaire", chemin, "binaire non prévu (ni image raster, ni police)"))
            continue
        violations += _controler(donnees.decode("utf-8", "replace"), chemin, regles, chemin=chemin, suffixe=suffixe)
    return violations


def controler_messages(depot: Path, intervalle: str, config: dict, *, limite: int | None = None) -> list[Violation]:
    """Messages des commits de l'intervalle (`a..b`), ou du seul commit donné avec `limite=1`."""
    regles = _Regles(config)
    options = [f"--max-count={limite}"] if limite else []
    sortie = _git(depot, "log", *options, "--format=%H%x1f%B%x1e", intervalle).decode("utf-8", "replace")
    violations = []
    for enregistrement in sortie.split("\x1e"):
        if not enregistrement.strip():
            continue
        oid, _, message = enregistrement.strip("\n").partition("\x1f")
        violations += _controler(message, f"commit {oid.strip()[:10]}", regles, chemin=None, messages=True)
    return violations


# --- ligne de commande -----------------------------------------------------------------------

def _charger_json(chemin: Path, quoi: str) -> dict:
    try:
        return json.loads(Path(chemin).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ErreurDeLecture(f"{quoi} illisible ({chemin}) : {e}") from e


def _images(chemin: Path) -> dict[str, str]:
    return {e["sha256"]: e["chemin"] for e in _charger_json(chemin, "manifeste des images").get("images", [])}


def _lire_texte(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    try:
        return Path(source).read_text(encoding="utf-8")
    except OSError as e:
        raise ErreurDeLecture(f"texte illisible ({source}) : {e}") from e


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Contrôle des termes interdits avant publication.")
    sub = p.add_subparsers(dest="cmd", required=True)
    communs = argparse.ArgumentParser(add_help=False)
    communs.add_argument("--depot", default=".", type=Path)
    communs.add_argument("--empreintes", default=DEFAUT_EMPREINTES, type=Path)
    communs.add_argument("--images", default=DEFAUT_IMAGES, type=Path)
    sub.add_parser("arbre", parents=[communs]).add_argument("commit")
    sub.add_parser("messages", parents=[communs]).add_argument("intervalle")
    sub.add_parser("texte", parents=[communs]).add_argument("fichier")
    o_ = sub.add_parser("tout", parents=[communs])
    o_.add_argument("--commit", required=True)
    o_.add_argument("--base")
    o_.add_argument("--texte", action="append", default=[])
    o_.add_argument("--nom", action="append", default=[])
    a = p.parse_args(argv)
    try:
        config = _charger_json(a.empreintes, "fichier d'empreintes")
        _Regles(config)
        violations: list[Violation] = []
        if a.cmd == "arbre":
            violations = controler_arbre(a.depot, a.commit, config, _images(a.images))
        elif a.cmd == "messages":
            violations = controler_messages(a.depot, a.intervalle, config)
        elif a.cmd == "texte":
            violations = controler_texte(_lire_texte(a.fichier), a.fichier, config, messages=True)
        else:
            violations = controler_arbre(a.depot, a.commit, config, _images(a.images))
            if a.base and a.base != "0" * 40:
                violations += controler_messages(a.depot, f"{a.base}..{a.commit}", config)
            else:       # nouvelle branche ou base inconnue : le message du commit de tête
                violations += controler_messages(a.depot, a.commit, config, limite=1)
            for source in a.texte:
                violations += controler_texte(_lire_texte(source), source, config, messages=True)
            for nom in a.nom:
                violations += controler_nom(nom, config)
    except ErreurDeLecture as e:
        print(f"❌ contrôle impossible : {e}", file=sys.stderr)
        return 2
    for v in sorted(violations, key=lambda v: (v.lieu, v.type)):
        print(v)
    if violations:
        print(f"❌ {len(violations)} violation(s) : rien ne part.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
