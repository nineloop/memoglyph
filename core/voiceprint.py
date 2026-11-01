"""Signature vocale : relier les locuteurs ENTRE fichiers (empreinte / voiceprint).

Chaque diarisation produit un embedding (256 d) par locuteur, stocké dans une
**annexe** `output/.voiceprints.json` (et NON dans le front-matter du `.md`, qui
resterait illisible : ~1,4 ko de base64 par locuteur). Ce module tient un
**registre de voix globales** dans l'index partagé (`index.db`) et rattache chaque locuteur
local (« Speaker N » d'un transcript) à une voix globale par **clustering online**
(cosinus vs centroïdes, seuil `VOICE_THRESHOLD`).

Clustering **incrémental** (pas de recluster global) : les ids et libellés de voix
sont stables — nommer une voix (« Ari ») survit à l'indexation d'autres fichiers.

Voix par compte : une voix appartient au propriétaire des projets de ses
documents, sans être écrite nulle part ; le propriétaire se lit dans les
manifestes. Une empreinte n'est rattachée qu'à une voix dont tous les membres
sont dans les projets du même propriétaire, et le nom d'une voix qui déborde
d'un compte n'est jamais rendu à ce compte. Une voix mêlée d'avant ce cloisonnement
est scindée à la réindexation (`_scinder_voix_melees`).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections import Counter
from math import sqrt
from typing import Optional

from pathlib import Path

from . import docid, names, resources, sqlite_db
from . import projects as project_store
from .config import OUTPUT_DIR, VOICE_EMBED_DIM, VOICE_THRESHOLD
from .writer_md import decode_embedding, encode_embedding

# MÊME base que l'index texte et sémantique : un chemin propre à ce module
# isolerait le registre vocal de l'index. Le chemin canonique vit dans
# `sqlite_db`, sous un nom générique, une seule fois.
DB_PATH = sqlite_db.DB_PATH

# Les empreintes vivent dans un fichier annexe, PAS dans le front-matter du .md
# (un vecteur = ~1,4 ko de base64 par locuteur : illisible dans le document).
# Le .md reste lisible, et le registre reste reconstructible sans re-diariser.
SIDECAR = OUTPUT_DIR / ".voiceprints.json"

# L'atomicité protège d'un plantage, PAS d'une course : deux jobs peuvent être
# actifs en même temps et lire la même version de l'annexe avant d'écrire — la
# seconde écriture écraserait l'entrée de la première. Le verrou couvre donc la
# séquence complète lecture → modification → écriture.
# (Mono-processus : suffisant ici ; un verrou fichier serait nécessaire en multi.)
_SIDECAR_LOCK = threading.RLock()


def _sidecar_path(root: Optional["Path"] = None) -> "Path":
    return (root / ".voiceprints.json") if root is not None else SIDECAR


class SidecarIllisible(RuntimeError):
    """L'annexe existe mais n'a pas pu être lue (JSON corrompu, droits, I/O).

    Distinct d'« annexe absente », qui est un état normal et vaut `{}`.
    """


def _read_sidecar(root: Optional["Path"] = None, *, strict: bool = False) -> dict:
    """Lit l'annexe des empreintes.

    `strict` est OBLIGATOIRE avant toute réécriture. Sans lui, une lecture
    ratée renvoyait `{}` : `save_embeddings` réécrivait alors l'annexe avec sa
    SEULE entrée et détruisait les empreintes de tous les autres documents,
    tandis que `drop_embeddings` devenait un no-op muet dont la purge concluait
    au succès. On n'écrit jamais par-dessus une lecture qu'on n'a pas su faire.
    """
    p = _sidecar_path(root)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001 — JSON corrompu, droits, disque
        if strict:
            raise SidecarIllisible(f"annexe des empreintes illisible ({p}) : {exc}") from exc
        print(f"[voix] annexe illisible ({p}) — empreintes ignorées ce tour")
        return {}
    if not isinstance(data, dict):
        if strict:
            raise SidecarIllisible(f"annexe des empreintes malformée ({p})")
        return {}
    return data


def _write_sidecar(data: dict, root: Optional["Path"] = None) -> None:
    """Écriture ATOMIQUE : un plantage en cours d'écriture ne doit pas laisser
    un fichier tronqué (on perdrait toutes les empreintes du corpus).

    Nom temporaire UNIQUE : un nom fixe créerait une course sur `replace()`
    entre deux écritures simultanées.
    """
    p = _sidecar_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".json.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    finally:
        tmp.unlink(missing_ok=True)


def save_embeddings(
    name: str, embeddings: dict[str, list[float]], root: Optional["Path"] = None
) -> None:
    """Enregistre les empreintes d'un transcript dans le fichier annexe.

    Les vecteurs de dimension inattendue sont REFUSÉS : sinon ils polluent le
    registre et se comparent silencieusement aux autres (zip tronque).
    """
    if not embeddings:
        return
    valid = {
        k: encode_embedding(list(v))
        for k, v in embeddings.items()
        if v and len(v) == VOICE_EMBED_DIM
    }
    ignored = [k for k, v in embeddings.items() if v and len(v) != VOICE_EMBED_DIM]
    if ignored:
        print(f"[voix] {name} : empreintes de dimension inattendue ignorées → {ignored}")
    if not valid:
        return
    key = _canonical_key(name, root)
    with _SIDECAR_LOCK:
        # strict : ne JAMAIS réécrire par-dessus une lecture ratée, sous peine
        # d'effacer les empreintes de tous les autres documents.
        data = _read_sidecar(root, strict=True)
        data[key] = valid
        _write_sidecar(data, root)


def _canonical_key(ref: str, root: Optional["Path"] = None) -> str:
    if docid.est_valide(ref) or root is not None:
        return ref
    try:
        from . import library
        item = library.get_transcript(ref)
        identifiant = (item or {}).get("meta", {}).get("id")
        return identifiant if docid.est_valide(identifiant) else ref
    except Exception:  # noqa: BLE001 — compatibilité avec un store non initialisé
        return ref


def load_embeddings(name: str, root: Optional[Path] = None) -> dict[str, list[float]]:
    """Empreintes d'un transcript depuis le fichier annexe (dimensions validées)."""
    data = _read_sidecar(root)
    key = _canonical_key(name, root)
    raw = data.get(key) or data.get(name) or {}
    out: dict[str, list[float]] = {}
    for label, b64 in raw.items():
        try:
            vec = decode_embedding(str(b64))
        except Exception:  # noqa: BLE001
            continue
        if len(vec) == VOICE_EMBED_DIM:  # écarte les empreintes corrompues
            out[str(label)] = vec
    return out


