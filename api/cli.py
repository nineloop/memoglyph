"""Commandes de maintenance sur la machine : `memoglyph reset-password`, `memoglyph attribuer-bibliotheque`, `memoglyph ecl-extraire`, `memoglyph sensibilite-reunions`, `memoglyph mcp`.

Mot de passe oublié, sans e-mail : la réinitialisation est réservée à
qui a accès au serveur. Le nouveau mot de passe se saisit au clavier, deux fois,
sans écho ; `--stdin` le lit sur l'entrée standard, pour un script. Toutes les
sessions du compte sont révoquées.

Bibliothèque d'avant les comptes : ses projets n'ont pas de
propriétaire et ne sont visibles que des administrateurs.
`attribuer-bibliotheque <utilisateur>` les liste sans rien écrire ; avec
`--appliquer`, il les attribue à ce compte. Seuls les manifestes changent.

Faits ECL de l'existant : `ecl-extraire` liste les transcripts citables
sans fichier de faits, ou dont des faits sont périmés (source corrigée depuis
l'extraction), sans rien écrire ; avec `--appliquer`, il les extrait un à un par
le modèle configuré et affiche, pour chacun, les faits gardés et refusés.
`--journal <fichier>` y écrit chaque fait proposé avec son sort, hors de la
bibliothèque. En fin de commande, le serveur du modèle qu'elle a
démarré est arrêté. Elle refuse de tourner pendant que l'application tourne
(même relevé que `tools/sauvegarder.py`), sauf `--forcer` : la réservation des
travaux lourds ne vaut que dans un processus, elle ne coordonne pas celui-ci avec
le serveur.

Sensibilité des réunions : `sensibilite-reunions` liste, sans rien écrire, les réunions
que le registre des réunions ne règle pas encore, chacune dans son projet, avec le niveau
le plus strict de leurs documents, et les entrées d'un format antérieur (rangées par
réunion seule) ; avec `--appliquer`, il les y inscrit par projet, chacune avec les
documents que son réglage couvre. Aucun `.md` n'est
réécrit, une réunion déjà réglée garde son niveau, et une seconde passe n'écrit rien. Elle
ne change aucun niveau appliqué : sans entrée, une réunion vaut déjà le plus strict de ses
documents.
Elle ne tourne qu'à la demande, jamais au démarrage, et refuse d'écrire pendant que
l'application tourne, sauf `--forcer`.

Serveur MCP en lecture seule : `mcp <utilisateur>` sert la portée de lecture de ce
compte sur stdio (`api/mcp.py`, `docs/mcp.md`).
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path
from typing import Optional

from core import ecl, library, llm
from core import projects as project_store

from . import auth


def _lire_mot_de_passe(depuis_stdin: bool) -> str:
    if depuis_stdin:
        return sys.stdin.readline().rstrip("\n")
    premier = getpass.getpass("Nouveau mot de passe : ")
    if getpass.getpass("Confirmation : ") != premier:
        raise auth.ErreurAuth("Les deux saisies diffèrent.")
    return premier


def _attribuer(utilisateur: str, appliquer: bool) -> int:
    compte = next((c for c in auth.lister_comptes()
                   if c["identifiant"].casefold() == utilisateur.casefold()), None)
    if compte is None:
        print(f"❌ Aucun compte « {utilisateur} ».", file=sys.stderr)
        return 1
    if not compte["actif"]:
        print(f"❌ Le compte « {compte['identifiant']} » est désactivé.", file=sys.stderr)
        return 1
    try:
        bilan = project_store.attribuer(
            library.OUTPUT_DIR, compte["identifiant"], simulation=not appliquer,
        )
    except project_store.ProjectManifestError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    print(f"Bibliothèque : {library.OUTPUT_DIR}")
    print("Attribution appliquée." if appliquer else "Simulation : rien n'est écrit.")
    projets = bilan["sans_proprietaire"]
    print(f"Projets sans propriétaire : {len(projets)}")
    for projet in projets:
        print(f"  - {projet['name']}  (dossier {projet['directory']}, id {projet['id']})")
    if bilan["sans_manifeste"]:
        print("Dossiers sans manifeste, à migrer d'abord (ignorés) : "
              + ", ".join(bilan["sans_manifeste"]))
    if projets:
        if appliquer:
            print(f"✅ {len(projets)} projet(s) attribué(s) à « {compte['identifiant']} ».")
        else:
            print(f"Relancer avec --appliquer pour les attribuer à « {compte['identifiant']} ».")
    return 0


def _application_tourne() -> Optional[str]:
    """Pourquoi l'on pense que l'application tourne sur cette racine de données, ou None.

    Fichiers `.memoglyph*.pid` vivants ou ports de `.memoglyph-ports` en écoute, à côté
    de `output/` : le relevé de `tools/sauvegarder.py`, pas un second.
    """
    from tools.sauvegarder import memoglyph_tourne  # noqa: PLC0415 — outil de la même racine

    return memoglyph_tourne(library.OUTPUT_DIR.parent)


def _ecl_extraire(appliquer: bool, journal: Optional[Path] = None, forcer: bool = False) -> int:
    if not ecl.actives():
        print(f"❌ {ecl.DESACTIVES}", file=sys.stderr)
        return 1
    if journal is not None and journal.resolve().is_relative_to(library.OUTPUT_DIR.resolve()):
        print("❌ Le journal s'écrit hors de la bibliothèque.", file=sys.stderr)
        return 1
    # `resources.heavy` coordonne les fils d'UN processus : contre l'application qui tourne à
    # côté (transcription, compte rendu, relance), cette commande se dispute la mémoire et le
    # serveur du modèle. Refus avant tout, sans décharger le modèle de l'application.
    raison = _application_tourne()
    if raison and not forcer:
        print(f"❌ Memoglyph tourne ({raison}) : arrêtez-le d'abord (./stop.sh), "
              "ou relancez avec --forcer en connaissance de cause.", file=sys.stderr)
        return 1
    try:
        return _ecl_extraire_tout(appliquer, journal)
    finally:
        # Rend la mémoire du modèle si cette commande a démarré le serveur ; un
        # serveur qui tournait déjà (l'application) n'est pas à elle.
        llm.unload()


def _ecl_extraire_tout(appliquer: bool, journal: Optional[Path]) -> int:
    restants, non_citables = ecl.a_extraire()
    perimes = ecl.aux_faits_perimes() & set(restants)
    print(f"Bibliothèque : {library.OUTPUT_DIR}")
    print("Extraction appliquée." if appliquer else "Simulation : rien n'est écrit.")
    print(f"Transcripts sans faits ECL : {len(restants) - len(perimes)}")
    if perimes:
        print(f"Transcripts aux faits périmés : {len(perimes)}")
    if non_citables:
        print(f"Sans réplique horodatée, ignorés : {len(non_citables)}")
    if not appliquer:
        for nom in restants:
            print(f"  - {nom}")
        if restants:
            print("Relancer avec --appliquer pour les extraire.")
        return 0
    if restants and not llm.is_available():
        print(f"❌ Modèle indisponible ({llm.FOURNISSEUR_DEFAUT}).", file=sys.stderr)
        return 1
    gardes = refuses = pannes = 0
    for nom in restants:
        entrees: list[dict] = []
        try:
            bilan = ecl.extraire(nom, journal=entrees if journal else None)
        except Exception as exc:  # noqa: BLE001 — un document en panne n'arrête pas les autres
            pannes += 1
            print(f"  ❌ {nom} : {exc}")
            continue
        finally:
            if journal:
                with journal.open("a", encoding="utf-8") as sortie:
                    for entree in entrees:
                        sortie.write(json.dumps({"document": nom, **entree}, ensure_ascii=False) + "\n")
        gardes += bilan["gardes"]
        refuses += bilan["refuses"]
        motifs = ", ".join(f"{m} {n}" for m, n in bilan["refus"].items() if n)
        print(f"  - {nom} : {bilan['gardes']} gardé(s), {bilan['refuses']} refusé(s)"
              + (f" ({motifs})" if motifs else "")
              + (f", {bilan['appels_en_echec']} appel(s) en échec" if bilan["appels_en_echec"] else ""))
    print(f"Total : {gardes} fait(s) gardé(s), {refuses} refusé(s), {pannes} document(s) en panne.")
    return 1 if pannes else 0


def _sensibilite_reunions(appliquer: bool, forcer: bool = False) -> int:
    raison = _application_tourne() if appliquer else None
    if raison and not forcer:
        print(f"❌ Memoglyph tourne ({raison}) : arrêtez-le d'abord (./stop.sh), "
              "ou relancez avec --forcer en connaissance de cause.", file=sys.stderr)
        return 1
    try:
        library.reindex()   # l'index dérivé suit les `.md` avant d'y lire les niveaux
        bilan = library.migrer_sensibilites(appliquer=appliquer)
    except (library.CorpusValidationError, library.RegistreSensibiliteIllisible) as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    print(f"Bibliothèque : {library.OUTPUT_DIR}")
    print("Inscription appliquée." if appliquer else "Simulation : rien n'est écrit.")
    print(f"Réunions déjà réglées : {bilan['deja_reglees']}")
    for (projet, meeting_id), niveau in bilan["inscrites"].items():
        print(f"  - projet {projet}, réunion {meeting_id} : {niveau}")
    if bilan["completees"]:
        print(f"Réglages au format précédent, complétés des documents couverts : {len(bilan['completees'])}")
    if appliquer:
        print(f"✅ {len(bilan['inscrites'])} réunion(s) inscrite(s) au registre.")
    elif bilan["inscrites"] or bilan["completees"]:
        print(f"{len(bilan['inscrites'])} réunion(s) à inscrire : relancer avec --appliquer.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="memoglyph", description=__doc__.splitlines()[0])
    commandes = parser.add_subparsers(dest="commande", required=True)
    reset = commandes.add_parser("reset-password", help="réinitialiser un mot de passe")
    reset.add_argument("utilisateur")
    reset.add_argument("--stdin", action="store_true",
                       help="lire le mot de passe sur l'entrée standard")
    attribution = commandes.add_parser(
        "attribuer-bibliotheque",
        help="attribuer à un compte les projets sans propriétaire (simulation par défaut)")
    attribution.add_argument("utilisateur")
    attribution.add_argument("--appliquer", action="store_true",
                             help="écrire l'attribution (sans lui : simulation)")
    extraction = commandes.add_parser(
        "ecl-extraire",
        help="extraire les faits ECL des transcripts qui n'en ont pas ou dont des faits sont "
             "périmés (simulation par défaut)")
    extraction.add_argument("--appliquer", action="store_true",
                            help="extraire et écrire les faits (sans lui : simulation)")
    extraction.add_argument("--journal", type=Path,
                            help="journal : chaque fait proposé, gardé ou refusé, en JSON par ligne")
    extraction.add_argument("--forcer", action="store_true",
                            help="tourner même si l'application tourne (à vos risques)")
    sensibilites = commandes.add_parser(
        "sensibilite-reunions",
        help="inscrire au registre des réunions le niveau le plus strict de leurs documents "
             "(simulation par défaut)")
    sensibilites.add_argument("--appliquer", action="store_true",
                              help="écrire le registre (sans lui : simulation)")
    sensibilites.add_argument("--forcer", action="store_true",
                              help="écrire même si l'application tourne (à vos risques)")
    serveur_mcp = commandes.add_parser(
        "mcp", help="serveur MCP en lecture seule, sur stdio, pour la portée de ce compte")
    serveur_mcp.add_argument("utilisateur")
    args = parser.parse_args(argv)

    if args.commande == "mcp":
        compte = auth.utilisateur_actif(args.utilisateur)
        if compte is None:
            print(f"❌ Aucun compte actif « {args.utilisateur} ».", file=sys.stderr)
            return 1
        from . import mcp  # noqa: PLC0415 — chargé pour cette seule commande
        mcp.servir(compte)
        return 0

    if args.commande == "sensibilite-reunions":
        return _sensibilite_reunions(args.appliquer, args.forcer)

    if args.commande == "ecl-extraire":
        return _ecl_extraire(args.appliquer, args.journal, args.forcer)

    if args.commande == "attribuer-bibliotheque":
        return _attribuer(args.utilisateur, args.appliquer)

    try:
        mot_de_passe = _lire_mot_de_passe(args.stdin)
        auth.reinitialiser_mot_de_passe(args.utilisateur, mot_de_passe)
    except auth.ErreurAuth as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    print(f"✅ Mot de passe de « {args.utilisateur} » remplacé ; ses sessions sont "
          "révoquées et son blocage levé.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
