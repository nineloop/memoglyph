"""Contrat des moteurs STT sur un VRAI extrait audio.

Marqués `ml` : exclus de `make check` (la CI n'a ni GPU ni modèles). À lancer
avec `make test-ml`.

Ils n'affirment rien sur le texte exact — ce serait fragile et sans valeur. Ils
vérifient des **propriétés** que les défauts rencontrés violaient réellement :

  · horodatages monotones et bornés par la durée — le clamp de fin de tranche
    écrasait des segments sur la même valeur ;
  · absence de boucle de répétition — un fichier a produit 223 « what » d'affilée ;
  · granularité des segments — un décodage dégradé sortait un méga-segment par
    fenêtre de 30 s, ce qui fusionnait deux locuteurs dans un même tour et
    faisait inverser les rôles par le profil.

L'audio de référence n'est pas versionné (données personnelles, poids). Fournir
son chemin par MEMOGLYPH_ML_AUDIO, ou déposer un fichier dans
tests/fixtures/audio/. Sans lui, ces tests sont ignorés, pas en échec.
"""

import pathlib
from collections import Counter

import pytest

from core.config import getenv_compat

pytestmark = [pytest.mark.ml, pytest.mark.slow]

_FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "audio"


def _audio_de_reference() -> pathlib.Path:
    depuis_env = getenv_compat("ML_AUDIO")
    if depuis_env:
        p = pathlib.Path(depuis_env)
        if p.exists():
            return p
        pytest.skip(f"MEMOGLYPH_ML_AUDIO pointe sur un fichier absent : {p}")
    if _FIXTURES.is_dir():
        for f in sorted(_FIXTURES.iterdir()):
            if f.suffix.lower() in {".m4a", ".wav", ".mp3", ".flac", ".ogg"}:
                return f
    pytest.skip(
        "aucun audio de référence — définir MEMOGLYPH_ML_AUDIO=/chemin/extrait.m4a "
        f"ou déposer un fichier dans {_FIXTURES}"
    )


@pytest.fixture(scope="module")
def transcript():
    """Une seule transcription pour tout le module : c'est l'étape coûteuse."""
    audio = _audio_de_reference()
    pytest.importorskip("mlx_whisper", reason="moteur Whisper absent")
    from core import media, stt

    wav = media.to_wav16k(audio)
    try:
        yield stt.get_engine("whisper", language=None).transcribe(wav)
    finally:
        wav.unlink(missing_ok=True)
        stt.unload()


def test_produit_du_texte(transcript):
    assert transcript.segments, "aucun segment produit"
    assert sum(len(s.text.split()) for s in transcript.segments) > 20


def test_langue_detectee(transcript):
    assert transcript.language, "aucune langue détectée"
    assert len(transcript.language) <= 5


def test_horodatages_monotones(transcript):
    """Un segment ne peut pas commencer avant la fin du précédent d'un cran."""
    debuts = [s.start for s in transcript.segments]
    assert debuts == sorted(debuts), "horodatages non monotones"


def test_pas_de_segment_a_duree_negative(transcript):
    for s in transcript.segments:
        assert s.end >= s.start, f"segment inversé à {s.start:.1f}s"


def test_aucune_boucle_de_repetition(transcript):
    """Seuil du code : une boucle occupe tout le segment, un texte répétitif
    réel en reste loin."""
    fautifs = []
    for s in transcript.segments:
        mots = s.text.split()
        if len(mots) < 8:
            continue
        top = Counter(m.lower().strip(".,!?") for m in mots).most_common(1)[0][1]
        if top / len(mots) >= 0.6:
            fautifs.append(f"{s.start:.0f}s : {s.text[:60]}…")
    assert not fautifs, "boucle(s) de répétition détectée(s) :\n" + "\n".join(fautifs)


def test_granularite_des_segments(transcript):
    """Un méga-segment par fenêtre de 30 s est le symptôme d'un décodage dégradé :
    il fusionne les locuteurs et fait inverser les rôles en aval."""
    ecarts = [
        b.start - a.start
        for a, b in zip(transcript.segments, transcript.segments[1:])
        if b.start >= a.start
    ]
    if len(ecarts) < 5:
        pytest.skip("extrait trop court pour juger de la granularité")
    ecarts.sort()
    median = ecarts[len(ecarts) // 2]
    assert median < 15, f"écart médian de {median:.0f}s entre segments — décodage dégradé ?"


def test_la_fabrique_rend_un_moteur_utilisable():
    from core import stt
    moteur = stt.get_engine("whisper", language="fr")
    assert moteur.name == "whisper"
    assert hasattr(moteur, "transcribe")
