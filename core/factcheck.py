"""Vérification déterministe des nombres d'un texte généré contre sa source.

## Ce que ça attrape — et ce que ça n'attrape pas

Patron HERMAN (Zhao, Cohen & Webber, Findings EMNLP 2020) : tout nombre affirmé
par un résumé doit exister dans la source. Constaté sur un compte-rendu réel : le
modèle a écrit « Mondial **2028** » alors que cette année n'est prononcée nulle
part dans le transcript. Détecté.

⚠️ **La limite est structurelle, pas un réglage.** Cette vérification ne voit que
les nombres **fabriqués** (hallucination extrinsèque). Elle est aveugle aux
nombres **mal attribués** (hallucination intrinsèque) : le même compte-rendu
affirmait « la France a marqué plus de buts (5-3) » alors que 5-3 était l'avance
de l'adversaire — et « 5-3 » figure bel et bien dans le transcript, donc rien
n'est signalé. Détecter cela demande de vérifier le couple (nombre, entité), ce
qui sort du domaine d'un test d'appartenance.

Ne pas présenter ce module comme un garde-fou de fidélité : c'est un filet sur un
seul mode d'échec, choisi parce qu'il est **déterministe**. Les vérificateurs
probabilistes restent peu précis sur les cas difficiles ; celui-ci, sur ce qu'il
couvre, ne se trompe pas.

## Principe respecté

Rien n'est réécrit ni supprimé : on **signale**. Une inférence LLM n'est jamais
une donnée d'autorité, mais elle n'est pas non plus effacée en silence.
"""

from __future__ import annotations

import re
from typing import Iterable

# Nombres écrits en toutes lettres : le transcript dit « un but à zéro » là où le
# résumé écrit « 1-0 ». Sans cette normalisation, on signalerait du bruit.
#
# ⚠️ Une table PLATE mot → chiffre ne suffit pas, et l'ancienne version violait
# l'invariant que ce module revendique (« jamais inventer une alerte »). Mesuré :
# 7 cas sur 7 en faux positif :
#
#   source « soixante-quinze personnes »  + CR « 75 »      → signalait 75
#   source « quatre-vingt-dix candidats » + CR « 90 »      → signalait 90
#   source « vingt-cinq mille tickets »   + CR « 25 000 »  → signalait 25 ET 000
#   source « fifteen people »             + CR « 15 »      → signalait 15
#
# Deux causes : aucune composition (« soixante » et « quinze » restaient
# séparés, jamais 75) et AUCUN mot anglais, alors que la moitié du corpus l'est.
# Un garde-fou qui crie au loup sur un chiffre correct détruit la confiance qui
# le justifie — c'est plus grave qu'une alerte manquée.

