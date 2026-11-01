"""Le backfill des empreintes doit repartir de l'ORIGINAL, pas de l'archive.

`output/.audio/*.m4a` n'est pas une copie : `media.keep_audio` ré-encode en mono
AAC 48 kbps, un format fait pour l'écoute dans le navigateur. Rejoué depuis
l'archive, le pipeline confond des locuteurs que l'original distinguait. Ce chemin
alimente le registre de voix,
seul magasin qui traverse les fichiers — une empreinte issue de deux voix
fusionnées y contaminerait tous les fichiers suivants.
"""

import pytest

from core import backfill

pytestmark = pytest.mark.unit


@pytest.fixture
def faux_arbre(tmp_path, monkeypatch):
    (tmp_path / "inputs").mkdir()
    (tmp_path / "cache").mkdir()
    monkeypatch.setattr(backfill, "ROOT", tmp_path)
    monkeypatch.setattr(backfill, "CACHE_DIR", tmp_path / "cache")
    return tmp_path


def test_l_original_gagne_sur_l_archive(faux_arbre, monkeypatch):
    original = faux_arbre / "inputs" / "reunion.mp4"
    original.write_bytes(b"original")
    archive = faux_arbre / "archive.m4a"
    archive.write_bytes(b"reencode 48k")
    monkeypatch.setattr(backfill.media, "audio_path", lambda n, root=None: archive)

    assert backfill._find_source_audio("reunion.mp4", "reunion.md") == original


def test_l_archive_reste_le_repli(faux_arbre, monkeypatch):
    """Les uploads sont effacés après traitement : sans ce repli, plus aucune
    empreinte ne serait calculable. Mieux vaut imparfait que rien."""
    archive = faux_arbre / "archive.m4a"
    archive.write_bytes(b"reencode 48k")
    monkeypatch.setattr(backfill.media, "audio_path", lambda n, root=None: archive)

    assert backfill._find_source_audio("disparu.mp4", "reunion.md") == archive


def test_rien_nulle_part(faux_arbre, monkeypatch):
    manquant = faux_arbre / "absent.m4a"
    monkeypatch.setattr(backfill.media, "audio_path", lambda n, root=None: manquant)
    assert backfill._find_source_audio("disparu.mp4", "reunion.md") is None
    assert backfill._find_source_audio(None, "") is None
