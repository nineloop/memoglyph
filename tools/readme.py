"""Générateur des README : `readme/readme.yaml` + une rubrique Markdown par langue.

    python tools/readme.py generer     # écrit README.md (anglais) et README.fr.md (français)
    python tools/readme.py verifier    # compare une régénération aux fichiers présents, n'écrit rien

Codes de sortie : 0 réussite ; 1 écart (`verifier`) ; 2 mauvais usage ; 3 gabarit ou rubrique invalide.
Aucune commande ne supprime de fichier, ne touche au réseau, ni n'écrit hors des deux README.

Les contrôles d'écriture (ton, coupe, longueur, attribution) vivent ici aussi : les tests les
rejouent sur de petits textes pour prouver qu'ils échouent, puis sur les vrais README.
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

import yaml

RACINE = Path(__file__).resolve().parents[1]
GABARIT = "readme/readme.yaml"
SORTIES = {"en": "README.md", "fr": "README.fr.md"}
ENTETES = {
    "en": "<!-- Generated file: edit readme/, then run make readme -->",
    "fr": "<!-- Fichier généré : modifier readme/, puis make readme -->",
}
LIGNES_MIN, LIGNES_MAX = 108, 132
ECART_MAX_MOTS = 0.10
PHRASE_MAX_MOTS = 35

ADRESSES = {
    "fr": r"tu|toi|ton|ta|tes|vous|votre|vos|on|nous|notre|nos",
    "en": r"you|your|yours|we|our|ours",
}
IMPERATIFS = {
    "fr": {"ouvrez", "lancez", "installez", "cliquez", "choisissez", "utilisez", "consultez", "voir", "exécutez",
           "lisez", "copiez", "créez", "ajoutez", "configurez", "démarrez", "téléchargez", "vérifiez", "suivez",
           "essayez", "regardez", "commencez", "tapez", "collez", "saisissez", "découvrez", "développez", "apprenez",
           "explorez", "trouvez"},
    "en": {"open", "run", "install", "click", "choose", "use", "see", "read", "copy", "create", "add",
           "configure", "start", "download", "check", "follow", "try", "launch", "look", "begin", "type", "paste",
           "learn", "develop", "discover", "explore", "get", "find"},
}
SUPERLATIFS = {
    "fr": r"meilleur|meilleure|meilleurs|meilleures|révolutionnaire|révolutionnaires|ultime|ultimes|incroyable|"
          r"incroyables|extraordinaire|extraordinaires|magique|magiques|parfait|parfaite|parfaits|parfaites",
    "en": r"best|revolutionary|ultimate|amazing|incredible|extraordinary|magical|perfect|flawless",
}
ATTRIBUTION = ("AMI Meeting Corpus", "CC BY 4.0", "Carletta")
DOSSIER_CAPTURES = "docs/assets/screenshots/"
IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico"}   # les mêmes que `tools/controle_public.py`


class ErreurGabarit(Exception):
    """Le gabarit ou une rubrique est invalide (code de sortie 3). `erreurs` liste tout, pas le premier seulement."""

    def __init__(self, erreurs: list[str]):
        super().__init__("\n".join(erreurs))
        self.erreurs = erreurs


@dataclass
class Gabarit:
    version: str
    regles_strictes: bool
    langues: list[str]
    rubriques: list[dict]
    captures: dict = field(default_factory=dict)
    liens: dict = field(default_factory=dict)
    attribution: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- chargement

def charger(racine: Path = RACINE) -> Gabarit:
    """Lit le gabarit et vérifie la cohérence avec les rubriques ; lève `ErreurGabarit` avec TOUTES les erreurs."""
    erreurs: list[str] = []
    chemin = racine / GABARIT
    try:
        brut = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        raise ErreurGabarit([f"{GABARIT} illisible : {e}"]) from e
    langues = brut.get("langues")
    if sorted(langues or []) != sorted(SORTIES):
        erreurs.append(f"{GABARIT} : `langues` doit être {sorted(SORTIES)} (reçu {langues!r})")
        langues = sorted(SORTIES)
    rubriques = brut.get("rubriques") or []
    if not rubriques:
        erreurs.append(f"{GABARIT} : aucune rubrique")
    ids: set[str] = set()
    for r in rubriques:
        rid = r.get("id")
        if not rid or rid in ids:
            erreurs.append(f"{GABARIT} : identifiant de rubrique manquant ou en double ({rid!r})")
        ids.add(rid)
        for lg in langues:
            nom = (r.get("fichier") or {}).get(lg)
            if not nom:
                erreurs.append(f"rubrique « {rid} » : pas de fichier pour la langue {lg}")
            elif not (racine / "readme" / lg / nom).is_file():
                erreurs.append(f"rubrique « {rid} » : readme/{lg}/{nom} manquant")
            elif not (racine / "readme" / lg / nom).read_text(encoding="utf-8").strip():
                erreurs.append(f"rubrique « {rid} » : readme/{lg}/{nom} est vide")
    for lg in langues:
        dossier = racine / "readme" / lg
        cites = {(r.get("fichier") or {}).get(lg) for r in rubriques}
        if dossier.is_dir():
            erreurs += [f"readme/{lg}/{f.name} : orphelin (absent du gabarit)"
                        for f in sorted(dossier.iterdir()) if f.is_file() and f.name not in cites]
    gabarit = Gabarit(version=str(brut.get("version", "")), regles_strictes=bool(brut.get("regles_strictes", False)),
                      langues=list(langues), rubriques=rubriques, captures=brut.get("captures") or {},
                      liens=brut.get("liens") or {}, attribution=brut.get("attribution") or {})
    if not gabarit.version.startswith("v"):
        erreurs.append(f"{GABARIT} : `version` attendue au format vX.Y.Z (reçu {gabarit.version!r})")
    if erreurs:
        raise ErreurGabarit(erreurs)
    for lg in gabarit.langues:
        for rid, texte in textes_des_rubriques(gabarit, lg, racine):
            erreurs += [f"rubrique « {rid} » ({lg}) : {e}" for e in _references_inconnues(texte, gabarit, lg)]
            if gabarit.regles_strictes:
                erreurs += [f"rubrique « {rid} » ({lg}) : {e}" for e in paragraphes_coupes(texte)]
        ensemble = "\n".join(t for _r, t in textes_des_rubriques(gabarit, lg, racine))
        if any(gabarit.captures[i].get("corpus_ami") for g, i in _REFERENCE.findall(ensemble)
               if g == "capture" and i in gabarit.captures) and EMPLACEMENT_ATTRIBUTION not in ensemble:
            erreurs.append(f"({lg}) capture du corpus de démonstration sans emplacement d'attribution {EMPLACEMENT_ATTRIBUTION}")
    if erreurs:
        raise ErreurGabarit(erreurs)
    return gabarit


def textes_des_rubriques(gabarit: Gabarit, langue: str, racine: Path = RACINE) -> list[tuple[str, str]]:
    return [(r["id"], (racine / "readme" / langue / r["fichier"][langue]).read_text(encoding="utf-8"))
            for r in gabarit.rubriques]


_REFERENCE = re.compile(r"\{\{(capture|lien):([^}]*)\}\}")
EMPLACEMENT_ATTRIBUTION = "{{attribution}}"
EMPLACEMENT_VERSION = "{{version}}"


def _references_inconnues(texte: str, gabarit: Gabarit, langue: str) -> list[str]:
    erreurs = []
    if EMPLACEMENT_ATTRIBUTION in texte and not str(gabarit.attribution.get(langue, "")).strip():
        erreurs.append("emplacement d'attribution sans texte d'attribution dans le gabarit")
    for genre, identifiant in _REFERENCE.findall(texte):
        table = gabarit.captures if genre == "capture" else gabarit.liens
        if identifiant not in table:
            erreurs.append(f"{{{{{genre}:{identifiant}}}}} inconnu du gabarit")
        elif genre == "capture" and langue not in (table[identifiant].get("chemin") or {}):
            erreurs.append(f"capture « {identifiant} » sans chemin pour {langue}")
    return erreurs


# --------------------------------------------------------------------------- assemblage

def _capture(gabarit: Gabarit, identifiant: str, langue: str) -> str:
    c = gabarit.captures[identifiant]
    return f"![{c['alt'][langue]}]({c['chemin'][langue]})\n*{c['legende'][langue]}*"


def assembler(gabarit: Gabarit, langue: str, racine: Path = RACINE) -> str:
    """Le README d'une langue : en-tête de fichier généré, puis les rubriques séparées par une ligne vide.

    Sortie déterministe : aucune date, aucun identifiant, sauts de ligne `\\n`, un seul `\\n` final."""
    def remplacer(m: re.Match) -> str:
        genre, identifiant = m.groups()
        if genre == "lien":
            return str(gabarit.liens[identifiant])
        return _capture(gabarit, identifiant, langue)

    def completer(texte: str) -> str:
        texte = _REFERENCE.sub(remplacer, texte)
        texte = texte.replace(EMPLACEMENT_VERSION, gabarit.version)
        return texte.replace(EMPLACEMENT_ATTRIBUTION, str(gabarit.attribution.get(langue, "")).strip("\n"))

    morceaux = [completer(texte).strip("\n") for _rid, texte in textes_des_rubriques(gabarit, langue, racine)]
    return ENTETES[langue] + "\n" + "\n\n".join(morceaux) + "\n"


def tout_assembler(racine: Path = RACINE) -> dict[str, str]:
    gabarit = charger(racine)
    return {SORTIES[lg]: assembler(gabarit, lg, racine) for lg in gabarit.langues}


def generer(racine: Path = RACINE) -> dict[str, tuple[int, int]]:
    """Écrit les deux README (et eux seuls) ; renvoie (lignes, mots) par fichier."""
    sorties = tout_assembler(racine)
    for nom, texte in sorties.items():
        with open(racine / nom, "w", encoding="utf-8", newline="\n") as f:
            f.write(texte)
    return {nom: mesure(texte) for nom, texte in sorties.items()}


def verifier(racine: Path = RACINE) -> list[str]:
    """Les écarts entre une régénération et les fichiers présents (liste vide : à jour). N'écrit rien."""
    ecarts = []
    for nom, attendu in tout_assembler(racine).items():
        chemin = racine / nom
        present = chemin.read_text(encoding="utf-8") if chemin.is_file() else None
        if present == attendu:
            continue
        if present is None:
            ecarts.append(f"{nom} : absent")
            continue
        a, b = present.splitlines(), attendu.splitlines()
        premiere = next((i for i, (x, y) in enumerate(zip(a, b), 1) if x != y), min(len(a), len(b)) + 1)
        ecarts.append(f"{nom} : diffère à la ligne {premiere} ({len(a)} lignes présentes, {len(b)} attendues)")
    return ecarts


