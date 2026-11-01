"""Corpus de démo : récupération vérifiée, sans réseau dans les tests."""

import hashlib
import io

import pytest

from tools import corpus_demo

pytestmark = pytest.mark.unit

CONTENU = b"RIFF-demo"
FICHIER = corpus_demo.Fichier("a.wav", "https://exemple.invalid/a.wav",
                              hashlib.sha256(CONTENU).hexdigest(), len(CONTENU))


def test_les_empreintes_declarees_sont_des_sha256() -> None:
    assert len(corpus_demo.FICHIERS) == 4
    for f in corpus_demo.FICHIERS:
        assert len(f.sha256) == 64 and int(f.sha256, 16) >= 0, f.nom
        assert f.url.startswith("https://groups.inf.ed.ac.uk/ami/"), f.nom


def test_etat_absent_conforme_different(tmp_path) -> None:
    assert corpus_demo.etat(FICHIER, tmp_path) == "absent"
    (tmp_path / "a.wav").write_bytes(CONTENU)
    assert corpus_demo.etat(FICHIER, tmp_path) == "conforme"
    (tmp_path / "a.wav").write_bytes(b"autre chose")
    assert corpus_demo.etat(FICHIER, tmp_path) == "different"


def test_un_telechargement_altere_ne_laisse_aucun_fichier(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(corpus_demo.urllib.request, "urlopen",
                        lambda *a, **k: io.BytesIO(b"contenu altere"))
    with pytest.raises(RuntimeError, match="empreinte"):
        corpus_demo.telecharger(FICHIER, tmp_path)
    assert not (tmp_path / "a.wav").exists()
    assert not (tmp_path / "a.wav.part").exists()


def test_un_telechargement_conforme_est_ecrit(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(corpus_demo.urllib.request, "urlopen",
                        lambda *a, **k: io.BytesIO(CONTENU))
    corpus_demo.telecharger(FICHIER, tmp_path)
    assert (tmp_path / "a.wav").read_bytes() == CONTENU
    assert not (tmp_path / "a.wav.part").exists()


def test_verifier_ne_telecharge_rien(tmp_path, monkeypatch) -> None:
    def interdit(*a, **k):
        raise AssertionError("aucun téléchargement attendu en mode --verifier")
    monkeypatch.setattr(corpus_demo.urllib.request, "urlopen", interdit)
    assert corpus_demo.main(["--verifier"], dossier=tmp_path) == 1
