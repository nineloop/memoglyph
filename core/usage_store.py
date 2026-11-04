"""Magasin DURABLE de télémétrie d'usage IA — `usage.db` (FinOps).

## Pourquoi un fichier séparé, jamais `index.db`

`index.db` est l'INDEX DÉRIVÉ, reconstruit depuis les `.md` par `library.reindex()`
qui réécrit et supprime librement ses lignes (voir `core/sqlite_db.py`, `core/docid.py`).
La télémétrie est l'exact inverse : un journal d'ÉVÉNEMENTS qui ont eu lieu et qu'AUCUN
document ne permet de reconstruire. Elle vit donc dans son PROPRE fichier durable,
`usage.db`, qu'aucune reconstruction d'index ne touche. Fichier et connexion distincts :
un verrou WAL sur `index.db` ne peut jamais bloquer une écriture de télémétrie, ni l'inverse.

## Modèle

- `execution`        — une opération IA logique (report | stt | …). Porte un coût DÉRIVÉ
                        (cache d'audit) et, pour la STT, une quantité `audio_seconds`.
- `model_call`       — l'unité ATOMIQUE : un appel fournisseur/modèle. Porte un coût DÉRIVÉ
                        + le `pricing_snapshot_id` utilisé (reproductibilité).
- `pricing_snapshot` — barème IMMUABLE et VERSIONNÉ. Vide par défaut : sans barème, le coût
                        reste NULL (jamais un faux zéro).

## Coût (phase 1A)

Le coût n'est jamais une donnée première : `coût = f(usage mesuré, barème)` (voir
`core/usage_cost.py`, pur). On stocke les FAITS d'usage (jamais réécrits), le
`pricing_snapshot_id` et le coût dérivé comme CACHE reproductible. Un barème étant immuable,
le coût d'un appel se recalcule à l'identique tant qu'on garde son `pricing_snapshot_id` ;
`reprice()` permet un recalcul rétroactif (nouveau barème → nouveaux coûts, faits inchangés).
Coût nuage `cloud_derived` (dérivé d'un prix catalogue, PAS réconcilié sur facture) et local
`local_imputed` ne se mélangent jamais ; les devises non plus (aucune conversion de change).

## Contrat de fiabilité

Tout ce module est BEST-EFFORT. Aucune écriture de la génération ne lève : base indisponible,
schéma périmé, barème malformé sont avalés. Le calcul de coût ne peut ni casser ni ralentir
la génération STT/CR ; barème absent ⇒ faits persistés, coût NULL.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import sqlite_db, usage_cost, docid
from .config import ROOT, getenv_compat

_DEFAULT_USAGE_DB = ROOT / "usage.db"

SCHEMA_VERSION = 7
_COMPONENT = "usage_schema"

# Provenance d'un tarif — d'où vient le NOMBRE, pour qu'un audit le distingue sans deviner.
# `official_published` : grille publiée d'un fournisseur (source_url + retrieved_at exigés) ;
# `configured_manual`  : tarif saisi à la main par l'exploitant (pas de source officielle) ;
# `local_benchmark`    : tarif IMPUTÉ local, dérivé d'un relevé/équivalent hébergé, jamais un
#                        défaut caché du produit. Aucun autre nom n'est accepté.
RATE_SOURCES = ("official_published", "configured_manual", "local_benchmark")


def usage_db_path() -> Path:
    """Chemin du magasin. `MEMOGLYPH_USAGE_DB` l'emporte (tests, emplacements alternatifs)."""
    override = getenv_compat("USAGE_DB")
    return Path(override) if override else _DEFAULT_USAGE_DB


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- DDL : additive, idempotente, versionnée (motif de la maison) -------------
_DDL = [
    """
    CREATE TABLE IF NOT EXISTS execution (
        execution_id         TEXT PRIMARY KEY,
        kind                 TEXT NOT NULL,
        document_id          TEXT,
        meeting_id           TEXT,
        project_id           TEXT,
        produced_artifact_id TEXT,
        provider_primary     TEXT,
        model_primary        TEXT,
        started_at           TEXT NOT NULL,
        ended_at             TEXT,
        wall_clock_s         REAL,
        status               TEXT,
        n_calls              INTEGER DEFAULT 0,
        in_tok               INTEGER,
        out_tok              INTEGER,
        audio_seconds        REAL,                -- quantité STT mesurée (unité non-jeton)
        cost                 REAL,                -- DÉRIVÉ (cache d'audit), NULL si inconnu
        cost_currency        TEXT,
        cost_basis           TEXT,                -- cloud_derived | local_imputed | mixed
        pricing_snapshot_id  TEXT,                -- barème utilisé (reproductibilité)
        cost_computed_at     TEXT,
        -- 1 : un document ultra-confidentiel est parti vers le cloud sous l'autorisation
        -- de son propriétaire (`sensitivity.tracer`) ; 0 sinon, y compris avant la v7.
        envoi_ultra_autorise INTEGER NOT NULL DEFAULT 0,
        schema_version       INTEGER NOT NULL,
        created_at           TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS model_call (
        call_id       TEXT PRIMARY KEY,
        execution_id  TEXT NOT NULL,
        seq           INTEGER NOT NULL,
        stage         TEXT,
        provider      TEXT,
        model         TEXT,
        model_servi   TEXT,                       -- le modèle qui a RÉPONDU (NULL : non dit) ; `model` reste le demandé
        error_code    TEXT,                       -- le CODE seul d'un appel refusé ou inutilisable (jamais le corps)
        started_at    TEXT,
        latency_s     REAL,
        status        TEXT,
        usage_source  TEXT,
        in_tok        INTEGER,
        out_tok       INTEGER,
        cached_tok    INTEGER,
        reasoning_tok INTEGER,
        cost                REAL,                 -- DÉRIVÉ (cache d'audit), NULL si inconnu
        cost_currency       TEXT,
        cost_basis          TEXT,                 -- cloud_derived | local_imputed
        pricing_snapshot_id TEXT,                 -- barème utilisé (reproductibilité)
        cost_computed_at    TEXT,
        created_at    TEXT NOT NULL
    )
    """,
    # Barème IMMUABLE, versionné. Vide par défaut : aucun coût fabriqué sans barème saisi.
    # `unit` = 'token' (tarifs input/output/cached par 1e6 jetons) ou une unité explicite
    # (ex. 'audio_second', tarif dans `unit_rate`). Aucun fournisseur n'est câblé au schéma.
    """
    CREATE TABLE IF NOT EXISTS pricing_snapshot (
        pricing_snapshot_id TEXT PRIMARY KEY,
        provider            TEXT NOT NULL,
        model               TEXT NOT NULL,
        kind                TEXT NOT NULL,        -- cloud_billed | local_imputed
        unit                TEXT NOT NULL DEFAULT 'token',
        version             INTEGER NOT NULL DEFAULT 1,
        input_rate          REAL,                 -- par 1e6 jetons (unit='token')
        output_rate         REAL,                 -- par 1e6 jetons (inclut le raisonnement)
        cached_rate         REAL,                 -- par 1e6 jetons (défaut : input_rate)
        unit_rate           REAL,                 -- par unité non-jeton (unit!='token')
        currency            TEXT,
        local_rate_unit     TEXT,
        effective_from      TEXT,
        source              TEXT,                 -- note humaine libre
        rate_source         TEXT,                 -- official_published | configured_manual | local_benchmark
        source_url          TEXT,                 -- URL officielle (tarif publié)
        retrieved_at        TEXT,                 -- date de relevé (tarif publié)
        created_at          TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS usage_meta (
        component  TEXT PRIMARY KEY,
        version    INTEGER NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    # Mapping EXPLICITE et AUDITÉ modèle observé -> identité de TARIFICATION. Le seul moyen de
    # tarifer un appel dont le `model` enregistré (ex. alias `mistral-small-latest`) n'est pas
    # lui-même tarifé. Correspondance EXACTE (provider, observed_model) : aucun aliasing flou,
    # aucun autre modèle n'hérite. Immuable/versionné/provenancé, comme un barème. Peuplé
    # uniquement par un import EXPLICITE ; une usage.db neuve n'en contient aucun.
    """
    CREATE TABLE IF NOT EXISTS model_alias (
        alias_id       TEXT PRIMARY KEY,
        provider       TEXT NOT NULL,
        observed_model TEXT NOT NULL,      -- ce que le moteur a enregistré (ex. mistral-small-latest)
        pricing_model  TEXT NOT NULL,      -- identité de tarification (ex. mistral-small-2603)
        version        INTEGER NOT NULL DEFAULT 1,
        effective_from TEXT,
        source         TEXT,               -- provenance : décision produit / note
        source_url     TEXT,
        retrieved_at   TEXT,
        created_at     TEXT NOT NULL
    )
    """,
    # Agrégat DÉRIVÉ et RECONSTRUCTIBLE que lira le futur tableau de bord Usage (phase 3A).
    # `execution`/`model_call` restent la SOURCE DE VÉRITÉ ; cette table se supprime et se
    # rebâtit intégralement depuis les faits bruts à tout instant. Grain (clé) :
    #   jour · projet · fournisseur · modèle · étape · genre d'exécution · devise · base de coût
    # `project_id` est un FAIT d'exécution (`execution.project_id`, jamais déduit d'un nom de
    # fichier) ; NULL = exécution héritée/hors projet, gardé EXPLICITE (jamais remappé). Deux
    # projets ne se mélangent jamais (le projet est dans la clé).
    # `stage` porte des sentinelles entre parenthèses hors des vraies étapes d'appel :
    #   'stt'          — une exécution STT (audio, zéro appel) : audio + coût audio + 1 exéc ;
    #   '(execution)'  — le rollup d'UNE exécution à appels (report/profile) : 1 exéc + wall,
    #                    coût NULL (le coût vit déjà sur les lignes d'étape d'appel) ;
    #   '(unspecified)'— un appel sans étape nommée.
    # Ainsi CHAQUE exécution émet exactement UNE ligne « unité d'exécution » (n_executions=1,
    # wall_clock) : jamais de double compte. Le coût NULL reste NULL (jamais un faux zéro) ;
    # devises et bases ne se mélangent jamais (elles sont dans la clé).
    """
    CREATE TABLE IF NOT EXISTS usage_daily (
        day           TEXT NOT NULL,        -- 'YYYY-MM-DD' UTC (date de started_at de l'exécution)
        project_id    TEXT,                 -- fait d'exécution ; NULL = hérité/hors projet (explicite)
        provider      TEXT,
        model         TEXT,
        stage         TEXT NOT NULL,        -- étape réelle | 'stt' | '(execution)' | '(unspecified)'
        exec_kind     TEXT,                 -- stt | report | profile | …
        currency      TEXT,
        cost_basis    TEXT,                 -- cloud_derived | local_imputed | mixed | NULL (non tarifé)
        n_executions  INTEGER NOT NULL DEFAULT 0,
        n_model_calls INTEGER NOT NULL DEFAULT 0,
        in_tok        INTEGER NOT NULL DEFAULT 0,
        out_tok       INTEGER NOT NULL DEFAULT 0,
        cached_tok    INTEGER NOT NULL DEFAULT 0,
        reasoning_tok INTEGER NOT NULL DEFAULT 0,
        audio_seconds REAL    NOT NULL DEFAULT 0,
        wall_clock_s  REAL    NOT NULL DEFAULT 0,
        cost          REAL,                 -- NULL = seau non tarifé (jamais un faux zéro)
        n_units       INTEGER NOT NULL DEFAULT 0,
        rebuilt_at    TEXT NOT NULL
    )
    """,
]

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_model_call_exec ON model_call(execution_id)",
    "CREATE INDEX IF NOT EXISTS idx_execution_started ON execution(started_at)",
    "CREATE INDEX IF NOT EXISTS idx_execution_kind ON execution(kind, started_at)",
    "CREATE INDEX IF NOT EXISTS idx_execution_document ON execution(document_id)",
    # Liste d'exécutions bornée par PROJET du drill-down (phase 3B) : indexée sur (projet, temps).
    "CREATE INDEX IF NOT EXISTS idx_execution_project ON execution(project_id, started_at)",
    "CREATE INDEX IF NOT EXISTS idx_pricing_lookup "
    "ON pricing_snapshot(provider, model, unit, effective_from)",
    "CREATE INDEX IF NOT EXISTS idx_alias_lookup "
    "ON model_alias(provider, observed_model, effective_from)",
    # usage_daily : le tableau de bord lit CET agrégat, jamais un scan de model_call.
    # Clé de grain (unicité logique garantie par le GROUP BY du rebuild par partition-jour) +
    # index ciblés pour range-jour, projet, fournisseur/modèle/étape et genre d'exécution.
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_daily_grain ON usage_daily("
    "day, project_id, provider, model, stage, exec_kind, currency, cost_basis)",
    "CREATE INDEX IF NOT EXISTS idx_usage_daily_day ON usage_daily(day)",
    "CREATE INDEX IF NOT EXISTS idx_usage_daily_project ON usage_daily(project_id, day)",
    "CREATE INDEX IF NOT EXISTS idx_usage_daily_kind ON usage_daily(exec_kind, day)",
    "CREATE INDEX IF NOT EXISTS idx_usage_daily_provider ON usage_daily(provider, model)",
    "CREATE INDEX IF NOT EXISTS idx_usage_daily_stage ON usage_daily(stage, day)",
]

# Colonnes ajoutées après coup sur une base existante (motif `PRAGMA table_info` de la
# maison). Sur une base neuve, le `CREATE` ci-dessus les porte déjà ; ici on rattrape une
# base phase-0. Purement additif, jamais de suppression.
_ADDED_COLUMNS = {
    "execution": [
        ("audio_seconds", "REAL"), ("cost", "REAL"), ("cost_currency", "TEXT"),
        ("cost_basis", "TEXT"), ("pricing_snapshot_id", "TEXT"), ("cost_computed_at", "TEXT"),
        # v7 : trace de l'envoi ultra-confidentiel autorisé (une base existante lit 0).
        ("envoi_ultra_autorise", "INTEGER NOT NULL DEFAULT 0"),
    ],
    "model_call": [
        ("cost", "REAL"), ("cost_currency", "TEXT"), ("cost_basis", "TEXT"),
        ("pricing_snapshot_id", "TEXT"), ("cost_computed_at", "TEXT"),
        ("model_servi", "TEXT"), ("error_code", "TEXT"),
    ],
    "pricing_snapshot": [
        ("unit", "TEXT NOT NULL DEFAULT 'token'"), ("version", "INTEGER NOT NULL DEFAULT 1"),
        ("unit_rate", "REAL"), ("rate_source", "TEXT"), ("source_url", "TEXT"),
        ("retrieved_at", "TEXT"),
    ],
    # `project_id` ajouté au grain usage_daily en v6 (agrégat DÉRIVÉ : rebâti ensuite).
    "usage_daily": [("project_id", "TEXT")],
}


def _ensure_columns(conn: sqlite3.Connection, table: str, columns) -> None:
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    for name, decl in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """DDL idempotente + garde de version avant (motif `library_meta`)."""
    row = None
    try:
        row = conn.execute(
            "SELECT version FROM usage_meta WHERE component = ?", (_COMPONENT,)
        ).fetchone()
    except sqlite3.OperationalError:
        pass
    if row is not None and int(row[0]) > SCHEMA_VERSION:
        raise sqlite3.DatabaseError(
            f"usage.db version {row[0]} > code {SCHEMA_VERSION} : refus d'écrire."
        )
    for ddl in _DDL:
        conn.execute(ddl)
    for table, cols in _ADDED_COLUMNS.items():
        _ensure_columns(conn, table, cols)
    # v6 : le grain usage_daily a gagné `project_id`. Une base < 6 porte un index de grain
    # SANS projet, qui refuserait à tort deux projets ne différant que par `project_id`. On le
    # supprime une fois (l'agrégat DÉRIVÉ se rebâtit ensuite) avant de recréer le bon index.
    if row is not None and int(row[0]) < 6:
        conn.execute("DROP INDEX IF EXISTS idx_usage_daily_grain")
        conn.execute("DELETE FROM usage_daily")
    for idx in _INDEXES:
        conn.execute(idx)
    conn.execute(
        "INSERT INTO usage_meta(component, version, updated_at) VALUES(?,?,?) "
        "ON CONFLICT(component) DO UPDATE SET version=excluded.version, "
        "updated_at=excluded.updated_at",
        (_COMPONENT, SCHEMA_VERSION, _now_iso()),
    )


def connect(path: Optional[Path | str] = None) -> sqlite3.Connection:
    """Ouvre `usage.db` (WAL + busy_timeout via `sqlite_db.connect`) et assure le schéma."""
    p = Path(path) if path is not None else usage_db_path()
    conn = sqlite_db.connect(p)
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    return conn


# ============================ BARÈME (immuable, versionné) ====================
def insert_pricing_snapshot(
    conn: sqlite3.Connection,
    *,
    provider: str,
    model: str,
    kind: str,
    currency: str,
    unit: str = usage_cost.TOKEN_UNIT,
    input_rate: Optional[float] = None,
    output_rate: Optional[float] = None,
    cached_rate: Optional[float] = None,
    unit_rate: Optional[float] = None,
    effective_from: Optional[str] = None,
    source: Optional[str] = None,
    local_rate_unit: Optional[str] = None,
    rate_source: Optional[str] = None,
    source_url: Optional[str] = None,
    retrieved_at: Optional[str] = None,
) -> str:
    """Insère un barème IMMUABLE et rend son id. Ne met JAMAIS à jour une ligne existante.

    La version est monotone par (provider, model, unit). `effective_from` ordonne les barèmes
    dans le temps ; ABSENT (NULL), le barème s'applique « depuis toujours » (jusqu'à ce qu'un
    barème DATÉ le supplante pour les appels postérieurs à sa date).

    PROVENANCE (auditabilité) : `rate_source` classe l'origine du tarif (voir `RATE_SOURCES`).
    Un tarif `official_published` EXIGE `source_url` + `retrieved_at` — on ne consigne pas un
    tarif « officiel » sans dire d'où ni de quand il vient. `source` reste une note libre.

    Les tarifs de jetons sont PAR MILLION de jetons ; `unit_rate` s'applique à une unité
    non-jeton. Aucun coût n'est fabriqué ici : c'est une donnée de barème, pas un calcul.
    """
    if kind not in ("cloud_billed", "local_imputed"):
        raise ValueError(f"kind inconnu : {kind!r}")
    if rate_source is not None and rate_source not in RATE_SOURCES:
        raise ValueError(f"rate_source inconnu : {rate_source!r} (attendu {RATE_SOURCES})")
    if rate_source == "official_published" and not (source_url and retrieved_at):
        raise ValueError("official_published exige source_url ET retrieved_at")
    prev = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM pricing_snapshot "
        "WHERE provider=? AND model=? AND unit=?",
        (provider, model, unit),
    ).fetchone()[0]
    snap_id = docid.nouveau()
    conn.execute(
        "INSERT INTO pricing_snapshot ("
        "pricing_snapshot_id, provider, model, kind, unit, version, input_rate, output_rate, "
        "cached_rate, unit_rate, currency, local_rate_unit, effective_from, source, "
        "rate_source, source_url, retrieved_at, created_at"
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (snap_id, provider, model, kind, unit, int(prev) + 1, input_rate, output_rate,
         cached_rate, unit_rate, currency, local_rate_unit,
         effective_from, source, rate_source, source_url, retrieved_at, _now_iso()),
    )
    return snap_id


# Champs comparés pour l'IDEMPOTENCE d'un import (deux tarifs « identiques » ne créent pas
# une nouvelle version). On NE compare PAS `version`/`created_at`/`effective_from`/`source`.
_IMPORT_MATCH = ("kind", "unit", "input_rate", "output_rate", "cached_rate", "unit_rate",
                 "currency", "rate_source", "source_url", "retrieved_at")


def import_pricing(conn: sqlite3.Connection, entries) -> dict:
    """Importe des barèmes de façon EXPLICITE, IDEMPOTENTE et provenancée. Jamais d'écrasement.

    Chaque `entry` est un dict (provider, model, kind, currency, unit?, input_rate?, output_rate?,
    cached_rate?, unit_rate?, rate_source, source_url?, retrieved_at?, source?). Pour chaque :
    - si le DERNIER barème (provider, model, unit) est IDENTIQUE sur `_IMPORT_MATCH` → ignoré
      (aucune nouvelle ligne) : ré-importer le même fichier ne fait rien ;
    - sinon → INSERT d'une NOUVELLE version (l'ancienne reste, immuable).

    N'écrit RIEN d'autre : appeler cette fonction est le SEUL moyen de peupler un barème. Une
    `usage.db` neuve reste vide tant que personne ne l'invoque. Renvoie un compte {imported, skipped}.
    """
    out = {"imported": 0, "skipped": 0}
    for e in entries:
        unit = e.get("unit", usage_cost.TOKEN_UNIT)
        latest = conn.execute(
            "SELECT * FROM pricing_snapshot WHERE provider=? AND model=? AND unit=? "
            "ORDER BY version DESC LIMIT 1",
            (e["provider"], e["model"], unit),
        ).fetchone()
        if latest is not None:
            def _entry_val(k):
                return unit if k == "unit" else e.get(k)
            same = all(latest[k] == _entry_val(k) for k in _IMPORT_MATCH)
            if same:
                out["skipped"] += 1
                continue
        insert_pricing_snapshot(
            conn, provider=e["provider"], model=e["model"], kind=e["kind"],
            currency=e["currency"], unit=unit,
            input_rate=e.get("input_rate"), output_rate=e.get("output_rate"),
            cached_rate=e.get("cached_rate"), unit_rate=e.get("unit_rate"),
            effective_from=e.get("effective_from"), source=e.get("source"),
            local_rate_unit=e.get("local_rate_unit"), rate_source=e.get("rate_source"),
            source_url=e.get("source_url"), retrieved_at=e.get("retrieved_at"),
        )
        out["imported"] += 1
    return out


# ============================ ALIAS modèle observé -> identité tarifaire =======
def insert_model_alias(
    conn: sqlite3.Connection,
    *,
    provider: str,
    observed_model: str,
    pricing_model: str,
    source: str,
    effective_from: Optional[str] = None,
    source_url: Optional[str] = None,
    retrieved_at: Optional[str] = None,
) -> str:
    """Insère un mapping EXACT observed_model -> pricing_model. IMMUABLE/versionné. Ne met à
    jour AUCUNE ligne. `source` (provenance/décision) est OBLIGATOIRE.

    TEMPORALITÉ (garde dure) : un alias porte TOUJOURS une `effective_from` datée — jamais NULL.
    Absente, elle vaut « maintenant » (l'instant de l'import) : un RE-CIBLAGE importé aujourd'hui
    ne s'applique donc PAS rétroactivement aux appels passés. Le barème shippé, lui, passe une
    date PASSÉE explicite (date de relevé) pour couvrir l'usage déjà enregistré. Sans date jamais
    NULL, deux versions ne peuvent plus se départager par le seul numéro de version — la
    résolution reste purement temporelle.
    """
    if not source:
        raise ValueError("insert_model_alias exige `source` (provenance de la décision)")
    eff = effective_from if effective_from is not None else _now_iso()
    prev = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM model_alias WHERE provider=? AND observed_model=?",
        (provider, observed_model),
    ).fetchone()[0]
    alias_id = docid.nouveau()
    conn.execute(
        "INSERT INTO model_alias (alias_id, provider, observed_model, pricing_model, version, "
        "effective_from, source, source_url, retrieved_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (alias_id, provider, observed_model, pricing_model, int(prev) + 1,
         eff, source, source_url, retrieved_at, _now_iso()),
    )
    return alias_id


def resolve_pricing_model(
    conn: sqlite3.Connection, *, provider: str, observed_model: str, at: Optional[str]
) -> str:
    """Rend l'IDENTITÉ DE TARIFICATION d'un modèle observé, via le mapping EXACT en vigueur.

    Correspondance stricte sur (provider, observed_model) : aucun aliasing flou. Sans mapping,
    l'identité est le modèle lui-même — donc aucun autre modèle n'hérite d'un alias par accident.
    """
    at = at or _now_iso()
    row = conn.execute(
        "SELECT pricing_model FROM model_alias WHERE provider=? AND observed_model=? "
        "AND (effective_from IS NULL OR effective_from <= ?) "
        "ORDER BY effective_from DESC, version DESC LIMIT 1",
        (provider, observed_model, at),
    ).fetchone()
    return row["pricing_model"] if row is not None else observed_model


_ALIAS_MATCH = ("pricing_model", "source_url", "retrieved_at")


def import_aliases(conn: sqlite3.Connection, entries) -> dict:
    """Importe des mappings d'alias, EXPLICITE et IDEMPOTENT (même cible = ignoré, cible changée
    = nouvelle version, jamais d'écrasement). Renvoie {imported, skipped}."""
    out = {"imported": 0, "skipped": 0}
    for e in entries:
        latest = conn.execute(
            "SELECT * FROM model_alias WHERE provider=? AND observed_model=? "
            "ORDER BY version DESC LIMIT 1",
            (e["provider"], e["observed_model"]),
        ).fetchone()
        if latest is not None and all(latest[k] == e.get(k) for k in _ALIAS_MATCH):
            out["skipped"] += 1
            continue
        insert_model_alias(
            conn, provider=e["provider"], observed_model=e["observed_model"],
            pricing_model=e["pricing_model"], source=e["source"],
            effective_from=e.get("effective_from"), source_url=e.get("source_url"),
            retrieved_at=e.get("retrieved_at"),
        )
        out["imported"] += 1
    return out


