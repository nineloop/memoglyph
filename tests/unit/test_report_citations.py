from __future__ import annotations

import pytest

from core import report_citations


pytestmark = pytest.mark.unit

TRANSCRIPT = """---
title: Réunion
---

## Transcript

**Speaker 1** [00:00:04] L'équipe valide le lancement mardi.

**Speaker 2** [00:00:12] Non, le budget reste à confirmer.
"""


def test_une_citation_exacte_couvre_son_affirmation() -> None:
    report = """## Décision

- L'équipe valide le lancement mardi.
> [source 00:00:04] L'équipe valide le lancement mardi.
"""

    audit = report_citations.audit(report, TRANSCRIPT)

    assert audit.status == "citation_integrity_verified"
    assert audit.claims == 1
    assert audit.claims_with_valid_citation == 1
    assert audit.citations == 1
    assert audit.valid_citations == 1
    assert audit.abstentions == 0
    assert audit.warnings == ()


def test_la_normalisation_ne_tolere_que_casse_accents_et_espaces() -> None:
    report = """## Décision

- Le lancement est validé.
> [source 00:00:04] L'ÉQUIPE   VALIDE LE LANCEMENT MARDI.
"""

    audit = report_citations.audit(report, TRANSCRIPT)

    assert audit.status == "citation_integrity_verified"
    assert audit.valid_citations == 1


@pytest.mark.parametrize(
    ("citation", "motif"),
    [
        (
            "> [source 00:00:04] L'équipe refuse le lancement mardi.",
            "citation altérée",
        ),
        (
            "> [source 00:09:59] L'équipe valide le lancement mardi.",
            "horodatage absent",
        ),
        ("> [source 00:00:04]", "citation vide"),
    ],
)
def test_une_citation_invalide_reste_une_reserve(citation: str, motif: str) -> None:
    report = f"""## Décision

- L'équipe valide le lancement mardi.
{citation}
"""

    audit = report_citations.audit(report, TRANSCRIPT)

    assert audit.status == "warnings"
    assert audit.claims_with_valid_citation == 0
    assert len(audit.warnings) == 1
    assert motif in audit.warnings[0]


def test_une_affirmation_sans_citation_et_une_citation_orpheline_sont_signalees() -> None:
    report = """## Décision

Une affirmation en prose sans citation.

## Autre section

> [source 00:00:12] Non, le budget reste à confirmer.
"""

    audit = report_citations.audit(report, TRANSCRIPT)

    assert audit.status == "warnings"
    assert audit.claims == 1
    assert audit.citations == 1
    assert any("affirmation sans citation" in warning for warning in audit.warnings)
    assert any("citation orpheline" in warning for warning in audit.warnings)


def test_plusieurs_citations_peuvent_soutenir_la_meme_affirmation() -> None:
    report = """## État

- Le lancement est validé, mais le budget reste à confirmer.
> [source 00:00:04] L'équipe valide le lancement mardi.
> [source 00:00:12] Non, le budget reste à confirmer.
"""

    audit = report_citations.audit(report, TRANSCRIPT)

    assert audit.status == "citation_integrity_verified"
    assert audit.claims == 1
    assert audit.claims_with_valid_citation == 1
    assert audit.citations == 2
    assert audit.valid_citations == 2


def test_source_none_est_une_abstention_explicite_pas_une_preuve() -> None:
    report = """## Échéance

- Aucune échéance n'est indiquée dans la transcription.
> [source none]
"""

    audit = report_citations.audit(report, TRANSCRIPT)

    assert audit.status == "citation_integrity_verified"
    assert audit.claims == 1
    assert audit.claims_with_valid_citation == 0
    assert audit.abstentions == 1
    assert audit.valid_citations == 0
    assert audit.warnings == ()


def test_les_metadonnees_sont_reconstructibles_et_sans_contenu_source() -> None:
    report = """## Décision

- L'équipe valide le lancement mardi.
> [source 00:00:04] L'équipe valide le lancement mardi.
"""

    meta = report_citations.audit(report, TRANSCRIPT).metadata()

    assert meta == {
        "contract_version": 7,
        "status": "citation_integrity_verified",
        "claims": 1,
        "claims_with_valid_citation": 1,
        "abstentions": 0,
        "citations": 1,
        "valid_citations": 1,
    }
    assert "lancement" not in str(meta).lower()


