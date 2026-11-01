"""Recherche sémantique : chunking + embeddings (locaux) + KNN (sqlite-vec).

Trouve les passages par le SENS, pas par le mot exact. Index dérivé stocké dans
la même base d'index (`index.db`, tables `chunks` + `vec_chunks`), reconstructible.
"""

from __future__ import annotations

import hashlib
import math
import re
import sqlite3
from typing import Optional

import sqlite_vec

from . import docid, embed, library, resources, sqlite_db
from . import projects as project_store
from .config import EMBED_DIM, EMBED_MODEL
from .experimental.chunking import normaliser_horodatage
from .llm_base import LLMError

# Chemin canonique unique (cf. sqlite_db). L'indirection par library.DB_PATH
# fonctionnait, mais faire pointer chaque store sur la source unique évite qu'un
# futur changement d'un seul module ne re-scinde l'index.
DB_PATH = sqlite_db.DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    document_id TEXT,
    idx         INTEGER,
    ts          TEXT,
    text        TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunks_name ON chunks(name);
"""

# 2 : un transcript ne livre plus que ses répliques (audit externe, B4). Le profil
# généré qui les précède entrait dans les passages avec le premier horodatage
# trouvé : l'assistant pouvait citer un résumé du modèle comme une parole. Une
# version stockée différente reconstruit l'index au démarrage (`reindex_all`).
CHUNKER_VERSION = 2

# Index lexical sur les MORCEAUX, et non sur les documents.
#
# `library.transcripts_fts` indexe des documents entiers. RRF fusionne des
# RANGS : deux listes qui ne classent pas le même objet ne se fusionnent pas.
# C'est cette dépendance qui imposait cet index-ci, qui n'existait pas.
#
# Table AUTONOME, comme `transcripts_fts` : elle stocke son propre texte.
#
# Piège : `content=''` ne décrit PAS ce mode, c'est
# l'inverse : une table « contentless » ne garde que l'index, et SQLite refuse
# alors tout DELETE, ce que cinq tests ont montré immédiatement. Le mode
# « external content » lierait la table à `chunks` par rowid et rendrait une
# désynchronisation silencieuse. À notre échelle — 250 morceaux — la
# duplication du texte coûte moins qu'un risque muet, et c'est déjà le choix
# fait pour `transcripts_fts`.
#
# Même tokenizer que `transcripts_fts` : sans `remove_diacritics 2`, « réunion »
# et « reunion » seraient deux termes, et la recherche française en souffrirait.
_CHUNKS_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""

_META_SCHEMA = """
CREATE TABLE IF NOT EXISTS semantic_index_meta (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    model           TEXT NOT NULL,
    dimension       INTEGER NOT NULL,
    chunker_version INTEGER NOT NULL,
    updated_at      TEXT NOT NULL
);
"""


def _vec_schema() -> str:
    return (
        "CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING "
        f"vec0(chunk_id INTEGER PRIMARY KEY, embedding float[{EMBED_DIM}]);"
    )


def _conn() -> sqlite3.Connection:
    conn = sqlite_db.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.executescript(_SCHEMA)
    conn.execute(_META_SCHEMA)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(chunks)")}
    if "document_id" not in columns:
        conn.execute("ALTER TABLE chunks ADD COLUMN document_id TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id)")
    # Migration dérivée et reprenable : l'identité vient de transcripts.id.
    conn.execute(
        "UPDATE chunks SET document_id = ("
        "SELECT t.id FROM transcripts t WHERE t.name = chunks.name"
        ") WHERE document_id IS NULL"
    )
    conn.execute(_vec_schema())
    conn.executescript(_CHUNKS_FTS_SCHEMA)
    # Ne jamais laisser les migrations ouvrir une transaction implicite chez
    # l'appelant, en particulier avant un appel réseau Ollama.
    conn.commit()
    return conn


def _nom_du_moteur() -> str:
    """Identifie l'espace vectoriel dans `semantic_index_meta`.

    Doit nommer le moteur RÉELLEMENT employé : sinon un changement de backend
    passerait inaperçu et l'on comparerait des vecteurs de deux espaces
    différents — des distances sans signification, rendues sans avertissement.
    """
    from .config import EMBED_BACKEND, EMBED_LOCAL_MODEL

    return EMBED_LOCAL_MODEL if EMBED_BACKEND == "local" else EMBED_MODEL


def _current_spec() -> dict:
    return {
        "model": _nom_du_moteur(),
        "dimension": EMBED_DIM,
        "chunker_version": CHUNKER_VERSION,
    }


def _stored_spec(conn: sqlite3.Connection) -> Optional[dict]:
    row = conn.execute(
        "SELECT model, dimension, chunker_version "
        "FROM semantic_index_meta WHERE id = 1"
    ).fetchone()
    return dict(row) if row is not None else None


def _write_spec(conn: sqlite3.Connection) -> None:
    spec = _current_spec()
    conn.execute(
        "INSERT INTO semantic_index_meta "
        "(id, model, dimension, chunker_version, updated_at) "
        "VALUES (1, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(id) DO UPDATE SET model = excluded.model, "
        "dimension = excluded.dimension, "
        "chunker_version = excluded.chunker_version, "
        "updated_at = excluded.updated_at",
        (spec["model"], spec["dimension"], spec["chunker_version"]),
    )


def index_spec() -> Optional[dict]:
    """Spécification ayant réellement produit l'index stocké."""
    conn = _conn()
    try:
        return _stored_spec(conn)
    finally:
        conn.close()


