"""`diarize_on` doit piloter réellement le mode cloud.

`stt_mistral` demandait `diarize: True` en dur : décocher « Identifier les
locuteurs » n'avait aucun effet, et le pipeline enchaînait en plus une
extraction d'empreintes pyannote — un calcul LOCAL lourd dans un mode présenté
comme « Tout via Mistral Cloud ». L'option de l'utilisateur était ignorée et
l'étanchéité des deux pipelines rompue.
"""

from __future__ import annotations

from core import stt
from core.stt_mistral import MistralSTTEngine


def test_le_moteur_mistral_transmet_le_choix_de_l_utilisateur() -> None:
    assert MistralSTTEngine(diarize=False).diarize is False
    assert MistralSTTEngine().diarize is True, "diariser reste le défaut"


def test_deux_reglages_ne_partagent_pas_la_meme_instance() -> None:
    """Sans `diarize` dans la clé de cache, le premier appel figeait le moteur
    pour tous les suivants — option décochée comprise."""
    avec = stt.get_engine("mistral", api_key="x", diarize=True)
    sans = stt.get_engine("mistral", api_key="x", diarize=False)

    assert avec is not sans
    assert avec.diarize is True and sans.diarize is False


def test_le_pipeline_n_extrait_pas_d_empreintes_si_l_option_est_decochee(
        monkeypatch, tmp_path) -> None:
    """Le point le plus grave : un calcul pyannote LOCAL déclenché dans un mode
    « tout cloud », sans que l'utilisateur l'ait demandé."""
    from core import diarization, pipeline
    from core.stt_base import Segment, Transcript

    appels: list[str] = []
    monkeypatch.setattr(
        diarization, "empreintes_par_locuteur",
        lambda *a, **k: appels.append("extraction") or {})

    class FauxMoteurDiarisant:
        name, model_name = "mistral", "voxtral-mini-latest"

        def transcribe(self, wav_path, **kw):
            return Transcript(
                segments=[Segment(0.0, 4.0, "bonjour", "Speaker 1"),
                          Segment(4.0, 8.0, "salut", "Speaker 2")],
                full_text="bonjour salut", language="fr")

    monkeypatch.setattr(pipeline, "get_engine", lambda *a, **k: FauxMoteurDiarisant())
    monkeypatch.setattr(pipeline.media, "to_wav16k", lambda src, **k: src)
    monkeypatch.setattr(pipeline.media, "probe_duration", lambda src: 8.0)
    monkeypatch.setattr(pipeline.media, "pcm_sha256", lambda src: "f" * 64)
    monkeypatch.setattr(pipeline.media, "validate_input", lambda src: src)

    source = tmp_path / "essai.wav"
    source.write_bytes(b"RIFF")
    monkeypatch.setattr(pipeline, "_project_id_for_output", lambda d: "proj")
    monkeypatch.setattr(pipeline.provenance, "transcript_fingerprint",
                        lambda **k: {"fingerprint": "x"})
    sortie = tmp_path / "out"
    sortie.mkdir()

    pipeline.run(source, engine_name="mistral", diarize_on=False,
                 output_dir=sortie, timestamps=True)

    assert appels == [], "pyannote a tourné alors que l'option était décochée"


def test_le_choix_atteint_vraiment_la_requete_envoyee(monkeypatch, tmp_path) -> None:
    """Vérifier l'attribut ne suffit pas — une mutation l'a prouvé.

    Le premier test de ce fichier passait encore quand on remettait
    `"diarize": True` en dur dans la charge utile : il regardait l'objet, pas ce
    qui part sur le réseau. C'est le motif « un contrôle peut passer en
    regardant ailleurs ».
    """
    import requests

    envoyees: list[dict] = []

    class FausseReponse:
        ok, status_code = True, 200

        def json(self):
            return {"text": "", "segments": []}

    def faux_post(url, **kw):
        envoyees.append(dict(kw.get("data") or {}))
        return FausseReponse()

    monkeypatch.setattr(requests, "post", faux_post)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF0000")
    monkeypatch.setattr(
        "core.stt_mistral.MistralSTTEngine._preparer_envoi",
        lambda self, p, progress=None: (p, False))

    for demande, attendu in ((True, "true"), (False, "false")):
        envoyees.clear()
        MistralSTTEngine(api_key="x", diarize=demande).transcribe(audio)
        assert envoyees, "aucune requête n'a été envoyée"
        assert envoyees[0]["diarize"] == attendu, (
            f"diarize={demande} doit envoyer « {attendu} », "
            f"reçu « {envoyees[0]['diarize']} »"
        )


def test_le_choix_atteint_aussi_le_chemin_SDK(monkeypatch, tmp_path) -> None:
    """Le chemin SDK n'était couvert par AUCUN test.

    `mistralai` n'est pas installé ici, donc tout passe par le repli HTTP — et
    une mutation de la ligne SDK ne faisait donc échouer aucun test. Elle
    tournerait pourtant chez quiconque installe le paquet. On injecte un faux
    module pour l'exercer.
    """
    import sys
    import types

    vus: list[dict] = []

    class FauxClient:
        def __init__(self, api_key=None):
            self.audio = types.SimpleNamespace(
                transcriptions=types.SimpleNamespace(complete=self._complete))

        def _complete(self, file=None, **kwargs):
            vus.append(dict(kwargs))
            return {"text": "", "segments": []}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    faux = types.ModuleType("mistralai")
    faux.Mistral = FauxClient
    monkeypatch.setitem(sys.modules, "mistralai", faux)
    monkeypatch.setattr(
        "core.stt_mistral.MistralSTTEngine._preparer_envoi",
        lambda self, p, progress=None: (p, False))

    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF0000")
    for demande in (True, False):
        vus.clear()
        MistralSTTEngine(api_key="x", diarize=demande).transcribe(audio)
        assert vus and vus[0]["diarize"] is demande, (
            f"le SDK reçoit diarize={vus[0]['diarize'] if vus else '—'} "
            f"alors que l'utilisateur a demandé {demande}"
        )