def test_le_bloc_visible_n_affirme_jamais_que_le_contenu_est_vrai() -> None:
    report = """## Décision

- Le lancement est refusé.
> [source 00:00:04] L'équipe refuse le lancement mardi.
"""
    audit = report_citations.audit(report, TRANSCRIPT)

    notice = report_citations.warning_notice(audit, "fr")

    assert "1 anomalie de citation" in notice
    assert "intégrité" in notice
    assert "vérité" in notice
    assert "citation altérée" in notice
    assert report_citations.warning_notice(
        report_citations.audit(
            "- Décision.\n> [source 00:00:04] L'équipe valide le lancement mardi.",
            TRANSCRIPT,
        ),
        "fr",
    ) == ""


def test_un_bloc_de_citation_hors_contrat_n_est_pas_ignore() -> None:
    audit = report_citations.audit(
        "## Décision\n\n> Le lancement est validé.", TRANSCRIPT
    )

    assert audit.status == "warnings"
    assert any("bloc de citation hors contrat" in warning for warning in audit.warnings)
    assert any("aucune affirmation détectée" in warning for warning in audit.warnings)


def test_un_cr_sauvegarde_peut_etre_reaudite_sans_compter_ses_reserves() -> None:
    report = f"""---
type: report
---

# Compte-rendu

- L'équipe valide le lancement mardi.
> [source 00:00:04] L'équipe valide le lancement mardi.

{report_citations.GENERATED_NOTICE_MARKER}

---

> ⚠️ **Réserve générée par Memoglyph.**

- Ce détail n'est pas une affirmation du modèle.
"""

    audit = report_citations.audit(report, TRANSCRIPT)

    assert audit.status == "citation_integrity_verified"
    assert audit.claims == 1
    assert audit.warnings == ()


def test_le_rendu_structure_separe_lecture_et_preuves() -> None:
    payload = {"sections": [
        _section(1, "Décision",
                 [_claim("L'équipe valide le lancement mardi.")]),
        _sans_reponse(2, "Budget"),
    ]}

    rendered = report_citations.render_structured(
        payload, TRANSCRIPT, expected_sections=2, language="fr"
    )

    editorial, appendix = rendered.split(report_citations.EVIDENCE_MARKER, 1)
    assert (
        "- L'équipe valide le lancement mardi. [source 00:00:04]"
        in editorial
    )
    assert "\n> [source" not in editorial
    assert "> [source 00:00:04] L'équipe valide le lancement mardi." in appendix
    assert "ne répond pas à cette question" in editorial, (
        "une section sans réponse se DIT, elle ne se laisse pas deviner")
    assert "> [source none]" in appendix, (
        "et reste une abstention pour l'audit, qui relit le Markdown seul")

    result = report_citations.audit(rendered, TRANSCRIPT)
    assert result.status == "citation_integrity_verified"
    assert result.claims == 2
    assert result.claims_with_valid_citation == 1
    assert result.abstentions == 1


def test_une_preuve_structuree_invalide_n_obtient_pas_de_badge() -> None:
    payload = {
        "sections": [
            {
                "question_id": 1, "title": "Décision", "answer_status": "answered", "claims": [
                    {"text": "Le lancement est refusé.", "primary_evidence": {"timestamp": "00:00:04", "quote": "L'équipe refuse le lancement mardi."}, "secondary_evidence": None}
                ],
            }
        ]
    }

    rendered = report_citations.render_structured(
        payload, TRANSCRIPT, expected_sections=1, language="fr"
    )
    editorial, appendix = rendered.split(report_citations.EVIDENCE_MARKER, 1)

    assert "[source 00:00:04]" not in editorial
    assert "[⚠ source à vérifier]" in editorial
    assert "> [source 00:00:04] L'équipe refuse le lancement mardi." in appendix
    assert report_citations.audit(rendered, TRANSCRIPT).status == "warnings"


def test_un_prefixe_source_exact_est_retire_avant_validation() -> None:
    payload = {"sections": [_section(1, "Décision", [_claim(
        "L'équipe valide le lancement mardi.",
        citation=("**Speaker 1** [00:00:04] "
                  "L'équipe valide le lancement mardi."))])]}

    rendered = report_citations.render_structured(
        payload, TRANSCRIPT, expected_sections=1, language="fr"
    )

    assert "**Speaker 1**" not in rendered
    assert report_citations.audit(
        rendered, TRANSCRIPT
    ).status == "citation_integrity_verified"


def test_la_structure_exige_une_section_par_question() -> None:
    with pytest.raises(
        report_citations.StructuredReportError, match="2 sections attendues"
    ):
        report_citations.render_structured(
            {"sections": []},
            TRANSCRIPT,
            expected_sections=2,
            language="fr",
        )


