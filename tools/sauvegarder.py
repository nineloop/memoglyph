"""Sauvegarde et restauration complètes d'une racine de données Memoglyph.

    python tools/sauvegarder.py sauvegarder --vers ~/Sauvegardes [--racine .] [--forcer]
    python tools/sauvegarder.py restaurer ~/Sauvegardes/memoglyph-AAAA-MM-JJ-HHhMMSS [--racine .] [--forcer]

Périmètre (docs/sauvegarde.md) : `output/` (les Markdown, qui font foi, l'audio archivé,
les manifestes de projets, les faits de l'ECL) et les trois bases : `index.db` (index et
registre des voix, qui ne se régénère pas), `auth.db` (comptes, sessions, jetons
d'appareil, partages), `usage.db` (télémétrie et coûts). Les bases sont copiées par l'API
de sauvegarde de SQLite, qui rend une image cohérente même avec un journal WAL.

Les deux commandes refusent de tourner pendant que Memoglyph tourne : une sauvegarde
prise en pleine écriture capterait un compte rendu à moitié écrit. Elles n'écrivent que
dans le dossier choisi (la sauvegarde) ou dans la racine (la restauration), et ne
suppriment rien : une restauration par-dessus des données existantes, seulement avec
`--forcer`, les range d'abord dans `.avant-restauration-<date>/`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

RACINE_PAR_DEFAUT = Path(__file__).resolve().parents[1]
BASES = ("index.db", "auth.db", "usage.db")
DOSSIERS = ("output",)
PERIMETRE = DOSSIERS + BASES
MANIFESTE = "MANIFESTE.json"
FORMAT = 1
PORTS_PAR_DEFAUT = (8770, 8771)


class Refus(Exception):
    """L'opération ne se fait pas ; le message dit pourquoi et comment faire."""


def _horodatage(maintenant: Optional[datetime] = None) -> str:
    return (maintenant or datetime.now()).strftime("%Y-%m-%d-%Hh%M%S")


