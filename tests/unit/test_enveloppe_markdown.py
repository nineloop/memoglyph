"""Le déballage retire l'ENVELOPPE de transport. Jamais un caractère du document.

## La règle que ces tests fixent

    Une clôture Markdown résiduelle n'est retirée que si le RESTE est un
    document JSON complet. Sinon, le texte ressort intact.

Ce critère n'est pas une précaution parmi d'autres : c'est la PREUVE. Si
`json.loads` accepte le reste, tout caractère retiré se situe strictement après
la fin d'un document complet, donc hors de tout contenu — par construction, et
non par intention. Chaque refus ci-dessous découle de cette seule phrase.

## Le cas observé qui a rendu cette règle nécessaire

Granite 4.1 3B sur une conférence sur le climat :

    745 caractères, dont les 744 premiers sont un JSON valide à 6 sections
    le 745e est un unique backtick
    accolades 7/7 et crochets 7/7 hors chaîne — AUCUN déséquilibre

`json.loads` lève `Extra data @744`. Le nettoyage historique ne s'appliquant
qu'aux sorties COMMENÇANT par ```, il ne retirait rien : le modèle n'avait émis
que la clôture finale, et partielle.

Ce n'est pas une faute de balance, donc `core/reparation_json.py` le refuse à
raison — sa docstring le dit explicitement. C'est un défaut de TRANSPORT, et il
se traite au déballage du transport.

## La frontière avec le filet de balance, et pourquoi elle tient

Des sorties réelles traînent elles aussi
quelque chose après un document complet — mais un `]` ou un `]}`. Ce sont des
symboles STRUCTURANTS, leur retrait est une réparation de balance, et il
appartient à `reparation_json`. Le déballage doit les REFUSER, sans quoi les
deux règles se recouvriraient. C'est l'objet de
`test_un_symbole_structurant_residuel_reste_au_filet_de_balance`.
"""
from __future__ import annotations

import json

import pytest

from core import llm_base, reparation_json, report
from core.llm_base import retirer_enveloppe_markdown

pytestmark = pytest.mark.unit

# Écrit ainsi, et non en littéral : trois backticks dans une source Python de ce
# dépôt finissent tôt ou tard dans un bloc Markdown de documentation.
BT = chr(96)
CLOTURE = BT * 3

DOCUMENT = '{"sections":[{"question_id":1,"claims":[]}]}'


# --------------------------------------------------------------------------
# Ce qui est retiré — une forme observée par test
# --------------------------------------------------------------------------

def test_le_cas_granite_reel_un_backtick_apres_un_document_complet():
    """La forme observée, reproduite à l'identique."""
    sortie = DOCUMENT + BT

    nettoye = retirer_enveloppe_markdown(sortie)

    assert nettoye == DOCUMENT
    assert json.loads(nettoye)["sections"][0]["question_id"] == 1


@pytest.mark.parametrize("n", [1, 2, 3])
def test_une_cloture_partielle_ou_complete_est_retiree(n):
    """Un modèle qui referme mal en écrit un ou deux ; correctement, trois."""
    assert retirer_enveloppe_markdown(DOCUMENT + BT * n) == DOCUMENT


def test_enveloppe_ouvrante_complete_comportement_historique_inchange():
    """La forme que le nettoyage traitait déjà. Aucune régression permise."""
    sortie = f"{CLOTURE}json\n{DOCUMENT}\n{CLOTURE}"

    assert retirer_enveloppe_markdown(sortie) == DOCUMENT


def test_enveloppe_ouverte_puis_refermee_par_un_seul_backtick():
    """Le cas que l'ancienne règle laissait passer À MOITIÉ.

    `rsplit("```", 1)` ne trouvait pas de clôture complète et rendait donc le
    texte entier, backtick résiduel compris. C'est ce qui impose que la règle 2
    s'applique APRÈS la règle 1, et non à sa place.
    """
    sortie = f"{CLOTURE}json\n{DOCUMENT}\n{BT}"

    assert retirer_enveloppe_markdown(sortie) == DOCUMENT


def test_un_saut_de_ligne_entre_le_document_et_la_cloture():
    assert retirer_enveloppe_markdown(f"{DOCUMENT}\n{BT}") == DOCUMENT


# --------------------------------------------------------------------------
# Ce qui est REFUSÉ — chaque refus protège le contenu du document
# --------------------------------------------------------------------------

def test_un_backtick_du_CONTENU_n_est_jamais_touche():
    """Le refus qui compte le plus : le document finit par un backtick CITÉ."""
    sortie = '{"sections":[{"citation":"il a dit ' + BT + '"}]}'

    nettoye = retirer_enveloppe_markdown(sortie)

    assert nettoye == sortie
    assert json.loads(nettoye)["sections"][0]["citation"].endswith(BT)


def test_une_sortie_tronquee_dans_une_citation_contenant_un_backtick():
    """Un `rstrip` naïf mutilerait ce texte. Le critère de parse le protège.

    La chaîne n'est pas fermée : le backtick final est du CONTENU d'une
    citation coupée, pas une clôture. Le reste ne parse pas, donc on ne
    retire rien, et le texte part au filet de balance tel que le modèle
    l'a écrit.
    """
    sortie = '{"sections":[{"citation":"il a dit ' + BT

    assert retirer_enveloppe_markdown(sortie) == sortie


def test_un_symbole_structurant_residuel_reste_au_filet_de_balance():
    """Sortie réelle : un `]` traîne après le document. Ce n'est PAS du transport.

    Le retirer est une réparation de BALANCE, que `core/reparation_json.py`
    sait faire et sait prouver. Le déballage n'y touche pas : fusionner les
    deux règles brouillerait le périmètre de chacune.
    """
    for residu in ("]", "]}"):
        sortie = DOCUMENT + residu
        assert retirer_enveloppe_markdown(sortie) == sortie


