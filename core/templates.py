"""Gabarits de compte-rendu, exprimés en **questions** et choisis par genre.

## Pourquoi des questions et non des rubriques

Un titre de section exige d'être rempli ; une question autorise « aucun ».
`report.py` demandait « couvre quand c'est pertinent : … décisions, actions… » et
obtenait, sur un plateau télé qui n'en contient aucune, des décisions et des
prochaines étapes fabriquées.

La littérature explique pourquoi la consigne conditionnelle ne pouvait pas
marcher — l'abstention est un problème non résolu que le prompt système ne répare
pas, et un modèle sommé d'extraire là où il n'y a rien invente presque
toujours. Le cadrage interrogatif est en revanche la forme
qu'emploient les travaux évalués : QMSum (query-focused summarization) et SCOPE,
où faire répondre le modèle à des questions avant de sélectionner le contenu bat
le prompt « joue tel rôle ».

⚠️ Honnêteté : aucun travail publié ne mesure « poser une question réduit le
meublage dans un compte-rendu ». C'est une extrapolation cohérente, pas un
résultat établi. Le corpus sert de contrôle.

## Pourquoi un genre

Aucune catégorie de réunion ne domine (Allen et al.) : un gabarit unique est
indéfendable. Le marché (Granola, Otter, Zoom) a
convergé vers des gabarits par genre — mais avec sélection **manuelle**, personne
ne revendiquant de détection automatique évaluée.

Ici le genre est déduit du **bloc de profil déjà écrit** — court et dense, donc
un appel bien moins cher qu'une relecture du transcript entier. Il en coûte
quand même un : cette page a un temps affirmé le contraire, c'était faux.

Deux garde-fous rendent une erreur de classification bénigne :

1. La liste est **fermée** : choisir dans un menu est bien plus facile pour un
   modèle qu'inventer un libellé, et le résultat est vérifiable en code.
2. Le défaut est le gabarit **le plus sobre**, jamais le plus exigeant. Un doute
   produit un CR moins riche, jamais un CR inventif — l'inverse d'aujourd'hui.

## Les genres à engagements

Seuls `projet` et `steerco` (cf. `COMMITMENT_GENRES`) interrogent décisions et
engagements — les autres n'en portent aucune trace, ce qu'un test vérifie. Ce
sont donc les seuls à rouvrir les modes d'échec documentés, et leurs questions
encodent les définitions de référence plutôt que le mot courant :

- une **décision** est une proposition **adoptée** (typologie DDA, Fernández et
  al.) ; une option discutée sans être retenue n'en est pas une. Sans cette
  contrainte, le modèle convertit la suggestion en décision — le biais est
  directionnel : la certitude est bien plus souvent augmentée que diminuée ;
- une **action** est un engagement **public** à faire une tâche (Purver et al.),
  pas une suggestion ; l'axe ferme/conditionnel est explicité, seul levier ayant
  amélioré l'accord entre annotateurs dans la littérature e-mail ;
- le **responsable** n'est demandé que « si le dialogue le dit » : l'attribuer
  reste peu fiable dès quelques personnes, et la recherche l'a explicitement
  laissé de côté.

⚠️ Ces deux genres justifient un garde-fou encore absent : la **densité
d'actions** d'une réunion d'une heure se compte en quelques unités. Un CR qui en
produit dix est anormal.

`entretien_embauche` ne demande **aucune appréciation** sur la personne : juger
un candidat serait une inférence sur un individu, hors du périmètre d'un
compte-rendu.
"""

from __future__ import annotations

from typing import Optional

GENERIC = "generique"

# Liste FERMÉE. Toute valeur hors de cet ensemble retombe sur GENERIC.
GENRES = (
    GENERIC,
    "analyse_sportive",
    "interview",
    "projet",
    "steerco",
    "entretien_embauche",
    "webinar",
    "keynote",
    "town_hall",
    "internal_briefing",
)

LABELS = {
    GENERIC: "générique",
    "analyse_sportive": "analyse sportive",
    "interview": "interview",
    "projet": "réunion de projet",
    "steerco": "comité de pilotage",
    "entretien_embauche": "entretien d'embauche",
    "webinar": "webinaire",
    "keynote": "keynote",
    "town_hall": "réunion plénière interne",
    "internal_briefing": "briefing interne",
}

# Genres qui interrogent décisions et engagements. Eux seuls rouvrent les modes
# d'échec documentés (invention d'actions, attribution d'un responsable) → leurs
# questions encodent les définitions de référence, et ce sont eux qui justifieront
# le contrôle de densité d'actions.
COMMITMENT_GENRES = ("projet", "steerco")

