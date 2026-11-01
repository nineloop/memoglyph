"""Un enrichissement optionnel qui échoue ne fait pas perdre la transcription.

C'est l'invariant le plus cher du pipeline : la transcription est le travail
irremplaçable — l'audio a été traité, la diarisation aussi, et l'utilisateur ne
peut pas les refaire gratuitement. Le profil et le compte-rendu, eux, se
régénèrent à volonté depuis le `.md`.

## Ce que ces tests ferment

`core/pipeline.py` appelait `profiling.profile()` SANS `try`, à l'étape 4, alors
que le Markdown ne s'écrit qu'à l'étape 5 — et `core/profiling.py` lève quand le
fournisseur ne répond pas. Un serveur LLM éteint détruisait donc une
transcription réussie.

L'asymétrie vivait dans la même fonction : le compte-rendu de l'étape 6 est
enveloppé depuis longtemps, avec le commentaire « le transcript reste généré ».
Deux enrichissements optionnels du même fournisseur, deux traitements opposés,
et c'est celui d'AVANT la publication qui n'était pas protégé.

**Aucun des deux n'était testé.** La garantie du compte-rendu reposait sur un
`try/except` que rien n'exerçait ; celle du profilage n'existait pas. Ce fichier
couvre les deux, parce qu'une garantie non mesurée n'en est pas une.

## Pourquoi on n'y bouchonne pas la façade LLM

Le cas qui compte est celui d'un fournisseur INDISPONIBLE, et c'est
`llm.is_available` qui le décide. Bouchonner plus haut testerait un scénario
inventé ; on coupe donc au même endroit que la réalité.
"""

from __future__ import annotations

import pytest
import yaml

from core import library

pytestmark = pytest.mark.unit


def _pipeline_bouchonne(monkeypatch, tmp_path):
    """Le pipeline sans ses dépendances lourdes — ni audio, ni modèle."""
    from core import pipeline
    from core.stt_base import Segment, Transcript

    class FauxMoteur:
        name, model_name = "whisper", "mlx-community/whisper-test"

        def transcribe(self, wav_path, **kw):
            return Transcript(
                segments=[Segment(0.0, 2.0, "bonjour tout le monde", None)],
                full_text="bonjour tout le monde", language="fr")

    monkeypatch.setattr(pipeline, "get_engine", lambda *a, **k: FauxMoteur())
    monkeypatch.setattr(pipeline.media, "to_wav16k", lambda src, **k: src)
    monkeypatch.setattr(pipeline.media, "probe_duration", lambda src: 2.0)
    monkeypatch.setattr(pipeline.media, "pcm_sha256", lambda src: "f" * 64)
    monkeypatch.setattr(pipeline.media, "validate_input", lambda src: src)
    monkeypatch.setattr(pipeline.media, "archive_audio", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.media, "archive_faithful", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.media, "keep_original", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "_project_id_for_output", lambda d: "proj")
    monkeypatch.setattr(pipeline.llm, "unload", lambda **k: False)

    source = tmp_path / "reunion.wav"
    source.write_bytes(b"RIFF")
    sortie = tmp_path / "out"
    sortie.mkdir()
    return pipeline, source, sortie


def _front_matter(contenu: str) -> dict:
    meta, _ = library._split_frontmatter(contenu)
    return meta or yaml.safe_load(contenu.split("---")[1])


def test_un_fournisseur_LLM_ETEINT_ne_fait_pas_perdre_la_transcription(
    monkeypatch, tmp_path
) -> None:
    """LE cas destructeur, coupé là où la réalité coupe.

    `profiling.profile` interroge `llm.is_available` et lève si le fournisseur
    ne répond pas. Avant ce lot, cette exception traversait `pipeline.run` : le
    texte transcrit, la diarisation et les empreintes étaient perdus avec elle.
    """
    from core import llm, profiling

    pipeline, source, sortie = _pipeline_bouchonne(monkeypatch, tmp_path)
    monkeypatch.setattr(llm, "is_available", lambda **k: False)
    # Le vrai chemin : on ne bouchonne pas `profile`, on éteint le fournisseur.
    assert profiling.profile is not None

    chemin, contenu = pipeline.run(
        source, engine_name="whisper", llm_provider="mlx", profile_on=True,
        report_on=False, output_dir=sortie, timestamps=True)

    assert chemin.exists(), "le document n'a pas été écrit"
    assert "bonjour tout le monde" in contenu, "le texte transcrit est perdu"

    fm = _front_matter(contenu)
    assert fm["profiled"] is False
    assert fm.get("llm_model") is None
    assert "profiling_error" in fm, (
        "le document ne dit pas que le profilage a ÉCHOUÉ — un profil absent se "
        "lit alors comme un enregistrement sans contenu analysable"
    )


