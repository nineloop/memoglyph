"""Suppression de transcripts — deux opérations distinctes et cohérentes.

Un transcript laisse des traces dans **plusieurs magasins** (fichier, index
SQLite, miroir FTS, chunks sémantiques + vecteurs, registre de voix, empreintes
annexes, archive audio, corbeille). Tout centraliser ici évite d'en oublier un.

- `archive()`   : réversible. Le `.md` part en corbeille et les index DÉRIVÉS
                  sont purgés, mais les artefacts durables (audio, empreintes)
                  sont CONSERVÉS — sinon « récupérable » serait un mensonge.
- `purge_transcript()` : **définitif**. Tout disparaît, corbeille comprise.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from . import docid, ecl, library, media, semantic, sqlite_db, voiceprint


def _trash_copies(name: str) -> list[Path]:
    """Copies de CE transcript dans la corbeille.

    Correspondance **exacte** via le manifeste (ou `foo.md` / `foo.<n>.md` pour
    les archives d'avant) : un préfixe emporterait `foobar.md`, un transcript
    archivé sans rapport.
    """
    trash = library.OUTPUT_DIR / ".trash"
    if not trash.exists():
        return []
    return [trash / n for n in library.archived_names_for(name) if (trash / n).exists()]


def resoudre(name: str) -> dict:
    """Fige l'identité d'un transcript AVANT toute suppression.

    `name` N'EST PAS une identité. `library.transcript_path` résout sur
    `id OR name OR source OR title` (core/library.py:1080) et retourne le
    PREMIER document existant. Deux passes sur le même média partagent `title`
    et `source` : la même chaîne désigne donc successivement deux documents
    différents. Re-résoudre après avoir supprimé le `.md` fait glisser la cible
    vers le suivant — la vérification mesurait un autre document, et un second
    appel en détruisait un que l'utilisateur n'avait jamais désigné.

    On résout donc UNE fois, et tout le reste — suppression comme
    vérification — travaille sur cette cible gelée.
    """
    path = library.transcript_path(name)
    item = library.get_transcript(name)
    document_id = ((item or {}).get("meta") or {}).get("id")
    if not document_id:
        for archived in library.trash_items():
            if archived.get("origin") == name and archived.get("document_id"):
                document_id = archived["document_id"]
                break
    fichier = path.name if path is not None else (
        name if name.endswith(".md") else f"{name}.md"
    )
    return {
        "demande": name,
        "path": path,
        "fichier": fichier,
        "document_id": document_id,
        "identity": document_id or name,
        "ambigu": _homonymes(name),
    }


def _nom_original(archived_name: str) -> str:
    """Nom de fichier d'origine d'une copie de corbeille (`foo.2.md` -> `foo.md`).

    Le manifeste ne retient que la chaîne d'appel (`origin`), qui peut être un
    titre ou un stem. Le NOM DE FICHIER, lui, est reconstructible depuis le nom
    d'archive — et c'est la seule clé que l'index et les artefacts connaissent.
    """
    tige = Path(archived_name).stem            # « foo.2 » pour « foo.2.md »
    base, _, suffixe = tige.rpartition(".")
    if base and suffixe.isdigit():
        tige = base
    return f"{tige}.md"


def _cle_stockage(reference: str) -> str:
    """Clé sous laquelle les artefacts durables sont rangés (cf. media._storage_keys).

    Un ULID est déjà la clé ; sinon c'est le stem du nom de fichier. Comparer
    ces clés — et non des chaînes hétérogènes — est le seul moyen de savoir si
    deux documents se partagent réellement un audio ou des empreintes.
    """
    if docid.est_valide(reference):
        return reference
    return Path(reference).stem


def _homonymes(name: str) -> list[str]:
    """Documents VIVANTS que cette chaîne désigne, au-delà du premier trouvé.

    Deux passes sur le même média partagent `title` et `source` : une chaîne
    peut donc viser plusieurs documents. `transcript_path` en choisit un
    silencieusement — acceptable pour lire, inacceptable pour détruire.
    """
    conn = sqlite_db.connect(library.DB_PATH)
    try:
        rows = conn.execute(
            "SELECT DISTINCT name FROM transcripts "
            "WHERE id = ? OR name = ? OR source = ? OR title = ?",
            (name, name, name, name),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    noms = [r[0] for r in rows if r[0]]
    return sorted(noms) if len(noms) > 1 else []


def traces(name: str, *, cible: Optional[dict] = None) -> dict:
    """Ce qui existe encore pour ce transcript, magasin par magasin.

    Sert à confirmer une suppression et à VÉRIFIER qu'il ne reste rien après.

    `cible` : identité gelée par `resoudre()` AVANT la suppression. Sans elle,
    la résolution repasse par le `.md` et le manifeste — que la purge vient de
    détruire — et l'on interroge alors une clé DIFFÉRENTE de celle qui a servi
    à purger : un artefact rangé sous l'ULID restait invisible et la purge se
    déclarait propre. On balaie donc toutes les clés connues de la cible ; au
    pire on signale une trace de trop, jamais une de moins.
    """
    c = cible or resoudre(name)
    fichier, identity = c["fichier"], c["identity"]
    # Clés de stockage possibles, sans doublon et sans None. On ajoute la clé
    # DÉRIVÉE (`_cle_stockage`) : un artefact rangé sous le stem restait
    # invisible à une vérification qui n'interrogeait que le nom de fichier.
    brutes = [r for r in (identity, fichier, c["demande"]) if r]
    refs = list(dict.fromkeys(brutes + [_cle_stockage(r) for r in brutes]))

    conn = sqlite_db.connect(library.DB_PATH)
    try:
        count = lambda sql, *params: conn.execute(sql, params).fetchone()[0]  # noqa: E731
        indexed = count("SELECT COUNT(*) FROM transcripts WHERE name = ? OR id = ?", fichier, identity)
        fts = count("SELECT COUNT(*) FROM transcripts_fts WHERE name = ?", fichier)
        try:
            chunks = count("SELECT COUNT(*) FROM chunks WHERE name = ? OR (document_id IS NOT NULL AND document_id = ?)", fichier, identity)
        except sqlite3.OperationalError:
            try:
                chunks = count("SELECT COUNT(*) FROM chunks WHERE name = ?", fichier)
            except sqlite3.OperationalError:
                # Table absente OU illisible : on ne peut pas distinguer les deux.
                # `None` dit « je n'ai pas pu regarder » — is_clean le refusera,
                # là où un 0 aurait fait passer un magasin en erreur pour vide.
                chunks = None
        try:
            voices = count(
                "SELECT COUNT(*) FROM voice_members WHERE name = ? OR (document_id IS NOT NULL AND document_id = ?)",
                fichier,
                identity,
            )
        except sqlite3.OperationalError:
            try:
                voices = count("SELECT COUNT(*) FROM voice_members WHERE name = ?", fichier)
            except sqlite3.OperationalError:
                voices = None
    finally:
        conn.close()

    ecl_faits, ecl_fichier = ecl.traces(c.get("document_id"))
    path = c["path"]
    return {
        # Le chemin GELÉ, pas une nouvelle résolution : sinon on rapporte
        # l'existence du document suivant qui partage titre ou source.
        "file": str(path) if path is not None and path.exists() else None,
        "indexed": indexed,
        "fts": fts,
        "chunks": chunks,
        "voice_members": voices,
        "embeddings": sum(len(voiceprint.load_embeddings(r)) for r in refs),
        "audio": any(media.audio_path(r).exists() for r in refs),
        "faithful": any(media.faithful_path(r).exists() for r in refs),
        "trash_copies": len(_trash_copies(c["demande"])),
        "ecl_faits": ecl_faits,
        "ecl_fichier": ecl_fichier,
    }


def is_clean(name: str, *, cible: Optional[dict] = None) -> bool:
    """Vrai si plus AUCUNE trace du transcript nulle part.

    Un compteur à `None` (magasin illisible) N'EST PAS une absence de trace :
    il empêche de conclure, donc il empêche de déclarer propre.
    """
    t = traces(name, cible=cible)
    return not any([
        t["file"], t["indexed"], t["fts"],
        t["chunks"] is None or t["chunks"],
        t["voice_members"] is None or t["voice_members"],
        t["embeddings"], t["audio"], t["faithful"],
        t["trash_copies"],
        t["ecl_faits"] is None or t["ecl_faits"], t["ecl_fichier"],
    ])


def archive(name: str) -> dict:
    """Archivage RÉVERSIBLE : .md en corbeille + purge des index dérivés.

    L'audio et les empreintes sont conservés pour qu'une restauration soit
    réellement possible. Retourne {archived, errors} : une désindexation ratée
    ne doit pas passer pour un succès (le store resterait incohérent).
    """
    # Gelé AVANT `library.archive` : celle-ci supprime la ligne `transcripts`,
    # après quoi l'ULID n'est plus résolvable depuis la base. On purgeait alors
    # les chunks sous une clé introuvable et l'on annonçait quand même
    # `errors: []` — les chunks et leurs vecteurs survivaient en silence.
    cible = resoudre(name)
    if not library.archive(name):  # déplace le fichier + purge table/FTS
        return {"archived": False, "errors": []}
    errors = _drop_derived(cible["identity"])
    if cible["fichier"] != cible["identity"]:
        errors += _drop_derived(cible["fichier"])
    return {"archived": True, "errors": errors}


def restore(archived_name: str) -> Optional[Path]:
    """Restaure un transcript depuis la corbeille, dans son projet d'origine.

    Sans ça, « archivage récupérable » n'était qu'une promesse : le manifeste
    contenait l'information mais rien ne la réutilisait.
    """
    result = restore_detailed(archived_name)
    return result["path"] if result else None


def restore_detailed(archived_name: str) -> Optional[dict]:
    """Restaure et expose les réindexations qui n'ont pas pu être reconstruites."""
    trash = library.OUTPUT_DIR / ".trash"
    src = trash / Path(archived_name).name
    if src.name != archived_name or not src.exists():
        return None
    meta = library._read_manifest().get(src.name, {})
    origin = meta.get("name") or src.name
    dest_dir = library.project_dir(meta.get("project"))
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / origin
    if dest.exists():  # un transcript porte déjà ce nom : ne rien écraser
        return None
    src.rename(dest)
    try:
        library.forget_archived(src.name)
    except Exception:
        # Le fichier et le manifeste forment ensemble la preuve restaurable.
        # Si le manifeste résiste, remettre le Markdown en corbeille évite une
        # restauration à moitié effectuée et invisible depuis l'interface.
        dest.rename(src)
        raise
    # Symétrie avec l'archivage : `_drop_derived` supprime les chunks sémantiques,
    # la restauration doit donc les reconstruire. Sans ça le document revenait
    # dans la bibliothèque et la recherche plein-texte, mais restait absent de la
    # recherche par le sens et du RAG jusqu'au prochain redémarrage — une
    # restauration silencieusement partielle.
    errors: list[str] = []
    try:
        library.sync_file(dest)
    except Exception as exc:  # noqa: BLE001 — le Markdown reste autoritatif
        errors.append(f"bibliothèque : {exc}")
    try:
        semantic.index_name(dest.name)
    except Exception as exc:  # noqa: BLE001 — le Markdown est déjà restauré
        errors.append(f"index sémantique : {exc}")
    try:  # les faits ECL reviennent depuis leur fichier, conservé à l'archivage
        ecl.indexer(dest.name)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"faits ECL : {exc}")
    try:  # ré-indexe les voix depuis les empreintes conservées
        voiceprint.index_name(dest.name)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"registre des voix : {exc}")
    return {"path": dest, "errors": errors}