# Formulations que le modèle produit spontanément pour un même genre. Le
# classifieur doit répondre par une clé exacte ; ceci rattrape les écarts
# bénins sans jamais élargir l'ensemble des genres possibles.
_ALIASES = {
    "analyse sportive": "analyse_sportive",
    "debat sportif": "analyse_sportive",
    "débat sportif": "analyse_sportive",
    "sport": "analyse_sportive",
    "sports_analysis": "analyse_sportive",
    "podcast": "interview",
    "reunion": "projet",
    "réunion": "projet",
    "meeting": "projet",
    "project": "projet",
    "comite de pilotage": "steerco",
    "comité de pilotage": "steerco",
    "steering": "steerco",
    "steering_committee": "steerco",
    "job_interview": "entretien_embauche",
    "entretien d'embauche": "entretien_embauche",
    "recrutement": "entretien_embauche",
    "webinaire": "webinar",
    "conference": "keynote",
    "conférence": "keynote",
    "presentation": "keynote",
    "présentation": "keynote",
    "town hall": "town_hall",
    "internal town meeting": "town_hall",
    "town meeting": "town_hall",
    "all hands": "town_hall",
    "all_hands": "town_hall",
    "briefing interne": "internal_briefing",
    "internal briefing": "internal_briefing",
    "management briefing": "internal_briefing",
    "generic": GENERIC,
    "générique": GENERIC,
    "autre": GENERIC,
    "other": GENERIC,
}