def bootstrap_official_pricing(conn: sqlite3.Connection) -> dict:
    """Importe le manifeste ratifié (`core.pricing_official`) : tarifs OFFICIELS + alias AUDITÉS.
    EXPLICITE, appelé par personne automatiquement. Idempotent. Une usage.db neuve reste vide
    tant qu'on ne l'invoque pas.
    """
    from . import pricing_official  # noqa: PLC0415
    out = import_pricing(conn, pricing_official.OFFICIAL_RATES)
    aliases = import_aliases(conn, getattr(pricing_official, "OFFICIAL_ALIASES", []))
    out["aliases_imported"] = aliases["imported"]
    out["aliases_skipped"] = aliases["skipped"]
    return out


def import_catalogue(conn: sqlite3.Connection) -> dict:
    """Importe TOUT le catalogue FinOps embarqué : tarifs OFFICIELS + alias AUDITÉS +
    HYPOTHÈSES LOCALES (`configured_manual`). Idempotent (rien n'est ré-inséré à l'identique),
    additif/versionné, jamais d'écrasement. Peut lever ; l'appelant app-level l'enveloppe.

    Les hypothèses locales sont importées via le MÊME mécanisme immuable/versionné que les
    tarifs, mais restent une source de données SÉPARÉE (`pricing_official.LOCAL_ASSUMPTIONS`,
    hors `OFFICIAL_RATES`).
    """
    from . import pricing_official  # noqa: PLC0415
    out = bootstrap_official_pricing(conn)
    local = import_pricing(conn, getattr(pricing_official, "LOCAL_ASSUMPTIONS", []))
    out["local_imported"] = local["imported"]
    out["local_skipped"] = local["skipped"]
    return out


