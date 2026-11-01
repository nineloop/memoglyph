"""Le filet STRUCTUREL : `secondary_evidence` omise, défaultée à `null`.

## Le défaut, mesuré sur le vrai serveur

Compte-rendu LOCAL, vrai uvicorn, vrai MLX : HTTP 503 après TROIS tentatives,
toujours `clés manquantes dans claim : ['secondary_evidence']`. La consigne
ciblée était bien câblée mais ne suffisait pas — graine fixe,
MLX l'honore, Qwen3-4B omet la clé trois fois à l'identique. Échec DÉTERMINISTE,
POINT FIXE.

## Ce que ce filet fait, et RIEN d'autre

`secondary_evidence` est la SEULE clé du contrat dont l'omission a un sens
unique : « pas de seconde preuve ». Et `null` est déjà la valeur que le contrat
donne à ce sens (voir `output_schema` et `valider_structure`). Écrire `null` là
où la clé manque rend donc EXPLICITE ce que l'omission voulait dire — no-op
sémantique, pas une invention.

Le contrat n'est PAS assoupli : `valider_structure` refuse toujours l'omission.
Ce filet vit AU-DESSUS d'elle, se compte à part (`PASS_NORMALIZED`) et se prouve
par empreinte. La contrepartie de cette pureté est ici : une preuve PRINCIPALE
absente n'est jamais défaultée — ce serait fabriquer une citation.
"""
from __future__ import annotations

import copy

import pytest

from core import report_citations

pytestmark = pytest.mark.unit


def _claim(**surcharges) -> dict:
    """Une affirmation à qui il manque EXACTEMENT `secondary_evidence`."""
    base = {"text": "Le lancement est validé.",
            "primary_evidence": {"timestamp": "00:00:04",
                                 "quote": "L'équipe valide le lancement mardi."}}
    base.update(surcharges)
    return base


def _payload(*claims: dict) -> dict:
    return {"sections": [{"question_id": 1, "title": "S",
                          "answer_status": "answered", "claims": list(claims)}]}


def test_défaute_uniquement_la_clé_secondaire_omise() -> None:
    """La clé apparaît, à `null`, et le reste de l'affirmation ne bouge pas."""
    resultat = report_citations.normaliser_preuve_secondaire(_payload(_claim()))

    assert resultat.appliquee is True
    assert resultat.affirmations == 1
    claim = resultat.charge["sections"][0]["claims"][0]
    assert claim["secondary_evidence"] is None
    assert set(claim) == {"text", "primary_evidence", "secondary_evidence"}
    # Le texte et la preuve principale sont INTACTS — on ajoute, on ne réécrit pas.
    assert claim["text"] == "Le lancement est validé."
    assert claim["primary_evidence"]["quote"] == "L'équipe valide le lancement mardi."


def test_ne_MUTE_pas_l_entrée() -> None:
    """La charge d'origine reste disponible pour la télémétrie et le débogage."""
    payload = _payload(_claim())
    avant = copy.deepcopy(payload)

    report_citations.normaliser_preuve_secondaire(payload)

    assert payload == avant, "l'entrée ne doit jamais être modifiée en place"


def test_une_affirmation_COMPLÈTE_n_est_pas_touchée() -> None:
    """`secondary_evidence` déjà présente — même à `null` — n'est pas un défaut."""
    for deja in (None, {"timestamp": "00:00:12", "quote": "Non, le budget."}):
        resultat = report_citations.normaliser_preuve_secondaire(
            _payload(_claim(secondary_evidence=deja)))
        assert resultat.appliquee is False
        assert resultat.affirmations == 0
        assert resultat.charge is None


def test_une_FONDATION_absente_n_est_JAMAIS_défaultée() -> None:
    """Sans `primary_evidence`, l'ensemble des clés n'est pas {text, primary}.

    C'est la garantie qui compte : le filet ne peut pas transformer une
    affirmation sans preuve principale en affirmation acceptable — il n'a pas
    de valeur par défaut honnête pour une fondation.
    """
    sans_primaire = {"text": "Affirmation en l'air."}
    resultat = report_citations.normaliser_preuve_secondaire(
        _payload(sans_primaire))

    assert resultat.appliquee is False
    assert resultat.affirmations == 0


def test_une_clé_EN_TROP_n_est_pas_touchée() -> None:
    """Une clé inconnue trahit une AUTRE faute : hors du ressort de ce filet."""
    avec_extra = _claim()
    avec_extra["evidence"] = [{"timestamp": "00:00:04", "quote": "x"}]
    resultat = report_citations.normaliser_preuve_secondaire(
        _payload(avec_extra))

    assert resultat.appliquee is False


def test_défaute_PLUSIEURS_affirmations_en_une_passe() -> None:
    """Le modèle omet souvent la clé partout : une passe les traite toutes."""
    payload = {"sections": [
        {"question_id": 1, "title": "A", "answer_status": "answered",
         "claims": [_claim(), _claim(text="Deuxième.")]},
        {"question_id": 2, "title": "B", "answer_status": "answered",
         "claims": [_claim(text="Troisième.")]},
    ]}
    resultat = report_citations.normaliser_preuve_secondaire(payload)

    assert resultat.appliquee is True
    assert resultat.affirmations == 3
    for section in resultat.charge["sections"]:
        for claim in section["claims"]:
            assert claim["secondary_evidence"] is None


def test_empreinte_avant_et_après_PROUVENT_le_changement() -> None:
    """Sans empreinte, un défaut serait indistinguable d'un succès natif."""
    resultat = report_citations.normaliser_preuve_secondaire(_payload(_claim()))

    assert resultat.sha_avant and resultat.sha_apres
    assert resultat.sha_avant != resultat.sha_apres
    journal = resultat.journal()
    assert journal == {
        "applied": True,
        "kind": "secondary_evidence_defaulted",
        "claims": 1,
        "original_sha": resultat.sha_avant,
        "normalized_sha": resultat.sha_apres,
    }


def test_un_journal_de_NON_application_ne_ment_pas() -> None:
    """Quand rien n'est défaulté, le journal le dit sans empreinte trompeuse."""
    resultat = report_citations.normaliser_preuve_secondaire(
        _payload(_claim(secondary_evidence=None)))

    assert resultat.journal() == {
        "applied": False, "kind": None, "claims": None,
        "original_sha": None, "normalized_sha": None,
    }


@pytest.mark.parametrize("payload", [
    None, {}, {"sections": "pas une liste"}, {"autre": 1},
    {"sections": [{"claims": "pas une liste"}]},
    {"sections": [{}]},
])
def test_une_charge_hors_forme_est_rendue_intacte(payload) -> None:
    """Le filet ne court qu'après un refus PREUVE_SECONDAIRE_ABSENTE, mais il

    reste DÉFENSIF : une forme inattendue ne le fait pas planter, elle le fait
    s'abstenir.
    """
    resultat = report_citations.normaliser_preuve_secondaire(payload)
    assert resultat.appliquee is False
