"""Génération de comptes-rendus (CR) structurés à partir d'un transcript.

Appel LLM **direct** (pas d'agent : on connaît le transcript). Le CR est
sauvegardé comme un `.md` séparé dans output/ (type: report) → il apparaît dans
la Bibliothèque, est cherchable et indexé pour le RAG.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Callable, Optional

from . import (
    factcheck, library, llm, llm_base, names, provenance, quality,  # noqa: F401
    report_citations,  # noqa: F401
    reparation_json, resources, semantic, templates, writer_md,  # noqa: F401
)
from . import config
from . import docid
from . import messages
from . import sensitivity

Progress = Callable[[float, str], None]

REPORT_MAX_TOKENS = 6144

# Température FIXE, pour toutes les tentatives.
#
# Elle MONTAIT — 0,1 puis 0,35 puis 0,6 — parce que sans graine ni changement de
# prompt, rejouer rendait une sortie rigoureusement identique et la boucle ne
# récupérait rien. C'était traiter le symptôme.
#
# Monter la température pour corriger une faute de CARDINALITÉ est
# contre-productif : on rend le modèle plus variable au moment où on lui demande
# plus de discipline structurelle. Ce qui varie
# désormais est l'INSTRUCTION — voir `_rappel_correctif`.
TEMPERATURE_CONTRAT = 0.1

# Graine FIXE : deux exécutions du même prompt rendent la même sortie.
#
# Règle, et elle coûte quelque chose qu'il faut dire : un
# document qui échoue échouera À L'IDENTIQUE. C'est voulu. Sans graine, le même
# transcript a échoué sur TROIS modes distincts en trois tirages — troncature,
# JSON tronqué, cardinalité — et aucune mesure comparant deux modèles ne voulait
# plus rien dire.
#
# La reprise reste capable de récupérer parce qu'elle change le PROMPT, pas la
# température : même graine, autre instruction, autre sortie.
SEED_CONTRAT = config.REPORT_SEED

# Système BILINGUE (comme profiling) : un système tout-français ferait dériver la
# sortie en français même pour un transcript anglais. La langue de sortie est
# fixée par la directive ci-dessous, écrite DANS la langue cible (ancrage fort).
_SYSTEM = (
    "Tu réponds à des questions sur une transcription. / You answer questions "
    "about a transcript. Réponds UNIQUEMENT aux questions posées dans l'objet "
    "JSON demandé. / Answer ONLY the questions asked in the requested JSON "
    "object. Écris TOUT — titres compris — dans la langue demandée par "
    "l'instruction ci-dessous (celle de la transcription). / Write EVERYTHING — "
    "headings included — in the language requested by the instruction below "
    "(the transcript's language). "
    # Un CR n'est pas un texte suivi : AutoMin exclut délibérément la cohérence
    # textuelle de son barème. Demander un document rédigé produit une narration,
    # et une narration réclame une fin — que le modèle fabrique.
    "N'ajoute NI introduction, NI conclusion, NI question non demandée. / Add NO "
    "introduction, NO conclusion, NO question that was not asked for. "
    "Quand une question n'a pas de réponse dans la transcription, dis-le "
    "explicitement plutôt que de meubler. / When a question has no answer in the "
    "transcript, say so explicitly rather than padding. "
    "Base-toi UNIQUEMENT sur la transcription, n'invente rien / Rely ONLY on the "
    "transcript, invent nothing. Sois concis et factuel / Be concise and factual."
)

# Classifieur de genre : liste FERMÉE, réponse d'un seul mot, vérifiée en code.
# Choisir dans un menu est bien plus fiable pour un modèle que d'inventer un
# libellé — et `templates.normalize` fait retomber tout écart sur le gabarit sobre.
_CLASSIFY_SYSTEM = (
    "Tu classes un enregistrement dans une catégorie. / You classify a recording "
    "into a category. Réponds par UN SEUL mot de la liste, sans ponctuation ni "
    "explication. / Reply with ONE word from the list, no punctuation, no "
    "explanation. En cas de doute, réponds « generique ». / When in doubt, reply "
    "\"generique\"."
)

# Directive de langue de sortie ÉCRITE DANS LA LANGUE CIBLE (cf. profiling) : une
# consigne dans la langue voulue ancre réellement la sortie du modèle, titres de
# sections compris. Elle couvre AUSSI l'impératif final (dans la même langue).
_REPORT_DIRECTIVE = {
    "en": "CRITICAL — write the ENTIRE report in ENGLISH: the title, every section "
          "heading and all body text. Do NOT use French. Now write the structured "
          "report of the recording below.",
    "fr": "IMPÉRATIF — rédige TOUT le compte-rendu en FRANÇAIS : le titre, chaque "
          "titre de section et tout le corps. Rédige maintenant le compte-rendu "
          "structuré de l'enregistrement ci-dessous.",
    "es": "IMPORTANTE — redacta TODO el informe en ESPAÑOL: el título, cada "
          "encabezado de sección y todo el cuerpo. Redacta ahora el informe "
          "estructurado de la grabación siguiente.",
    "de": "WICHTIG — schreibe den GESAMTEN Bericht auf DEUTSCH: den Titel, jede "
          "Abschnittsüberschrift und den gesamten Text. Schreibe jetzt den "
          "strukturierten Bericht der folgenden Aufnahme.",
    "it": "IMPORTANTE — scrivi TUTTO il resoconto in ITALIANO: il titolo, ogni "
          "titolo di sezione e tutto il corpo. Scrivi ora il resoconto "
          "strutturato della registrazione seguente.",
    "pt": "IMPORTANTE — redija TODO o relatório em PORTUGUÊS: o título, cada "
          "cabeçalho de seção e todo o corpo. Redija agora o relatório "
          "estruturado da gravação a seguir.",
    "nl": "BELANGRIJK — schrijf het VOLLEDIGE verslag in het NEDERLANDS: de titel, "
          "elke sectiekop en de volledige tekst. Schrijf nu het gestructureerde "
          "verslag van de onderstaande opname.",
}


def _report_directive(language: str | None) -> str:
    if language:
        d = _REPORT_DIRECTIVE.get(language.lower())
        if d:
            return d
        return (
            f"CRITICAL — write the ENTIRE report (title, section headings and body) "
            f"in the transcript's language ('{language}'). Now write the structured "
            "report of the recording below."
        )
    return (
        "CRITICAL — write the ENTIRE report (title, section headings and body) in "
        "the SAME language as the transcript below. Now write the structured report "
        "of the recording."
    )


# Combien de fois on redemande un compte-rendu au modèle quand sa réponse sort
# du contrat. Trois tentatives de CONTRAT, donc deux réessais métier.
#
# SEUIL PROVISOIRE, et il faut le dire ainsi. Avec des réessais, les échecs de
# contrat d'un même transcript chez Mistral deviennent rares. Ce que ce n'est
# PAS : une preuve d'indépendance des tirages, ni un taux valable sur un autre
# transcript ou sur MLX. Le chiffre 3 vient d'une projection qui suppose les
# tirages indépendants ; l'observation est cohérente avec elle sans la démontrer.
#
# À remesurer si le fournisseur par défaut change, ou si un motif de refus
# nouveau apparaît. Le budget vit en un seul endroit pour ça.
#
# COÛT MAXIMAL RÉEL : 6 appels au modèle, pas 3. Chaque tentative de contrat
# appelle `chat_json(retries=1)`, qui peut lui-même reprendre une fois sur un
# JSON mal formé. Le plafond est assumé et borné par un test.
TENTATIVES_CONTRAT = 3


# Une correction CONCISE par code de refus, dans les deux langues.
#
# Règle : ne pas coller le texte de l'exception.
# Il peut être long, technique, et porter un fragment de la sortie générée — le
# réinjecter ferait de lui une QUATRIÈME formulation du contrat, après le
# prompt, le schéma et le validateur, et elle dériverait sans que personne ne
# s'en aperçoive.
#
# Un code stable, une consigne courte, et rien du contenu produit.
_CORRECTIONS = {
    report_citations.TROP_DE_PREUVES: (
        "une affirmation portait plus de preuves que la structure n'en admet. "
        "Chaque affirmation a EXACTEMENT une `primary_evidence` et AU PLUS une "
        "`secondary_evidence`, qui vaut `null` si tu n'en as pas. Aucune clé "
        "`evidence` n'existe.",
        "a claim carried more evidence than the structure allows. Each claim "
        "has EXACTLY one `primary_evidence` and AT MOST one "
        "`secondary_evidence`, which is `null` if you have none. There is no "
        "`evidence` key.",
    ),
    report_citations.PREUVE_PRINCIPALE_ABSENTE: (
        "une affirmation était sans preuve principale. Toute affirmation "
        "retenue porte une `primary_evidence` citée mot pour mot. Si tu n'as "
        "pas de preuve, n'écris pas l'affirmation.",
        "a claim had no primary evidence. Every kept claim carries a "
        "`primary_evidence` quoted verbatim. If you have no evidence, do not "
        "write the claim.",
    ),
    report_citations.PREUVE_SECONDAIRE_ABSENTE: (
        "une affirmation omettait la clé `secondary_evidence`. Cette clé est "
        "OBLIGATOIRE dans chaque affirmation : écris `\"secondary_evidence\": "
        "null` quand tu n'as pas de seconde preuve. Ne la supprime jamais.",
        "a claim omitted the `secondary_evidence` key. That key is MANDATORY "
        "in every claim: write `\"secondary_evidence\": null` when you have no "
        "second piece of evidence. Never drop it.",
    ),
    report_citations.SANS_REPONSE_MAIS_AFFIRME: (
        "une section marquée `not_found` portait des affirmations. Une section "
        "sans réponse a `\"claims\": []`, et rien d'autre.",
        "a section marked `not_found` carried claims. A section with no answer "
        "has `\"claims\": []`, nothing else.",
    ),
    report_citations.SECTION_INCONNUE: (
        "les `question_id` ne couvraient pas exactement les questions posées. "
        "Rends une section par question, numérotée de 1 à N, chacune une seule "
        "fois.",
        "the `question_id` values did not cover the asked questions exactly. "
        "Return one section per question, numbered 1 to N, each exactly once.",
    ),
}
_CORRECTION_PAR_DEFAUT = (
    "la structure attendue n'était pas respectée. Relis le contrat de sortie "
    "et rends exactement les clés demandées, sans en ajouter ni en omettre.",
    "the expected structure was not respected. Re-read the output contract and "
    "return exactly the requested keys, adding none and omitting none.",
)


def _rappel_correctif(erreur: Exception, language: str | None) -> str:
    """L'erreur observée, rendue au modèle comme une CONSIGNE — pas du hasard.

    ## Ce que cette fonction remplace

    La boucle rejouait le MÊME prompt en montant la température : 0,1 puis 0,35
    puis 0,6. C'était la seule façon de sortir d'une ornière tant qu'aucune
    graine n'était envoyée — sans elle, six générations identiques coûtaient
    sept minutes pour le même échec.

    Mais monter la température pour corriger une faute de CARDINALITÉ est
    contre-productif : on rend le modèle plus variable au moment précis où on
    lui demande plus de discipline structurelle.

    Désormais la température ne bouge plus, la graine est fixe, et c'est
    l'INSTRUCTION qui change.

    ## Pourquoi un code, et pas le message du validateur

    Le message peut être long, technique, et porter un fragment de la sortie
    générée. Le réinjecter ferait dépendre le prompt de ce que le modèle vient
    d'écrire, et ferait de ce texte une quatrième spécification instable.
    """
    code = getattr(erreur, "code", None)
    fr = (language or "fr").lower() == "fr"
    correction = _CORRECTIONS.get(code, _CORRECTION_PAR_DEFAUT)[0 if fr else 1]
    if fr:
        return (
            "CORRECTION — ta réponse précédente a été REFUSÉE : "
            f"{correction} Régénère le document ENTIER. Ne rends ni excuse ni "
            "commentaire, seulement l'objet JSON demandé.\n\n"
        )
    return (
        "CORRECTION — your previous answer was REJECTED: "
        f"{correction} Regenerate the WHOLE document. Return no apology and no "
        "commentary, only the requested JSON object.\n\n"
    )


def _tenter_reparation(
    exc: Exception,
    body: str,
    *,
    attendues: int,
) -> reparation_json.Reparation | None:
    """Le filet syntaxique, entre l'échec de parsage et la reprise LLM.

    Rend `None` quand l'erreur ne porte pas de sortie exploitable — un moteur
    qui n'attache rien, ou un refus métier (la structure était lisible, elle
    était seulement hors contrat : il n'y a rien à rééquilibrer).

    ## Le nombre de sections vient du GABARIT, jamais de la sortie

    Règle, et elle ferme un vrai trou : le prototype
    déduisait `sections_attendues` du plus grand `question_id` produit. Une
    sortie ayant oublié sa dernière section aurait alors été jugée conforme à
    ses propres manques. `attendues` descend ici depuis `len(asked)`, comme
    pour toute autre validation.
    """
    brut = getattr(exc, "brut", "") or ""
    if not brut.strip():
        return None

    def valider(charge: object) -> object:
        # La validation SEULE — pas le rendu, qui porterait la sélection. Le
        # réparateur ne doit jamais décider qu'une affirmation est bonne ou
        # mauvaise.
        return report_citations.valider_structure(
            charge, body, expected_sections=attendues)

    return reparation_json.reparer(
        brut, valider, finish_reason=getattr(exc, "finish_reason", None))


def _generer_sous_contrat(
    instruction: str,
    body: str,
    *,
    attendues: int,
    language: str | None,
    provider: str,
    mistral_api_key: Optional[str],
    progress: Optional[Progress] = None,
    journal: Optional[dict] = None,
    envoi: sensitivity.Envois = None,
) -> str:
    """Génère un compte-rendu qui HONORE le contrat, ou lève après N tentatives.

    Le défaut que cette fonction ferme : la validation du contrat vivait APRÈS
    l'appel au modèle, donc hors de toute boucle de réessai. Le paramètre
    `retries` de `chat_json` ne couvre que le JSON INVALIDE — les trois moteurs
    le documentent ainsi. Une réponse bien formée mais hors contrat n'avait donc
    **aucune** seconde chance : une déviation passagère du modèle coûtait le
    compte-rendu, définitivement.

    Mesuré, puis quantifié : un même transcript échoue puis réussit au
    tirage suivant, sans que rien ne change. Chaque exécution était un tirage
    unique, sans filet.

    On ne relâche PAS le contrat : le validateur a raison de refuser un CR dont
    les sections ne portent pas ce qu'elles promettent. C'est l'absence de
    seconde chance qui était fautive.

    ## L'ordre, et pourquoi la réparation passe AVANT la reprise

    ::

        sortie brute → parse JSON → si échec et finish_reason=stop
                     → réparation d'UNE opération → parse → validation métier
                     → acceptation ; sinon seulement, reprise LLM

    Une reprise coûte une génération entière — MESURÉ, jusqu'à 6 999 jetons et
    263 s pour ne rien rendre. Le filet coûte quelques dizaines de
    millisecondes, et n'accepte que ce qui franchit EXACTEMENT les mêmes
    validations que toute autre sortie. Le tenter en second serait payer le
    plein tarif avant d'essayer le gratuit.

    `journal`, s'il est fourni, reçoit l'issue (`PASS_DIRECT`,
    `PASS_REPAIRED`, `PASS_NORMALIZED`, `PASS_RETRY`, `FAIL`), le rang de la
    tentative et le détail de la réparation. Un dictionnaire en sortie plutôt
    qu'un tuple : l'appelant qui ne veut que le Markdown n'a rien à changer.

    Il porte DEUX filets, comptés séparément pour qu'aucun ne masque l'autre :
    `repair` (une faute de balance JSON réparée → `PASS_REPAIRED`) et
    `normalization` (une clé `secondary_evidence` omise, défaultée à `null` →
    `PASS_NORMALIZED`). Chacun vaut `{"applied": False}` quand il n'a pas servi.
    Un défaut de clé n'est pas une réparation de syntaxe, et les distinguer,
    c'est voir lequel travaille — donc une dérive du modèle plutôt que « ça
    passe ». Voir `core/report_citations.normaliser_preuve_secondaire`.

    Il reçoit aussi `envelope` : l'enveloppe Markdown que le moteur a dû
    retirer avant de parser, ou `None`. Ce champ ne dit RIEN de l'issue — un
    déballage réussi reste `PASS_DIRECT`, retirer une enveloppe n'étant pas
    corriger une faute de structure. Il est là parce que sa fréquence est un
    signal, et qu'un `PASS_DIRECT` muet le perdait entièrement. Voir
    `core/llm_base.retirer_enveloppe_markdown`.
    """
    derniere: Exception | None = None
    correctif = ""
    # La dernière charge PARSÉE, retenue hors de la boucle pour que le filet
    # structurel puisse la défaulter dans le `except`. Elle n'est lue QUE sur un
    # refus `PREUVE_SECONDAIRE_ABSENTE`, code que seul `render_structured` lève,
    # donc APRÈS un `chat_json` réussi de la MÊME tentative : jamais une valeur
    # rémanente d'un tour précédent.
    structured: object = None
    reparations: list[dict] = []
    # Une durée PAR TENTATIVE, et pas seulement un total.
    #
    # Motif : sur une campagne Mistral, la latence
    # médiane valait 24 s et le maximum 105 s, sans qu'on puisse dire ce que
    # coûtait la reprise elle-même. Un total ne distingue pas « le service a
    # été lent » de « il a fallu redemander », et ce sont deux problèmes qui
    # appellent deux réponses.
    #
    # Ce n'est PAS le temps bout en bout de l'endpoint : ni le profilage, ni le
    # Map-Reduce, ni l'écriture n'entrent ici. Uniquement l'appel de génération
    # du compte-rendu, boucle interne du moteur comprise — et c'est justement ce
    # qu'on veut isoler.
    durees: list[dict] = []

    def _noter(statut: str, tentative: int, reparation=None,
               normalisation=None, ancrage=None) -> None:
        if journal is None:
            return
        journal.update(status=statut, attempt=tentative,
                       repair=(reparation.journal() if reparation
                               else {"applied": False}),
                       # Un champ À PART du `repair` : le défaut d'une clé omise
                       # (`PASS_NORMALIZED`) n'est pas une réparation de balance
                       # (`PASS_REPAIRED`), et les confondre masquerait lequel
                       # des deux filets travaille. Voir `reparation_json`.
                       normalization=(normalisation.journal() if normalisation
                                      else {"applied": False}),
                       # TROISIÈME filet, encore à part : l'ancrage d'une citation
                       # verbatim sur son horodatage canonique. Ni syntaxe, ni clé
                       # omise — le pointeur temporel d'une preuve. Rempli par
                       # `render_structured`. Voir `report_citations._ancrer_preuve`.
                       citation_anchoring=(ancrage or {"applied": False}),
                       repairs_attempted=reparations,
                       attempts=durees,
                       generation_seconds=round(
                           sum(d["seconds"] for d in durees), 1),
                       # Une enveloppe retirée ne change pas l'issue — un
                       # déballage réussi reste PASS_DIRECT — mais elle cesse
                       # d'être invisible. Sans ce champ, un modèle qui se met
                       # à baliser ses sorties ne se distinguerait pas d'un
                       # modèle sain, et la dérive ne se verrait qu'au jour où
                       # la clôture arrive tronquée.
                       envelope=llm_base.enveloppe_observee() or None)

    for tentative in range(1, TENTATIVES_CONTRAT + 1):
        try:
            # Le relevé d'enveloppe appartient à CETTE tentative. Sans cet
            # oubli, celle de la précédente lui serait attribuée, et un modèle
            # qui a cessé d'en produire paraîtrait continuer.
            llm_base.oublier_enveloppe_observee()
            _depart = time.monotonic()
            structured = llm.chat_json(
                _SYSTEM,
                correctif + instruction,
                provider=provider,
                api_key=llm.cle_mistral(provider, mistral_api_key),
                max_tokens=REPORT_MAX_TOKENS,
                schema=report_citations.output_schema(attendues),
                # Le premier réessai du JSON MAL FORMÉ reste chez le moteur :
                # une virgule manquante ne doit pas coûter le cycle métier
                # complet. Quand SA boucle s'épuise, la nôtre prend le relais —
                # voir le `except` ci-dessous.
                retries=1,
                # Température FIXE et graine FIXE : ce qui change d'une
                # tentative à l'autre est le `correctif` en tête d'instruction,
                # pas le hasard. Voir `_rappel_correctif`.
                temperature=TEMPERATURE_CONTRAT,
                seed=SEED_CONTRAT,
                envoi=envoi,
            )
            durees.append({"attempt": tentative,
                           "seconds": round(time.monotonic() - _depart, 1),
                           "outcome": "parsed"})
            _ancrage: dict = {}
            rendu = report_citations.render_structured(
                structured, body, expected_sections=attendues, language=language,
                journal=_ancrage,
            )
            _noter(reparation_json.PASS_DIRECT if tentative == 1
                   else reparation_json.PASS_RETRY, tentative,
                   ancrage=_ancrage.get("citation_anchoring"))
            return rendu
        # Deux échecs, une seule réponse : REDEMANDER. Le modèle a répondu dans
        # les deux cas, mal formé ou hors contrat, et MESURÉ, il change de mode
        # d'une exécution à l'autre — donc redemander a de vraies chances.
        #
        # Une panne de SERVICE, elle, reste hors de ce `except` : elle rendrait
        # la même panne et triplerait l'attente. Avec `MLX_TIMEOUT` à 900 s,
        # cela ferait quarante-cinq minutes avant d'annoncer l'indisponibilité.
        # C'est `LLMMalformedOutputError` qui porte cette frontière, et rien
        # d'autre — `llm.LLMError` tout court l'effacerait.
        #
        # ## La TRONCATURE est du même côté que la panne de service, et ce n'est
        # ## pas un oubli
        #
        # `MlxLLMOutputLimitError` n'hérite pas de `LLMMalformedOutputError` :
        # une réponse coupée à la limite de sortie n'est donc PAS rejouée ici.
        # C'est délibéré, et mesuré.
        #
        # Ce que cette boucle sait faire, c'est varier la température pour sortir
        # d'une sortie mal formée. Or une température plus HAUTE ne raccourcit
        # rien — au mieux elle change le texte, au pire elle l'allonge. MESURÉ :
        # trois tirages sur le même transcript, trois troncatures, à 427,
        # 417 et 461 s. Rejouer aurait triplé l'attente pour le même mur.
        #
        # La troncature ne se répare pas en redemandant la même chose : elle se
        # répare en demandant MOINS à la fois. Voir l'entrée « génération par
        # section » de la feuille de route — c'est le vrai correctif, et il
        # change la forme de l'appel, pas son nombre.
        except (report_citations.StructuredReportError,
                llm.LLMMalformedOutputError) as exc:
            derniere = exc
            # La tentative qui ÉCHOUE est chronométrée elle aussi : c'est même
            # la plus intéressante, puisque c'est elle qu'une reprise fait
            # payer deux fois. `outcome` distingue un refus de CONTRAT — le
            # document était lisible — d'une sortie qu'on n'a pas su parser.
            durees.append({
                "attempt": tentative,
                "seconds": round(time.monotonic() - _depart, 1),
                "outcome": ("contract_refused"
                            if isinstance(exc, report_citations.StructuredReportError)
                            else "unparsable")})

            # LE FILET, avant la reprise. Il ne se déclenche que sur une sortie
            # que le moteur a jugée COMPLÈTE et qui n'a pas su se parser : une
            # `StructuredReportError` n'attache aucun `brut`, donc `None`, donc
            # on redemande — ce qui est juste, un document lisible mais hors
            # contrat n'a pas de faute de balance à corriger.
            reparation = _tenter_reparation(exc, body, attendues=attendues)
            if reparation is not None:
                reparations.append(reparation.journal())
            if reparation is not None and reparation.reparee:
                # Le pipeline NORMAL reprend ici : même rendu, même sélection,
                # même audit ensuite. Le réparateur n'a rendu qu'un objet
                # contractuellement valide, il n'a rien décidé d'autre.
                _ancrage = {}
                rendu = report_citations.render_structured(
                    reparation.charge, body,
                    expected_sections=attendues, language=language,
                    journal=_ancrage)
                _noter(reparation_json.PASS_REPAIRED, tentative, reparation,
                       ancrage=_ancrage.get("citation_anchoring"))
                return rendu

            # LE SECOND FILET, structurel, et pour UN cas unique : la clé
            # `secondary_evidence` omise alors qu'elle est la SEULE qui manque.
            #
            # Pourquoi un défaut DÉTERMINISTE plutôt qu'une énième consigne — et
            # c'est la mesure qui tranche, pas une préférence. La graine est
            # fixe et MLX l'honore (`mx.random.seed`), la température aussi :
            # d'une tentative à l'autre, SEUL le `correctif` change. Or MESURÉ
            # sur le vrai serveur, ce correctif ne déplace pas Qwen3-4B sur
            # ce transcript — il omet la clé trois fois de suite. L'échec n'est
            # donc pas un mauvais tirage qu'un réessai corrigerait : c'est un
            # POINT FIXE. Seule une reprise déterministe garantit que le parcours
            # aboutisse, et `null` est déjà la valeur que le contrat donne à
            # « pas de seconde preuve » — on la rend explicite, on n'invente rien.
            #
            # Le contrat n'est PAS assoupli : `valider_structure` REFUSE
            # toujours l'omission. Ce filet vit AU-DESSUS d'elle, comme la
            # réparation de balance, se compte à part (`PASS_NORMALIZED`) et se
            # prouve par empreinte — la dérive du modèle reste visible.
            if (getattr(exc, "code", None)
                    == report_citations.PREUVE_SECONDAIRE_ABSENTE
                    and structured is not None):
                normalisation = report_citations.normaliser_preuve_secondaire(
                    structured)
                if normalisation.appliquee:
                    _ancrage = {}
                    try:
                        rendu = report_citations.render_structured(
                            normalisation.charge, body,
                            expected_sections=attendues, language=language,
                            journal=_ancrage)
                    except (report_citations.StructuredReportError,
                            llm.LLMMalformedOutputError) as exc_apres:
                        # Le défaut n'a pas suffi : une AUTRE faute subsiste
                        # (une fondation absente ailleurs, par exemple). On
                        # redemande — mais guidé par la VRAIE faute bloquante,
                        # pas par la clé déjà défaultée. Sinon le `correctif` et
                        # le FAIL final désigneraient un problème RÉSOLU au lieu
                        # du problème réel. On ne masque que ce qu'on sait
                        # défaire seul.
                        exc = exc_apres
                        derniere = exc_apres
                    else:
                        _noter(reparation_json.PASS_NORMALIZED, tentative,
                               normalisation=normalisation,
                               ancrage=_ancrage.get("citation_anchoring"))
                        return rendu

            correctif = _rappel_correctif(exc, language)
            if tentative < TENTATIVES_CONTRAT and progress:
                # L'utilisateur doit savoir qu'on redemande : une génération de
                # plus, c'est de l'attente en plus, et une barre qui n'avance
                # pas sans explication se lit comme un blocage.
                #
                # La valeur AVANCE, elle ne repart pas de zéro. Première
                # version : `progress(0.0, …)`. Or cette progression est mise à
                # l'échelle par l'appelant sur `[0,80 ; 0,98]`, et la
                # classification a déjà rendu `0.50` — la barre revenait donc de
                # ~89 % à 80 % à chaque réessai.
                # Une progression qui recule se lit comme un travail refait
                # depuis le début.
                progress(
                    0.50 + 0.10 * tentative,
                    messages.compte_rendu_hors_contrat(
                        str(exc), tentative, TENTATIVES_CONTRAT),
                )

    _noter(reparation_json.FAIL, TENTATIVES_CONTRAT)
    raise llm.LLMError(
        f"Réponse de compte-rendu hors contrat après {TENTATIVES_CONTRAT} "
        f"tentatives : {derniere}"
    ) from derniere


def demote_headings(markdown: str) -> str:
    """Rétrograde les titres de niveau 1 du modèle en niveau 2.

    Le gabarit écrit déjà un `#` (« Compte-rendu — <titre> ») et le modèle en
    produit un autre : le document sortait avec deux H1 consécutifs. On
    rétrograde plutôt que de supprimer, car l'intitulé du modèle est souvent plus
    informatif que le nôtre — et la hiérarchie des titres reste valide.

    Les blocs de code sont laissés intacts : un `#` y est un commentaire, pas un
    titre.
    """
    out: list[str] = []
    in_fence = False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence and line.startswith("# "):
            line = "#" + line
        out.append(line)
    return "\n".join(out)


def spoken_markdown(body: str) -> str:
    """Isole la source prononcée en conservant locuteurs et horodatages.

    Un transcript récent contient avant ``## Transcript`` un profil rédigé par
    un LLM. Ce profil aide à choisir le gabarit, mais ne doit jamais être remis
    au générateur comme s'il s'agissait d'une parole source. Les anciens fichiers
    sans section explicite sont déjà constitués uniquement de leur transcript.
    """
    lines = body.splitlines()
    for index, raw in enumerate(lines):
        line = raw.strip()
        if line.startswith("#") and "transcript" in line.lower():
            return "\n".join(lines[index + 1:]).strip()
    return body.strip()


def profile_excerpt(body: str, *, limit: int = 1400) -> tuple[str, bool]:
    """(extrait, profilé) — l'entrée du classifieur et la force de la preuve.

    Le bloc de profil est court et dense (type + résumé + thèmes), donc bien
    moins cher que le transcript entier. Il est lu depuis le `.md` plutôt que du
    front-matter : ça marche aussi sur les transcripts déjà écrits, sans migration.

    Le second élément dit si l'on a vraiment un profil. Le repli — début du texte
    prononcé — est une preuve nettement plus faible, et l'appelant en tient
    compte en restreignant les genres proposés.
    """
    # Le bloc de profil doit être RECONNU, pas déduit de « ce qui précède le
    # transcript » : les .md d'avant le profilage n'ont ni titre « Profil » ni
    # titre « Transcript », et cette heuristique y avalait tout le transcript en
    # le prenant pour un profil — donc annonçait une preuve forte qui n'existait pas.
    head: list[str] = []
    in_profile = False
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            low = line.lower()
            if "transcript" in low:
                break
            in_profile = "profil" in low or "profile" in low
            continue
        if in_profile and line:
            head.append(line)
    if head:
        return "\n".join(head)[:limit], True
    spoken = quality.transcript_texts(body)
    return " ".join(spoken)[:limit], False


def classify_genre(
    body: str,
    language: str | None,
    *,
    num_ctx: int = 2048,
    provider: str = llm.FOURNISSEUR_DEFAUT,
    mistral_api_key: Optional[str] = None,
    envoi: sensitivity.Envois = None,
) -> str:
    """Genre du gabarit. Toute défaillance retombe sur le gabarit sobre.

    Le classifieur ne peut pas casser la génération du CR : une erreur LLM, une
    réponse hors liste ou un profil absent donnent `GENERIC`, c'est-à-dire un
    compte-rendu moins riche — jamais un compte-rendu inventif.

    `provider`/`mistral_api_key` : le classifieur DOIT viser le même moteur que la
    génération. Sans eux, un run Mistral classait via Ollama (défaut) — indisponible
    chez un utilisateur Mistral-only, donc `except` → gabarit générique EN SILENCE,
    alors que le CR lui-même se générait bien via Mistral.
    """
    excerpt, profiled = profile_excerpt(body)
    if not excerpt.strip():
        return templates.GENERIC
    prompt = (
        "Catégories possibles / possible categories : "
        f"{templates.genre_list(commitments=profiled)}\n\n"
        f"Enregistrement / Recording :\n{excerpt}\n\n"
        "Catégorie ? / Category?"
    )
    try:
        # La graine porte la reproductibilité de la CHAÎNE, pas d'une étape :
        # la classification décide du gabarit, donc du nombre de sections, donc
        # de tout le reste. La laisser libre rendait le compte-rendu final
        # irrejouable même avec `chat_json` fixé.
        raw = llm.chat(
            _CLASSIFY_SYSTEM, prompt, provider=provider,
            api_key=llm.cle_mistral(provider, mistral_api_key), seed=SEED_CONTRAT,
            envoi=envoi,
        )
    except sensitivity.EnvoiRefuse:
        # Un refus de la règle d'envoi n'est pas une panne à absorber : le
        # gabarit sobre laisserait croire que le texte pouvait partir.
        raise
    except Exception:  # noqa: BLE001 — le CR doit rester possible sans classifieur
        return templates.GENERIC
    genre = templates.normalize(raw)
    # Deuxième barrière, vérifiée en code : sans profil on juge sur du texte brut,
    # preuve trop faible pour engager un gabarit qui réclame décisions et actions.
    if not profiled and genre in templates.COMMITMENT_GENRES:
        return templates.GENERIC
    return genre


def _bindings_notice(retirees: list[str], language: str | None) -> str:
    """Bloc signalant les liaisons « Speaker N (Nom) » retirées du CR.

    Ici on RETIRE, là où `_numbers_notice` se contente d'avertir — et la
    différence est délibérée. Un chiffre douteux reste lisible et vérifiable par
    le lecteur ; une identité fausse accolée à une étiquette se propage, se cite
    et se recopie. Le nom lui-même est conservé partout ailleurs dans le texte :
    seule la liaison saute.
    """
    if not retirees:
        return ""
    liste = ", ".join(f"**{b}**" for b in dict.fromkeys(retirees))
    if (language or "fr").lower() == "fr":
        return (
            "\n---\n\n> ℹ️ **Identités retirées :** {liste}.\n> Le modèle a rattaché "
            "un nom à une étiquette de locuteur. Nos étiquettes viennent de la "
            "diarisation, qui découpe les VOIX — elle sur-segmente et ne sait pas "
            "qui est qui.\n> Les noms restent cités dans le texte ; seul le "
            "rattachement a été retiré.\n".replace("{liste}", liste)
        )
    return (
        "\n---\n\n> ℹ️ **Identities removed:** {liste}.\n> The model attached a name "
        "to a speaker label. Our labels come from diarization, which segments "
        "VOICES — it over-segments and does not know who is who.\n> The names "
        "remain in the text; only the attachment was removed.\n".replace("{liste}", liste)
    )


def _numbers_notice(flagged: list[str], language: str | None) -> str:
    """Bloc d'avertissement ajouté au CR quand des nombres ne sont pas sourcés.

    Ajouté, jamais substitué : on ne réécrit pas la sortie du modèle. Le
    lecteur voit le chiffre ET le doute, et tranche lui-même.
    """
    if not flagged:
        return ""
    liste = ", ".join(f"**{n}**" for n in flagged)
    if (language or "fr").lower() == "fr":
        return (
            "\n---\n\n> ⚠️ **À vérifier — chiffres non trouvés dans le transcript :** "
            f"{liste}.\n> Ils ne sont prononcés nulle part dans l'enregistrement. Le "
            "modèle a pu les inventer — ou les **déduire** correctement de ce qui est "
            "dit (un score intermédiaire, un total).\n> Ce contrôle ne détecte que les "
            "nombres **absents** de la source : ni ceux qui y figurent mais sont **mal "
            "attribués**, ni la différence entre invention et déduction.\n"
        )
    return (
        "\n---\n\n> ⚠️ **Check these — numbers not found in the transcript:** "
        f"{liste}.\n> They are never spoken in the recording; the model may have "
        "made them up.\n> This check only catches numbers **absent** from the "
        "source, not those present but **misattributed**.\n"
    )


def split_into_chunks(spoken_body: str, max_lines_per_chunk: int = 120) -> list[str]:
    """Découpe la transcription prononcée en tranches pour la phase MAP.

    Respecte les tours de parole (`**Speaker N**` ou horodatages) pour ne pas
    couper au milieu d'une réplique. Si le texte fait moins de `max_lines_per_chunk`
    lignes, renvoie une seule tranche.
    """
    lines = [line for line in spoken_body.splitlines() if line.strip()]
    if len(lines) <= max_lines_per_chunk:
        return [spoken_body]

    chunks: list[str] = []
    current_chunk: list[str] = []

    for line in lines:
        current_chunk.append(line)
        # On ne coupe qu'aux frontières de répliques et au-delà du seuil
        if len(current_chunk) >= max_lines_per_chunk and (
            "**Speaker" in line or line.startswith("[") or line.startswith("**")
        ):
            chunks.append("\n".join(current_chunk))
            current_chunk = []

    if current_chunk:
        if chunks and len(current_chunk) < 20:  # trop court : fusionne avec la dernière
            chunks[-1] += "\n" + "\n".join(current_chunk)
        else:
            chunks.append("\n".join(current_chunk))

    return chunks


def map_summarize_chunks(
    chunks: list[str],
    language: str | None,
    *,
    provider: str = llm.FOURNISSEUR_DEFAUT,
    mistral_api_key: Optional[str] = None,
    num_ctx: int = 4096,
    progress: Optional[Progress] = None,
    envoi: sensitivity.Envois = None,
) -> tuple[str, list[str]]:
    """Phase MAP : résumé factuel de chaque tranche, ET la liste des PERDUES.

    Extrait les sujets, lieux (numéros de salles), décisions, engagements et
    participants pour que le LLM ne perde pas d'information au milieu du texte.

    Rend un couple : le contexte, et ce qui n'a pas pu être
    résumé. Une tranche perdue changeait silencieusement le compte rendu.
    """
    perdues: list[str] = []
    if len(chunks) <= 1:
        return "", perdues

    lang = (language or "fr").lower()
    summaries: list[str] = []

    for index, chunk in enumerate(chunks, 1):
        frac = (index - 1) / len(chunks)
        msg = (
            f"Compte-rendu : analyse Tranche {index}/{len(chunks)}…"
            if lang == "fr"
            else f"Report: analyzing Chunk {index}/{len(chunks)}…"
        )
        if progress:
            progress(frac, msg)

        if lang == "fr":
            prompt = (
                f"Voici la tranche {index}/{len(chunks)} d'une transcription de réunion :\n\n"
                f"{chunk}\n\n"
                "Fais un résumé factuel et exhaustif de cette tranche (max 200 mots) en Français. "
                "Inclus OBLIGATOIREMENT : tous les sujets abordés, les lieux ou numéros de salles "
                "mentionnés, les décisions, et chaque engagement ou action assignée à une personne."
            )
        else:
            prompt = (
                f"Here is chunk {index}/{len(chunks)} of a meeting transcript:\n\n"
                f"{chunk}\n\n"
                "Provide a factual and thorough summary of this chunk (max 200 words) in English. "
                "MANDATORY: include all topics, locations or room numbers mentioned, decisions, "
                "and every action item or commitment assigned to any person."
            )

        try:
            # MESURÉ : deux exécutions au même commit et à la même
            # graine ont produit des prompts de compte-rendu de 13 257 et
            # 13 071 jetons. L'écart venait d'ICI — ces synthèses entrent dans
            # le prompt final, et n'étaient pas fixées.
            summary = llm.chat(
                _SYSTEM,
                prompt,
                provider=provider,
                api_key=llm.cle_mistral(provider, mistral_api_key),
                seed=SEED_CONTRAT,
                envoi=envoi,
            )
            summaries.append(f"### Tranche {index}/{len(chunks)}\n{summary.strip()}")
        except sensitivity.EnvoiRefuse:
            raise  # un refus de la règle d'envoi arrête tout, il ne perd pas une tranche
        except Exception as exc:  # noqa: BLE001 — une tranche perdue ne perd pas tout
            # Le `continue` était MUET. Neuf tranches sur dix perdues sur des
            # 429 donnaient un compte rendu tronqué, sans un avertissement, et
            # `citation_integrity_verified` restait intact. Le prédicat de
            # silence était corrélé au défaut : plus il y avait d'échecs, moins
            # on en savait.
            perdues.append(f"tranche {index}/{len(chunks)} : {exc}")
            continue

    if perdues:
        print(f"[report] {len(perdues)} tranche(s) perdue(s) en phase MAP : "
              + " ; ".join(perdues[:3]))
    if progress:
        progress(1.0, messages.tranches_resumees())
    return "\n\n".join(summaries), perdues


def _identity_fields(source_meta: dict) -> dict[str, str]:
    """Relations autoritaires d'un CR, héritées de son transcript source."""
    fields = {
        "source_document_id": source_meta.get("id"),
        "meeting_id": source_meta.get("meeting_id"),
        "project_id": source_meta.get("project_id"),
    }
    if not all(docid.est_valide(value) for value in fields.values()):
        raise ValueError(
            "Le transcript source n'a pas une identité document/réunion/projet complète"
        )
    return fields