def test_plus_de_trois_backticks_n_est_pas_une_cloture():
    """On ne retire pas ce qu'on ne sait pas nommer."""
    sortie = DOCUMENT + BT * 4

    assert retirer_enveloppe_markdown(sortie) == sortie


def test_un_reste_qui_ne_parse_pas_laisse_le_texte_intact():
    """Document incomplet ET backtick : le déballage s'abstient."""
    sortie = '{"sections":[{"question_id":1' + BT

    assert retirer_enveloppe_markdown(sortie) == sortie


def test_deux_documents_concatenes_ne_sont_pas_un_document_complet():
    sortie = DOCUMENT + DOCUMENT + BT

    assert retirer_enveloppe_markdown(sortie) == sortie


def test_la_limite_assumee_cloture_residuelle_ET_faute_de_balance():
    """Les deux à la fois : aucun des deux filets ne récupère, et c'est dit.

    Le reste ne parse pas — il manque une accolade — donc le déballage
    s'abstient et rend le texte INTACT. C'est ce que la docstring documente
    comme limite tenue plutôt que contournée : retirer la clôture avant de
    savoir si le reste est un document reviendrait à renoncer à la preuve qui
    autorise le retrait.
    """
    sortie = '{"sections":[{"question_id":1,"claims":[]}]' + BT  # accolade manquante

    assert retirer_enveloppe_markdown(sortie) == sortie
    assert llm_base.enveloppe_observee() == ""


# --------------------------------------------------------------------------
# Le relevé — un déballage réussi ne doit plus être muet
# --------------------------------------------------------------------------

def test_un_deballage_est_RELEVE_et_nomme():
    llm_base.oublier_enveloppe_observee()

    retirer_enveloppe_markdown(DOCUMENT + BT)

    assert llm_base.enveloppe_observee() == llm_base.CLOTURE_RESIDUELLE


def test_une_enveloppe_ouvrante_est_relevee_sous_son_propre_nom():
    llm_base.oublier_enveloppe_observee()

    retirer_enveloppe_markdown(f"{CLOTURE}json\n{DOCUMENT}\n{CLOTURE}")

    assert llm_base.enveloppe_observee() == llm_base.OUVRANTE


def test_les_deux_formes_a_la_fois_sont_relevees_toutes_les_deux():
    llm_base.oublier_enveloppe_observee()

    retirer_enveloppe_markdown(f"{CLOTURE}json\n{DOCUMENT}\n{BT}")

    assert llm_base.enveloppe_observee() == (
        f"{llm_base.OUVRANTE}+{llm_base.CLOTURE_RESIDUELLE}")


def test_une_sortie_sans_enveloppe_ne_releve_RIEN():
    """Le relevé doit rester vide, sinon le signal perd tout son sens."""
    llm_base.oublier_enveloppe_observee()

    retirer_enveloppe_markdown(DOCUMENT)

    assert llm_base.enveloppe_observee() == ""


def test_le_releve_d_une_sortie_precedente_ne_traine_pas():
    """Un modèle qui a CESSÉ d'envelopper ne doit pas paraître continuer."""
    retirer_enveloppe_markdown(DOCUMENT + BT)
    assert llm_base.enveloppe_observee() == llm_base.CLOTURE_RESIDUELLE

    retirer_enveloppe_markdown(DOCUMENT)

    assert llm_base.enveloppe_observee() == ""


# --------------------------------------------------------------------------
# Le journal de PRODUCTION — un PASS_DIRECT déballé ne doit plus être muet
# --------------------------------------------------------------------------

def _generer(monkeypatch, sortie_serveur: str) -> dict:
    """Une génération complète, moteur bouché sur `sortie_serveur`.

    Le bouchon fait ce que fait le VRAI moteur : il déballe avant de parser.
    C'est le seul point où le déballage est observable, et donc le seul endroit
    où ce témoin a un sens.
    """
    def chat_json(*a, **k):
        return json.loads(retirer_enveloppe_markdown(sortie_serveur))

    monkeypatch.setattr(report.llm, "chat_json", chat_json)
    monkeypatch.setattr(report.report_citations, "render_structured",
                        lambda *a, **k: "# Compte-rendu")

    journal: dict = {}
    report._generer_sous_contrat(
        "instruction", "corps", attendues=1, language="fr",
        provider="mlx", mistral_api_key=None, journal=journal)
    return journal


def test_le_journal_de_production_PORTE_le_deballage(monkeypatch):
    """Le trou que ce témoin ferme : PASS_DIRECT sans la moindre trace.

    L'issue ne change pas — déballer n'est pas réparer — mais un modèle qui se
    met à baliser ses sorties doit devenir visible AVANT le jour où sa clôture
    arrive tronquée.
    """
    journal = _generer(monkeypatch, DOCUMENT + BT)

    assert journal["status"] == reparation_json.PASS_DIRECT
    assert journal["envelope"] == llm_base.CLOTURE_RESIDUELLE
    # Et surtout : ce n'est PAS comptabilisé comme une réparation.
    assert journal["repair"] == {"applied": False}


def test_une_generation_SANS_enveloppe_ne_porte_rien_au_journal(monkeypatch):
    """Sans ce pendant, le champ pourrait être toujours rempli sans qu'on le voie."""
    journal = _generer(monkeypatch, DOCUMENT)

    assert journal["status"] == reparation_json.PASS_DIRECT
    assert journal["envelope"] is None


# --------------------------------------------------------------------------
# Non-régression sur les sorties déjà valides
# --------------------------------------------------------------------------

def test_une_sortie_deja_valide_ressort_semantiquement_identique():
    assert json.loads(retirer_enveloppe_markdown(DOCUMENT)) == json.loads(DOCUMENT)
