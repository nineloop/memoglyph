"""Normalisation d'horodatage — provider-agnostique, déterministe.

Certains moteurs (mesuré : Mistral) émettent des horodatages en `MM:SS` là où le
transcript porte le canonique `HH:MM:SS`. Sans réconciliation, l'unité échoue la
validation timestamp (appartenance EXACTE) et son contenu est écarté — matière
produite puis perdue au contrôle.

Ce module NE FAIT QUE reformater structurellement (nombre de parties → padding)
puis EXIGE l'appartenance exacte au jeu d'horodatages du transcript. Il n'invente
jamais un horodatage, n'appose aucun appariement flou/temporel, et ne branche sur
AUCUN fournisseur : c'est une couche commune, utile aux deux moteurs (elle est
inerte quand l'entrée est déjà canonique).
"""
from __future__ import annotations

import re
from typing import Optional

# 2 parties = M:SS (minutes:secondes) ; 3 parties = H:MM:SS. Rien d'autre.
_TS_RE = re.compile(r"^(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?$")


def canonicalize(raw: object) -> Optional[str]:
    """Reformate un horodatage vers `HH:MM:SS`, ou `None` si malformé.

    - `MM:SS`   → `00:MM:SS`   (2 parties = minutes:secondes)
    - `H:MM:SS` → `0H:MM:SS`   (3 parties = heures:minutes:secondes)
    - déjà canonique → inchangé
    Rejette (None) : non-chaîne, parties non numériques, minutes/secondes ≥ 60,
    heures ≥ 100, formats à 1 ou ≥ 4 parties, texte parasite. NE VALIDE PAS
    l'existence — voir `resolve`. N'invente rien : purement structurel.
    """
    if not isinstance(raw, str):
        return None
    m = _TS_RE.match(raw.strip())
    if not m:
        return None
    a, b, c = m.group(1), m.group(2), m.group(3)
    if c is None:
        hh, mm, ss = 0, int(a), int(b)      # 2 parties = minutes:secondes
    else:
        hh, mm, ss = int(a), int(b), int(c)  # 3 parties = heures:minutes:secondes
    if mm >= 60 or ss >= 60 or hh >= 100:
        return None
    return f"{hh:02d}:{mm:02d}:{ss:02d}"


def resolve(raw: object, valid_ts: set[str]) -> Optional[str]:
    """Horodatage canonique EXISTANT dans le transcript, ou `None`.

    Normalise puis exige l'appartenance EXACTE à `valid_ts`. Ne renvoie donc
    jamais un horodatage absent du transcript (aucune invention, aucun voisinage,
    aucun appariement flou). Idempotent sur une entrée déjà canonique et présente.
    """
    cand = canonicalize(raw)
    if cand is not None and cand in valid_ts:
        return cand
    return None
