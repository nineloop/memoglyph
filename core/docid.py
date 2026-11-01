"""Identifiant STABLE d'un document, indépendant de son nom de fichier.

## Pourquoi

Aujourd'hui le nom de fichier EST la clé, dans cinq magasins : `transcripts.name`,
`voice_members.name`, `chunks.name`, le `source_transcript` du front-matter, et
les empreintes du fichier annexe. Renommer un `.md` orpheline donc ses chunks
sémantiques, son rattachement aux voix, ses empreintes, et casse le lien de ses
comptes-rendus — sans que rien ne le signale.

## Pourquoi PAS dans SQLite

Un `INTEGER PRIMARY KEY AUTOINCREMENT` serait un piège. L'invariant du projet est
que le `.md` est la source de vérité et que **la base est dérivée et
reconstructible** : `reindex()` réécrit les lignes, et un identifiant né là
disparaîtrait avec elles — en emportant les rattachements qui le référencent.

L'identifiant vit donc dans le FRONT-MATTER, écrit une fois à la création, et la
base ne fait que le transporter.

## Format

Un ULID : 26 caractères en base32 Crockford, dont les 48 premiers bits sont
l'horodatage en millisecondes. Triable par date de création, sans séparateur,
sans caractère ambigu (ni I, ni L, ni O, ni U), et deux fois plus court qu'un
UUID écrit. Généré ici plutôt qu'importé : 30 lignes contre une dépendance.
"""

from __future__ import annotations

import os
import re
import time

# Base32 Crockford : I, L, O et U sont exclus pour éviter toute confusion à la
# lecture ou à la saisie (1/I/L, 0/O).
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def _base32(valeur: int, longueur: int) -> str:
    out = []
    for _ in range(longueur):
        valeur, reste = divmod(valeur, 32)
        out.append(_ALPHABET[reste])
    return "".join(reversed(out))


def nouveau(*, horodatage_ms: int | None = None) -> str:
    """Un ULID : 10 caractères d'horodatage puis 16 d'aléa."""
    ms = horodatage_ms if horodatage_ms is not None else int(time.time() * 1000)
    alea = int.from_bytes(os.urandom(10), "big")
    return _base32(ms, 10) + _base32(alea, 16)


def est_valide(valeur: object) -> bool:
    """Vrai si la valeur a la forme d'un ULID. Sert à ne pas écraser un id existant."""
    return isinstance(valeur, str) and bool(_ID_RE.match(valeur))


def horodatage_ms(identifiant: str) -> int:
    """Millisecondes de création encodées dans les 10 premiers caractères."""
    ms = 0
    for c in identifiant[:10]:
        ms = ms * 32 + _ALPHABET.index(c)
    return ms
