"""Garde-fous DÉTERMINISTES sur les noms propres déduits par le profil LLM.

Le prompt exige une preuve dans le dialogue avant de nommer un locuteur, mais un
modèle n'obéit pas de façon fiable : sur un fichier où un locuteur est réellement
nommé, essais répétés à l'appui, certains modèles perdent le nom la plupart du
temps, d'autres le gardent, et un autre jamais.

Aucun modèle n'est fiable seul → on vérifie en code. Rien ne distingue
syntaxiquement une invention d'un vrai nom, donc on ne se fie qu'à des signaux
structurels : un même nom porté par plusieurs locuteurs (devinette manifeste), et
un nombre de locuteurs élevé.

⚠️ Ce qu'un nom désigne vraiment. Un rôle nomme l'AUTEUR des mots ; une empreinte
vocale capture la VOIX qui les prononce. Ces deux choses divergent dès qu'il y a
doublage ou traduction simultanée : sur un plateau français recevant des invités
américains en voix off, la diarisation isole les **interprètes**, pas les acteurs.
Nommer « Hugo Lambert » y ferait apprendre au registre la voix de l'interprète —
qui rattacherait ensuite à Hugo Lambert tout autre fichier doublé par la même
personne. Le registre étant le seul magasin qui traverse les fichiers, l'erreur
serait globale et silencieuse. D'où le refus de nommer au-delà d'un certain
nombre de locuteurs : ce n'est pas seulement un signe de sur-segmentation, c'est
aussi la signature des plateaux à interprètes et des montages d'extraits, là où
voix et auteur cessent de coïncider.

Ce module est **pur** (aucune I/O) : il est la source unique de la règle, pour
que le `.md` écrit et le registre de voix appliquent exactement le même filtre.
Deux implémentations divergeraient sans que rien ne le signale.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Mapping, Optional

# Au-delà : soit la diarisation est sur-segmentée, soit le contenu est un plateau
# à interprètes / un montage d'extraits — dans les deux cas la voix diarisée n'est
# plus celle de l'auteur des mots, et aucun nom n'est exploitable.
MAX_SPEAKERS_FOR_NAMES = 8

_PAREN = re.compile(r"\s*\(([^)]*)\)")


def _looks_like_name(s: str) -> bool:
    """Ce fragment ressemble-t-il à un nom propre ?

    Une majuscule ne suffit pas : « Interviewer / Co-host » en contient. On exige
    aussi une forme de nom — au plus trois mots, sans séparateur d'énumération.
    """
    if not s or len(s) > 40 or "/" in s:
        return False
    return any(c.isupper() for c in s) and len(s.split()) <= 3


def split_role(role: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Sépare un rôle en (nom propre probable, rôle générique).

    Le modèle produit les deux dispositions, et n'en traiter qu'une laissait
    passer le nom :
      « interviewee (Lina) » → ("Lina", "interviewee")
      « Hugo Lambert (acteur) »   → ("Hugo Lambert", "acteur")
      « Interviewer / Host »    → (None, "Interviewer / Host")
    """
    if not role:
        return None, role
    m = _PAREN.search(role)
    if not m:
        return None, role.strip() or None
    inside = m.group(1).strip()
    outside = _PAREN.sub("", role).strip(" -—:,")
    if _looks_like_name(inside):
        return inside, (outside or None)
    if _looks_like_name(outside):
        return outside, (inside or None)
    return None, role.strip() or None


def name_from_role(role: Optional[str]) -> Optional[str]:
    """Nom propre plausible contenu dans un rôle, quelle que soit sa disposition."""
    return split_role(role)[0]


def strip_name(role: Optional[str]) -> Optional[str]:
    """Rôle privé de son nom propre (« Hugo Lambert (acteur) » → « acteur »).

    Le rôle générique est conservé : il est vérifiable, le nom ne l'est pas. Si
    le rôle se réduisait au seul nom, on renvoie None.
    """
    return split_role(role)[1]


def reliable_names(roles: Mapping[str, Optional[str]]) -> dict[str, str]:
    """{locuteur -> rôle} → {locuteur -> nom RETENU}, après garde-fous.

    Rejette tout si trop de locuteurs (sur-segmentation), et rejette tout nom
    attribué à PLUSIEURS locuteurs.
    """
    if len(roles) > MAX_SPEAKERS_FOR_NAMES:
        return {}
    found = {spk: name_from_role(role) for spk, role in roles.items()}
    counts = Counter(n for n in found.values() if n)
    return {spk: n for spk, n in found.items() if n and counts[n] == 1}


