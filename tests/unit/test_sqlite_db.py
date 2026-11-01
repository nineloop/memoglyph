from __future__ import annotations

from core import sqlite_db


def test_busy_timeout_is_explicit_on_every_derived_store_connection(tmp_path) -> None:
    conn = sqlite_db.connect(tmp_path / "derived.db")
    try:
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    assert timeout == sqlite_db.BUSY_TIMEOUT_MS == 5_000
    assert journal == sqlite_db.JOURNAL_MODE == "wal"


def test_wal_allows_a_writer_to_commit_while_a_reader_keeps_its_snapshot(tmp_path) -> None:
    path = tmp_path / "concurrent.db"
    setup = sqlite_db.connect(path)
    setup.execute("CREATE TABLE values_for_test (value INTEGER)")
    setup.execute("INSERT INTO values_for_test VALUES (1)")
    setup.commit()
    setup.close()

    reader = sqlite_db.connect(path)
    writer = sqlite_db.connect(path)
    try:
        reader.execute("BEGIN")
        assert reader.execute("SELECT value FROM values_for_test").fetchone()[0] == 1
        writer.execute("PRAGMA busy_timeout = 0")
        writer.execute("INSERT INTO values_for_test VALUES (2)")
        writer.commit()  # échoue immédiatement en journal DELETE
        assert reader.execute("SELECT COUNT(*) FROM values_for_test").fetchone()[0] == 1
    finally:
        reader.rollback()
        reader.close()
        writer.close()

    check = sqlite_db.connect(path)
    try:
        assert check.execute("SELECT COUNT(*) FROM values_for_test").fetchone()[0] == 2
    finally:
        check.close()
