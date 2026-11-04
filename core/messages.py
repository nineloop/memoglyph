"""Messages destinés à l'utilisateur : un CODE stable, des paramètres, un texte.

Le backend émettait des phrases françaises. L'interface, devenue bilingue, les
affichait telles quelles : en anglais, le panneau de traitement restait français
du début à la fin. Traduire une phrase reçue reviendrait à la deviner.

**Règle** : un code stable (`step.diarization`),
des paramètres nommés, et un texte **anglais** par défaut. L'interface traduit
depuis le code ; un `curl` sur `/api/jobs` reste lisible sans elle.

L'anglais comme défaut est un choix, pas une commodité : **l'API est une
interface machine, l'interface humaine localise**.

## Pourquoi `Message` hérite de `str`

Le rappel de progression est typé `Callable[[float, str], None]` et traverse
`core/pipeline.py`, `api/jobs.py`, les instantanés de job et le flux SSE. En
changer la signature aurait touché chaque maillon, pour un gain nul : la
plupart n'ont besoin que du texte.

Un `Message` EST donc une chaîne — sa valeur est le texte anglais. Tout
consommateur existant continue de fonctionner sans rien savoir, y compris la
sérialisation JSON. Ceux qui veulent traduire lisent `.code` et `.params`.

La contrepartie, à connaître : `json.dumps` d'un `Message` rend le texte et
PERD le code. C'est pourquoi `api/jobs.py` construit son événement
explicitement au lieu de sérialiser l'objet — un test le vérifie.
"""

from __future__ import annotations

from typing import Any


class Message(str):
    """Texte anglais, plus le code et les paramètres qui permettent de traduire."""

    code: str
    params: dict[str, Any]

    def __new__(cls, code: str, texte: str, **params: Any) -> "Message":
        objet = super().__new__(cls, texte)
        objet.code = code
        objet.params = params
        return objet

    def __repr__(self) -> str:  # pragma: no cover — confort de débogage
        return f"Message({self.code!r}, {str(self)!r}, {self.params!r})"

    def pour_evenement(self) -> dict[str, Any]:
        """Forme publiée sur le fil : code, paramètres, ET texte de repli."""
        return {"code": self.code, "params": self.params, "message": str(self)}


def evenement_message(msg: Any) -> dict[str, Any]:
    """Normalise un message, qu'il porte un code ou non.

    Une chaîne nue reste possible — un message d'exception, un texte venu d'une
    dépendance. Elle est publiée sans code, et l'interface la montre telle
    quelle. Refuser ces cas obligerait à inventer un code par erreur système,
    ce qui reviendrait à traduire des messages qu'on ne maîtrise pas.
    """
    if isinstance(msg, Message):
        return msg.pour_evenement()
    return {"code": None, "params": {}, "message": str(msg)}


# ---------- Catalogue ----------
#
# Un code par étape RÉELLE du pipeline. Les paramètres sont nommés et non
# interpolés dans le code : `{"name": "x"}` et non `done.x`, sans quoi le
# catalogue de traduction serait infini.

def preparation_audio() -> Message:
    return Message("step.audio.prepare", "Preparing audio…")


def audio_pret() -> Message:
    return Message("step.audio.ready", "Audio ready.")


def diarisation() -> Message:
    return Message("step.diarization", "Identifying speakers…")


def empreintes_moteur_diarise() -> Message:
    return Message(
        "step.voiceprints.after_native_diarization",
        "Extracting voiceprints (the engine already identified speakers)…",
    )


def profilage(modele: str) -> Message:
    return Message("step.profiling", f"Profiling with {modele}…", model=modele)


def langue_deduite(code: str) -> Message:
    """Le moteur n'a pas déclaré la langue ; on l'a déduite du TEXTE.

    Annoncé plutôt que silencieux : cette langue va ancrer toutes les sorties
    LLM en aval, et l'utilisateur doit pouvoir la contredire s'il la voit fausse.
    """
    return Message("step.languageInferred",
                   f"Language not declared by the engine — inferred as « {code} » "
                   f"from the text.", language=code)


