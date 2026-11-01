"""Connexion SQLite commune aux index dérivés de Memoglyph."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from .config import ROOT

# --- Emplacement CANONIQUE de l'index dérivé ---------------------------------
#
# Nom volontairement GÉNÉRIQUE, indépendant du nom de l'application : une base
# nommée d'après le produit se scinde au premier renommage dès qu'un module
# oublie de suivre (le registre vocal dans un fichier, l'index dans un autre).
# Un nom neutre écarte cette classe de défaut.
#
# Cette base est l'INDEX DÉRIVÉ, reconstructible depuis les `.md` (source de
# vérité) et le sidecar `.voiceprints.json`. La perdre ne perd aucune donnée
# irréversible.
DB_PATH = ROOT / "index.db"

BUSY_TIMEOUT_MS = 5_000
JOURNAL_MODE = "wal"
_JOURNAL_LOCK = threading.Lock()


def connect(path: Path | str) -> sqlite3.Connection:
    """Ouvre SQLite avec une attente bornée et explicite en cas de contention.

    Le défaut implicite de CPython vaut actuellement cinq secondes, mais le
    contrat ne doit pas dépendre d'un défaut de bibliothèque ni diverger entre
    l'index texte, sqlite-vec et le registre vocal.
    """
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1_000)
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    # WAL permet à l'indexation de committer pendant qu'une recherche conserve
    # un snapshot de lecture. Le mode persiste dans le fichier ; le verrou évite
    # que deux premières connexions du même processus tentent la bascule ensemble.
    with _JOURNAL_LOCK:
        current = conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
        if current != JOURNAL_MODE:
            actual = conn.execute(f"PRAGMA journal_mode = {JOURNAL_MODE}").fetchone()[0]
            if actual.lower() != JOURNAL_MODE:
                conn.close()
                raise sqlite3.OperationalError(
                    f"SQLite refuse le journal {JOURNAL_MODE!r} (actuel : {actual!r})"
                )
    return conn