class SemanticIndexIncompatible(LLMError):
    """L'index stocké ne correspond pas au moteur configuré.

    `LLMError` reste le marqueur « réponds 503 », mais il vient du module
    NEUTRE : rien ici ne dépend d'Ollama.
    """


def _require_compatible_index(conn: sqlite3.Connection) -> None:
    stored = _stored_spec(conn)
    current = _current_spec()
    if stored != current:
        raise SemanticIndexIncompatible(
            "Index sémantique incompatible ou non versionné ; "
            "une reconstruction est requise"
        )


def _check_index_compatible() -> None:
    conn = _conn()
    try:
        _require_compatible_index(conn)
    finally:
        conn.close()


# ---------- Chunking ----------

_TS = re.compile(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]")
# Titre écrit par `writer_md.render_markdown` entre le profil et les répliques,
# le même en français et en anglais. Absent d'un transcript sans profil (il n'a
# que des répliques) et d'un compte-rendu.
_SECTION_TRANSCRIPT = re.compile(r"^## Transcript\s*$", re.MULTILINE)


def _clean(p: str) -> str:
    """Retire libellés **Speaker** et [timestamps] pour un embedding plus propre."""
    p = re.sub(r"\*\*[^*]+\*\*", "", p)
    p = _TS.sub("", p)
    return " ".join(p.split()).strip(" ·-*#")


def paroles(body: str) -> str:
    """Ce qui, dans un corps de transcript, a été dit : les répliques, sans le profil."""
    m = _SECTION_TRANSCRIPT.search(body or "")
    return (body or "")[m.end():] if m else (body or "")


def chunk_body(body: str, target: int = 600) -> list[tuple[str, str | None]]:
    """Découpe le corps en passages (~target caractères). Retourne (texte, timestamp).

    Seules les répliques d'un transcript sont découpées (`paroles`) : profil et
    résumé générés ne deviennent jamais des passages cités comme parole.
    """
    paras = [p for p in re.split(r"\n\s*\n", paroles(body)) if p.strip() and not p.lstrip().startswith("#")]
    chunks: list[tuple[str, str | None]] = []
    cur: list[str] = []
    cur_ts: str | None = None
    cur_len = 0
    for p in paras:
        m = _TS.search(p)
        # Le passage cite l'horodatage sous la forme du CR et de l'ECL, `HH:MM:SS` ;
        # un horodatage impossible garde sa forme écrite plutôt que de disparaître.
        ts = (normaliser_horodatage(m.group(1)) or m.group(1)) if m else None
        text = _clean(p)
        if not text:
            continue
        if cur_ts is None:
            cur_ts = ts
        cur.append(text)
        cur_len += len(text) + 1
        if cur_len >= target:
            chunks.append((" ".join(cur), cur_ts))
            cur, cur_ts, cur_len = [], None, 0
    if cur:
        chunks.append((" ".join(cur), cur_ts))
    return chunks


# ---------- Indexation ----------


class InvalidEmbedding(LLMError):
    """Réponse d'embedding inutilisable : refuser avant toute écriture SQLite."""