def drop_embeddings(name: str, root: Optional[Path] = None) -> None:
    with _SIDECAR_LOCK:
        # strict : une annexe illisible rendait `drop_embeddings` inopérant SANS
        # lever — la purge concluait alors au succès en laissant les empreintes.
        data = _read_sidecar(root, strict=True)
        changed = data.pop(_canonical_key(name, root), None) is not None
        changed = data.pop(name, None) is not None or changed
        if changed:
            _write_sidecar(data, root)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS voices (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Identité STABLE (ULID), miroir de `output/.voices.json`. L'`id` entier
    -- reste la clé technique interne, mais il NAÎT EN BASE : après une
    -- reconstruction, « voix 3 » ne désigne plus la même personne. Le `uid`,
    -- lui, survit — c'est lui que portent les décisions humaines.
    uid          TEXT,
    label        TEXT,             -- nom de la voix (NULL = anonyme « Voix N »)
    label_source TEXT,             -- 'auto' (déduit du profil) | 'manual' | NULL
    centroid     TEXT,             -- embedding moyen (base64 float32)
    n_members    INTEGER DEFAULT 0,
    created_at   REAL
);
CREATE TABLE IF NOT EXISTS voice_members (
    name          TEXT NOT NULL,   -- transcript (.md)
    document_id   TEXT,            -- identité stable du document
    speaker_local TEXT NOT NULL,   -- « Speaker 1 » … local au transcript
    voice_id      INTEGER,
    embedding     TEXT,            -- base64 float32
    role_hint     TEXT,            -- rôle inféré par le profil LLM (ex. "interviewee (Lina)")
    PRIMARY KEY (name, speaker_local)
);
CREATE INDEX IF NOT EXISTS idx_vmembers_voice ON voice_members(voice_id);
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite_db.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    # Migration douce des bases antérieures (colonnes ajoutées après coup).
    vcols = {r["name"] for r in conn.execute("PRAGMA table_info(voices)")}
    if "label_source" not in vcols:
        conn.execute("ALTER TABLE voices ADD COLUMN label_source TEXT")
    if "uid" not in vcols:
        conn.execute("ALTER TABLE voices ADD COLUMN uid TEXT")
    mcols = {r["name"] for r in conn.execute("PRAGMA table_info(voice_members)")}
    if "role_hint" not in mcols:
        conn.execute("ALTER TABLE voice_members ADD COLUMN role_hint TEXT")
    if "document_id" not in mcols:
        conn.execute("ALTER TABLE voice_members ADD COLUMN document_id TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vmembers_document ON voice_members(document_id)"
    )
    try:
        conn.execute(
            "UPDATE voice_members SET document_id = ("
            "SELECT t.id FROM transcripts t WHERE t.name = voice_members.name"
            ") WHERE document_id IS NULL"
        )
    except sqlite3.OperationalError:
        pass  # registre isolé legacy, sans table transcripts
    return conn