# --------------------------------------------------------------------------- mesures et règles d'écriture

def mesure(texte: str) -> tuple[int, int]:
    """(lignes, mots) d'un texte."""
    return len(texte.splitlines()), len(texte.split())


def ecart_de_longueur(mots_a: int, mots_b: int) -> float:
    """L'écart relatif entre deux nombres de mots, rapporté au plus petit (lecture la plus stricte de « 10 % près »)."""
    petit = min(mots_a, mots_b)
    return abs(mots_a - mots_b) / petit if petit else float("inf")


def blocs(texte: str) -> list[tuple[str, str]]:
    """Découpe en blocs séparés par une ligne vide : (genre, texte) avec genre parmi
    code, tableau, liste, html, titre, prose. Un bloc de code garde ses lignes vides."""
    resultat: list[tuple[str, str]] = []
    courant: list[str] = []
    dans_code = False

    def vider() -> None:
        if not courant:
            return
        bloc = "\n".join(courant)
        premier = courant[0].lstrip()
        if premier.startswith("```"):
            genre = "code"
        elif premier.startswith("|"):
            genre = "tableau"
        elif re.match(r"([-*+]|\d+[.)])\s", premier):
            genre = "liste"
        elif premier.startswith("<"):
            genre = "html"
        elif premier.startswith("#"):
            genre = "titre"
        elif premier.startswith(">"):
            genre = "citation"
        else:
            genre = "prose"
        resultat.append((genre, bloc))
        courant.clear()

    for ligne in texte.splitlines():
        if ligne.lstrip().startswith("```"):
            if not dans_code:
                vider()
            courant.append(ligne)
            dans_code = not dans_code
            if not dans_code:
                vider()
            continue
        if dans_code:
            courant.append(ligne)
        elif not ligne.strip():
            vider()
        else:
            courant.append(ligne)
    vider()
    return resultat


