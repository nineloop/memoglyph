"""Le filet ne répare qu'une faute de balance non ambiguë. Tout le reste : refus.

## La règle que ces tests fixent

    Quand le modèle produit un document COMPLET, sémantiquement exploitable,
    mais commet une UNIQUE faute de balance structurelle NON AMBIGUË,
    Memoglyph répare la syntaxe puis applique EXACTEMENT les mêmes validations
    que pour toute autre sortie.

Chaque adjectif de cette phrase porte un refus, et chaque refus a son test.

## Ce que le filet doit tenir

La plupart des sorties invalides réelles s'écartent d'un document complet d'un seul
symbole ; celles qui en demandent deux sont refusées. Une enveloppe Markdown
résiduelle n'est pas une faute de structure : le transport la déballe en amont
(`test_enveloppe_markdown.py`), et les deux règles restent séparées.

Ce qui compte le plus : une sortie déjà valide n'est jamais modifiée.
"""
from __future__ import annotations

import json

import pytest

from core import reparation_json, report_citations

TRANSCRIPT = """---
title: Réunion
---

## Transcript

**Speaker 1** [00:00:04] L'équipe valide le lancement mardi.

**Speaker 2** [00:00:12] Non, le budget reste à confirmer.
"""


def _claim(texte: str, citation: str = "L'équipe valide le lancement mardi.",
           horodatage: str = "00:00:04") -> dict:
    return {"text": texte,
            "primary_evidence": {"timestamp": horodatage, "quote": citation},
            "secondary_evidence": None}


def _section(question_id: int, titre: str, claims: list[dict]) -> dict:
    return {"question_id": question_id, "title": titre,
            "answer_status": "answered", "claims": claims}


def _payload(n: int = 2) -> dict:
    return {"sections": [
        _section(i, f"Question {i}", [_claim(f"Affirmation {i}.")])
        for i in range(1, n + 1)]}


def _valideur(attendues: int, transcript: str = TRANSCRIPT):
    def valider(charge: object) -> object:
        return report_citations.valider_structure(
            charge, transcript, expected_sections=attendues)
    return valider


