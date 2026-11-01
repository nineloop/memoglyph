"""Résolveur de portée unique : la part de la bibliothèque qu'un compte voit.

Une portée est l'ensemble des projets dont le compte de la session est
propriétaire ; un administrateur y ajoute les projets sans propriétaire, ceux
d'avant les comptes, jusqu'à leur attribution. « Tous les projets » se lit donc
« tous mes projets ». Isolation logique : un seul index SQLite, filtré par ces
identités de projet.

Toute route qui lit ou modifie un document, un projet, la corbeille, les
doublons, la recherche, l'assistant, l'usage ou la destination d'une
transcription passe par ici. Hors de la portée : 404, jamais 403, et sans
recopier l'identifiant demandé. Un 403 confirmerait que l'objet existe chez un
autre compte.

Partage v1 : la LECTURE s'étend à ce qui est partagé avec le compte, par la
portée `shared` (« Partagés avec moi ») ou l'identité d'un projet partagé
(`lecture`). `all` reste « tous mes projets » (Q14). Toute écriture passe par
`resoudre`, qui ne connaît que les projets du compte : un invité ne modifie rien.

Une portée se résout sans rien écrire : elle rend les projets EXISTANTS du compte.
Son « Non classé » naît à la création du compte et sous les routes qui écrivent
(`creer_non_classe`, `projet(..., ecriture=True)`), jamais sous une lecture.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Request

from core import library
from core import projects as project_store

from . import auth, partages

TOUS = "all"
PARTAGES = "shared"
# Identité qui ne désigne aucun projet (une identité de projet est un ULID, sans tiret
# ni minuscule) : la portée d'un « Non classé » qui n'existe pas encore, pour une
# fonction qui ne prend qu'une identité et lit la chaîne vide comme « tout ».
AUCUN_PROJET = "aucun-projet"


def utilisateur(request: Request) -> auth.Utilisateur:
    """Le compte de la session, posé par le middleware de `api.main`."""
    compte = getattr(request.state, "utilisateur", None)
    if compte is None:
        raise HTTPException(401, "Connexion requise.")
    return compte


def _manifestes() -> list[tuple[Path, project_store.Project]]:
    try:
        return project_store.all_projects(library.OUTPUT_DIR)
    except project_store.ProjectManifestError as exc:
        raise HTTPException(409, str(exc)) from exc


def visible(compte: auth.Utilisateur, projet: project_store.Project) -> bool:
    if projet.owner is None:
        return compte.admin
    return project_store.meme_compte(projet.owner, compte.identifiant)


def projets_visibles(compte: auth.Utilisateur) -> list[tuple[Path, project_store.Project]]:
    return [(d, p) for d, p in _manifestes() if visible(compte, p)]


def non_classe(compte: auth.Utilisateur) -> Optional[project_store.Project]:
    """Le « Non classé » du compte s'il existe, sinon None. N'écrit rien."""
    try:
        trouve = project_store.find_personal_inbox(library.OUTPUT_DIR, compte.identifiant)
    except project_store.ProjectManifestError as exc:
        raise HTTPException(409, str(exc)) from exc
    return None if trouve is None else trouve[1]


def creer_non_classe(identifiant: str) -> project_store.Project:
    """Le « Non classé » du compte, créé s'il manque : création du compte et routes qui
    écrivent seulement. Sur une bibliothèque vierge, c'est ici que le « Non classé »
    de la racine est attribué (`project_store.personal_inbox`)."""
    try:
        return project_store.personal_inbox(library.OUTPUT_DIR, identifiant)[1]
    except project_store.ProjectManifestError as exc:
        raise HTTPException(409, str(exc)) from exc


def tous_mes_projets(compte: auth.Utilisateur) -> frozenset[str]:
    """Les projets existants du compte ; sans « Non classé », la portée est sans lui."""
    return frozenset(p.id for _d, p in projets_visibles(compte))


def projet(compte: auth.Utilisateur, project_id: str, *,
           ecriture: bool = False) -> tuple[Path, project_store.Project]:
    """Un projet de la portée du compte, par son identité ; 404 sinon.

    Vide = le « Non classé » du compte : `ecriture` le crée s'il manque (une
    destination) ; sans elle, un « Non classé » absent est introuvable, comme tout
    projet qui n'existe pas. `all` n'est pas un projet.
    """
    project_id = (project_id or "").strip()
    if not project_id:
        inbox = creer_non_classe(compte.identifiant) if ecriture else non_classe(compte)
        if inbox is None:
            raise HTTPException(404, "Projet introuvable")
        project_id = inbox.id
    for directory, candidat in projets_visibles(compte):
        if candidat.id == project_id:
            return directory, candidat
    raise HTTPException(404, "Projet introuvable")


def resoudre(compte: auth.Utilisateur, view_scope: str = "") -> frozenset[str]:
    """La portée de consultation : un projet du compte, ou `all` = tous les siens.

    Vide = le « Non classé » du compte ; absent, une portée vide (aucun document),
    jamais une erreur ni une création.
    """
    scope = (view_scope or "").strip()
    if scope == TOUS:
        return tous_mes_projets(compte)
    if not scope:
        inbox = non_classe(compte)
        return frozenset() if inbox is None else frozenset({inbox.id})
    return frozenset({projet(compte, scope)[1].id})


def _proprietaires_actifs() -> set[str]:
    return {c["identifiant"].casefold() for c in auth.lister_comptes() if c["actif"]}


def projets_de(identifiant: str) -> frozenset[str]:
    """Les projets dont `identifiant` est propriétaire (jamais ceux sans propriétaire)."""
    return frozenset(
        p.id for _d, p in _manifestes() if project_store.meme_compte(p.owner, identifiant)
    )


def partages_actifs(compte: auth.Utilisateur) -> list[dict]:
    """Les partages reçus par le compte dont le propriétaire est actif et détient
    encore la cible : un retrait, un compte désactivé ou un projet attribué à un
    autre ferment l'accès dès la requête suivante."""
    actifs = _proprietaires_actifs()
    retenus = []
    for partage in partages.recus(compte.identifiant):
        if partage["proprietaire"].casefold() not in actifs:
            continue
        if partage["type"] == "projet" and partage["cible"] not in projets_de(partage["proprietaire"]):
            continue
        retenus.append(partage)
    return retenus