def paragraphes_coupes(texte: str) -> list[str]:
    """Règle de coupe : un paragraphe par ligne source. Les paragraphes de prose qui contiennent un saut
    de ligne simple (code, tableaux, listes, blocs HTML et titres exemptés)."""
    return [f"paragraphe coupé sur plusieurs lignes : « {b.splitlines()[0][:50]}… »"
            for genre, b in blocs(texte) if genre in {"prose", "citation"} and "\n" in b]


def _sans_balisage(ligne: str) -> str:
    ligne = re.sub(r"<!--.*?-->", "", ligne)
    ligne = re.sub(r"`[^`]*`", "", ligne)                         # code en ligne : hors contrôle
    ligne = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", ligne)            # images
    ligne = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", ligne)        # lien : le texte reste, la cible part
    ligne = re.sub(r"<[^>]+>", "", ligne)
    return ligne.replace("**", "").replace("*", "")


def phrases_de_prose(texte: str) -> list[str]:
    """Les phrases des paragraphes de prose et des éléments de liste (hors code, tableaux, titres, HTML,
    légendes de captures en italique seul sur une ligne, mention de fichier généré)."""
    phrases: list[str] = []
    for genre, bloc in blocs(texte):
        if genre not in {"prose", "liste", "citation"}:
            continue
        for ligne in bloc.splitlines():
            ligne = re.sub(r"^\s*([-*+]|\d+[.)])\s+", "", ligne).lstrip("> ")
            if re.fullmatch(r"\s*\*[^*].*\*\s*", ligne) and not ligne.strip().startswith("**"):
                continue                                           # légende d'une capture
            propre = _sans_balisage(ligne).strip()
            phrases += [p for p in re.split(r"(?<=[.!?])\s+", propre) if p.strip()]
    return phrases