@pytest.mark.parametrize("claims_count", [10, 11])
def test_le_cr_est_borne_a_dix_affirmations_au_total(claims_count: int) -> None:
    """Le plafond total SUIT désormais le nombre de sections, et il NORMALISE.

    Deux changements par rapport à la version d'origine, tous deux mesurés :

    - la constante 10 contredisait le contrat sur les genres à six questions —
      six sections à deux affirmations en font DOUZE. Un modèle parfaitement
      conforme était donc rejeté par construction, et les deux CR Mistral de six
      sections du corpus tournaient à marge zéro ;
    - un excès s'écarte au lieu de rejeter le document, comme pour le plafond
      par section. La borne reste réelle — privé de toute cardinalité, Mistral
      produit 31 affirmations sur six sections — mais elle ne détruit plus tout.
    """
    per_section = [2] * 5 + ([1] if claims_count > 10 else [])
    payload = {
        "sections": [
            {
                "question_id": question_id,
                "title": f"Synthèse {question_id}",
                "answer_status": "answered",
                # FONDÉES, et c'est nécessaire depuis la règle sobre : une
                # affirmation sans citation vérifiable ne remplit plus le
                # budget, donc un jeu non fondé ne testerait plus le plafond.
                "claims": [
                    _claim(f"Affirmation pertinente {question_id}-{index}.")
                    for index in range(section_claims)
                ],
            }
            for question_id, section_claims in enumerate(per_section, 1)
        ]
    }

    rendered = report_citations.render_structured(
        payload,
        TRANSCRIPT,
        expected_sections=len(per_section),
        language="fr",
    )
    plafond = max(report_citations.MAX_REPORT_CLAIMS,
                  len(per_section) * report_citations.DOTATION_PAR_SECTION)
    assert rendered.count("### claim-") == min(claims_count, plafond)
    assert claims_count <= plafond, (
        "ce jeu ne dépasse plus le plafond : il ne teste plus la normalisation")


def test_la_borne_par_section_est_dans_le_TEXTE_et_PAS_dans_le_schema() -> None:
    """L'asymétrie est le cœur du correctif, et elle n'est pas décorative.

    Les deux moteurs ne lisent pas la même chose. Mistral honore le SCHÉMA au
    décodage ; le modèle local l'ignore et n'a que le TEXTE. Une borne posée au
    mauvais endroit rate donc sa cible, ou abîme l'autre chemin.

    Observé en local, seule cette phrase du texte changeant : sans clause, le
    modèle génère jusqu'aux 16 384 jetons sans aboutir ; avec « au maximum 5 »,
    il aboutit, plus vite et sans user d'une reprise.

    Le schéma, lui, doit RESTER libre : y remettre `maxItems` ramènerait le
    nivellement de la matière qu'on a retiré, et par le seul chemin où il
    s'impose vraiment.
    """
    fr = report_citations.prompt_contract("fr")
    en = report_citations.prompt_contract("en")
    claims = report_citations.output_schema(5)[
        "properties"]["sections"]["items"]["properties"]["claims"]

    borne = report_citations.BORNE_TEXTE_PAR_SECTION
    assert f"au maximum {borne} affirmations" in fr
    assert f"at most {borne} claims" in en
    assert "maxItems" not in claims, (
        "le schéma doit rester libre : c'est lui que Mistral honore")


def test_le_schema_n_impose_PLUS_de_plafond_par_section() -> None:
    """Le schéma est la seule contrainte que Mistral honore vraiment au
    décodage. Y laisser `maxItems: 2` par section ramènerait par la porte du
    cloud le nivellement qu'on vient de retirer du rendu — le modèle produirait
    deux affirmations partout et la sélection n'aurait plus rien à préférer.

    `minItems: 1` reste : une section vide n'est pas réparable après coup.
    """
    schema = report_citations.output_schema(expected_sections=5)
    sections = schema["properties"]["sections"]
    claims = sections["items"]["properties"]["claims"]
    affirmation = claims["items"]["properties"]

    assert sections["minItems"] == sections["maxItems"] == 5
    assert "maxItems" not in claims, (
        "le schéma doit rester libre : c'est lui que Mistral honore")
    assert "minItems" not in claims, (
        "une section `not_found` est LÉGITIMEMENT vide")
    # La cardinalité des preuves n'est plus une BORNE, elle est la STRUCTURE.
    assert "evidence" not in affirmation, "la liste ouverte a disparu"
    assert affirmation["primary_evidence"]["type"] == "object"
    assert "null" in str(affirmation["secondary_evidence"])