def portee_partagee(compte: auth.Utilisateur, projet_id: str = "") -> project_store.PorteeLecture:
    """Les documents partagés avec le compte.

    Un `projet_id` la réduit à ce qui en vient : un projet partagé, ou le projet
    d'origine d'une réunion partagée seule. L'interface ouvre un document avec
    la portée de son projet (`porteeDocument`) ; sans cela, une réunion partagée
    seule ne s'ouvrirait pas.
    """
    recus = partages_actifs(compte)
    projets = frozenset(p["cible"] for p in recus if p["type"] == "projet")
    reunions = frozenset(p["cible"] for p in recus if p["type"] == "reunion")
    sources: frozenset[str] = frozenset()
    for proprietaire in {p["proprietaire"] for p in recus if p["type"] == "reunion"}:
        sources |= projets_de(proprietaire)
    proprietaires: frozenset[str] = frozenset()
    for proprietaire in {p["proprietaire"] for p in recus}:
        proprietaires |= projets_de(proprietaire)
    if projet_id:
        if projet_id not in library.documents_lisibles(projets, reunions, sources).sources:
            raise HTTPException(404, "Projet introuvable")
        projets, sources = projets & {projet_id}, frozenset({projet_id})
    lisibles = library.documents_lisibles(projets, reunions, sources)
    return dataclasses.replace(lisibles, proprietaires=proprietaires)


def lecture(compte: auth.Utilisateur, view_scope: str = "") -> project_store.Portee:
    """La portée de LECTURE : celle du compte, ou ce qui lui est partagé."""
    scope = (view_scope or "").strip()
    if scope == PARTAGES:
        return portee_partagee(compte)
    if scope and scope != TOUS and scope not in {p.id for _d, p in projets_visibles(compte)}:
        return portee_partagee(compte, scope)
    return resoudre(compte, view_scope)
