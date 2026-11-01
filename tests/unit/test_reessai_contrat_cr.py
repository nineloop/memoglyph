"""Un compte-rendu hors contrat est REDEMANDÉ, pas perdu.

## Le défaut

La validation du contrat vivait APRÈS l'appel au modèle :

    structured = llm.chat_json(..., retries=1)     # boucle sur le JSON invalide
    cr_body = report_citations.render_structured(structured, ...)   # HORS boucle

Le paramètre `retries` ne couvre que le JSON MAL FORMÉ — les trois moteurs le
documentent ainsi. Une réponse bien formée mais hors contrat n'avait donc
**aucune** seconde chance : une déviation passagère du modèle coûtait le
compte-rendu, définitivement.

## Ce que la reprise change

Sur des générations réelles par Mistral, redemander fait nettement reculer les
comptes-rendus perdus. Les échecs portaient le même motif — « chaque section doit
contenir 1 à 2 affirmations » — et le même type d'échec avait été observé côté MLX.
Ce n'est donc pas la faiblesse d'un petit modèle.

Le budget de `TENTATIVES_CONTRAT` vient de ces générations, pas d'une intuition.

## Ce qu'on ne fait PAS

On ne relâche pas le contrat. Le validateur a raison de refuser un CR dont les
sections ne portent pas ce qu'elles promettent : publier un compte-rendu hors
contrat serait pire que ne pas en publier. C'est l'absence de seconde chance qui
était fautive.
"""

from __future__ import annotations

import pytest

from core import messages, report, report_citations

pytestmark = pytest.mark.unit

# Le budget ATTENDU, écrit ici plutôt que lu depuis le code : un test qui
# dérive sa cible du code testé accepte n'importe quel changement en silence.
TENTATIVES_ATTENDUES = 3


def _reponse_valide(sections: int) -> dict:
    return {
        "sections": [
            {
                "question_id": index,
                "title": f"Section {index}",
                "claims": [{
                    "text": "The speaker says hello.", "abstention": False,
                    "evidence": [{"timestamp": "00:00:04", "quote": "Hello."}],
                }],
            }
            for index in range(1, sections + 1)
        ]
    }


def _generer(monkeypatch, reponses: list, progress=None) -> str:
    """Enchaîne `reponses` sur les appels successifs au modèle.

    Une entrée `StructuredReportError` fait échouer la VALIDATION, pas l'appel :
    c'est le cas réel — le modèle rend un JSON parfaitement formé qui ne tient
    pas le contrat.
    """
    appels = {"n": 0}

    def chat_json(*a, **k):
        appels["n"] += 1
        return {"tirage": appels["n"]}

    def valider(structured, body, *, expected_sections, language, journal=None):
        # `journal` : optionnel côté production (observabilité du filet
        # d'ancrage), ignoré ici — ces tests portent sur la boucle de reprise,
        # pas sur les citations. Le stub doit néanmoins EN accepter la signature.
        resultat = reponses[structured["tirage"] - 1]
        if isinstance(resultat, Exception):
            raise resultat
        return resultat

    monkeypatch.setattr(report.llm, "chat_json", chat_json)
    monkeypatch.setattr(report.report_citations, "render_structured", valider)
    return report._generer_sous_contrat(
        "instruction", "corps", attendues=2, language="fr",
        provider="mlx", mistral_api_key=None, progress=progress,
    ), appels


def test_un_CR_hors_contrat_est_redemande_et_la_seconde_reponse_passe(monkeypatch):
    hors_contrat = report_citations.StructuredReportError(
        "chaque section doit contenir 1 à 2 affirmations")

    (cr, appels) = _generer(monkeypatch, [hors_contrat, "# Compte-rendu"])

    assert cr == "# Compte-rendu"
    assert appels["n"] == 2, "le modèle n'a pas été redemandé"


def test_un_CR_VALIDE_du_premier_coup_ne_coute_pas_de_generation_en_plus(monkeypatch):
    """Le réessai ne doit se payer que quand il sert.

    Une génération de compte-rendu est chère — plusieurs secondes en cloud, bien
    plus en local. La boucle ne doit jamais tourner à vide.
    """
    (cr, appels) = _generer(monkeypatch, ["# Compte-rendu"])

    assert cr == "# Compte-rendu"
    assert appels["n"] == 1


def test_apres_TOUTES_les_tentatives_l_echec_est_declare_avec_leur_nombre(monkeypatch):
    hors_contrat = report_citations.StructuredReportError("sections manquantes")

    with pytest.raises(report.llm.LLMError) as capture:
        _generer(monkeypatch, [hors_contrat] * report.TENTATIVES_CONTRAT)

    message = str(capture.value)
    assert str(report.TENTATIVES_CONTRAT) in message, message
    assert "sections manquantes" in message, "le motif du refus doit survivre"


