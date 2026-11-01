"""Garde de contenance / provenance — DÉTERMINISTE (étape 7).

Rejette toute phrase rendue qui introduit un nombre ou une entité nommée absent
de ses unités citées. Appartenance de jetons EXACTE — aucune validation floue,
aucun dédoublonnage sémantique.
"""
from __future__ import annotations

import re

NUM = re.compile(r"\d+")
# Entité candidate : mot capitalisé de ≥3 lettres (noms propres, sigles).
CAP = re.compile(r"\b([A-ZÀ-Ý][\wÀ-ÿ]{2,})\b")
# Mots capitalisés courants en début de phrase — ne les traite pas comme entités.
_STOP = {
    "Le", "La", "Les", "Un", "Une", "Des", "Ce", "Cette", "Ces", "Il", "Elle",
    "Ils", "Elles", "On", "Nous", "Vous", "Leur", "Son", "Sa", "Ses", "Notre",
    "Aujourd", "Depuis", "Après", "Avant", "Avec", "Sans", "Pour", "Dans",
    "Sur", "Sous", "Entre", "Malgré", "The", "This", "That", "These", "With",
}


# Sigle écrit lettre par lettre, comme le transcript AMI : des lettres isolées séparées
# par des tirets bas, tiret final facultatif (« L_C_D_ », « L_C_D »). Lu comme le sigle
# d'un seul mot (« LCD ») des deux côtés de la comparaison ; rien d'autre n'est
# rapproché (« V_CR_ » n'est pas une suite de lettres isolées). Sans cela, une phrase
# rendue « LCD » depuis une unité « L_C_D_ » était refusée.
SIGLE_A_TIRETS = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z](?:_[A-Za-z])+_?(?![A-Za-z0-9_])")


def sigles_d_un_mot(text: str) -> str:
    """« L_C_D_ » → « LCD » ; le reste du texte est inchangé."""
    return SIGLE_A_TIRETS.sub(lambda m: m.group(0).replace("_", ""), text)


def _numbers(text: str) -> set[str]:
    return set(NUM.findall(text))


def _entities(text: str) -> set[str]:
    """Entités nommées = mots capitalisés, MAIS jamais le mot initial d'une phrase
    (la capitale de début de phrase n'est pas une preuve d'entité). On découpe sur
    les frontières de phrase et on ignore le premier mot de chaque segment."""
    ents: set[str] = set()
    for seg in re.split(r"[.!?:]\s+|\n", text):
        toks = seg.split()
        for tok in toks[1:]:  # saute le mot initial du segment
            for m in CAP.findall(tok):
                if m not in _STOP:
                    ents.add(m)
    return ents


def check_sentence(text: str, support_texts: str) -> dict:
    """Renvoie {ok, missing_numbers, missing_entities}. `support_texts` = concat des
    textes des unités citées. Les sigles à tirets bas sont lus d'un mot des deux côtés."""
    text, support_texts = sigles_d_un_mot(text), sigles_d_un_mot(support_texts)
    sup_nums = _numbers(support_texts)
    sup_low = support_texts.lower()
    miss_num = sorted(n for n in _numbers(text) if n not in sup_nums)
    miss_ent = sorted(e for e in _entities(text) if e.lower() not in sup_low)
    return {"ok": not miss_num and not miss_ent,
            "missing_numbers": miss_num, "missing_entities": miss_ent}
