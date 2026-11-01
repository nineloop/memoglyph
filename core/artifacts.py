"""Artefacts d'hallucination de Whisper — détection DÉTERMINISTE.

Whisper hallucine sur les plages NON VOCALES (Koenecke et al., FAccT 2024). Nous
en avons observé deux formes :

  · **boucles de répétition** — en dernière ligne, sur le silence final :
    « Sn » ×221, « hot » ×219 ;
  · **génériques de sous-titrage** — y compris en MILIEU de fichier :
    « Sous-titrage Société Radio-Canada », phrase apprise sur des sous-titres
    et recrachée sur du silence.

⚠️ **Les boucles viennent du DÉCOUPAGE, pas du silence.** L'expérience
l'a montré : les 4 fichiers ont été purgés puis ré-importés en passe unique,
depuis le même média : plus AUCUNE boucle. Les nouvelles dernières lignes sont
« Peace. » et « Music. ». Les deux boucles étaient donc bien un artefact du
DÉCOUPAGE, comme les autres — les transcripts fautifs dataient de l'époque
`STT_CHUNK_SEC=120`.

Les génériques, eux, ont bel et bien persisté : 3 retirés du même fichier en
passe unique. Eux ne dépendent pas du découpage.

Le détecteur de boucles reste néanmoins branché, pour deux raisons : il ne coûte
rien, et il est le seul filet si quelqu'un réactive `MEMOGLYPH_STT_CHUNK_SEC` —
le test `ml` prouve qu'il se déclenche dans ce cas. Mais il ne faut PAS le
présenter comme une réponse à un défaut du mode actuel.

## Ce qui est fait, et pourquoi pas plus

Rien ne disparaît en silence — c'est un invariant du projet. Les deux formes sont
traitées différemment parce qu'elles ne portent pas la même chose :

  · une boucle commence souvent par de la VRAIE parole (« Let's start a hot hot
    hot… ») : on replie la répétition et on laisse une marque lisible, à
    l'horodatage exact. Le lecteur voit ce qui s'est passé ;
  · un générique n'est parole à aucun endroit : le segment est retiré du corps et
    consigné dans le front-matter, où il reste auditable.

## Ce qui n'est PAS filtré, délibérément

« Thanks for watching », « Merci d'avoir regardé » et autres formules de clôture
sont des artefacts Whisper CONNUS — et aussi des phrases que des gens prononcent
vraiment, en particulier dans les vidéos qui composent ce corpus. Les filtrer
supprimerait de la parole réelle. On s'en tient aux mentions de PRODUCTION de
sous-titres, qu'aucun intervenant ne prononce.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Sequence

from .stt_base import Segment

# Seuil (cf. tests/unit/test_stt_logic.py) : une boucle occupe tout le segment,
# un français même très répétitif en reste loin. 60 % laisse une marge des deux
# côtés ; 40 % aurait signalé du texte parfaitement valide.
RATIO_BOUCLE = 0.60
MOTS_MINIMUM = 8

# Combien d'occurrences on garde avant de replier : assez pour que la répétition
# soit visible en tant que telle, pas assez pour noyer le texte.
OCCURRENCES_GARDEES = 3

# Mentions de PRODUCTION de sous-titres. Volontairement étroit — voir l'en-tête.
_GENERIQUE = re.compile(
    r"sous-titr(?:age|es)\s+(?:soci[ée]t[ée]|r[ée]alis[ée]s?|st'|par\s)"
    r"|amara\.org"
    r"|subtitles?\s+by\s"
    r"|sous-titrage\s+(?:fr|st)\b",
    re.IGNORECASE,
)
# Un générique est COURT. Cette borne évite d'emporter une phrase réelle qui
# citerait l'un de ces termes.
MOTS_MAX_GENERIQUE = 12


@dataclass
class Rapport:
    """Ce qui a été replié ou retiré — destiné au front-matter, pas aux logs."""
    boucles: list[str]
    generiques: list[str]
    series: list[str]

    def __bool__(self) -> bool:
        return bool(self.boucles or self.generiques or self.series)


def _mot_dominant(texte: str) -> tuple[str, int, int]:
    mots = texte.split()
    if not mots:
        return "", 0, 0
    normalises = [m.lower().strip(".,!?;:…") for m in mots]
    mot, n = Counter(normalises).most_common(1)[0]
    return mot, n, len(mots)


# Une boucle peut aussi être une suite SANS espace : « 입입입… » ×221, observée sur une
# réunion transcrite en passe unique, donc sans découpage. Elle tient en un ou deux
# « mots », et le seuil par mots ne la voit pas. Vingt répétitions d'un motif d'une à
# trois lettres : aucune graphie expressive (« Ahhhh », « hahaha ») n'y arrive.
REPETITIONS_SUITE = 20
_SUITE = re.compile(r"([^\W\d_]{1,3}?)\1{%d,}" % (REPETITIONS_SUITE - 1))


def _suite(texte: str) -> tuple[str, int, int, int] | None:
    """(motif, répétitions, début, fin) de la plus longue suite sans espace, ou None."""
    suites = list(_SUITE.finditer(texte))
    if not suites:
        return None
    m = max(suites, key=lambda m: len(m[0]))
    return m[1], len(m[0]) // len(m[1]), m.start(), m.end()


def est_une_boucle(texte: str) -> bool:
    """Vrai si un mot occupe au moins 60 % d'un segment d'au moins 8 mots, ou si un
    motif de une à trois lettres se répète au moins vingt fois sans espace."""
    _, n, total = _mot_dominant(texte)
    if total >= MOTS_MINIMUM and n / total >= RATIO_BOUCLE:
        return True
    return _suite(texte) is not None


def motif_de_boucle(texte: str) -> tuple[str, int]:
    """(motif, répétitions) d'une boucle, pour le rapport."""
    suite = _suite(texte)
    _, n, total = _mot_dominant(texte)
    if suite and not (total >= MOTS_MINIMUM and n / total >= RATIO_BOUCLE):
        return suite[0], suite[1]
    mot, n, _ = _mot_dominant(texte)
    return mot, n