def _validated_embeddings(vectors, expected: int) -> list[list[float]]:
    if not isinstance(vectors, (list, tuple)) or len(vectors) != expected:
        actual = len(vectors) if isinstance(vectors, (list, tuple)) else "inconnu"
        raise InvalidEmbedding(
            f"Le moteur d'embeddings a renvoyé {actual} vecteur(s) pour {expected} texte(s)"
        )
    clean: list[list[float]] = []
    for index, vector in enumerate(vectors):
        if not isinstance(vector, (list, tuple)) or len(vector) != EMBED_DIM:
            actual = len(vector) if isinstance(vector, (list, tuple)) else "inconnue"
            raise InvalidEmbedding(
                f"Dimension invalide pour le vecteur {index} : {actual}, attendu {EMBED_DIM}"
            )
        try:
            normalized = [float(value) for value in vector]
        except (TypeError, ValueError) as exc:
            raise InvalidEmbedding(f"Vecteur {index} non numérique") from exc
        if not all(math.isfinite(value) for value in normalized):
            raise InvalidEmbedding(f"Vecteur {index} contenant NaN ou infini")
        clean.append(normalized)
    return clean


def _embed(texts: list[str]) -> list[list[float]]:
    """Vectorise des PASSAGES. Un seul calcul à la fois, validé avant usage."""
    with resources.heavy(label="embeddings"):
        return _validated_embeddings(embed.embed_documents(texts), len(texts))


def _embed_query(text: str) -> list[list[float]]:
    """Vectorise une QUESTION.

    Distinct de `_embed` : les modèles de recherche encodent différemment un
    passage et une question. Le même appel servait les deux, ce qui privait
    nomic de ses préfixes, au prix d'une nette perte de rappel.
    """
    with resources.heavy(label="embeddings"):
        return _validated_embeddings([embed.embed_query(text)], 1)


def _identity(conn: sqlite3.Connection, ref: str) -> tuple[str, str]:
    """(document_id, nom affichable), avec repli legacy pour les vieux tests."""
    if docid.est_valide(ref):
        row = conn.execute(
            "SELECT name FROM transcripts WHERE id = ?", (ref,)
        ).fetchone()
        return ref, row["name"] if row else ref
    row = conn.execute(
        "SELECT id, name FROM transcripts WHERE name = ?", (ref,)
    ).fetchone()
    if row and row["id"]:
        return row["id"], row["name"]
    return ref, ref


def index_file(name: str, body: str) -> int:
    """(Ré)indexe les chunks d'un transcript. Retourne le nb de chunks. Embeddings via Ollama."""
    with resources.heavy(label="indexation sémantique"):
        return _index_file(name, body)


def _index_file(
    name: str, body: str, *, allow_incompatible: bool = False,
    record_spec: bool = True,
) -> int:
    chunks = chunk_body(body)

    # Lecture courte, fermée AVANT l'appel réseau potentiellement long.
    identity_conn = _conn()
    try:
        document_id, display_name = _identity(identity_conn, name)
        stored = _stored_spec(identity_conn)
        has_chunks = identity_conn.execute("SELECT EXISTS(SELECT 1 FROM chunks)").fetchone()[0]
        if not allow_incompatible and stored != _current_spec() and (stored or has_chunks):
            _require_compatible_index(identity_conn)
    finally:
        identity_conn.close()

    vectors = _embed([text for text, _ in chunks]) if chunks else []

    # Remplacement atomique : l'ancien index reste intact si le calcul ou
    # la validation des vecteurs échoue.
    conn = _conn()
    try:
        with conn:
            old = [r[0] for r in conn.execute(
                "SELECT id FROM chunks WHERE document_id = ? OR "
                "(document_id IS NULL AND name = ?)", (document_id, display_name),
            )]
            for cid in old:
                conn.execute("DELETE FROM vec_chunks WHERE chunk_id = ?", (cid,))
                # Suppression explicite : sans elle, le morceau d'hier resterait
                # interrogeable en lexical alors qu'il a disparu du vectoriel.
                # Deux index désaccordés rendraient des résultats fantômes, et
                # RRF les remonterait sans rien signaler.
                conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (cid,))
            conn.execute(
                "DELETE FROM chunks WHERE document_id = ? OR "
                "(document_id IS NULL AND name = ?)", (document_id, display_name),
            )
            for i, ((text, ts), vector) in enumerate(zip(chunks, vectors)):
                cur = conn.execute(
                    "INSERT INTO chunks (name, document_id, idx, ts, text) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (display_name, document_id, i, ts, text),
                )
                conn.execute(
                    "INSERT INTO vec_chunks (chunk_id, embedding) VALUES (?, ?)",
                    (cur.lastrowid, sqlite_vec.serialize_float32(vector)),
                )
                # Même rowid dans les trois tables : c'est ce qui permet à RRF
                # de fusionner deux listes d'identifiants sans jointure.
                conn.execute(
                    "INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)",
                    (cur.lastrowid, text),
                )
            if record_spec:
                _write_spec(conn)
        return len(chunks)
    finally:
        conn.close()