_UNITES = {
    "zéro": 0, "zero": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4,
    "cinq": 5, "six": 6, "sept": 7, "huit": 8, "neuf": 9, "dix": 10, "onze": 11,
    "douze": 12, "treize": 13, "quatorze": 14, "quinze": 15, "seize": 16,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_DIZAINES = {
    "vingt": 20, "vingts": 20, "trente": 30, "quarante": 40, "cinquante": 50,
    "soixante": 60,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
# Multiplicateurs : « cent » multiplie ce qui précède, « mille » clôt un groupe.
_CENT = {"cent": 100, "cents": 100, "hundred": 100}
_ECHELLES = {
    "mille": 1_000, "milles": 1_000, "thousand": 1_000,
    "million": 1_000_000, "millions": 1_000_000,
    "milliard": 1_000_000_000, "milliards": 1_000_000_000,
    "billion": 1_000_000_000,
}
_MOT_NOMBRE = set(_UNITES) | set(_DIZAINES) | set(_CENT) | set(_ECHELLES) | {"et", "and", "-"}


def _plier(mots: list[str]) -> int | None:
    """Valeur d'une suite de mots-nombres. None si la suite n'en est pas une.

    Algorithme classique à deux accumulateurs : `courant` pour le groupe en
    cours, `total` pour ce qu'une échelle a déjà clos. « quatre-vingt » est un
    cas français à part — c'est 4 × 20, pas 4 + 20.
    """
    total = courant = 0
    vu = False
    i = 0
    while i < len(mots):
        m = mots[i]
        if m in ("et", "and", "-"):
            i += 1
            continue
        if m in _DIZAINES:
            # « quatre-vingt(s) » : le 4 déjà accumulé devient un multiplicateur.
            if m.startswith("vingt") and courant == 4:
                courant = 80
            else:
                courant += _DIZAINES[m]
            vu = True
        elif m in _UNITES:
            courant += _UNITES[m]
            vu = True
        elif m in _CENT:
            courant = (courant or 1) * 100
            vu = True
        elif m in _ECHELLES:
            total += (courant or 1) * _ECHELLES[m]
            courant = 0
            vu = True
        else:
            return None
        i += 1
    return total + courant if vu else None


_SEPARATEUR_MILLIERS = re.compile(r"(?<=\d)[   ](?=\d{3}\b)")


def _annee_par_paires(mots: list[str]) -> int | None:
    """« twenty twenty » → 2020, « nineteen ninety-five » → 1995.

    Une année se lit souvent en DEUX paires, que l'addition plie à tort (20+20=40).
    Les deux lectures sont légitimes hors contexte, donc on ne choisit pas : cette
    variante n'est ajoutée qu'à l'ensemble SOURCE, jamais aux nombres du résumé.
    C'est le biais que ce module assume déjà — une source plus permissive peut
    masquer une alerte, jamais en inventer une.
    """
    coupe = next((i for i, m in enumerate(mots) if i and m in _DIZAINES
                  and _plier(mots[:i]) is not None), None)
    if coupe is None:
        return None
    g1, g2 = _plier(mots[:coupe]), _plier(mots[coupe:])
    if g1 is None or g2 is None or not (10 <= g1 <= 99) or not (0 <= g2 <= 99):
        return None
    return g1 * 100 + g2


def _chiffrer_les_lettres(text: str, *, permissif: bool = False) -> str:
    """Remplace chaque suite de mots-nombres par sa valeur chiffrée.

    `permissif` : ajoute AUSSI les lectures alternatives (années par paires).
    Réservé à la source — voir `_annee_par_paires`.
    """
    jetons = re.split(r"([^\wà-ÿ]+)", text, flags=re.IGNORECASE)
    sortie: list[str] = []
    tampon: list[str] = []

    def vider() -> None:
        if not tampon:
            return
        # On ne plie que les mots, pas la ponctuation qui les sépare.
        mots = [t.lower() for t in tampon if t.strip() and t.strip("- ")]
        propres = [m for m in mots if m]
        val = _plier(propres)
        if val is None:
            sortie.append("".join(tampon))
        else:
            rendu = str(val)
            if permissif:
                annee = _annee_par_paires(propres)
                if annee is not None and annee != val:
                    rendu += f" {annee}"
            sortie.append(rendu)
        tampon.clear()

    for j in jetons:
        nu = j.strip().lower()
        if nu in _MOT_NOMBRE or (not nu and tampon) or (nu == "" and not tampon):
            if nu in _MOT_NOMBRE or (tampon and not nu):
                tampon.append(j)
                continue
        vider()
        sortie.append(j)
    vider()
    return "".join(sortie)

# 12, 4-0, 4–0 (tiret demi-cadratin inclus : le modèle en produit)
_NUM = re.compile(r"\d+(?:\s*[-–]\s*\d+)?")
_WORD = re.compile(r"[a-zà-ÿ]+", re.IGNORECASE)

# Bruit structurel, à retirer AVANT extraction — sinon « Speaker 7 » et les puces
# numérotées d'une liste ressortent comme des affirmations chiffrées.
_SPEAKER = re.compile(r"\bspeakers?\s*\d+", re.IGNORECASE)
_LIST_MARKER = re.compile(r"^\s*\d+[.)]\s", re.MULTILINE)
_HEADING = re.compile(r"^#.*$", re.MULTILINE)

# Horodatages : « [00:15:46] », « at 00:15:46 », « 15:46 ».
#
# Un horodatage est une RÉFÉRENCE, pas une donnée chiffrée. Le décomposer en
# « 00 », « 15 », « 46 » revenait à punir le modèle pour avoir cité sa source —
# exactement ce qu'on lui demande de faire. Et le verdict était arbitraire : le
# texte source ne contient que la parole, jamais les horodatages, donc un
# composant n'échappait à l'alerte que s'il apparaissait par hasard ailleurs.
# Constaté sur un vrai CR : « 15 » passait, « 00 » et « 46 » étaient signalés,
# pour une citation pourtant exacte.
_TIMESTAMP = re.compile(r"\[?\b\d{1,2}:\d{2}(?::\d{2})?\b\]?")


# Scores énoncés à l'oral : « un but à zéro », « 4 à 2 », « six partout ». Sans
# cette étape, la source ne fournit que les chiffres SÉPARÉS et un résumé qui
# écrit correctement « 1-0 » serait signalé à tort.
_SCORE_A = re.compile(r"\b(\d+)\s*(?:buts?\s*)?à\s*(\d+)\b", re.IGNORECASE)
_SCORE_PARTOUT = re.compile(r"\b(\d+)\s*(?:buts?\s*)?partout\b", re.IGNORECASE)


def _normalize(text: str, *, permissif: bool = False) -> str:
    """Chiffres en toutes lettres → chiffres, puis scores oraux → forme « N-M ».

    Limite acceptée : « de 3 à 4 minutes » produit aussi « 3-4 ». Le biais va dans
    le bon sens — il rend la SOURCE plus permissive, donc il peut masquer une
    alerte, jamais en inventer une.
    """
    text = _SEPARATEUR_MILLIERS.sub("", text)   # « 25 000 » → « 25000 »
    text = _chiffrer_les_lettres(text, permissif=permissif)
    text = _SCORE_A.sub(r"\1-\2 ", text)
    text = _SCORE_PARTOUT.sub(r"\1-\1 ", text)
    return text


def _strip_noise(text: str) -> str:
    """Retire ce qui contient des chiffres sans rien affirmer."""
    text = _HEADING.sub("", text)      # le titre du gabarit reprend le nom du fichier
    text = _SPEAKER.sub("", text)      # « Speaker 7 » n'est pas une donnée chiffrée
    text = _LIST_MARKER.sub("", text)  # « 2. » d'une liste numérotée
    text = _TIMESTAMP.sub(" ", text)   # « [00:15:46] » cite la source, n'affirme rien
    return text


def numbers(text: str, *, strip_noise: bool = True, permissif: bool = False) -> set[str]:
    """Nombres affirmés par un texte, normalisés (« 4 - 0 » et « quatre à zéro » → « 4-0 »)."""
    if strip_noise:
        text = _strip_noise(text)
    found = _NUM.findall(_normalize(text, permissif=permissif))
    return {re.sub(r"\s*", "", n) for n in found}


def _source_set(texts: Iterable[str]) -> set[str]:
    """Nombres de la source, composites **décomposés**.

    Une source qui dit « 5-3 » soutient aussi bien « 5 » que « 3 » : sans cette
    décomposition, un résumé correct serait signalé à tort.
    """
    out: set[str] = set()
    for n in numbers(" ".join(texts), strip_noise=False, permissif=True):
        out.add(n)
        if "-" in n or "–" in n:
            out.update(re.split(r"[-–]", n))
    return out


def unsupported_numbers(summary: str, source_texts: Iterable[str]) -> list[str]:
    """Nombres du résumé introuvables dans la source, triés (déterministe)."""
    source = _source_set(source_texts)
    return sorted(numbers(summary) - source, key=lambda n: (len(n), n))


def nombres_du_passage(texts: Iterable[str]) -> set[str]:
    """Nombres qu'un passage de parole soutient, pour le garde des unités de CR.

    Ceux de `_source_set`, plus chaque nombre en lettres pris SEUL : « douze
    cinquante » se lit 62 ou 1250 en bloc, mais soutient aussi « 12,50 », que
    l'énoncé découpe en 12 et 50.
    """
    source = _source_set(texts)
    for texte in texts:
        for mot in _WORD.findall(texte):
            source |= numbers(mot, strip_noise=False, permissif=True)
    return source


def chiffres_non_ancres(enonce: str, passages: Iterable[str]) -> list[str]:
    """Nombres de l'énoncé qu'aucun passage cité ne porte, triés (déterministe)."""
    soutien = nombres_du_passage(list(passages))
    return sorted(numbers(enonce) - soutien, key=lambda n: (len(n), n))