def initialize_catalogue(path: Optional[Path | str] = None) -> dict:
    """Seam d'initialisation FinOps AU NIVEAU APPLICATION, invoqué par le démarrage normal.

    BEST-EFFORT ABSOLU : ouvre `usage.db`, importe le catalogue, commit, ferme — et ne lève
    JAMAIS. Un échec d'initialisation (base indisponible, schéma, import) est avalé : il ne peut
    ni casser le démarrage de Memoglyph, ni la STT, ni les rapports. Idempotent : sûr à CHAQUE
    lancement. Ne REPRICE JAMAIS l'historique (aucun appel à `reprice()` ici).

    N'est PAS dans `connect()` (qui reste une primitive base/schéma sans effet de bord) : le
    seed vit ici, dans une fonction d'initialisation explicite appelée par l'app.
    """
    conn = None
    out = {"ok": False}
    try:
        conn = connect(path)
        res = import_catalogue(conn)
        conn.commit()
        out = {"ok": True, **res}
    except Exception as exc:  # noqa: BLE001 — l'init FinOps ne casse jamais le démarrage
        try:
            print(f"[usage] init catalogue ignorée (best-effort) : {type(exc).__name__}: {exc}")
        except Exception:  # noqa: BLE001
            pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    return out


def _row_to_snapshot(row) -> Optional[usage_cost.PricingSnapshot]:
    if row is None:
        return None
    return usage_cost.PricingSnapshot(
        pricing_snapshot_id=row["pricing_snapshot_id"], provider=row["provider"],
        model=row["model"], kind=row["kind"], unit=row["unit"], currency=row["currency"],
        input_rate=row["input_rate"], output_rate=row["output_rate"],
        cached_rate=row["cached_rate"], unit_rate=row["unit_rate"],
    )


