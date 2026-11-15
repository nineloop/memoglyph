"""Les refus de l'API qui portent un code stable EN PLUS de leur phrase.

Un refus sans code est une `HTTPException` ordinaire. Un refus avec code garde le même statut, la même phrase (`detail`) et les
mêmes en-têtes (`Retry-After` sur un 429) : le corps ne gagne que la clé `code`, jamais un `detail` en dictionnaire. Le code
(`domaine.raison`) est la clé que l'interface traduit ; la phrase reste celle du serveur, que l'interface ne montre pas.
"""
from __future__ import annotations

from fastapi import HTTPException


class RefusAvecCode(HTTPException):
    """Un refus qui porte un code stable. `code` est nommé : le troisième argument de `HTTPException` est `headers`."""

    def __init__(self, status_code: int, detail: str, *, code: str | None = None,
                 headers: dict[str, str] | None = None) -> None:
        super().__init__(status_code, detail, headers=headers)
        self.code = code


def corps_du_refus(refus: RefusAvecCode) -> dict:
    """Le corps de la réponse : la phrase, et le code quand il y en a un."""
    corps: dict = {"detail": refus.detail}
    if refus.code:
        corps["code"] = refus.code
    return corps


def refus_d_auth(exc: Exception) -> RefusAvecCode:
    """Le 400 d'un refus de `auth` (`ErreurAuth`) : sa phrase, et son code quand il en porte un."""
    return RefusAvecCode(400, str(exc), code=getattr(exc, "code", None))