def reindex_all(project_id: Optional[str] = None) -> dict:
    """Synchronise l'index sémantique avec les transcripts.

    Indexe les nouveaux, **réindexe ceux dont le contenu a changé** (le `.md` est
    la source de vérité et peut être édité à la main) et supprime les orphelins.
    La comparaison se fait sur une empreinte du corps : sans elle, une recherche
    sémantique continuait de répondre sur l'ancien texte.
    """
    with resources.heavy(label="réindexation sémantique"):
        return _reindex_all(project_id)


def _reindex_all(project_id: Optional[str] = None) -> dict:
    conn = _conn()
    try:
        # Deux causes de reconstruction, et la seconde est facile à manquer.
        #
        # La spec couvre le modèle, la dimension et le découpage. Elle ne dit
        # rien de l'index LEXICAL, ajouté après coup : à sa création il était
        # vide, aucun document n'avait changé, et la réindexation incrémentale
        # n'avait donc rien à faire. `retrieve` aurait continué de fonctionner
        # en retombant silencieusement sur le dense seul — la moitié de
        # l'hybride absente, sans un mot.
        #
        # On compare donc aussi les EFFECTIFS. C'est un invariant direct, qui
        # se vérifie en une requête et qui rattrape toute désynchronisation
        # future, pas seulement cette migration-ci.
        rebuild = _stored_spec(conn) != _current_spec()
        if not rebuild:
            n_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            n_lex = conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
            if n_chunks != n_lex:
                print(f"[semantic] index lexical désynchronisé "
                      f"({n_lex} entrées pour {n_chunks} morceaux) — reconstruction")
                rebuild = True
        # Une portée sans aucun document n'a rien à indexer : elle ne déclenche pas
        # la reconstruction du corpus entier qui suit. C'est le cas de
        # `api.portee.AUCUN_PROJET` (administrateur sans « Non classé »).
        if rebuild and project_id and conn.execute(
                "SELECT 1 FROM transcripts WHERE project_id = ? LIMIT 1", (project_id,)
        ).fetchone() is None:
            return {"indexed_files": 0, "chunks": 0, "removed": 0}
        # Le vec0 et son modèle sont globaux. Une incompatibilité ne peut pas
        # être réparée pour un seul projet sans laisser un index mixte : la
        # reconstruction balaie donc le corpus autorisé complet (prototype
        # local mono-utilisateur ; le Lot 6 fournira l'union ACL).
        effective_project_id = None if rebuild else project_id
        current_sql = "SELECT id, name, body FROM transcripts"
        current_params: list = []
        if effective_project_id:
            current_sql += " WHERE project_id = ?"
            current_params.append(effective_project_id)
        current = {
            (r["id"] or r["name"]): (r["name"], r["body"] or "")
            for r in conn.execute(current_sql, current_params)
        }
        indexed: dict[str, str] = {}
        if rebuild:
            # L'ancien vec0 peut avoir une dimension physique différente.
            # Tout est dérivé : on le recrée, mais on ne déclare la nouvelle
            # spec qu'APRÈS le dernier document indexé avec succès.
            with conn:
                conn.execute("DROP TABLE IF EXISTS vec_chunks")
                conn.execute("DELETE FROM chunks")
                conn.execute("DELETE FROM chunks_fts")
                conn.execute("DELETE FROM semantic_index_meta")
                conn.execute(_vec_schema())
        else:
            indexed_sql = (
                "SELECT COALESCE(c.document_id, c.name) AS identity, "
                "GROUP_CONCAT(c.text, '') AS t FROM chunks c"
            )
            indexed_params: list = []
            if effective_project_id:
                indexed_sql += (
                    " JOIN transcripts t ON t.id = c.document_id "
                    "OR (c.document_id IS NULL AND t.name = c.name) "
                    "WHERE t.project_id = ?"
                )
                indexed_params.append(effective_project_id)
            indexed_sql += " GROUP BY COALESCE(c.document_id, c.name)"
            for r in conn.execute(indexed_sql, indexed_params):
                indexed[r["identity"]] = hashlib.sha1(
                    (r["t"] or "").encode()
                ).hexdigest()
    finally:
        conn.close()

    def fingerprint(body: str) -> str:
        joined = "".join(t for t, _ in chunk_body(body))
        return hashlib.sha1(joined.encode()).hexdigest()

    todo = [(identity, name, body) for identity, (name, body) in current.items()
            if identity not in indexed or indexed[identity] != fingerprint(body)]
    orphans = [identity for identity in indexed if identity not in current]

    total = 0
    for _, name, body in todo:
        total += _index_file(
            name, body, allow_incompatible=True, record_spec=False,
        )
    for identity in orphans:
        remove(identity)
    conn = _conn()
    try:
        with conn:
            _write_spec(conn)
    finally:
        conn.close()
    return {"indexed_files": len(todo), "chunks": total, "removed": len(orphans)}