def test_un_profilage_REUSSI_ne_laisse_aucune_trace_d_echec(monkeypatch, tmp_path) -> None:
    """Un champ absent ne prétend rien ; un champ à `null` obligerait à
    distinguer « pas essayé » de « réussi ». Même règle que `voiceprint_error`."""
    from core import profiling

    pipeline, source, sortie = _pipeline_bouchonne(monkeypatch, tmp_path)
    monkeypatch.setattr(profiling, "profile", lambda *a, **k: {"speakers": {}})

    _, contenu = pipeline.run(
        source, engine_name="whisper", llm_provider="mlx", profile_on=True,
        report_on=False, output_dir=sortie, timestamps=True)

    fm = _front_matter(contenu)
    assert fm["profiled"] is True
    assert "profiling_error" not in fm


def test_un_profilage_NON_DEMANDE_ne_laisse_aucune_trace_d_echec(
    monkeypatch, tmp_path
) -> None:
    pipeline, source, sortie = _pipeline_bouchonne(monkeypatch, tmp_path)

    _, contenu = pipeline.run(
        source, engine_name="whisper", llm_provider="mlx", profile_on=False,
        report_on=False, output_dir=sortie, timestamps=True)

    fm = _front_matter(contenu)
    assert fm["profiled"] is False
    assert "profiling_error" not in fm


def test_l_echec_de_profilage_est_ANNONCE_par_un_code_de_message(
    monkeypatch, tmp_path
) -> None:
    """La progression doit le dire, et par un CODE — pas une phrase française.

    Décision 1 : le backend émet un code stable, l'interface traduit. Une
    chaîne écrite ici ne serait traduisible dans aucune langue.
    """
    from core import llm

    pipeline, source, sortie = _pipeline_bouchonne(monkeypatch, tmp_path)
    monkeypatch.setattr(llm, "is_available", lambda **k: False)

    codes: list[str] = []
    pipeline.run(
        source, engine_name="whisper", llm_provider="mlx", profile_on=True,
        report_on=False, output_dir=sortie, timestamps=True,
        progress=lambda p, m: codes.append(getattr(m, "code", str(m))))

    assert "warn.profiling.failed" in codes, codes


def test_un_COMPTE_RENDU_qui_echoue_ne_perd_pas_non_plus_la_transcription(
    monkeypatch, tmp_path
) -> None:
    """La garantie existait, elle n'était exercée par AUCUN test.

    Elle repose sur un `try/except` de l'étape 6. Le retirer ne faisait rougir
    personne — vérifié par mutation.
    """
    from core import profiling, report as report_mod

    pipeline, source, sortie = _pipeline_bouchonne(monkeypatch, tmp_path)
    monkeypatch.setattr(profiling, "profile", lambda *a, **k: {"speakers": {}})

    def echoue(*a, **k):
        raise RuntimeError("le moteur a rendu du JSON hors contrat")

    monkeypatch.setattr(report_mod, "generate_report", echoue)

    chemin, contenu = pipeline.run(
        source, engine_name="whisper", llm_provider="mlx", profile_on=True,
        report_on=True, output_dir=sortie, timestamps=True)

    assert chemin.exists()
    assert "bonjour tout le monde" in contenu