def _drop_derived(name: str) -> list[str]:
    """Purge les index RECONSTRUCTIBLES : chunks sémantiques + rattachement aux
    voix. Ne touche pas aux empreintes ni à l'audio (artefacts durables).

    Retourne les erreurs rencontrées : on continue la purge, mais on ne les
    dissimule pas — une suppression partielle doit pouvoir être signalée.
    """
    errors: list[str] = []
    for label, fn in (("chunks", semantic.remove), ("voix", voiceprint.unindex),
                      ("faits ECL", ecl.desindexer)):
        try:
            fn(name)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label} : {exc}")
    return errors


def purge_transcript(name: str) -> dict:
    """Suppression **DÉFINITIVE** et complète. Retourne l'état avant/après.

    Irréversible : fichier, index, vecteurs, voix, empreintes, audio, corbeille.
    """
    # Identité gelée AVANT toute suppression : chaque étape, vérification
    # comprise, doit viser exactement le même document (voir `resoudre`).
    cible = resoudre(name)
    identity, fichier = cible["identity"], cible["fichier"]
    before = traces(name, cible=cible)
    if cible["ambigu"]:
        # Plusieurs documents vivants répondent à cette chaîne. En choisir un
        # détruirait arbitrairement le travail de quelqu'un : on refuse et on
        # nomme les candidats, à l'appelant de désigner un nom de fichier.
        return {
            "name": name, "before": before, "after": before, "clean": False,
            "errors": [
                f"« {name} » désigne {len(cible['ambigu'])} documents "
                f"({', '.join(cible['ambigu'])}) : préciser lequel supprimer"
            ],
        }
    errors: list[str] = []

    def attempt(label: str, fn) -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 — on continue, mais on trace
            errors.append(f"{label} : {exc}")

    # 1. Index dérivés : table + FTS, chunks sémantiques, rattachement aux voix.
    attempt("index", lambda: library.forget(fichier))
    errors += _drop_derived(fichier)
    if identity != fichier:
        errors += _drop_derived(identity)

    # 2. Le fichier lui-même (dans son projet), puis ses copies en corbeille.
    path = cible["path"]
    if path is not None:
        attempt("fichier", lambda: path.unlink(missing_ok=True))
    for p in _trash_copies(name):
        supprime = True

        def _unlink(p=p) -> None:
            p.unlink(missing_ok=True)

        try:
            _unlink()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"corbeille/{p.name} : {exc}")
            supprime = False
        # Le manifeste est le SEUL support durable du nom d'origine, du projet
        # et de l'ULID. L'effacer alors que le Markdown a résisté détruirait
        # l'identité nécessaire à une nouvelle tentative : on le garde.
        if supprime:
            attempt(f"manifeste/{p.name}", lambda p=p: library.forget_archived(p.name))

    # 3. Artefacts durables : empreintes annexes + archive audio.
    attempt("empreintes", lambda: voiceprint.drop_embeddings(identity))
    attempt("audio", lambda: media.drop_audio(identity))
    if cible["document_id"]:
        attempt("faits ECL", lambda: ecl.effacer_fichier(cible["document_id"]))

    after = traces(name, cible=cible)
    # `None` ne signifie « illisible » que pour les compteurs SQL ; pour `file`
    # il signifie « plus de fichier », c'est-à-dire exactement ce qu'on veut.
    illisibles = ("chunks", "voice_members", "ecl_faits")
    reste = {
        k: v for k, v in after.items()
        if v or (v is None and k in illisibles)
    }
    if reste:
        # Des traces subsistent : le dire explicitement plutôt que de laisser
        # croire à une suppression complète.
        errors.append(
            "traces restantes : "
            + ", ".join(f"{k}={'illisible' if v is None else v}" for k, v in reste.items())
        )
    # `clean` ne peut PAS contredire `errors` : une suppression dont une étape a
    # échoué n'est pas propre, même si la vérification n'a rien vu — c'est
    # précisément quand la vérification est aveugle que l'erreur est le seul
    # signal restant. Les appelants (api/main.py) ne lisent `errors` que si
    # `clean` est faux : les deux champs doivent donc rester cohérents.
    clean = not reste and not errors
    return {"name": name, "before": before, "after": after, "clean": clean, "errors": errors}


