"""Contrat et contrôle déterministe des citations d'un compte-rendu.

Le contrôle prouve seulement qu'un extrait est présent, à l'horodatage annoncé,
dans le transcript source. Il ne décide ni si l'extrait soutient l'affirmation,
ni si une inférence est vraie. Les anomalies sont rendues, jamais corrigées.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass

from .library import _split_frontmatter


CONTRACT_VERSION = 7

# Le plafond total, appliqué APRÈS COUP par la sélection — et nulle part ailleurs.
#
# RETIRÉ du texte du contrat. Ce qu'il
# demandait au modèle, `_selectionner` l'impose de toute façon : le retirer ne
# change RIEN au document rendu, seulement ce que le modèle doit tenir pendant
# qu'il écrit.
#
# C'était la dernière règle à état GLOBAL du contrat. Elle exigeait un compteur
# maintenu sur toute la sortie, là où toutes les autres se vérifient dans une
# fenêtre courte — une section, une affirmation, une preuve.
#
# Et elle n'était pas tenue : un transcript dense produisait plus du double des
# affirmations autorisées, deux fois trop dans une seule section — et c'est à la
# sortie de cette section que son JSON cassait.
#
# Aucun schéma ne pourra jamais l'imposer non plus : JSON Schema n'a pas
# d'arithmétique entre propriétés sœurs.
#
# Le conseil ÉDITORIAL qui l'accompagnait est conservé — « sélectionne les plus
# pertinentes, ne découpe pas artificiellement une même idée ». Il ne demande
# aucun comptage, et le retirer aurait changé deux règles à la fois.
MAX_REPORT_CLAIMS = 10

# Un GARDE-FOU pour le modèle local, pas un plafond éditorial. La distinction
# est tout le sujet, et elle a coûté deux jours à être comprise.
#
# Le plafond par section a été retiré du texte ET du schéma parce
# qu'il nivelait la matière. Le retirer du SCHÉMA était juste — c'est la seule
# contrainte que Mistral honore au décodage. Le retirer du TEXTE a cassé le
# chemin local, qui ignore le schéma et n'a que le texte.
#
# Sur le même transcript, seule cette phrase changeant : sans la clause, aucun
# tirage n'aboutit et la génération court jusqu'au plafond de jetons ; avec
# « au maximum 5 », chaque tirage rend la MÊME répartition et tient le contrat
# du premier coup, en deux fois moins de temps : la boucle de reprise ne sert
# jamais.
#
# Le contrat portait pourtant déjà « au maximum 10 au total », et le modèle le
# franchissait. L'explication est mécanique : une borne PAR SECTION est
# vérifiable là où le modèle écrit, une borne TOTALE exige une comptabilité sur
# toute la sortie. Un modèle de 4 milliards tient la première, pas la seconde.
#
# 5 et non 2 : sans clause, le maximum observé par section est de 5 chez
# Mistral. La borne donne au local un nombre à tenir sans resserrer le cloud,
# et la sélection écarte de toute façon l'excédent ensuite.
BORNE_TEXTE_PAR_SECTION = 5

# N'est PLUS un plafond par section : c'est la dotation qui sert à calculer le
# budget total quand un genre porte plus de cinq sections. Le plafond par
# section a été retiré du contrat, du schéma et du rendu — il nivelait
# la matière. MESURÉ sans aucune bride : la répartition réelle est très inégale,
# [4, 8, 6, 1, 3, 1] chez Mistral et [2, 6, 1, 1, 3, 4] en local. C'est le
# transcript qui décide de la densité d'une section, pas une constante.
DOTATION_PAR_SECTION = 2
# Les codes de refus, STABLES, et c'est leur raison d'être.
#
# La reprise dirigée renvoie au modèle le motif du rejet. Y coller le texte
# d'une exception ferait de ce texte une QUATRIÈME formulation du contrat —
# après le prompt, le schéma et le validateur — et elle dériverait sans que
# personne ne s'en aperçoive.
#
# Un code stable, une correction concise, et rien du contenu généré : une
# exception peut porter un fragment de la sortie du modèle, qu'on ne veut pas
# lui réinjecter.
TROP_DE_PREUVES = "TOO_MANY_EVIDENCE"
PREUVE_PRINCIPALE_ABSENTE = "MISSING_PRIMARY_EVIDENCE"
SECTION_INCONNUE = "UNKNOWN_SECTION"
SECTION_HORS_CONTRAT = "SECTION_OFF_CONTRACT"
AFFIRMATION_HORS_CONTRAT = "CLAIM_OFF_CONTRACT"
# `secondary_evidence` absente — l'omission la plus MÉCANIQUE du contrat, et
# elle méritait son propre code.
#
# MESURÉ sur le parcours réel : sur un média quelconque, le
# compte-rendu échouait en 503 après TROIS tentatives, toujours sur
# `clés manquantes dans claim : ['secondary_evidence']`. Le code émis était
# `CLAIM_OFF_CONTRACT`, qui n'a pas de correction ciblée : le modèle recevait
# donc trois fois « relis le contrat de sortie », et échouait trois fois.
#
# Le champ RESTE obligatoire. Ce qui manquait n'était pas une souplesse du
# contrat, c'était une consigne qui dise ce qu'il faut écrire quand on n'a pas
# de preuve secondaire — `null`, et non rien.
PREUVE_SECONDAIRE_ABSENTE = "MISSING_SECONDARY_EVIDENCE_KEY"
SANS_REPONSE_MAIS_AFFIRME = "NOT_FOUND_WITH_CLAIMS"
RACINE_HORS_CONTRAT = "ROOT_OFF_CONTRACT"
EVIDENCE_MARKER = "<!-- memoglyph:evidence -->"
GENERATED_NOTICE_MARKER = "<!-- memoglyph:generated-notices -->"

_SEGMENT = re.compile(
    r"(?ms)^\s*(?:\*\*(?P<speaker>[^*\n]+)\*\*\s*)?"
    r"\[(?P<timestamp>\d{1,2}:\d{2}(?::\d{2})?)\]\s*"
    r"(?P<text>.*?)"
    r"(?=^\s*(?:\*\*[^*\n]+\*\*\s*)?\[\d{1,2}:\d{2}(?::\d{2})?\]|\Z)"
)
_SOURCE = re.compile(
    r"^>\s*\[source\s+(?P<target>none|\d{1,2}:\d{2}(?::\d{2})?)\]"
    r"(?:\s+(?P<quote>.*?))?\s*$",
    re.IGNORECASE,
)
_HEADING = re.compile(r"^#{1,6}\s+")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+(.+)$")
_RULE = re.compile(r"^\s*(?:---+|\*\*\*+|___+)\s*$")


def prompt_contract(language: str | None) -> str:
    """Contrat JSON borné ; Memoglyph, pas le modèle, rend le Markdown."""
    if (language or "fr").lower() == "fr":
        return (
            "CONTRAT DE SORTIE — Réponds uniquement avec un objet JSON "
            '`{"sections":[...]}`. Chaque section contient exactement '
            '`{"question_id": entier, "title": texte, '
            '"answer_status": "answered" ou "not_found", "claims":[...]}`. '
            "Chaque affirmation est atomique et contient exactement "
            '`{"text": texte, "primary_evidence": {...}, '
            '"secondary_evidence": {...} ou null}`. '
            "Sélectionne uniquement les affirmations les plus pertinentes "
            "et ne découpe pas artificiellement une même idée. "
            f"Chaque section contient au maximum {BORNE_TEXTE_PAR_SECTION} "
            "affirmations. "
            "Une affirmation porte UNE preuve principale, obligatoire, et AU "
            "PLUS une preuve secondaire, facultative. La secondaire doit "
            "apporter un élément DISTINCT, jamais reformuler la première ; "
            "écris `null` si tu n'en as pas. Aucune autre preuve n'est possible. "
            "Chaque preuve contient exactement "
            '`{"timestamp":"HH:MM:SS","quote":"extrait exact"}`. '
            "Recopie mot pour mot un extrait d'UN SEUL tour portant exactement "
            "cet horodatage complet. Dans `quote`, recopie uniquement les mots "
            "situés après l'horodatage, jamais le locuteur ni `[HH:MM:SS]`. "
            "Ne fusionne jamais plusieurs tours et n'utilise jamais de points "
            "de suspension. "
            "Si la transcription ne répond pas à une question, écris "
            '`"answer_status": "not_found"` et `"claims": []` pour cette '
            "section — n'invente jamais une affirmation pour combler. "
            "N'ajoute aucune clé, prose ou Markdown hors du JSON."
        )
    return (
        "OUTPUT CONTRACT — Reply only with one JSON object "
        '`{"sections":[...]}`. Each section contains exactly '
        '`{"question_id": integer, "title": text, '
        '"answer_status": "answered" or "not_found", "claims":[...]}`. '
        "Each atomic claim contains exactly "
        '`{"text": text, "primary_evidence": {...}, '
        '"secondary_evidence": {...} or null}`. '
        "Select only the most relevant claims and do not artificially "
        "split one idea. "
        f"Each section contains at most {BORNE_TEXTE_PAR_SECTION} claims. "
        "A claim carries ONE primary evidence, mandatory, and AT MOST one "
        "secondary evidence, optional. The secondary must add a DISTINCT "
        "element, never restate the first; write `null` if you have none. No "
        "other evidence is possible. "
        "Each evidence item contains exactly "
        '`{"timestamp":"HH:MM:SS","quote":"exact excerpt"}`. '
        "Copy the quote verbatim from ONE turn carrying exactly that full "
        "timestamp. In `quote`, copy only the words after the timestamp, never "
        "the speaker label or `[HH:MM:SS]`. Never splice turns and never use "
        "ellipses. "
        "If the transcript does not answer a question, write "
        '`"answer_status": "not_found"` and `"claims": []` for that section — '
        "never invent a claim to fill the gap. "
        "Add no keys, prose, or Markdown outside the JSON."
    )


def output_schema(expected_sections: int) -> dict:
    """Le schéma, où la cardinalité des preuves est INCARNÉE, plus demandée.

    ## Ce que ce schéma corrige

    `evidence` était un tableau ouvert, borné par un `maxItems: 2` que seul
    Mistral honore. MESURÉ en capturant une sortie tronquée : le modèle
    local y avait mis 172 preuves pour 11 affirmations — `[2, 3, 26, 19, 7, 30,
    3, 2, 19, 17, 44]` — en recopiant le transcript tour par tour, horodatage
    croissant. Il ne bouclait pas : il transcrivait.

    Un tableau ouvert est précisément ce qu'un modèle remplit sans fin. Deux
    champs nommés ne le peuvent pas : la borne n'est plus une consigne polie,
    elle est la forme même de la sortie.

    ## `additionalProperties: false` et champs tous requis

    Forme STRICTE, celle qu'attendent les sorties structurées natives : toute
    propriété déclarée est requise, et l'optionnalité passe par `null` explicite
    plutôt que par l'absence. `secondary_evidence` vaut donc `null`, jamais
    « pas là » — un champ absent et un champ nul se distinguent mal dans une
    sortie générée, et le modèle oublie plus facilement une clé qu'une valeur.
    """
    evidence = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "timestamp": {"type": "string"},
            "quote": {"type": "string"},
        },
        "required": ["timestamp", "quote"],
    }
    claim = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "text": {"type": "string"},
            "primary_evidence": evidence,
            # `null` EXPLICITE plutôt qu'absence : voir la docstring.
            "secondary_evidence": {"anyOf": [evidence, {"type": "null"}]},
        },
        "required": ["text", "primary_evidence", "secondary_evidence"],
    }
    section = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "question_id": {
                "type": "integer",
                "minimum": 1,
                "maximum": expected_sections,
            },
            "title": {"type": "string"},
            # Le « sans réponse » vit au niveau SECTION, pas dans une
            # affirmation artificielle. Une question sans réponse est une
            # propriété de la question, pas une affirmation à propos du monde.
            "answer_status": {"type": "string",
                              "enum": ["answered", "not_found"]},
            # Pas de `maxItems` : c'est la seule contrainte que Mistral honore
            # réellement au décodage, et la borner par section ramènerait le
            # nivellement qu'on a retiré. Pas de `minItems` non plus — une
            # section `not_found` est LÉGITIMEMENT vide.
            "claims": {"type": "array", "items": claim},
        },
        "required": ["question_id", "title", "answer_status", "claims"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "sections": {
                "type": "array",
                "minItems": expected_sections,
                "maxItems": expected_sections,
                "items": section,
            }
        },
        "required": ["sections"],
    }


def normalize_claim(claim: dict) -> dict:
    """Une affirmation à l'ANCIEN format, relue au nouveau. Lecture seule.

    La compatibilité appartient au LECTEUR, pas au prompt : on ne demande jamais
    au modèle de produire deux formats.

    Sert aux artefacts déjà écrits, qui portent des charges utiles
    `evidence: [...]`. Au-delà de deux preuves, seules les deux premières
    survivent — le reste est précisément ce que le nouveau contrat rend
    impossible à produire.
    """
    if "evidence" not in claim:
        return claim
    preuves = claim.get("evidence") or []
    reste = {k: v for k, v in claim.items() if k not in ("evidence", "abstention")}
    return {
        **reste,
        "primary_evidence": preuves[0] if preuves else None,
        "secondary_evidence": preuves[1] if len(preuves) > 1 else None,
    }


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(
        char for char in decomposed if not unicodedata.combining(char)
    ).lower()


def normalized_quote(text: str) -> str:
    """Tolère seulement la casse, les accents et les espaces."""
    return " ".join(_fold(text).split())


def quote_exists(source: str, quote: str) -> bool:
    normalized = normalized_quote(quote)
    return bool(normalized) and normalized in normalized_quote(source)


@dataclass(frozen=True)
class CitationAudit:
    status: str
    claims: int
    claims_with_valid_citation: int
    abstentions: int
    citations: int
    valid_citations: int
    warnings: tuple[str, ...]

    def metadata(self) -> dict[str, int | str]:
        """Résumé compact et reconstructible écrit dans le Markdown."""
        return {
            "contract_version": CONTRACT_VERSION,
            "status": self.status,
            "claims": self.claims,
            "claims_with_valid_citation": self.claims_with_valid_citation,
            "abstentions": self.abstentions,
            "citations": self.citations,
            "valid_citations": self.valid_citations,
        }


@dataclass
class _Claim:
    text: str
    markers: int = 0
    valid_citations: int = 0
    abstentions: int = 0


class StructuredReportError(ValueError):
    """Le modèle a répondu en JSON, mais hors du contrat de CR.

    Porte un CODE stable en plus de son message. La reprise dirigée renvoie ce
    code au modèle, jamais le message : celui-ci peut contenir un fragment de la
    sortie générée, et en faire une consigne rendrait le prompt dépendant de ce
    que le modèle vient d'écrire.

    Le code par défaut couvre les refus de forme élémentaires — un champ qui
    n'est pas du texte, une racine mal formée — pour lesquels il n'existe pas de
    correction plus précise que « respecte le contrat ».
    """

    def __init__(self, message: str, *, code: str = RACINE_HORS_CONTRAT) -> None:
        super().__init__(message)
        self.code = code


def _plain(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise StructuredReportError(f"{field} doit être un texte")
    compact = " ".join(value.split()).strip()
    if not compact:
        raise StructuredReportError(f"{field} ne peut pas être vide")
    return compact


def _evidence_valid(
    segments: dict[str, list[str]], timestamp: str, quote: str
) -> bool:
    return timestamp in segments and any(
        quote_exists(text, quote) for text in segments[timestamp]
    )


@dataclass(frozen=True)
class _Ancrage:
    """Verdict d'ancrage d'une citation sur le transcript.

    - natif (``normalise=False``, ``valide=True``) : l'extrait verbatim est à
      l'horodatage annoncé. Rien n'a bougé, validation normale.
    - normalisé (``normalise=True``, ``valide=True``) : l'horodatage annoncé ne
      portait pas l'extrait, mais celui-ci existe AILLEURS de façon UNIQUE ; on
      le rattache à l'horodatage canonique réel du transcript. Récupéré, mais
      explicitement pas natif.
    - refus (``valide=False``) : zéro correspondance (paraphrase, tours fusionnés)
      ou plusieurs occurrences ambiguës. On ne réancre pas, l'échec est conservé.
    """

    timestamp: str          # canonique : celui du transcript quand on réancre
    valide: bool
    normalise: bool
    origine: str = ""       # horodatage du modèle, renseigné seulement si réancré


def _span_contains_from(
    ordered: list[tuple[str, str | None, str]], start: int, nq: str
) -> bool:
    """`nq` (déjà normalisé) est-il un extrait CONTIGU de segments consécutifs
    d'un MÊME locuteur à partir de `start`, la correspondance DÉBUTANT dans le
    segment de départ ?

    Recoudre des segments d'un même tour rattrape la segmentation LARGE de
    certains STT — Whisper découpe un monologue continu en tranches horodatées,
    et une citation verbatim d'une phrase enjambe alors deux tranches. On ne
    franchit JAMAIS un changement de locuteur : une citation à cheval sur deux
    voix reste une couture interdite (voir `test_refuse_tours_fusionnes`). Et le
    contrat verbatim tient : `nq` doit être un sous-texte contigu de la
    concaténation, jamais une paraphrase ni un saut de segment.
    """
    speaker = ordered[start][1]
    first_len = len(normalized_quote(ordered[start][2]))
    acc = ""
    for index in range(start, len(ordered)):
        if index > start and ordered[index][1] != speaker:
            break  # ne jamais recoudre par-dessus un changement de locuteur
        piece = ordered[index][2]
        acc = piece if not acc else f"{acc} {piece}"
        position = normalized_quote(acc).find(nq)
        if position != -1:
            # La correspondance doit COMMENCER dans le segment de départ ; sinon
            # c'est un segment suivant qui la porte, et il sera l'ancre à son tour.
            return first_len == 0 or position < first_len
        # Une fois l'accumulé plus long que `nq` d'au moins le segment de départ,
        # aucun début possible n'y reste : inutile d'étendre davantage.
        if len(normalized_quote(acc)) >= len(nq) + first_len:
            return False
    return False


def _span_anchor(
    ordered: list[tuple[str, str | None, str]], quote: str
) -> str | None:
    """Horodatage de DÉBUT d'un extrait verbatim couvrant des segments
    consécutifs d'un même locuteur ; ``None`` si aucun, ou si plusieurs débuts
    distincts (ambigu) — jamais de réancrage dans le doute."""
    nq = normalized_quote(quote)
    if not nq:
        return None
    debuts = [
        ordered[i][0] for i in range(len(ordered))
        if _span_contains_from(ordered, i, nq)
    ]
    uniques = list(dict.fromkeys(debuts))
    return uniques[0] if len(uniques) == 1 else None


def _quote_from_anchor(
    ordered: list[tuple[str, str | None, str]], target: str, quote: str
) -> bool:
    """L'extrait est-il verbatim à partir d'un segment portant `target` — un seul
    segment, ou plusieurs consécutifs d'un même locuteur ?

    C'est la revérification, côté AUDIT, du span que l'ancrage a retenu à la
    génération : le corps rend TOUJOURS l'horodatage de début, donc relire depuis
    cet horodatage doit retrouver l'extrait, sinon la citation reste une réserve.
    Le cas mono-segment historique en est le sous-cas (span de longueur 1).
    """
    nq = normalized_quote(quote)
    if not nq:
        return False
    return any(
        ts == target and _span_contains_from(ordered, i, nq)
        for i, (ts, _speaker, _text) in enumerate(ordered)
    )


def _ancrer_preuve(
    segments: dict[str, list[str]],
    timestamp: str,
    quote: str,
    *,
    ordered: list[tuple[str, str | None, str]] | None = None,
) -> _Ancrage:
    """Valide une citation, avec un filet DÉTERMINISTE sur le seul pointeur temporel.

    Le contrat de preuve n'est PAS relâché : jamais de fenêtre temporelle
    approximative, jamais de correction d'une paraphrase. Le filet ne déplace que
    l'HORODATAGE d'une citation dont l'extrait est déjà, mot pour mot, dans le
    transcript.

    1. horodatage fourni + extrait verbatim présent à cet horodatage : natif ;
    2. sinon, extrait verbatim dans UN seul autre segment, occurrence unique :
       rattaché à cet horodatage canonique, résultat NORMALISÉ ;
    3. plusieurs occurrences mono-segment : ancre indécidable, échec conservé ;
    4. sinon, si `ordered` est fourni, extrait verbatim recousu sur des segments
       CONSÉCUTIFS d'un MÊME locuteur (STT à segmentation large, ex. Whisper) :
       rattaché à l'horodatage de DÉBUT du span. Natif si ce début est déjà
       l'horodatage annoncé, sinon normalisé.

    « Verbatim » a le sens de :func:`quote_exists` — l'extrait mot pour mot, à la
    casse, aux accents et aux espaces près. Une paraphrase, un saut de segment ou
    une couture par-dessus un changement de locuteur ne sont retrouvés nulle
    part : jamais réancrés.
    """
    if _evidence_valid(segments, timestamp, quote):
        return _Ancrage(timestamp, valide=True, normalise=False)
    # Les horodatages sont les CLÉS de `segments`, donc déjà distincts : la
    # cardinalité de `hits` est exactement le nombre d'ancres candidates.
    hits = [
        ts for ts, textes in segments.items()
        if any(quote_exists(texte, quote) for texte in textes)
    ]
    if len(hits) == 1:
        return _Ancrage(hits[0], valide=True, normalise=True, origine=timestamp)
    if len(hits) >= 2:
        # Ambigu au niveau d'UN segment : on ne réancre pas, l'échec est conservé.
        return _Ancrage(timestamp, valide=False, normalise=False)
    # Dernier filet : aucun segment unique ne porte l'extrait. Un extrait
    # verbatim peut néanmoins enjamber des segments consécutifs d'un même
    # locuteur, ce que produit un STT à segmentation large. On l'ancre au début.
    if ordered is not None:
        debut = _span_anchor(ordered, quote)
        if debut is not None:
            normalise = debut != timestamp
            return _Ancrage(debut, valide=True, normalise=normalise,
                            origine=timestamp if normalise else "")
    return _Ancrage(timestamp, valide=False, normalise=False)


def _without_source_prefix(timestamp: str, quote: str) -> str:
    """Retire seulement un préfixe source recopié à l'identique par le modèle."""
    prefixed = re.match(
        r"^(?:\*\*[^*\n]+\*\*\s*)?"
        r"\[(?P<timestamp>\d{1,2}:\d{2}(?::\d{2})?)\]\s*(?P<text>.+)$",
        quote,
    )
    if prefixed and prefixed.group("timestamp") == timestamp:
        return prefixed.group("text").strip()
    return quote