def index_name(name: str) -> int:
    """Indexe un transcript en lisant son body depuis la table transcripts."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT name, body FROM transcripts WHERE name = ? OR id = ?",
            (name, name),
        ).fetchone()
    finally:
        conn.close()
    return index_file(row["name"], row["body"]) if row else 0


def is_empty() -> bool:
    conn = _conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0
    finally:
        conn.close()


def remove(name: str) -> None:
    conn = _conn()
    try:
        document_id, display_name = _identity(conn, name)
        old = [r[0] for r in conn.execute(
            "SELECT id FROM chunks WHERE document_id = ? OR "
            "(document_id IS NULL AND name = ?)", (document_id, display_name),
        )]
        for cid in old:
            conn.execute("DELETE FROM vec_chunks WHERE chunk_id = ?", (cid,))
            # `chunks_fts` était OUBLIÉ ici. `_index_file()` fait bien les trois
            # suppressions ; ce chemin-ci n'en faisait que deux, et le TEXTE
            # d'un document supprimé restait dans l'index lexical. Le résultat
            # fantôme était filtré à la jointure finale, mais il consommait une
            # place du top-50 et survivait à la suppression jusqu'au prochain
            # `reindex_all()`.
            conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (cid,))
        conn.execute(
            "DELETE FROM chunks WHERE document_id = ? OR "
            "(document_id IS NULL AND name = ?)", (document_id, display_name),
        )
        conn.commit()
    finally:
        conn.close()


# ---------- Recherche KNN ----------

# Le KNN sqlite-vec applique `k` AVANT le WHERE : pour filtrer par projet sans
# perdre de résultats, on élargit le KNN puis on tronque après filtrage.
_PROJECT_OVERSAMPLE = 8
# Un facteur FIXE ne suffit pas : dans un gros corpus, un petit projet peut
# n'avoir aucun chunk parmi les k*8 plus proches. On réessaie donc en élargissant
# jusqu'à obtenir assez de résultats (ou à avoir balayé tout l'index).


def _total_chunks(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]


def _knn(conn, select: str, qvec, k: int, clause: str, extra: list, want: int) -> list:
    """KNN avec élargissement progressif tant que le filtre projet affame le résultat."""
    total = _total_chunks(conn)
    inner = k if not clause else k * _PROJECT_OVERSAMPLE
    rows: list = []
    while True:
        inner = min(inner, total) or 1
        rows = conn.execute(
            select + " WHERE v.embedding MATCH ? AND k = ?" + clause + " ORDER BY v.distance",
            (sqlite_vec.serialize_float32(qvec), inner, *extra),
        ).fetchall()
        if not clause or len(rows) >= want or inner >= total:
            break
        inner *= _PROJECT_OVERSAMPLE
    return rows


def _project_clause(project_id: project_store.Portee) -> tuple[str, list]:
    condition, valeurs = project_store.filtre_sql("t.project_id", project_id)
    return (f" AND {condition}", valeurs) if condition else ("", [])



# ---------- Fusion lexical + dense ----------

# Amortissement des tout premiers rangs : sans lui, un premier rang dans une
# liste écraserait tout le vote de l'autre.
#
# k est pris dans une région basse de la grille k × profondeur, mesurée sur la
# VRAIE `retrieve` (rappel à 5 et MRR). Au-delà d'une trentaine, k cesse d'agir :
# les valeurs plus hautes rendent toutes la même chose, et l'ancien défaut (60)
# était sur ce PLATEAU. Dans la région basse, les écarts sont trop faibles pour
# discriminer ; on choisit donc sur un critère que la moyenne globale masquait,
# l'ARBITRAGE entre questions reformulées et termes littéraux.
#
# 30 est retenu parce qu'il ne dégrade AUCUNE sous-population. Un gain net payé
# par une régression sur les termes littéraux serait le genre de compromis qu'on
# découvre six mois plus tard, en se demandant pourquoi une recherche exacte ne
# trouve plus.
RRF_K = 30

# Profondeur prise dans CHAQUE liste avant fusion.
#
# C'est le signal NET du même balayage : une profondeur plus faible coûte du
# rappel à TOUS les k ; une profondeur plus forte n'en rend presque pas, pour un
# coût qui, lui, croît avec le corpus.
#
# Ne pas descendre sous 50 sans refaire la mesure. `test_semantic.py` en garde
# le plancher.
RRF_PROFONDEUR = 50


def _requete_lexicale(q: str) -> str:
    """Traduit une question en requête FTS5 tolérante.

    Chaque mot devient un préfixe, réunis par OR. Deux raisons, et la seconde
    est celle qui compte : une requête FTS5 brute lève sur la moindre
    apostrophe, et l'AND implicite de FTS5 ne rendrait presque rien sur une
    question reformulée — or c'est justement le régime où le dense prend le
    relais. Le lexical doit ici ratisser, pas trancher : c'est la fusion qui
    tranche.
    """
    mots = [m for m in re.findall(r"[\w']{3,}", q.lower()) if m]
    return " OR ".join(f'"{m}"*' for m in mots) or '""'


def _rangs_lexicaux(conn: sqlite3.Connection, q: str,
                    profondeur: int = RRF_PROFONDEUR,
                    clause: str = "", extra: tuple = ()) -> list[int]:
    """`chunk_id` classés par BM25, du meilleur au moins bon — DÉJÀ filtrés.

    Le plafond tombait AUTREFOIS avant le filtre projet : les cinquante
    meilleurs du corpus ENTIER étaient prélevés, puis on écartait ceux d'un
    autre projet ou les comptes-rendus exclus du RAG. Un corpus où un autre
    projet domine sur les mots de la requête consommait donc les cinquante
    places, et la recherche cadrée retombait en SILENCE sur le dense seul.

    Ce n'était pas une fuite de résultat — rien d'interdit ne sortait — mais une
    famine : le bon morceau autorisé n'atteignait jamais la fusion. Le chemin
    dense a son élargissement progressif (`_knn`) pour exactement ce motif ; le
    lexical n'en avait pas l'équivalent.

    Le filtre entre maintenant DANS la requête, par la jointure
    `chunks_fts → chunks → transcripts`. Le plafond porte alors sur les
    candidats AUTORISÉS, qui sont les seuls qui comptent.
    """
    try:
        return [r[0] for r in conn.execute(
            "SELECT f.rowid FROM chunks_fts f "
            "JOIN chunks c ON c.id = f.rowid "
            "JOIN transcripts t ON t.id = c.document_id "
            "OR (c.document_id IS NULL AND t.name = c.name) "
            "WHERE chunks_fts MATCH ?" + clause + " "
            "ORDER BY bm25(chunks_fts) LIMIT ?",
            (_requete_lexicale(q), *extra, profondeur))]
    except sqlite3.OperationalError as exc:
        # Une requête que FTS5 refuse ne doit pas faire tomber la recherche :
        # le dense seul reste utilisable, et c'est ce que l'utilisateur avait
        # avant l'hybride.
        print(f"[semantic] requête lexicale refusée ({exc}) — dense seul ce tour")
        return []


def _fusion_rrf(*listes: list[int], k: int | None = None) -> list[int]:
    """Reciprocal Rank Fusion : chaque liste vote 1/(k + rang).

    On fusionne des RANGS et non des scores, parce qu'un BM25 et un cosinus ne
    sont pas comparables — les normaliser demanderait une calibration par corpus
    qui se périmerait au premier document ajouté.

    Deux régimes de requête : la question reformulée et le terme littéral.
    Aucune stratégie pure n'est acceptable : le dense s'effondre sur les termes
    exacts, le lexical sur la paraphrase. La complémentarité est asymétrique —
    sur le régime littéral, le lexical rattrape une grande part de ce que le dense
    rate, et sur le régime reformulé il n'en rattrape rien. C'est la robustesse
    au pire cas qui justifie la fusion, pas un gain moyen.
    """
    score: dict[int, float] = {}
    # Lu ICI, pas en défaut d'argument. `k: int = RRF_K` gelait la valeur dans
    # `__kwdefaults__` à l'import : changer `RRF_K` ne changeait RIEN, et aucun
    # des deux appelants ne passe `k=`. Symptôme : un balayage des seuils rend
    # des lignes identiques, comme si k n'avait aucun effet.
    amortissement = RRF_K if k is None else k
    for liste in listes:
        for rang, chunk_id in enumerate(liste, start=1):
            score[chunk_id] = score.get(chunk_id, 0.0) + 1.0 / (amortissement + rang)
    return sorted(score, key=lambda cid: -score[cid])


def search(query: str, k: int = 24, project_id: project_store.Portee = None) -> list[dict]:
    """Recherche HYBRIDE : dense et lexical fusionnés, puis dédup par document.

    `project_id=None` = tout le corpus ; sinon restreint à ce projet, ou à la
    collection des projets d'un compte.

    Cette fonction est restée dense pure quand `retrieve` est passée à la
    fusion — l'onglet Recherche subissait donc encore la faiblesse du dense sur
    les requêtes par terme exact, alors que c'est précisément là qu'un
    utilisateur tape un nom propre. Même moteur que `retrieve`.
    """
    query = (query or "").strip()
    if not query:
        return []
    _check_index_compatible()
    qvec = _embed_query(query)
    clause, extra = _project_clause(project_id)
    colonnes = (
        "SELECT c.id AS chunk_id, c.name, c.ts, c.text, t.type, "
        "  t.id AS document_id, t.meeting_id, t.project_id, "
        f"  t.title, {library.SOURCE_AFFICHEE_SQL} AS source, t.language, t.duration, t.project, "
        "  t.diarization, t.profiled, t.n_speakers "
    )
    conn = _conn()
    try:
        # Tirer `k` seulement suffirait pour le dense, mais la fusion doit voir
        # des candidats qu'aucune des deux listes ne place dans son propre
        # top-k. La dédup par document consomme en plus plusieurs morceaux par
        # résultat : sans profondeur, on rendrait moins de `k` documents.
        profondeur = max(k, RRF_PROFONDEUR)
        denses = _knn(
            conn,
            colonnes + "FROM vec_chunks v "
            "JOIN chunks c ON c.id = v.chunk_id "
            "JOIN transcripts t ON t.id = c.document_id "
            "OR (c.document_id IS NULL AND t.name = c.name)",
            qvec[0], profondeur, clause, extra, want=profondeur,
        )
        connus = {r["chunk_id"]: r for r in denses}
        lexicaux = _rangs_lexicaux(conn, query, profondeur, clause, extra)
        manquants = [cid for cid in lexicaux if cid not in connus]
        if manquants:
            marques = ",".join("?" * len(manquants))
            for r in conn.execute(
                colonnes + "FROM chunks c "
                "JOIN transcripts t ON t.id = c.document_id "
                "OR (c.document_id IS NULL AND t.name = c.name) "
                f"WHERE c.id IN ({marques})" + clause,
                (*manquants, *extra),
            ):
                connus[r["chunk_id"]] = r
        lexicaux = [cid for cid in lexicaux if cid in connus]
        ordre = _fusion_rrf([r["chunk_id"] for r in denses], lexicaux)
    finally:
        conn.close()

    return _dedup_by_transcript(connus[cid] for cid in ordre)[:k]


def origine(type_document: Optional[str]) -> str:
    """D'où vient un passage : `transcript` (parole transcrite) ou `compte-rendu` (généré)."""
    return "compte-rendu" if type_document == "report" else "transcript"


