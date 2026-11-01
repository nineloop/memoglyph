#!/usr/bin/env python3
"""Récupère le corpus de démo : trois réunions du AMI Meeting Corpus.

Le corpus n'est JAMAIS versionné : ce script le télécharge dans `demo/ami/`
(ignoré par git) et vérifie chaque fichier contre son empreinte SHA-256. Un
fichier déjà présent et conforme n'est pas retéléchargé ; un fichier présent
mais différent est signalé, jamais écrasé en silence.

Licence et attribution : `docs/corpus-demo.md` (AMI, CC BY 4.0).

Exemples :
  python tools/corpus_demo.py              # télécharge ce qui manque, vérifie tout
  python tools/corpus_demo.py --verifier   # vérifie sans rien télécharger
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
DOSSIER = RACINE / "demo" / "ami"

_AUDIO = "https://groups.inf.ed.ac.uk/ami/AMICorpusMirror/amicorpus/{m}/audio/{m}.Mix-Headset.wav"


@dataclass(frozen=True)
class Fichier:
    nom: str
    url: str
    sha256: str
    taille: int


# Trois réunions-scénarios à quatre participants, une par site d'enregistrement
# (Édimbourg, IDIAP, TNO), en « headset mix » : un seul fichier par réunion, 16 kHz
# mono. L'archive d'annotations porte la transcription de référence mot à mot.
FICHIERS = (
    Fichier("ES2002a.Mix-Headset.wav", _AUDIO.format(m="ES2002a"),
            "9c76866990fcc8b84006dc32d273ad99df439090b748ebe72103bb78c3216ee7", 40_724_524),
    Fichier("IS1009a.Mix-Headset.wav", _AUDIO.format(m="IS1009a"),
            "6eb5a0ede0d9e72794f976ce7bea5b78133eae969f99b4c5418b43c2468d25b1", 26_844_093),
    Fichier("TS3003a.Mix-Headset.wav", _AUDIO.format(m="TS3003a"),
            "b6ce59ff735c234afdd94b13744aa26f6785af746170543fd03b33160e920a6d", 48_180_608),
    Fichier("ami_public_manual_1.6.2.zip",
            "https://groups.inf.ed.ac.uk/ami/AMICorpusAnnotations/ami_public_manual_1.6.2.zip",
            "b56e5babb2496b8795deeeda7e71178d7fbc9963f94276cf2a3f4b56ebbc9f9d", 22_887_865),
)


def empreinte(chemin: Path) -> str:
    h = hashlib.sha256()
    with chemin.open("rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


def etat(fichier: Fichier, dossier: Path) -> str:
    """`absent`, `conforme` ou `different`."""
    chemin = dossier / fichier.nom
    if not chemin.exists():
        return "absent"
    return "conforme" if empreinte(chemin) == fichier.sha256 else "different"


def telecharger(fichier: Fichier, dossier: Path) -> None:
    """Écrit dans un fichier temporaire, vérifie, puis renomme : un
    téléchargement interrompu ou altéré ne laisse jamais un faux fichier."""
    dossier.mkdir(parents=True, exist_ok=True)
    tmp = dossier / (fichier.nom + ".part")
    with urllib.request.urlopen(fichier.url, timeout=60) as r, tmp.open("wb") as f:
        while bloc := r.read(1 << 20):
            f.write(bloc)
    obtenu = empreinte(tmp)
    if obtenu != fichier.sha256:
        tmp.unlink()
        raise RuntimeError(f"{fichier.nom} : empreinte {obtenu}, attendue {fichier.sha256}")
    tmp.replace(dossier / fichier.nom)


def media(nom: str, dossier: Path = DOSSIER) -> Path:
    """Chemin d'un fichier du corpus, avec la marche à suivre s'il manque."""
    chemin = dossier / nom
    if not chemin.exists():
        raise SystemExit(f"corpus de démo absent : {chemin} — lancer "
                         "`python tools/corpus_demo.py` (voir docs/corpus-demo.md)")
    return chemin


def extrait(nom: str, debut_s: float, duree_s: float, dossier_sortie: Path,
            dossier: Path = DOSSIER) -> Path:
    """Découpe un passage d'un fichier du corpus (WAV 16 kHz mono), avec ffmpeg.

    Les tests qui veulent un média COURT le découpent à l'exécution, dans leur
    dossier jetable : le corpus téléchargé reste tel quel.
    """
    source = media(nom, dossier)
    sortie = dossier_sortie / f"{source.stem}-{int(debut_s)}s-{int(duree_s)}s.wav"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-ss", str(debut_s), "-t", str(duree_s), "-i", str(source),
         "-ac", "1", "-ar", "16000", str(sortie)],
        check=True,
    )
    return sortie


def main(argv: list[str] | None = None, dossier: Path = DOSSIER) -> int:
    ap = argparse.ArgumentParser(description="Corpus de démo AMI (CC BY 4.0).")
    ap.add_argument("--verifier", action="store_true",
                    help="vérifie les fichiers présents sans rien télécharger")
    args = ap.parse_args(argv)

    total = sum(f.taille for f in FICHIERS)
    print(f"Corpus de démo AMI : {len(FICHIERS)} fichiers, {total / 1e6:.1f} Mo, dans {dossier}")
    echecs = 0
    for fichier in FICHIERS:
        e = etat(fichier, dossier)
        if e == "absent" and not args.verifier:
            print(f"  ↓ {fichier.nom} ({fichier.taille / 1e6:.1f} Mo)")
            try:
                telecharger(fichier, dossier)
                e = "conforme"
            except Exception as exc:  # noqa: BLE001 — on rend compte et on continue
                print(f"  ✗ {exc}", file=sys.stderr)
                echecs += 1
                continue
        marque = {"conforme": "✓", "absent": "·", "different": "✗"}[e]
        print(f"  {marque} {fichier.nom} : {e}")
        if e == "different":
            echecs += 1
        elif e == "absent":
            echecs += 1
    return 1 if echecs else 0


if __name__ == "__main__":
    raise SystemExit(main())