def effective_snapshot(
    conn: sqlite3.Connection, *, provider: str, model: str, unit: str, at: Optional[str]
) -> Optional[usage_cost.PricingSnapshot]:
    """Barème en vigueur pour (provider, model, unit) à l'instant `at` : le plus récent dont
    `effective_from <= at`. `None` si aucun — le coût reste alors INCONNU."""
    at = at or _now_iso()
    row = conn.execute(
        "SELECT * FROM pricing_snapshot WHERE provider=? AND model=? AND unit=? "
        "AND (effective_from IS NULL OR effective_from <= ?) "
        "ORDER BY effective_from DESC, version DESC LIMIT 1",
        (provider, model, unit, at),
    ).fetchone()
    return _row_to_snapshot(row)


def snapshot_by_id(conn: sqlite3.Connection, snap_id: str) -> Optional[usage_cost.PricingSnapshot]:
    row = conn.execute(
        "SELECT * FROM pricing_snapshot WHERE pricing_snapshot_id=?", (snap_id,)
    ).fetchone()
    return _row_to_snapshot(row)


# ============================ LECTURE SEULE (affichage FinOps) =================
# Champs SÛRS à exposer : ids, dimensions, comptes, coûts. AUCUN texte de prompt/transcript/CR,
# aucun secret, aucune charge fournisseur — la structure même l'interdit (ces tables n'en
# contiennent pas). La lecture n'ÉCRIT JAMAIS (`mode=ro`, pas de `_ensure_schema`).
_EXEC_PUBLIC = ("execution_id", "kind", "status", "document_id", "meeting_id", "project_id",
                "produced_artifact_id", "provider_primary", "model_primary", "started_at",
                "ended_at", "wall_clock_s", "n_calls", "in_tok", "out_tok", "audio_seconds",
                "cost", "cost_currency", "cost_basis", "pricing_snapshot_id")