# ---------------------------------------------------------------------------
# Un EXCÈS se normalise, un MANQUE se rejette
# ---------------------------------------------------------------------------


def _claim(texte: str, horodatage: str = "00:00:04",
           citation: str = "L'équipe valide le lancement mardi.") -> dict:
    return {"text": texte,
            "primary_evidence": {"timestamp": horodatage, "quote": citation},
            "secondary_evidence": None}


def _payload_avec(nb_affirmations: int) -> dict:
    return {"sections": [_section(
        1, "Lancement",
        [_claim(f"Affirmation {i}") for i in range(1, nb_affirmations + 1)])]}


def _non_fondee(texte: str) -> dict:
    """Une affirmation dont la citation N'EXISTE PAS dans le transcript."""
    return _claim(texte, citation="L'équipe refuse le lancement mardi.")


def _section(question_id: int, titre: str, claims: list[dict]) -> dict:
    """Une section RÉPONDUE. Le « sans réponse » a son propre constructeur."""
    return {"question_id": question_id, "title": titre,
            "answer_status": "answered", "claims": claims}


def _sans_reponse(question_id: int, titre: str = "Sans réponse") -> dict:
    return {"question_id": question_id, "title": titre,
            "answer_status": "not_found", "claims": []}


def test_un_exces_d_affirmations_est_ECARTE_et_non_rejete() -> None:
    """Le plafond est ÉDITORIAL — le contrat le dit lui-même : « sélectionne
    uniquement les plus pertinentes et ne découpe pas artificiellement une même
    idée ». C'est de la concision, pas de la justesse.

    Rejeter tout un document pour ça était disproportionné ET inapplicable :
    rien ne contraint le modèle pendant le décodage, mlx-lm acceptant
    `response_format: json_schema` puis l'ignorant : le CR local échouait
    sur cette seule clause.
    """
    rendu = report_citations.render_structured(
        _payload_avec(14), TRANSCRIPT, expected_sections=1, language="fr")

    editorial = rendu.split(report_citations.EVIDENCE_MARKER, 1)[0]
    assert editorial.count("\n- ") == report_citations.MAX_REPORT_CLAIMS


def test_une_section_RICHE_n_est_plus_nivelee_sur_les_autres() -> None:
    """Le plafond PAR SECTION est retiré : seul le total subsiste.

    Il nivelait la matière au lieu de l'épouser. Sans aucune bride, la
    répartition réelle est très inégale, chez Mistral comme en local. Une
    section dense et une section
    pauvre ne méritent pas le même quota ; c'est le transcript qui décide.
    """
    rendu = report_citations.render_structured(
        _payload_avec(5), TRANSCRIPT, expected_sections=1, language="fr")

    editorial = rendu.split(report_citations.EVIDENCE_MARKER, 1)[0]
    for i in range(1, 6):
        assert f"Affirmation {i}" in editorial
    assert "écartée" not in editorial, "cinq tient sous le plafond total"


def test_la_selection_garde_les_FONDEES_d_abord() -> None:
    """LE critère, et la raison d'être de cette sélection.

    On gardait « les premières ». Or l'ordre n'a AUCUNE corrélation avec la
    qualité — sur une conférence sur le climat, la première affirmation de la
    section 1 n'est pas fondée, la deuxième l'est, et l'ancienne troncature
    gardait des affirmations invérifiables en écartant des fondées.

    `evidence.valid` est le seul jugement que ce module s'autorise : la citation
    existe-t-elle mot pour mot dans le transcript. Déterministe, sans LLM.
    """
    claims = [_non_fondee(f"Sans preuve {i}") for i in range(1, 11)]
    claims += [_claim(f"Fondée {i}") for i in range(1, 5)]
    rendu = report_citations.render_structured(
        {"sections": [_section(1, "Lancement", claims)]},
        TRANSCRIPT, expected_sections=1, language="fr")

    editorial = rendu.split(report_citations.EVIDENCE_MARKER, 1)[0]
    for i in range(1, 5):
        assert f"Fondée {i}" in editorial, "une affirmation vérifiable ne s'écarte pas"
    # Quatorze produites, quatre retenues : le budget de dix reste OUVERT plutôt
    # que d'être comblé par les dix affirmations sans citation vérifiable.
    assert editorial.count("Sans preuve") == 0
    assert editorial.count("\n- ") == 4


