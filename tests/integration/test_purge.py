"""Suppression de transcripts : l'archivage doit rester réversible, et la
suppression définitive ne doit RIEN laisser dans aucun magasin."""

from __future__ import annotations

import sqlite3

import pytest

from core import config, docid, library, media, purge, semantic, voice_registry, voiceprint
from core.config import VOICE_EMBED_DIM


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    """Un transcript présent dans TOUS les magasins (fichier, index, FTS,
    chunks, voix, empreintes, audio)."""
    out = tmp_path / "output"
    (out / "Projet").mkdir(parents=True)
    db = tmp_path / "index.db"
    audio_dir = out / ".audio"
    audio_dir.mkdir()

    for mod in (library, purge):
        monkeypatch.setattr(mod, "OUTPUT_DIR", out, raising=False)
    # chaque magasin porte sa propre constante DB_PATH (même fichier en prod)
    monkeypatch.setattr(library, "DB_PATH", db)
    monkeypatch.setattr(voiceprint, "DB_PATH", db)
    monkeypatch.setattr(semantic, "DB_PATH", db)
    monkeypatch.setattr(voiceprint, "SIDECAR", out / ".voiceprints.json")
    # Le registre des voix vit aussi sous `output/` : non redirigé, il écrivait
    # `.voices.json` dans l'`output/` partagé de la session.
    monkeypatch.setattr(voice_registry, "FICHIER", out / ".voices.json")
    monkeypatch.setattr(media, "audio_path", lambda n, root=None: audio_dir / (n[:-3] + ".m4a"))
    monkeypatch.setattr(media, "original_path", lambda n, root=None: None)

    name = "reunion.md"
    md = out / "Projet" / name
    md.write_text(
        "---\ntitle: reunion\nlanguage: fr\ndiarization: true\n---\n\n"
        "**Speaker 1** [00:00:01] bonjour tout le monde\n",
        encoding="utf-8",
    )
    library.sync_file(md)

    # chunks sémantiques + vecteur (on écrit en base sans appeler Ollama)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE IF NOT EXISTS chunks (id INTEGER PRIMARY KEY, name TEXT, idx INTEGER, ts TEXT, text TEXT)")
    conn.execute("INSERT INTO chunks (name, idx, ts, text) VALUES (?, 0, NULL, 'bonjour')", (name,))
    conn.commit()
    conn.close()

    # dimension réelle : save_embeddings refuse (à raison) les vecteurs jouets
    vec = [1.0] + [0.0] * (VOICE_EMBED_DIM - 1)
    voiceprint.index_file(name, {"Speaker 1": vec})
    voiceprint.save_embeddings(name, {"Speaker 1": vec})
    (audio_dir / "reunion.m4a").write_bytes(b"fake-audio")
    return name


def test_traces_sees_every_store(corpus) -> None:
    t = purge.traces(corpus)
    assert t["file"] is not None
    assert t["indexed"] == 1 and t["fts"] == 1
    assert t["chunks"] == 1
    assert t["voice_members"] == 1
    assert t["embeddings"] == 1
    assert t["audio"] is True


def test_archive_is_reversible_and_keeps_durable_artifacts(corpus) -> None:
    assert purge.archive(corpus)["archived"] is True
    t = purge.traces(corpus)
    # Index dérivés purgés, fichier parti en corbeille…
    assert t["indexed"] == 0 and t["fts"] == 0
    assert t["chunks"] == 0 and t["voice_members"] == 0
    assert t["trash_copies"] == 1
    # …mais l'audio et les empreintes restent, sinon la restauration serait vaine.
    assert t["audio"] is True
    assert t["embeddings"] == 1


def test_purge_leaves_nothing_anywhere(corpus) -> None:
    result = purge.purge_transcript(corpus)

    assert result["before"]["indexed"] == 1  # il y avait bien quelque chose
    after = result["after"]
    assert after == {
        "file": None, "indexed": 0, "fts": 0, "chunks": 0,
        "voice_members": 0, "embeddings": 0, "audio": False, "faithful": False,
        "trash_copies": 0, "ecl_faits": 0, "ecl_fichier": False,
    }
    assert purge.is_clean(corpus)
    assert corpus not in [i["name"] for i in library.list_transcripts()]


def test_purge_also_removes_an_archived_copy(corpus) -> None:
    assert purge.archive(corpus)["archived"]
    assert purge.traces(corpus)["trash_copies"] == 1
    purge.purge_transcript(corpus)
    assert purge.is_clean(corpus)


def test_archived_transcript_is_not_considered_clean(corpus) -> None:
    """Un transcript archivé garde des traces (corbeille, audio, empreintes) :
    il doit rester purgeable — sinon la suppression n'est pas « de bout en bout »."""
    assert purge.archive(corpus)["archived"]
    assert not purge.is_clean(corpus)
    purge.purge_transcript(corpus)
    assert purge.is_clean(corpus)


