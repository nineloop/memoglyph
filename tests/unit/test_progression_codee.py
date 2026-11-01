"""Tout ce qui atteint l'écran doit porter un code — producteurs compris.

Le premier passage n'avait codé que les onze appels de `core/pipeline.py`. Les
sous-moteurs, atteints par `_scaled()`, continuaient d'émettre des chaînes
françaises nues : `stt_whisper`, `stt_mistral`, `diarization`, `profiling`,
`report`. Elles arrivaient dans `JobManager` en `{code: null, message:
"<français>"}`, et l'interface anglaise affichait leur repli français.

Le pire n'est pas la langue, c'est le moment : ces messages sont émis JUSTE
APRÈS celui de l'orchestrateur, qu'ils écrasent en quelques millisecondes.
Corriger l'orchestrateur seul ne changeait donc presque rien de ce que
l'utilisateur lit.

Le contrôle précédent — la table de complétude du catalogue — prouvait que le
CATALOGUE était complet, pas que les producteurs s'en servaient. Il regardait le
bon objet, mais pas toutes ses entrées.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core.messages import Message

RACINE = Path(__file__).resolve().parents[2]

# Modules dont la progression atteint l'écran d'un traitement.
MODULES_DE_PROGRESSION = [
    "core/pipeline.py",
    "core/stt_whisper.py",
    "core/stt_mistral.py",
    "core/diarization.py",
    "core/profiling.py",
    "core/report.py",
]


def _appels_avec_texte_nu(chemin: Path) -> list[str]:
    """Appels `progress(...)`/`report(...)` dont le message est un littéral.

    Analyse l'AST plutôt que le texte : une f-string, une concaténation ou un
    littéral sur plusieurs lignes échappent tous à une recherche par motif —
    et c'est exactement sous ces formes que se cachaient six des dix-neuf.
    """
    arbre = ast.parse(chemin.read_text(encoding="utf-8"))
    fautifs: list[str] = []
    for noeud in ast.walk(arbre):
        if not isinstance(noeud, ast.Call):
            continue
        nom = getattr(noeud.func, "id", None)
        if nom not in ("progress", "report") or len(noeud.args) < 2:
            continue
        message = noeud.args[1]
        if isinstance(message, (ast.Constant, ast.JoinedStr, ast.BinOp)):
            extrait = ast.unparse(message)[:60]
            fautifs.append(f"{chemin.relative_to(RACINE)}:{noeud.lineno} → {extrait}")
    return fautifs


@pytest.mark.parametrize("module", MODULES_DE_PROGRESSION)
def test_aucun_producteur_n_emet_un_texte_nu(module: str) -> None:
    """Le contrôle qui GÉNÉRALISE : il vaut pour les messages à venir.

    Vérifier une séquence exécutée ne couvre que les chemins qu'on a pensé à
    parcourir. Vérifier la SOURCE couvre aussi celui qu'on ajoutera demain.
    """
    fautifs = _appels_avec_texte_nu(RACINE / module)
    assert fautifs == [], (
        "message de progression en clair — il arriverait sans code et "
        f"l'interface afficherait son texte brut : {fautifs}"
    )


def test_toute_la_sequence_d_un_traitement_porte_un_code(monkeypatch, tmp_path) -> None:
    """Le critère : capturer la séquence RÉELLE de `run()`.

    Le test de source ci-dessus interdit les littéraux ; celui-ci vérifie ce
    qui sort effectivement, y compris ce que relaie `_scaled()`.
    """
    from core import messages, pipeline
    from core.stt_base import Segment, Transcript

    recus: list[tuple[float, object]] = []

    class MoteurQuiAnnonce:
        """Émet, via le callback, les messages d'un vrai sous-moteur."""

        name, model_name = "whisper", "whisper-large-v3-turbo"

        def transcribe(self, wav_path, progress=None, **kw):
            if progress:
                progress(0.02, messages.transcription_whisper("fr"))
                progress(0.5, messages.transcription_progression(1, 2))
                progress(1.0, messages.transcription_terminee())
            return Transcript(
                segments=[Segment(0.0, 4.0, "bonjour", None)],
                full_text="bonjour", language="fr")

    monkeypatch.setattr(pipeline, "get_engine", lambda *a, **k: MoteurQuiAnnonce())
    monkeypatch.setattr(pipeline.media, "to_wav16k", lambda src, **k: src)
    monkeypatch.setattr(pipeline.media, "probe_duration", lambda src: 4.0)
    monkeypatch.setattr(pipeline.media, "pcm_sha256", lambda src: "f" * 64)
    monkeypatch.setattr(pipeline.media, "validate_input", lambda src: src)
    monkeypatch.setattr(pipeline.media, "archive_faithful", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.media, "keep_original", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "_project_id_for_output", lambda d: "proj")

    source = tmp_path / "essai.wav"
    source.write_bytes(b"RIFF")

    # Aucun `try`/`pytest.skip` ici, volontairement : un test qui skippe ne
    # rapporte RIEN, et c'est exactement la forme de panne qu'on traque. Si le
    # pipeline cesse de tourner, ce test doit ÉCHOUER, pas se taire.
    pipeline.run(
        source,
        progress=lambda f, m: recus.append((f, m)),
        diarize_on=False, profile_on=False, report_on=False,
        output_dir=tmp_path / "corpus",
    )

    assert recus, "aucune progression capturée — le test regarderait à côté"
    sans_code = [
        f"{frac:.2f} → {msg!r}" for frac, msg in recus
        if not isinstance(msg, Message)
    ]
    assert sans_code == [], (
        f"étape(s) sans code dans la séquence réelle : {sans_code}")
