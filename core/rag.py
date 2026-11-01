"""RAG — « Interroge tes transcripts ».

Question → retrieval sémantique (passages pertinents) → réponse LLM **citée**
(chaque affirmation renvoie à [n] = un extrait, avec fichier + timestamp).
"""

from __future__ import annotations

import re
from typing import Optional

from . import library, llm, resources, semantic, sensitivity, telemetry
from . import projects as project_store


class PassagesUltraConfidentiels(Exception):
    """Tous les passages trouvés sont ultra-confidentiels sans l'autorisation de leur
    propriétaire, et le fournisseur est cloud : il ne reste rien à lui envoyer
    (`sensitivity.verifier_envoi`)."""


def _envois(chunks: list[dict]) -> list[sensitivity.Envoi]:
    """La règle d'envoi de chaque passage : sa sensibilité EFFECTIVE (Q16,
    `library.sensibilite_effective`, le calcul du CR, de l'ECL et du MCP) et le
    propriétaire du projet qui contient son document.

    Le propriétaire, pas le compte qui pose la question : les passages peuvent venir
    de plusieurs projets (portée « tous », partages). Il n'est cherché que pour un
    passage ultra-confidentiel, le seul que la règle juge, une fois par document.
    """
    proprietaires: dict[str, object] = {}
    sensibilites: dict[str, str] = {}
    envois = []
    for c in chunks:
        nom = c.get("name") or ""
        if nom not in sensibilites:
            sensibilites[nom] = library.sensibilite_effective(c.get("sensitivity"), c.get("meeting_id"),
                                                              c.get("project_id"))
        ultra = sensibilites[nom] == sensitivity.ULTRA_CONFIDENTIEL
        if ultra and nom not in proprietaires:
            proprietaires[nom] = library.proprietaire_du_document(nom)
        envois.append(sensitivity.Envoi(sensibilite=sensibilites[nom],
                                        proprietaire=proprietaires.get(nom) if ultra else None,
                                        document_id=c.get("document_id")))
    return envois

_SYSTEM = (
    "Tu es un assistant qui répond à des questions à partir d'extraits de "
    "transcriptions (réunions, interviews, vidéos). RÈGLES : réponds dans la "
    "LANGUE DE LA QUESTION ; base-toi UNIQUEMENT sur les extraits fournis ; "
    "cite chaque information avec un repère [n] correspondant au numéro de "
    "l'extrait ; si les extraits ne permettent pas de répondre, dis-le "
    "clairement sans inventer. Sois concis et factuel."
)


# Mots outils fréquents, sans modèle. Le français et l'anglais reçoivent une consigne
# explicite ; les autres langues ne servent qu'à reconnaître une question qui n'est ni
# l'une ni l'autre, pour s'abstenir. Un mot présent dans plusieurs listes (« la », « de »,
# « que », « le ») ne départage rien et ne compte pour aucune.
# « a », « on » et « en » existent dans plusieurs langues : ils ne figurent nulle part, ni
# les mots courts qu'une question anglaise ou technique porte aussi (« ci », « come »,
# « per », « met », « as », « no », « um »…), ni ceux d'une question française (« dit »).
_MOTS_LANGUE = {
    "en": frozenset("the is are was were what who when where why how which did does do of to in for with "
                    "about and or not this that it be have has can should would could will there".split()),
    "fr": frozenset("le la les un une des est sont était été qui que quoi quand où pourquoi comment quel "
                    "quelle quels quelles combien ont de du au aux sur pour avec et ou ne pas ce cette il "
                    "elle nous vous qu".split()),
    "de": frozenset("der die das ist sind waren wer wie wo warum welche welcher welches und oder nicht "
                    "ein eine einen dem den zu für über auf ich wir es hat haben wird werden kann "
                    "soll".split()),
    "es": frozenset("el los las la de que un es fue qué cuál cuáles quién quiénes cuándo dónde cómo "
                    "por para con una del y o pero está están hay lo su sus".split()),
    "it": frozenset("il lo la le gli un una uno di del della dei che è sono chi cosa quale quali "
                    "quando dove perché con su sul sulla e o".split()),
    "pt": frozenset("o os uma de do da dos das em que qual quais quem quando onde como por "
                    "para com é são foi não e ou".split()),
    "nl": frozenset("de het een zijn wie wat waar wanneer waarom hoe welke van op voor en niet "
                    "dat".split()),
}
_DISTINCTIFS = {
    langue: mots - frozenset().union(*(autres for autre, autres in _MOTS_LANGUE.items() if autre != langue))
    for langue, mots in _MOTS_LANGUE.items()
}
# En deçà, une question ne tranche pas, même faite de deux mots outils : « Pourquoi pas ? ».
_MOTS_MIN = 3
_DISTINCTIFS_MIN = 2
# Le français ou l'anglais doit peser au moins quatre fois toutes les autres langues réunies.
_DOMINANCE = 4
# Une consigne générique (« la langue de la question ») ne suffit pas à un petit modèle
# local devant des extraits d'une autre langue : la langue détectée est nommée.
_CONSIGNE_LANGUE = {"en": "Answer in English.", "fr": "Réponds en français."}