def _identity(
    conn: sqlite3.Connection, ref: str, display_name: Optional[str] = None,
) -> tuple[str, str]:
    if docid.est_valide(ref):
        if display_name:
            return ref, display_name
        try:
            row = conn.execute(
                "SELECT name FROM transcripts WHERE id = ?", (ref,)
            ).fetchone()
        except sqlite3.OperationalError:
            row = None
        return ref, row["name"] if row else ref
    try:
        row = conn.execute(
            "SELECT id, name FROM transcripts WHERE name = ?", (ref,)
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    if row and row["id"]:
        return row["id"], display_name or row["name"]
    return ref, display_name or ref


# ---------- Nom auto depuis le rôle inféré par le profil LLM ----------

def _reliable_names(conn: sqlite3.Connection, transcript: str) -> dict[str, str]:
    """{speaker_local -> nom} retenus pour un transcript, après garde-fous.

    La règle elle-même vit dans `core/names.py` : elle est aussi appliquée avant
    l'écriture du `.md`, et deux copies divergeraient en silence.
    """
    roles = {
        r["speaker_local"]: r["role_hint"]
        for r in conn.execute(
            "SELECT speaker_local, role_hint FROM voice_members WHERE name = ?",
            (transcript,),
        )
    }
    return names.reliable_names(roles)


def suggested_name(
    conn: sqlite3.Connection, voice_id: int, project_id: project_store.Portee = None,
) -> Optional[str]:
    """Nom SUGGÉRÉ pour une voix, déduit des rôles inférés par le profil LLM.

    ⚠️ C'est une **hypothèse**, jamais un fait : le LLM hallucine régulièrement
    des célébrités à partir du sujet (une interview d'Hugo Lambert sur son dernier
    film a produit « filmmaker (Simon Royer) »). Rien ne distingue
    syntaxiquement une invention d'un vrai nom → on ne nomme JAMAIS une voix
    automatiquement, on propose et l'utilisateur valide en un clic.
    """
    names: list[str] = []
    sql = "SELECT m.name, m.speaker_local FROM voice_members m"
    params: list = [voice_id]
    condition, valeurs = project_store.filtre_sql("t.project_id", project_id)
    if condition:
        sql += (
            " JOIN transcripts t ON t.id = m.document_id "
            "OR (m.document_id IS NULL AND t.name = m.name)"
        )
    sql += " WHERE m.voice_id = ?"
    if condition:
        sql += f" AND {condition}"
        params += valeurs
    for r in conn.execute(sql, params):
        n = _reliable_names(conn, r["name"]).get(r["speaker_local"])
        if n:
            names.append(n)
    if not names:
        return None
    # plus fréquent, tie -> ordre alpha (déterministe)
    return sorted(Counter(names).items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


# ---------- Algèbre (pur Python : peu de voix, pas besoin de sqlite-vec) ----------

def _cosine(a: list[float], b: list[float]) -> float:
    """Cosinus de deux empreintes. Des dimensions différentes ne sont PAS
    comparables : `zip` tronquerait en silence et donnerait un score crédible
    mais faux (une empreinte corrompue pourrait ainsi rejoindre une voix)."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sqrt(sum(x * x for x in a))
    nb = sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _mean(vectors: list[list[float]]) -> list[float]:
    n = len(vectors)
    if not n:
        return []
    dim = len(vectors[0])
    return [sum(v[i] for v in vectors) / n for i in range(dim)]


# ---------- Rattachement d'une voix ----------

_JOINTURE_DOCUMENT = (
    "LEFT JOIN transcripts t ON t.id = m.document_id "
    "OR (m.document_id IS NULL AND t.name = m.name)"
)


def _membre_hors(projets: project_store.Portee) -> tuple[str, list]:
    """Sous-requête : la voix `v` a-t-elle un membre hors de `projets` ?

    Un membre dont le document n'est plus indexé compte comme « hors » : on ne
    peut pas affirmer qu'il est dans le compte.
    """
    condition, valeurs = project_store.filtre_sql("t.project_id", projets)
    return (
        f"SELECT 1 FROM voice_members m {_JOINTURE_DOCUMENT} "
        f"WHERE m.voice_id = v.id AND (t.project_id IS NULL OR NOT ({condition or '1'}))",
        valeurs,
    )


def _best_voice(
    conn: sqlite3.Connection, vec: list[float], exclude: set[int],
    projets: project_store.Portee = None,
) -> tuple[Optional[int], float]:
    """Voix au cosinus max avec `vec` (hors `exclude`). (voice_id|None, sim).

    `projets` : les projets d'un même propriétaire ; seules les voix dont tous
    les membres y sont restent candidates. `None` : toutes les voix.
    """
    best_id, best_sim = None, -1.0
    sql, params = "SELECT v.id, v.centroid FROM voices v", []
    if projets is not None:
        hors, params = _membre_hors(projets)
        sql += f" WHERE NOT EXISTS ({hors})"
    for row in conn.execute(sql, params):
        if row["id"] in exclude or not row["centroid"]:
            continue
        sim = _cosine(vec, decode_embedding(row["centroid"]))
        if sim > best_sim:
            best_id, best_sim = row["id"], sim
    return best_id, best_sim


def _recompute_centroid(conn: sqlite3.Connection, voice_id: int) -> None:
    """Recalcule centroïde + n_members d'une voix depuis ses membres ; purge si vide."""
    embs = [
        decode_embedding(r["embedding"])
        for r in conn.execute(
            "SELECT embedding FROM voice_members WHERE voice_id = ? AND embedding IS NOT NULL",
            (voice_id,),
        )
    ]
    if not embs:
        conn.execute("DELETE FROM voices WHERE id = ?", (voice_id,))
        return
    conn.execute(
        "UPDATE voices SET centroid = ?, n_members = ? WHERE id = ?",
        (encode_embedding(_mean(embs)), len(embs), voice_id),
    )


def index_file(
    document_ref: str,
    embeddings: dict[str, list[float]],
    *,
    name: Optional[str] = None,
    roles: Optional[dict[str, str]] = None,
    now: float = 0.0,
    projets: project_store.Portee = None,
) -> dict:
    """(Ré)indexe les empreintes d'un transcript et rattache ses locuteurs.

    `embeddings` : {« Speaker N » -> vecteur 256 d}. Clustering online incrémental.
    `roles` : {« Speaker N » -> rôle inféré par le profil} → nom AUTO de la voix.
    `projets` : les projets du propriétaire du document ; un locuteur ne rejoint
    qu'une voix de ce propriétaire.
    Retourne {speaker_local -> voice_id}. Idempotent (remove + réassign).
    """
    roles = roles or {}
    conn = _conn()
    try:
        document_id, display_name = _identity(conn, document_ref, name)
        # Détache d'abord les anciennes occurrences SANS supprimer leurs voix.
        # Leurs centroïdes restent ainsi disponibles pendant le reclustering :
        # réindexer un même fichier avec les mêmes empreintes conserve les ids et
        # les libellés utilisateur. Les voix réellement orphelines sont purgées
        # seulement une fois les nouvelles affectations terminées.
        old_voice_ids = {
            r["voice_id"]
            for r in conn.execute(
                "SELECT DISTINCT voice_id FROM voice_members "
                "WHERE document_id = ? OR (document_id IS NULL AND name = ?)",
                (document_id, display_name),
            )
            if r["voice_id"] is not None
        }
        conn.execute(
            "DELETE FROM voice_members WHERE document_id = ? "
            "OR (document_id IS NULL AND name = ?)",
            (document_id, display_name),
        )

        assigned: dict[str, int] = {}
        claimed: set[int] = set()  # une voix ne peut coiffer 2 locuteurs du même fichier
        for speaker_local, vec in embeddings.items():
            if not vec:
                continue
            role_hint = roles.get(speaker_local)
            vid, sim = _best_voice(conn, vec, exclude=claimed, projets=projets)
            if vid is not None and sim >= VOICE_THRESHOLD:
                conn.execute(
                    "INSERT INTO voice_members (name, document_id, speaker_local, voice_id, embedding, role_hint) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (display_name, document_id, speaker_local, vid, encode_embedding(vec), role_hint),
                )
                _recompute_centroid(conn, vid)
            else:
                # Avant de forger une identité neuve : ce locuteur appartenait-il
                # déjà à une voix connue ? C'est ce qui rend une reconstruction
                # non destructive — le groupe reconstitué récupère son identité
                # et son nom au lieu de repartir anonyme.
                uid, label_repris = _identite_heritee(conn, document_id, speaker_local)
                if uid is None:
                    # L'identité naît dans le magasin autoritatif, pas ici :
                    # l'index doit rester intégralement reconstructible.
                    uid = _registre().nouvelle_voix()
                cur = conn.execute(
                    "INSERT INTO voices (uid, label, label_source, centroid, n_members, created_at) "
                    "VALUES (?, ?, ?, ?, 1, ?)",
                    (uid, label_repris[0], label_repris[1], encode_embedding(vec), now),
                )
                vid = cur.lastrowid
                conn.execute(
                    "INSERT INTO voice_members (name, document_id, speaker_local, voice_id, embedding, role_hint) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (display_name, document_id, speaker_local, vid, encode_embedding(vec), role_hint),
                )
            claimed.add(vid)
            assigned[speaker_local] = vid
            # Consigner les membres MAINTENANT : l'identité est créée avant que
            # le locuteur soit rattaché, ce qui laisserait sinon une entrée sans
            # membres — donc une voix impossible à retrouver après reconstruction.
            _consigner_membres(conn, vid)

        # Met à jour ou supprime les anciennes voix qui n'ont pas été réutilisées.
        for vid in old_voice_ids - claimed:
            _recompute_centroid(conn, vid)
        # Pas de nommage automatique : les rôles LLM ne servent que de SUGGESTION
        # (cf. suggested_name) — on ne grave jamais une hypothèse dans le registre.
        conn.commit()
        return assigned
    finally:
        conn.close()


def embeddings_from_meta(meta: dict) -> dict[str, list[float]]:
    """Décode le bloc `speaker_embeddings` du front-matter -> {Speaker N -> vecteur}."""
    raw = meta.get("speaker_embeddings") if isinstance(meta, dict) else None
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[float]] = {}
    for label, b64 in raw.items():
        try:
            out[str(label)] = decode_embedding(str(b64))
        except Exception:  # noqa: BLE001
            continue
    return out


def roles_from_meta(meta: dict) -> dict[str, str]:
    """Rôles inférés par le profil LLM : {Speaker N -> rôle} depuis `speakers:`."""
    raw = meta.get("speakers") if isinstance(meta, dict) else None
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for label, info in raw.items():
        role = info.get("role") if isinstance(info, dict) else None
        if role:
            out[str(label)] = str(role)
    return out


def index_name(name: str, *, now: float = 0.0) -> dict:
    """Lit le .md, extrait les empreintes du front-matter et (ré)indexe la voix."""
    with resources.heavy(label="indexation des voix"):
        return _index_name(name, now=now)


def _index_name(name: str, *, now: float = 0.0) -> dict:
    from . import library  # local (library n'importe pas voiceprint : pas de cycle)

    path = library.transcript_path(name)
    if path is None or not path.exists():
        return {}
    meta, _ = library._split_frontmatter(path.read_text(encoding="utf-8"))
    document_id = meta.get("id") if docid.est_valide(meta.get("id")) else name
    embs = load_embeddings(document_id) or load_embeddings(name)
    if not embs:
        # Legacy : anciens .md porteurs du bloc speaker_embeddings -> on migre.
        embs = embeddings_from_meta(meta)
        if embs:
            save_embeddings(document_id, embs)
    if not embs:
        _clear(document_id)
        return {}
    return index_file(
        document_id, embs, name=path.name, roles=roles_from_meta(meta), now=now,
        projets=_projets_du_meme_compte(path),
    )


def _projets_du_meme_compte(path: Path) -> frozenset[str]:
    """Les projets du propriétaire du document, par le manifeste de son dossier."""
    from . import library

    try:
        return project_store.projets_du_meme_proprietaire(
            library.OUTPUT_DIR, library._project_id_du_dossier(path.parent),
        )
    except project_store.ProjectManifestError:
        return frozenset()


def reindex_all(*, now: float = 0.0, project_id: Optional[str] = None) -> dict:
    """Rattache les voix de tous les transcripts porteurs d'empreintes.

    Ordre stable (par nom) pour un clustering déterministe. Récursif (projets).
    """
    with resources.heavy(label="réindexation des voix"):
        return _reindex_all(now=now, project_id=project_id)


def _reindex_all(*, now: float = 0.0, project_id: Optional[str] = None) -> dict:
    from . import library

    scindees = _scinder_voix_melees()
    known = set(_read_sidecar())
    indexed = 0
    # Un `.md` illisible interrompait la boucle ENTIÈRE. Comme elle indexe au fil
    # de l'eau et par ordre alphabétique, le résultat dépendait du RANG du
    # fichier fautif : reproduit à 1/3, 2/3 ou 3/3 documents rattachés selon son
    # nom. Un registre partiel et arbitraire, sans que rien ne le signale —
    # `_startup_reindex` avalait l'exception dans un `print`.
    #
    # Contrairement à `library.reindex()`, qui refuse en bloc à raison (un index
    # de bibliothèque à moitié reconstruit ferait disparaître des documents), le
    # registre vocal est un ENRICHISSEMENT : l'absence des voix d'un document ne
    # cache pas ce document, et l'écran de maintenance affiche déjà l'écart
    # « empreintes N/M ». On saute donc le fichier fautif, et on le NOMME.
    ignores: list[str] = []
    for path in sorted(library._iter_md(), key=lambda p: p.name):
        try:
            meta, _ = library._split_frontmatter(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — codage invalide, droits, I/O
            ignores.append(f"{path.name} : {exc}")
            continue
        if project_id and meta.get("project_id") != project_id:
            continue
        document_id = meta.get("id")
        has_emb = document_id in known or path.name in known
        if not has_emb:  # legacy : ancien bloc dans le front-matter
            has_emb = bool(embeddings_from_meta(meta))
        if has_emb:
            try:
                index_name(path.name, now=now)
            except Exception as exc:  # noqa: BLE001 — un document ne bloque pas les autres
                ignores.append(f"{path.name} : {exc}")
                continue
            indexed += 1
    # `ignores` est TOUJOURS présent : une clé absente se lirait comme « rien à
    # signaler », alors qu'une liste vide dit « vérifié, rien trouvé ».
    return {"indexed": indexed, "ignores": ignores, "scindees": scindees}


def _scinder_voix_melees() -> int:
    """Rend à chaque compte sa part d'une voix qui mêle plusieurs propriétaires.

    Avant le cloisonnement, une empreinte pouvait rejoindre la voix d'un
    autre compte. La voix garde son identité et son nom du côté du propriétaire
    qui a le plus d'occurrences ; les membres des autres comptes sont détachés,
    et la réindexation qui suit les regroupe chez leur propriétaire, en voix
    anonymes. Les membres dont le document n'est plus indexé ne comptent pour
    personne. Rend le nombre de voix scindées.
    """
    from . import library

    try:
        cle = {
            p.id: (p.owner or "").casefold() if p.owner else None
            for _d, p in project_store.all_projects(library.OUTPUT_DIR)
        }
    except project_store.ProjectManifestError:
        return 0
    conn = _conn()
    scindees = 0
    try:
        try:
            lignes = list(conn.execute(
                f"SELECT m.voice_id, m.name, m.speaker_local, t.project_id "
                f"FROM voice_members m {_JOINTURE_DOCUMENT} WHERE m.voice_id IS NOT NULL"
            ))
        except sqlite3.OperationalError:
            return 0  # registre isolé, sans table transcripts
        par_voix: dict[int, list] = {}
        for ligne in lignes:
            if ligne["project_id"] in cle:
                par_voix.setdefault(ligne["voice_id"], []).append(ligne)
        for voice_id, membres in par_voix.items():
            parts = Counter(cle[m["project_id"]] for m in membres)
            if len(parts) < 2:
                continue
            majoritaire = sorted(parts.items(), key=lambda kv: (-kv[1], kv[0] or ""))[0][0]
            for m in membres:
                if cle[m["project_id"]] != majoritaire:
                    conn.execute(
                        "DELETE FROM voice_members WHERE name = ? AND speaker_local = ?",
                        (m["name"], m["speaker_local"]),
                    )
            _recompute_centroid(conn, voice_id)
            _consigner_membres(conn, voice_id)
            scindees += 1
        conn.commit()
    finally:
        conn.close()
    return scindees


def _clear(name: str) -> None:
    conn = _conn()
    try:
        _remove(conn, name)
        conn.commit()
    finally:
        conn.close()


def _remove(conn: sqlite3.Connection, name: str) -> None:
    document_id, display_name = _identity(conn, name)
    voice_ids = [
        r["voice_id"]
        for r in conn.execute(
            "SELECT DISTINCT voice_id FROM voice_members "
            "WHERE (document_id IS NOT NULL AND document_id = ?) OR name = ? OR name = ?",
            (document_id, name, display_name),
        )
        if r["voice_id"] is not None
    ]
    conn.execute(
        "DELETE FROM voice_members WHERE (document_id IS NOT NULL AND document_id = ?) OR name = ? OR name = ?",
        (document_id, name, display_name),
    )
    for vid in voice_ids:
        _recompute_centroid(conn, vid)


def unindex(name: str) -> None:
    """Détache un transcript du registre de voix SANS toucher à ses empreintes.

    Utilisé à l'archivage : les empreintes (durables) doivent survivre pour
    qu'une restauration soit possible.
    """
    conn = _conn()
    try:
        document_id, display_name = _identity(conn, name)
        voice_ids = [
            r["voice_id"]
            for r in conn.execute(
                "SELECT DISTINCT voice_id FROM voice_members "
                "WHERE name = ? OR name = ? OR (document_id IS NOT NULL AND (document_id = ? OR document_id = ?))",
                (name, display_name, name, document_id),
            )
            if r["voice_id"] is not None
        ]
        conn.execute(
            "DELETE FROM voice_members WHERE name = ? OR name = ? OR (document_id IS NOT NULL AND (document_id = ? OR document_id = ?))",
            (name, display_name, name, document_id),
        )
        for vid in voice_ids:
            _recompute_centroid(conn, vid)
        conn.commit()
    finally:
        conn.close()


def remove(name: str) -> None:
    """Retire un transcript du registre ET supprime ses empreintes (définitif)."""
    unindex(name)
    drop_embeddings(name)


# ---------- Lecture / édition ----------

def _display(voice_id: int, label: Optional[str]) -> str:
    return label or f"Voix {voice_id}"


def _debordantes(conn: sqlite3.Connection, compte: project_store.Portee) -> set[int]:
    """Les voix qui ont un membre hors des projets `compte` : leur nom ne lui est pas rendu."""
    if compte is None:
        return set()
    hors, params = _membre_hors(compte)
    try:
        return {r["id"] for r in conn.execute(
            f"SELECT v.id FROM voices v WHERE EXISTS ({hors})", params)}
    except sqlite3.OperationalError:
        return {r["id"] for r in conn.execute("SELECT id FROM voices")}


def assignments(name: str, compte: project_store.Portee = None) -> dict[str, dict]:
    """Pour un transcript : {speaker_local -> {voice_id, label, display}}.

    `compte` : les projets du compte qui lit ; le nom d'une voix qui en déborde
    est tu (Q11).
    """
    conn = _conn()
    try:
        document_id, display_name = _identity(conn, name)
        taire = _debordantes(conn, compte)
        out: dict[str, dict] = {}
        for r in conn.execute(
            "SELECT m.speaker_local, m.voice_id, v.label, v.label_source "
            "FROM voice_members m LEFT JOIN voices v ON v.id = m.voice_id "
            "WHERE m.document_id = ? OR (m.document_id IS NULL AND m.name = ?)",
            (document_id, display_name),
        ):
            label = None if r["voice_id"] in taire else r["label"]
            out[r["speaker_local"]] = {
                "voice_id": r["voice_id"],
                "label": label,
                "label_source": None if r["voice_id"] in taire else r["label_source"],
                "display": _display(r["voice_id"], label),
            }
        return out
    finally:
        conn.close()


def list_voices(
    project_id: project_store.Portee = None, compte: project_store.Portee = None,
) -> list[dict]:
    """Voix visibles + occurrences enrichies de leur provenance projet.

    `project_id=None` conserve la vue globale explicite ; sinon un projet ou la
    collection des projets d'un compte. Une portée filtre aussi les suggestions
    LLM : un rôle inféré dans un autre projet ne doit jamais fuiter dans la vue
    courante. `compte` : les projets du compte qui lit ; le nom d'une voix qui
    en déborde est tu (Q11).
    """
    conn = _conn()
    try:
        taire = _debordantes(conn, compte)
        voices = {
            r["id"]: {
                "id": r["id"],
                "label": r["label"],
                "label_source": r["label_source"],
                "display": _display(r["id"], r["label"]),
                "n_members": r["n_members"],
                "occurrences": [],
            }
            for r in conn.execute(
                "SELECT id, label, label_source, n_members FROM voices ORDER BY n_members DESC, id"
            )
        }
        sql = (
            "SELECT m.name, m.document_id, m.speaker_local, m.voice_id, "
            "t.title, t.source, t.project_id, t.project "
            "FROM voice_members m LEFT JOIN transcripts t "
            "ON t.id = m.document_id OR (m.document_id IS NULL AND t.name = m.name)"
        )
        condition, params = project_store.filtre_sql("t.project_id", project_id)
        if condition:
            sql += f" WHERE {condition}"
        sql += " ORDER BY m.name"
        try:
            members = conn.execute(sql, params)
        except sqlite3.OperationalError:
            if project_id is not None:
                members = []
            else:
                members = conn.execute(
                    "SELECT name, document_id, speaker_local, voice_id, "
                    "NULL AS title, NULL AS source, NULL AS project_id, NULL AS project "
                    "FROM voice_members ORDER BY name"
                )
        for r in members:
            v = voices.get(r["voice_id"])
            if v is not None:
                v["occurrences"].append(
                    {
                        "name": r["name"], "document_id": r["document_id"],
                        "title": r["title"] or r["name"],
                        # Média d'origine : l'interface en tire le titre lisible
                        # quand le titre est resté un slug.
                        "source": r["source"],
                        "speaker_local": r["speaker_local"],
                        "project_id": r["project_id"], "project": r["project"],
                    }
                )
        if project_id is not None:
            voices = {vid: v for vid, v in voices.items() if v["occurrences"]}
            for v in voices.values():
                v["n_members"] = len(v["occurrences"])
        for vid in taire & voices.keys():
            voices[vid].update(label=None, label_source=None, display=_display(vid, None))
        # Suggestion de nom (hypothèse du profil LLM, à valider par l'utilisateur).
        for vid, v in voices.items():
            v["suggestion"] = (
                None if v["label"] else suggested_name(conn, vid, project_id)
            )
        return list(voices.values())
    finally:
        conn.close()


def visible_in_project(voice_id: int, project_id: project_store.Portee) -> bool:
    """Une mutation UI ne peut viser qu'une voix visible dans sa portée."""
    if project_id is None:
        return True
    condition, valeurs = project_store.filtre_sql("t.project_id", project_id)
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM voice_members m JOIN transcripts t "
            "ON t.id = m.document_id OR (m.document_id IS NULL AND t.name = m.name) "
            f"WHERE m.voice_id = ? AND {condition} LIMIT 1",
            (voice_id, *valeurs),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def appartient_au_compte(voice_id: int, compte: project_store.Portee) -> bool:
    """La voix existe, a des membres, et tous sont dans les projets `compte`.

    Garde des mutations : renommer ou fusionner une voix qui déborde du
    compte toucherait la part d'un autre compte.
    """
    hors, params = _membre_hors(compte)
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM voices v WHERE v.id = ? "
            "AND EXISTS (SELECT 1 FROM voice_members m WHERE m.voice_id = v.id) "
            f"AND NOT EXISTS ({hors})",
            (voice_id, *params),
        ).fetchone()
        return row is not None
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


def clear_auto_labels() -> int:
    """Efface les noms posés automatiquement (devenus de simples suggestions).
    Les noms saisis à la main sont conservés. Retourne le nb effacé."""
    conn = _conn()
    try:
        cur = conn.execute(
            "UPDATE voices SET label = NULL, label_source = NULL WHERE label_source = 'auto'"
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


class UnknownVoice(KeyError):
    """Voix inexistante : refuser plutôt que muter le registre à l'aveugle."""


def _registre():
    """Import tardif : `voice_registry` ne doit pas créer de cycle."""
    from . import voice_registry

    return voice_registry


def _uid_de(conn: sqlite3.Connection, voice_id: int) -> Optional[str]:
    row = conn.execute("SELECT uid FROM voices WHERE id = ?", (voice_id,)).fetchone()
    return row["uid"] if row is not None else None


def _membres_de(conn: sqlite3.Connection, voice_id: int) -> list[tuple[str, str]]:
    """`(document_id, speaker_local)` — la seule clé qui survive à tout."""
    return [
        (r["document_id"] or r["name"], r["speaker_local"])
        for r in conn.execute(
            "SELECT name, document_id, speaker_local FROM voice_members "
            "WHERE voice_id = ?", (voice_id,)
        )
    ]


def _identite_heritee(
    conn: sqlite3.Connection, document_id: Optional[str], speaker_local: str,
) -> tuple[Optional[str], tuple[Optional[str], Optional[str]]]:
    """Identité et nom d'une voix connue à laquelle ce locuteur appartenait.

    Retourne `(uid, (label, label_source))`, ou `(None, (None, None))`.

    C'est la pièce qui rend une reconstruction NON DESTRUCTIVE : sans elle, vider
    puis reconstruire le registre redonnait des voix anonymes, et les noms
    restaient dans le magasin sans que rien ne sache à qui les appliquer.

    Une identité déjà revendiquée par une autre voix de la base n'est PAS
    reprise : deux groupes distincts ne peuvent pas être la même personne, et
    dupliquer un `uid` casserait l'appariement dans les deux sens.
    """
    if not document_id and not speaker_local:
        return None, (None, None)
    try:
        registre = _registre()
        uid = registre.identite_pour([(document_id or "", speaker_local)])
        if not uid:
            return None, (None, None)
        deja = conn.execute("SELECT 1 FROM voices WHERE uid = ?", (uid,)).fetchone()
        if deja is not None:
            return None, (None, None)
        return uid, registre.etiquette(uid)
    except Exception as exc:  # noqa: BLE001 — au pire on repart d'une voix anonyme
        print(f"[voix] identité héritée indisponible : {exc}")
        return None, (None, None)


def _consigner_membres(conn: sqlite3.Connection, voice_id: int) -> None:
    """Reporte la composition d'une voix dans le magasin autoritatif.

    Best-effort : le rattachement est déjà en base, l'utilisateur le voit.
    """
    uid = _uid_de(conn, voice_id)
    if not uid:
        return
    try:
        _registre().enregistrer_membres(uid, _membres_de(conn, voice_id))
    except Exception as exc:  # noqa: BLE001
        print(f"[voix] membres non consignés dans le magasin autoritatif : {exc}")


def _consigner_nom(conn: sqlite3.Connection, voice_id: int, label: Optional[str]) -> None:
    """Écrit le nom dans le magasin autoritatif. LÈVE si l'écriture échoue.

    Cette fonction était best-effort et avalait toute exception. Sur disque
    plein, droits, ou magasin illisible, l'API rendait un succès, l'utilisateur
    voyait son nom — et il disparaissait à la réindexation suivante, sans que
    rien ne l'ait annoncé. Or le magasin est AUTORITATIF et SQLite est dérivé :
    la seule des deux écritures qui doit être garantie est celle-ci.

    L'appelant écrit donc ici D'ABORD, et ne mute la base qu'ensuite. Un échec
    laisse les deux magasins cohérents dans leur état antérieur, et l'erreur
    remonte jusqu'à l'utilisateur — qui peut réessayer.
    """
    uid = _uid_de(conn, voice_id)
    if not uid:
        return
    # UNE seule écriture : la décision est publiée entièrement ou pas du tout.
    _registre().nommer_avec_membres(uid, label, _membres_de(conn, voice_id))


def _consigner_fusion(conn: sqlite3.Connection, source_id: int, target_id: int) -> None:
    """Journalise la fusion dans le magasin autoritatif. LÈVE si ça échoue.

    Même raison que `_consigner_nom` : une fusion perdue ici réapparaît en deux
    voix distinctes à la réindexation suivante, alors que l'utilisateur l'a
    décidée et vue appliquée.

    À appeler AVANT toute mutation SQLite : les membres de la source doivent
    être lus tant qu'ils lui appartiennent encore. L'ancien ordre les lisait
    après l'`UPDATE` qui les avait déjà déplacés vers la cible — ça marchait par
    ricochet, la cible les ayant récupérés, mais plus rien ne le garantissait.
    """
    source_uid, cible_uid = _uid_de(conn, source_id), _uid_de(conn, target_id)
    if not source_uid or not cible_uid:
        return
    membres = _membres_de(conn, source_id) + _membres_de(conn, target_id)
    # UNE seule écriture : réessayer après échec n'ajoute pas une seconde
    # entrée au journal append-only pour une seule décision humaine.
    _registre().fusionner_avec_membres(source_uid, cible_uid, membres)


def adopter_identites() -> dict:
    """Donne une identité STABLE aux voix qui n'en ont pas encore.

    Les voix créées avant l'introduction du `uid` — ou par une réindexation qui
    rattache sans créer — n'ont pas d'entrée dans le magasin autoritatif. Elles
    resteraient donc innommables de façon durable : un nom saisi dessus
    disparaîtrait à la reconstruction suivante, exactement le défaut que ce
    magasin existe pour supprimer.

    Idempotent : une voix déjà identifiée n'est pas touchée. Les membres sont
    (re)consignés au passage, puisque c'est par eux qu'une voix se retrouve.
    """
    registre = _registre()
    conn = _conn()
    adoptees, rafraichies = 0, 0
    try:
        for row in list(conn.execute("SELECT id, uid, label FROM voices ORDER BY id")):
            uid = row["uid"]
            if not uid:
                uid = registre.nouvelle_voix()
                conn.execute("UPDATE voices SET uid = ? WHERE id = ?", (uid, row["id"]))
                adoptees += 1
            else:
                rafraichies += 1
            registre.enregistrer_membres(uid, _membres_de(conn, row["id"]))
            if row["label"]:
                # Un nom déjà présent en base doit rejoindre l'autoritatif,
                # sinon la prochaine reconstruction l'efface.
                registre.nommer(uid, row["label"])
        conn.commit()
    finally:
        conn.close()
    return {"adoptees": adoptees, "deja_identifiees": rafraichies}


def _require_voice(conn: sqlite3.Connection, voice_id: int) -> None:
    if conn.execute("SELECT 1 FROM voices WHERE id = ?", (voice_id,)).fetchone() is None:
        raise UnknownVoice(f"Voix {voice_id} inexistante")


def rename(voice_id: int, label: Optional[str]) -> None:
    """Nomme une voix à la main → `label_source='manual'` (override le nom auto).

    Nom vide = on efface et on repasse en AUTO (le profil peut reproposer un nom).
    """
    clean = (label or "").strip()
    conn = _conn()
    try:
        _require_voice(conn, voice_id)
        # AUTORITATIF D'ABORD. La base est dérivée et reconstructible ; le
        # magasin, non. Si l'ordre était inverse et que le magasin refusait
        # l'écriture, l'utilisateur verrait un nom que la prochaine
        # réindexation effacerait — un faux succès, silencieux.
        _consigner_nom(conn, voice_id, clean or None)
        if clean:
            conn.execute(
                "UPDATE voices SET label = ?, label_source = 'manual' WHERE id = ?",
                (clean, voice_id),
            )
        else:
            conn.execute(
                "UPDATE voices SET label = NULL, label_source = NULL WHERE id = ?",
                (voice_id,),
            )
        conn.commit()
    finally:
        conn.close()


def merge_voices(source_id: int, target_id: int) -> None:
    """Fusionne source -> cible (réattribue les membres, recalcule, supprime source)."""
    if source_id == target_id:
        return
    conn = _conn()
    try:
        # Valider AVANT toute mutation : fusionner vers un id inexistant
        # réaffectait les membres à une voix fantôme puis supprimait la source,
        # laissant des occurrences orphelines dans le registre.
        _require_voice(conn, source_id)
        _require_voice(conn, target_id)
        # AUTORITATIF D'ABORD, avant même l'`UPDATE` : la source doit encore
        # porter ses membres et son `uid` au moment où on les journalise. Un
        # échec ici laisse les deux magasins intacts et remonte à l'utilisateur.
        _consigner_fusion(conn, source_id, target_id)
        conn.execute(
            "UPDATE voice_members SET voice_id = ? WHERE voice_id = ?",
            (target_id, source_id),
        )
        conn.execute("DELETE FROM voices WHERE id = ?", (source_id,))
        _recompute_centroid(conn, target_id)
        conn.commit()
    finally:
        conn.close()


def is_empty() -> bool:
    conn = _conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM voice_members").fetchone()[0] == 0
    finally:
        conn.close()
