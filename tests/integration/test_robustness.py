"""Robustesse sous erreur et concurrence — les quatre scénarios du 2e audit.

Chemins d'erreur, validation des identifiants et écritures simultanées : ce sont
les endroits où l'on corrompt l'état sans jamais s'en apercevoir.
"""

from __future__ import annotations

import threading

import pytest

from api.jobs import JobManager, JobsBusy
from core import library, media, purge, semantic, voice_registry, voiceprint
from core.config import VOICE_EMBED_DIM


def _vec(first: float = 1.0) -> list[float]:
    return [first] + [0.0] * (VOICE_EMBED_DIM - 1)


# ---------- 1. Un upload rejeté ne doit pas confisquer un slot ----------

def test_discarded_job_frees_its_slot() -> None:
    jobs = JobManager(max_active=2, max_history=10)
    a, b = jobs.create(), jobs.create()
    with pytest.raises(JobsBusy):
        jobs.create()

    # Uploads refusés (413) : les jobs n'ont jamais démarré.
    jobs.discard(a)
    jobs.discard(b)

    assert jobs.active_count() == 0
    jobs.create()  # ne doit plus lever


def test_discard_never_drops_a_running_job() -> None:
    jobs = JobManager(max_active=1, max_history=10)
    job = jobs.create()
    job.status = "running"
    jobs.discard(job)
    assert jobs.get(job.id) is not None


# ---------- 2. Fusion / renommage de voix inexistante ----------

@pytest.fixture
def voices(tmp_path, monkeypatch):
    monkeypatch.setattr(voiceprint, "DB_PATH", tmp_path / "v.db")
    monkeypatch.setattr(voiceprint, "SIDECAR", tmp_path / ".voiceprints.json")
    # Le registre des voix aussi : sinon fusion et renommage réécrivent le
    # `.voices.json` de l'`output/` partagé de la session.
    monkeypatch.setattr(voice_registry, "FICHIER", tmp_path / ".voices.json")
    return voiceprint.index_file("a.md", {"Speaker 1": _vec()})["Speaker 1"]


def test_merge_into_unknown_voice_is_refused(voices) -> None:
    with pytest.raises(voiceprint.UnknownVoice):
        voiceprint.merge_voices(voices, 999)

    conn = voiceprint._conn()
    try:
        orphans = conn.execute(
            "SELECT COUNT(*) FROM voice_members m "
            "LEFT JOIN voices v ON v.id = m.voice_id WHERE v.id IS NULL"
        ).fetchone()[0]
    finally:
        conn.close()
    assert orphans == 0  # le registre reste cohérent


def test_rename_unknown_voice_is_refused(voices) -> None:
    with pytest.raises(voiceprint.UnknownVoice):
        voiceprint.rename(999, "Fantôme")


# ---------- 3. Vidage de corbeille : reprenable, compteur juste ----------

@pytest.fixture
def trashed(tmp_path, monkeypatch):
    out = tmp_path / "output"
    out.mkdir()
    for mod in (library, purge):
        monkeypatch.setattr(mod, "OUTPUT_DIR", out, raising=False)
    monkeypatch.setattr(library, "DB_PATH", tmp_path / "s.db")
    monkeypatch.setattr(voiceprint, "DB_PATH", tmp_path / "s.db")
    monkeypatch.setattr(semantic, "DB_PATH", tmp_path / "s.db")
    monkeypatch.setattr(voiceprint, "SIDECAR", out / ".voiceprints.json")
    monkeypatch.setattr(media, "audio_path", lambda n, root=None: out / ".audio" / f"{n[:-3]}.m4a")
    monkeypatch.setattr(media, "original_path", lambda n, root=None: None)

    p = out / "foo.md"
    p.write_text("---\ntitle: foo\n---\n\ncorps\n", encoding="utf-8")
    library.sync_file(p)
    library.archive("foo.md")
    return out


def test_emptying_trash_counts_actual_removals(trashed) -> None:
    stats = purge.purge_trash()
    assert stats["removed"] == 1
    assert stats["errors"] == [] and stats["kept"] == []
    assert library.trash_items() == []


def test_failed_artifact_keeps_the_item_recoverable(trashed, monkeypatch) -> None:
    """Si un artefact durable résiste, on GARDE le .md et son manifeste : sinon
    on perd l'identité qui permet de retrouver l'artefact resté en place."""
    def boom(name, root=None):
        raise OSError("disque en lecture seule")

    monkeypatch.setattr(media, "drop_audio", boom)

    stats = purge.purge_trash()

    assert stats["removed"] == 0            # rien n'a été réellement supprimé…
    assert stats["removed"] >= 0            # …et surtout jamais négatif
    assert stats["kept"] == ["foo.md"]
    assert len(library.trash_items()) == 1  # reprenable
    assert library.archived_names_for("foo.md") == ["foo.md"]  # identité préservée


# ---------- 4. Écritures simultanées ----------

def test_concurrent_sidecar_writes_keep_every_entry(tmp_path, monkeypatch) -> None:
    """Deux jobs peuvent être actifs : sans verrou, la 2e écriture écrasait
    l'entrée de la 1re (lecture → modification → écriture non protégée)."""
    monkeypatch.setattr(voiceprint, "SIDECAR", tmp_path / ".voiceprints.json")

    def write(i: int) -> None:
        voiceprint.save_embeddings(f"t{i}.md", {"Speaker 1": _vec(float(i + 1))})

    threads = [threading.Thread(target=write, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    data = voiceprint._read_sidecar()
    assert len(data) == 12, f"entrées perdues : {sorted(data)}"


def test_concurrent_archives_keep_every_manifest_entry(tmp_path, monkeypatch) -> None:
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setattr(library, "OUTPUT_DIR", out)
    monkeypatch.setattr(library, "DB_PATH", tmp_path / "s.db")
    for i in range(10):
        p = out / f"t{i}.md"
        p.write_text(f"---\ntitle: t{i}\n---\n\nx\n", encoding="utf-8")
        library.sync_file(p)

    threads = [threading.Thread(target=library.archive, args=(f"t{i}.md",)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(library._read_manifest()) == 10
    assert len(library.trash_items()) == 10