def test_une_section_SANS_REPONSE_ne_fabrique_plus_d_affirmation() -> None:
    """Le « sans réponse » a changé de niveau, et c'est un gain de justesse.

    Il était porté par une affirmation artificielle — `abstention: true`,
    `evidence: []`, texte du genre « aucune information trouvée ». C'était faire
    dire au modèle une affirmation à propos du monde pour exprimer une propriété
    de la QUESTION. (Règle portée par `prompt_contract`.)

    Il vit maintenant dans `answer_status` au niveau de la section, dont les
    `claims` sont alors légitimement vides. Le document, lui, le dit toujours —
    et l'annexe garde `> [source none]`, la notation que l'audit relit sans
    connaître le JSON.
    """
    payload = {"sections": [
        _section(1, "Répondue", [_claim("Prouvée")]),
        _sans_reponse(2, "Muette"),
    ]}

    rendu = report_citations.render_structured(
        payload, TRANSCRIPT, expected_sections=2, language="fr")

    editorial, annexe = rendu.split(report_citations.EVIDENCE_MARKER, 1)
    assert "ne répond pas à cette question" in editorial
    assert "> [source none]" in annexe
    # Et `audit()` compte la section comme une abstention dans le rendu.
    assert report_citations.audit(rendu, TRANSCRIPT).abstentions == 1


def test_une_section_SANS_REPONSE_qui_affirme_est_REFUSEE() -> None:
    """Les deux incohérences sont symétriques et toutes deux refusées : une
    section muette qui affirme, une section répondue qui n'affirme rien."""
    muette_bavarde = {"sections": [
        {"question_id": 1, "title": "Muette", "answer_status": "not_found",
         "claims": [_claim("Pourtant affirmée")]}]}
    with pytest.raises(report_citations.StructuredReportError) as capture:
        report_citations.render_structured(
            muette_bavarde, TRANSCRIPT, expected_sections=1, language="fr")
    assert capture.value.code == report_citations.SANS_REPONSE_MAIS_AFFIRME

    repondue_vide = {"sections": [
        {"question_id": 1, "title": "Répondue", "answer_status": "answered",
         "claims": []}]}
    with pytest.raises(report_citations.StructuredReportError):
        report_citations.render_structured(
            repondue_vide, TRANSCRIPT, expected_sections=1, language="fr")


def test_chaque_section_garde_au_moins_UNE_affirmation() -> None:
    """Le quota de survie prime sur le critère : une section vide est un rejet.

    Sans lui, un budget consommé par les premières sections viderait les
    dernières — et le document tomberait pour une règle censée l'améliorer.
    """
    sections = [
        _section(1, "Dense", [_claim(f"Fondée {i}") for i in range(1, 13)]),
        _section(2, "Pauvre", [_non_fondee("La seule de sa section")]),
    ]
    rendu = report_citations.render_structured(
        {"sections": sections}, TRANSCRIPT, expected_sections=2, language="fr")

    assert "La seule de sa section" in rendu, (
        "la survie d'une section prime sur la préférence pour les fondées")


def test_la_selection_CONSERVE_l_ordre_du_modele() -> None:
    """Elle choisit quoi garder ; elle ne réordonne pas le propos.

    Remonter les fondées en tête ferait de ce module un éditeur du discours, et
    non plus un vérificateur. La frontière du dépôt tient là : le modèle choisit
    les mots, Memoglyph possède la structure.
    """
    sections = [
        # Rien de fondé ici : seul le quota de survie la sauve, et elle doit
        # rester en tête du document, là où le modèle l'a mise.
        _section(1, "Sans preuve", [_non_fondee("La seule de sa section")]),
        _section(2, "Fondée", [
            _claim("Bêta vient en premier"),
            _non_fondee("Écartée du milieu"),
            _claim("Alpha vient en second"),
        ]),
    ]
    rendu = report_citations.render_structured(
        {"sections": sections}, TRANSCRIPT, expected_sections=2, language="fr")

    editorial = rendu.split(report_citations.EVIDENCE_MARKER, 1)[0]
    assert "Écartée du milieu" not in editorial
    assert editorial.index("La seule de sa section") < editorial.index("Bêta")
    # L'ordre du modèle, PAS l'ordre alphabétique ni celui de la sélection.
    assert editorial.index("Bêta vient en premier") < editorial.index("Alpha vient en second")