_CALL_PUBLIC = ("seq", "stage", "provider", "model", "model_servi", "started_at", "latency_s", "status",
                "error_code", "usage_source", "in_tok", "out_tok", "cached_tok", "reasoning_tok",
                "cost", "cost_currency", "cost_basis", "pricing_snapshot_id")

# Sentinelle « aucun filtre projet » (vue globale/opérateur). À distinguer de `project_id=None`
# qui, lui, cible EXPLICITEMENT les exécutions héritées/hors projet (`project_id IS NULL`).
UNSCOPED = object()


def read_connect(path: Optional[Path | str] = None) -> sqlite3.Connection:
    """Ouvre `usage.db` en LECTURE SEULE (`mode=ro`). N'exécute AUCUN DDL, n'écrit rien.

    Lève `sqlite3.OperationalError` si le fichier n'existe pas ou est illisible — l'appelant
    d'affichage attrape et rend « indisponible ». C'est le SEUL point d'entrée des chemins
    d'affichage : ils ne doivent jamais toucher `connect()` (qui migre le schéma).
    """
    p = Path(path) if path is not None else usage_db_path()
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=sqlite_db.BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    return conn


def _pick(row, fields) -> dict:
    return {k: row[k] for k in fields}


def _provenance(conn: sqlite3.Connection, snap_id: Optional[str]) -> Optional[str]:
    if not snap_id:
        return None
    try:
        r = conn.execute("SELECT rate_source FROM pricing_snapshot WHERE pricing_snapshot_id=?",
                         (snap_id,)).fetchone()
        return r["rate_source"] if r is not None else None
    except Exception:  # noqa: BLE001
        return None


def execution_summary(
    *, document_id: Optional[str] = None, artifact_id: Optional[str] = None,
    kind: Optional[str] = None, path: Optional[Path | str] = None,
) -> Optional[dict]:
    """Résumé compact de l'exécution liée à un artefact (CR) OU un document (transcript).

    Priorité à `artifact_id` (le CR est lié par son ULID via `produced_artifact_id`), sinon
    `document_id` (le transcript via l'exécution `stt`). La PLUS RÉCENTE si plusieurs. Best-effort :
    rend `None` (indisponible) sur toute erreur, base absente comprise. N'écrit JAMAIS.
    """
    conn = None
    try:
        conn = read_connect(path)
        clauses, params = [], []
        if artifact_id is not None:
            clauses.append("produced_artifact_id=?")
            params.append(artifact_id)
        elif document_id is not None:
            clauses.append("document_id=?")
            params.append(document_id)
        else:
            return None
        if kind is not None:
            clauses.append("kind=?")
            params.append(kind)
        row = conn.execute(
            f"SELECT * FROM execution WHERE {' AND '.join(clauses)} "
            "ORDER BY started_at DESC LIMIT 1", params,
        ).fetchone()
        if row is None:
            return None
        out = _pick(row, _EXEC_PUBLIC)
        out["pricing_provenance"] = _provenance(conn, row["pricing_snapshot_id"])
        return out
    except Exception:  # noqa: BLE001 — l'affichage ne casse jamais le produit
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def execution_detail(execution_id: str, *, path: Optional[Path | str] = None) -> Optional[dict]:
    """Détail forensique d'UNE exécution : la ligne + ses `model_call` (champs SÛRS). Best-effort,
    lecture seule. `None` si introuvable/indisponible."""
    conn = None
    try:
        conn = read_connect(path)
        exec_row = conn.execute("SELECT * FROM execution WHERE execution_id=?",
                                (execution_id,)).fetchone()
        if exec_row is None:
            return None
        calls = conn.execute("SELECT * FROM model_call WHERE execution_id=? ORDER BY seq",
                             (execution_id,)).fetchall()
        summary = _pick(exec_row, _EXEC_PUBLIC)
        summary["pricing_provenance"] = _provenance(conn, exec_row["pricing_snapshot_id"])
        return {"execution": summary, "calls": [_pick(c, _CALL_PUBLIC) for c in calls]}
    except Exception:  # noqa: BLE001
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def _clause_projet(project_id) -> tuple[str, list]:
    """`None` = hérité/hors projet (IS NULL), explicite ; une valeur = ce projet ; une
    collection = ces projets (la portée d'un compte), `None` admis parmi eux pour
    les exécutions héritées. Une collection vide ne voit rien."""
    if project_id is None:
        return "project_id IS NULL", []
    if isinstance(project_id, str):
        return "project_id=?", [project_id]
    valeurs = sorted(v for v in project_id if v is not None)
    conditions = [f"project_id IN ({','.join('?' * len(valeurs))})"] if valeurs else []
    if None in project_id:
        conditions.append("project_id IS NULL")
    return ("(" + " OR ".join(conditions) + ")") if conditions else "0", valeurs


# Borne dure de la liste d'exécutions du drill-down : jamais un journal géant. `limit` est
# clampé à [1, MAX] ; le tableau de bord ne charge que par page, à la demande.
EXEC_LIST_MAX = 200
EXEC_LIST_DEFAULT = 50