def _brut(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 1. Ce qui est déjà valide n'est JAMAIS touché
# ---------------------------------------------------------------------------


def test_un_json_deja_valide_n_est_pas_modifie() -> None:
    """PASS_DIRECT : le filet se retire, et le dit.

    C'est la garantie la plus importante du module. Un réparateur qui
    « améliorerait » au passage une sortie correcte introduirait une variation
    silencieuse dans un pipeline dont toute la valeur est d'être reproductible.
    """
    brut = _brut(_payload())
    r = reparation_json.reparer(brut, _valideur(2), finish_reason="stop")

    assert not r.reparee
    assert r.motif == "la sortie est déjà valide"
    assert r.texte == "", "aucun texte de remplacement ne doit être proposé"
    assert r.operation == ""
    assert r.sha_avant == reparation_json.empreinte(brut)


# ---------------------------------------------------------------------------
# 2. Les deux formes de faute réellement observées
# ---------------------------------------------------------------------------


def test_un_crochet_fermant_en_trop_est_supprime() -> None:
    """PASS_REPAIRED — la forme majoritaire du corpus (interview sportive, conférence sur le climat)."""
    valide = _brut(_payload())
    coupe = valide.index("]}")                      # fin de la 1re section
    brut = valide[:coupe] + "]" + valide[coupe:]    # un `]` de trop

    r = reparation_json.reparer(brut, _valideur(2), finish_reason="stop")

    assert r.reparee
    assert r.operation.startswith("suppression_")
    assert json.loads(r.texte) == _payload()
    assert r.journal()["operation"] == "delete_closing_bracket"
    assert r.journal()["original_error"], "l'erreur du parseur est conservée"
    assert r.journal()["original_sha"] != r.journal()["repaired_sha"]


def test_une_accolade_fermante_manquante_est_inseree() -> None:
    """PASS_REPAIRED — la forme d'une sortie réelle tronquée et de Granite 8B."""
    valide = _brut(_payload())
    brut = valide.replace('"secondary_evidence": null}', '"secondary_evidence": null',
                          1)                        # une `}` en moins

    r = reparation_json.reparer(brut, _valideur(2), finish_reason="stop")

    assert r.reparee
    assert r.operation.startswith("insertion_")
    assert json.loads(r.texte) == _payload()
    assert r.journal()["operation"] == "insert_closing_brace"


def test_la_charge_reparee_est_rendue_prete_a_valider() -> None:
    """Le réparateur rend un OBJET, et le pipeline normal reprend ensuite.

    Il ne rend pas un document : il ne sélectionne pas, ne numérote pas,
    n'audite pas. Règle : ne pas mélanger réparation et sélection.
    """
    valide = _brut(_payload())
    brut = valide[:-1]                              # `}` finale manquante

    r = reparation_json.reparer(brut, _valideur(2), finish_reason="stop")

    assert r.reparee
    assert r.charge == _payload()
    rendu = report_citations.render_structured(
        r.charge, TRANSCRIPT, expected_sections=2, language="fr")
    assert "Affirmation 1." in rendu


# ---------------------------------------------------------------------------
# 3. Les refus — un par adjectif de la règle
# ---------------------------------------------------------------------------


def test_deux_operations_necessaires_sont_refusees() -> None:
    """Le cas de l'interview sportive au premier tirage : accolades 52/53 ET crochets 6/7.

    Au-delà d'une opération on ne répare plus, on reconstruit — et
    reconstruire, c'est inventer.
    """
    valide = _brut(_payload())
    coupe = valide.index("]}")
    brut = valide[:coupe] + "]" + valide[coupe:] + "}"   # un `]` ET une `}` en trop

    r = reparation_json.reparer(brut, _valideur(2), finish_reason="stop")

    assert not r.reparee
    assert r.motif == "aucune opération unique ne répare"
    assert r.texte == ""


def test_plusieurs_reparations_possibles_sont_refusees() -> None:
    """Deux corrections d'UNE opération, deux documents DIFFÉRENTS → aucune.

    `[[1], [2]]]` porte un `]` de trop, et deux suppressions le rendent
    parsable : retirer le dernier donne `[[1], [2]]`, retirer celui qui suit
    le `1` donne `[[1, [2]]]`. Deux documents également plausibles — choisir
    serait décider à la place du modèle ce qu'il voulait écrire.

    Le validateur est ici volontairement PERMISSIF. Avec le contrat réel, une
    telle ambiguïté est presque impossible à construire : `valider_structure`
    exige un nombre exact de sections et un jeu de clés fermé, si bien qu'un
    seul des deux documents survit d'ordinaire. Cette rigueur est une bonne
    nouvelle — mais elle ne doit pas laisser la garde d'ambiguïté sans test,
    car c'est elle qui protégerait un contrat futur plus souple.
    """
    r = reparation_json.reparer("[[1], [2]]]", lambda charge: charge,
                                finish_reason="stop")

    assert not r.reparee, "une ambiguïté ne se tranche pas au hasard"
    assert r.resultats_distincts >= 2
    assert "ambigu" in r.motif


def test_une_modification_dans_une_chaine_est_impossible() -> None:
    """Aucun contenu textuel n'est créé, supprimé ni réécrit. Par CONSTRUCTION.

    Une citation qui porte une accolade ne doit jamais devenir un candidat :
    la déplacer d'un caractère rendrait le compte-rendu faux sans rien casser
    de visible. Le masque de chaînes l'interdit, et la comparaison des
    littéraux le vérifie une seconde fois.
    """
    payload = {"sections": [_section(
        1, "Question 1",
        [{"text": "Le format est }] selon lui.",
          "primary_evidence": {"timestamp": "00:00:04",
                               "quote": "L'équipe valide le lancement mardi."},
          "secondary_evidence": None}])]}
    valide = _brut(payload)
    brut = valide[:-1]                              # `}` finale manquante

    r = reparation_json.reparer(brut, _valideur(1), finish_reason="stop")

    assert r.reparee
    assert json.loads(r.texte)["sections"][0]["claims"][0]["text"] == (
        "Le format est }] selon lui."), "la citation est intacte, symboles compris"
    assert (reparation_json._litteraux(r.texte)
            == reparation_json._litteraux(brut)), "aucun littéral n'a bougé"


def test_une_section_manquante_apres_reparation_est_refusee() -> None:
    """Parsable ≠ conforme. Le nombre de sections vient du GABARIT.

    Raison : si le réparateur déduisait le nombre
    attendu de la sortie, un document ayant oublié sa dernière section serait
    jugé conforme à ses propres manques.
    """
    brut = _brut(_payload(2))[:-1] + "]"            # déséquilibre réparable…

    assert not reparation_json.reparer(
        brut, _valideur(3), finish_reason="stop").reparee, (
        "…mais le gabarit en attend TROIS")

    # Le CONTRASTE fait la démonstration : le refus vient bien du nombre
    # attendu, et non d'un texte que le filet n'aurait de toute façon pas su
    # réparer. Sans cette seconde assertion, le test passerait tout aussi bien
    # si la réparation était cassée.
    assert reparation_json.reparer(
        brut, _valideur(2), finish_reason="stop").reparee, (
        "le même texte se répare quand le gabarit en attend DEUX")


def test_un_json_reparable_mais_hors_contrat_est_refuse() -> None:
    """Une clé inconnue survit au parseur, pas à la validation métier."""
    payload = _payload(1)
    payload["sections"][0]["claims"][0]["evidence"] = []   # l'ancienne liste ouverte
    brut = _brut(payload)[:-1]

    r = reparation_json.reparer(brut, _valideur(1), finish_reason="stop")

    assert not r.reparee
    assert r.motif == "aucune opération unique ne répare"

    # Même contraste : le texte EST rééquilibrable, c'est le contrat qui le
    # refuse. Un validateur permissif l'accepte, le nôtre non.
    assert reparation_json.reparer(
        brut, lambda charge: charge, finish_reason="stop").reparee


# ---------------------------------------------------------------------------
# 4. Une sortie incomplète ne se répare pas — elle se redemande
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("motif", ["length", None, "content_filter"])
def test_sans_arret_normal_aucune_reparation_n_est_tentee(motif) -> None:
    """`finish_reason` doit valoir `stop`. `None` compris — on n'invente pas.

    Un document coupé au milieu n'est pas déséquilibré, il est INCOMPLET : le
    compléter reviendrait à écrire sa fin. Et un moteur qui n'expose pas le
    motif d'arrêt ne permet pas d'affirmer que la sortie était complète, donc
    le filet s'abstient plutôt que de parier.
    """
    brut = _brut(_payload())[:-1]                   # réparable, si on y avait droit
    r = reparation_json.reparer(brut, _valideur(2), finish_reason=motif)

    assert not r.reparee
    assert "finish_reason" in r.motif
    assert r.erreur_origine == "", "on n'a même pas tenté de parser"


def test_une_sortie_tronquee_en_pleine_chaine_ne_se_repare_pas() -> None:
    """Même avec `stop`, une chaîne inachevée n'a aucune réparation d'un symbole.

    Aucun `]` ni `}` ajouté HORS chaîne ne peut fermer une chaîne ouverte : le
    refus tombe de lui-même, sans règle spéciale. Ce test le fige, parce que
    c'est la propriété qui rend le masque suffisant.
    """
    brut = _brut(_payload())[:-40]                  # coupé au milieu d'une citation

    r = reparation_json.reparer(brut, _valideur(2), finish_reason="stop")

    assert not r.reparee
    assert r.motif == "aucune opération unique ne répare"
