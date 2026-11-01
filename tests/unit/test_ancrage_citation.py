"""PR-D — filet d'ancrage déterministe des citations.

Un extrait VERBATIM dont seul l'horodatage est faux est rattaché à son ancre
canonique réelle ; une paraphrase, des tours fusionnés ou une occurrence
ambiguë restent refusés. Le contrat de preuve n'est jamais relâché : l'audit
qui relit le Markdown revérifie la citation à l'horodatage canonique, à
l'identique — un extrait absent du transcript ne peut donc pas être « sauvé ».
"""
from __future__ import annotations

import pytest

from core import report, report_citations as rc

pytestmark = pytest.mark.unit


TRANSCRIPT = """\
---
title: t
language: fr
---

## Transcript

**Speaker 1** [00:00:05] la France est championne du monde en deux mille dix-huit.

**Speaker 2** [00:01:10] Lefèvre tire sa révérence après la coupe du monde.

**Speaker 1** [00:02:00] une meme phrase revient plus tard a l'identique.

**Speaker 3** [00:03:00] une meme phrase revient plus tard a l'identique.

**Speaker 1** [00:04:00] le debut de la phrase coupee en deux

**Speaker 2** [00:04:05] et la fin de la phrase coupee en deux
"""

SEGMENTS = rc._transcript_segments(TRANSCRIPT)


# --------------------------------------------------------------------------- #
#  _ancrer_preuve : le cœur déterministe                                       #
# --------------------------------------------------------------------------- #
def test_natif_ne_bouge_pas() -> None:
    anc = rc._ancrer_preuve(SEGMENTS, "00:00:05", "la France est championne du monde")
    assert (anc.valide, anc.normalise, anc.timestamp) == (True, False, "00:00:05")


def test_reancre_pointeur_decale() -> None:
    """Horodatage faux mais extrait verbatim unique ailleurs : rattaché au réel."""
    anc = rc._ancrer_preuve(
        SEGMENTS, "00:00:09", "la France est championne du monde en deux mille dix-huit")
    assert anc.valide and anc.normalise
    assert anc.timestamp == "00:00:05"      # canonique du transcript
    assert anc.origine == "00:00:09"        # ce qu'avait écrit le modèle


def test_reancre_format_mmss_vers_hhmmss() -> None:
    """Le cas cloud : MM:SS chez le modèle, HH:MM:SS dans le transcript."""
    anc = rc._ancrer_preuve(
        SEGMENTS, "01:10", "Lefèvre tire sa révérence après la coupe du monde")
    assert anc.valide and anc.normalise
    assert anc.timestamp == "00:01:10" and anc.origine == "01:10"


def test_refuse_paraphrase() -> None:
    """Un extrait qui n'est nulle part mot pour mot n'est JAMAIS réancré."""
    anc = rc._ancrer_preuve(SEGMENTS, "00:00:05", "la France a gagné la coupe du monde")
    assert not anc.valide and not anc.normalise


def test_refuse_tours_fusionnes() -> None:
    """Deux tours recousus en une citation : introuvable dans UN seul tour."""
    quote = "le debut de la phrase coupee en deux et la fin de la phrase coupee en deux"
    anc = rc._ancrer_preuve(SEGMENTS, "00:04:00", quote)
    assert not anc.valide and not anc.normalise


def test_refuse_occurrence_ambigue() -> None:
    """Le même extrait à deux horodatages : ancre indécidable, on ne touche pas."""
    quote = "une meme phrase revient plus tard a l'identique"
    hits = [t for t, tx in SEGMENTS.items() if any(rc.quote_exists(x, quote) for x in tx)]
    assert len(hits) == 2, hits
    anc = rc._ancrer_preuve(SEGMENTS, "00:09:00", quote)
    assert not anc.valide and not anc.normalise


def test_natif_prime_sur_la_recherche() -> None:
    """Si l'extrait est déjà à l'horodatage annoncé, aucun réancrage même si
    l'extrait apparaît ailleurs : on ne quitte jamais une ancre native valide."""
    quote = "une meme phrase revient plus tard a l'identique"
    anc = rc._ancrer_preuve(SEGMENTS, "00:02:00", quote)
    assert anc.valide and not anc.normalise and anc.timestamp == "00:02:00"