def test_le_budget_est_reellement_consomme(monkeypatch):
    """Le garde contre un budget décoratif.

    Poser `TENTATIVES_CONTRAT = 3` sans que la boucle l'utilise donnerait le
    même résultat visible — un échec — pour une raison différente.
    """
    hors_contrat = report_citations.StructuredReportError("hors contrat")
    appels = {"n": 0}

    def chat_json(*a, **k):
        appels["n"] += 1
        return {}

    monkeypatch.setattr(report.llm, "chat_json", chat_json)
    monkeypatch.setattr(
        report.report_citations, "render_structured",
        lambda *a, **k: (_ for _ in ()).throw(hors_contrat))

    with pytest.raises(report.llm.LLMError):
        report._generer_sous_contrat(
            "instruction", "corps", attendues=2, language="fr",
            provider="mlx", mistral_api_key=None)

    assert appels["n"] == report.TENTATIVES_CONTRAT


def test_le_reessai_est_ANNONCE_par_un_code_de_message(monkeypatch):
    """Une génération de plus, c'est de l'attente en plus.

    Une barre qui ne bouge plus sans explication se lit comme un blocage. Le
    message est CODÉ (décision 1) : l'interface le traduit, un client sans
    interface lit l'anglais.
    """
    hors_contrat = report_citations.StructuredReportError("hors contrat")
    vus: list[str] = []

    _generer(
        monkeypatch, [hors_contrat, "# CR"],
        progress=lambda p, m: vus.append(getattr(m, "code", str(m))),
    )

    assert "warn.report.retry" in vus, vus


def test_le_dernier_echec_n_annonce_PAS_un_reessai(monkeypatch):
    """Promettre une tentative qui n'aura pas lieu serait le défaut de `_arreter`.

    Un message affirmait déjà « le prochain appel en démarrera un neuf » alors
    que rien n'était relancé. On ne recommence pas ici.
    """
    hors_contrat = report_citations.StructuredReportError("hors contrat")
    vus: list[str] = []

    with pytest.raises(report.llm.LLMError):
        _generer(
            monkeypatch, [hors_contrat] * report.TENTATIVES_CONTRAT,
            progress=lambda p, m: vus.append(getattr(m, "code", str(m))),
        )

    assert vus.count("warn.report.retry") == report.TENTATIVES_CONTRAT - 1, vus


def test_le_reessai_du_JSON_reste_chez_le_moteur(monkeypatch):
    """Deux échecs différents, deux budgets différents.

    Confondre « JSON mal formé » et « hors contrat » ferait payer une génération
    entière pour une virgule manquante — le moteur, lui, sait redemander sans
    tout régénérer.
    """
    vus: dict = {}

    def chat_json(*a, **k):
        vus.update(k)
        return {}

    monkeypatch.setattr(report.llm, "chat_json", chat_json)
    monkeypatch.setattr(
        report.report_citations, "render_structured", lambda *a, **k: "# CR")

    report._generer_sous_contrat(
        "instruction", "corps", attendues=2, language="fr",
        provider="mlx", mistral_api_key=None)

    assert vus["retries"] == 1


def test_le_message_de_reessai_porte_le_rang_de_la_tentative(monkeypatch):
    msg = messages.compte_rendu_hors_contrat("motif", 2, 3)
    assert msg.code == "warn.report.retry"
    assert msg.params["attempt"] == 2
    assert msg.params["total"] == 3


# ---------------------------------------------------------------------------
# Trois garde-fous de la boucle de reprise


def test_la_progression_ne_RECULE_jamais(monkeypatch):
    """Une régression introduite par la première boucle de reprise.

    Cette progression est mise à l'échelle par l'appelant sur `[0,80 ; 0,98]`,
    et la classification a déjà rendu `0.50`. Le premier réessai appelait
    `progress(0.0, …)` : la barre revenait de ~89 % à 80 % à chaque tentative.

    Le témoin collecte les valeurs et exige l'ordre — c'est plus solide qu'une
    valeur choisie à l'œil : le test ne dépend d'aucun chiffre particulier,
    seulement de la propriété.
    """
    hors_contrat = report_citations.StructuredReportError("hors contrat")
    vues: list[float] = []

    _generer(
        monkeypatch, [hors_contrat, hors_contrat, "# CR"],
        progress=lambda p, m: vues.append(p),
    )

    assert vues, "aucune progression émise, le témoin ne prouverait rien"
    assert vues == sorted(vues), f"la barre recule : {vues}"
    # Et jamais en deçà de ce que l'appelant a déjà annoncé (classification).
    assert min(vues) >= 0.50, vues


