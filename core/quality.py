"""Indicateurs de qualité d'une transcription (calcul pur, sans LLM).

## Stabilité de la langue

Whisper bascule parfois en **traduction** au milieu d'un fichier, sans le
signaler : « description fidèle du poète » ressort en `a loyal description of
the poet`. Le transcript est alors silencieusement corrompu — le contenu n'est
plus ce qui a été dit — et rien dans le `.md` ne le trahit. Un contenu mélangeant
réellement les langues (extraits en VO, plateau à interprètes, montage) produit
la même signature.

On mesure donc la part de segments dont la langue s'écarte de celle déclarée.
C'est un **avertissement**, pas un classifieur : il invite à vérifier, il ne
décide de rien. Un plateau français avec invités américains ressort nettement
au-dessus des autres enregistrements, qui restent près de zéro.

⚠️ Un seul cas positif observé : le seuil ci-dessous est un ordre de grandeur, pas
une valeur calibrée. Ne pas lui faire prendre de décision automatique.

## Portée assumée

Seule la paire **français / anglais** est couverte — celle qui se produit
réellement ici. Pour toute autre langue, la fonction renvoie `None` (« je ne sais
pas ») et **jamais** `0.0` : annoncer « aucun mélange » sur une paire qu'on n'a
pas su examiner serait une garantie inventée.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Iterable, Optional

# Mots-outils : très fréquents, propres à chaque langue, robustes aux fautes de
# transcription (contrairement au vocabulaire de contenu, qui circule d'une
# langue à l'autre — « IMAX », « streaming »).
_STOPWORDS = {
    "en": {"the", "and", "you", "that", "this", "with", "have", "was", "for",
           "are", "but", "not", "they", "what", "from", "just", "like", "know",
           "because", "would", "there", "about", "were", "been", "his", "her"},
    "fr": {"le", "la", "les", "de", "des", "du", "et", "que", "qui", "est",
           "pas", "une", "un", "dans", "pour", "vous", "nous", "ce", "il",
           "elle", "sur", "avec", "mais", "plus", "tout", "sont", "cette"},
}

SUPPORTED_PAIR = ("fr", "en")

# Au-delà : à vérifier avant de se fier au contenu ou de nommer une voix.
UNSTABLE_THRESHOLD = 0.03

_WORD = re.compile(r"[a-zà-ÿ']+")

# Un seuil de VOLUME était une mauvaise question. `_MIN_SEGMENTS = 20` taisait
# `8/8 segments en dérive` — un résultat pourtant écrasant — et acceptait
# `2/40`, qui ne tranche rien. Ce n'est pas `n` qui décide, c'est la proportion
# observée : un résultat extrême sur peu d'échantillons vaut mieux qu'un
# résultat moyen sur beaucoup.
#
# MESURÉ, et c'est ce qui a écarté l'idée d'un seuil dérivé de la DURÉE : la
# densité de segments varie d'un facteur 3 à 4 selon l'enregistrement ET le
# moteur — 0,40 à 0,51 seg/s sur un message vocal de 19,8 s, 0,11 à 0,16 sur un
# discours de 120 s. Un seuil tiré de la durée aurait demandé une calibration
# par moteur, par langue et par contenu : une constante devinée remplacée par
# une formule devinée.
#
# ⚠️ RÉSERVE, et elle est sérieuse : l'intervalle suppose des tirages
# INDÉPENDANTS. Dix segments d'affilée du même locuteur dans la même langue ne
# sont pas dix observations, plutôt une. L'intervalle est donc OPTIMISTE, et
# resserrer davantage demanderait de compter les BASCULES plutôt que les
# segments. Non fait ici : ce serait une correction non calibrée.
# Un PLANCHER subsiste, et il a un rôle distinct de la confiance : `language_mix`
# est AFFICHÉ. Rendre `0,0` sur trois segments montrerait « vérifié propre » sur
# presque rien — c'est ce que l'ancien seuil protégeait, et ce rôle ne se voit
# pas à la lecture du seul seuil.
#
# Il descend de 20 à 5. Motif : les messages vocaux courts, exactement le cas
# d'usage WhatsApp, rendent 8 à 10 segments — Voxtral 8, Whisper 10 sur 19,8 s.
# Vingt les taisait tous. Cinq admet ces deux-là et continue d'écarter la
# poignée de fragments.
_MIN_SEGMENTS = 5

_Z_95 = 1.96


def _wilson(succes: int, total: int, z: float = _Z_95) -> tuple[float, float]:
    """Intervalle de Wilson à 95 % pour une proportion.

    Choisi plutôt que l'approximation normale parce qu'il reste correct aux
    extrêmes — et nos cas intéressants SONT extrêmes : `8/8` rend [0,68 ; 1,00],
    quand l'approximation normale rendrait un intervalle nul et mensonger.
    """
    if total <= 0:
        return (0.0, 1.0)
    p = succes / total
    d = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / d
    ecart = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / d
    return (max(0.0, centre - ecart), min(1.0, centre + ecart))


def _proportion(succes: int, total: int) -> Optional[float]:
    """La proportion observée, ou `None` s'il n'y a rien de mesurable.

    La MESURE n'est pas le lieu de la statistique — l'ALERTE l'est. Ma première
    version taisait la proportion quand l'intervalle ne tranchait pas contre le
    seuil : statistiquement honnête, et pratiquement mauvais. Prouver « propre »
    contre 3 % exige environ 200 segments, ce qui aurait fait basculer presque
    tout le corpus de « vérifié propre » à « non vérifié ».

    On rend donc l'estimation ponctuelle, et c'est `is_unstable` qui exige de la
    confiance avant de crier — voir sa docstring pour l'asymétrie qui le
    justifie.
    """
    return succes / total if total >= _MIN_SEGMENTS else None


def _lang_of(text: str) -> Optional[str]:
    """Langue dominante d'un segment, ou None si les indices s'équilibrent."""
    words = set(_WORD.findall(text.lower()))
    scores = {lg: len(words & sw) for lg, sw in _STOPWORDS.items()}
    en, fr = scores["en"], scores["fr"]
    if en == fr:
        return None
    return "en" if en > fr else "fr"


# --- Écritures ---------------------------------------------------------------
#
# Une ÉCRITURE n'est pas une langue, et il faut le dire avant de s'en servir :
# l'arabe, le persan et l'ourdou partagent l'arabe ; le russe, l'ukrainien et le
# bulgare partagent le cyrillique. L'écriture ÉLIMINE des candidats, elle n'en
# nomme aucun.
#
# Ce qu'elle fait très bien, en revanche, et sans modèle ni LLM : trancher entre
# deux familles d'écritures avec une certitude quasi totale, en une passe sur la
# chaîne. C'est ce qui manquait, parce que la détection par mots-outils ne
# couvre que le couple français / anglais.
#
# L'intérêt principal n'est pas de nommer la langue. C'est d'attraper la panne
# que ce module surveille depuis le début et qu'il ne voyait que sur ce couple :
# Whisper qui bascule silencieusement en TRADUCTION. Sur un audio arabe rendu en
# anglais, la langue déclarée dirait toujours `ar` — mais le texte serait en
# écriture latine, et cela se voit ici.
_ECRITURES: tuple[tuple[str, tuple[tuple[int, int], ...]], ...] = (
    ("latin", ((0x0041, 0x024F),)),
    ("grec", ((0x0370, 0x03FF), (0x1F00, 0x1FFF))),
    ("cyrillique", ((0x0400, 0x04FF), (0x0500, 0x052F))),
    ("hebreu", ((0x0590, 0x05FF),)),
    ("arabe", ((0x0600, 0x06FF), (0x0750, 0x077F), (0xFB50, 0xFDFF),
               (0xFE70, 0xFEFF))),
    ("devanagari", ((0x0900, 0x097F),)),
    ("han", ((0x4E00, 0x9FFF), (0x3400, 0x4DBF))),
    ("kana", ((0x3040, 0x309F), (0x30A0, 0x30FF))),
    ("hangul", ((0xAC00, 0xD7AF), (0x1100, 0x11FF))),
)


def repartition_ecritures(text: str) -> dict[str, float]:
    """Part de chaque écriture, sur les seuls caractères qui en portent une.

    Chiffres, ponctuation et espaces sont ÉCARTÉS : ils sont communs à toutes
    les écritures, et les compter diluerait la mesure sans rien lui apprendre.
    Un nom propre latin dans une phrase arabe pèse, lui, et c'est voulu — c'est
    précisément le mélange qu'on veut voir.

    Rend `{}` quand aucun caractère n'est classable. C'est « je ne sais pas »,
    et surtout pas « tout va bien » : l'appelant doit pouvoir les distinguer.
    """
    compte: dict[str, int] = {}
    total = 0
    for caractere in text:
        point = ord(caractere)
        for nom, plages in _ECRITURES:
            if any(debut <= point <= fin for debut, fin in plages):
                compte[nom] = compte.get(nom, 0) + 1
                total += 1
                break
    if not total:
        return {}
    return {nom: n / total for nom, n in
            sorted(compte.items(), key=lambda kv: -kv[1])}


def ecriture_dominante(text: str, seuil: float = 0.5) -> Optional[str]:
    """L'écriture majoritaire, ou None si aucune n'atteint `seuil`.

    `None` est une réponse — « ce texte est réellement mélangé » — et pas un
    échec. Un message vocal franco-darija en écriture arabe et latine n'a pas
    d'écriture dominante, et le forcer en nommerait une au hasard.
    """
    parts = repartition_ecritures(text)
    if not parts:
        return None
    nom, part = next(iter(parts.items()))
    return nom if part >= seuil else None


def mesure_langue(
    texts: Iterable[str], declared: Optional[str],
) -> tuple[Optional[float], int]:
    """`(proportion, nombre de segments décidables)`.

    Le compte accompagne la mesure parce que `is_unstable` en a besoin pour
    savoir s'il peut crier. Les séparer obligerait l'appelant à recalculer, et
    un recalcul qui dérive est une source d'incohérence silencieuse.
    """
    textes = list(texts)
    lang = (declared or "").lower()[:2]
    attendue = _ECRITURE_ATTENDUE.get(lang)

    if attendue is not None:
        vus = [e for t in textes if (e := ecriture_dominante(t))]
        return _proportion(sum(e != attendue for e in vus), len(vus)), len(vus)

    if lang not in SUPPORTED_PAIR:
        return None, 0

    decided = [lg for t in textes if (lg := _lang_of(t))]
    return (_proportion(sum(lg != lang for lg in decided), len(decided)),
            len(decided))


def language_mix(texts: Iterable[str], declared: Optional[str]) -> Optional[float]:
    """Part de segments dont la langue s'écarte de `declared`.

    Renvoie None — et non 0.0 — quand la question ne peut pas être tranchée :
    langue déclarée hors paire couverte, absente, ou trop peu de segments
    exploitables. L'appelant doit distinguer « propre » de « non vérifié ».

    Deux régimes, et le premier est nouveau. Si la langue déclarée s'écrit dans
    une écriture NON latine, la comparaison se fait sur l'écriture : c'est
    déterministe, sans liste de mots à tenir, et cela couvre d'un coup toutes
    les langues que les mots-outils ignoraient. Sinon on retombe sur les
    mots-outils, seuls capables de départager deux langues latines.
    """
    return mesure_langue(texts, declared)[0]


def langue_probable(texts: Iterable[str]) -> Optional[str]:
    """Un CODE de langue déduit du texte, ou `None` — jamais une supposition.

    Ce module savait dire « cette écriture est arabe » et « ce texte s'écarte de
    la langue déclarée ». Il ne savait pas dire « ce texte est en anglais »,
    alors que c'est ce dont `profiling` et `report` ont besoin pour choisir leur
    directive de langue ANCRÉE.

    ## Pourquoi ça manquait, et ce que ça a coûté

    Voxtral ne déclare JAMAIS la langue. `_lang_directive(None)` retombe donc sur
    une consigne générique — « write every text value in the SAME language as the
    transcript below » — là où les directives par langue sont rédigées DANS la
    langue cible. Or `core/profiling.py` documente précisément pourquoi :
    « une consigne en français le fait répondre en français, même si elle dit
    réponds en anglais ». La consigne générique n'a pas cet ancrage.

    MESURÉ : un entretien d'embauche ANGLAIS transcrit par Voxtral a été
    profilé en FRANÇAIS — rôles `recruteur` et `candidat`. Le même média en local,
    où Whisper déclare `en`, rend `interviewer` et `candidate`.

    ## Deux règles, et elles priment sur la couverture

    **Ne jamais deviner.** Écriture latine sans mots-outils tranchants → `None`,
    et l'appelant garde son repli générique. Se tromper de langue est PIRE que
    ne pas en imposer : un mauvais ancrage produit un document entier dans la
    mauvaise langue, avec l'assurance d'une consigne explicite.

    **Ne rendre que ce qu'on sait défendre.** L'écriture ne nomme pas une langue
    — arabe, persan et ourdou la partagent. On ne rend un code que là où
    l'écriture est un discriminant suffisant dans le corpus visé, et le latin
    retombe sur les mots-outils, qui ne couvrent que `fr`/`en`.
    """
    textes = [t for t in texts if t and t.strip()]
    if not textes:
        return None

    ecritures = Counter(e for t in textes if (e := ecriture_dominante(t)))
    if not ecritures:
        return None
    dominante, combien = ecritures.most_common(1)[0]
    if combien / sum(ecritures.values()) < 0.5:
        return None  # réellement mélangé : aucune langue ne domine

    if dominante != "latin":
        return _LANGUE_PAR_ECRITURE.get(dominante)

    # Latin : l'écriture n'apprend rien, les mots-outils tranchent — ou pas.
    votes = Counter(lg for t in textes if (lg := _lang_of(t)))
    if not votes:
        return None
    if len(votes) > 1 and votes.most_common(2)[0][1] == votes.most_common(2)[1][1]:
        return None  # ex æquo : on ne départage pas à pile ou face
    return votes.most_common(1)[0][0]


# L'inverse de `_ECRITURE_ATTENDUE`, et volontairement PARTIEL : on ne nomme une
# langue que là où l'écriture suffit à la désigner dans un corpus ordinaire.
# L'arabe est ici parce qu'un enregistrement en persan ou en ourdou n'a jamais
# été rencontré ; le jour où ça arrive, ce défaut deviendra faux et il faudra
# lever cette entrée plutôt que de la corriger à l'aveugle.
_LANGUE_PAR_ECRITURE = {
    "arabe": "ar", "cyrillique": "ru", "grec": "el", "hebreu": "he",
    "devanagari": "hi", "han": "zh", "kana": "ja", "hangul": "ko",
}


# --- Segmentation régulière --------------------------------------------------
#
# Une parole réelle ne tombe jamais sur une grille. Quand Whisper n'arrive pas à
# reconnaître, il retombe sur une FENÊTRE FIXE et rend des segments de durée
# rigoureusement identique — mesuré sur un message vocal franco-darija de 19,8 s :
# dix segments de 2,0 s exactement, bornes jointives, et un texte dont la moitié
# ne veut rien dire.
#
# Cet indicateur existe parce que `language_mix` est AVEUGLE à ce cas. Le texte
# y était intégralement en écriture arabe, cohérent avec le `ar` déclaré : la
# dérive d'écriture ne voyait rien, puisque le français avait été rendu EN
# caractères arabes. Deux pannes distinctes, deux instruments distincts.
_MIN_SEGMENTS_GRILLE = 6  # sous ce volume, une régularité n'est pas un indice
_TOLERANCE_GRILLE = 0.05  # secondes ; deux durées plus proches sont « égales »


def regularite_segmentation(bornes: Iterable[tuple[float, float]]) -> Optional[float]:
    """Part des segments dont la durée est celle, dominante, de l'ensemble.

    1.0 = grille parfaite, donc repli sur une fenêtre fixe. Une transcription
    saine tombe très bas : les durées y suivent le souffle, pas une horloge.

    Rend `None` — jamais 0.0 — quand la question ne se pose pas : trop peu de
    segments, ou durées inexploitables. « Non vérifié » et « propre » ne doivent
    pas se confondre, c'est la règle de ce module.

    On ne juge PAS ici : la fonction rend une proportion, l'appelant décide.
    Un enregistrement réellement découpé en tranches fixes en amont produirait
    la même signature sans être malade.
    """
    durees = [round(fin - debut, 3) for debut, fin in bornes if fin > debut]
    if len(durees) < _MIN_SEGMENTS_GRILLE:
        return None

    # Durée dominante au sens de la TOLÉRANCE, et non de l'égalité stricte : un
    # arrondi de flottant ne doit pas casser un groupe.
    meilleure = 0
    for reference in durees:
        proches = sum(abs(d - reference) <= _TOLERANCE_GRILLE for d in durees)
        meilleure = max(meilleure, proches)
    return meilleure / len(durees)


# Sur les deltas entre débuts de tours consécutifs, des transcriptions saines
# restent dans une bande étroite, loin du seuil ; un message vocal tombé sur la
# grille vaut 1,00. Mais cette observation malade est UNIQUE : on ne sait pas où
# passe la vraie frontière, seulement qu'elle est entre la bande saine et 1,00. Le seuil est
# donc un ordre de grandeur, pas une valeur calibrée, et il ne doit décider de
# rien automatiquement — il invite à regarder.
SEUIL_GRILLE = 0.9


# Les langues dont l'ÉCRITURE suffit à trancher contre du latin. Volontairement
# court : on n'y met que ce qu'on est prêt à défendre. Une langue absente
# retombe sur les mots-outils, donc sur `None` si elle n'est pas dans la paire
# couverte — le silence honnête d'aujourd'hui, jamais un faux « propre ».
_ECRITURE_ATTENDUE = {
    "ar": "arabe", "fa": "arabe", "ur": "arabe",
    "ru": "cyrillique", "uk": "cyrillique", "bg": "cyrillique",
    "el": "grec", "he": "hebreu", "hi": "devanagari",
    "zh": "han", "ja": "kana", "ko": "hangul",
}


_SPEAKER_LINE = re.compile(r"^\*\*[^*]+\*\*\s*(?:\[[\d:]+\])?\s*(.*)")


_PROFILE_MARKERS = ("profil", "profile")


def transcript_texts(body: str) -> list[str]:
    """Texte réellement PRONONCÉ, extrait du corps d'un `.md`.

    Le bloc de profil est écarté : rédigé par le LLM, le mesurer reviendrait à
    évaluer la langue du modèle plutôt que celle de l'enregistrement.

    Deux formats coexistent dans le corpus. Les `.md` récents ont une section
    « ## Transcript » ; ceux d'avant le profilage n'ont que le titre suivi du
    texte. Exiger la section renvoyait **zéro segment** sur les anciens fichiers,
    donc « langue non vérifiée » — un faux silence indiscernable d'un vrai.
    """
    lines = body.splitlines()
    has_section = any(
        line.strip().startswith("#") and "transcript" in line.lower()
        for line in lines
    )

    out: list[str] = []
    keep = not has_section
    for raw in lines:
        line = raw.strip()
        if line.startswith("#"):
            low = line.lower()
            keep = (
                "transcript" in low if has_section
                else not any(m in low for m in _PROFILE_MARKERS)
            )
            continue
        if not keep or not line:
            continue
        m = _SPEAKER_LINE.match(line)
        text = m.group(1).strip() if m else line
        if text:
            out.append(text)
    return out


def is_unstable(mix: Optional[float], total: Optional[int] = None) -> bool:
    """Vrai seulement si le mélange est MESURÉ et si l'on est CONFIANT.

    Un `None` (non vérifié) ne vaut pas alerte : on ne prétend pas savoir.

    `total` — le nombre de segments sur lesquels la proportion a été calculée —
    déplace la décision de l'estimation ponctuelle vers la BORNE BASSE de son
    intervalle de confiance. L'asymétrie qui le justifie : une fausse alerte
    coûte un coup d'œil inutile, une dérive manquée coûte un transcript corrompu
    que personne ne relira. On veut donc rater peu de dérives, et ne crier que
    sur ce qu'on tient.

    Ce que ça change, sur deux cas réels :

    - `8/8` sur un message vocal — borne basse 0,68, l'alerte PART. L'ancien
      code était muet : 8 segments étaient sous son seuil de volume de 20 ;
    - `2/40` — proportion 0,05, borne basse 0,01, l'alerte NE part pas. L'ancien
      code criait, sur une mesure compatible avec « propre ».

    Sans `total`, on retombe sur l'estimation ponctuelle — le comportement
    d'avant, pour les appelants qui n'ont pas le compte sous la main.
    """
    if mix is None:
        return False
    if total is None:
        return mix >= UNSTABLE_THRESHOLD
    bas, _ = _wilson(round(mix * total), total)
    return bas > UNSTABLE_THRESHOLD