# --------------------------------------------------------------------------- #
#  render_structured : réécriture d'horodatage, journal, et NON-relâchement    #
# --------------------------------------------------------------------------- #
def _payload(timestamp: str, quote: str) -> dict:
    return {"sections": [{
        "question_id": 1, "title": "Q", "answer_status": "answered",
        "claims": [{"text": "une affirmation testée",
                    "primary_evidence": {"timestamp": timestamp, "quote": quote},
                    "secondary_evidence": None}]}]}


def test_render_reancre_reecrit_horodatage_et_journalise() -> None:
    journal: dict = {}
    rendu = rc.render_structured(
        _payload("00:01:15", "Lefèvre tire sa révérence après la coupe du monde"),
        TRANSCRIPT, expected_sections=1, language="fr", journal=journal)

    # Le corps porte l'horodatage CANONIQUE, pas celui, erroné, du modèle.
    assert "[source 00:01:10]" in rendu
    assert "00:01:15" not in rendu
    assert "[⚠ source à vérifier]" not in rendu   # la citation est fondée

    anc = journal["citation_anchoring"]
    assert anc["applied"] and anc["reanchored"] == 1
    assert anc["citations"][0]["de"] == "00:01:15"
    assert anc["citations"][0]["vers"] == "00:01:10"
    assert anc["citations"][0]["quote_sha"]           # empreinte, pas l'extrait

    # NON-RELÂCHEMENT : l'audit relit le Markdown et revérifie le verbatim à
    # l'horodatage canonique, sans rien savoir du réancrage.
    audit = rc.audit(rendu, TRANSCRIPT)
    assert audit.valid_citations == 1


def test_render_paraphrase_reste_reserve() -> None:
    journal: dict = {}
    rendu = rc.render_structured(
        _payload("00:00:05", "la France a gagné la coupe du monde"),
        TRANSCRIPT, expected_sections=1, language="fr", journal=journal)

    assert "[⚠ source à vérifier]" in rendu            # non fondée → réserve
    assert journal["citation_anchoring"] == {
        "applied": False, "kind": None, "reanchored": 0, "citations": None}
    assert rc.audit(rendu, TRANSCRIPT).valid_citations == 0


def test_render_sans_journal_reste_compatible() -> None:
    """L'ancrage agit toujours ; l'observabilité est seulement optionnelle."""
    rendu = rc.render_structured(
        _payload("00:01:15", "Lefèvre tire sa révérence après la coupe du monde"),
        TRANSCRIPT, expected_sections=1, language="fr")
    assert "[source 00:01:10]" in rendu


# --------------------------------------------------------------------------- #
#  Segments LARGES (Whisper) : un extrait verbatim enjambe des tranches        #
#  consécutives d'un MÊME locuteur — recousu, jamais par-dessus une voix.      #
# --------------------------------------------------------------------------- #
COARSE = """\
---
title: t
language: en
---

## Transcript

**Speaker 1** [00:00:00] we combine structured data and unstructured data into a single

**Speaker 1** [00:00:05] orchestrator that serves each persona within their workflow.

**Speaker 2** [00:00:10] but you cannot have a partner

**Speaker 1** [00:00:14] that you cannot trust at all.

**Speaker 1** [00:00:20] repeated alpha beta

**Speaker 1** [00:00:24] gamma delta then more

**Speaker 3** [00:00:28] unrelated interruption here

**Speaker 1** [00:00:32] repeated alpha beta

**Speaker 1** [00:00:36] gamma delta then more
"""

SEG_COARSE = rc._transcript_segments(COARSE)
ORDERED_COARSE = rc._ordered_segments(COARSE)

# L'extrait qui enjambe [00:00:00]→[00:00:05], un seul locuteur.
_SPAN = "unstructured data into a single orchestrator that serves each persona"


def test_span_meme_locuteur_natif_au_debut() -> None:
    """Extrait à cheval sur deux tranches d'un même tour, annoncé au DÉBUT réel :
    natif, aucun réancrage. Le mono-segment ne le portait pas."""
    assert not rc._evidence_valid(SEG_COARSE, "00:00:00", _SPAN)
    anc = rc._ancrer_preuve(SEG_COARSE, "00:00:00", _SPAN, ordered=ORDERED_COARSE)
    assert (anc.valide, anc.normalise, anc.timestamp) == (True, False, "00:00:00")


