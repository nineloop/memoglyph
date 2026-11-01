"""Logique pure des moteurs STT — sans charger le moindre modèle.

`stt_whisper` était à 0 % de couverture, et c'est pourtant là qu'ont vécu deux
défauts réels : le clamp d'horodatages qui écrasait des segments sur la même
valeur, et le report d'`initial_prompt` qui pouvait propager une boucle. Le
chargement des modèles n'est pas nécessaire pour éprouver cette logique : les
fonctions de découpe et de conversion de segments sont pures.
"""

import subprocess
from pathlib import Path

import pytest

from core import stt
from core.stt_base import Segment, Transcript

pytestmark = pytest.mark.unit


# --- Fabrique de moteurs -----------------------------------------------------

def test_moteur_inconnu_refuse_avec_les_choix():
    with pytest.raises(ValueError) as exc:
        stt.get_engine("sphinx")
    assert "whisper" in str(exc.value)
    assert "mistral" in str(exc.value)


def test_obtenir_moteur_mistral():
    stt.unload()
    m = stt.get_engine("mistral", api_key="dummy_key")
    assert m.name == "mistral"


def test_mistral_cle_manquante_leve_une_erreur(tmp_path):
    from core.stt_mistral import MistralSTTEngine

    dummy_file = tmp_path / "audio.wav"
    dummy_file.write_bytes(b"RIFF....WAVE")
    engine = MistralSTTEngine(api_key="")
    with pytest.raises(ValueError, match="MISTRAL_API_KEY manquante"):
        engine.transcribe(dummy_file)


def test_le_cache_rend_la_meme_instance():
    stt.unload()
    a = stt.get_engine("whisper", language="fr")
    b = stt.get_engine("whisper", language="fr")
    assert a is b, "le modèle serait rechargé à chaque fichier"


def test_le_cache_distingue_les_langues():
    """`language` fait partie de la clé : sinon un forçage fuiterait d'un fichier
    à l'autre, et un transcript anglais hériterait du « fr » du précédent."""
    stt.unload()
    fr = stt.get_engine("whisper", language="fr")
    en = stt.get_engine("whisper", language="en")
    assert fr is not en
    assert fr.language == "fr" and en.language == "en"


def test_unload_libere_le_cache():
    """Appelé avant le profilage LLM : sans ça, MLX et Ollama se disputent la RAM."""
    stt.unload()
    a = stt.get_engine("whisper")
    stt.unload()
    assert stt.get_engine("whisper") is not a


def test_le_defaut_est_whisper():
    stt.unload()
    assert stt.get_engine().name == "whisper"
    assert stt.DEFAULT_ENGINE == "whisper"


# --- Conversion des segments -------------------------------------------------

def _engine():
    from core.stt_whisper import WhisperEngine
    return WhisperEngine(language="fr")


def test_segments_decales_par_offset():
    res = {"segments": [{"start": 1.0, "end": 3.0, "text": "bonjour"}]}
    out = _engine()._to_segments(res, offset=10.0)
    assert (out[0].start, out[0].end, out[0].text) == (11.0, 13.0, "bonjour")


def test_segments_vides_ignores():
    res = {"segments": [
        {"start": 0.0, "end": 1.0, "text": "   "},
        {"start": 1.0, "end": 2.0, "text": "vrai texte"},
    ]}
    out = _engine()._to_segments(res)
    assert [s.text for s in out] == ["vrai texte"]


def test_le_clamp_borne_les_debordements():
    """Whisper pad à 30 s : sans borne, un segment dépassait la tranche et cassait
    la monotonie des horodatages entre tranches."""
    res = {"segments": [{"start": 5.0, "end": 40.0, "text": "déborde"}]}
    out = _engine()._to_segments(res, offset=0.0, span=10.0)
    assert out[0].end == 10.0


def test_sans_span_aucun_clamp():
    res = {"segments": [{"start": 5.0, "end": 40.0, "text": "libre"}]}
    out = _engine()._to_segments(res, offset=0.0)
    assert out[0].end == 40.0


def test_resultat_sans_segments():
    assert _engine()._to_segments({}) == []


# --- Découpe aux creux d'énergie ---------------------------------------------

@pytest.fixture
def np():
    return pytest.importorskip("numpy")


def test_les_tranches_couvrent_tout_l_audio(np):
    from core.stt_whisper import _cut_points
    from core.config import SAMPLE_RATE
    audio = np.zeros(SAMPLE_RATE * 10, dtype=np.float32)
    bounds = _cut_points(audio, chunk_sec=3.0)
    assert bounds[0][0] == 0
    assert bounds[-1][1] == len(audio)
    for (_, fin), (debut, _) in zip(bounds, bounds[1:]):
        assert fin == debut, "les tranches doivent être jointives, sans trou ni recouvrement"


def test_audio_plus_court_qu_une_tranche(np):
    from core.stt_whisper import _cut_points
    from core.config import SAMPLE_RATE
    audio = np.zeros(SAMPLE_RATE * 2, dtype=np.float32)
    assert _cut_points(audio, chunk_sec=60.0) == [(0, len(audio))]