def test_une_panne_de_SERVICE_n_est_PAS_reessayee(monkeypatch):
    """La frontière entre les deux échecs, vérifiée.

    Une `LLMError` — réseau coupé, clé invalide, service indisponible — n'a
    aucune raison d'être répétée trois fois : la réponse sera la même, et on
    aurait triplé l'attente avant d'annoncer la panne. Seule une réponse HORS
    CONTRAT mérite une seconde chance.

    Le code était déjà correct — seule `StructuredReportError` est attrapée —
    mais rien ne le vérifiait.
    """
    appels = {"n": 0}

    def tombe(*a, **k):
        appels["n"] += 1
        raise report.llm.LLMError("service indisponible")

    monkeypatch.setattr(report.llm, "chat_json", tombe)

    with pytest.raises(report.llm.LLMError, match="service indisponible"):
        report._generer_sous_contrat(
            "instruction", "corps", attendues=2, language="fr",
            provider="mlx", mistral_api_key=None)

    assert appels["n"] == 1, "une panne de service a été réessayée"


def test_le_PLAFOND_brut_d_appels_au_modele_est_borne(monkeypatch):
    """Le coût maximal réel est de 6 appels, pas 3.

    Chaque tentative de contrat appelle `chat_json(retries=1)`, qui peut
    lui-même reprendre une fois sur un JSON mal formé. Le commentaire de
    `TENTATIVES_CONTRAT` l'annonce ; ce test l'empêche de dériver.

    On compte ici les tentatives de CONTRAT et le budget JSON transmis : leur
    produit est le plafond, et il doit rester explicite.
    """
    hors_contrat = report_citations.StructuredReportError("hors contrat")
    budgets: list[int] = []

    def chat_json(*a, **k):
        budgets.append(k["retries"])
        return {}

    monkeypatch.setattr(report.llm, "chat_json", chat_json)
    monkeypatch.setattr(
        report.report_citations, "render_structured",
        lambda *a, **k: (_ for _ in ()).throw(hors_contrat))

    with pytest.raises(report.llm.LLMError):
        report._generer_sous_contrat(
            "instruction", "corps", attendues=2, language="fr",
            provider="mlx", mistral_api_key=None)

    assert len(budgets) == TENTATIVES_ATTENDUES
    assert set(budgets) == {1}, budgets
    plafond = len(budgets) * (1 + budgets[0])
    assert plafond == 6, f"le plafond brut a changé : {plafond} appels"


# ---------------------------------------------------------------------------
# La frontière entre « service mort » et « sortie inexploitable »
# ---------------------------------------------------------------------------


def test_une_sortie_inexploitable_EST_reessayee(monkeypatch):
    """Le pendant du témoin de service, et la raison d'être du nouveau type.

    Le modèle a RÉPONDU, sa sortie est seulement inutilisable. MESURÉ : la panne
    du compte-rendu local change de mode d'une exécution à l'autre — JSON
    malformé, puis JSON valide mais hors contrat. Redemander a donc de vraies
    chances d'aboutir, contrairement à un service mort.

    Avant ce type, `report.py` ne pouvait pas distinguer les deux : `MlxLLMError`
    portait indifféremment le réseau coupé et le charabia.
    """
    appels = {"n": 0}

    def bafouille(*a, **k):
        appels["n"] += 1
        raise report.llm.LLMMalformedOutputError("JSON invalide après 2 essais")

    monkeypatch.setattr(report.llm, "chat_json", bafouille)

    # La fonction ENVELOPPE la dernière erreur après épuisement — le type final
    # est donc celui du contrat, pas celui du moteur. Ce qui compte ici est le
    # NOMBRE d'appels : c'est lui qui prouve que le réessai a eu lieu.
    with pytest.raises(report.llm.LLMError, match="après 3 tentatives"):
        report._generer_sous_contrat(
            "instruction", "corps", attendues=2, language="fr",
            provider="mlx", mistral_api_key=None)

    assert appels["n"] == report.TENTATIVES_CONTRAT, (
        "une sortie inexploitable doit bénéficier des tentatives métier")


def test_le_nouveau_type_reste_traduit_en_503():
    """Il hérite de `LLMError`, donc les cinq `except` d'`api/main.py` le
    traduisent en 503 sans rien savoir de lui. Le contraire rendrait un 500 brut
    sur un compte-rendu raté."""
    from core.llm_base import LLMError, LLMMalformedOutputError

    assert issubclass(LLMMalformedOutputError, LLMError)


def test_les_deux_moteurs_levent_le_type_partage():
    """La frontière ne vaut que si les DEUX moteurs la respectent : `report.py`
    n'a qu'un `except`, et il ne nomme aucun moteur."""
    from core import llm_mistral, llm_mlx
    from core.llm_base import LLMMalformedOutputError

    for classe in (llm_mlx._MlxSortieInexploitable,
                   llm_mistral._MistralSortieInexploitable):
        assert issubclass(classe, LLMMalformedOutputError), classe.__name__

    assert not issubclass(llm_mlx.MlxLLMError, LLMMalformedOutputError), (
        "une panne de service ordinaire ne doit PAS être réessayée")