def fautes_d_ecriture(texte: str, langue: str) -> list[str]:
    """Le plancher de ton : adresse au lecteur, impératif en tête de phrase, superlatif, phrase trop longue."""
    fautes = []
    adresse = re.compile(rf"\b({ADRESSES[langue]})\b", re.IGNORECASE)
    superlatif = re.compile(rf"\b({SUPERLATIFS[langue]})\b", re.IGNORECASE)
    for phrase in phrases_de_prose(texte):
        if m := adresse.search(phrase):                       # « on » n'est dans la liste que du français
            fautes.append(f"adresse au lecteur « {m.group(1)} » : {phrase[:60]}")
        # L'impératif se cherche au début de la phrase ET de chaque proposition après « : » ou « ; ».
        for proposition in re.split(r"\s*[;:]\s+", phrase):
            debut = re.match(r"\W*(\w+)", proposition)
            if debut and debut.group(1).lower() in IMPERATIFS[langue]:
                fautes.append(f"impératif « {debut.group(1)} » : {phrase[:60]}")
        if m := superlatif.search(phrase):
            fautes.append(f"superlatif « {m.group(1)} » : {phrase[:60]}")
        if len(phrase.split()) > PHRASE_MAX_MOTS:
            fautes.append(f"phrase de plus de {PHRASE_MAX_MOTS} mots : {phrase[:60]}…")
    return fautes


def fautes_de_titre(texte: str, langue: str) -> list[str]:
    """Les titres de section (`## …`) ne commencent pas par un impératif, comme la prose."""
    fautes = []
    en_code = False
    for ligne in texte.splitlines():
        if ligne.lstrip().startswith("```"):
            en_code = not en_code
        m = None if en_code else re.match(r"##\s+(\w+)", ligne)
        if m and m.group(1).lower() in IMPERATIFS[langue]:
            fautes.append(f"titre à l'impératif « {m.group(1)} » : {ligne.strip()}")
    return fautes


def titre_d_ouverture_manquant(texte: str) -> bool:
    """Une rubrique (hors ouverture) commence par un titre `## …`."""
    return not texte.lstrip().startswith("## ")


def attribution_manquante(texte: str) -> list[str]:
    """Tant qu'une capture du corpus de démonstration est affichée, l'attribution CC BY 4.0 figure dans le texte."""
    if DOSSIER_CAPTURES not in texte:
        return []
    return [mot for mot in ATTRIBUTION if mot not in texte]


# --------------------------------------------------------------------------- liens, ancres, images

def ancre_github(titre: str) -> str:
    """L'ancre d'un titre comme GitHub la calcule : minuscules, balisage retiré, ponctuation retirée sauf tirets
    et soulignés, lettres accentuées conservées, espaces en tirets, tirets répétés NON fusionnés."""
    t = re.sub(r"`([^`]*)`", r"\1", titre)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"<[^>]+>", "", t).replace("*", "").strip().lower()
    t = "".join(c for c in t if c.isalnum() or c in " -_" or unicodedata.category(c).startswith("M"))
    return t.replace(" ", "-")