def est_un_generique(texte: str) -> bool:
    """Vrai si le segment est une mention de production de sous-titres."""
    return bool(_GENERIQUE.search(texte)) and len(texte.split()) <= MOTS_MAX_GENERIQUE


def replier_boucle(texte: str) -> str:
    """Replie la répétition en gardant le préfixe réel et une marque explicite.

    « Let's start a hot hot hot … » (×219) devient
    « Let's start a hot hot hot […] » avec le décompte : la parole du début est
    conservée, et le lecteur sait exactement ce qui a été retiré.
    """
    _, n_mot, total = _mot_dominant(texte)
    suite = _suite(texte)
    if suite and not (total >= MOTS_MINIMUM and n_mot / total >= RATIO_BOUCLE):
        motif, n, debut, fin = suite
        garde = (texte[:debut] + motif * OCCURRENCES_GARDEES).strip()
        reste = texte[fin:].strip()
        return (f"{garde} […{n}× « {motif} » — boucle de répétition repliée]"
                + (f" {reste}" if reste else ""))
    mot, n, _ = _mot_dominant(texte)
    mots = texte.split()
    sortie: list[str] = []
    vus = 0
    for m in mots:
        if m.lower().strip(".,!?;:…") == mot:
            vus += 1
            if vus > OCCURRENCES_GARDEES:
                continue
        sortie.append(m)
    return " ".join(sortie) + f" […{n}× « {mot} » — boucle de répétition repliée]"


# Une boucle peut aussi s'étaler SUR PLUSIEURS segments courts, et le seuil
# ci-dessus est alors aveugle : il exige 8 mots dans UN segment. Observé :
# « Let's go to London. » répété à l'identique sur
# 21 segments consécutifs, « Music. » sur 3, « mon » sur 4. Aucun n'atteignait
# 8 mots, donc aucun n'était vu.
REPETITIONS_MINIMUM = 3


def boucles_entre_segments(
    segments: Sequence[Segment], *, minimum: int = REPETITIONS_MINIMUM
) -> list[tuple[int, int, str]]:
    """Séries de segments CONSÉCUTIFS portant le même texte.

    Renvoie (index de début, longueur, texte). Comparaison sur le texte
    normalisé — casse et ponctuation finale ignorées, car Whisper varie sur les
    deux d'une occurrence à l'autre.
    """
    def cle(s: Segment) -> str:
        return s.text.strip().lower().rstrip(".!?,;… ")

    series: list[tuple[int, int, str]] = []
    debut = 0
    for i in range(1, len(segments) + 1):
        if i < len(segments) and cle(segments[i]) == cle(segments[debut]):
            continue
        n = i - debut
        if n >= minimum and cle(segments[debut]):
            series.append((debut, n, segments[debut].text.strip()))
        debut = i
    return series


def scrub(segments: Sequence[Segment]) -> tuple[list[Segment], Rapport]:
    """Segments nettoyés + inventaire de ce qui a bougé.

    Ne supprime jamais un segment porteur de parole : une boucle est repliée sur
    place, seul un générique — qui n'est parole à aucun endroit — est retiré.
    """
    gardes: list[Segment] = []
    rapport = Rapport(boucles=[], generiques=[], series=[])

    # Séries répétées sur plusieurs segments : on SIGNALE sans rien retirer. Une
    # phrase répétée peut être un refrain, un slogan scandé, une relance — pas
    # forcément un artefact. Le lecteur tranche ; nous ne faisons que pointer.
    for debut, n, texte in boucles_entre_segments(segments):
        rapport.series.append(f"{_hms(segments[debut].start)} « {texte[:40]} » ×{n}")

    for seg in segments:
        if est_un_generique(seg.text):
            rapport.generiques.append(f"{_hms(seg.start)} {seg.text.strip()}")
            continue
        if est_une_boucle(seg.text):
            mot, n = motif_de_boucle(seg.text)
            rapport.boucles.append(f"{_hms(seg.start)} « {mot} » ×{n}")
            gardes.append(
                Segment(seg.start, seg.end, replier_boucle(seg.text), speaker=seg.speaker)
            )
            continue
        gardes.append(seg)

    return gardes, rapport


def _hms(secondes: float) -> str:
    s = int(secondes)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"