def test_la_temperature_NE_MONTE_PLUS_et_la_graine_est_FIXE(monkeypatch):
    """Règle de `core/report.py` : la politique de reprise a changé de nature.

    Elle montait : 0,1 puis 0,35 puis 0,6. C'était la seule façon de sortir
    d'une ornière tant qu'aucune graine n'était envoyée — MESURÉ : six
    générations consécutives rendaient 2 062 jetons CHACUNE, à l'identique.

    Mais monter la température pour corriger une faute de CARDINALITÉ est
    contre-productif : on rend le modèle plus variable au moment précis où on
    lui demande plus de discipline structurelle. Ce qui varie désormais est
    l'INSTRUCTION, pas le hasard.
    """
    vues: list[tuple] = []

    def refuse(*a, **k):
        vues.append((k.get("temperature"), k.get("seed")))
        raise report.report_citations.StructuredReportError("hors contrat")

    monkeypatch.setattr(report.llm, "chat_json", refuse)

    with pytest.raises(report.llm.LLMError):
        report._generer_sous_contrat(
            "instruction", "corps", attendues=2, language="fr",
            provider="mlx", mistral_api_key=None)

    assert len(vues) == report.TENTATIVES_CONTRAT
    assert {t for t, _ in vues} == {report.TEMPERATURE_CONTRAT}, (
        f"la température ne doit plus varier : {vues}")
    assert {g for _, g in vues} == {report.SEED_CONTRAT}, (
        f"la graine doit être fixe et transmise : {vues}")


def test_chaque_REPRISE_porte_une_CORRECTION_ciblee(monkeypatch):
    """Ce qui change d'une tentative à l'autre, et la seule chose qui peut
    encore récupérer maintenant que le hasard est figé.

    La correction est choisie sur un CODE STABLE, pas sur le message du
    validateur. Réinjecter le message
    ferait de lui une quatrième formulation du contrat — après le prompt, le
    schéma et le validateur — qui dériverait sans que personne ne s'en aperçoive.
    Et un message peut porter un fragment de la sortie GÉNÉRÉE, qu'on ne veut
    surtout pas transformer en consigne.
    """
    instructions: list[str] = []

    def refuse(system, instruction, *a, **k):
        instructions.append(instruction)
        raise report.report_citations.StructuredReportError(
            "peu importe le texte, et c'est le point",
            code=report.report_citations.TROP_DE_PREUVES)

    monkeypatch.setattr(report.llm, "chat_json", refuse)

    with pytest.raises(report.llm.LLMError):
        report._generer_sous_contrat(
            "instruction", "corps", attendues=2, language="fr",
            provider="mlx", mistral_api_key=None)

    assert "CORRECTION" not in instructions[0], (
        "la première tentative part du prompt nominal")
    for reprise in instructions[1:]:
        assert reprise.startswith("CORRECTION"), "la reprise s'annonce en tête"
        assert "primary_evidence" in reprise, (
            "la correction doit nommer la règle enfreinte")
        assert "peu importe le texte" not in reprise, (
            "le message du validateur ne doit JAMAIS être réinjecté")
    assert instructions[1] != instructions[0], (
        "à graine et température figées, un prompt identique rejouerait la "
        "MÊME sortie — la reprise ne récupérerait rien")


def test_un_code_de_refus_INCONNU_reste_actionnable(monkeypatch):
    """Un code sans correction dédiée ne doit pas produire une reprise vide :
    le repli dit au moins « relis le contrat »."""
    monkeypatch.setattr(
        report.llm, "chat_json",
        lambda *a, **k: (_ for _ in ()).throw(
            report.report_citations.StructuredReportError("inconnu")))

    rappel = report._rappel_correctif(
        report.report_citations.StructuredReportError("x", code="INEXISTANT"),
        "fr")

    assert "CORRECTION" in rappel
    assert "contrat de sortie" in rappel


def test_les_deux_moteurs_acceptent_la_temperature():
    """La variation ne vaut que si les DEUX moteurs la respectent : `report.py`
    ne nomme aucun fournisseur."""
    import inspect

    from core import llm_mistral, llm_mlx

    for moteur in (llm_mlx.MlxEngine, llm_mistral.MistralEngine):
        params = inspect.signature(moteur.chat_json).parameters
        assert "temperature" in params, moteur.__name__