def ecriture_markdown() -> Message:
    return Message("step.markdown", "Writing the Markdown document…")


def compte_rendu() -> Message:
    return Message("step.report", "Generating the report (Map-Reduce)…")


# Étapes du compte-rendu LOCAL riche (extraction atomique → strates → rédaction).
# Mêmes codes stables que le reste : l'UI traduit, l'API reste lisible.
def compte_rendu_extraction() -> Message:
    return Message("step.report.extract", "Reading the transcript in detail…")


def compte_rendu_organisation() -> Message:
    return Message("step.report.organize", "Organizing content by importance…")


def compte_rendu_redaction() -> Message:
    return Message("step.report.write", "Writing the report…")


def compte_rendu_hors_contrat(erreur: str, tentative: int, total: int) -> Message:
    """Le modèle a rendu un CR hors contrat ; on redemande.

    Ce n'est PAS un échec : c'est une attente supplémentaire, et l'utilisateur
    doit savoir pourquoi la barre ne bouge plus. Un silence de plusieurs
    secondes se lit comme un blocage.
    """
    return Message(
        "warn.report.retry",
        f"Report out of contract ({erreur}) — retrying {tentative}/{total}…",
        error=erreur, attempt=tentative, total=total,
    )


def profilage_echoue(erreur: str) -> Message:
    """Le PENDANT de `compte_rendu_echoue`, qui manquait.

    Le compte-rendu échouait déjà sans emporter le transcript ; le profilage,
    lui, levait — et il tourne AVANT l'écriture du Markdown, donc son échec
    faisait perdre une transcription réussie. Deux enrichissements optionnels du
    même fournisseur, deux traitements opposés.
    """
    return Message(
        "warn.profiling.failed",
        f"Profiling failed: {erreur}",
        error=erreur,
    )


def compte_rendu_echoue(erreur: str) -> Message:
    return Message(
        "warn.report.failed",
        f"Report generation failed: {erreur}",
        error=erreur,
    )


def diarisation_echouee(erreur: str) -> Message:
    """Le pendant de `profilage_echoue` pour la diarisation Pyannote.

    Elle était le SEUL enrichissement optionnel non protégé : elle
    levait entre un STT réussi et l'écriture, emportant la transcription.
    Enveloppée comme le profilage et le compte-rendu désormais, et le `.md`
    porte la trace.
    """
    return Message(
        "warn.diarization.failed",
        f"Speaker identification failed, the transcript is kept: {erreur}",
        error=erreur,
    )


def termine(nom: str) -> Message:
    return Message("step.done", f"Done — {nom}", name=nom)


def transcription(moteur: str) -> Message:
    return Message("step.transcription", f"Transcribing with {moteur}…", engine=moteur)


def envoi_api(fournisseur: str) -> Message:
    return Message(
        "step.upload", f"Sending audio to {fournisseur}…", provider=fournisseur)


def chargement_modele(quoi: str) -> Message:
    return Message("step.model.loading", f"Loading the {quoi} model…", what=quoi)


def telechargement_modele(quoi: str) -> Message:
    """Premier usage : le modèle se télécharge (plusieurs Go) dans l'appel qui
    suit, sans signal propre.

    Constat : la barre restait figée vers 11 % pendant des
    minutes, indiscernable d'un blocage. On DIT que c'est un téléchargement, et
    qu'il ne se reproduira pas.
    """
    return Message(
        "step.model.downloading",
        f"Downloading the {quoi} model (first use — this can take several "
        f"minutes)…",
        what=quoi,
    )


def preparation_modele_local() -> Message:
    """Le modèle LOCAL se prépare, sans qu'on puisse PROUVER si c'est un
    téléchargement ou un simple chargement.

    Message HONNÊTE de repli : on ne prétend pas savoir. Utilisé quand la lecture
    du cache Hugging Face ne tranche pas (import indisponible, repo atypique).
    Le premier usage peut nécessiter un gros téléchargement — on le DIT sans
    l'affirmer.
    """
    return Message(
        "step.model.preparing",
        "Preparing the local model. First use may require downloading several "
        "GB.",
    )