def test_la_selection_est_ANNONCEE_jamais_silencieuse() -> None:
    """Une sélection muette laisserait croire que le modèle a produit exactement
    ce qu'on voit — et le lecteur ne saurait ni qu'il manque des affirmations,
    ni sur quel critère. Le dépôt a déjà ce motif pour les identités : on
    retire, et on le dit."""
    rendu = report_citations.render_structured(
        _payload_avec(14), TRANSCRIPT, expected_sections=1, language="fr")

    assert "4 affirmation(s) écartée(s)" in rendu, (
        "le nombre écarté doit être dit, pas seulement le fait")
    assert "VÉRIFIÉE" in rendu, "le critère de sélection doit être dit au lecteur"


def test_une_section_SANS_affirmation_reste_un_rejet() -> None:
    """Un MANQUE n'est pas un excès : rien ne peut le réparer après coup. La
    frontière entre normaliser et rejeter tient là, et elle doit tenir dans les
    deux sens."""
    with pytest.raises(report_citations.StructuredReportError):
        report_citations.render_structured(
            {"sections": [_section(1, "Vide", [])]},
            TRANSCRIPT, expected_sections=1, language="fr")


def test_le_nombre_exact_ne_declenche_aucun_avis() -> None:
    """Le cas nominal ne doit pas porter de bruit : un document conforme n'a
    aucune raison d'annoncer une troncature."""
    rendu = report_citations.render_structured(
        _payload_avec(report_citations.DOTATION_PAR_SECTION),
        TRANSCRIPT, expected_sections=1, language="fr")

    assert "Affirmations écartées" not in rendu


def _payload_sections(nb_sections: int, par_section: int) -> dict:
    return {"sections": [
        _section(i, f"Section {i}",
                 [_claim(f"S{i} affirmation {j}") for j in range(1, par_section + 1)])
        for i in range(1, nb_sections + 1)]}


def test_un_modele_CONFORME_n_est_plus_rejete_sur_six_sections() -> None:
    """L'incohérence était arithmétique et INÉVITABLE : six sections à deux
    affirmations en font douze, pour un plafond total de dix. Un modèle
    respectant EXACTEMENT le contrat était refusé par construction sur quatre
    genres sur cinq.

    Et les deux CR Mistral de six sections du corpus tournaient à marge ZÉRO,
    dix sur dix — une affirmation de plus et le pipeline qui marche tombait.
    """
    rendu = report_citations.render_structured(
        _payload_sections(6, report_citations.DOTATION_PAR_SECTION),
        TRANSCRIPT, expected_sections=6, language="fr")

    editorial = rendu.split(report_citations.EVIDENCE_MARKER, 1)[0]
    assert editorial.count("\n- ") == 12, "les douze affirmations doivent tenir"
    assert "écartée" not in editorial, "rien ne doit être écarté sur un document conforme"


def test_le_plafond_total_reste_un_PLANCHER_pour_les_genres_courts() -> None:
    """Un genre à quatre sections n'obtient pas moins que la constante
    historique : le plafond suit les sections, sans jamais descendre sous dix."""
    rendu = report_citations.render_structured(
        _payload_sections(4, 2), TRANSCRIPT, expected_sections=4, language="fr")
    assert "écartée" not in rendu


def test_au_dela_du_plafond_total_on_ecarte_et_on_le_DIT() -> None:
    """Le plafond n'est PAS vestigial, contrairement aux apparences.
    Privé de toute cardinalité, Mistral produit bien plus d'affirmations que le
    plafond. Sa concision est de l'obéissance, pas un penchant. La borne porte donc réellement la concision du document.
    """
    # 6 sections × 3 = 18 affirmations, pour un plafond de 12.
    rendu = report_citations.render_structured(
        _payload_sections(6, 3), TRANSCRIPT, expected_sections=6, language="fr")

    editorial = rendu.split(report_citations.EVIDENCE_MARKER, 1)[0]
    assert "écartée" in editorial, "un excès total doit être annoncé"
    assert editorial.count("\n- ") <= 12


def test_les_numeros_suivent_l_ORDRE_DU_DOCUMENT_et_non_celui_du_modele() -> None:
    """Le modèle n'est pas tenu d'émettre ses sections dans l'ordre : le contrat
    porte `question_id`, et Memoglyph trie. La numérotation doit suivre le
    document rendu — un `claim-003` au-dessus d'un `claim-001` serait illisible,
    et l'annexe renvoie à ces identifiants.
    """
    sections = [
        _section(2, "Seconde", [_claim("Vient après")]),
        _section(1, "Première", [_claim("Vient avant")]),
    ]
    rendu = report_citations.render_structured(
        {"sections": sections}, TRANSCRIPT, expected_sections=2, language="fr")

    editorial, annexe = rendu.split(report_citations.EVIDENCE_MARKER, 1)
    assert editorial.index("Première") < editorial.index("Seconde")
    # L'annexe suit le corps, et c'est elle qui porte les identifiants.
    assert annexe.index("claim-001") < annexe.index("claim-002")
    assert annexe.index("Vient avant") < annexe.index("Vient après")