def test_une_TRONCATURE_traverse_la_boucle_sans_etre_rejouee(monkeypatch):
    """Règle voulue, et ce n'est PAS un oubli d'héritage.

    `MlxLLMOutputLimitError` ne descend pas de `LLMMalformedOutputError` : une
    réponse coupée à la limite de sortie n'est donc pas reprise ici.

    Ce que cette boucle sait faire, c'est varier la TEMPÉRATURE pour sortir
    d'une sortie mal formée. Or une température plus haute ne raccourcit rien —
    au mieux elle change le texte, au pire elle l'allonge. MESURÉ : trois
    tirages sur le même transcript, trois troncatures, à 427, 417 et 461 s.
    Rejouer aurait triplé l'attente pour le même mur.

    Une troncature se répare en demandant MOINS à la fois, pas en redemandant
    la même chose.
    """
    from core.llm_mlx import MlxLLMOutputLimitError

    appels = {"n": 0}

    def chat_json(*a, **k):
        appels["n"] += 1
        raise MlxLLMOutputLimitError("limite de sortie atteinte")

    monkeypatch.setattr(report.llm, "chat_json", chat_json)

    with pytest.raises(MlxLLMOutputLimitError):
        report._generer_sous_contrat(
            "instruction", "corps", attendues=2, language="fr",
            provider="mlx", mistral_api_key=None, progress=None,
        )

    assert appels["n"] == 1, (
        f"{appels['n']} générations pour un mur qui ne bouge pas d'un essai "
        f"à l'autre")


def test_TOUS_les_appels_au_modele_portent_la_graine(monkeypatch):
    """La reproductibilité est une propriété de la CHAÎNE, pas d'une étape.

    MESURÉ : deux exécutions du même transcript au même commit avec la
    même graine : le prompt du compte-rendu faisait 13 257 jetons dans l'une et
    13 071 dans l'autre, pour 18 affirmations contre 8.

    L'écart venait d'AMONT. La classification de genre et les synthèses
    Map-Reduce passent par `llm.chat`, ne portaient aucune graine, et leur
    sortie entre dans le prompt final. Fixer `chat_json` seul rendait rejouable
    une étape dont l'ENTRÉE variait — ce qui ne se voit pas, et se lit comme du
    bruit du modèle.
    """
    graines_vues: list = []

    def _chat(system, user, **k):
        graines_vues.append(k.get("seed"))
        return "interview"

    def _chat_json(system, user, **k):
        graines_vues.append(k.get("seed"))
        raise report.report_citations.StructuredReportError("hors contrat")

    monkeypatch.setattr(report.llm, "chat", _chat)
    monkeypatch.setattr(report.llm, "chat_json", _chat_json)
    # SANS ce bouchon, `make_report` sonde le moteur — et la sonde DÉMARRE un
    # vrai serveur mlx_lm sur le port de production. Un test unitaire qui laisse
    # un serveur derrière lui fait rougir le témoin anti-fuite de tout le monde.
    monkeypatch.setattr(report.llm, "is_available", lambda **k: True)
    monkeypatch.setattr(report.library, "get_transcript", lambda name: {
        "meta": {"language": "fr", "title": "T"},
        # Assez de lignes pour DÉCLENCHER le Map-Reduce : sous le seuil d'une
        # seule tranche, il ne fait aucun appel et le témoin ne verrait rien.
        "body": "## Transcript\n\n" + "\n".join(
            f"**Speaker 1** [00:{i // 60:02d}:{i % 60:02d}] Ligne {i}."
            for i in range(300)),
    })

    with pytest.raises(report.llm.LLMError):
        report.make_report("peu-importe", provider="mlx")

    assert graines_vues, "aucun appel au modèle n'a été observé"
    assert all(g == report.SEED_CONTRAT for g in graines_vues), (
        f"un appel part sans graine, ou avec une autre : {graines_vues}")


# ---------------------------------------------------------------------------
# Le FILET de réparation — entre l'échec de parsage et la reprise
# ---------------------------------------------------------------------------
#
# Rejoué hors ligne, par ce chemin exactement : la plupart des sorties invalides
# conservées sont rattrapables par UNE opération sur un symbole, et les sorties
# valides ne bougent pas. La question que ces tests ferment n'est plus
# « le réparateur marche-t-il », c'est « la boucle l'appelle-t-elle au bon
# moment, et s'arrête-t-elle quand il réussit ».


TRANSCRIPT_FILET = (
    "## Transcript\n\n**Speaker 1** [00:00:04] L'équipe valide le lancement mardi.\n"
)


def _sortie_du_modele(sections: int = 2) -> str:
    import json as _json
    return _json.dumps({"sections": [
        {"question_id": i, "title": f"Section {i}", "answer_status": "answered",
         "claims": [{"text": f"Affirmation {i}.",
                     "primary_evidence": {
                         "timestamp": "00:00:04",
                         "quote": "L'équipe valide le lancement mardi."},
                     "secondary_evidence": None}]}
        for i in range(1, sections + 1)]}, ensure_ascii=False)


