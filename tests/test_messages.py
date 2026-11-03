"""Le contrat des messages : un code stable, des paramètres, un texte anglais.

Ce fichier existe parce que le basculement des onze messages de `pipeline.py`
du français vers l'anglais codé n'a fait échouer **aucun** des 806 tests. Une
absence d'échec n'est pas une preuve de justesse quand rien ne regarde.
"""

from __future__ import annotations

import json

import pytest

from core import messages


def test_un_message_est_une_chaine_pour_qui_ne_sait_rien():
    """Le contrat de compatibilité : tout consommateur existant voit du texte."""
    msg = messages.diarisation()
    assert isinstance(msg, str)
    assert msg == "Identifying speakers…"
    assert f"{msg}" == "Identifying speakers…"
    assert msg.upper().startswith("IDENT")


def test_le_code_et_les_parametres_voyagent_avec():
    msg = messages.profilage("qwen2.5:7b")
    assert msg.code == "step.profiling"
    assert msg.params == {"model": "qwen2.5:7b"}
    assert "qwen2.5:7b" in msg


def test_les_parametres_ne_sont_pas_interpoles_dans_le_code():
    """Sinon le catalogue de traduction serait infini : un code par valeur."""
    a = messages.termine("reunion-mai.md")
    b = messages.termine("reunion-juillet.md")
    assert a.code == b.code == "step.done"
    assert a.params != b.params


def test_serialiser_un_message_directement_PERD_le_code():
    """La contrepartie de l'héritage de `str`, vérifiée plutôt que supposée.

    C'est précisément pourquoi `api/jobs.py` construit son événement champ par
    champ. Si ce test se met un jour à échouer, c'est que `Message` n'hérite
    plus de `str` — et la construction manuelle devient inutile.
    """
    msg = messages.diarisation()
    assert json.loads(json.dumps({"m": msg})) == {"m": "Identifying speakers…"}


def test_evenement_message_normalise_une_chaine_nue():
    """Un message d'exception n'a pas de code, et doit passer quand même."""
    evt = messages.evenement_message("MemoryError: out of memory")
    assert evt == {
        "code": None,
        "params": {},
        "message": "MemoryError: out of memory",
    }


def test_evenement_message_deplie_un_message_code():
    evt = messages.evenement_message(messages.locuteurs_detectes(3))
    assert evt["code"] == "step.diarization.done"
    assert evt["params"] == {"count": 3}
    assert evt["message"] == "3 speaker(s) detected."


CONSTRUCTEURS = [
    (messages.trop_d_attente, ("traitement",)),
    (messages.preparation_audio, ()),
    (messages.audio_pret, ()),
    (messages.diarisation, ()),
    (messages.empreintes_moteur_diarise, ()),
    (messages.profilage, ("m",)),
    (messages.langue_deduite, ("en",)),
    (messages.ecriture_markdown, ()),
    (messages.compte_rendu, ()),
    (messages.compte_rendu_extraction, ()),
    (messages.compte_rendu_organisation, ()),
    (messages.compte_rendu_redaction, ()),
    (messages.compte_rendu_hors_contrat, ("boom", 1, 3)),
    (messages.profilage_echoue, ("boom",)),
    (messages.diarisation_echouee, ("boom",)),
    (messages.compte_rendu_echoue, ("boom",)),
    (messages.termine, ("x.md",)),
    (messages.transcription, ("whisper",)),
    (messages.envoi_api, ("Mistral",)),
    (messages.chargement_modele, ("embedding",)),
    (messages.telechargement_modele, ("whisper-large-v3-turbo",)),
    (messages.preparation_modele_local, ()),
    (messages.locuteurs_detectes, (2,)),
    # Sous-moteurs.
    (messages.transcription_whisper, ("fr",)),
    (messages.transcription_progression, (1, 2)),
    (messages.transcription_terminee, ()),
    (messages.envoi_mistral, ()),
    (messages.audio_compresse, (120, 30)),
    (messages.nouvelle_tentative_mistral, (429, 10)),
    (messages.pause_limite_audio_mistral, (52,)),
    (messages.formatage_segments, ()),
    (messages.transcription_mistral_terminee, ()),
    (messages.chargement_diarisation, ()),
    (messages.analyse_locuteurs, ()),
    (messages.analyse_llm, ()),
    (messages.analyse_terminee, ()),
    (messages.prechauffage_llm, ()),
    (messages.tranches_resumees, ()),
    (messages.classification_genre, ()),
]


@pytest.mark.parametrize("fabrique,args", CONSTRUCTEURS)
def test_chaque_message_a_un_code_non_vide_et_un_texte(fabrique, args):
    msg = fabrique(*args)
    assert msg.code and msg.code.strip() == msg.code
    assert str(msg).strip()


