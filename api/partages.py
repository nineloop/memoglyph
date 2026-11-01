"""Partages v1 : un projet ou une réunion, en lecture seule, avec un compte existant.

Ni lien public, ni partage avec soi-même. Un partage est une décision humaine :
il vit dans `auth.db`, à côté des comptes, et jamais dans l'index dérivé, qui
se reconstruit. Ce module ne fait que tenir le registre ; ce qu'un partage
ouvre en lecture se calcule dans `api.portee`, et ce qui peut se partager se
vérifie dans `api.main`.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from typing import Literal

from core import docid

from . import auth

TypePartage = Literal["projet", "reunion"]
TYPES: tuple[str, ...] = ("projet", "reunion")


def _connexion():
    con = auth._connexion()
    con.execute("""
        CREATE TABLE IF NOT EXISTS partages (
            id TEXT PRIMARY KEY,
            proprietaire TEXT NOT NULL COLLATE NOCASE,
            invite TEXT NOT NULL COLLATE NOCASE,
            type TEXT NOT NULL CHECK (type IN ('projet', 'reunion')),
            cible TEXT NOT NULL,
            cree_le TEXT NOT NULL,
            UNIQUE (invite, type, cible)
        )
    """)
    return con


def _ligne(ligne) -> dict:
    return {k: ligne[k] for k in ("id", "proprietaire", "invite", "type", "cible", "cree_le")}


def partager(proprietaire: str, invite: str, type_: TypePartage, cible: str) -> dict:
    """Enregistre le partage ; le même partage demandé deux fois est rendu tel quel."""
    if type_ not in TYPES:
        raise auth.ErreurAuth(f"Type de partage inconnu : {type_!r}")
    with closing(_connexion()) as con, con:
        existant = con.execute(
            "SELECT * FROM partages WHERE invite = ? AND type = ? AND cible = ?",
            (invite, type_, cible),
        ).fetchone()
        if existant is not None:
            return _ligne(existant)
        ligne = {
            "id": docid.nouveau(), "proprietaire": proprietaire, "invite": invite,
            "type": type_, "cible": cible,
            "cree_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        con.execute(
            "INSERT INTO partages (id, proprietaire, invite, type, cible, cree_le) "
            "VALUES (:id, :proprietaire, :invite, :type, :cible, :cree_le)", ligne,
        )
        return ligne


def donnes(proprietaire: str) -> list[dict]:
    with closing(_connexion()) as con:
        return [_ligne(r) for r in con.execute(
            "SELECT * FROM partages WHERE proprietaire = ? ORDER BY cree_le, id", (proprietaire,))]


def recus(invite: str) -> list[dict]:
    with closing(_connexion()) as con:
        return [_ligne(r) for r in con.execute(
            "SELECT * FROM partages WHERE invite = ? ORDER BY cree_le, id", (invite,))]


def retirer(proprietaire: str, partage_id: str) -> bool:
    """Retire un partage de `proprietaire` ; faux s'il n'existe pas ou n'est pas à lui."""
    with closing(_connexion()) as con, con:
        return con.execute(
            "DELETE FROM partages WHERE id = ? AND proprietaire = ?", (partage_id, proprietaire),
        ).rowcount > 0