def _moteur_qui_deseque(monkeypatch, brut: str, motif: str = "stop"):
    """Le moteur rend une sortie que le parseur refuse, et compte les appels."""
    appels = {"n": 0}

    def chat_json(*a, **k):
        appels["n"] += 1
        erreur = report.llm.LLMMalformedOutputError(
            "Réponse invalide", brut=brut, finish_reason=motif)
        raise erreur

    monkeypatch.setattr(report.llm, "chat_json", chat_json)
    return appels


def test_une_sortie_REPARABLE_evite_la_reprise(monkeypatch):
    """Le gain qui justifie le filet : une génération épargnée, pas une de plus.

    MESURÉ sur le corpus : une reprise coûte jusqu'à 6 999 jetons et 263 s pour
    ne rien rendre. Si la boucle réparait APRÈS avoir redemandé, le filet ne
    ferait économiser que le troisième appel — c'est-à-dire presque rien.
    """
    brut = _sortie_du_modele()[:-1]                 # `}` finale manquante
    appels = _moteur_qui_deseque(monkeypatch, brut)
    journal: dict = {}

    cr = report._generer_sous_contrat(
        "instruction", TRANSCRIPT_FILET, attendues=2, language="fr",
        provider="mlx", mistral_api_key=None, journal=journal)

    assert appels["n"] == 1, "la reprise a eu lieu alors que la réparation suffisait"
    assert "Affirmation 1." in cr, "le pipeline normal a bien rendu le document"
    assert journal["status"] == "PASS_REPAIRED"
    assert journal["attempt"] == 1
    assert journal["repair"]["applied"] is True
    assert journal["repair"]["operation"] == "insert_closing_brace"
    assert journal["repair"]["original_sha"] != journal["repair"]["repaired_sha"]


def test_une_sortie_TRONQUEE_n_est_pas_reparee_mais_redemandee(monkeypatch):
    """`finish_reason != stop` : le filet s'abstient, la boucle reprend la main.

    Compléter un document coupé reviendrait à en écrire la fin. Le filet ne
    fabrique jamais de contenu — il rééquilibre, ou il se retire.
    """
    brut = _sortie_du_modele()[:-1]
    appels = _moteur_qui_deseque(monkeypatch, brut, motif="length")
    journal: dict = {}

    with pytest.raises(report.llm.LLMError):
        report._generer_sous_contrat(
            "instruction", TRANSCRIPT_FILET, attendues=2, language="fr",
            provider="mlx", mistral_api_key=None, journal=journal)

    assert appels["n"] == TENTATIVES_ATTENDUES, "la boucle a bien repris"
    assert journal["status"] == "FAIL"
    assert journal["repair"]["applied"] is False


def test_un_CR_hors_contrat_ne_passe_PAS_par_le_filet(monkeypatch):
    """Une `StructuredReportError` n'a pas de faute de balance à corriger.

    Le document était parsable : il était seulement hors contrat. Le seul
    remède est de redemander — et le filet doit le laisser passer sans même
    tenter, sous peine de brûler du temps sur chaque refus métier.
    """
    tentatives = {"n": 0}

    def _tenter(*a, **k):
        tentatives["n"] += 1
        return None

    monkeypatch.setattr(report, "_tenter_reparation", _tenter)
    hors_contrat = report_citations.StructuredReportError("hors contrat")
    (cr, appels) = _generer(monkeypatch, [hors_contrat, "# Compte-rendu"])

    assert cr == "# Compte-rendu"
    assert appels["n"] == 2
    # Le filet est SOLLICITÉ (la boucle ne distingue pas les deux erreurs), mais
    # il se retire faute de sortie brute — c'est `_tenter_reparation` qui porte
    # la décision, et il rend `None` sans rien parser.
    assert tentatives["n"] == 1


def test_le_journal_distingue_le_succes_NATIF_du_succes_REPARE(monkeypatch):
    """PASS_DIRECT et PASS_REPAIRED ne se confondent pas. Jamais.

    Règle de vocabulaire (`_generer_sous_contrat`), et elle porte une garde :

        succès sans génération supplémentaire = PASS_DIRECT + PASS_REPAIRED
        succès NATIF du modèle                = PASS_DIRECT seulement

    Les additionner masquerait une dégradation du modèle derrière un filet qui
    travaillerait de plus en plus.
    """
    journal: dict = {}
    monkeypatch.setattr(report.llm, "chat_json", lambda *a, **k: {"ok": 1})
    monkeypatch.setattr(report.report_citations, "render_structured",
                        lambda *a, **k: "# CR")

    report._generer_sous_contrat(
        "i", "c", attendues=2, language="fr", provider="mlx",
        mistral_api_key=None, journal=journal)

    assert journal["status"] == "PASS_DIRECT"
    assert journal["repair"] == {"applied": False}