def test_empty_trash_frees_everything(corpus) -> None:
    assert purge.archive(corpus)["archived"]
    stats = purge.purge_trash()
    assert stats["removed"] == 1 and stats["freed_bytes"] > 0
    assert library.trash_items() == []
    assert purge.is_clean(corpus)


def test_purge_one_archived_item_is_verified_and_exact(corpus) -> None:
    assert purge.archive(corpus)["archived"]
    archived_name = library.trash_items()[0]["name"]

    result = purge.purge_archived(archived_name)

    assert result["deleted"] == archived_name
    assert result["origin"] == corpus
    assert result["clean"] is True
    assert result["errors"] == []
    assert library.trash_items() == []
    assert purge.is_clean(corpus)


def test_failed_individual_purge_keeps_markdown_and_manifest(
    corpus, monkeypatch,
) -> None:
    assert purge.archive(corpus)["archived"]
    archived_name = library.trash_items()[0]["name"]

    def fail(_identity, root=None):
        raise OSError("volume verrouillé")

    monkeypatch.setattr(media, "drop_audio", fail)

    result = purge.purge_archived(archived_name)

    assert result["deleted"] is None
    assert result["clean"] is False
    assert "volume verrouillé" in " ".join(result["errors"])
    assert [item["name"] for item in library.trash_items()] == [archived_name]
    assert library.archived_names_for(corpus) == [archived_name]


def test_manifest_failure_rolls_back_individual_markdown_deletion(
    corpus, monkeypatch,
) -> None:
    assert purge.archive(corpus)["archived"]
    archived_name = library.trash_items()[0]["name"]

    def fail_manifest(_name: str) -> None:
        raise OSError("manifeste verrouillé")

    monkeypatch.setattr(library, "forget_archived", fail_manifest)
    result = purge.purge_archived(archived_name)

    assert result["deleted"] is None
    assert "manifeste verrouillé" in " ".join(result["errors"])
    assert [item["name"] for item in library.trash_items()] == [archived_name]
    assert library.archived_names_for(corpus) == [archived_name]


def test_archiving_an_identified_document_detaches_relations_by_id(
    isolated_stores, monkeypatch,
) -> None:
    output = isolated_stores["output_dir"]
    document_id = docid.nouveau()
    name = "identified.md"
    path = output / name
    path.write_text(
        f"---\nid: {document_id}\nmeeting_id: {docid.nouveau()}\n"
        f"project_id: {docid.nouveau()}\ntitle: Identified\n---\n\nBody\n",
        encoding="utf-8",
    )
    library.sync_file(path)
    conn = semantic._conn()
    conn.execute(
        "INSERT INTO chunks (name, document_id, text) VALUES (?, ?, ?)",
        (name, document_id, "Body"),
    )
    conn.commit()
    conn.close()
    vec = [1.0] + [0.0] * (VOICE_EMBED_DIM - 1)
    voiceprint.index_file(document_id, {"Speaker 1": vec}, name=name)
    voiceprint.save_embeddings(document_id, {"Speaker 1": vec}, root=output)
    monkeypatch.setattr(voiceprint, "SIDECAR", output / ".voiceprints.json")
    audio = output / ".audio"
    audio.mkdir()
    canonical = audio / f"{document_id}.m4a"
    canonical.write_bytes(b"audio")
    monkeypatch.setattr(config, "AUDIO_DIR", audio)

    result = purge.archive(name)

    assert result == {"archived": True, "errors": []}
    conn = semantic._conn()
    assert conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE document_id = ?", (document_id,)
    ).fetchone()[0] == 0
    conn.close()
    conn = voiceprint._conn()
    assert conn.execute(
        "SELECT COUNT(*) FROM voice_members WHERE document_id = ?", (document_id,)
    ).fetchone()[0] == 0
    conn.close()
    assert voiceprint.load_embeddings(document_id, root=output)
    assert canonical.exists()
    assert library.trash_items()[0]["document_id"] == document_id


def test_trash_item_exposes_authoritative_markdown_metadata(
    isolated_stores,
) -> None:
    output = isolated_stores["output_dir"]
    document_id = docid.nouveau()
    meeting_id = docid.nouveau()
    project_id = docid.nouveau()
    path = output / "report.md"
    path.write_text(
        f"---\nid: {document_id}\nmeeting_id: {meeting_id}\n"
        f"project_id: {project_id}\ntitle: Compte-rendu produit\n"
        "type: report\nsource_transcript: source.md\n"
        f"source_document_id: {docid.nouveau()}\n---\n\nBody\n",
        encoding="utf-8",
    )
    library.sync_file(path)

    assert purge.archive(path.name)["archived"]
    item = library.trash_items()[0]

    assert item["origin"] == "report.md"
    assert item["title"] == "Compte-rendu produit"
    assert item["type"] == "report"
    assert item["meeting_id"] == meeting_id
    assert item["project_id"] == project_id
    assert item["source_transcript"] == "source.md"