def langue_de(texte: str) -> Optional[str]:
    """« en » ou « fr » quand la question l'établit nettement, None sinon.

    S'abstient sur une question courte, sans mot outil distinctif (noms propres,
    vocabulaire technique, autre alphabet), d'une autre langue, ou qui mêle les langues :
    la consigne générale reste alors seule. Ne lit que la question, jamais la langue de
    l'interface ni celle des extraits.
    """
    mots = re.findall(r"[^\W\d_]+", (texte or "").lower())
    if len(mots) < _MOTS_MIN:
        return None
    scores = {langue: sum(m in distinctifs for m in mots) for langue, distinctifs in _DISTINCTIFS.items()}
    gagnante = max(scores, key=scores.__getitem__)
    autres = sum(scores.values()) - scores[gagnante]
    if gagnante not in _CONSIGNE_LANGUE or scores[gagnante] < _DISTINCTIFS_MIN:
        return None
    if autres and scores[gagnante] < _DOMINANCE * autres:
        return None
    return gagnante


# Longueur de l'extrait d'un passage cité, affiché sous la réponse.
EXTRAIT_MAX = 220
_MOT = re.compile(r"\w+")


def extrait(texte: str, reponse: str, *, limite: int = EXTRAIT_MAX) -> str:
    """La fenêtre du passage qui porte la réponse : là où ses chiffres et ses mots reviennent.

    Le début d'un passage ne porte pas toujours ce que la réponse en tire (un prix dit
    en fin de réplique) : vérifier une citation ne doit pas obliger à ouvrir la source.
    Un chiffre de la réponse pèse plus qu'un mot ; à poids égal, la fenêtre la plus tôt.
    """
    if len(texte) <= limite:
        return texte
    cles = {m.casefold(): (3 if m.isdigit() else 1) for m in _MOT.findall(reponse or "")
            if m.isdigit() or len(m) >= 4}

    def poids(debut: int) -> int:
        return sum(cles.get(m.casefold(), 0) for m in _MOT.findall(texte[debut:debut + limite]))

    debut = max([0] + [m.start() for m in _MOT.finditer(texte)], key=lambda d: (poids(d), -d))
    fin = min(len(texte), debut + limite)
    if fin < len(texte) and (coupe := texte.rfind(" ", debut, fin)) > debut:
        fin = coupe  # pas de mot coupé en fin d'extrait
    return ("…" if debut else "") + texte[debut:fin] + ("…" if fin < len(texte) else "")


