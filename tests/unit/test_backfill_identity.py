from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from core import backfill_identity, docid, projects


pytestmark = pytest.mark.unit


def _write(path: Path, meta: dict, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)
        + "---\n\n" + body,
        encoding="utf-8",
    )
    return path


def _corpus(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "output"
    root.mkdir()
    inbox = projects.create(root, name=projects.INBOX_NAME, kind="inbox")
    client = projects.create(root / "Client", name="Client")
    ids = {
        name: docid.nouveau()
        for name in ("a", "b", "client_doc", "report", "archived")
    }
    _write(root / "a.md", {
        "id": ids["a"], "title": "A", "source": "same.mp3",
    }, "# A\n\nCorps A.\n")
    _write(root / "b.md", {
        "id": ids["b"], "title": "B", "source": "same.mp3",
    }, "# B\n\nCorps B.\n")
    _write(root / "Client" / "client.md", {
        "id": ids["client_doc"], "title": "Client", "source": "client.mp3",
    }, "# Client\n\nCorps client.\n")
    _write(root / "a-cr.md", {
        "id": ids["report"], "title": "CR", "type": "report",
        "source_transcript": "a.md",
    }, "# CR\n\nCorps CR.\n")
    archived = _write(root / ".trash" / "ancien.md", {
        "id": ids["archived"], "title": "Ancien", "source": "old.mp3",
    }, "# Ancien\n\nCorps archivé.\n")
    (root / ".trash" / "manifest.json").write_text(json.dumps({
        archived.name: {"name": archived.name, "project": projects.INBOX_NAME},
    }), encoding="utf-8")
    (root / ".voiceprints.json").write_text(json.dumps({
        "a.md": {"Speaker 1": "encoded"},
    }), encoding="utf-8")
    audio = root / ".audio"
    (audio / "faithful").mkdir(parents=True)
    (audio / "a.m4a").write_bytes(b"listen")
    (audio / "faithful" / "a.flac").write_bytes(b"faithful")
    ids["inbox"] = inbox.id
    ids["client"] = client.id
    return root, ids


def _body(path: Path) -> str:
    return path.read_text(encoding="utf-8").split("---", 2)[2]


def test_preview_writes_nothing_and_does_not_merge_media_names(tmp_path) -> None:
    root, ids = _corpus(tmp_path)
    before = {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*") if p.is_file()
    }

    plan = backfill_identity.prepare(root)
    summary = plan.summary()

    after = {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*") if p.is_file()
    }
    assert after == before
    assert summary["simulation"] is True
    assert summary["candidate_groups"] == [{
        "source": "same.mp3",
        "documents": [ids["a"], ids["b"]],
        "names": ["a.md", "b.md"],
        "confirmed": False,
    }]
    planned = {item["document_id"]: item for item in summary["documents"]}
    assert planned[ids["a"]]["meeting_id"] != planned[ids["b"]]["meeting_id"]


def test_confirmed_group_and_report_inheritance_are_applied(tmp_path) -> None:
    root, ids = _corpus(tmp_path)
    bodies = {p.name: _body(p) for p in root.rglob("*.md")}
    plan = backfill_identity.prepare(root, confirmed_groups=[[ids["a"], ids["b"]]])

    result = backfill_identity.apply(plan)

    assert result["simulation"] is False
    metas = {}
    for p in root.rglob("*.md"):
        metas[p.name] = yaml.safe_load(p.read_text().split("---", 2)[1])
        assert _body(p) == bodies[p.name]
    assert metas["a.md"]["meeting_id"] == metas["b.md"]["meeting_id"]
    assert metas["a.md"]["project_id"] == ids["inbox"]
    assert metas["b.md"]["project_id"] == ids["inbox"]
    assert metas["client.md"]["project_id"] == ids["client"]
    assert metas["a-cr.md"]["source_document_id"] == ids["a"]
    assert metas["a-cr.md"]["meeting_id"] == metas["a.md"]["meeting_id"]
    assert metas["a-cr.md"]["project_id"] == ids["inbox"]
    assert metas["ancien.md"]["project_id"] == ids["inbox"]

    sidecar = json.loads((root / ".voiceprints.json").read_text(encoding="utf-8"))
    assert list(sidecar) == [ids["a"]]
    assert (root / ".audio" / f"{ids['a']}.m4a").read_bytes() == b"listen"
    assert (root / ".audio" / "faithful" / f"{ids['a']}.flac").read_bytes() == b"faithful"
    manifest = json.loads((root / ".trash" / "manifest.json").read_text())
    assert manifest["ancien.md"]["document_id"] == ids["archived"]
    assert manifest["ancien.md"]["project_id"] == ids["inbox"]


def test_a_group_crossing_projects_is_refused(tmp_path) -> None:
    root, ids = _corpus(tmp_path)

    with pytest.raises(backfill_identity.IdentityMigrationError, match="projets"):
        backfill_identity.prepare(
            root, confirmed_groups=[[ids["a"], ids["client_doc"]]],
        )