def _selectionner(sections: list[dict], plafond: int) -> int:
    """Retient au plus `plafond` affirmations, les FONDÉES d'abord. Rend le nombre écarté.

    Modifie `sections` sur place et numérote ce qui reste.

    ## Pourquoi l'ordre d'arrivée était le mauvais critère

    On gardait « les deux premières » de chaque section. Or l'ordre n'a AUCUNE
    corrélation avec la qualité : la première affirmation d'une section peut être
    non fondée et la deuxième fondée, et une section pouvait garder deux
    affirmations non fondées alors qu'elle en contenait une qui l'était. La
    troncature écartait ainsi des affirmations vérifiables au profit d'autres
    qui ne l'étaient pas.

    ## Le critère, et pourquoi celui-là

    `evidence.valid` est le seul jugement que ce module s'autorise : la citation
    existe-t-elle mot pour mot dans le transcript, à l'horodatage annoncé. Pas
    de LLM, pas d'heuristique, déterministe. On ne demande pas au produit d'avoir
    du goût — seulement de préférer ce qu'il peut prouver.

    ## La garantie qui prime sur le critère

    **Au moins une affirmation par section RÉPONDUE.** Sélectionner sur la seule
    citation valide viderait une section dont aucune affirmation n'est
    vérifiable, et le contrat refuse un `answered` sans affirmation.

    Une section `not_found` est LÉGITIMEMENT vide et ne consomme aucun budget :
    « la transcription ne dit rien là-dessus » est une propriété de la question,
    plus une affirmation artificielle à fabriquer. C'est ce que `answer_status`
    a remplacé.

    ## Ce qui reste du plafond, et ce qu'on refuse d'en faire

    Le plafond reste : sans aucune bride, le local comme Mistral produisent une
    trentaine d'affirmations. Ce n'est plus un compte-rendu.

    Mais on ne le REMPLIT PAS avec du non vérifié : compléter le budget avec les
    affirmations non fondées ne sauve pas UNE affirmation prouvée de plus, et fait
    baisser la part vérifiable du document, chez Mistral comme en local.

    Une affirmation non fondée n'est pas fausse — sa citation n'a pas été
    retrouvée mot pour mot, souvent parce que le modèle a paraphrasé. Mais elle
    n'apporte au lecteur qu'une ligne à croire sur parole, et le compte-rendu se
    juge sur ce qu'il peut prouver.

    Conséquence assumée : le document est plus COURT quand le modèle a moins
    bien cité. Sa longueur cesse d'être constante et devient une information.
    """
    budget = plafond
    # On retient par IDENTITÉ, pas par valeur : un modèle peut produire deux
    # fois la même affirmation mot pour mot, et `in` sur des dictionnaires
    # égaux garderait les deux tout en n'en décomptant qu'une du budget.
    gardes: set[int] = set()

    # 1. le quota de survie : une par section RÉPONDUE, fondée si possible.
    #    Une section sans réponse n'a rien à sauver et ne coûte rien.
    for section in sections:
        claims = section["claims"]
        if not claims:
            continue
        tete = next((c for c in claims if c["fonde"]), claims[0])
        gardes.add(id(tete))
        budget -= 1

    # 2. le reste du budget, aux FONDÉES seulement — et section par section
    #    pour ne pas vider les dernières au profit des premières.
    #
    # Ce qu'on ne fait PAS, et c'est un choix : compléter avec les
    # affirmations non vérifiées quand les fondées ne remplissent pas le
    # budget. Remplir ne sauve pas UNE affirmation fondée de plus, et fait
    # baisser la part vérifiable du document, chez les deux fournisseurs. Sur
    # un transcript où le modèle cite mal, le lecteur recevait une majorité de
    # lignes qui ne renvoient à rien.
    #
    # Le compte-rendu devient donc plus COURT quand le modèle a moins bien
    # travaillé. C'est voulu : la longueur cesse d'être constante et devient
    # elle-même une information.
    for section in sections:
        for claim in section["claims"]:
            if budget <= 0:
                break
            if id(claim) in gardes or not claim["fonde"]:
                continue
            gardes.add(id(claim))
            budget -= 1

    ecartees = 0
    numero = 0
    for section in sections:
        # L'ordre d'origine est CONSERVÉ à l'affichage : la sélection choisit
        # quoi garder, elle ne réordonne pas le propos du modèle.
        retenus = [c for c in section["claims"] if id(c) in gardes]
        ecartees += len(section["claims"]) - len(retenus)
        for claim in retenus:
            numero += 1
            claim["id"] = f"claim-{numero:03d}"
            claim.pop("fonde", None)
        section["claims"] = retenus
    return ecartees