def retrieve(query: str, k: int = 6, project_id: project_store.Portee = None) -> list[dict]:
    """Retrieval HYBRIDE pour le RAG : top-k passages, dense et lexical fusionnés.

    `project_id` restreint le retrieval → le RAG ne répond que sur ce projet.

    Pourquoi les deux plutôt qu'un — cf. `_fusion_rrf` : le dense rate une
    grande part des recherches par terme exact, le lexical une grande part des
    questions reformulées, et leurs échecs ne se recouvrent pas. Aucune stratégie pure
    n'est acceptable sur le pire des deux régimes.
    """
    query = (query or "").strip()
    if not query:
        return []
    _check_index_compatible()
    qvec = _embed_query(query)
    project_clause, extra = _project_clause(project_id)
    # Les comptes-rendus sont des synthèses LLM. Ils restent recherchables dans
    # l'onglet sémantique, mais ne deviennent jamais des preuves du RAG par
    # défaut : les citations doivent remonter aux transcripts.
    clause = " AND COALESCE(t.type, '') != 'report'" + project_clause
    # La sensibilité de chaque passage est celle de sa RÉUNION, calculée hors SQL par
    # `library.sensibilite_effective` (registre des réunions, sinon le plus strict de
    # ses documents) : un seul calcul pour le CR, l'ECL, l'Assistant et le MCP, relu à
    # chaque requête, sans réindexation sémantique après un changement.
    colonnes = (
        "SELECT c.id AS chunk_id, c.name, c.ts, c.text, t.title, t.type, "
        "  t.id AS document_id, t.meeting_id, t.project_id, t.project, "
        "  t.sensitivity AS sensibilite_propre "
    )
    conn = _conn()
    try:
        # On tire PLUS profond que `k` de chaque côté : la fusion doit voir des
        # candidats que ni l'un ni l'autre ne place dans son propre top-k, sinon
        # elle ne ferait que réordonner une liste déjà tranchée.
        denses = _knn(
            conn,
            colonnes + "FROM vec_chunks v "
            "JOIN chunks c ON c.id = v.chunk_id "
            "JOIN transcripts t ON t.id = c.document_id "
            "OR (c.document_id IS NULL AND t.name = c.name)",
            qvec[0], RRF_PROFONDEUR, clause, extra, want=RRF_PROFONDEUR,
        )
        rangs_denses = [r["chunk_id"] for r in denses]
        lexicaux = _rangs_lexicaux(conn, query, RRF_PROFONDEUR, clause, extra)

        # `chunks_fts` ne porte ni projet ni type : le filtre s'applique ici, en
        # relisant les lignes candidates. Fusionner d'abord et filtrer ensuite
        # laisserait un morceau hors périmètre consommer une place du top-k.
        connus = {r["chunk_id"]: r for r in denses}
        manquants = [cid for cid in lexicaux if cid not in connus]
        if manquants:
            marques = ",".join("?" * len(manquants))
            for r in conn.execute(
                colonnes + "FROM chunks c "
                "JOIN transcripts t ON t.id = c.document_id "
                "OR (c.document_id IS NULL AND t.name = c.name) "
                f"WHERE c.id IN ({marques})" + clause,
                (*manquants, *extra),
            ):
                connus[r["chunk_id"]] = r
        lexicaux = [cid for cid in lexicaux if cid in connus]

        ordre = _fusion_rrf(rangs_denses, lexicaux)
    finally:
        conn.close()
    return [
        {
            "name": r["name"], "title": r["title"], "ts": r["ts"],
            "text": r["text"],
            # Le rang de fusion remplace la similarité : un score RRF n'est pas
            # une distance et ne se compare pas d'une requête à l'autre. Le
            # publier comme « score » ferait croire à une confiance.
            "rang": position,
            "origine": origine(r["type"]),
            "document_id": r["document_id"], "meeting_id": r["meeting_id"],
            "project_id": r["project_id"],
            "project": r["project"] or library.DEFAULT_PROJECT,
            # Sans elle, `rag.ask` lisait `None`, normalisé en `standard` : un
            # passage ultra-confidentiel partait vers un fournisseur cloud.
            "sensitivity": library.sensibilite_effective(r["sensibilite_propre"], r["meeting_id"],
                                                         r["project_id"]),
        }
        for position, r in enumerate(
            (connus[cid] for cid in ordre[:k]), start=1)
    ]