# ---------------------------------------------------------------------------
# `secondary_evidence` omise — le défaut qui bloquait le CR local
# ---------------------------------------------------------------------------
#
# MESURÉ sur le vrai serveur, vrai média, vrai MLX : le compte-rendu échouait en
# HTTP 503 après TROIS tentatives, toujours sur le même refus —
#
#     clés manquantes dans claim : ['secondary_evidence']
#
# Le code émis était `CLAIM_OFF_CONTRACT`, qui n'avait pas d'entrée dans
# `_CORRECTIONS`. Le modèle recevait donc trois fois la consigne générique
# « relis le contrat de sortie et rends exactement les clés demandées », et
# échouait trois fois. À graine fixe, l'échec est DÉTERMINISTE : ce transcript
# ne pouvait produire aucun document, jamais.
#
# Le contrat n'a pas été assoupli. `secondary_evidence` reste obligatoire — ce
# qui manquait était une consigne disant quoi écrire quand il n'y a pas de
# seconde preuve : `null`, et non rien.


def _claim_sans_secondaire() -> dict:
    """L'omission EXACTE observée en production. Ni plus, ni moins."""
    return {"text": "Le lancement est validé.",
            "primary_evidence": {"timestamp": "00:00:04",
                                 "quote": "L'équipe valide le lancement mardi."}}


def test_omettre_secondary_evidence_porte_son_PROPRE_code() -> None:
    """Un motif générique appelle une consigne générique. D'où un code dédié."""
    charge = {"sections": [{"question_id": 1, "title": "S", "answer_status":
                            "answered", "claims": [_claim_sans_secondaire()]}]}
    with pytest.raises(report_citations.StructuredReportError) as refus:
        report_citations.render_structured(
            charge, TRANSCRIPT_FILET, expected_sections=1, language="fr")

    assert refus.value.code == report_citations.PREUVE_SECONDAIRE_ABSENTE
    assert refus.value.code != report_citations.AFFIRMATION_HORS_CONTRAT, (
        "le code générique était précisément le défaut")


def test_la_correction_DIT_d_écrire_null_plutôt_que_de_relire_le_contrat() -> None:
    """La consigne doit être ACTIONNABLE, pas une invitation à relire.

    C'est toute la différence entre les trois tentatives perdues sur le vrai
    serveur et une reprise qui a une chance d'aboutir.
    """
    refus = report_citations.StructuredReportError(
        "clés manquantes dans claim : ['secondary_evidence']",
        code=report_citations.PREUVE_SECONDAIRE_ABSENTE)
    for langue, attendu in (("fr", "null"), ("en", "null")):
        consigne = report._rappel_correctif(refus, langue)
        assert "secondary_evidence" in consigne
        assert attendu in consigne
        assert consigne != report._rappel_correctif(
            report_citations.StructuredReportError("x", code="INCONNU"), langue), (
            "la correction ne doit pas retomber sur le message générique")


def test_le_champ_reste_OBLIGATOIRE_le_contrat_n_est_pas_assoupli() -> None:
    """Le correctif porte sur la CONSIGNE, jamais sur ce qui est accepté.

    Rendre `secondary_evidence` optionnelle aurait fait disparaître le symptôme
    en supprimant une garantie : la cardinalité des preuves est la STRUCTURE du
    contrat depuis v7, pas une borne cosmétique.
    """
    charge = {"sections": [{"question_id": 1, "title": "S", "answer_status":
                            "answered", "claims": [_claim_sans_secondaire()]}]}
    with pytest.raises(report_citations.StructuredReportError):
        report_citations.render_structured(
            charge, TRANSCRIPT_FILET, expected_sections=1, language="fr")

    # Et `null` explicite reste la forme ACCEPTÉE.
    claim = {**_claim_sans_secondaire(), "secondary_evidence": None}
    rendu = report_citations.render_structured(
        {"sections": [{"question_id": 1, "title": "S", "answer_status":
                       "answered", "claims": [claim]}]},
        TRANSCRIPT_FILET, expected_sections=1, language="fr")
    assert "Le lancement est validé." in rendu


# ---------------------------------------------------------------------------
# Le défaut DÉTERMINISTE — pourquoi la consigne ne suffisait pas, et ce qui suit
# ---------------------------------------------------------------------------
#
# La consigne ciblée était CÂBLÉE juste : bon code, bonne
# instruction, reprise qui aboutissait DÈS QUE le modèle s'y conformait. Mais
# elle a échoué sur le vrai serveur, et la mesure dit pourquoi : la graine est
# fixe et MLX l'honore (`mx.random.seed`), donc d'une tentative à l'autre seul
# le `correctif` change — et il ne déplaçait pas Qwen3-4B sur ce transcript.
# Trois tentatives IDENTIQUES, trois omissions, HTTP 503. L'échec est un POINT
# FIXE, pas un mauvais tirage.
#
# D'où le filet structurel : `secondary_evidence` omise est la SEULE omission de
# clé au sens unique — « pas de seconde preuve » —, et `null` est déjà la valeur
# que le contrat donne à ce sens. On la rend explicite, on RE-valide à
# l'identique, et le journal le DIT (`PASS_NORMALIZED`). Le contrat n'est pas
# assoupli : `valider_structure` refuse toujours l'omission (voir
# `test_le_champ_reste_OBLIGATOIRE...`), le filet vit au-dessus d'elle.


