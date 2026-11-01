"""Profilage de la conversation et des locuteurs.

Deux niveaux :
- **Quantitatif** (sans LLM) : temps de parole, part, nb de tours, mots par locuteur.
- **Qualitatif** (via Ollama) : type de conversation, résumé, rôle/ton/points clés
  de chaque locuteur.
"""

from __future__ import annotations

from typing import Callable, Optional

from . import config, llm, names, sensitivity
from . import messages
from .stt_base import Transcript

Progress = Callable[[float, str], None]


def _fmt_hms(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def speaker_stats(transcript: Transcript) -> dict[str, dict]:
    """Stats par locuteur (pur calcul). {} si non diarisé."""
    segs = [s for s in transcript.segments if s.speaker]
    if not segs:
        return {}

    total_time = sum(s.end - s.start for s in segs) or 1.0
    stats: dict[str, dict] = {}
    prev_speaker = None
    for s in transcript.segments:
        if not s.speaker:
            continue
        d = stats.setdefault(
            s.speaker, {"talk_time_sec": 0.0, "turns": 0, "words": 0}
        )
        d["talk_time_sec"] += s.end - s.start
        d["words"] += len(s.text.split())
        if s.speaker != prev_speaker:  # nouveau tour de parole
            d["turns"] += 1
        prev_speaker = s.speaker

    for spk, d in stats.items():
        d["talk_time"] = _fmt_hms(d["talk_time_sec"])
        d["share"] = f"{round(100 * d['talk_time_sec'] / total_time)}%"
    return stats


def conversation_stats(transcript: Transcript, spk_stats: dict) -> dict:
    segs = transcript.segments
    span = (segs[-1].end - segs[0].start) if segs else 0.0
    n_turns = sum(s["turns"] for s in spk_stats.values()) if spk_stats else 0
    return {
        "duration": _fmt_hms(span),
        "n_speakers": len(spk_stats),
        "n_turns": n_turns,
    }


def _transcript_for_llm(transcript: Transcript, diarized: bool) -> str:
    if diarized:
        return "\n".join(
            f"{s.speaker or 'Locuteur ?'}: {s.text}" for s in transcript.segments
        )
    return transcript.full_text or " ".join(s.text for s in transcript.segments)


# Directive de langue ÉCRITE DANS LA LANGUE CIBLE : c'est ce qui ancre réellement
# la sortie du modèle (une consigne en français le fait répondre en français,
# même si elle dit « réponds en anglais »).
_LANG_DIRECTIVE = {
    "en": "CRITICAL — write EVERY text value (summary, topics, roles, tone, key points) in ENGLISH, not French.",
    "fr": "IMPÉRATIF — rédige CHAQUE valeur textuelle (résumé, thèmes, rôles, ton, points clés) en FRANÇAIS.",
    "es": "IMPORTANTE — escribe CADA valor de texto (resumen, temas, roles, tono, puntos clave) en ESPAÑOL.",
    "de": "WICHTIG — schreibe JEDEN Textwert (Zusammenfassung, Themen, Rollen, Ton, Kernpunkte) auf DEUTSCH.",
    "it": "IMPORTANTE — scrivi OGNI valore testuale (riassunto, temi, ruoli, tono, punti chiave) in ITALIANO.",
    "pt": "IMPORTANTE — escreva CADA valor de texto (resumo, temas, papéis, tom, pontos-chave) em PORTUGUÊS.",
    "nl": "BELANGRIJK — schrijf ELKE tekstwaarde (samenvatting, onderwerpen, rollen, toon, kernpunten) in het NEDERLANDS.",
}

_SYSTEM = (
    "Tu es un analyste de conversations / You are a conversation analyst. "
    "Réponds UNIQUEMENT en JSON valide / Reply ONLY with valid JSON, no prose. "
    "Write every text value in the language requested by the user instruction "
    "below (it matches the transcript's language). Be factual and concise; do "
    "not invent anything unsupported by the transcript. "
    # Règle anti-confusion « cité » vs « qui parle » : sur des contenus riches en
    # célébrités, le modèle attribuait aux locuteurs les noms simplement mentionnés
    # (une interview d'Hugo Lambert a produit « (Simon Royer) »).
    "RÈGLE DES NOMS / NAME RULE : n'ajoute un nom propre au rôle QUE si le "
    "dialogue le prouve — le locuteur se présente (« moi c'est X ») ou est "
    "interpellé (« alors X, … »). Un nom seulement CITÉ — quelqu'un dont on PARLE "
    "— ne désigne PAS le locuteur : interdit de deviner d'après le sujet, le titre "
    "ou les célébrités évoquées. Un même nom ne peut pas coiffer deux locuteurs, "
    "et pas de formule hésitante (« X ou autre ») : soit le nom est prouvé, soit "
    "tu donnes le rôle générique seul (« invité », « animateur »). / "
    "Add a proper name ONLY if the dialogue proves it (self-introduction or being "
    "directly addressed). A merely MENTIONED name does NOT identify the speaker — "
    "never guess from the topic, title or celebrities discussed. The same name "
    "must not cover two speakers, and no hedging (« X or someone ») : either the "
    "name is proven, or give the generic role alone."
)


def _consigne_francaise(language: Optional[str]) -> bool:
    """La consigne est écrite en français pour un transcript français ou de langue inconnue.

    Pour toute autre langue, elle est écrite en anglais. Une consigne française
    précédée d'une seule ligne « write in ENGLISH » donnait un profil entièrement
    français à des réunions anglaises longues : le modèle local suit la langue de la
    consigne qu'il lit juste avant le transcript, pas une directive lue des milliers
    de jetons plus tôt (même défaut que pour les faits, `core/ecl.py`, `_RAPPEL_LANGUE`).
    """
    return not language or language.lower().startswith("fr")


def _lang_directive(language: Optional[str]) -> str:
    """Directive de langue de sortie, écrite dans la langue cible (ancrage fort)."""
    if language:
        d = _LANG_DIRECTIVE.get(language.lower())
        if d:
            return d + "\n\n"
        return f"CRITICAL — write every text value in the transcript's language ('{language}').\n\n"
    return "CRITICAL — write every text value in the SAME language as the transcript below.\n\n"


def _instruction_anglaise(body: str, diarized: bool, speaker_labels: list[str]) -> str:
    """La consigne de `analyze` écrite en anglais, pour un transcript qui n'est pas français.

    Mêmes demandes, même schéma et même règle des noms que la consigne française.
    """
    if diarized and speaker_labels:
        labels = ", ".join(speaker_labels)
        schema_hint = (
            '{\n'
            '  "conversation": {"type": "...", "summary": "...", "topics": ["...", "..."]},\n'
            '  "speakers": {\n'
            f'    // one entry per speaker, keys EXACTLY among: {labels}\n'
            '    "Speaker 1": {"role": "...", "tone": "...", "key_points": ["...", "..."]}\n'
            '  }\n'
            '}'
        )
        return (
            f"Analyse this conversation between {len(speaker_labels)} speakers "
            f"({labels}). For the conversation: give its type (interview, debate, "
            "monologue, meeting…), a 2-3 sentence summary, and 3-5 topics. "
            "For EACH speaker: their likely role (e.g. interviewer, expert, host…), "
            "their tone, and 2-4 key points they argue or raise. "
            "Reminder: put a NAME in the role only if the speaker introduces themselves "
            "or is addressed by that name; a merely mentioned name does not identify "
            "them (otherwise write the role alone, without a name). "
            f"Use exactly these speaker keys: {labels}.\n\n"
            f"Expected JSON structure:\n{schema_hint}\n\n"
            f"Transcript:\n{body}"
        )
    schema_hint = '{"conversation": {"type": "...", "summary": "...", "topics": ["...", "..."]}}'
    return (
        "Analyse this recording. Give its type (monologue, interview, lecture, "
        "debate…), a 3-4 sentence summary, and 3-6 main topics.\n\n"
        f"Expected JSON structure:\n{schema_hint}\n\n"
        f"Transcript:\n{body}"
    )


def analyze(
    transcript: Transcript,
    *,
    diarized: bool,
    speaker_labels: list[str],
    provider: str = llm.FOURNISSEUR_DEFAUT,
    mistral_api_key: Optional[str] = None,
    progress: Optional[Progress] = None,
    seed: Optional[int] = config.PROFILE_SEED,
    envoi: sensitivity.Envois = None,
) -> dict:
    """Analyse qualitative via LLM. Retourne {conversation, speakers}.

    `envoi` : le document transcrit (sensibilité, propriétaire), contrôlé par la
    façade avant l'appel ; le transcript n'a pas encore de front-matter à relire.

    `num_ctx` a disparu de la signature : aucun appelant ne le renseignait, il
    valait donc TOUJOURS `None` et le moteur local l'estimait lui-même. Un
    paramètre qui ne transporte jamais rien est une promesse d'accordage que
    personne ne tient — et c'est aussi un réglage propre à Ollama, qui n'a pas
    sa place dans un contrat commun à tous les moteurs.
    """
    if progress:
        progress(0.1, messages.analyse_llm())

    body = _transcript_for_llm(transcript, diarized)

    if not _consigne_francaise(transcript.language):
        instruction = _instruction_anglaise(body, diarized, speaker_labels)
    elif diarized and speaker_labels:
        labels = ", ".join(speaker_labels)
        schema_hint = (
            '{\n'
            '  "conversation": {"type": "...", "summary": "...", "topics": ["...", "..."]},\n'
            '  "speakers": {\n'
            f'    // une entrée par locuteur, clés EXACTES parmi : {labels}\n'
            '    "Speaker 1": {"role": "...", "tone": "...", "key_points": ["...", "..."]}\n'
            '  }\n'
            '}'
        )
        instruction = (
            f"Analyse cette conversation entre {len(speaker_labels)} locuteurs "
            f"({labels}). Pour la conversation : donne son type (interview, débat, "
            "monologue, réunion…), un résumé de 2-3 phrases, et 3-5 thèmes. "
            "Pour CHAQUE locuteur : son rôle probable (ex. intervieweur, expert, "
            "animateur…), son ton, et 2-4 points clés qu'il défend/aborde. "
            "Rappel : ne mets un NOM dans le rôle que si le locuteur se présente "
            "ou est interpellé par ce nom ; un nom simplement cité ne l'identifie "
            "pas (sinon écris juste le rôle, sans nom). "
            f"Utilise exactement ces clés de locuteurs : {labels}.\n\n"
            f"Structure JSON attendue :\n{schema_hint}\n\n"
            f"Transcription :\n{body}"
        )
    else:
        schema_hint = (
            '{"conversation": {"type": "...", "summary": "...", "topics": ["...", "..."]}}'
        )
        instruction = (
            "Analyse cet enregistrement. Donne son type (monologue, interview, "
            "cours, débat…), un résumé de 3-4 phrases, et 3-6 thèmes principaux.\n\n"
            f"Structure JSON attendue :\n{schema_hint}\n\n"
            f"Transcription :\n{body}"
        )

    # Directive de langue de sortie (valeurs dans la langue de la transcription), en tête ;
    # hors français, répétée APRÈS le transcript, dans la langue cible : c'est la dernière
    # chose que le modèle lit avant d'écrire.
    instruction = _lang_directive(transcript.language) + instruction
    if not _consigne_francaise(transcript.language):
        instruction = instruction.rstrip() + "\n\n" + _lang_directive(transcript.language).strip()

    # La graine porte la reproductibilité de la CHAÎNE, pas d'une étape.
    #
    # Cet appel était le SEUL du tronc à partir sans elle, et c'était le plus
    # coûteux à laisser libre : son résultat est écrit dans le `.md`, et
    # `report.classify_genre` le relit pour choisir le gabarit — donc le nombre
    # de sections, donc tout le compte-rendu. Un profil qui varie fait varier
    # un document entier, deux étapes plus loin, sans qu'aucune trace ne relie
    # les deux.
    #
    # Rejouer des transcripts déjà profilés part d'un profil figé sur disque : cela
    # vérifie « transcript profilé → compte-rendu » là où l'utilisateur vit
    # « audio → compte-rendu ». Voir `config.PROFILE_SEED`.
    result = llm.chat_json(
        _SYSTEM, instruction, provider=provider, api_key=llm.cle_mistral(provider, mistral_api_key),
        seed=seed, envoi=envoi,
    )

    if progress:
        progress(1.0, messages.analyse_terminee())
    return result


def profile(
    transcript: Transcript,
    *,
    diarized: bool,
    provider: str = llm.FOURNISSEUR_DEFAUT,
    mistral_api_key: Optional[str] = None,
    progress: Optional[Progress] = None,
    seed: Optional[int] = config.PROFILE_SEED,
    envoi: sensitivity.Envois = None,
) -> dict:
    """Profil complet : stats (calcul) + analyse (LLM), fusionnés.

    `seed` traverse jusqu'à l'appel LLM : c'est la première étape du pipeline,
    et laisser cette seule étape libre suffisait à rendre tout le reste
    irrejouable. Voir `config.PROFILE_SEED`.
    """
    if progress:
        progress(0.02, messages.prechauffage_llm())
    if not llm.is_available(provider=provider, api_key=llm.cle_mistral(provider, mistral_api_key)):
        raise llm.LLMError(f"Le service LLM ({provider}) n'est pas disponible.")

    stats = speaker_stats(transcript)
    conv_stats = conversation_stats(transcript, stats)
    labels = list(stats.keys())

    analysis = analyze(
        transcript,
        diarized=diarized,
        speaker_labels=labels,
        provider=provider,
        mistral_api_key=mistral_api_key,
        progress=progress,
        seed=seed,
        envoi=envoi,
    )

    # Fusion stats <-> analyse par locuteur (clés identiques : "Speaker N").
    llm_speakers = analysis.get("speakers") if isinstance(analysis, dict) else None
    if not isinstance(llm_speakers, dict):
        llm_speakers = {}

    # Garde-fous AVANT toute écriture : un nom seulement cité (« Lina ») ne
    # doit pas atterrir dans le .md que l'utilisateur lit. Le filtre du registre
    # de voix n'agissait qu'à l'étape suggestion, en aval — trop tard.
    per_speaker = {
        label: a if isinstance(a := llm_speakers.get(label), dict) else {}
        for label in labels
    }
    clean_roles = names.sanitize_roles(
        {label: a.get("role") for label, a in per_speaker.items()}
    )

    speakers: dict[str, dict] = {}
    for label in labels:
        merged = dict(stats.get(label, {}))
        merged.pop("talk_time_sec", None)
        a = per_speaker[label]
        merged["role"] = clean_roles.get(label)
        merged["tone"] = a.get("tone")
        merged["key_points"] = a.get("key_points", [])
        speakers[label] = merged

    return {
        "conversation": analysis.get("conversation", {}) if isinstance(analysis, dict) else {},
        "conversation_stats": conv_stats,
        "speakers": speakers,
    }
