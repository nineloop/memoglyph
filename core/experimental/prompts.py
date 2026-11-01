"""Prompts + schémas JSON des étapes LLM (1, 3, 5, 6).

Aucun exemple ne montre `"g#"` ni `"F|S"` comme valeur littérale : le modèle
recopiait ces placeholders.
"""
from __future__ import annotations

# Les trois derniers portent le statut que le CR range en rubriques (Décisions,
# Actions, Questions ouvertes) : sans eux, une décision n'était qu'un `fact` ou un
# `event` et son statut ne survivait que dans le libellé.
RUBRIC_KINDS = ("decision", "action", "open_question")
ALLOWED_KINDS = ("fact", "event", "argument", "nuance", "example", "formulation", *RUBRIC_KINDS)

# ---------- Étape 1 : extraction atomique ----------
EXTRACT_SYSTEM = (
    "You extract ATOMIC units from ONE transcript chunk. Output ONLY a JSON object "
    '{"units": [ ... ]}, no prose, no fences. Each unit has keys: id, kind, text, start, end, '
    "speaker. kind is one of: fact, event, argument, nuance, example, formulation, "
    "decision, action, open_question. Use decision ONLY for what the speakers settle or agree "
    "on, and keep in 'text' the status as it is said (agreed, proposed, rejected, postponed); "
    "action for a task someone will do; open_question for a point the speakers explicitly "
    "leave to be decided. An action unit also has the key 'owner': who will do it, written "
    "exactly as the chunk says it, or null when nobody is named; no other unit has 'owner'. "
    "RULES: (a) 'start' MUST be a timestamp copied EXACTLY from THIS chunk (format HH:MM:SS); "
    "never invent one. (b) 'end' may be null. (c) 'text' is one concise clause, written in the "
    "SAME language as the chunk — NEVER translate. (d) Extract only what is said IN THIS CHUNK; "
    "no thesis, no summary, no chronology, no cross-chunk inference. (e) exactly one 'start' per "
    "unit, never a list. "
    "SALIENCE: emit a unit ONLY if it carries autonomous information whose omission would "
    "materially reduce a report's quality; do NOT create separate units for reformulations, "
    "restatements, backchannel or filler; prefer fewer, denser units."
)


def extract_user(chunk: str) -> str:
    return f"CHUNK:\n{chunk}\n\nReturn ONLY the JSON object."


# ---------- Étape 3 : classification fermée ----------
CLASSIFY_SYSTEM = (
    "You classify EACH transcript unit into EXACTLY ONE bucket from the ALLOWED list. "
    'Output ONLY a JSON object {"assignments": [ ... ]}, each item having keys \'id\' and '
    "'bucket', one per input unit, reusing the SAME ids. No prose, no explanation, no new labels. "
    "bucket MUST be one of the allowed bucket ids. Pick the best-fitting theme; use off_topic if "
    "unrelated to the transcript's main subject, dialogic_noise for interjections/questions/filler "
    "with no autonomous information, other only if on-topic but fitting no theme."
)


def classify_user(bucket_block: str, units_block: str) -> str:
    return (
        f"ALLOWED BUCKETS:\n{bucket_block}\n\nUNITS:\n{units_block}\n\n"
        "Return ONLY the JSON array."
    )


# ---------- Étape 5 : structure bornée ----------
STRUCTURE_SYSTEM = (
    "You receive THEMES from one transcript, each a label plus up to 3 representative units "
    "given as 'id | text'. Output ONLY one JSON object with keys: source_shape (short string), "
    "thesis (object with 'text' and 'support'), tensions (array of objects with 'text' and "
    "'support'), sections (array of objects with 'label' and 'support'). "
    "RULES: 'support' is an array of unit ids taken ONLY from the input; never invent an id. "
    "Every thesis/tension/section carries at least one support id. Order sections by the source's "
    "own logic. If there is genuinely no tension, use an empty array. Do NOT copy unit text, do "
    "NOT write any timestamp, do NOT write paragraphs."
)


def structure_user(themes_block: str) -> str:
    return f"THEMES:\n{themes_block}\n\nReturn ONLY the JSON object."


# ---------- Étape 6 : rendu fidèle une-phrase-par-unité ----------
RENDER_SYSTEM = (
    "You rewrite EACH supplied transcript unit as ONE concise, clean report sentence. "
    "Output ONLY a JSON object with a single key 'sentences', an array of objects each with keys "
    "'text' and 'support'. There is exactly one object per input unit, in the SAME order, and "
    "'support' is an array containing ONLY that unit's id. "
    "HARD RULES: keep the unit's meaning, actors, numbers and dates EXACTLY as given; rewrite only "
    "for fluency, in the SAME language as the unit — NEVER translate. Add nothing, infer nothing, "
    "merge nothing, split nothing. No new fact, name, date, cause or comparison. No markdown, no "
    "commentary. JSON only."
)


def render_user(units_block: str) -> str:
    return f"UNITS:\n{units_block}\n\nReturn ONLY the JSON object."


# ---------- Schémas (indicatifs ; chat_json les passe au moteur qui sait) ----------
EXTRACT_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["id", "kind", "text", "start"],
        "properties": {
            "id": {"type": "string"},
            "kind": {"type": "string", "enum": list(ALLOWED_KINDS)},
            "text": {"type": "string"},
            "start": {"type": "string"},
            "end": {"type": ["string", "null"]},
            "speaker": {"type": ["string", "null"]},
        },
        # Porteur d'une action, tel qu'il est dit ; la validation le retire sinon. Clé
        # des seules actions : sur les autres unités, ce sont des jetons de sortie en
        # plus sous une borne que les tranches denses atteignaient.
        "allOf": [{
            "if": {"properties": {"kind": {"const": "action"}}},
            "then": {"properties": {"owner": {"type": ["string", "null"]}}},
            "else": {"not": {"required": ["owner"]}},
        }],
    },
}
