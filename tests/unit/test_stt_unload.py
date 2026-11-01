"""`stt.unload()` doit libérer les POIDS, pas seulement ses enveloppes.

Mesuré : `_cache.clear()` seul ne
rendait que **4 % de l'empreinte physique** — 2 650 Mo pris par une
transcription Whisper, 2 560 Mo encore résidents après l'appel. Les poids
vivent dans `mlx_whisper.transcribe.ModelHolder`, un singleton de CLASSE que
rien dans ce dépôt ne référençait, alors que la docstring promettait « la
mémoire MLX/GPU ».

Après correction, mesuré : **94 % rendus**, 2 494 Mo sur 2 650.

Ces tests n'ont pas besoin de charger Whisper : ils vérifient que le mécanisme
atteint sa cible et qu'il PARLE quand il ne l'atteint pas. La quantité, elle, a
été mesurée à la main — un test ne peut pas l'affirmer sans charger 2,6 Go.
"""

from __future__ import annotations

import sys
import types

from core import stt


def _faux_mlx(monkeypatch, *, avec_holder: bool = True):
    """Reproduit le piège du vrai paquet : `__init__` masque le sous-module.

    `mlx_whisper/__init__.py` réexporte la FONCTION `transcribe` sous le nom du
    sous-module. `from mlx_whisper import transcribe` ET
    `import mlx_whisper.transcribe as x` rendent donc la fonction — la forme
    `as` résout par attribut sur le paquet. Deux corrections successives sont
    tombées dans ce piège avant `importlib.import_module`.
    """
    paquet = types.ModuleType("mlx_whisper")
    sous_module = types.ModuleType("mlx_whisper.transcribe")
    if avec_holder:
        class ModelHolder:
            model = "POIDS"
            model_path = "/un/chemin"
        sous_module.ModelHolder = ModelHolder
    # Le masquage, reproduit fidèlement :
    paquet.transcribe = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "mlx_whisper", paquet)
    monkeypatch.setitem(sys.modules, "mlx_whisper.transcribe", sous_module)
    return sous_module


def test_les_poids_sont_reellement_liberes(monkeypatch) -> None:
    sous_module = _faux_mlx(monkeypatch)

    stt.unload()

    assert sous_module.ModelHolder.model is None
    assert sous_module.ModelHolder.model_path is None


def test_le_masquage_du_sous_module_ne_fait_pas_echouer(monkeypatch) -> None:
    """Le cœur de l'affaire : avec `from mlx_whisper import transcribe`, on
    récupérait la fonction et `ModelHolder` restait introuvable — alors que
    35 % de mémoire étaient quand même rendus par `gc` et le cache MLX, ce qui
    donnait l'illusion d'une correction réussie."""
    sous_module = _faux_mlx(monkeypatch)
    from mlx_whisper import transcribe as par_attribut

    assert not hasattr(par_attribut, "ModelHolder"), "le piège n'est pas reproduit"
    stt.unload()
    assert sous_module.ModelHolder.model is None


def test_un_holder_absent_est_DIT_et_non_tu(monkeypatch, capsys) -> None:
    """On touche à l'intérieur d'une dépendance : si une version future renomme
    `ModelHolder`, le retour silencieux à 4 % serait invisible. L'avertissement
    a déjà servi deux fois, sur des erreurs d'import."""
    _faux_mlx(monkeypatch, avec_holder=False)

    stt.unload()

    assert "ModelHolder introuvable" in capsys.readouterr().out


def test_l_absence_du_moteur_mlx_ne_fait_rien_echouer(monkeypatch) -> None:
    """Sur une machine sans MLX il n'y a rien à libérer, et ce n'est pas une
    erreur — le chemin cloud n'a pas besoin de Whisper."""
    monkeypatch.setitem(sys.modules, "mlx_whisper", None)

    stt.unload()  # ne doit pas lever


def test_le_pipeline_decharge_le_STT_meme_sans_profilage(monkeypatch, tmp_path) -> None:
    """Le défaut le plus coûteux en pratique.

    `stt.unload()` était À L'INTÉRIEUR du `if profile_on:`. Un parcours
    « profilage désactivé, compte-rendu activé » — parfaitement courant —
    n'essayait donc même pas de rendre Whisper avant de charger Qwen, et les
    deux se disputaient la RAM unifiée. Mesuré : 2,6 Go de poids inutiles
    pendant tout le compte-rendu.
    """
    from core import pipeline
    from core.stt_base import Segment, Transcript

    appels: list[str] = []
    monkeypatch.setattr(stt, "unload", lambda: appels.append("unload"))

    class FauxMoteur:
        name, model_name = "whisper", "large-v3"

        def transcribe(self, wav_path, **kw):
            return Transcript([Segment(0.0, 2.0, "bonjour", None)], "bonjour", "fr")

    monkeypatch.setattr(pipeline, "get_engine", lambda *a, **k: FauxMoteur())
    monkeypatch.setattr(pipeline.media, "to_wav16k", lambda s, **k: s)
    monkeypatch.setattr(pipeline.media, "probe_duration", lambda s: 2.0)
    monkeypatch.setattr(pipeline.media, "pcm_sha256", lambda s: "f" * 64)
    monkeypatch.setattr(pipeline.media, "validate_input", lambda s: s)
    monkeypatch.setattr(pipeline, "_project_id_for_output", lambda d: "proj")

    source = tmp_path / "a.wav"
    source.write_bytes(b"RIFF")
    sortie = tmp_path / "out"
    sortie.mkdir()
    pipeline.run(source, engine_name="whisper", profile_on=False, report_on=False,
                 output_dir=sortie, timestamps=True)

    assert appels == ["unload"], (
        "le STT n'est pas déchargé quand le profilage est désactivé"
    )