def ancres_du_texte(texte: str) -> set[str]:
    """Toutes les ancres des titres ATX d'un document (hors blocs de code), doublons suffixés -1, -2…"""
    vues: dict[str, int] = {}
    resultat: set[str] = set()
    dans_code = False
    for ligne in texte.splitlines():
        if ligne.lstrip().startswith("```"):
            dans_code = not dans_code
        if dans_code:
            continue
        m = re.match(r"#{1,6}\s+(.*?)\s*#*\s*$", ligne)
        if not m:
            continue
        base = ancre_github(m.group(1))
        n = vues.get(base, 0)
        vues[base] = n + 1
        resultat.add(base if n == 0 else f"{base}-{n}")
    return resultat


def _hors_code(texte: str) -> str:
    texte = re.sub(r"```.*?```", "", texte, flags=re.DOTALL)
    return re.sub(r"`[^`\n]*`", "", texte)


def cibles_des_liens(texte: str) -> list[str]:
    """Toutes les cibles citées : liens et images Markdown, `src`, `srcset` et `href` HTML (hors code)."""
    nu = _hors_code(texte)
    cibles = re.findall(r"!?\[[^\]]*\]\(([^)\s]+)", nu)
    cibles += re.findall(r'\b(?:src|href)="([^"]+)"', nu)
    for jeu in re.findall(r'\bsrcset="([^"]+)"', nu):
        cibles += [p.split()[0] for p in jeu.split(",") if p.strip()]
    return cibles


def images_citees(texte: str) -> list[str]:
    nu = _hors_code(texte)
    cibles = re.findall(r"!\[[^\]]*\]\(([^)\s]+)", nu)
    cibles += re.findall(r'<img\b[^>]*\bsrc="([^"]+)"', nu)
    for jeu in re.findall(r'\bsrcset="([^"]+)"', nu):
        cibles += [p.split()[0] for p in jeu.split(",") if p.strip()]
    return cibles


def liens_casses(texte: str, fichier: str, exportes: set[str], lire) -> list[str]:
    """Les liens relatifs et ancres qui ne résolvent pas dans l'arbre exporté.

    `fichier` : chemin du document dans le dépôt ; `exportes` : chemins de l'arbre exporté ; `lire(chemin)` :
    texte d'un document de l'arbre. Les liens `http(s)`, `mailto` ne sont pas résolus (pas de réseau)."""
    casses = []
    dossier = PurePosixPath(fichier).parent
    for cible in cibles_des_liens(texte):
        if re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*:", cible):
            continue
        chemin, _, fragment = cible.partition("#")
        if chemin:
            resolu = str((dossier / unquote(chemin)).as_posix()) if str(dossier) != "." else unquote(chemin)
            resolu = PurePosixPath(resolu).as_posix()
            # Normalise `a/../b`, sans quitter la racine.
            parties: list[str] = []
            for p in resolu.split("/"):
                if p == "..":
                    if parties:
                        parties.pop()
                elif p not in ("", "."):
                    parties.append(p)
            resolu = "/".join(parties)
        else:
            resolu = fichier
        existe = resolu in exportes or any(e.startswith(resolu.rstrip("/") + "/") for e in exportes)
        if not existe:
            casses.append(f"{cible} : introuvable dans l'arbre exporté")
            continue
        if fragment and resolu.endswith(".md") and resolu in exportes:
            document = texte if resolu == fichier else lire(resolu)
            if unquote(fragment).lower() not in ancres_du_texte(document):
                casses.append(f"{cible} : ancre absente de {resolu}")
    return casses


# --------------------------------------------------------------------------- ligne de commande

def main(argv: list[str] | None = None) -> int:
    parseur = argparse.ArgumentParser(prog="readme.py", description=__doc__.splitlines()[0])
    parseur.add_argument("commande", choices=["generer", "verifier"])
    parseur.add_argument("--racine", default=str(RACINE), help="racine du dépôt (par défaut : celle de l'outil)")
    try:
        args = parseur.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    racine = Path(args.racine)
    try:
        if args.commande == "generer":
            for nom, (lignes, mots) in generer(racine).items():
                print(f"{nom} : {lignes} lignes, {mots} mots")
            return 0
        ecarts = verifier(racine)
    except ErreurGabarit as e:
        print("Gabarit ou rubrique invalide :", *e.erreurs, sep="\n  - ", file=sys.stderr)
        return 3
    for e in ecarts:
        print(e)
    return 1 if ecarts else 0


if __name__ == "__main__":
    sys.exit(main())