def purge_archived(archived_name: str) -> dict:
    """Supprime UN élément exact de la corbeille, de façon vérifiable et reprenable.

    Tant qu'un artefact résiste, le Markdown et son manifeste restent en place :
    ils portent l'identité nécessaire à une nouvelle tentative.
    """
    safe_name = Path(archived_name).name
    if safe_name != archived_name:
        return {
            "deleted": None, "origin": None, "clean": False,
            "shared_artifacts_preserved": False,
            "freed_bytes": 0, "errors": ["nom d'archive invalide"],
        }
    item = next(
        (candidate for candidate in library.trash_items() if candidate["name"] == safe_name),
        None,
    )
    if item is None:
        return {
            "deleted": None, "origin": None, "clean": False,
            "shared_artifacts_preserved": False,
            "freed_bytes": 0, "errors": ["élément introuvable en corbeille"],
        }

    origin = item.get("origin") or safe_name
    document_id = item.get("document_id")
    # `origin` est la CHAÎNE BRUTE passée à `archive()` — un titre, un stem, un
    # nom sans extension. Ce n'est ni un nom de fichier ni une identité. S'en
    # servir comme clé laissait la ligne d'index en place et purgeait les
    # artefacts sous une clé inexistante, en annonçant « clean ».
    fichier = _nom_original(safe_name)
    identity = document_id or fichier
    cible = {
        "demande": origin,
        "path": library.transcript_path(fichier),
        "fichier": fichier,
        "document_id": document_id,
        "identity": identity,
    }

    # Les artefacts durables (audio, empreintes) sont rangés sous UNE clé de
    # stockage. Le partage se juge donc là-dessus, et pas en comparant des
    # chaînes hétérogènes entre elles.
    notre_cle = _cle_stockage(identity)
    other_items = [
        candidate for candidate in library.trash_items()
        if candidate["name"] != safe_name
        and _cle_stockage(
            candidate.get("document_id") or _nom_original(candidate["name"])
        ) == notre_cle
    ]
    active_same_identity = any(
        _cle_stockage(candidate.get("id") or candidate.get("name", "")) == notre_cle
        for candidate in library.list_transcripts()
    )
    shared = bool(other_items or active_same_identity)
    errors: list[str] = []

    if not shared:
        try:
            library.forget(fichier)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"index : {exc}")
        errors += _drop_derived(identity)
        # Sous l'identité ET sous sa clé de stockage dérivée : un artefact rangé
        # sous le stem survivait à une purge menée par nom de fichier.
        cles = list(dict.fromkeys([identity, _cle_stockage(identity)]))
        for label, fn in (
            ("empreintes", voiceprint.drop_embeddings),
            ("audio", media.drop_audio),
        ):
            for cle in cles:
                try:
                    fn(cle)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{label} : {exc}")
        if document_id:
            try:
                ecl.effacer_fichier(document_id)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"faits ECL : {exc}")

        # Le fichier en corbeille est encore attendu à ce stade ; toute autre
        # trace doit être résolue AVANT de perdre son manifeste — c'est lui qui
        # porte l'ULID, donc la seule clé sous laquelle les artefacts durables
        # sont rangés. On gèle donc la cible sur l'identité du MANIFESTE plutôt
        # que de la re-dériver d'un Markdown archivé.
        remaining = traces(origin, cible=cible)
        # `None` ne vaut « illisible » que pour les compteurs SQL ; pour `file`
        # il signifie « plus de fichier », soit l'état recherché.
        unexpected = {
            key: value for key, value in remaining.items()
            if key != "trash_copies"
            and (value or (value is None and key in ("chunks", "voice_members", "ecl_faits")))
        }
        if unexpected:
            errors.append(
                "traces restantes : "
                + ", ".join(f"{key}={value}" for key, value in unexpected.items())
            )

    if errors:
        return {
            "deleted": None,
            "origin": origin,
            "clean": False,
            "shared_artifacts_preserved": shared,
            "freed_bytes": 0,
            "errors": errors,
        }

    trash_path = library.OUTPUT_DIR / ".trash" / safe_name
    payload = trash_path.read_bytes()
    try:
        trash_path.unlink()
        library.forget_archived(safe_name)
    except Exception as exc:  # noqa: BLE001
        rollback_error = None
        if not trash_path.exists():
            try:
                trash_path.write_bytes(payload)
            except Exception as rollback_exc:  # noqa: BLE001
                rollback_error = rollback_exc
        detail = f"corbeille/{safe_name} : {exc}"
        if rollback_error is not None:
            detail += f" ; restauration du Markdown impossible : {rollback_error}"
        return {
            "deleted": None,
            "origin": origin,
            "clean": False,
            "shared_artifacts_preserved": shared,
            "freed_bytes": 0,
            "errors": [detail],
        }

    # Le Markdown de corbeille vient de partir : re-résoudre ici rendrait la
    # vérification aveugle aux artefacts rangés sous l'ULID. On réutilise la
    # cible gelée, dont l'identité vient du manifeste.
    clean = is_clean(origin, cible=cible)
    return {
        "deleted": safe_name,
        "origin": origin,
        "clean": clean,
        "shared_artifacts_preserved": shared,
        "freed_bytes": item.get("size") or 0,
        "errors": [],
    }


def purge_trash(archived_names: set[str] | None = None) -> dict:
    """Vide la corbeille : suppression définitive de tous les archivés.

    Les artefacts durables sont purgés sous la clé d'ORIGINE (manifeste) — le
    nom archivé (`foo.1.md`) n'est pas la clé de l'audio ni des empreintes.
    """
    items = [
        item for item in library.trash_items()
        if archived_names is None or item["name"] in archived_names
    ]
    freed, removed, errors, kept = 0, 0, [], []

    for item in items:
        result = purge_archived(item["name"])
        if result["deleted"]:
            freed += result["freed_bytes"]
            removed += 1
        else:
            errors += result["errors"]
            kept.append(item["name"])

    return {"removed": removed, "freed_bytes": freed, "kept": kept, "errors": errors}