def list_executions(
    *, project_id=UNSCOPED, start: Optional[str] = None, end: Optional[str] = None,
    provider: Optional[str] = None, model: Optional[str] = None,
    stage: Optional[str] = None, kind: Optional[str] = None,
    limit: int = EXEC_LIST_DEFAULT, offset: int = 0, path: Optional[Path | str] = None,
) -> dict:
    """Liste BORNÉE d'exécutions brutes (drill-down), champs SÛRS uniquement. Lecture seule.

    Bornée par projet (`project_id` : `UNSCOPED` = pas de filtre / une valeur = ce projet / `None`
    = hérité IS NULL) et paginée (`limit` clampé [1, EXEC_LIST_MAX], `offset`). Requête indexée sur
    la table `execution` (jamais un scan du journal d'appels). Filtres : plage de jours, fournisseur
    (`provider_primary`), modèle (`model_primary`), genre (`kind`), et `stage` (via EXISTS sur
    model_call, ou 'stt' = genre stt). AUCUN texte de prompt/CR ni secret. Best-effort : rend une
    liste vide sur indisponibilité. N'écrit JAMAIS.
    """
    try:
        limit = max(1, min(int(limit), EXEC_LIST_MAX))
    except (TypeError, ValueError):
        limit = EXEC_LIST_DEFAULT
    try:
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        offset = 0
    conn = None
    try:
        conn = read_connect(path)
        where, params = [], []
        if start is not None:
            where.append("substr(started_at,1,10)>=?")
            params.append(start)
        if end is not None:
            where.append("substr(started_at,1,10)<=?")
            params.append(end)
        if project_id is not UNSCOPED:
            condition, valeurs = _clause_projet(project_id)
            where.append(condition)
            params += valeurs
        if provider is not None:
            where.append("provider_primary=?")
            params.append(provider)
        if model is not None:
            where.append("model_primary=?")
            params.append(model)
        if kind is not None:
            where.append("kind=?")
            params.append(kind)
        if stage is not None and stage not in ("stt", "(execution)"):
            where.append("execution_id IN (SELECT execution_id FROM model_call WHERE stage=?)")
            params.append(stage)
        elif stage == "stt":
            where.append("kind='stt'")
        wsql = (" WHERE " + " AND ".join(where)) if where else ""
        rows = conn.execute(
            f"SELECT * FROM execution{wsql} ORDER BY started_at DESC, execution_id DESC "
            "LIMIT ? OFFSET ?", [*params, limit + 1, offset],   # +1 pour savoir s'il reste
        ).fetchall()
        has_more = len(rows) > limit
        out = []
        for r in rows[:limit]:
            item = _pick(r, _EXEC_PUBLIC)
            item["pricing_provenance"] = _provenance(conn, r["pricing_snapshot_id"])
            out.append(item)
        return {"executions": out, "limit": limit, "offset": offset, "has_more": has_more}
    except Exception:  # noqa: BLE001 — l'affichage ne casse jamais le produit
        return {"executions": [], "limit": limit, "offset": offset, "has_more": False}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


# --- Lecture d'AGRÉGAT pour le futur tableau de bord (lit usage_daily, jamais model_call) ---
# Dimensions de regroupement autorisées (liste blanche : injectées dans le SQL). `currency` et
# `cost_basis` sont TOUJOURS ajoutés au regroupement et à la sortie — on ne mélange jamais deux
# devises ni deux bases dans un même agrégat (règle dure FinOps).
_ROLLUP_DIMS = ("day", "project_id", "provider", "model", "stage", "exec_kind", "cost_basis")


def usage_rollup(
    *, start: Optional[str] = None, end: Optional[str] = None,
    project_id=UNSCOPED,
    provider: Optional[str] = None, model: Optional[str] = None,
    stage: Optional[str] = None, kind: Optional[str] = None,
    group_by: str = "day", path: Optional[Path | str] = None,
) -> list[dict]:
    """Séries agrégées lues depuis `usage_daily` (lecture seule). Pour le tableau de bord Usage.

    `group_by` ∈ {day, project_id, provider, model, stage, exec_kind, cost_basis}. `currency`+
    `cost_basis` figurent toujours en sortie : nuage (`cloud_derived`) et local (`local_imputed`)
    restent séparés, devises jamais additionnées, non tarifé (`cost_basis` NULL, coût NULL) distinct.
    Filtres : plage de jours (`start`/`end`), fournisseur, modèle, étape, genre d'exécution, et
    `project_id` — `UNSCOPED` (défaut) = pas de filtre projet (vue globale) ; une valeur = ce seul
    projet ; `None` = EXPLICITEMENT les exécutions héritées/hors projet. Deux projets ne se
    mélangent jamais. Best-effort : `[]` si l'agrégat est indisponible. N'écrit JAMAIS."""
    dim = group_by if group_by in _ROLLUP_DIMS else "day"
    conn = None
    try:
        conn = read_connect(path)
        where, params = [], []
        if start is not None:
            where.append("day>=?")
            params.append(start)
        if end is not None:
            where.append("day<=?")
            params.append(end)
        if project_id is not UNSCOPED:
            condition, valeurs = _clause_projet(project_id)
            where.append(condition)
            params += valeurs
        if provider is not None:
            where.append("provider=?")
            params.append(provider)
        if model is not None:
            where.append("model=?")
            params.append(model)
        if stage is not None:
            where.append("stage=?")
            params.append(stage)
        if kind is not None:
            where.append("exec_kind=?")
            params.append(kind)
        wsql = (" WHERE " + " AND ".join(where)) if where else ""
        rows = conn.execute(
            f"""
            SELECT {dim} AS bucket, currency, cost_basis,
                   SUM(n_executions)  AS n_executions,
                   SUM(n_model_calls) AS n_model_calls,
                   SUM(in_tok) AS in_tok, SUM(out_tok) AS out_tok,
                   SUM(cached_tok) AS cached_tok, SUM(reasoning_tok) AS reasoning_tok,
                   SUM(audio_seconds) AS audio_seconds, SUM(wall_clock_s) AS wall_clock_s,
                   CASE WHEN COUNT(cost)=0 THEN NULL ELSE ROUND(SUM(cost),10) END AS cost
            FROM usage_daily{wsql}
            GROUP BY {dim}, currency, cost_basis
            ORDER BY {dim}
            """,
            params,
        ).fetchall()
        return [{"group_by": dim, **_pick(r, ("bucket", "currency", "cost_basis",
                 "n_executions", "n_model_calls", "in_tok", "out_tok", "cached_tok",
                 "reasoning_tok", "audio_seconds", "wall_clock_s", "cost"))} for r in rows]
    except Exception:  # noqa: BLE001 — l'affichage ne casse jamais le produit
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


