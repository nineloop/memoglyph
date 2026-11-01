from __future__ import annotations

import os
from pathlib import Path

import pytest

from core import docid, library


pytestmark = pytest.mark.integration


def _write_transcript(
    output_dir: Path,
    name: str,
    *,
    title: str = "Entretien",
    language: str = "fr",
    source: str = "audio.mp3",
    body: str = "# Entretien\n\n[00:00:01] Le climat évolue rapidement.",
    diarization: bool = False,
    profiled: bool = False,
) -> Path:
    meeting_id = docid.nouveau()
    project_id = docid.nouveau()
    path = output_dir / name
    path.write_text(
        "\n".join(
            [
                "---",
                f"id: {docid.nouveau()}",
                f"meeting_id: {meeting_id}",
                f"project_id: {project_id}",
                f"title: {title}",
                "date: 2026-07-19",
                "duration: 00:01:30",
                f"language: {language}",
                "engine: whisper",
                f"source: {source}",
                f"diarization: {str(diarization).lower()}",
                f"profiled: {str(profiled).lower()}",
                "---",
                "",
                body,
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_split_frontmatter_handles_valid_absent_and_invalid_yaml() -> None:
    meta, body = library._split_frontmatter("---\ntitle: Essai\n---\n\nCorps")
    assert meta == {"title": "Essai"}
    assert body == "Corps"

    assert library._split_frontmatter("Corps seul") == ({}, "Corps seul")

    meta, body = library._split_frontmatter("---\ninvalid: [\n---\nCorps")
    assert meta == {}
    assert body == "Corps"


def test_sync_get_list_and_languages(isolated_stores) -> None:
    output = isolated_stores["output_dir"]
    fr = _write_transcript(output, "fr.md", title="Français")
    en = _write_transcript(output, "en.md", title="English", language="en")

    library.sync_file(fr)
    library.sync_file(en)

    items = library.list_transcripts(sort="title", order="asc")
    assert [item["title"] for item in items] == ["English", "Français"]
    assert items[0]["diarization"] is False
    assert library.languages() == ["en", "fr"]
    detail = library.get_transcript("fr.md")
    assert detail is not None
    assert detail["meta"]["source"] == "audio.mp3"
    assert "climat" in detail["body"]


def test_list_filters_and_unknown_sort_are_safe(isolated_stores) -> None:
    output = isolated_stores["output_dir"]
    plain = _write_transcript(output, "plain.md", title="Alpha")
    rich = _write_transcript(
        output,
        "rich.md",
        title="Beta",
        language="en",
        diarization=True,
        profiled=True,
    )
    library.sync_file(plain)
    library.sync_file(rich)

    assert [x["name"] for x in library.list_transcripts(language="en")] == ["rich.md"]
    assert [x["name"] for x in library.list_transcripts(diarized=True)] == ["rich.md"]
    assert [x["name"] for x in library.list_transcripts(profiled=False)] == ["plain.md"]
    assert [x["name"] for x in library.list_transcripts(q="alp")] == ["plain.md"]
    assert len(library.list_transcripts(sort="mtime; DROP TABLE transcripts")) == 2


def test_fts_search_is_accent_insensitive_and_escapes_syntax(isolated_stores) -> None:
    output = isolated_stores["output_dir"]
    path = _write_transcript(
        output,
        "energie.md",
        body="# Note\n\n[00:00:01] La transition énergétique avance.",
    )
    library.sync_file(path)

    results = library.search("energet")

    assert [r["name"] for r in results] == ["energie.md"]
    assert results[0]["document_id"]
    assert results[0]["meeting_id"]
    assert results[0]["project_id"]
    assert library.SNIPPET_START in results[0]["snippet"]
    assert library.search('" OR *') == []
    assert library.search("") == []


def test_reindex_adds_updates_and_removes_files(isolated_stores) -> None:
    output = isolated_stores["output_dir"]
    path = _write_transcript(output, "one.md", title="Version 1")

    assert library.reindex() == {"added": 1, "updated": 0, "removed": 0, "total": 1}
    assert library.reindex() == {"added": 0, "updated": 0, "removed": 0, "total": 1}

    previous = path.stat().st_mtime
    _write_transcript(output, "one.md", title="Version 2")
    os.utime(path, (previous + 2, previous + 2))
    stats = library.reindex()
    assert stats["updated"] == 1
    assert library.list_transcripts()[0]["title"] == "Version 2"

    path.unlink()
    assert library.reindex() == {"added": 0, "updated": 0, "removed": 1, "total": 0}


def test_reindex_recognizes_a_rename_by_document_id(isolated_stores) -> None:
    """Le nom change ; l'identité et les relations dérivées restent stables."""
    import sqlite3

    output = isolated_stores["output_dir"]
    db = isolated_stores["db_path"]
    document_id = docid.nouveau()
    meeting_id = docid.nouveau()
    project_id = docid.nouveau()
    path = output / "avant.md"
    path.write_text(
        f"---\nid: {document_id}\nmeeting_id: {meeting_id}\n"
        f"project_id: {project_id}\ntitle: Stable\n---\n\nCorps stable.\n",
        encoding="utf-8",
    )
    library.reindex()

    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS chunks ("
        "id INTEGER PRIMARY KEY, name TEXT, document_id TEXT, text TEXT)"
    )
    conn.execute(
        "INSERT INTO chunks (name, document_id, text) VALUES (?, ?, ?)",
        ("avant.md", document_id, "Corps stable."),
    )
    conn.commit()
    conn.close()

    path.rename(output / "apres.md")
    stats = library.reindex()

    assert stats["removed"] == 0
    conn = sqlite3.connect(db)
    try:
        transcript = conn.execute(
            "SELECT name, id FROM transcripts WHERE id = ?", (document_id,)
        ).fetchone()
        relation = conn.execute(
            "SELECT c.name, t.name FROM chunks c "
            "JOIN transcripts t ON t.id = c.document_id "
            "WHERE c.document_id = ?", (document_id,),
        ).fetchone()
    finally:
        conn.close()
    assert transcript == ("apres.md", document_id)
    assert relation == ("apres.md", "apres.md")


def test_schema_upgrade_rehydrates_an_unchanged_markdown(isolated_stores) -> None:
    """Ajouter une colonne SQL ne suffit pas : une ligne dont le mtime n'a pas
    changé doit être relue après une évolution de l'index dérivé."""
    import sqlite3

    output = isolated_stores["output_dir"]
    db = isolated_stores["db_path"]
    document_id = docid.nouveau()
    meeting_id = docid.nouveau()
    project_id = docid.nouveau()
    path = output / "legacy.md"
    path.write_text(
        "\n".join([
            "---",
            f"id: {document_id}",
            f"meeting_id: {meeting_id}",
            f"project_id: {project_id}",
            "title: Vérité du Markdown",
            "language: fr",
            "repetition_loops:",
            "  - boucle retirée",
            "---",
            "",
            "Corps intact.",
        ]),
        encoding="utf-8",
    )
    unchanged_mtime = path.stat().st_mtime

    # Simule une base déjà indexée AVANT que `id` et `warnings` soient
    # transportés. Le mtime est rigoureusement identique au fichier.
    conn = library._conn()
    conn.execute(
        "INSERT INTO transcripts (name, title, mtime) VALUES (?, ?, ?)",
        (path.name, "Valeur périmée", unchanged_mtime),
    )
    conn.commit()
    conn.close()

    stats = library.reindex()

    assert stats["updated"] == 1
    raw = sqlite3.connect(db)
    try:
        row = raw.execute(
            "SELECT id, title, warnings FROM transcripts WHERE name = ?", (path.name,)
        ).fetchone()
    finally:
        raw.close()
    assert row == (document_id, "Vérité du Markdown", 1)
    assert library.reindex()["updated"] == 0


def test_reindex_refuses_an_invalid_corpus_and_keeps_index_migration_pending(
    isolated_stores,
) -> None:
    """Une reconstruction n'est jamais déclarée terminée si l'identité du
    corpus est ambiguë."""
    import sqlite3

    output = isolated_stores["output_dir"]
    db = isolated_stores["db_path"]
    duplicate = docid.nouveau()
    for name in ("a.md", "b.md"):
        (output / name).write_text(
            f"---\nid: {duplicate}\ntitle: {name}\n---\n\nCorps\n",
            encoding="utf-8",
        )

    with pytest.raises(library.CorpusValidationError) as caught:
        library.reindex()

    assert caught.value.issues["duplicate_ids"] == {
        duplicate: ["a.md", "b.md"],
    }
    conn = sqlite3.connect(db)
    try:
        version = conn.execute(
            "SELECT version FROM library_meta WHERE component = 'library_index'"
        ).fetchone()
    finally:
        conn.close()
    assert version is None


def test_a_future_schema_version_is_never_silently_downgraded(isolated_stores) -> None:
    import sqlite3

    conn = library._conn()
    conn.execute(
        "UPDATE library_meta SET version = ? WHERE component = 'library_schema'",
        (library.SCHEMA_VERSION + 1,),
    )
    conn.commit()
    conn.close()

    with pytest.raises(sqlite3.DatabaseError, match="plus récente"):
        library._conn()


def _meta_schema() -> tuple:
    conn = library._conn()
    try:
        return tuple(conn.execute(
            "SELECT version, updated_at FROM library_meta WHERE component = 'library_schema'").fetchone())
    finally:
        conn.close()


def _poser_meta_schema(version: int) -> None:
    conn = library._conn()
    conn.execute("UPDATE library_meta SET version = ?, updated_at = '2000-01-01 00:00:00' "
                 "WHERE component = 'library_schema'", (version,))
    conn.commit()
    conn.close()


def test_une_lecture_de_l_index_n_ecrit_pas_sa_version(isolated_stores) -> None:
    # Une date ancienne, posée à la main : réécrite, elle changerait quelle que soit
    # l'horloge. Deux connexions de lecture la laissent telle quelle.
    _poser_meta_schema(library.SCHEMA_VERSION)
    library._conn().close()
    library._conn().close()
    assert _meta_schema() == (library.SCHEMA_VERSION, "2000-01-01 00:00:00")


def test_une_migration_ecrit_toujours_la_version_du_schema(isolated_stores) -> None:
    _poser_meta_schema(library.SCHEMA_VERSION - 1)
    version, date = _meta_schema()
    assert version == library.SCHEMA_VERSION and date != "2000-01-01 00:00:00"


def test_a_future_index_version_is_never_silently_downgraded(isolated_stores) -> None:
    import sqlite3

    output = isolated_stores["output_dir"]
    _write_transcript(output, "future.md")
    conn = library._conn()
    conn.execute(
        "INSERT INTO library_meta (component, version, updated_at) "
        "VALUES ('library_index', ?, datetime('now'))",
        (library.INDEX_VERSION + 1,),
    )
    conn.commit()
    conn.close()

    with pytest.raises(sqlite3.DatabaseError, match="plus récente"):
        library.reindex()


def test_archive_moves_file_to_recoverable_trash_and_purges_index(isolated_stores) -> None:
    output = isolated_stores["output_dir"]
    path = _write_transcript(output, "archive.md")
    library.sync_file(path)

    assert library.archive("archive.md") is True

    assert not path.exists()
    assert (output / ".trash" / "archive.md").exists()
    assert library.list_transcripts() == []
    assert library.archive("archive.md") is False


def test_moving_a_transcript_moves_its_whole_meeting_and_updates_project_id(
    isolated_stores,
) -> None:
    from core import projects

    output = isolated_stores["output_dir"]
    inbox = projects.create(output, name=projects.INBOX_NAME, kind="inbox")
    target = projects.create(output / "Client", name="Client")
    meeting_id = docid.nouveau()
    transcript_id = docid.nouveau()
    transcript = output / "meeting.md"
    transcript.write_text(
        f"---\nid: {transcript_id}\nmeeting_id: {meeting_id}\n"
        f"project_id: {inbox.id}\ntitle: Meeting\n---\n\nBody\n",
        encoding="utf-8",
    )
    report = output / "meeting-cr.md"
    report.write_text(
        f"---\nid: {docid.nouveau()}\nmeeting_id: {meeting_id}\n"
        f"project_id: {inbox.id}\nsource_document_id: {transcript_id}\n"
        "type: report\nsource_transcript: meeting.md\ntitle: Report\n---\n\nReport\n",
        encoding="utf-8",
    )
    library.reindex()

    destination = library.move_to_project("meeting.md", "Client")

    assert destination == output / "Client" / "meeting.md"
    assert (output / "Client" / "meeting-cr.md").exists()
    assert not transcript.exists() and not report.exists()
    for name in ("meeting.md", "meeting-cr.md"):
        detail = library.get_transcript(name)
        assert detail["meta"]["project_id"] == target.id
    assert library.audit_corpus()["ok"]


def test_set_sensitivity_vaut_pour_la_reunion_sans_reecrire_aucun_document(isolated_stores) -> None:
    from core import projects

    output = isolated_stores["output_dir"]
    inbox = projects.create(output, name=projects.INBOX_NAME, kind="inbox")
    meeting_id = docid.nouveau()
    transcript_id = docid.nouveau()
    project_id = inbox.id  # le project_id DOIT correspondre au dossier
    (output / "m.md").write_text(
        f"---\nid: {transcript_id}\nmeeting_id: {meeting_id}\nproject_id: {project_id}\n"
        "title: M\nsensitivity: standard\n---\n\nBody\n", encoding="utf-8",
    )
    (output / "m-cr.md").write_text(
        f"---\nid: {docid.nouveau()}\nmeeting_id: {meeting_id}\nproject_id: {project_id}\n"
        f"source_document_id: {transcript_id}\ntype: report\nsource_transcript: m.md\n"
        "title: R\nsensitivity: standard\n---\n\nReport\n", encoding="utf-8",
    )
    library.reindex()
    report_mtime = (output / "m-cr.md").stat().st_mtime
    transcript_mtime = (output / "m.md").stat().st_mtime

    # Le niveau est celui de la RÉUNION (registre) : il vaut pour le transcript et le CR,
    # et aucun `.md` n'est réécrit — ni contenu, ni mtime, donc ni l'ordre des versions.
    assert library.set_sensitivity("m.md", "ultra_confidential") == "ultra_confidential"
    assert library.get_transcript("m.md")["meta"]["sensitivity"] == "standard"
    assert library.get_transcript("m-cr.md")["meta"]["sensitivity"] == "standard"
    for nom in ("m.md", "m-cr.md"):
        meta = library.get_transcript(nom)["meta"]
        assert library.sensibilite_effective(meta["sensitivity"], meta["meeting_id"]) == "ultra_confidential"
    assert (output / "m-cr.md").stat().st_mtime == report_mtime  # CR intact
    assert (output / "m.md").stat().st_mtime == transcript_mtime  # transcript intact
    assert library.audit_corpus()["ok"]


def test_set_sensitivity_coerces_unknown_and_reports_missing(isolated_stores) -> None:
    from core import projects

    output = isolated_stores["output_dir"]
    inbox = projects.create(output, name=projects.INBOX_NAME, kind="inbox")
    (output / "solo.md").write_text(
        f"---\nid: {docid.nouveau()}\nmeeting_id: {docid.nouveau()}\n"
        f"project_id: {inbox.id}\ntitle: Solo\n---\n\nBody\n", encoding="utf-8",
    )
    library.reindex()

    # Valeur inconnue → défaut, inscrit au registre de la réunion (le `.md` reste tel quel).
    assert library.set_sensitivity("solo.md", "bogus") == "standard"
    meeting_id = library.get_transcript("solo.md")["meta"]["meeting_id"]
    assert library.lire_registre_sensibilite() == {(inbox.id, meeting_id): "standard"}
    assert "sensitivity" not in library.get_transcript("solo.md")["meta"]
    # Document introuvable → None, sans lever.
    assert library.set_sensitivity("absent.md", "confidential") is None


def test_projects_count_meetings_not_documents(isolated_stores) -> None:
    from core import projects

    output = isolated_stores["output_dir"]
    inbox = projects.create(output, name=projects.INBOX_NAME, kind="inbox")
    first_meeting = docid.nouveau()
    transcript_id = docid.nouveau()
    documents = (
        ("meeting.md", transcript_id, first_meeting, ""),
        ("meeting-cr.md", docid.nouveau(), first_meeting,
         f"type: report\nsource_document_id: {transcript_id}\nsource_transcript: meeting.md\n"),
        ("other.md", docid.nouveau(), docid.nouveau(), ""),
    )
    for name, document_id, meeting_id, extra in documents:
        (output / name).write_text(
            f"---\nid: {document_id}\nmeeting_id: {meeting_id}\nproject_id: {inbox.id}\n"
            f"{extra}title: {name}\n---\n\nCorps\n",
            encoding="utf-8",
        )
    library.reindex()

    assert library.projects()[0]["count"] == 2


def test_moving_a_meeting_uses_destination_project_id(isolated_stores) -> None:
    from core import projects

    output = isolated_stores["output_dir"]
    inbox = projects.create(output, name=projects.INBOX_NAME, kind="inbox")
    target = projects.create(output / "Client", name="Client")
    transcript = output / "meeting.md"
    transcript.write_text(
        f"---\nid: {docid.nouveau()}\nmeeting_id: {docid.nouveau()}\n"
        f"project_id: {inbox.id}\ntitle: Meeting\n---\n\nBody\n",
        encoding="utf-8",
    )
    library.reindex()

    destination = library.move_to_project_id("meeting.md", target.id)

    assert destination == output / "Client" / "meeting.md"
    assert library.get_transcript("meeting.md")["meta"]["project_id"] == target.id
    with pytest.raises(library.UnsafeProject, match="destination"):
        library.move_to_project_id("meeting.md", "all")


def test_transcript_path_never_leaves_output_directory(isolated_stores, tmp_path) -> None:
    output = isolated_stores["output_dir"]
    outside = tmp_path / "secret.md"
    outside.write_text("secret", encoding="utf-8")

    assert library.transcript_path("../secret.md") is None
    assert library.transcript_path("anything.txt") is None
    inside = _write_transcript(output, "safe.md")
    assert library.transcript_path("safe.md") == inside


def test_meeting_filter_selects_parent_then_keeps_every_child(isolated_stores) -> None:
    from core import projects

    output = isolated_stores["output_dir"]
    inbox = projects.create(output, name=projects.INBOX_NAME, kind="inbox")
    meeting_id = docid.nouveau()
    transcript_id = docid.nouveau()
    (output / "meeting.md").write_text(
        f"---\nid: {transcript_id}\nmeeting_id: {meeting_id}\nproject_id: {inbox.id}\n"
        "title: Réunion\ndiarization: true\nprofiled: true\nrepetition_loops: 2\n"
        "date: 2026-07-20\n---\n\nTranscript\n",
        encoding="utf-8",
    )
    (output / "meeting-cr.md").write_text(
        f"---\nid: {docid.nouveau()}\nmeeting_id: {meeting_id}\nproject_id: {inbox.id}\n"
        f"type: report\nsource_document_id: {transcript_id}\nsource_transcript: meeting.md\n"
        "title: Compte-rendu\ndate: 2026-07-21\n---\n\nRapport\n",
        encoding="utf-8",
    )
    (output / "plain.md").write_text(
        f"---\nid: {docid.nouveau()}\nmeeting_id: {docid.nouveau()}\nproject_id: {inbox.id}\n"
        "title: Sans diarisation\n---\n\nAutre\n",
        encoding="utf-8",
    )
    library.reindex()

    meetings = library.list_meetings(diarized=True, project_id=inbox.id)

    assert len(meetings) == 1
    meeting = meetings[0]
    assert meeting["id"] == meeting_id
    assert meeting["preferred_transcript"]["id"] == transcript_id
    assert meeting["preferred_transcript"]["modified"] > 0
    assert [item["name"] for item in meeting["transcripts"]] == ["meeting.md"]
    assert all(item["modified"] > 0 for item in [*meeting["transcripts"], *meeting["reports"]])
    assert [item["name"] for item in meeting["reports"]] == ["meeting-cr.md"]
    assert meeting["quality_reservations"] == [
        {"document_id": transcript_id, "name": "meeting.md", "count": 1},
    ]