def test_le_budget_n_est_PAS_comble_par_du_non_verifie() -> None:
    """La propriété qui définit la règle de `_selectionner`.

    Quand les affirmations fondées ne remplissent pas le plafond, on s'arrête
    là. On ne complète pas avec les autres.

    Compléter ne sauve pas UNE affirmation fondée de plus — les deux règles
    retiennent exactement les mêmes — et fait baisser la part vérifiable du
    document, chez les deux fournisseurs. Sur un transcript où le modèle cite
    mal, le lecteur recevait surtout des lignes qui ne renvoient à rien.

    Une affirmation non fondée n'est pas fausse : sa citation n'a pas été
    retrouvée mot pour mot, souvent parce que le modèle a paraphrasé. Mais elle
    n'apporte qu'une ligne à croire sur parole.

    Conséquence assumée, et c'est ce que ce témoin fige : le compte-rendu est
    plus COURT quand le modèle a moins bien cité.
    """
    claims = [_claim("La seule prouvée")]
    claims += [_non_fondee(f"Sans preuve {i}") for i in range(1, 13)]
    rendu = report_citations.render_structured(
        {"sections": [_section(1, "Lancement", claims)]},
        TRANSCRIPT, expected_sections=1, language="fr")

    editorial = rendu.split(report_citations.EVIDENCE_MARKER, 1)[0]
    assert editorial.count("\n- ") == 1, (
        "le budget de dix reste ouvert plutôt que d'être comblé par du non vérifié")
    assert "La seule prouvée" in editorial
    assert "12 affirmation(s) écartée(s)" in rendu
    assert "ne complètent pas le compte-rendu" in rendu, (
        "le lecteur doit savoir POURQUOI le document est court")


# ---------------------------------------------------------------------------
# La cardinalité des preuves, désormais INCARNÉE dans la structure
# ---------------------------------------------------------------------------


def test_une_preuve_PRINCIPALE_seule_suffit() -> None:
    """Le cas nominal : une affirmation, une citation vérifiée."""
    rendu = report_citations.render_structured(
        {"sections": [_section(1, "Décision", [_claim("Prouvée")])]},
        TRANSCRIPT, expected_sections=1, language="fr")

    assert "Prouvée [source 00:00:04]" in rendu


def test_une_preuve_SECONDAIRE_est_acceptee_et_rendue() -> None:
    """Elle doit apporter un élément DISTINCT — le contrat le demande au modèle,
    et rien ici ne peut le vérifier. Ce qu'on vérifie, c'est qu'elle voyage."""
    claim = _claim("Deux appuis")
    claim["secondary_evidence"] = {
        "timestamp": "00:00:12", "quote": "Non, le budget reste à confirmer."}

    rendu = report_citations.render_structured(
        {"sections": [_section(1, "Décision", [claim])]},
        TRANSCRIPT, expected_sections=1, language="fr")

    assert "[source 00:00:04]" in rendu and "[source 00:00:12]" in rendu


def test_une_preuve_PRINCIPALE_absente_est_refusee() -> None:
    """« Une information absente ne doit pas créer un claim sans preuve. »

    C'est la règle qui rend la structure honnête : sans elle, `primary_evidence:
    null` rouvrirait la porte à l'affirmation non appuyée que le tableau ouvert
    permettait déjà.
    """
    orphelin = {"text": "Sans appui", "primary_evidence": None,
                "secondary_evidence": None}

    with pytest.raises(report_citations.StructuredReportError) as capture:
        report_citations.render_structured(
            {"sections": [_section(1, "Décision", [orphelin])]},
            TRANSCRIPT, expected_sections=1, language="fr")

    assert capture.value.code == report_citations.PREUVE_PRINCIPALE_ABSENTE


def test_une_SECONDAIRE_sans_principale_est_refusee() -> None:
    """L'ordre n'est pas décoratif : la secondaire complète, elle ne remplace
    pas."""
    inverse = {"text": "À l'envers", "primary_evidence": None,
               "secondary_evidence": {"timestamp": "00:00:04",
                                      "quote": "L'équipe valide le lancement mardi."}}

    with pytest.raises(report_citations.StructuredReportError) as capture:
        report_citations.render_structured(
            {"sections": [_section(1, "Décision", [inverse])]},
            TRANSCRIPT, expected_sections=1, language="fr")

    assert capture.value.code == report_citations.PREUVE_PRINCIPALE_ABSENTE


