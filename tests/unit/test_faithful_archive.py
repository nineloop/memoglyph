"""Archive FIDÈLE : sans perte, à la résolution que les modèles consomment.

L'archive d'écoute (`output/.audio/*.m4a`, AAC 48 kbps mono) n'est pas une copie :
rejouer le pipeline depuis elle confond des locuteurs, et l'accord image par image
avec l'original n'est pas parfait. Le FLAC 16 kHz mono l'est — parce que
`to_wav16k` réduit de toute façon tout à `-ac 1 -ar 16000` : cette archive EST
l'entrée des modèles.
"""

import pytest

from core import config, media, purge

pytestmark = pytest.mark.unit


def test_le_chemin_fidele_est_distinct_de_celui_d_ecoute(tmp_path):
    ecoute = media.audio_path("reunion.md", root=tmp_path)
    fidele = media.faithful_path("reunion.md", root=tmp_path)
    assert fidele != ecoute
    assert fidele.suffix == ".flac"
    assert fidele.parent.name == config.FAITHFUL_DIRNAME
    assert ".audio" in str(fidele)


def test_les_deux_archives_partagent_la_cle_du_transcript(tmp_path):
    """Même clé — le nom du .md — sinon la purge ne retrouverait pas la paire."""
    assert (
        media.faithful_path("reunion.md", root=tmp_path).stem
        == media.audio_path("reunion.md", root=tmp_path).stem
    )


def test_une_identite_document_est_directement_la_cle_audio(tmp_path):
    from core import docid

    document_id = docid.nouveau()

    assert media.audio_path(document_id, root=tmp_path).stem == document_id
    assert media.faithful_path(document_id, root=tmp_path).stem == document_id


def test_drop_audio_efface_AUSSI_l_archive_fidele(tmp_path):
    """Sans ça, `purge.is_clean()` mentirait : elle vit dans un sous-dossier et
    échappe au balayage par extension."""
    fidele = media.faithful_path("reunion.md", root=tmp_path)
    fidele.parent.mkdir(parents=True)
    fidele.write_bytes(b"flac")
    ecoute = media.audio_path("reunion.md", root=tmp_path)
    ecoute.parent.mkdir(parents=True, exist_ok=True)
    ecoute.write_bytes(b"aac")

    media.drop_audio("reunion.md", root=tmp_path)

    assert not fidele.exists(), "l'archive fidèle survivrait à une purge « définitive »"
    assert not ecoute.exists()


def test_drop_audio_balaie_aussi_le_flac_a_la_racine(tmp_path):
    """`.flac` manquait à la liste d'extensions balayées."""
    d = tmp_path / ".audio"
    d.mkdir(parents=True)
    (d / "reunion.flac").write_bytes(b"flac")
    media.drop_audio("reunion.md", root=tmp_path)
    assert not (d / "reunion.flac").exists()


def test_la_purge_compte_l_archive_fidele_dans_ses_traces():
    """`traces()` est le contrat de « purge vérifiable » : un magasin absent de
    cette liste est un magasin qu'on oublie."""
    t = purge.traces("inexistant.md")
    assert "faithful" in t, "l'archive fidèle doit figurer dans l'inventaire des traces"


def test_desactivable(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "KEEP_FAITHFUL", False)
    assert media.archive_faithful(tmp_path / "x.mp4", "reunion.md", root=tmp_path) is None


# --- La garantie, éprouvée sur un vrai encodage ------------------------------

def _pcm_md5(src):
    """Empreinte des échantillons que les modèles reçoivent réellement."""
    import hashlib
    import subprocess
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(src),
         "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
        capture_output=True,
    )
    return hashlib.md5(out.stdout).hexdigest()


@pytest.fixture
def audio_synthetique(tmp_path):
    """Généré par ffmpeg : pas de fixture binaire à versionner."""
    import shutil
    import subprocess
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg absent")
    src = tmp_path / "source.wav"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=3", "-ar", "44100", "-ac", "2",
         str(src), "-y"],
        capture_output=True,
    )
    return src


def test_l_archive_fidele_est_IDENTIQUE_bit_a_bit(audio_synthetique, tmp_path):
    """Le cœur du contrat, et il se vérifie sans diarisation.

    Mesuré aussi sur un vrai fichier de 19 min : même MD5 de PCM que l'original
    (3a7df3d7…). Ce n'est pas « 100 % d'accord », c'est une garantie — tout
    modèle futur recevra exactement les mêmes échantillons.
    """
    fidele = media.archive_faithful(audio_synthetique, "essai.md", root=tmp_path)
    assert fidele is not None and fidele.exists()
    assert _pcm_md5(fidele) == _pcm_md5(audio_synthetique)


def test_l_archive_d_ecoute_n_est_PAS_identique(audio_synthetique, tmp_path):
    """Le témoin : sans lui, le test précédent pourrait passer pour une raison
    triviale (par exemple si les deux chemins renvoyaient le même fichier)."""
    ecoute = media.archive_audio(audio_synthetique, "essai.md", root=tmp_path)
    if ecoute is None:
        pytest.skip("archive d'écoute désactivée")
    assert _pcm_md5(ecoute) != _pcm_md5(audio_synthetique)


def test_le_format_est_celui_que_to_wav16k_produit(audio_synthetique, tmp_path):
    """16 kHz, mono, s16 — ni plus, ni moins. En 24 bits, le volume doublait
    pour une précision que les modèles ne voient jamais."""
    import subprocess
    fidele = media.archive_faithful(audio_synthetique, "essai.md", root=tmp_path)
    info = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=sample_fmt,sample_rate,channels,codec_name",
         "-of", "default=noprint_wrappers=1", str(fidele)],
        capture_output=True, text=True,
    ).stdout
    assert "sample_rate=16000" in info
    assert "channels=1" in info
    assert "sample_fmt=s16" in info
    assert "codec_name=flac" in info