def test_les_DEUX_enrichissements_peuvent_echouer_ensemble(monkeypatch, tmp_path) -> None:
    """Le pire cas plausible sur une machine sans LLM : rien ne doit être perdu."""
    from core import llm, report as report_mod

    pipeline, source, sortie = _pipeline_bouchonne(monkeypatch, tmp_path)
    monkeypatch.setattr(llm, "is_available", lambda **k: False)

    def echoue(*a, **k):
        raise RuntimeError("indisponible")

    monkeypatch.setattr(report_mod, "generate_report", echoue)

    chemin, contenu = pipeline.run(
        source, engine_name="whisper", llm_provider="mlx", profile_on=True,
        report_on=True, output_dir=sortie, timestamps=True)

    assert chemin.exists()
    assert "bonjour tout le monde" in contenu
    assert "profiling_error" in _front_matter(contenu)


# ---------------------------------------------------------------------------
# La DIARISATION — voir `core/messages.py` (`diarisation_echouee`)
# ---------------------------------------------------------------------------
#
# La diarisation Pyannote était le SEUL des trois enrichissements optionnels
# sans `try` : elle tourne entre un STT réussi (étape 2) et l'écriture du .md
# (étape 5), et lève quand la dépendance `--group diar` est absente, quand
# HF_TOKEN est refusé, ou sur un crash pyannote. Une transcription réussie était
# donc perdue — exactement l'asymétrie déjà fermée pour le profilage.


def _forcer_diarisation(monkeypatch, exc: Exception | None):
    """Bouchonne `diarization.diarize` : lève `exc`, ou n'est jamais atteint."""
    from core import diarization

    def _diar(*a, **k):
        if exc is not None:
            raise exc
        raise AssertionError("diarize ne devait pas être appelée")

    monkeypatch.setattr(diarization, "diarize", _diar)


def test_une_DIARISATION_qui_echoue_ne_perd_pas_la_transcription(
    monkeypatch, tmp_path
) -> None:
    """LE cas : dépendance/permission absente, le texte survit quand même."""
    pipeline, source, sortie = _pipeline_bouchonne(monkeypatch, tmp_path)
    _forcer_diarisation(monkeypatch, RuntimeError("groupe --group diar absent"))

    chemin, contenu = pipeline.run(
        source, engine_name="whisper", llm_provider="mlx", diarize_on=True,
        profile_on=False, report_on=False, output_dir=sortie, timestamps=True)

    assert chemin.exists(), "le document n'a pas été écrit"
    assert "bonjour tout le monde" in contenu, "le texte transcrit est perdu"

    fm = _front_matter(contenu)
    assert fm["diarization"] is False, "la diarisation a échoué, pas réussi"
    assert "diarization_error" in fm, (
        "le document ne dit pas que la diarisation a ÉCHOUÉ — un corpus sans "
        "voix ressemble sinon à un enregistrement sans locuteurs distincts")


def test_l_echec_de_diarisation_est_ANNONCE_par_un_code_de_message(
    monkeypatch, tmp_path
) -> None:
    """Un code stable (`warn.diarization.failed`), pas une phrase française."""
    pipeline, source, sortie = _pipeline_bouchonne(monkeypatch, tmp_path)
    _forcer_diarisation(monkeypatch, RuntimeError("HF_TOKEN refusé"))

    codes: list[str] = []
    pipeline.run(
        source, engine_name="whisper", llm_provider="mlx", diarize_on=True,
        profile_on=False, report_on=False, output_dir=sortie, timestamps=True,
        progress=lambda p, m: codes.append(getattr(m, "code", str(m))))

    assert "warn.diarization.failed" in codes, codes


def test_une_diarisation_NON_DEMANDEE_ne_laisse_aucune_trace(
    monkeypatch, tmp_path
) -> None:
    """Champ absent, pas `null` : « non demandée » ≠ « tentée et échouée »."""
    pipeline, source, sortie = _pipeline_bouchonne(monkeypatch, tmp_path)
    _forcer_diarisation(monkeypatch, None)  # ne doit jamais être appelée

    _, contenu = pipeline.run(
        source, engine_name="whisper", llm_provider="mlx", diarize_on=False,
        profile_on=False, report_on=False, output_dir=sortie, timestamps=True)

    assert "diarization_error" not in _front_matter(contenu)