def test_span_meme_locuteur_reancre_vers_le_debut() -> None:
    """Même extrait, horodatage annoncé décalé sur la 2e tranche : rattaché au
    DÉBUT du span, marqué normalisé."""
    anc = rc._ancrer_preuve(SEG_COARSE, "00:00:05", _SPAN, ordered=ORDERED_COARSE)
    assert anc.valide and anc.normalise
    assert anc.timestamp == "00:00:00" and anc.origine == "00:00:05"


def test_span_ne_franchit_pas_un_changement_de_locuteur() -> None:
    """Deux tranches consécutives mais de VOIX différentes : couture interdite,
    même avec l'ancrage multi-segments actif."""
    quote = "but you cannot have a partner that you cannot trust at all"
    anc = rc._ancrer_preuve(SEG_COARSE, "00:00:10", quote, ordered=ORDERED_COARSE)
    assert not anc.valide and not anc.normalise


def test_span_ne_recoud_pas_des_tranches_non_consecutives() -> None:
    """Même locuteur mais tranches SÉPARÉES par une autre voix : pas de couture."""
    quote = "gamma delta then more repeated alpha beta"  # 00:00:24 puis 00:00:32
    anc = rc._ancrer_preuve(SEG_COARSE, "00:00:24", quote, ordered=ORDERED_COARSE)
    assert not anc.valide and not anc.normalise


def test_span_ambigu_reste_refuse() -> None:
    """Un span qui commence à DEUX endroits distincts : ancre indécidable."""
    quote = "beta gamma delta"  # 00:00:20→24 ET 00:00:32→36
    anc = rc._ancrer_preuve(SEG_COARSE, "00:00:20", quote, ordered=ORDERED_COARSE)
    assert not anc.valide and not anc.normalise


def test_span_paraphrase_reste_refuse() -> None:
    """Recoudre n'autorise pas la paraphrase : le verbatim contigu reste requis."""
    quote = "unstructured data feeding one orchestrator per persona"
    anc = rc._ancrer_preuve(SEG_COARSE, "00:00:00", quote, ordered=ORDERED_COARSE)
    assert not anc.valide and not anc.normalise


def test_span_sans_ordered_non_sauve() -> None:
    """`ordered` absent (appel hérité) : le filet multi-segments ne s'active pas,
    le comportement mono-segment historique est préservé à l'identique."""
    anc = rc._ancrer_preuve(SEG_COARSE, "00:00:00", _SPAN)
    assert not anc.valide and not anc.normalise


def test_render_et_audit_valident_un_span_whisper() -> None:
    """Bout en bout : le corps porte l'horodatage de début et l'audit, qui relit
    le Markdown, revérifie le span à l'identique — la citation est fondée."""
    payload = {"sections": [{
        "question_id": 1, "title": "Q", "answer_status": "answered",
        "claims": [{"text": "one orchestrator per persona",
                    "primary_evidence": {"timestamp": "00:00:05", "quote": _SPAN},
                    "secondary_evidence": None}]}]}
    journal: dict = {}
    rendu = rc.render_structured(payload, COARSE, expected_sections=1,
                                 language="en", journal=journal)
    assert "[source 00:00:00]" in rendu           # ancré au DÉBUT du span
    assert "[⚠ source to check]" not in rendu      # fondée, pas de réserve
    assert journal["citation_anchoring"]["reanchored"] == 1
    assert rc.audit(rendu, COARSE).valid_citations == 1


# --------------------------------------------------------------------------- #
#  front-matter : un simple compte, jamais d'extrait                           #
# --------------------------------------------------------------------------- #
def test_resume_front_matter_compact() -> None:
    assert report._resume_ancrage_citations(None) is None
    assert report._resume_ancrage_citations({"citation_anchoring":
                                             {"reanchored": 0}}) is None
    assert report._resume_ancrage_citations({"citation_anchoring":
                                             {"reanchored": 3}}) == {"reanchored": 3}