def test_l_ANCIEN_tableau_ouvert_est_refuse_avec_le_bon_CODE() -> None:
    """MESURÉ : le modèle local avait mis 172 preuves pour 11
    affirmations dans ce tableau, en recopiant le transcript tour par tour.

    Le refus doit porter `TOO_MANY_EVIDENCE` et non un « hors contrat »
    générique : c'est ce code qui déclenche la correction ciblée de la reprise.
    """
    ancien = {"text": "Ancien format", "abstention": False,
              "evidence": [{"timestamp": "00:00:04", "quote": "Bonjour."}]}

    with pytest.raises(report_citations.StructuredReportError) as capture:
        report_citations.render_structured(
            {"sections": [_section(1, "Décision", [ancien])]},
            TRANSCRIPT, expected_sections=1, language="fr")

    assert capture.value.code == report_citations.TROP_DE_PREUVES


@pytest.mark.parametrize(("preuves", "attendu"), [
    (0, (None, None)),
    (1, ("00:00:04", None)),
    (2, ("00:00:04", "00:00:12")),
    (44, ("00:00:04", "00:00:12")),
])
def test_normalize_claim_relit_l_ancien_format(preuves, attendu) -> None:
    """La compatibilité appartient au LECTEUR, jamais au prompt.

    Sert aux artefacts déjà écrits. Au-delà de deux preuves, seules les
    deux premières survivent — le reste est précisément ce que le nouveau
    contrat rend impossible à produire.
    """
    horodatages = ["00:00:04", "00:00:12"] + [f"00:01:{i:02d}" for i in range(42)]
    ancien = {
        "text": "Relu", "abstention": False,
        "evidence": [{"timestamp": h, "quote": "peu importe"}
                     for h in horodatages[:preuves]],
    }

    relu = report_citations.normalize_claim(ancien)

    assert set(relu) == {"text", "primary_evidence", "secondary_evidence"}, (
        "`abstention` disparaît : le « sans réponse » vit au niveau section")
    principale, secondaire = attendu
    assert (relu["primary_evidence"] or {}).get("timestamp") == principale
    assert (relu["secondary_evidence"] or {}).get("timestamp") == secondaire


def test_normalize_claim_laisse_intact_le_NOUVEAU_format() -> None:
    """Relire deux fois ne doit rien abîmer."""
    neuf = _claim("Déjà au format")

    assert report_citations.normalize_claim(neuf) == neuf


def test_le_contrat_ne_demande_AUCUN_compteur_global() -> None:
    """Règle de `prompt_contract`, et la raison est mécanique.

    Le contrat portait « au maximum 10 affirmations au total ». C'était sa
    dernière règle à état GLOBAL : elle exige un compteur maintenu sur toute la
    sortie, là où toutes les autres se vérifient dans une fenêtre courte — une
    section, une affirmation, une preuve.

    Elle n'était pas tenue : sur une conférence sur le climat, le modèle
    dépassait largement le total autorisé, et une section le double de sa borne —
    c'est à la sortie de cette section que son JSON cassait.

    La retirer ne change RIEN au document rendu : `_selectionner` applique le
    plafond après coup, et c'est la seule façon de l'imposer — JSON Schema n'a
    pas d'arithmétique entre propriétés sœurs.
    """
    for langue in ("fr", "en"):
        contrat = report_citations.prompt_contract(langue)
        assert str(report_citations.MAX_REPORT_CLAIMS) not in contrat, (
            f"le plafond TOTAL est revenu dans le contrat {langue}")
        # La borne PAR SECTION, elle, doit rester : sans elle le modèle local
        # dérape.
        assert str(report_citations.BORNE_TEXTE_PAR_SECTION) in contrat


def test_le_plafond_total_reste_APPLIQUE_apres_coup() -> None:
    """Retirer la consigne ne retire pas la borne : c'est tout l'argument.

    Si ce témoin tombait, l'allègement de la consigne aurait cessé d'être gratuit — il
    changerait le document rendu au lieu de n'alléger que la charge du modèle.
    """
    rendu = report_citations.render_structured(
        _payload_avec(14), TRANSCRIPT, expected_sections=1, language="fr")

    editorial = rendu.split(report_citations.EVIDENCE_MARKER, 1)[0]
    assert editorial.count("\n- ") == report_citations.MAX_REPORT_CLAIMS
    assert "4 affirmation(s) écartée(s)" in rendu