# ---------------------------------------------------------------------------
# Persistance IMMÉDIATE après STT (voir `core/pipeline.py`, `run`)
# ---------------------------------------------------------------------------
#
# Les gardes ci-dessus rattrapent un enrichissement qui LÈVE. Mais un crash
# natif Metal/MLX pendant le profilage ou le CR tue le processus SANS passer par
# un `except`. La seule protection est d'ÉCRIRE la transcription sur le disque
# avant les enrichissements, puis de réécrire le même fichier une fois enrichi.


def test_le_transcript_est_sur_le_disque_AVANT_la_diarisation(
    monkeypatch, tmp_path
) -> None:
    """Au moment où la diarisation est appelée, le .md existe déjà."""
    from core import diarization

    pipeline, source, sortie = _pipeline_bouchonne(monkeypatch, tmp_path)
    vus: dict = {}

    def _diar_espionne(*a, **k):
        vus["mds"] = [p.read_text(encoding="utf-8") for p in sortie.glob("*.md")]
        raise RuntimeError("on s'arrête après avoir constaté l'écriture précoce")

    monkeypatch.setattr(diarization, "diarize", _diar_espionne)

    pipeline.run(
        source, engine_name="whisper", llm_provider="mlx", diarize_on=True,
        profile_on=False, report_on=False, output_dir=sortie, timestamps=True)

    assert vus.get("mds"), "aucun .md sur le disque au moment de la diarisation"
    assert any("bonjour tout le monde" in c for c in vus["mds"]), (
        "le .md précoce ne contient pas le texte transcrit")
    # Le document précoce se DÉCLARE interrompu tant qu'il n'est pas finalisé.
    assert any(_front_matter(c).get("interrupted") is True for c in vus["mds"]), (
        "le .md précoce ne porte pas le marqueur `interrupted`")


def test_un_crash_DUR_apres_STT_ne_perd_pas_le_transcript(
    monkeypatch, tmp_path
) -> None:
    """Un `SystemExit` (comme un crash natif) pendant le profilage n'est PAS
    attrapé par `except Exception` — et pourtant le transcript survit, parce
    qu'il a été écrit avant."""
    from core import profiling

    pipeline, source, sortie = _pipeline_bouchonne(monkeypatch, tmp_path)

    def _crash(*a, **k):
        raise SystemExit("crash natif simulé pendant le profilage")

    monkeypatch.setattr(profiling, "profile", _crash)

    with pytest.raises(SystemExit):
        pipeline.run(
            source, engine_name="whisper", llm_provider="mlx", profile_on=True,
            report_on=False, output_dir=sortie, timestamps=True)

    mds = list(sortie.glob("*.md"))
    assert mds, "le transcript a été perdu par le crash — écriture précoce absente"
    contenus = [p.read_text(encoding="utf-8") for p in mds]
    assert any("bonjour tout le monde" in c for c in contenus)
    # Le document rescapé est IDENTIFIABLE comme partiel : il n'a jamais été
    # finalisé, donc il porte encore `interrupted: true`.
    assert any(_front_matter(c).get("interrupted") is True for c in contenus), (
        "le document rescapé d'un crash ne se déclare pas interrompu")


def test_ecriture_precoce_puis_enrichie_ne_fait_PAS_de_doublon(
    monkeypatch, tmp_path
) -> None:
    """Les deux écritures visent le MÊME chemin : `unique_path` n'est appelé
    qu'une fois, `write_md` écrase. Un run complet laisse UN seul .md."""
    pipeline, source, sortie = _pipeline_bouchonne(monkeypatch, tmp_path)

    chemin, contenu = pipeline.run(
        source, engine_name="whisper", llm_provider="mlx", profile_on=False,
        report_on=False, output_dir=sortie, timestamps=True)

    mds = list(sortie.glob("*.md"))
    assert len(mds) == 1, f"attendu un seul .md, trouvé {[p.name for p in mds]}"
    assert mds[0] == chemin
    # Un run COMPLET ne porte JAMAIS le marqueur : l'écriture finale l'a retiré.
    assert "interrupted" not in _front_matter(contenu), (
        "un document finalisé ne doit pas se déclarer interrompu")
