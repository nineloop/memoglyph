"""Sûreté du cycle de vie des fichiers : confinement des projets et
correspondance exacte des archives. Ce sont les deux endroits où un défaut
entraîne une perte de contenu."""

from __future__ import annotations

import pytest

from core import library, purge


@pytest.fixture
def out(tmp_path, monkeypatch):
    d = tmp_path / "output"
    d.mkdir()
    for mod in (library, purge):
        monkeypatch.setattr(mod, "OUTPUT_DIR", d, raising=False)
    monkeypatch.setattr(library, "DB_PATH", tmp_path / "index.db")
    return d


# ---------- Confinement des projets ----------

@pytest.mark.parametrize("bad", ["..", "../..", "A/B", "a\\b", ".", ".trash", ".audio", ".caché"])
def test_dangerous_project_names_are_refused(out, bad) -> None:
    with pytest.raises(library.UnsafeProject):
        library.project_dir(bad)


@pytest.mark.parametrize("name", ["Réunions 2026", "Client X", "etc"])
def test_valid_project_stays_inside_output(out, name) -> None:
    d = library.project_dir(name)
    assert out.resolve() in d.resolve().parents


def test_default_project_is_the_output_root(out) -> None:
    assert library.DEFAULT_PROJECT == "Non classé"
    for value in (None, "", "  ", library.DEFAULT_PROJECT):
        assert library.project_dir(value) == out


def test_leading_slash_is_not_an_escape(out) -> None:
    """« /etc » devient un projet nommé etc, pas /etc."""
    assert library.project_dir("/etc") == out / "etc"


# ---------- Correspondance exacte des archives ----------

def _make(out, name: str, body: str = "x") -> None:
    p = out / name
    p.write_text(f"---\ntitle: {p.stem}\n---\n\n{body}\n", encoding="utf-8")
    library.sync_file(p)


def test_archiving_records_origin_in_manifest(out) -> None:
    _make(out, "foo.md")
    assert library.archive("foo.md")
    item = library.trash_items()[0]
    assert item["origin"] == "foo.md"
    assert item["project"] == library.DEFAULT_PROJECT


def test_purge_does_not_touch_similarly_named_archives(out) -> None:
    """Supprimer « foo.md » ne doit PAS emporter foobar.md ni foo-autre.md."""
    for n in ("foo.md", "foobar.md", "foo-autre.md"):
        _make(out, n)
        library.archive(n)
    assert len(library.trash_items()) == 3

    purge.purge_transcript("foo.md")

    restants = sorted(i["name"] for i in library.trash_items())
    assert restants == ["foo-autre.md", "foobar.md"]


def test_purge_removes_every_copy_of_the_same_transcript(out) -> None:
    """Deux archivages successifs du même nom -> foo.md puis foo.1.md."""
    _make(out, "foo.md")
    library.archive("foo.md")
    _make(out, "foo.md")
    library.archive("foo.md")
    assert sorted(i["name"] for i in library.trash_items()) == ["foo.1.md", "foo.md"]

    purge.purge_transcript("foo.md")
    assert library.trash_items() == []


def test_archived_names_stay_reserved(out) -> None:
    """Un nom en corbeille ne doit pas être réattribué : sinon deux générations
    partageraient audio et empreintes."""
    from core.writer_md import unique_path

    _make(out, "reunion.md")
    library.archive("reunion.md")
    assert unique_path("reunion", out).name != "reunion.md"