# Table de référence des textes par défaut.
#
# Première version : « aucun caractère accentué ». Une mutation l'a prise en
# défaut — remettre « Diarisation (qui parle)… » PASSAIT, ce texte n'ayant pas
# un seul accent. Le contrôle était vert en regardant ailleurs.
#
# Une table nominative ne peut pas se tromper de cible : tout écart échoue, et
# le test de complétude ci-dessous oblige à l'étendre à chaque nouveau message.
TEXTES_ATTENDUS = {
    # Texte historique de `ResourcesBusy`, conservé tel quel pour les clients existants ; le CODE est ce qui se traduit.
    "step.error.waitTooLong": ("Ressources occupées : un autre traitement est en cours. Réessaie dans un moment — "
                               "cette machine ne traite qu'une tâche lourde à la fois."),
    "step.audio.prepare": "Preparing audio…",
    "step.audio.ready": "Audio ready.",
    "step.diarization": "Identifying speakers…",
    "step.voiceprints.after_native_diarization":
        "Extracting voiceprints (the engine already identified speakers)…",
    "step.profiling": "Profiling with m…",
    "step.languageInferred": "Language not declared by the engine — inferred as « en » from the text.",
    "step.markdown": "Writing the Markdown document…",
    "step.report": "Generating the report (Map-Reduce)…",
    "step.report.extract": "Reading the transcript in detail…",
    "step.report.organize": "Organizing content by importance…",
    "step.report.write": "Writing the report…",
    "warn.report.retry": "Report out of contract (boom) — retrying 1/3…",
    "warn.profiling.failed": "Profiling failed: boom",
    "warn.diarization.failed": "Speaker identification failed, the transcript is kept: boom",
    "warn.report.failed": "Report generation failed: boom",
    "step.done": "Done — x.md",
    "step.transcription": "Transcribing with whisper…",
    "step.upload": "Sending audio to Mistral…",
    "step.model.loading": "Loading the embedding model…",
    "step.model.downloading": "Downloading the whisper-large-v3-turbo model (first use — this can take several minutes)…",
    "step.model.preparing": "Preparing the local model. First use may require downloading several GB.",
    "step.diarization.done": "2 speaker(s) detected.",
    "step.transcription.whisper": "Transcribing with Whisper (language: fr)…",
    "step.transcription.chunk": "Transcribing… 1/2",
    "step.transcription.done": "Transcription complete.",
    "step.upload.mistral":
        "Sending to the Mistral AI API (Voxtral Mini, native diarization)…",
    "step.upload.compressed": "Audio compressed for upload: 120 MB → 30 MB",
    "step.upload.mistral.retry":
        "Mistral refused the request (HTTP 429), retrying in 10 s…",
    "step.upload.mistral.pause":
        "Pause imposed by Mistral's audio limit, resuming in 52 s…",
    "step.segments.format": "Formatting segments and speakers…",
    "step.transcription.mistral.done": "Mistral AI transcription complete.",
    "step.diarization.loading": "Loading the diarization model…",
    "step.diarization.analyzing": "Analysing speakers…",
    "step.profiling.running": "LLM analysis in progress…",
    "step.profiling.done": "Analysis complete.",
    "step.profiling.warmup": "Warming up / checking the LLM model…",
    "step.report.chunks": "Report: sections summarised.",
    "step.report.classify": "Report: classifying the document kind…",
}


@pytest.mark.parametrize("fabrique,args", CONSTRUCTEURS)
def test_le_texte_par_defaut_est_celui_attendu_en_anglais(fabrique, args):
    """Règle : l'API est une interface MACHINE (voir `core/messages.py`).

    Un `curl` sur `/api/jobs` doit rester lisible sans notre interface, et
    l'anglais est la langue par défaut d'une interface de programmation.
    """
    msg = fabrique(*args)
    assert str(msg) == TEXTES_ATTENDUS[msg.code]


def test_la_table_de_reference_couvre_tous_les_messages():
    """Sans ça, ajouter un message français échapperait au test précédent."""
    codes = {f(*a).code for f, a in CONSTRUCTEURS}
    assert codes == set(TEXTES_ATTENDUS)


def test_les_codes_sont_tous_distincts():
    """Deux étapes qui partagent un code deviennent indiscernables à l'écran."""
    codes = [f(*a).code for f, a in CONSTRUCTEURS]
    assert len(codes) == len(set(codes))


def test_aucun_constructeur_n_echappe_a_la_table():
    """Le trou de la première version : la liste CONSTRUCTEURS était écrite à la
    main, donc un message ajouté sans l'y inscrire n'était vérifié par rien.
    On dérive maintenant la liste du module lui-même.
    """
    import inspect

    publics = {
        nom for nom, obj in vars(messages).items()
        if inspect.isfunction(obj) and not nom.startswith("_")
        and nom != "evenement_message" and obj.__module__ == messages.__name__
    }
    couverts = {f.__name__ for f, _ in CONSTRUCTEURS}
    assert publics == couverts, (
        f"message(s) sans couverture : {sorted(publics - couverts)}")