# Chaque question porte son propre droit au « aucun » quand la réponse peut
# légitimement être vide. C'est ce qui distingue une question d'une rubrique.
_QUESTIONS: dict[str, dict[str, list[str]]] = {
    # Révisé. Les 5 questions d'origine {événement, faits,
    # analyse, désaccord, prédiction} laissaient tomber la matière la plus
    # documentaire d'une rétrospective — contexte de départ, personnes nommées,
    # choix et affaires marquantes, méthode/style, héritage — faute d'une rubrique
    # où la loger : le contrat impose EXACTEMENT les sections des questions, donc
    # ce qu'aucune question n'invite n'a nulle part où aller : le modèle local ne
    # produisait pas ces catégories.
    #
    # « Désaccord » a été retiré : il FABRIQUAIT une affirmation quand tout le
    # monde était d'accord (le CR local a produit « les intervenants sont d'accord
    # sur… » comme un claim). La divergence se lit maintenant DANS les jugements,
    # sans forcer de ligne de remplissage.
    #
    # Aucun mot de décision/action ici (cf. `test_seuls_les_gabarits_dedies...`) :
    # une rétrospective RAPPORTE des choix passés, elle n'ACTE rien. Chaque
    # question qui peut légitimement être vide porte son droit au « aucun ».
    "analyse_sportive": {
        "fr": [
            "De quel événement ou sujet parle-t-on, quand, et dans quel contexte "
            "antérieur cela s'inscrit-il (situation de départ, historique rappelé) ? "
            "Ne reprends que ce qui est dit.",
            "Quels faits, résultats, dates, scores, titres ou records sont établis "
            "par les intervenants ? N'ajoute aucun chiffre qui ne soit pas prononcé "
            "dans la transcription.",
            "Quelles personnes sont nommées, et à quel titre (joueurs, entraîneurs, "
            "dirigeants, successeurs…) ? S'il n'y en a aucune, réponds « aucune ».",
            "Quelles réussites et quels échecs sont évoqués, et dans quelles "
            "circonstances (adversaire, compétition, manière) ?",
            "Quels choix marquants ou affaires sont rapportés (sélection, "
            "discipline, épisodes hors-terrain) ? S'il n'y en a aucun, réponds "
            "« aucun ».",
            "Quelle méthode, quel style de jeu ou quelle approche sont décrits ou "
            "critiqués ? S'il n'y en a aucun, réponds « aucun ».",
            "Quels jugements ou appréciations chaque intervenant défend-il, et sur "
            "qui ou quoi ? S'ils divergent, sur quels points ?",
            "Quelles attentes, prédictions ou perspectives d'avenir sont exprimées, "
            "et par qui ? S'il n'y en a aucune, réponds « aucune ».",
        ],
        "en": [
            "Which event or topic is being discussed, when, and in what prior "
            "context does it sit (starting situation, history recalled)? Report "
            "only what is said.",
            "Which facts, results, dates, scores, titles or records do the speakers "
            "establish? Do not add any figure that is not spoken in the transcript.",
            "Which people are named, and in what capacity (players, coaches, "
            "officials, successors…)? If there are none, answer \"none\".",
            "Which successes and failures are mentioned, and in what circumstances "
            "(opponent, competition, manner)?",
            "Which notable choices or affairs are reported (selection, discipline, "
            "off-field episodes)? If there are none, answer \"none\".",
            "Which method, playing style or approach is described or criticised? "
            "If there is none, answer \"none\".",
            "Which judgements or assessments does each speaker defend, and about "
            "whom or what? If they diverge, on which points?",
            "Which expectations, predictions or future prospects are expressed, and "
            "by whom? If there are none, answer \"none\".",
        ],
    },
    "interview": {
        "fr": [
            "Qui mène l'entretien et qui répond, si le dialogue permet de l'établir ? "
            "Si ce n'est pas déterminable, dis-le plutôt que de deviner.",
            "Quels sujets sont abordés ?",
            "Quelles affirmations notables la personne interrogée fait-elle ?",
            "Quelles anecdotes ou expériences personnelles raconte-t-elle ?",
            "Quels conseils, enseignements ou recommandations la personne interrogée partage-t-elle ? "
            "S'il n'y en a aucun, réponds « aucun ».",
            "Des annonces sont-elles faites (projet, date, sortie) ? "
            "S'il n'y en a aucune, réponds « aucune ».",
        ],
        "en": [
            "Who conducts the interview and who answers, if the dialogue makes it clear? "
            "If it cannot be determined, say so rather than guessing.",
            "Which topics are covered?",
            "Which notable claims does the interviewee make?",
            "Which anecdotes or personal experiences do they recount?",
            "Which advice, lessons, or recommendations does the interviewee share? "
            "If none, answer \"none\".",
            "Are any announcements made (project, date, release)? "
            "If there are none, answer \"none\".",
        ],
    },
    # Les deux seuls gabarits qui interrogent décisions et engagements. Les
    # formulations suivent les définitions de référence :
    #  · une décision = une proposition ADOPTÉE (Fernández et al., typologie DDA) —
    #    une option discutée sans être retenue n'en est pas une ;
    #  · une action = un ENGAGEMENT PUBLIC à faire une tâche (Purver et al.), pas
    #    une suggestion ; d'où l'axe ferme/conditionnel rendu explicite, seul
    #    levier ayant amélioré l'accord entre annotateurs dans la littérature ;
    #  · le responsable n'est demandé que « si le dialogue le dit » : l'attribuer
    #    reste peu fiable, et la recherche l'a explicitement écarté.
    "projet": {
        "fr": [
            "Quels sujets ont été traités ?",
            "Quelles décisions ont été ADOPTÉES ? Une option discutée sans être retenue "
            "n'est pas une décision. Si rien n'a été acté, réponds « aucune ».",
            "Quelqu'un s'est-il engagé explicitement à faire une tâche, ou s'est-il vu "
            "assigner clairement une tâche ? Ne nomme la personne que si le dialogue le "
            "dit. Si personne ne s'est engagé, réponds « personne ».",
            "Quels jalons, livrables ou dates limites sont annoncés ? "
            "S'il n'y en a aucun, réponds « aucun ».",
            "Quelles questions restent ouvertes ou non tranchées ?",
            "Quels blocages, risques ou dépendances sont mentionnés ? "
            "S'il n'y en a aucun, réponds « aucun ».",
        ],
        "en": [
            "Which topics were covered?",
            "Which decisions were ADOPTED? An option discussed but not retained is not a "
            "decision. If nothing was settled, answer \"none\".",
            "Did anyone explicitly commit to a task, or was anyone clearly assigned one? "
            "Name the person only if the dialogue says so. If nobody committed, answer "
            "\"nobody\".",
            "Which milestones, deliverables or deadlines are announced? "
            "If none, answer \"none\".",
            "Which questions remain open or unsettled?",
            "Which blockers, risks or dependencies are mentioned? If none, answer \"none\".",
        ],
    },
    "steerco": {
        "fr": [
            "Quel état d'avancement est présenté ?",
            "Quels écarts par rapport au plan sont signalés (délai, budget, périmètre) ? "
            "Ne reprends que les chiffres prononcés. S'il n'y en a aucun, réponds « aucun ».",
            "Quels arbitrages ont été RENDUS ? Une option débattue sans trancher n'est pas "
            "un arbitrage. Si rien n'a été tranché, réponds « aucun ».",
            "Quelles demandes d'approbation, de budget complémentaire ou d'arbitrage futur "
            "sont soumises au comité ? S'il n'y en a aucune, réponds « aucune ».",
            "Quels risques ou alertes sont remontés, et quelle suite leur est donnée ?",
            "Quels engagements explicites ont été pris, et par qui si le dialogue le dit ? "
            "Si personne ne s'est engagé, réponds « aucun ».",
        ],
        "en": [
            "What progress status is presented?",
            "Which deviations from plan are reported (schedule, budget, scope)? Use only "
            "the figures actually spoken. If there are none, answer \"none\".",
            "Which trade-offs were SETTLED? An option debated without a ruling is not a "
            "decision. If nothing was settled, answer \"none\".",
            "Which requests for approval, additional budget, or future trade-offs are "
            "submitted to the committee? If none, answer \"none\".",
            "Which risks or alerts are raised, and what follow-up is given?",
            "Which explicit commitments were made, and by whom if the dialogue says so? "
            "If nobody committed, answer \"none\".",
        ],
    },
    # Contenu sensible : on décrit ce qui est dit, on n'ÉVALUE pas la personne.
    # Une appréciation sur un candidat serait une inférence sur un individu, hors
    # du périmètre d'un compte-rendu et lourde de conséquences.
    "entretien_embauche": {
        "fr": [
            "De quel poste et de quel contexte s'agit-il, si c'est dit ?",
            "Quel parcours et quelles expériences la personne décrit-elle ?",
            "Quelles compétences sont évoquées, et avec quels exemples concrets ?",
            "Quelles questions la personne interrogée pose-t-elle de son côté ? "
            "Si elle n'en pose aucune, réponds « aucune ».",
            "Quels éléments pratiques sont abordés (disponibilité, calendrier annoncé) ? "
            "S'il n'y en a aucun, réponds « aucun ». N'émets aucune appréciation sur la "
            "candidature : rapporte seulement ce qui est dit.",
        ],
        "en": [
            "Which role and context are discussed, if stated?",
            "Which background and experience does the person describe?",
            "Which skills are mentioned, and with which concrete examples?",
            "Which questions does the interviewee ask in return? If none, answer \"none\".",
            "Which practical points are covered (availability, timeline mentioned)? "
            "If none, answer \"none\". Do not assess the candidate: report only what is said.",
        ],
    },
    "webinar": {
        "fr": [
            "Quel est le sujet, et qui présente si c'est dit ?",
            "Quels points sont exposés, et dans quel ordre ?",
            "Quelles démonstrations, exemples ou données sont montrés ?",
            "Quelles questions le public pose-t-il, et quelles réponses reçoit-il ? "
            "S'il n'y a pas de session de questions, réponds « aucune ».",
            "Quelles ressources ou suites sont annoncées ? S'il n'y en a aucune, "
            "réponds « aucune ».",
        ],
        "en": [
            "What is the topic, and who presents if stated?",
            "Which points are presented, and in what order?",
            "Which demos, examples or data are shown?",
            "Which questions does the audience ask, and what answers do they get? "
            "If there is no Q&A, answer \"none\".",
            "Which resources or follow-ups are announced? If none, answer \"none\".",
        ],
    },
    "keynote": {
        "fr": [
            "Quelle est la thèse principale défendue ?",
            "Quels arguments et quelles preuves sont avancés à l'appui ?",
            "Quelles annonces sont faites ? S'il n'y en a aucune, réponds « aucune ».",
            "Quels moments ou formules marquants ressortent ?",
            "À quel public l'intervention s'adresse-t-elle, si c'est dit ?",
        ],
        "en": [
            "What is the main thesis being argued?",
            "Which arguments and evidence support it?",
            "Which announcements are made? If none, answer \"none\".",
            "Which standout moments or phrases emerge?",
            "Which audience is the talk addressed to, if stated?",
        ],
    },
    # Communication collective large : annonces, alignement et questions du
    # public. Contrairement à une réunion de projet, on ne présume ni décision
    # adoptée ni engagement individuel.
    "town_hall": {
        "fr": [
            "Quel est l'objectif de cette réunion collective, et à quel public "
            "s'adresse-t-elle si c'est dit ?",
            "Quelles orientations, nouvelles ou évolutions importantes sont présentées ?",
            "Quelles politiques ou quels processus sont expliqués ou clarifiés ? "
            "Ne rapporte que ce qui est explicitement dit.",
            "Quelles questions le public pose-t-il, et quelles réponses reçoit-il ? "
            "S'il n'y a pas de questions, réponds « aucune ».",
            "Quelles ressources, quels rappels ou quels rendez-vous sont mentionnés ? "
            "S'il n'y en a aucun, réponds « aucun ».",
        ],
        "en": [
            "What is the purpose of this collective meeting, and which audience is it "
            "addressed to if stated?",
            "Which directions, news, or important changes are presented?",
            "Which policies or processes are explained or clarified? Report only what "
            "is explicitly stated.",
            "Which questions does the audience ask, and which answers are given? If "
            "there are no questions, answer \"none\".",
            "Which resources, reminders, or upcoming gatherings are mentioned? If none, "
            "answer \"none\".",
        ],
    },
    # Brief ciblé : transmettre une règle, un processus ou un contexte
    # opérationnel à un groupe précis. Il privilégie les consignes et limites,
    # sans transformer leur explication en décision de réunion.
    "internal_briefing": {
        "fr": [
            "Quel est le contexte et l'objectif de ce briefing, et à qui s'adresse-t-il ?",
            "Quelles règles, procédures ou consignes opérationnelles sont expliquées ?",
            "Quelles raisons, contraintes ou limites sont données pour les justifier ?",
            "Quels cas particuliers ou exceptions sont explicitement mentionnés ? "
            "S'il n'y en a aucun, réponds « aucun ».",
            "Qui est le point de contact référent ou quel est le canal d'escalade désigné en cas de problème ? "
            "S'il n'y en a aucun, réponds « aucun ».",
            "Quelles questions sont posées, quelles réponses sont données et quels points "
            "restent incertains ? S'il n'en reste aucun, dis-le.",
        ],
        "en": [
            "What is the context and purpose of this briefing, and who is it for?",
            "Which operational rules, procedures, or instructions are explained?",
            "Which reasons, constraints, or limits are given to justify them?",
            "Which special cases or exceptions are explicitly mentioned? If none, answer "
            "\"none\".",
            "Who is the designated reference contact or escalation channel in case of issue? "
            "If none, answer \"none\".",
            "Which questions are asked, which answers are given, and which points remain "
            "uncertain? If none remain, say so.",
        ],
    },
    GENERIC: {
        "fr": [
            "De quoi parle cet enregistrement ?",
            "Quels sont les 3 à 5 points principaux ?",
            "Quelles suites ou perspectives sont évoquées ? "
            "S'il n'y en a aucune, réponds « aucune ».",
            "Qu'y a-t-il d'important à retenir ?",
        ],
        "en": [
            "What is this recording about?",
            "What are the 3 to 5 main points?",
            "Which follow-ups or outlooks are mentioned? "
            "If none, answer \"none\".",
            "What is important to remember?",
        ],
    },
}