def test_la_coupe_vise_le_creux_d_energie(np):
    """C'est la raison d'être de la fonction : ne pas couper au milieu d'un mot."""
    from core.stt_whisper import _cut_points
    from core.config import SAMPLE_RATE
    audio = np.ones(SAMPLE_RATE * 8, dtype=np.float32)  # « parole » partout…
    silence = int(3.5 * SAMPLE_RATE)
    audio[silence:silence + SAMPLE_RATE // 2] = 0.0     # …sauf un silence à 3,5 s
    bounds = _cut_points(audio, chunk_sec=4.0)
    coupe = bounds[0][1] / SAMPLE_RATE
    assert 3.4 <= coupe <= 4.1, f"coupe à {coupe:.2f}s, hors du silence visé"


# --- Types partagés ----------------------------------------------------------

def test_transcript_porte_ses_segments():
    t = Transcript(
        segments=[Segment(0.0, 1.0, "a", speaker="Speaker 1")],
        full_text="a", language="fr",
    )
    assert t.language == "fr" and t.segments[0].speaker == "Speaker 1"


# --- Seuil de détection des boucles ------------------------------------------
# Le test ML `test_aucune_boucle_de_repetition` applique ce critère sur un vrai
# audio ; ici on éprouve le SEUIL lui-même, pour que la CI le protège sans GPU.

def _ratio_max(texte: str) -> float:
    from collections import Counter
    mots = texte.split()
    if len(mots) < 8:
        return 0.0
    return Counter(m.lower().strip(".,!?") for m in mots).most_common(1)[0][1] / len(mots)


@pytest.mark.parametrize("texte,attendu", [
    # Boucles réellement observées, mode découpé
    ("what " * 40, True),
    ("no " * 25, True),
    ("You know there are there there are there there there there there", True),
    # Français normal, y compris le segment le plus répétitif du corpus
    ("Merci pour les émotions, les buts, les joies, les cris et les larmes", False),
    ("C'est rare de voir une équipe de France jouer de cette manière ce soir", False),
    ("De l'histoire de la Coupe du Monde, il restera ce match et ce joueur", False),
])
def test_le_seuil_separe_boucles_et_francais_normal(texte, attendu):
    """Une boucle occupe tout le segment ; du français répétitif réel en reste
    loin. Le seuil à 60 % laisse une marge des deux côtés — un seuil bien plus bas
    aurait signalé du texte parfaitement valide."""
    assert (_ratio_max(texte) >= 0.6) is attendu


def test_un_texte_court_n_est_jamais_signale():
    """Sous 8 mots, une répétition est trop probable pour conclure."""
    assert _ratio_max("oui oui oui") == 0.0


# --- Provider Mistral câblé de bout en bout ----------------------------------

def test_get_engine_mistral_transporte_la_langue_forcee():
    """`language` était ignoré à la construction du moteur Mistral : « fr » forcé
    retombait en détection auto, en silence."""
    stt.unload()
    m = stt.get_engine("mistral", language="fr", api_key="k")
    assert m.language == "fr"


def test_voxtral_est_un_alias_de_mistral_et_garde_la_langue():
    stt.unload()
    m = stt.get_engine("voxtral", language="en", api_key="k")
    assert m.name == "mistral" and m.language == "en"


def test_le_moteur_mistral_ENREGISTRE_la_langue(monkeypatch, tmp_path):
    """Le front-matter tombait à `language: None` : l'API renvoie la langue,
    elle doit atterrir sur le Transcript."""
    from core.stt_mistral import MistralSTTEngine
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    engine = MistralSTTEngine(api_key="k")  # pas de langue forcée
    monkeypatch.setattr(
        engine, "_call_api",
        lambda *a, **k: {"text": "bonjour", "language": "fr", "segments": []},
    )
    assert engine.transcribe(wav).language == "fr"


def test_la_langue_FORCEE_prime_sur_la_detectee(monkeypatch, tmp_path):
    from core.stt_mistral import MistralSTTEngine
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    engine = MistralSTTEngine(api_key="k", language="en")
    monkeypatch.setattr(
        engine, "_call_api",
        lambda *a, **k: {"text": "hi", "language": "fr", "segments": []},
    )
    assert engine.transcribe(wav).language == "en"


def test_classify_genre_vise_le_MEME_provider_que_la_generation(monkeypatch):
    """Sans ce câblage, un run Mistral classait via Ollama (défaut) : indisponible
    chez un Mistral-only → gabarit générique EN SILENCE."""
    from core import report
    vu = {}

    def faux_chat(system, user, *, provider="ollama", api_key=None, **k):
        vu["provider"], vu["api_key"] = provider, api_key
        return "interview"

    monkeypatch.setattr(report.llm, "chat", faux_chat)
    report.classify_genre(
        "## Profil\n\ntexte de profil", "fr",
        provider="mistral", mistral_api_key="K",
    )
    assert vu == {"provider": "mistral", "api_key": "K"}


# --- Compression avant envoi au moteur distant --------------------------

def test_la_compression_est_desactivable(tmp_path, monkeypatch):
    """`MISTRAL_UPLOAD_KBPS=0` doit envoyer le WAV tel quel."""
    from core import config
    from core.stt_mistral import MistralSTTEngine

    monkeypatch.setattr(config, "MISTRAL_UPLOAD_KBPS", 0)
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    chemin, temporaire = MistralSTTEngine()._preparer_envoi(wav)
    assert chemin == wav and temporaire is False


def test_un_fichier_deja_compresse_n_est_pas_re_encode(tmp_path):
    """Ré-encoder de l'AAC en AAC dégraderait pour rien."""
    from core.stt_mistral import MistralSTTEngine

    m4a = tmp_path / "a.m4a"
    m4a.write_bytes(b"ftyp")
    chemin, temporaire = MistralSTTEngine()._preparer_envoi(m4a)
    assert chemin == m4a and temporaire is False


def test_un_echec_de_compression_ne_casse_pas_la_transcription(tmp_path, monkeypatch):
    """La transcription prime : on repart sur le WAV, sans lever."""
    from core import media
    from core.stt_mistral import MistralSTTEngine

    def indisponible() -> None:
        raise RuntimeError("ffmpeg absent")

    monkeypatch.setattr(media, "ensure_ffmpeg", indisponible)
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    chemin, temporaire = MistralSTTEngine()._preparer_envoi(wav)
    assert chemin == wav and temporaire is False


def test_un_wav_est_compresse_et_signale_comme_temporaire(tmp_path, monkeypatch):
    """Le fichier compressé est marqué pour suppression : il ne doit pas
    s'accumuler dans le cache à chaque transcription."""
    from core import config, media
    from core.stt_mistral import MistralSTTEngine

    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(config, "MISTRAL_UPLOAD_KBPS", 64)
    monkeypatch.setattr(media, "ensure_ffmpeg", lambda: None)

    def faux_ffmpeg(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"aac-compresse")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", faux_ffmpeg)
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF" * 100)
    chemin, temporaire = MistralSTTEngine()._preparer_envoi(wav)
    assert temporaire is True
    assert chemin.suffix == ".m4a" and chemin.exists()


# --- Identifiants de locuteur renvoyés par l'API Mistral -----------------

def _transcrire_avec(monkeypatch, segments):
    """Fait passer une charge utile d'API par le formatage, sans réseau."""
    from core.stt_mistral import MistralSTTEngine

    moteur = MistralSTTEngine(api_key="factice")
    monkeypatch.setattr(
        moteur, "_call_api",
        lambda *a, **k: {"text": "peu importe", "segments": segments, "language": "fr"},
    )
    monkeypatch.setattr(moteur, "_preparer_envoi", lambda src, progress=None: (src, False))
    return moteur.transcribe(Path("factice.wav"))


def test_un_locuteur_d_indice_zero_n_est_pas_perdu(monkeypatch):
    """`speaker_id` valant 0 est un indice LÉGITIME. Traité comme absent, il
    écrivait « Speaker ? » dans le .md — qui fait foi — et décalait les autres."""
    transcript = _transcrire_avec(monkeypatch, [
        {"start": 0.0, "end": 1.0, "text": "premier", "speaker_id": 0},
        {"start": 1.0, "end": 2.0, "text": "second", "speaker_id": 1},
    ])
    locuteurs = [s.speaker for s in transcript.segments]
    assert locuteurs == ["Speaker 1", "Speaker 2"], (
        f"le locuteur 0 doit être étiqueté, pas ignoré — obtenu {locuteurs}"
    )


def test_un_identifiant_entier_et_sa_forme_texte_sont_le_meme_locuteur(monkeypatch):
    """Sinon `0` et `\"0\"` créeraient deux locuteurs pour une seule personne."""
    transcript = _transcrire_avec(monkeypatch, [
        {"start": 0.0, "end": 1.0, "text": "a", "speaker_id": 2},
        {"start": 1.0, "end": 2.0, "text": "b", "speaker_id": "2"},
    ])
    assert [s.speaker for s in transcript.segments] == ["Speaker 1", "Speaker 1"]


def test_un_identifiant_absent_ou_vide_reste_sans_locuteur(monkeypatch):
    """Pas de faux positif : une chaîne vide n'est pas un identifiant."""
    transcript = _transcrire_avec(monkeypatch, [
        {"start": 0.0, "end": 1.0, "text": "a"},
        {"start": 1.0, "end": 2.0, "text": "b", "speaker_id": "   "},
    ])
    assert [s.speaker for s in transcript.segments] == [None, None]


def test_le_repli_sur_la_cle_speaker_fonctionne_toujours(monkeypatch):
    """`speaker` reste consulté quand `speaker_id` est absent."""
    transcript = _transcrire_avec(monkeypatch, [
        {"start": 0.0, "end": 1.0, "text": "a", "speaker": "SPEAKER_00"},
    ])
    assert transcript.segments[0].speaker == "Speaker 1"