# ============================ COÛT (dérivé, best-effort) ======================
def _price_execution(conn: sqlite3.Connection, execution_id: str) -> None:
    """Calcule le coût dérivé des appels d'une exécution + le rollup d'exécution.

    Barème choisi par instant d'appel (`started_at`) → reproductible. Barème absent ⇒ coût
    NULL (jamais un faux zéro). Coût nuage DÉRIVÉ (catalogue) et local IMPUTÉ restent
    séparés. Une exécution n'a en pratique qu'un fournisseur, donc une seule base et une
    seule DEVISE ; par sûreté, si des appels mélangeaient devises ou bases, on REFUSE de les
    additionner (rollup à NULL) plutôt que d'inventer une somme USD+EUR — aucune conversion
    de change en phase 1A. Cette fonction PEUT lever ; ses appelants l'enveloppent (best-effort).
    """
    calls = conn.execute(
        "SELECT * FROM model_call WHERE execution_id=? ORDER BY seq", (execution_id,)
    ).fetchall()
    now = _now_iso()
    total = 0.0
    any_priced = False
    basis = None
    currency = None
    snap_id_seen = None
    mixed = False        # devises OU bases divergentes entre appels d'une même exécution
    for call in calls:
        # Identité de TARIFICATION (l'appel garde son `model` observé, jamais réécrit).
        pricing_model = resolve_pricing_model(
            conn, provider=call["provider"], observed_model=call["model"], at=call["started_at"])
        snap = effective_snapshot(
            conn, provider=call["provider"], model=pricing_model,
            unit=usage_cost.TOKEN_UNIT, at=call["started_at"],
        )
        cost = usage_cost.token_cost(
            in_tok=call["in_tok"], out_tok=call["out_tok"],
            cached_tok=call["cached_tok"], snap=snap,
        )
        if cost is None or snap is None:
            conn.execute(
                "UPDATE model_call SET cost=NULL, cost_currency=NULL, cost_basis=NULL, "
                "pricing_snapshot_id=NULL, cost_computed_at=? WHERE call_id=?",
                (now, call["call_id"]),
            )
            continue
        b = usage_cost.cost_basis_for(snap.kind)
        conn.execute(
            "UPDATE model_call SET cost=?, cost_currency=?, cost_basis=?, "
            "pricing_snapshot_id=?, cost_computed_at=? WHERE call_id=?",
            (cost, snap.currency, b, snap.pricing_snapshot_id, now, call["call_id"]),
        )
        if any_priced and (snap.currency != currency or b != basis):
            mixed = True     # jamais additionner deux devises/bases en un seul montant
        total += cost
        any_priced = True
        basis = b
        currency = snap.currency
        snap_id_seen = snap.pricing_snapshot_id

    # Exécution STT non tarifée au jeton : coût à l'UNITÉ audio. La base vient du barème
    # (`cloud_derived` pour un tarif fournisseur type Voxtral $/min, `local_imputed` pour un
    # relevé local $/s) — jamais présumée. La durée mesurée (secondes) est convertie vers l'unité
    # NATIVE du barème (ex. minute) par un facteur exact.
    ex = conn.execute(
        "SELECT provider_primary, model_primary, audio_seconds, started_at "
        "FROM execution WHERE execution_id=?", (execution_id,)
    ).fetchone()
    if not any_priced and ex is not None and ex["audio_seconds"] is not None:
        stt_pricing_model = resolve_pricing_model(
            conn, provider=ex["provider_primary"], observed_model=ex["model_primary"],
            at=ex["started_at"])
        for unit, secs in usage_cost.AUDIO_UNIT_SECONDS.items():
            snap = effective_snapshot(
                conn, provider=ex["provider_primary"], model=stt_pricing_model,
                unit=unit, at=ex["started_at"],
            )
            qty = ex["audio_seconds"] / secs
            cost = usage_cost.unit_cost(quantity=qty, snap=snap)
            if cost is not None and snap is not None:
                total = cost
                any_priced = True
                basis = usage_cost.cost_basis_for(snap.kind)
                currency = snap.currency
                snap_id_seen = snap.pricing_snapshot_id
                break

    if any_priced and not mixed:
        conn.execute(
            "UPDATE execution SET cost=?, cost_currency=?, cost_basis=?, "
            "pricing_snapshot_id=?, cost_computed_at=? WHERE execution_id=?",
            (round(total, 10), currency, basis, snap_id_seen, now, execution_id),
        )
    elif any_priced and mixed:
        # Devises/bases mélangées : les coûts PAR APPEL restent écrits, mais on REFUSE
        # un total d'exécution qui additionnerait des devises/bases hétérogènes.
        conn.execute(
            "UPDATE execution SET cost=NULL, cost_currency=NULL, cost_basis='mixed', "
            "pricing_snapshot_id=NULL, cost_computed_at=? WHERE execution_id=?",
            (now, execution_id),
        )
    else:
        conn.execute(
            "UPDATE execution SET cost=NULL, cost_currency=NULL, cost_basis=NULL, "
            "pricing_snapshot_id=NULL, cost_computed_at=? WHERE execution_id=?",
            (now, execution_id),
        )


def reprice(path: Optional[Path | str] = None, *, execution_id: Optional[str] = None) -> dict:
    """Recalcule le coût dérivé (toutes les exécutions, ou une seule). RÉTROACTIF et sûr.

    Ne touche JAMAIS aux faits d'usage : seuls les champs `cost*`/`pricing_snapshot_id` sont
    réécrits. Insérer un barème corrigé puis rappeler `reprice()` met à jour l'historique sans
    réinterpréter le moindre jeton. Best-effort : renvoie un compte, n'altère aucun produit.
    """
    conn = None
    out = {"executions": 0, "errors": 0}
    try:
        conn = connect(path)
        ids = ([execution_id] if execution_id
               else [r[0] for r in conn.execute("SELECT execution_id FROM execution")])
        for eid in ids:
            try:
                _price_execution(conn, eid)
                out["executions"] += 1
            except Exception:  # noqa: BLE001
                out["errors"] += 1
        # Les coûts ont changé : l'agrégat dérivé doit suivre. Rebuild complet (idempotent),
        # dans le même filet best-effort — jamais bloquant.
        try:
            conn.execute("DELETE FROM usage_daily")
            _daily_insert(conn, day=None, now=_now_iso())
        except Exception:  # noqa: BLE001
            out["errors"] += 1
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        out["errors"] += 1
        try:
            print(f"[usage] reprice ignoré (best-effort) : {type(exc).__name__}: {exc}")
        except Exception:  # noqa: BLE001
            pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    return out


# ============================ AGRÉGAT usage_daily (dérivé, reconstructible) ====
# Deux « unités » alimentent l'agrégat, réunies puis groupées sur les 7 dimensions de grain.
# Le jour est la DATE (UTC) de `execution.started_at` : toutes les étapes d'un CR partagent
# donc le même jour. Chaque exécution émet EXACTEMENT une unité d'exécution ('stt' si audio
# sans appel, sinon '(execution)') → n_executions et wall_clock comptés une seule fois.
_DAILY_UNITS = """
    SELECT substr(e.started_at,1,10) AS day, e.project_id AS project_id,
           c.provider AS provider, c.model AS model,
           COALESCE(c.stage,'(unspecified)') AS stage, e.kind AS exec_kind,
           c.cost_currency AS currency, c.cost_basis AS cost_basis,
           0 AS n_executions, 1 AS n_model_calls,
           COALESCE(c.in_tok,0) AS in_tok, COALESCE(c.out_tok,0) AS out_tok,
           COALESCE(c.cached_tok,0) AS cached_tok, COALESCE(c.reasoning_tok,0) AS reasoning_tok,
           0.0 AS audio_seconds, 0.0 AS wall_clock_s,
           c.cost AS cost, CASE WHEN c.cost IS NOT NULL THEN 1 ELSE 0 END AS priced
    FROM model_call c JOIN execution e ON e.execution_id = c.execution_id{where}
    UNION ALL
    SELECT substr(e.started_at,1,10) AS day, e.project_id AS project_id,
           e.provider_primary AS provider, e.model_primary AS model,
           CASE WHEN e.audio_seconds IS NOT NULL AND COALESCE(e.n_calls,0)=0
                THEN 'stt' ELSE '(execution)' END AS stage,
           e.kind AS exec_kind, e.cost_currency AS currency, e.cost_basis AS cost_basis,
           1 AS n_executions, 0 AS n_model_calls,
           0 AS in_tok, 0 AS out_tok, 0 AS cached_tok, 0 AS reasoning_tok,
           COALESCE(e.audio_seconds,0.0) AS audio_seconds,
           COALESCE(e.wall_clock_s,0.0) AS wall_clock_s,
           CASE WHEN COALESCE(e.n_calls,0)=0 THEN e.cost ELSE NULL END AS cost,
           CASE WHEN COALESCE(e.n_calls,0)=0 AND e.cost IS NOT NULL THEN 1 ELSE 0 END AS priced
    FROM execution e{where}
"""