def _dedup_by_transcript(rows) -> list[dict]:
    """Un résultat par document : le premier passage rencontré.

    Les lignes arrivent DÉJÀ classées par la fusion, donc « premier » vaut
    « meilleur » — comme avant, où elles arrivaient classées par distance.
    """
    seen: dict[str, dict] = {}
    for r in rows:
        if r["name"] in seen:
            continue
        text = r["text"] or ""
        seen[r["name"]] = {
            "name": r["name"],
            "document_id": r["document_id"],
            "meeting_id": r["meeting_id"],
            "project_id": r["project_id"],
            "title": r["title"],
            "origine": origine(r["type"]),
            "source": r["source"],
            "language": r["language"],
            "duration": r["duration"],
            "diarization": bool(r["diarization"]),
            "profiled": bool(r["profiled"]),
            "n_speakers": r["n_speakers"],
            "ts": r["ts"],
            "snippet": text[:240] + ("…" if len(text) > 240 else ""),
            # Rang de fusion, PAS une similarité. L'interface affichait
            # `score * 100` en pourcentage, ce qui se lit comme une confiance.
            # Une fois deux classements fusionnés cette confiance n'existe
            # plus : un résultat classé premier pourrait afficher 45 % pendant
            # que le second afficherait 60 %, puisque la distance dense ne
            # décide plus seule de l'ordre. Un chiffre faux vaut moins que pas
            # de chiffre — et le rang est déjà porté par l'ordre de la liste.
            "rang": len(seen) + 1,
        }
    return list(seen.values())
