"""Attribution d'un identifiant stable aux documents qui n'en ont pas.

Opération IDEMPOTENTE et NON DESTRUCTIVE : un `id` déjà présent et valide n'est
jamais remplacé — l'écraser orphelinerait tout ce qui le référence. Seul le
front-matter est touché ; le corps du document n'est ni relu ni réécrit.

L'ordre d'attribution suit la date de création du fichier, pour que l'ordre
alphabétique des identifiants reflète l'ordre d'arrivée dans le corpus.
"""

from __future__ import annotations

from pathlib import Path

from . import docid
from .config import OUTPUT_DIR


def _sans_id(chemin: Path) -> bool:
    """Vrai si le fichier n'a pas encore d'identifiant valide."""
    from .library import _split_frontmatter
    meta, _ = _split_frontmatter(chemin.read_text(encoding="utf-8"))
    return not docid.est_valide(meta.get("id"))


def attribuer(racine: Path | None = None, *, simulation: bool = True) -> dict:
    """Écrit un `id` dans chaque .md qui n'en a pas.

    `simulation=True` par défaut : on n'écrit rien tant que l'appelant ne l'a pas
    demandé explicitement. Une migration silencieuse sur des données personnelles
    n'est pas acceptable.
    """
    from . import writer_md
    from .library import _split_frontmatter

    base = racine or OUTPUT_DIR
    fichiers = sorted(
        (f for f in base.rglob("*.md") if ".trash" not in str(f)),
        key=lambda f: f.stat().st_ctime,
    )
    vus, attribues, deja = 0, [], 0
    for f in fichiers:
        vus += 1
        texte = f.read_text(encoding="utf-8")
        meta, corps = _split_frontmatter(texte)
        if docid.est_valide(meta.get("id")):
            deja += 1
            continue
        # `id` en TÊTE du front-matter : c'est l'identité, elle se lit d'abord.
        nouveau_meta = {"id": docid.nouveau(horodatage_ms=int(f.stat().st_ctime * 1000))}
        nouveau_meta.update(meta)
        if not simulation:
            f.write_text(
                writer_md._yaml_frontmatter(nouveau_meta) + "\n\n" + corps.lstrip("\n"),
                encoding="utf-8",
            )
        attribues.append(f.name)
    return {"vus": vus, "deja_identifies": deja, "attribues": len(attribues),
            "simulation": simulation, "fichiers": attribues}