def valider_structure(
    payload: object,
    transcript_markdown: str,
    *,
    expected_sections: int,
) -> list[dict[str, object]]:
    """Le CONTRAT seul : accepte ou refuse, ne choisit rien, ne rend rien.

    Extraite de :func:`render_structured` pour que le filet de
    réparation syntaxique dispose d'un critère d'acceptation qui ne DÉCIDE
    rien. Règle : ne pas mélanger réparation et sélection.
    Un réparateur qui appellerait le rendu complet ferait dépendre son verdict
    de la sélection, c'est-à-dire d'un jugement éditorial sur les affirmations.
    Ici, il n'y a que des refus vérifiables :

    - exactement `expected_sections` sections, `question_id` 1..N, sans doublon
      ni trou ;
    - `answer_status` dans `answered`/`not_found`, et cohérent avec `claims` ;
    - une `primary_evidence`, au plus une `secondary_evidence` ;
    - aucune clé inconnue, à aucun niveau.

    Rend les sections analysées, TRIÉES par `question_id` — le modèle n'est pas
    tenu de les émettre dans l'ordre, et la sélection numérote ensuite.
    """
    if not isinstance(payload, dict) or set(payload) != {"sections"}:
        raise StructuredReportError("la racine doit contenir uniquement sections")
    sections = payload["sections"]
    if not isinstance(sections, list) or len(sections) != expected_sections:
        raise StructuredReportError(
            f"{expected_sections} sections attendues, {len(sections) if isinstance(sections, list) else 0} reçues"
        )

    parsed: list[dict[str, object]] = []
    seen_questions: set[int] = set()
    segments = _transcript_segments(transcript_markdown)
    ordered = _ordered_segments(transcript_markdown)

    for raw_section in sections:
        if not isinstance(raw_section, dict) or set(raw_section) != {
            "question_id", "title", "answer_status", "claims"
        }:
            raise StructuredReportError("section hors contrat",
                                        code=SECTION_HORS_CONTRAT)
        question_id = raw_section["question_id"]
        if (
            not isinstance(question_id, int)
            or isinstance(question_id, bool)
            or question_id < 1
            or question_id > expected_sections
            or question_id in seen_questions
        ):
            raise StructuredReportError(
                "question_id absent, dupliqué ou hors plage",
                code=SECTION_INCONNUE)
        seen_questions.add(question_id)
        title = _plain(raw_section["title"], field="title").lstrip("#").strip()
        statut = raw_section["answer_status"]
        if statut not in ("answered", "not_found"):
            raise StructuredReportError(
                "answer_status doit valoir answered ou not_found",
                code=SECTION_HORS_CONTRAT)
        claims = raw_section["claims"]
        if not isinstance(claims, list):
            raise StructuredReportError("claims doit être une liste",
                                        code=SECTION_HORS_CONTRAT)
        # Une section SANS RÉPONSE est légitimement vide — c'est une propriété
        # de la question, pas une affirmation à propos du monde. Ce qu'on
        # refuse, ce sont les deux incohérences symétriques.
        if statut == "not_found" and claims:
            raise StructuredReportError(
                "une section sans réponse ne porte aucune affirmation",
                code=SANS_REPONSE_MAIS_AFFIRME)
        if statut == "answered" and not claims:
            raise StructuredReportError(
                "une section répondue doit porter au moins une affirmation",
                code=SECTION_HORS_CONTRAT)

        def _preuve(brut: object, champ: str) -> dict[str, object]:
            if not isinstance(brut, dict) or set(brut) != {"timestamp", "quote"}:
                raise StructuredReportError(f"{champ} hors contrat",
                                            code=AFFIRMATION_HORS_CONTRAT)
            timestamp = _plain(brut["timestamp"], field=f"{champ}.timestamp")
            quote = _without_source_prefix(
                timestamp, _plain(brut["quote"], field=f"{champ}.quote"))
            # Filet déterministe : un extrait verbatim dont SEUL l'horodatage est
            # faux est rattaché à son ancre canonique réelle. Le pointeur rendu
            # est donc TOUJOURS celui du transcript, jamais celui, erroné, du
            # modèle — l'audit qui relit le Markdown le revérifie à l'identique.
            ancrage = _ancrer_preuve(segments, timestamp, quote, ordered=ordered)
            preuve: dict[str, object] = {
                "timestamp": ancrage.timestamp,
                "quote": quote,
                "valid": ancrage.valide,
            }
            if ancrage.normalise:
                preuve["reancrage"] = {"de": ancrage.origine,
                                       "vers": ancrage.timestamp}
            return preuve

        parsed_claims: list[dict[str, object]] = []
        for raw_claim in claims:
            if not isinstance(raw_claim, dict):
                raise StructuredReportError("claim hors contrat",
                                            code=AFFIRMATION_HORS_CONTRAT)
            attendues = {"text", "primary_evidence", "secondary_evidence"}
            recues = set(raw_claim)
            if recues - attendues:
                # Une clé EN TROP est très probablement l'ancien `evidence`,
                # c'est-à-dire la liste ouverte qu'on vient de supprimer. Le
                # code est donc plus précis qu'un « hors contrat » générique.
                code = (TROP_DE_PREUVES if "evidence" in recues
                        else AFFIRMATION_HORS_CONTRAT)
                raise StructuredReportError(
                    f"clés inattendues dans claim : {sorted(recues - attendues)}",
                    code=code)
            if recues != attendues:
                # Le code DIT laquelle manque. Un motif générique renverrait au
                # modèle une consigne générique, et c'est ce qui a coûté trois
                # tentatives pour rien — voir `PREUVE_SECONDAIRE_ABSENTE`.
                if "primary_evidence" not in recues:
                    code = PREUVE_PRINCIPALE_ABSENTE
                elif attendues - recues == {"secondary_evidence"}:
                    code = PREUVE_SECONDAIRE_ABSENTE
                else:
                    code = AFFIRMATION_HORS_CONTRAT
                raise StructuredReportError(
                    f"clés manquantes dans claim : {sorted(attendues - recues)}",
                    code=code)
            text = _plain(raw_claim["text"], field="claim.text")
            if raw_claim["primary_evidence"] is None:
                raise StructuredReportError(
                    "une affirmation sans preuve principale n'est pas retenue",
                    code=PREUVE_PRINCIPALE_ABSENTE)
            parsed_evidence = [_preuve(raw_claim["primary_evidence"],
                                       "primary_evidence")]
            secondaire = raw_claim["secondary_evidence"]
            if secondaire is not None:
                parsed_evidence.append(_preuve(secondaire, "secondary_evidence"))

            # Ni numéro ni décision ICI : la sélection a besoin de connaître
            # la validité de TOUTES les affirmations avant d'en garder une.
            # Ni numéro ni décision ICI : la sélection a besoin de connaître
            # la validité de TOUTES les affirmations avant d'en garder une.
            parsed_claims.append({
                "text": text,
                "evidence": parsed_evidence,
                # `abstention` a disparu : une question sans réponse se dit
                # maintenant au niveau SECTION, par `answer_status`, au lieu de
                # fabriquer une affirmation artificielle « aucune information
                # trouvée ». Une affirmation retenue porte donc TOUJOURS une
                # preuve principale — reste à savoir si elle est vérifiable.
                "fonde": any(e["valid"] for e in parsed_evidence),
            })
        parsed.append({
            "question_id": question_id,
            "title": title,
            "answer_status": statut,
            "claims": parsed_claims,
        })

    if seen_questions != set(range(1, expected_sections + 1)):
        raise StructuredReportError("la série des question_id est incomplète")

    # Trier AVANT de rendre : la sélection numérote, et un `claim-003` placé
    # au-dessus d'un `claim-001` dans le document rendu serait illisible. Le
    # modèle n'est pas tenu d'émettre ses sections dans l'ordre.
    parsed.sort(key=lambda section: int(section["question_id"]))
    return parsed