def _resume_ancrage_citations(journal: Optional[dict]) -> Optional[dict]:
    """Compte compact des citations réancrées, pour le front-matter.

    Le détail (horodatages d'origine, canoniques, empreintes) vit dans le journal
    de génération, qui n'est PAS écrit sur le disque. Le document ne persiste
    qu'un NOMBRE : la normalisation reste visible dans les métriques sans y
    recopier le moindre extrait. Absent du front-matter quand rien n'a été
    réancré — pas de bruit à zéro.
    """
    anchoring = (journal or {}).get("citation_anchoring") or {}
    reancrees = anchoring.get("reanchored") or 0
    return {"reanchored": reancrees} if reancrees else None


def make_report(
    name: str,
    *,
    provider: str = llm.FOURNISSEUR_DEFAUT,
    mistral_api_key: Optional[str] = None,
    progress: Optional[Progress] = None,
) -> dict:
    """Génère + sauvegarde le CR d'un transcript. Retourne {name, content}."""
    item = library.get_transcript(name)
    if item is None:
        raise ValueError(f"Transcript introuvable : {name}")

    meta = item.get("meta") or {}
    body = item.get("body") or ""
    # La règle d'envoi AVANT le premier appel ; chaque appel la relit ensuite.
    envoi = library.envoi_du_document(name, meta)
    ultra_autorise = sensitivity.controler(envoi, local=llm.est_local(provider))
    source_body = spoken_markdown(body)
    language = meta.get("language")
    # Titre LISIBLE (titre saisi, sinon nom de la source) : il porte le H1 et le
    # titre du CR, jamais le slug du fichier.
    title = library.titre_lisible(meta.get("title"), meta.get("source"), nom=name)

    directive = _report_directive(language)
    citation_contract = report_citations.prompt_contract(language)

    chunks = split_into_chunks(source_body, max_lines_per_chunk=120)

    def _map_prog(frac: float, msg: str) -> None:
        if progress:
            progress(0.05 + frac * 0.45, msg)

    map_context, tranches_perdues = map_summarize_chunks(
        chunks,
        language,
        provider=provider,
        mistral_api_key=mistral_api_key,
        progress=_map_prog if progress else None,
        envoi=envoi,
    )

    if progress:
        progress(0.50, messages.classification_genre())

    genre = classify_genre(
        body, language, provider=provider, mistral_api_key=mistral_api_key, envoi=envoi,
    )
    asked = templates.questions(genre, language)
    numbered = "\n".join(f"{i}. {q}" for i, q in enumerate(asked, 1))
    section_rule = (
        f"Return EXACTLY {len(asked)} sections, with question_id 1 through "
        f"{len(asked)} exactly once. Never create a section for a speaker, "
        "claim, or evidence item."
    )

    map_section = ""
    if map_context:
        if (language or "fr").lower() == "fr":
            map_section = (
                f"Synthèse factuelle des tranches (Map-Reduce) :\n{map_context}\n\n"
            )
        else:
            map_section = (
                f"Factual chunk summaries (Map-Reduce):\n{map_context}\n\n"
            )

    instruction = (
        f"{directive}\n\n{section_rule}\n\n{citation_contract}\n\n"
        f"Questions :\n{numbered}\n\n"
        f"{map_section}"
        f"Transcript / Transcription :\n{source_body}\n\n"
        f"Questions :\n{numbered}\n\n{section_rule}\n\n"
        f"{citation_contract}\n\n{directive}"
    )

    # Une seule branche, et les contraintes de sortie sont demandées pour TOUS
    # les moteurs. Le chemin local court-circuitait la façade parce qu'elle ne
    # savait porter ni la borne de sortie, ni le schéma, ni les tentatives — si
    # bien que le compte-rendu cloud partait sans aucune des trois, en silence.
    # Chaque moteur les honore par le moyen dont il dispose, et le dit.
    if not llm.is_available(provider=provider, api_key=llm.cle_mistral(provider, mistral_api_key)):
        raise llm.LLMError(f"Le service LLM ({provider}) n'est pas disponible.")

    # Le journal de génération remonte jusqu'à l'appelant : sans lui, une
    # réparation serait indistinguable d'un succès, et le taux de fautes du
    # modèle deviendrait invisible — exactement le signal sur lequel repose
    # l'analyse des fautes de balance.
    journal_generation: dict = {}
    cr_body = _generer_sous_contrat(
        instruction,
        body,
        attendues=len(asked),
        language=language,
        provider=provider,
        mistral_api_key=mistral_api_key,
        progress=progress,
        journal=journal_generation,
        envoi=envoi,
    )

    # Garde-fou d'identité, AVANT le contrôle numérique : le modèle rattache des
    # noms aux étiquettes de diarisation (« Speaker 10 (Léa Marchand/Iris) »),
    # une inférence que la source ne porte pas — et qu'elle contredisait ici.
    cr_body, bindings = names.strip_speaker_bindings(cr_body)

    # Vérification déterministe : tout nombre affirmé doit exister dans le
    # transcript. Constaté en vrai — un CR a inventé « Mondial 2028 ».
    # Désarmé par défaut — voir `config.FACTCHECK_NUMBERS` pour le pourquoi.
    flagged = [] if not config.FACTCHECK_NUMBERS else factcheck.unsupported_numbers(
        cr_body, quality.transcript_texts(body)
    )
    citation_audit = report_citations.audit(cr_body, body)

    # Le CR annonçait toujours `LLM_MODEL`, donc Qwen local, même quand
    # il avait été produit par Mistral. Deux comptes-rendus de moteurs
    # différents portaient la même provenance, et le fingerprint aussi.
    #
    # Cette correction-là était un `if/else` à deux branches, et le troisième
    # moteur l'a rouverte : un CR produit par MLX réannonçait `qwen3.5:9b`.
    # C'est la CLASSE qu'on ferme, pas l'occurrence — la résolution est chez le
    # moteur, et un quatrième fournisseur ne peut plus la contourner.
    modele_reel = llm.modele_servi(provider)
    now = datetime.now()
    fm = {
        "id": docid.nouveau(),
        **_identity_fields(meta),
        "title": f"{title} — compte-rendu",
        "date": now.strftime("%Y-%m-%d"),
        "type": "report",
        "source_transcript": name,
        # Le niveau de la réunion à la génération, noté pour mémoire : celui qui
        # s'applique reste celui de la réunion (`library.sensibilite_effective`).
        "sensitivity": library.sensibilite_effective(meta.get("sensitivity"), meta.get("meeting_id"),
                                                     meta.get("project_id")),
        # Présent seulement quand ce CR ultra-confidentiel a été produit en cloud sous
        # l'autorisation du propriétaire (`sensitivity.controler`).
        "envoi_ultra_autorise": True if ultra_autorise else None,
        "language": language,
        "model": modele_reel,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "genre": genre,
        "unsupported_numbers": flagged or None,
        "removed_speaker_bindings": bindings or None,
        # Ce que la phase MAP n'a pas pu résumer. Le compte rendu reste
        # publiable — mais il est amputé, et le lecteur doit le savoir.
        "map_chunks_failed": tranches_perdues or None,
        "citation_audit": citation_audit.metadata(),
        "citation_warnings": list(citation_audit.warnings) or None,
        # Combien de citations verbatim ont été rattachées à leur horodatage
        # canonique. `audit()` relit le Markdown déjà réancré et ne peut plus le
        # distinguer d'un pointeur natif : le compte vient donc du journal de
        # génération, pas du contrôleur. Absent si aucun réancrage.
        "citation_anchoring": _resume_ancrage_citations(journal_generation),
        "pipeline": provenance.report_fingerprint(
            source_pipeline=meta.get("pipeline"),
            model=modele_reel,
            language=language,
            genre=genre,
            num_ctx=4096,
            questions=asked,
        ),
    }
    heading = "Compte-rendu" if (language or "fr").lower() == "fr" else "Report"
    content = (
        writer_md._yaml_frontmatter(fm)
        + f"\n\n# {heading} — {title}\n\n{cr_body.strip()}\n"
        + f"\n{report_citations.GENERATED_NOTICE_MARKER}\n"
        + _bindings_notice(bindings, language)
        + _numbers_notice(flagged, language)
        + report_citations.warning_notice(citation_audit, language)
    )

    # Le CR reste dans le PROJET de son transcript source : sinon il atterrit
    # dans « Non classé » et sort du périmètre où on l'a demandé.
    src_path = library.transcript_path(name)
    target_dir = src_path.parent if src_path is not None else writer_md.OUTPUT_DIR
    out_path = writer_md.write_md(
        content, writer_md.unique_path(f"{title} — CR", target_dir)
    )

    # Index (FTS + sémantique) pour qu'il apparaisse en biblio / recherche / RAG.
    library.sync_file(out_path)
    avertissements: list[str] = []
    try:
        semantic.index_name(out_path.name)
    except Exception as exc:  # noqa: BLE001
        # L'échec était avalé : le CR apparaissait en bibliothèque mais restait
        # introuvable par la recherche sémantique, qui le promet pourtant. Le
        # pipeline principal sait déjà remonter ce genre de dégradation dans
        # les warnings de job ; ce chemin-ci obtient le même contrat.
        avertissements.append(
            f"Compte-rendu écrit mais absent de la recherche par le sens : {exc}"
        )
        print(f"[report] indexation sémantique ignorée : {exc}")

    return {"name": out_path.name, "content": content, "genre": genre,
            "warnings": avertissements or None,
            "generation": journal_generation or None}