def normalize(raw: Optional[str]) -> str:
    """Réponse du classifieur → genre connu. Tout écart retombe sur GENERIC.

    C'est le garde-fou central : le modèle propose, le code vérifie, et
    l'inconnu produit le gabarit le plus sobre.
    """
    if not raw:
        return GENERIC
    key = raw.strip().strip(".\"'` \n").lower().replace("-", "_")
    if key in GENRES:
        return key
    if key in _ALIASES:
        return _ALIASES[key]
    # Le modèle a répondu par une phrase : on cherche un genre nommé dedans. Les
    # libellés les plus longs sont testés d'abord — « comité de pilotage » doit
    # l'emporter sur « comité », et l'ordre ne doit pas dépendre de la déclaration.
    named = sorted(
        ((g.replace("_", " "), g) for g in GENRES if g != GENERIC),
        key=lambda kv: -len(kv[0]),
    )
    for label, genre in named:
        if label in key:
            return genre
    for alias, genre in sorted(_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if alias in key:
            return genre
    return GENERIC


def questions(genre: str, language: Optional[str]) -> list[str]:
    """Questions du gabarit, dans la langue du transcript quand elle est couverte.

    Le français et l'anglais sont rédigés ; toute autre langue reçoit les
    questions anglaises — la directive de langue de `report.py` impose de toute
    façon la langue de **sortie**.
    """
    genre = genre if genre in GENRES else GENERIC
    lang = (language or "fr").lower()[:2]
    per_lang = _QUESTIONS[genre]
    return list(per_lang.get(lang) or per_lang["en"])


def genre_list(*, commitments: bool = True) -> str:
    """Les genres proposés au classifieur.

    `commitments=False` retire `projet` et `steerco` : à réserver aux cas où
    l'on juge sur une preuve faible (transcript non profilé). Sans cette
    restriction, un vlog sans profil se faisait classer « projet », donc se
    voyait demander décisions et engagements — le bug qu'on vient de fermer.
    """
    genres = GENRES if commitments else tuple(
        g for g in GENRES if g not in COMMITMENT_GENRES
    )
    return ", ".join(genres)
