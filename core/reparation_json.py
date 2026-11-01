"""Réparer UNE faute de balance sur une sortie COMPLÈTE. Rien d'autre.

Ce module formalise une règle, et une seule :

    Quand le modèle produit un document complet, sémantiquement exploitable,
    mais commet une unique faute de balance structurelle non ambiguë,
    Memoglyph répare la syntaxe puis applique EXACTEMENT les mêmes validations
    que pour toute autre sortie.

## Ce qui autorise ce filet

Sur les sorties invalides observées, de plusieurs modèles et tailles, la faute
dominante est un déséquilibre de symboles structurants sur une sortie dont
`finish_reason` vaut `stop`. Jamais une chaîne non fermée, jamais un
échappement, jamais un type.

Ni le volume, ni le nombre d'affirmations, ni le nombre de transitions ne
prédisent quand la faute arrive. Face à une erreur qu'on ne sait pas prévenir,
qu'on sait localiser, et qui coûte une génération entière — des milliers de
jetons et plusieurs minutes pour ne rien rendre — réparer cesse d'être un
pis-aller.

## Ce qu'il répare, et ce qui n'arrive plus jusqu'ici

Un JSON parfaitement équilibré suivi d'un backtick résiduel n'arrive plus
jusqu'ici : `llm_base.retirer_enveloppe_markdown` le déballe dans le transport,
et il parse ensuite du premier coup. C'est un défaut de TRANSPORT, pas de
structure, et il se traite là où l'on déballe le transport.

Une telle sortie compte donc PASS_DIRECT, et surtout **jamais** PASS_REPAIRED :
aucune faute de structure du modèle n'a été corrigée.

Le critère est net, et c'est ce qui rend ce filet prévisible plutôt que malin :

    écart de 1 symbole  →  réparé, sans exception
    écart de 2 symboles →  refusé, sans exception

Le nombre de sections attendu vient du GABARIT, jamais de la sortie elle-même :
le déduire du plus grand `question_id` produit jugerait conforme à ses propres
manques une sortie qui a oublié sa dernière section.

## Ce qu'il s'autorise, et RIEN d'autre

Une seule opération, prise dans trois formes : insérer `]` ou `}`, supprimer un
`]` ou `}`, remplacer l'un par l'autre.

Les positions considérées sont exclusivement HORS CHAÎNE. C'est ce qui garantit
la règle qui compte le plus : **aucun contenu textuel n'est créé, supprimé ni
réécrit**. Une citation ne peut pas être touchée par construction, et le
témoin le vérifie EN PLUS par comparaison des littéraux.

## Ce qu'il refuse, et pourquoi chaque refus compte

- **`finish_reason` autre que `stop`** — une sortie coupée est incomplète, pas
  déséquilibrée. La compléter serait inventer la fin du document.
- **plusieurs modifications nécessaires** — au-delà d'une, on ne répare plus, on
  reconstruit. Une sortie qui en demande deux est refusée.
- **plusieurs résultats possibles** — si deux réparations distinctes rendent des
  documents différents, aucune n'est « la » réparation. Choisir serait inventer.
- **la validation métier échoue** — un JSON parsable hors contrat n'est pas
  réparé, il est seulement lisible.

## Ce qu'il ne fait pas, et ne doit jamais faire

Il ne SÉLECTIONNE pas. Le critère d'acceptation est
`report_citations.valider_structure`, qui accepte ou refuse sans rien choisir —
et non le rendu complet, qui porte la sélection. Le réparateur ne décide jamais
qu'une affirmation est bonne ou mauvaise.

Il ne corrige pas la cause, et il ne doit pas dispenser de la chercher. D'où le
marquage obligatoire — un document réparé le DIT — et d'où la distinction que
la mesure doit tenir. Un SECOND filet déterministe l'a rejoint depuis — le défaut
d'une clé `secondary_evidence` omise (`PASS_NORMALIZED`, voir
`report_citations.normaliser_preuve_secondaire`) — et il entre dans la même
lecture de coût sans jamais compter comme natif :

    succès SANS GÉNÉRATION SUPPLÉMENTAIRE = PASS_DIRECT + PASS_REPAIRED + PASS_NORMALIZED
    succès NATIF DU MODÈLE                = PASS_DIRECT seulement

Confondre le natif et le reste masquerait une dégradation du modèle derrière un
filet qui travaille de plus en plus. Et `PASS_REPAIRED` (une balance JSON) ne se
confond jamais avec `PASS_NORMALIZED` (une clé de contrat omise) : deux fautes
différentes, deux filets, deux comptes.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Callable, Optional

# Les cinq issues d'une génération, distinguées pour que ni les mesures ni la
# télémétrie ne confondent « ça marche » et « ça a fini par marcher ».
#
# Le vocabulaire des issues vit ICI — c'est le seul point où mesures, API et tests
# le lisent. TROIS façons distinctes de « réussir malgré un modèle fautif »
# l'alimentent, et elles NE DOIVENT JAMAIS être confondues :
#   · PASS_REPAIRED   ← ce module, une faute de BALANCE sur du JSON malformé.
#                       Déterministe, AUCUNE génération supplémentaire ;
#   · PASS_NORMALIZED ← `report_citations.normaliser_preuve_secondaire`, le
#                       DÉFAUT d'une clé omise non ambiguë sur du JSON bien
#                       formé mais hors contrat. Déterministe, AUCUNE génération
#                       supplémentaire non plus ;
#   · PASS_RETRY      ← ni l'un ni l'autre : il a fallu REDEMANDER au modèle,
#                       une génération entière de plus. C'est le seul des trois
#                       qui coûte un nouvel appel.
# Deux fautes différentes (balance vs clé), deux filets déterministes, et à
# part une reprise coûteuse. Les garder séparés, c'est pouvoir dire lequel
# travaille — donc voir une dérive du modèle plutôt que la masquer derrière
# « ça passe ».
PASS_DIRECT = "PASS_DIRECT"          # valide du premier coup, rien touché
PASS_REPAIRED = "PASS_REPAIRED"      # une opération sur un symbole a suffi
PASS_NORMALIZED = "PASS_NORMALIZED"  # une clé omise non ambiguë défaultée à `null`
PASS_RETRY = "PASS_RETRY"            # il a fallu redemander au modèle
FAIL = "FAIL"                        # rien n'a abouti

FERMANTS = "]}"

# Le vocabulaire du journal est explicite : « suppression_] » ne se lit pas dans
# une alerte à trois heures du matin.
_NOMS = {
    "insertion_]": "insert_closing_bracket",
    "insertion_}": "insert_closing_brace",
    "suppression_]": "delete_closing_bracket",
    "suppression_}": "delete_closing_brace",
    "remplacement_]_}": "replace_bracket_with_brace",
    "remplacement_}_]": "replace_brace_with_bracket",
}


def empreinte(texte: str) -> str:
    """SHA-256 tronqué. Sert à PROUVER ce qui a été réparé, et à partir de quoi."""
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()[:16]


@dataclass
class Reparation:
    """Le compte rendu d'une tentative. Jamais un simple booléen.

    Un `False` nu ne dirait pas POURQUOI, et le motif de refus est précisément
    ce qui permettra de savoir si le filet est trop strict ou pas assez — donc
    de le régler sur des mesures plutôt que sur une impression.
    """

    reparee: bool
    motif: str = ""
    operation: str = ""
    position: Optional[int] = None
    erreur_origine: str = ""
    sha_avant: str = ""
    sha_apres: str = ""
    texte: str = ""
    charge: object = None
    resultats_distincts: int = 0

    def journal(self) -> dict:
        """Le détail d'exécution, tel qu'il part en provenance et en télémétrie.

        Conserve OBLIGATOIREMENT l'empreinte avant et
        après, le type et la position de la réparation, et l'erreur du parseur
        d'origine. Sans quoi une réparation devient indistinguable d'un succès.
        """
        return {
            "applied": self.reparee,
            "operation": _NOMS.get(self.operation, self.operation) or None,
            "position": self.position,
            "original_error": self.erreur_origine or None,
            "original_sha": self.sha_avant or None,
            "repaired_sha": self.sha_apres or None,
            "refused": None if self.reparee else (self.motif or None),
        }


def _masque_chaines(texte: str) -> list[bool]:
    """`True` là où le caractère est DANS une chaîne JSON, guillemets compris.

    C'est la garantie STRUCTURELLE de « aucun contenu textuel touché » : on ne
    considère jamais une position marquée `True`. Le guillemet lui-même est
    marqué — le déplacer changerait les frontières d'une citation.
    """
    dedans, chaine, echap = [], False, False
    for ch in texte:
        if echap:
            dedans.append(True)
            echap = False
            continue
        if chaine and ch == "\\":
            dedans.append(True)
            echap = True
            continue
        if ch == '"':
            dedans.append(True)
            chaine = not chaine
            continue
        dedans.append(chaine)
    return dedans


def _litteraux(texte: str) -> list[str]:
    """Les chaînes du document, dans l'ordre. La ceinture par-dessus la bretelle.

    Le masque garantit déjà qu'aucune position en chaîne n'est modifiée. Cette
    comparaison le VÉRIFIE, et elle vaut d'exister parce que la règle qu'elle
    protège est la plus importante du lot : une citation qui bougerait d'un
    caractère rendrait le compte-rendu faux sans rien casser de visible.
    """
    sorties, courant, chaine, echap = [], [], False, False
    for ch in texte:
        if echap:
            courant.append(ch)
            echap = False
            continue
        if chaine and ch == "\\":
            courant.append(ch)
            echap = True
            continue
        if ch == '"':
            if chaine:
                sorties.append("".join(courant))
                courant = []
            chaine = not chaine
            continue
        if chaine:
            courant.append(ch)
    return sorties


def _candidats(texte: str) -> list[tuple[str, int, str]]:
    """Toutes les réparations d'UNE opération, hors chaîne.

    Rend `(opération, position, texte proposé)`. L'énumération est exhaustive à
    dessein : c'est elle qui permet de DÉTECTER l'ambiguïté. Un réparateur qui
    ne testerait que la première correction plausible ne saurait jamais qu'une
    seconde existait.
    """
    masque = _masque_chaines(texte)
    propositions: list[tuple[str, int, str]] = []
    for i in range(len(texte) + 1):
        if i < len(texte) and masque[i]:
            continue
        for symbole in FERMANTS:
            propositions.append((f"insertion_{symbole}", i,
                                 texte[:i] + symbole + texte[i:]))
    for i, ch in enumerate(texte):
        if masque[i] or ch not in FERMANTS:
            continue
        propositions.append((f"suppression_{ch}", i, texte[:i] + texte[i + 1:]))
        autre = "}" if ch == "]" else "]"
        propositions.append((f"remplacement_{ch}_{autre}", i,
                             texte[:i] + autre + texte[i + 1:]))
    return propositions


def reparer(
    brut: str,
    valider: Callable[[object], object],
    *,
    finish_reason: Optional[str] = None,
) -> Reparation:
    """UNE opération, UN résultat, et le contrat métier respecté. Sinon, refus.

    `valider` reçoit la charge JSON et lève si elle est hors contrat. C'est une
    fonction, et non un import de `report_citations`, pour deux raisons : ce
    module reste sans dépendance métier, et l'appelant reste MAÎTRE de ce que
    « valide » veut dire — en particulier du nombre de sections attendues, qui
    vient du gabarit et jamais de la sortie elle-même.

    `finish_reason` doit valoir `stop`. `None` est refusé : on n'affirme pas
    qu'une sortie est complète parce qu'on ignore si elle l'est.
    """
    sha = empreinte(brut)

    if finish_reason != "stop":
        return Reparation(
            False,
            motif=f"sortie non terminée par le modèle (finish_reason="
                  f"{finish_reason!r}) — une troncature ne se répare pas",
            sha_avant=sha)

    try:
        json.loads(brut)
        return Reparation(False, motif="la sortie est déjà valide", sha_avant=sha)
    except json.JSONDecodeError as exc:
        erreur = f"{exc.msg} @{exc.pos}"

    litteraux_origine = _litteraux(brut)
    # Clé = document CANONIQUE. Deux opérations différentes qui rendent le même
    # document ne sont pas une ambiguïté : c'est la même réparation, écrite deux
    # fois. Ce sont les RÉSULTATS distincts qui disqualifient.
    retenus: dict[str, tuple[str, int, str, object]] = {}

    for operation, position, propose in _candidats(brut):
        try:
            charge = json.loads(propose)
        except json.JSONDecodeError:
            continue
        if _litteraux(propose) != litteraux_origine:
            continue                       # aurait touché du contenu : écarté
        try:
            valider(charge)
        except Exception:  # noqa: BLE001 — tout refus métier disqualifie
            continue
        cle = json.dumps(charge, sort_keys=True, ensure_ascii=False)
        retenus.setdefault(cle, (operation, position, propose, charge))

    if not retenus:
        return Reparation(False, motif="aucune opération unique ne répare",
                          erreur_origine=erreur, sha_avant=sha)
    if len(retenus) > 1:
        return Reparation(
            False, motif=f"{len(retenus)} résultats distincts — ambigu",
            erreur_origine=erreur, sha_avant=sha,
            resultats_distincts=len(retenus))

    operation, position, propose, charge = next(iter(retenus.values()))
    return Reparation(
        True, operation=operation, position=position, erreur_origine=erreur,
        sha_avant=sha, sha_apres=empreinte(propose), texte=propose,
        charge=charge, resultats_distincts=1)