def locuteurs_detectes(nombre: int) -> Message:
    return Message(
        "step.diarization.done",
        f"{nombre} speaker(s) detected.",
        count=nombre,
    )


# ---------- Sous-moteurs ----------
#
# Ces messages viennent des moteurs eux-mêmes, atteints par `_scaled()`. Ils
# étaient restés en français nu après le premier passage — et comme ils sont
# émis JUSTE APRÈS le message d'étape de l'orchestrateur, ils l'écrasaient à
# l'écran en quelques millisecondes. Corriger l'orchestrateur seul ne changeait
# donc presque rien de ce que l'utilisateur lit.

def transcription_whisper(langue: str) -> Message:
    return Message(
        "step.transcription.whisper",
        f"Transcribing with Whisper (language: {langue})…",
        language=langue,
    )


def transcription_progression(fait: int, total: int) -> Message:
    return Message(
        "step.transcription.chunk",
        f"Transcribing… {fait}/{total}",
        done=fait,
        total=total,
    )


def transcription_terminee() -> Message:
    return Message("step.transcription.done", "Transcription complete.")


def envoi_mistral() -> Message:
    return Message(
        "step.upload.mistral",
        "Sending to the Mistral AI API (Voxtral Mini, native diarization)…",
    )


def nouvelle_tentative_mistral(statut: int, delai_s: int) -> Message:
    return Message(
        "step.upload.mistral.retry",
        f"Mistral refused the request (HTTP {statut}), retrying in {delai_s} s…",
        status=statut,
        delay_s=delai_s,
    )


def pause_limite_audio_mistral(delai_s: int) -> Message:
    return Message(
        "step.upload.mistral.pause",
        f"Pause imposed by Mistral's audio limit, resuming in {delai_s} s…",
        delay_s=delai_s,
    )


def audio_compresse(avant_mo: int, apres_mo: int) -> Message:
    return Message(
        "step.upload.compressed",
        f"Audio compressed for upload: {avant_mo} MB → {apres_mo} MB",
        before_mb=avant_mo,
        after_mb=apres_mo,
    )


def formatage_segments() -> Message:
    return Message("step.segments.format", "Formatting segments and speakers…")


def transcription_mistral_terminee() -> Message:
    return Message("step.transcription.mistral.done", "Mistral AI transcription complete.")


def chargement_diarisation() -> Message:
    return Message("step.diarization.loading", "Loading the diarization model…")


def analyse_locuteurs() -> Message:
    return Message("step.diarization.analyzing", "Analysing speakers…")


def analyse_llm() -> Message:
    return Message("step.profiling.running", "LLM analysis in progress…")


def analyse_terminee() -> Message:
    return Message("step.profiling.done", "Analysis complete.")


def prechauffage_llm() -> Message:
    return Message("step.profiling.warmup", "Warming up / checking the LLM model…")


def tranches_resumees() -> Message:
    return Message("step.report.chunks", "Report: sections summarised.")


def classification_genre() -> Message:
    return Message("step.report.classify", "Report: classifying the document kind…")


def trop_d_attente(label: str) -> Message:
    """Le créneau lourd n'a pas été obtenu dans le délai : un code, que l'interface traduit.

    Le texte anglais reste celui d'avant (`job.error` le garde pour les clients existants).
    """
    return Message(
        "step.error.waitTooLong",
        f"Ressources occupées : un autre {label} est en cours. Réessaie dans un moment — "
        "cette machine ne traite qu'une tâche lourde à la fois.",
        label=label)


def traitement_annule() -> Message:
    """Un traitement annulé avant d'avoir commencé : un échec à cause codée, sans nouvel état (chantier 005).

    La phrase est courte et propre : le compagnon lit le texte brut de `job.error`.
    """
    return Message("step.error.cancelled", "Traitement annulé.")