def _daily_insert(conn: sqlite3.Connection, *, day: Optional[str], now: str) -> None:
    """(Re)bâtit les seaux usage_daily, soit tout l'agrégat, soit une SEULE partition-jour.

    Coût du seau : NULL si aucune unité tarifée (jamais un faux zéro), sinon la somme (une
    seule devise et une seule base par seau, garanties par la clé de grain).
    """
    where = " WHERE substr(e.started_at,1,10)=:day" if day is not None else ""
    units = _DAILY_UNITS.format(where=where)
    conn.execute(
        f"""
        INSERT INTO usage_daily (
            day, project_id, provider, model, stage, exec_kind, currency, cost_basis,
            n_executions, n_model_calls, in_tok, out_tok, cached_tok, reasoning_tok,
            audio_seconds, wall_clock_s, cost, n_units, rebuilt_at)
        SELECT day, project_id, provider, model, stage, exec_kind, currency, cost_basis,
               SUM(n_executions), SUM(n_model_calls),
               SUM(in_tok), SUM(out_tok), SUM(cached_tok), SUM(reasoning_tok),
               SUM(audio_seconds), SUM(wall_clock_s),
               CASE WHEN SUM(priced)=0 THEN NULL ELSE ROUND(SUM(cost),10) END,
               COUNT(*), :now
        FROM ({units})
        GROUP BY day, project_id, provider, model, stage, exec_kind, currency, cost_basis
        """,
        {"day": day, "now": now},
    )


def rebuild_usage_daily(path: Optional[Path | str] = None) -> dict:
    """Reconstruit INTÉGRALEMENT usage_daily depuis les faits bruts. Idempotent, déterministe.

    Source de vérité de l'agrégat : `execution`/`model_call` restent intacts (lecture seule
    ici). Supprime tout puis réinsère. Best-effort : n'altère jamais la génération.
    """
    conn = None
    out = {"rows": 0, "errors": 0}
    try:
        conn = connect(path)
        conn.execute("DELETE FROM usage_daily")
        _daily_insert(conn, day=None, now=_now_iso())
        conn.commit()
        out["rows"] = conn.execute("SELECT COUNT(*) FROM usage_daily").fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        out["errors"] += 1
        try:
            print(f"[usage] rebuild usage_daily ignoré (best-effort) : {type(exc).__name__}: {exc}")
        except Exception:  # noqa: BLE001
            pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    return out


def _update_daily_for(conn: sqlite3.Connection, execution_id: str) -> None:
    """Recalcule la/les partition(s)-jour touchée(s) par une exécution, depuis les faits bruts.

    Incrémental SÛR : DELETE de la partition-jour puis réinsertion groupée de ce jour. Comme le
    grain agrège plusieurs exécutions, on ne met pas à jour un seau isolé — on rebâtit le JOUR
    entier (déterministe, idempotent, identique au rebuild complet pour ce jour). PEUT lever ;
    l'appelant enveloppe (best-effort)."""
    now = _now_iso()
    days = [r[0] for r in conn.execute(
        "SELECT DISTINCT substr(started_at,1,10) FROM execution WHERE execution_id=?",
        (execution_id,))]
    for day in days:
        conn.execute("DELETE FROM usage_daily WHERE day=?", (day,))
        _daily_insert(conn, day=day, now=now)


# ============================ PERSISTANCE (best-effort) =======================
def persist_execution(buffer, *, path: Optional[Path | str] = None) -> bool:
    """Écrit une exécution + ses appels, en UNE transaction. BEST-EFFORT : ne lève JAMAIS.

    Les FAITS d'usage sont écrits d'abord et priment. Le calcul de coût suit, dans son PROPRE
    filet : un barème absent ou malformé laisse le coût à NULL sans compromettre les faits ni
    la génération.
    """
    conn = None
    try:
        conn = connect(path)
        exec_row = buffer.execution_row()
        conn.execute(
            "INSERT OR REPLACE INTO execution ("
            "execution_id, kind, document_id, meeting_id, project_id, produced_artifact_id, "
            "provider_primary, model_primary, started_at, ended_at, wall_clock_s, status, "
            "n_calls, in_tok, out_tok, audio_seconds, envoi_ultra_autorise, schema_version, created_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                exec_row["execution_id"], exec_row["kind"], exec_row["document_id"],
                exec_row["meeting_id"], exec_row["project_id"], exec_row["produced_artifact_id"],
                exec_row["provider_primary"], exec_row["model_primary"], exec_row["started_at"],
                exec_row["ended_at"], exec_row["wall_clock_s"], exec_row["status"],
                exec_row["n_calls"], exec_row["in_tok"], exec_row["out_tok"],
                exec_row.get("audio_seconds"), int(exec_row.get("envoi_ultra_autorise") or 0),
                SCHEMA_VERSION, exec_row["created_at"],
            ),
        )
        for call in buffer.call_rows():
            conn.execute(
                "INSERT OR REPLACE INTO model_call ("
                "call_id, execution_id, seq, stage, provider, model, started_at, latency_s, "
                "status, usage_source, in_tok, out_tok, cached_tok, reasoning_tok, created_at, model_servi, error_code"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    call["call_id"], call["execution_id"], call["seq"], call["stage"],
                    call["provider"], call["model"], call["started_at"], call["latency_s"],
                    call["status"], call["usage_source"], call["in_tok"], call["out_tok"],
                    call["cached_tok"], call["reasoning_tok"], call["created_at"], call.get("model_servi"),
                    call.get("error_code"),
                ),
            )
        # Coût DÉRIVÉ, filet séparé : jamais laisser un barème casser la persistance des faits.
        try:
            _price_execution(conn, exec_row["execution_id"])
        except Exception as exc:  # noqa: BLE001
            try:
                print(f"[usage] coût ignoré (best-effort) : {type(exc).__name__}: {exc}")
            except Exception:  # noqa: BLE001
                pass
        conn.commit()
        # Agrégat usage_daily : maintenance incrémentale APRÈS le commit des faits, filet isolé.
        # Les faits sont déjà durables ; un échec ici ne perd rien (un rebuild complet reste la
        # source de vérité) et ne peut pas casser la génération. Hors chemin critique.
        try:
            _update_daily_for(conn, exec_row["execution_id"])
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
            try:
                print(f"[usage] usage_daily incrémental ignoré (best-effort) : "
                      f"{type(exc).__name__}: {exc}")
            except Exception:  # noqa: BLE001
                pass
        return True
    except Exception as exc:  # noqa: BLE001 — la télémétrie ne casse jamais la génération
        try:
            print(f"[usage] persistance ignorée (best-effort) : {type(exc).__name__}: {exc}")
        except Exception:  # noqa: BLE001
            pass
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