def sanitize_roles(roles: Mapping[str, Optional[str]]) -> dict[str, Optional[str]]:
    """{locuteur -> rôle} avec les noms NON retenus retirés.

    Appliqué avant écriture du `.md` : sinon un nom halluciné reste visible dans
    le transcript même si le registre de voix l'a rejeté (le garde-fou ne
    protégeait que la suggestion de nom, pas ce que l'utilisateur lit).
    """
    keep = reliable_names(roles)
    # Transcript entièrement suspect (plateau à interprètes, montage d'extraits) :
    # on ne se fie même plus au découpage nom/rôle, donc tout fragment portant une
    # majuscule saute. Perdre « Guest » est sans gravité ; laisser fuiter un nom
    # dans le registre global ne l'est pas.
    distrust_all = len(roles) > MAX_SPEAKERS_FOR_NAMES

    out: dict[str, Optional[str]] = {}
    for spk, role in roles.items():
        if keep.get(spk):
            out[spk] = role
            continue
        generic = strip_name(role)
        if distrust_all and generic and any(c.isupper() for c in generic):
            generic = None
        out[spk] = generic
    return out


# --- Liaisons « Speaker N (Nom) » dans un compte-rendu ------------------------

# L'étiquette elle-même : « Speaker 3 », « Locuteur 2 ».
_ETIQUETTE = r"(?:speakers?|locuteur\.?e?s?)\s*\d+"

# Les deux formes de LIAISON, et rien d'autre :
#   « Speaker 3 (Paul R. Bertin) »  →  on retire la parenthèse
#   « Paul R. Bertin (Speaker 3) »  →  on retire la parenthèse
#
# ⚠️ Version corrigée après un vrai CR. La première tentative
# cherchait à CAPTURER le nom, ce qui était une erreur de conception — deux
# défauts en sont sortis :
#
#   · `re.IGNORECASE`, nécessaire pour « Speaker »/« speaker », neutralisait
#     aussi `[A-ZÀ-Þ]` : l'exigence de majuscule était annulée et le motif
#     mordait sur les mots précédents (« tandis que Ségolène Royal ») ;
#   · un nom se terminant par autre chose qu'un caractère de mot échappait au
#     motif — « L'animateur du journal « C'est à vous » (Speaker 2) » a
#     SURVÉCU dans un CR réel, faux négatif bien plus grave.
#
# L'opération voulue n'a jamais été de reconnaître un nom : c'est de retirer la
# parenthèse qui accroche une étiquette. On ne capture donc plus rien — on
# supprime la parenthèse, quel que soit son voisinage.
_LIAISON_APRES = re.compile(rf"(?P<label>{_ETIQUETTE})\s*\([^)]{{1,60}}\)", re.IGNORECASE)
_LIAISON_AVANT = re.compile(rf"\s*\(\s*{_ETIQUETTE}\s*\)", re.IGNORECASE)

# Contexte repris dans l'avertissement — purement informatif, sans effet sur ce
# qui est retiré. Borné à quelques mots pour rester lisible.
_MOTS_AVANT = 4


def strip_speaker_bindings(text: str) -> tuple[str, list[str]]:
    """Retire les liaisons « Speaker N (Nom) » d'un compte-rendu.

    Motif du garde-fou — cas réel. Un CR a écrit
    « Speaker 10 (Léa Marchand/Iris) ». Le transcript le CONTREDIT : Speaker 10
    dit avoir auditionné pour le rôle de Iris, tandis que « Léa » y est citée
    par d'autres comme partant avec Paul, et la source apparie « Mike and
    Nora » d'un côté, « Léa and Paul » de l'autre — deux personnes
    distinctes, distinguables sans aucune connaissance extérieure.

    Aucun de nos garde-fous ne pouvait le voir : le vérificateur numérique ne
    regarde que les chiffres, et `sanitize_roles` ne s'applique qu'au `.md`.

    Ce qui est retiré n'est PAS le nom — les noms cités dans le contenu sont
    légitimes et utiles, et ils sont presque toujours réellement prononcés. C'est
    la LIAISON entre un nom et une étiquette de diarisation : une inférence que
    la source ne porte pas, et que nos étiquettes ne peuvent pas soutenir (elles
    sur-segmentent — sur ce fichier, 14 étiquettes, dont deux pour la même
    personne).

    Renvoie (texte nettoyé, liaisons retirées) pour pouvoir le signaler.
    """
    retirees: list[str] = []

    def _apres(m: "re.Match[str]") -> str:
        retirees.append(m.group(0).strip())
        return m.group("label")

    def _avant(m: "re.Match[str]") -> str:
        amont = text[: m.start()].split()[-_MOTS_AVANT:]
        retirees.append(f"{' '.join(amont)}{m.group(0).rstrip()}".strip())
        return ""

    text = _LIAISON_APRES.sub(_apres, text)
    text = _LIAISON_AVANT.sub(_avant, text)
    return text, retirees