def generate_report(
    name: str,
    *,
    provider: str = llm.FOURNISSEUR_DEFAUT,
    mistral_api_key: Optional[str] = None,
    progress: Optional[Progress] = None,
) -> dict:
    """Seam UNIQUE de génération de CR pour l'application.

    UN SEUL contrat produit riche pour TOUS les fournisseurs (MLX local ET Mistral
    cloud) : architecture `core.experimental.app_report` — strates MAIN/SECONDARY/
    CLOSING/NOISE, rétention non destructive, garde de contenance, interprétation
    B4 étiquetée, normalisation d'horodatage, provenance, citations `[source]`.
    La différence entre moteurs se limite au PROFIL D'EXÉCUTION (tranche/lots/
    plafonds), résolu PAR CAPACITÉ dans `run_stages` — jamais par une comparaison
    de nom de fournisseur dans le tronc.

    `provider` est transmis normalement ; l'utilisateur ne choisit AUCUN mode.
    Contrat de retour identique : `{name, content, genre, warnings, generation}`.

    Le CR hérité `make_report` reste DISPONIBLE (repli/référence) mais n'est plus
    le chemin normal de production Mistral. Import paresseux de l'expérimental —
    le test « importer core.report ne tire pas l'expérimental » reste vrai.
    """
    from core.experimental import app_report  # noqa: PLC0415
    return app_report.make_local_report(
        name, provider=provider, mistral_api_key=mistral_api_key, progress=progress
    )