def _empreinte(chemin: Path) -> str:
    h = hashlib.sha256()
    with chemin.open("rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


def _ecoute(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _ports(racine: Path) -> tuple[int, ...]:
    fichier = racine / ".memoglyph-ports"
    if not fichier.exists():
        return PORTS_PAR_DEFAUT
    ports = []
    for ligne in fichier.read_text(encoding="utf-8").splitlines():
        cle, _, valeur = ligne.partition("=")
        if cle.strip() in {"UI_PORT", "API_PORT"} and valeur.strip().isdigit():
            ports.append(int(valeur))
    return tuple(ports) or PORTS_PAR_DEFAUT


def memoglyph_tourne(racine: Path) -> Optional[str]:
    """Pourquoi l'on pense que Memoglyph tourne sur cette racine, ou `None`.

    Un fichier `.memoglyph-*.pid` dont le processus vit, ou un port de la pile
    (`.memoglyph-ports`, sinon 8770 et 8771) qui écoute.
    """
    for pid_fichier in sorted(racine.glob(".memoglyph*.pid")):
        texte = pid_fichier.read_text(encoding="utf-8").strip()
        if texte.isdigit():
            try:
                os.kill(int(texte), 0)
            except ProcessLookupError:
                continue
            except PermissionError:
                pass
            return f"le processus {texte} de {pid_fichier.name} est vivant"
    for port in _ports(racine):
        if _ecoute(port):
            return f"le port {port} de Memoglyph écoute"
    return None


def _verifier_arret(racine: Path, forcer: bool) -> None:
    raison = memoglyph_tourne(racine)
    if raison and not forcer:
        raise Refus(f"Memoglyph tourne ({raison}) : arrêtez-le d'abord (./stop.sh), "
                    "ou relancez avec --forcer en connaissance de cause.")


def _copier_base(source: Path, cible: Path) -> None:
    lecture = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        ecriture = sqlite3.connect(cible)
        try:
            lecture.backup(ecriture)
        finally:
            ecriture.close()
    finally:
        lecture.close()


def _dans(chemin: Path, dossier: Path) -> bool:
    try:
        chemin.resolve().relative_to(dossier.resolve())
        return True
    except ValueError:
        return False


def sauvegarder(racine: Path, vers: Path, *, forcer: bool = False,
                maintenant: Optional[datetime] = None) -> Path:
    """Copie le périmètre de `racine` dans un dossier daté, neuf, sous `vers`."""
    racine, vers = racine.resolve(), vers.expanduser().resolve()
    _verifier_arret(racine, forcer)
    if _dans(vers, racine / "output"):
        raise Refus("La sauvegarde ne se range pas dans output/ : elle s'y copierait elle-même.")
    presents = [nom for nom in PERIMETRE if (racine / nom).exists()]
    if not presents:
        raise Refus(f"Rien à sauvegarder dans {racine} : ni output/ ni base.")
    dossier = vers / f"memoglyph-{_horodatage(maintenant)}"
    try:
        dossier.mkdir(parents=True, exist_ok=False)   # jamais par-dessus une sauvegarde existante
    except FileExistsError:
        raise Refus(f"{dossier} existe déjà : une sauvegarde ne s'écrit jamais par-dessus une autre.")
    for nom in presents:
        source, cible = racine / nom, dossier / nom
        if nom in BASES:
            _copier_base(source, cible)
        else:
            shutil.copytree(source, cible, symlinks=True)
    fichiers = sorted(p for p in dossier.rglob("*") if p.is_file() and not p.is_symlink())
    manifeste = {
        "format": FORMAT,
        "cree": (maintenant or datetime.now()).isoformat(timespec="seconds"),
        "racine": str(racine),
        "elements": presents,
        "empreintes": {str(p.relative_to(dossier)): _empreinte(p) for p in fichiers},
    }
    (dossier / MANIFESTE).write_text(json.dumps(manifeste, indent=1, ensure_ascii=False), encoding="utf-8")
    return dossier


def _lire_manifeste(sauvegarde: Path) -> dict:
    fichier = sauvegarde / MANIFESTE
    if not fichier.is_file():
        raise Refus(f"{sauvegarde} n'est pas une sauvegarde de Memoglyph : {MANIFESTE} absent.")
    manifeste = json.loads(fichier.read_text(encoding="utf-8"))
    if manifeste.get("format") != FORMAT:
        raise Refus(f"Format de sauvegarde inconnu : {manifeste.get('format')!r}.")
    elements = manifeste.get("elements") or []
    if not elements or any(e not in PERIMETRE for e in elements):
        raise Refus(f"Éléments hors du périmètre dans le manifeste : {elements!r}.")
    for relatif, attendue in (manifeste.get("empreintes") or {}).items():
        chemin = sauvegarde / relatif
        if Path(relatif).is_absolute() or ".." in Path(relatif).parts or not _dans(chemin, sauvegarde):
            raise Refus(f"Chemin hors de la sauvegarde dans le manifeste : {relatif!r}.")
        if not chemin.is_file() or _empreinte(chemin) != attendue:
            raise Refus(f"Sauvegarde abîmée : {relatif} manque ou ne correspond plus à son empreinte.")
    return manifeste


def restaurer(sauvegarde: Path, racine: Path, *, forcer: bool = False,
              maintenant: Optional[datetime] = None) -> list[str]:
    """Remet le périmètre d'une sauvegarde vérifiée dans `racine` ; rend les éléments remis."""
    sauvegarde, racine = sauvegarde.expanduser().resolve(), racine.resolve()
    manifeste = _lire_manifeste(sauvegarde)
    _verifier_arret(racine, forcer)
    elements = manifeste["elements"]
    occupes = [n for n in PERIMETRE for c in (n, f"{n}-wal", f"{n}-shm") if (racine / c).exists()]
    if occupes and not forcer:
        raise Refus(f"La racine contient déjà {', '.join(sorted(set(occupes)))} : restaurez dans une "
                    "racine vide, ou relancez avec --forcer pour les ranger d'abord à part.")
    if occupes:
        cote = racine / f".avant-restauration-{_horodatage(maintenant)}"
        cote.mkdir(exist_ok=False)
        for nom in PERIMETRE:
            for c in (nom, f"{nom}-wal", f"{nom}-shm"):
                if (racine / c).exists():
                    (racine / c).rename(cote / c)
    for nom in elements:
        source, cible = sauvegarde / nom, racine / nom
        if not _dans(cible, racine):
            raise Refus(f"{nom} sortirait de la racine.")
        if source.is_dir():
            shutil.copytree(source, cible, symlinks=True)
        else:
            shutil.copy2(source, cible)
    return list(elements)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Sauvegarde et restauration de Memoglyph (docs/sauvegarde.md).")
    sous = ap.add_subparsers(dest="commande", required=True)
    s = sous.add_parser("sauvegarder", help="copie output/ et les bases dans un dossier daté")
    s.add_argument("--vers", type=Path, required=True, help="dossier où créer la sauvegarde")
    r = sous.add_parser("restaurer", help="remet une sauvegarde vérifiée dans la racine")
    r.add_argument("sauvegarde", type=Path, help="dossier memoglyph-AAAA-MM-JJ-HHhMMSS")
    for p in (s, r):
        p.add_argument("--racine", type=Path, default=RACINE_PAR_DEFAUT, help="racine de données de Memoglyph")
        p.add_argument("--forcer", action="store_true",
                       help="passer outre Memoglyph en marche ; en restauration, ranger l'existant à part")
    args = ap.parse_args(argv)
    try:
        if args.commande == "sauvegarder":
            dossier = sauvegarder(args.racine, args.vers, forcer=args.forcer)
            print(f"✓ Sauvegarde : {dossier}")
        else:
            remis = restaurer(args.sauvegarde, args.racine, forcer=args.forcer)
            print(f"✓ Restauré dans {args.racine.resolve()} : {', '.join(remis)}")
    except Refus as exc:
        print(f"Refus : {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