@dataclass
class Normalisation:
    """Le compte rendu d'un DÉFAUT déterministe posé avant validation.

    Jamais un booléen nu, pour la même raison qu'une :class:`Reparation` n'en
    est pas un : on veut pouvoir dire ENSUITE combien d'affirmations ont été
    complétées, et à partir de quel document. Sans cette trace, un modèle qui se
    met à omettre la clé sur toutes ses affirmations se cacherait derrière un
    simple « ça passe », et la dérive ne se verrait jamais.
    """

    appliquee: bool
    affirmations: int = 0
    sha_avant: str = ""
    sha_apres: str = ""
    charge: object = None

    def journal(self) -> dict:
        """Ce qui part en provenance et en télémétrie. Distinct d'une réparation.

        `kind` NOMME le seul défaut que ce filet s'autorise : jamais confondu
        avec une réparation de balance (`repair`) ni un déballage d'enveloppe
        (`envelope`). L'empreinte avant/après PROUVE ce qui a été touché.
        """
        return {
            "applied": self.appliquee,
            "kind": "secondary_evidence_defaulted" if self.appliquee else None,
            "claims": self.affirmations or None,
            "original_sha": self.sha_avant or None,
            "normalized_sha": self.sha_apres or None,
        }


def _empreinte_charge(charge: object) -> str:
    """SHA-256 tronqué d'une charge JSON. Sert à PROUVER ce qui a été défaulté."""
    return hashlib.sha256(
        json.dumps(charge, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]


def normaliser_preuve_secondaire(payload: object) -> Normalisation:
    """Défaute la SEULE omission de clé non ambiguë du contrat : `secondary_evidence`.

    ## Pourquoi cette clé, et elle seule

    Le contrat exige trois clés par affirmation — `text`, `primary_evidence`,
    `secondary_evidence` — et `secondary_evidence` est la seule dont l'omission
    a un sens UNIQUE. « Pas de seconde preuve » est déjà exprimable dans le
    contrat, et sa valeur est `null` (voir :func:`output_schema` et le
    traitement `if secondaire is not None` de :func:`valider_structure`). Écrire
    `null` là où le modèle a omis la clé ne fait donc que rendre EXPLICITE ce
    que l'omission signifiait — c'est un no-op sémantique, pas une invention.

    À l'inverse, une `primary_evidence` absente est un défaut de FONDATION :
    aucune valeur par défaut ne peut la remplacer sans fabriquer une citation.
    C'est pourquoi ce filet ne la touche jamais.

    ## Ce qu'il touche, et RIEN d'autre

    Une affirmation dont l'ensemble des clés est EXACTEMENT ``{"text",
    "primary_evidence"}`` : la clé secondaire manque, et aucune clé en trop ne
    trahit une autre faute. La valeur de `primary_evidence` (nulle ? citation
    fausse ?) n'est pas jugée ici — elle l'est APRÈS, par le validateur, à
    l'identique. Ce filet ne masque donc rien de la fondation : il complète une
    clé, la validation reste souveraine sur le reste.

    ## Ce qu'il ne fait pas

    Il ne mute pas `payload` — il rend une COPIE défaultée. L'original reste
    disponible pour la télémétrie et le débogage. Et il ne relâche pas le
    contrat : :func:`valider_structure` REFUSE toujours l'omission ; ce défaut
    vit au-dessus d'elle, se compte à part (`PASS_NORMALIZED`) et se prouve par
    empreinte.
    """
    if not isinstance(payload, dict) or not isinstance(
        payload.get("sections"), list
    ):
        return Normalisation(False)

    charge = copy.deepcopy(payload)
    defauts = 0
    for section in charge["sections"]:
        if not isinstance(section, dict) or not isinstance(
            section.get("claims"), list
        ):
            continue
        for claim in section["claims"]:
            # EXACTEMENT `text` + `primary_evidence`. Une clé en trop trahirait
            # une autre faute (hors contrat) ; `primary_evidence` absente est un
            # défaut de fondation que la validation doit refuser, pas nous.
            if isinstance(claim, dict) and set(claim) == {
                "text", "primary_evidence"
            }:
                claim["secondary_evidence"] = None
                defauts += 1

    if not defauts:
        return Normalisation(False)
    return Normalisation(
        True,
        affirmations=defauts,
        sha_avant=_empreinte_charge(payload),
        sha_apres=_empreinte_charge(charge),
        charge=charge,
    )


def render_structured(
    payload: object,
    transcript_markdown: str,
    *,
    expected_sections: int,
    language: str | None,
    journal: dict | None = None,
) -> str:
    """Valide la structure LLM puis rend corps compact + annexe auditable.

    Le modèle choisit les mots de l'affirmation et propose des preuves. Memoglyph
    possède la structure : il numérote, contrôle et rend les marqueurs Markdown.
    Une preuve invalide reste dans l'annexe et devient une réserve lors de
    :func:`audit`, mais n'obtient jamais un badge d'horodatage dans le corps.
    """
    parsed = valider_structure(payload, transcript_markdown,
                               expected_sections=expected_sections)

    # Le plafond total suit le nombre de sections attendues, au lieu d'être une
    # constante qui les contredit.
    #
    # MESURÉ : `MAX_REPORT_CLAIMS = 10` contre six sections à deux affirmations
    # — douze — rejetait par construction quatre genres sur cinq. La constante
    # avait été posée pour un genre à quatre sections et n'a jamais suivi.
    #
    # Elle reste un PLANCHER : un genre court n'obtient pas moins de dix.
    # ⚠️ Ce plafond N'EST PAS vestigial, contrairement aux apparences.
    # MESURÉ : privé de toute cardinalité, Mistral produit 31
    # affirmations sur six sections — `[4, 10, 9, 1, 6, 1]`. Sa concision
    # habituelle est de l'OBÉISSANCE, pas un penchant. Le plafond porte donc
    # réellement la concision du document, et on le rend cohérent plutôt que de
    # le lever.
    #
    # Il vit ICI et non dans `valider_structure` : c'est une règle de SÉLECTION,
    # pas d'acceptation. Une sortie qui déborde n'est pas refusée, elle est
    # taillée.
    plafond_total = max(MAX_REPORT_CLAIMS,
                        expected_sections * DOTATION_PAR_SECTION)
    ecartees = _selectionner(parsed, plafond_total)

    # Observabilité du filet d'ancrage : ce qui a été RÉANCRÉ dans le document
    # FINAL — après sélection, donc uniquement les citations qui survivent au
    # rendu. On ne consigne que l'horodatage d'origine, le canonique retenu et
    # une empreinte de l'extrait ; aucune prose en plus, l'extrait vit déjà dans
    # l'annexe. Une génération sans réancrage laisse `applied: False`, jamais un
    # champ absent — la dérive d'un modèle qui se met à mal horodater reste
    # lisible plutôt que muette.
    if journal is not None:
        reancres = [
            {**item["reancrage"], "quote_sha": _empreinte_charge(item["quote"])}
            for section in parsed
            for claim in section["claims"]
            for item in claim["evidence"]
            if "reancrage" in item
        ]
        journal["citation_anchoring"] = {
            "applied": bool(reancres),
            "kind": "citation_reanchored" if reancres else None,
            "reanchored": len(reancres),
            "citations": reancres or None,
        }

    source_none = "source absente" if (language or "fr").lower() == "fr" else "source absent"
    source_check = (
        "source à vérifier"
        if (language or "fr").lower() == "fr"
        else "source to check"
    )
    sans_reponse = (
        "La transcription ne répond pas à cette question."
        if (language or "fr").lower() == "fr"
        else "The transcript does not answer this question."
    )
    appendix_title = (
        "Sources et vérifications"
        if (language or "fr").lower() == "fr"
        else "Sources and verification"
    )

    body: list[str] = []
    appendix: list[str] = [EVIDENCE_MARKER, "", f"## {appendix_title}", ""]
    # La sélection a numéroté les affirmations retenues ; les sections SANS
    # RÉPONSE en reçoivent une chacune, à la suite, pour que l'annexe reste
    # complète et que les identifiants ne se marchent pas dessus.
    numero = sum(len(section["claims"]) for section in parsed)
    for section in parsed:
        body.extend((f"## {section['title']}", ""))
        for claim in section["claims"]:
            evidence = claim["evidence"]
            valid = [item for item in evidence if item["valid"]]
            invalid = [item for item in evidence if not item["valid"]]
            suffix = "".join(
                f" [source {item['timestamp']}]" for item in valid
            )
            if invalid or not evidence:
                suffix += f" [⚠ {source_check}]"
            body.append(f"- {claim['text']}{suffix}")

            appendix.extend((f"### {claim['id']}", "", f"- {claim['text']}"))
            for item in evidence:
                appendix.append(
                    f"> [source {item['timestamp']}] {item['quote']}"
                )
            appendix.append("")
        if section["answer_status"] == "not_found":
            # La section vide se DIT, elle ne se laisse pas deviner : un titre
            # suivi de rien se lit comme un bug.
            #
            # Et elle reste une ABSTENTION pour l'audit, qui relit le Markdown
            # sans connaître le JSON. `> [source none]` est sa notation, celle
            # que `audit()` compte déjà — la forme du contrat a changé, pas la
            # trace laissée au document.
            numero += 1
            identifiant = f"claim-{numero:03d}"
            body.append(f"- {sans_reponse} [{source_none}]")
            appendix.extend((f"### {identifiant}", "", f"- {sans_reponse}",
                             "> [source none]", ""))
        body.append("")

    if ecartees:
        # ANNONCÉ, jamais silencieux. Une sélection muette laisserait croire que
        # le modèle a produit exactement ce qu'on voit, et le lecteur ne saurait
        # pas qu'il manque des affirmations ni sur quel critère.
        fr = (language or "fr").lower() == "fr"
        body.append("")
        body.append(
            f"> ℹ️ **{ecartees} affirmation(s) écartée(s).** Sont gardées celles "
            f"dont une citation a été VÉRIFIÉE dans le transcript, et au moins "
            f"une par section, dans la limite de {plafond_total}. Les "
            f"affirmations sans citation vérifiable ne complètent pas le "
            f"compte-rendu : elles ne sont pas fausses pour autant, mais rien "
            f"ici ne permet de les appuyer."
            if fr else
            f"> ℹ️ **{ecartees} claim(s) dropped.** Kept are those whose citation "
            f"was VERIFIED in the transcript, and at least one per section, up "
            f"to {plafond_total}. Claims without a verifiable citation do not "
            f"pad the report: that does not make them false, but nothing here "
            f"supports them."
        )

    return "\n".join(body + appendix).rstrip()


def _short(text: str, limit: int = 120) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def _transcript_segments(markdown: str) -> dict[str, list[str]]:
    _, body = _split_frontmatter(markdown)
    segments: dict[str, list[str]] = {}
    for match in _SEGMENT.finditer(body):
        text = " ".join(match.group("text").split()).strip()
        if text:
            segments.setdefault(match.group("timestamp"), []).append(text)
    return segments


def _ordered_segments(markdown: str) -> list[tuple[str, str | None, str]]:
    """Segments dans l'ORDRE du transcript : ``(horodatage, locuteur, texte)``.

    Le dictionnaire de :func:`_transcript_segments` perd l'ordre et le locuteur ;
    l'ancrage multi-segments a besoin des deux, pour ne recoudre que des tranches
    CONSÉCUTIVES d'un MÊME locuteur.
    """
    _, body = _split_frontmatter(markdown)
    ordered: list[tuple[str, str | None, str]] = []
    for match in _SEGMENT.finditer(body):
        text = " ".join(match.group("text").split()).strip()
        if not text:
            continue
        speaker = match.group("speaker")
        ordered.append(
            (match.group("timestamp"), speaker.strip() if speaker else None, text)
        )
    return ordered


def _claim_text(lines: list[str], start: int) -> tuple[str, int] | None:
    """Lit le prochain bloc de prose ; les titres et règles ne sont pas des claims."""
    line = lines[start]
    item = _LIST_ITEM.match(line)
    if item:
        return item.group(1).strip(), start + 1
    if (
        not line.strip()
        or _HEADING.match(line)
        or _RULE.match(line)
        or line.lstrip().startswith("```")
        or line.startswith(">")
    ):
        return None

    paragraph = [line.strip()]
    index = start + 1
    while index < len(lines):
        candidate = lines[index]
        if (
            not candidate.strip()
            or _HEADING.match(candidate)
            or _RULE.match(candidate)
            or _LIST_ITEM.match(candidate)
            or candidate.startswith(">")
            or candidate.lstrip().startswith("```")
        ):
            break
        paragraph.append(candidate.strip())
        index += 1
    return " ".join(paragraph), index


def audit(report_markdown: str, transcript_markdown: str) -> CitationAudit:
    """Contrôle les marqueurs source sans transformer le compte-rendu."""
    segments = _transcript_segments(transcript_markdown)
    ordered = _ordered_segments(transcript_markdown)
    report_meta, report_body = _split_frontmatter(report_markdown)
    # Depuis le contrat v2, le corps éditorial ne contient que des horodatages
    # compacts. L'annexe après ce marqueur porte les affirmations et extraits
    # complets que le contrôleur doit relire. Les anciens CR sans marqueur
    # conservent leur comportement historique.
    if EVIDENCE_MARKER in report_body:
        report_body = report_body.split(EVIDENCE_MARKER, 1)[1]
    # Un CR sauvegardé peut être réaudité sans prendre les réserves ajoutées par
    # Memoglyph pour de nouvelles affirmations du modèle.
    if report_meta.get("type") == "report":
        if GENERATED_NOTICE_MARKER in report_body:
            report_body = report_body.split(GENERATED_NOTICE_MARKER, 1)[0]
    lines = report_body.replace("\r\n", "\n").replace("\r", "\n").splitlines()

    claims: list[_Claim] = []
    warnings: list[str] = []
    current: _Claim | None = None
    citations = 0
    valid_citations = 0
    in_fence = False
    index = 0

    def close_claim() -> None:
        nonlocal current
        if current is not None and current.markers == 0:
            warnings.append(
                f"affirmation sans citation : « {_short(current.text)} »"
            )
        current = None

    while index < len(lines):
        line = lines[index]
        if line.lstrip().startswith("```"):
            # Un bloc de code n'appartient pas au contrat de CR : son contenu
            # n'est pas pris à tort pour une série d'affirmations.
            in_fence = not in_fence
            close_claim()
            index += 1
            continue
        if in_fence:
            index += 1
            continue

        source = _SOURCE.match(line)
        if source:
            target = source.group("target").lower()
            quote = (source.group("quote") or "").strip()
            if current is None:
                if target != "none":
                    citations += 1
                warnings.append(f"citation orpheline : « {_short(line)} »")
                index += 1
                continue

            current.markers += 1
            if target == "none":
                current.abstentions += 1
                if quote:
                    warnings.append(
                        f"source none doit rester vide : « {_short(line)} »"
                    )
            else:
                citations += 1
                if not quote:
                    warnings.append(f"citation vide à {target}")
                elif target not in segments:
                    warnings.append(f"horodatage absent du transcript : {target}")
                elif _quote_from_anchor(ordered, target, quote):
                    current.valid_citations += 1
                    valid_citations += 1
                else:
                    warnings.append(
                        f"citation altérée à {target} : « {_short(quote)} »"
                    )
            index += 1
            continue

        if line.startswith(">"):
            close_claim()
            warnings.append(
                f"bloc de citation hors contrat : « {_short(line)} »"
            )
            index += 1
            continue

        parsed = _claim_text(lines, index)
        if parsed is not None:
            close_claim()
            text, index = parsed
            current = _Claim(text=text)
            claims.append(current)
            continue

        if line.strip() and (_HEADING.match(line) or _RULE.match(line)):
            close_claim()
        index += 1

    close_claim()

    if not claims:
        warnings.append("aucune affirmation détectée dans le compte-rendu")

    for claim in claims:
        if claim.abstentions and claim.valid_citations:
            warnings.append(
                f"source none et citation mélangées : « {_short(claim.text)} »"
            )
        elif claim.abstentions > 1:
            warnings.append(
                f"plusieurs source none pour une affirmation : « {_short(claim.text)} »"
            )

    abstentions = sum(1 for claim in claims if claim.abstentions)
    claims_with_valid_citation = sum(
        1 for claim in claims if claim.valid_citations
    )
    return CitationAudit(
        status="warnings" if warnings else "citation_integrity_verified",
        claims=len(claims),
        claims_with_valid_citation=claims_with_valid_citation,
        abstentions=abstentions,
        citations=citations,
        valid_citations=valid_citations,
        warnings=tuple(warnings),
    )


def warning_notice(audit_result: CitationAudit, language: str | None) -> str:
    """Avertissement visible ajouté sans retirer ni réécrire le CR."""
    if not audit_result.warnings:
        return ""
    count = len(audit_result.warnings)
    details = "\n".join(f"- {warning}" for warning in audit_result.warnings)
    if (language or "fr").lower() == "fr":
        label = f"{count} anomalie{'s' if count > 1 else ''} de citation"
        return (
            "\n---\n\n"
            f"> ⚠️ **Citations à vérifier — {label}.**\n"
            "> Ce contrôle vérifie uniquement l’intégrité des extraits et de leurs "
            "horodatages, jamais la vérité des affirmations.\n\n"
            f"{details}\n"
        )
    label = f"{count} citation issue{'s' if count > 1 else ''}"
    return (
        "\n---\n\n"
        f"> ⚠️ **Citations to check — {label}.**\n"
        "> This check verifies only excerpt and timestamp integrity, never whether "
        "a claim is true.\n\n"
        f"{details}\n"
    )