def test_le_défaut_déterministe_complète_secondary_evidence_dès_la_1re_tentative(
        monkeypatch) -> None:
    """Une seule génération, le document sort, et le journal ne le cache pas.

    Le seul comportement qui garantit que le parcours aboutisse sur un modèle
    dont la graine est fixe : ne pas ESPÉRER une tentative différente, la
    RENDRE inutile.
    """
    appels = {"n": 0}

    def _chat_json(system, user, **k):
        appels["n"] += 1
        return {"sections": [{"question_id": 1, "title": "S", "answer_status":
                              "answered", "claims": [_claim_sans_secondaire()]}]}

    monkeypatch.setattr(report.llm, "chat_json", _chat_json)
    journal: dict = {}
    rendu = report._generer_sous_contrat(
        "instruction", TRANSCRIPT_FILET, attendues=1, language="fr",
        provider="mlx", mistral_api_key=None, journal=journal)

    assert "Le lancement est validé." in rendu
    assert appels["n"] == 1, "aucune reprise ne doit être nécessaire"
    # La cible est écrite EN DUR, jamais lue depuis le code testé : un test qui
    # dérive sa cible accepte n'importe quel renommage en silence.
    assert journal["status"] == "PASS_NORMALIZED"
    assert journal["attempt"] == 1
    # Le défaut est TRACÉ, distinct d'une réparation de balance.
    assert journal["normalization"]["applied"] is True
    assert journal["normalization"]["kind"] == "secondary_evidence_defaulted"
    assert journal["normalization"]["claims"] == 1
    assert journal["normalization"]["original_sha"], "l'empreinte AVANT doit exister"
    assert journal["normalization"]["normalized_sha"], "l'empreinte APRÈS doit exister"
    assert (journal["normalization"]["original_sha"]
            != journal["normalization"]["normalized_sha"]), (
        "défaulter la clé DOIT changer l'empreinte")
    # Une réparation de balance N'A PAS eu lieu : les deux filets ne se
    # confondent pas.
    assert journal["repair"] == {"applied": False}


def test_le_filet_ne_MASQUE_pas_une_fondation_absente(monkeypatch) -> None:
    """Défaulter `secondary_evidence` ne doit JAMAIS sauver un défaut de preuve.

    Le contre-exemple qui prouve que le filet est surgical : une affirmation à
    qui il manque AUSSI une `primary_evidence` valide n'est pas masquée — la
    validation la refuse après le défaut, et la boucle redemande. Un filet qui
    laisserait passer ça fabriquerait un compte-rendu non fondé.
    """
    # `primary_evidence: null` + `secondary_evidence` omise : l'ensemble des
    # clés vaut bien {text, primary_evidence}, donc le filet DÉFAUTE la
    # secondaire — mais la fondation nulle reste refusée ensuite.
    charge_bancale = {"sections": [{"question_id": 1, "title": "S",
                      "answer_status": "answered",
                      "claims": [{"text": "Affirmation sans fondation.",
                                  "primary_evidence": None}]}]}
    charge_saine = {"sections": [{"question_id": 1, "title": "S",
                    "answer_status": "answered",
                    "claims": [{**_claim_sans_secondaire(),
                                "secondary_evidence": None}]}]}
    charges = iter([charge_bancale, charge_saine])
    consignes: list[str] = []

    def _chat_json(system, user, **k):
        consignes.append(user)
        return next(charges)

    monkeypatch.setattr(report.llm, "chat_json", _chat_json)
    journal: dict = {}
    rendu = report._generer_sous_contrat(
        "instruction", TRANSCRIPT_FILET, attendues=1, language="fr",
        provider="mlx", mistral_api_key=None, journal=journal)

    assert "Le lancement est validé." in rendu
    # La 1re tentative a été REDEMANDÉE : le filet n'a pas su la sauver, et c'est
    # bien — une fondation nulle n'a pas de valeur par défaut honnête.
    assert journal["attempt"] == 2
    assert journal["status"] == "PASS_RETRY"
    # Et la reprise est guidée par la VRAIE faute — la preuve principale absente,
    # pas la clé secondaire déjà défaultée. Un correctif qui redemanderait
    # `secondary_evidence: null` désignerait un problème résolu au lieu du réel.
    correction_reprise = consignes[1]
    assert "preuve principale" in correction_reprise, correction_reprise
    assert "secondary_evidence" not in correction_reprise, (
        "le correctif ne doit PAS parler de la clé déjà défaultée")