def ask(
    question: str,
    *,
    k: int = 6,
    project_id: project_store.Portee = None,
    provider: str = llm.FOURNISSEUR_DEFAUT,
    mistral_api_key: Optional[str] = None,
) -> dict:
    """Répond à `question` sur le corpus. Retourne {answer, sources}."""
    question = (question or "").strip()
    if not question:
        return {"answer": "", "sources": []}

    # Vers un fournisseur cloud, un passage ultra-confidentiel ne part que si le
    # propriétaire de son document l'a autorisé : la décision se prend PASSAGE PAR
    # PASSAGE (`sensitivity.controler`). Les autres sont retirés AVANT de construire
    # l'invite, et comptés pour le dire. Le tri se fait sur tout le classement, pas sur
    # les `k` premiers : un passage écarté ne prend pas la place d'un passage permis,
    # sans quoi l'invite partirait appauvrie.
    local = llm.est_local(provider)
    if local:
        chunks, ecartes = semantic.retrieve(question, k=k, project_id=project_id), 0
        envois = _envois(chunks)
    else:
        chunks, envois, ecartes = [], [], 0
        candidats = semantic.retrieve(question, k=semantic.RRF_PROFONDEUR, project_id=project_id)
        for c, envoi in zip(candidats, _envois(candidats)):
            try:
                sensitivity.controler(envoi, local=False)
            except sensitivity.EnvoiRefuse:
                ecartes += 1
                continue
            chunks.append(c)
            envois.append(envoi)
            if len(chunks) == k:
                break
        if ecartes and not chunks:
            raise PassagesUltraConfidentiels(sensitivity.REFUS_CLOUD)
    if not chunks:
        scope = " dans le projet sélectionné" if isinstance(project_id, str) else ""
        return {
            "answer": f"Aucun passage pertinent trouvé dans les transcripts{scope} pour cette question.",
            "sources": [],
        }

    # L'origine vient de l'index, jamais supposée : un passage de compte-rendu
    # (généré) ne doit pas se présenter au modèle comme une parole transcrite.
    context = "\n\n".join(
        f"[{i + 1}] ({'compte-rendu généré' if c.get('origine') == 'compte-rendu' else 'transcript'}"
        f" « {c['title']} »"
        + (f", projet « {c['project']} »" if not isinstance(project_id, str) else "")
        + (f", à {c['ts']}" if c.get("ts") else "")
        + f")\n{c['text']}"
        for i, c in enumerate(chunks)
    )
    prompt = (
        f"Extraits disponibles :\n\n{context}\n\n"
        f"Question : {question}\n\n"
        "Réponds à la question en te basant uniquement sur ces extraits, "
        "en citant tes sources avec [1], [2]… Réponds dans la langue de la question."
    )
    if langue := langue_de(question):
        prompt += f" {_CONSIGNE_LANGUE[langue]}"

    with resources.heavy(label="traitement"):
        # Une seule branche. Le chemin local doublait la façade sans rien y
        # gagner — mêmes arguments, même appel — et coûtait à ce module la
        # connaissance d'une implémentation.
        cle = llm.cle_mistral(provider, mistral_api_key)
        if not llm.is_available(provider=provider, api_key=cle):
            raise llm.LLMError(f"Le service LLM ({provider}) n'est pas disponible.")
        # Une exécution FinOps à elle : hors exécution, la télémétrie est un no-op, et
        # l'appel cloud de l'Assistant échappait au suivi. Elle porte aussi la trace d'un
        # envoi ultra-confidentiel autorisé. Jamais de document : une réponse en cite
        # plusieurs. Le projet seulement pour une portée « projet » (une identité) : une
        # portée « tous » (`frozenset`) ou un partage (`PorteeLecture`) en couvre
        # plusieurs, l'exécution reste sans projet (vue Usage globale seulement).
        with telemetry.execution(kind="assistant", provider=provider,
                                 project_id=project_id if isinstance(project_id, str) else None,
                                 model=llm.modele_servi(provider)):
            # La façade relit l'autorisation de chaque passage juste avant l'envoi.
            answer = llm.chat(_SYSTEM, prompt, provider=provider, api_key=cle, envoi=envois)

    sources = [
        {
            "n": i + 1,
            "name": c["name"],
            "title": c["title"],
            "ts": c.get("ts"),
            # `retrieve` rend désormais un RANG de fusion et non une
            # similarité : un score RRF n'est pas une distance et ne se compare
            # pas d'une requête à l'autre. Le publier comme « score » ferait
            # croire à une confiance qu'il ne porte pas.
            #
            # `c.get("score")` était resté ici après le renommage : il rendait
            # `None` pour chaque citation, silencieusement. Attrapé par le lint,
            # pas par un test — aucun ne regardait ce champ.
            "rang": c.get("rang"),
            "origine": c.get("origine", "transcript"),
            "excerpt": extrait(c["text"], answer),
            "document_id": c.get("document_id"),
            "meeting_id": c.get("meeting_id"),
            "project_id": c.get("project_id"),
            "project": c.get("project"),
        }
        for i, c in enumerate(chunks)
    ]
    reponse = {"answer": answer, "sources": sources}
    if ecartes:  # absent quand rien n'est écarté
        reponse["passages_ecartes"] = ecartes
    return reponse
