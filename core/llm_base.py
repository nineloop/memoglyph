"""Types et interface partagés par les moteurs de génération de texte (LLM).

Jumeau de `core/stt_base.py`, et pour la même raison : le contrat vit dans un
module NEUTRE, qu'aucune implémentation ne possède. Ici, il ne l'était pas —
`core/llm.py` faisait `LLMError = llm_ollama.LLMError`, si bien que le type
d'erreur générique de tout le dépôt appartenait au module d'UN moteur.
"""

from __future__ import annotations

import contextvars
import json
from typing import Any, Optional, Protocol

# Une clôture Markdown complète. Un modèle qui l'ouvre écrit ces trois
# caractères ; un modèle qui la referme MAL en écrit un ou deux.
_CLOTURE = "```"

# Les deux formes d'enveloppe, nommées comme les opérations de
# `reparation_json._NOMS` : un journal se lit à trois heures du matin.
OUVRANTE = "opening_fence"
CLOTURE_RESIDUELLE = "residual_fence"

# ── Observer le déballage, parce qu'un PASS_DIRECT muet est un angle mort ────
#
# Une sortie déballée puis valide rend `PASS_DIRECT` : aucune reprise n'est
# payée, et jusqu'ici RIEN n'en gardait trace. C'est exactement l'angle mort
# dont se garde `core/reparation_json.py` — un filet qui travaille de plus en
# plus masquerait une dégradation du modèle. Une enveloppe n'est pas une faute
# de structure, et ne doit jamais être comptée comme telle ; mais sa FRÉQUENCE
# est un signal, et un signal qu'on ne relève pas n'existe pas.
#
# Pourquoi pas un paramètre de `chat_json` : la docstring de `LLMEngine` réduit
# délibérément la surface du contrat à ce que la production appelle vraiment.
# Faire traverser une sortie de télémétrie à la façade et à chaque moteur
# élargirait ce contrat pour un besoin qui n'est pas le sien.
#
# Pourquoi pas une variable de module : l'API est asynchrone, deux
# comptes-rendus peuvent se générer de front, et un global attribuerait
# l'enveloppe de l'un au journal de l'autre. `ContextVar` est isolé par
# contexte d'exécution — c'est la seule forme correcte ici.
_ENVELOPPE_OBSERVEE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "memoglyph_enveloppe_observee", default="")


def oublier_enveloppe_observee() -> None:
    """À appeler AVANT une génération, sinon le relevé traîne.

    Sans cet oubli, l'enveloppe d'une tentative précédente serait attribuée à
    la suivante — et un modèle qui a cessé d'en produire paraîtrait continuer.
    """
    _ENVELOPPE_OBSERVEE.set("")


def enveloppe_observee() -> str:
    """Ce que le dernier déballage de ce contexte a retiré. `""` s'il n'a rien
    retiré."""
    return _ENVELOPPE_OBSERVEE.get()


def retirer_enveloppe_markdown(texte: str) -> str:
    """Retirer l'ENVELOPPE de transport d'une sortie JSON. Jamais son contenu.

    Règle : cette fonction vit ici, dans le module que ne possède
    aucun moteur, parce que les deux moteurs en portaient jusqu'ici une copie
    littérale. Une règle de nettoyage dupliquée dérive : la corriger d'un côté
    seulement produirait deux pipelines qui n'acceptent pas les mêmes sorties,
    exactement ce que le contrat commun existe pour empêcher. La symétrie est
    désormais STRUCTURELLE, pas recopiée.

    ## Deux formes, et pourquoi la seconde a fallu l'ajouter

    1. **Enveloppe ouvrante** — le modèle encadre son JSON de ```…```. Forme
       historique, comportement inchangé.

    2. **Clôture résiduelle** — le modèle n'émet QUE la clôture finale, et
       partielle. MESURÉ : Granite 4.1 3B rend 744 caractères de JSON
       parfaitement valide à 6 sections, suivis d'un unique backtick. Le
       document ne COMMENÇANT pas par ```, la règle 1 ne retirait rien, et
       `json.loads` levait `Extra data @744`.

       Ce cas n'est pas une faute de balance — accolades et crochets hors
       chaîne sont à 7/7 et 7/7 — donc `core/reparation_json.py` le refuse à
       raison, et sa docstring dit explicitement qu'il n'entre pas dans son
       périmètre. C'est un défaut de TRANSPORT : il se traite là où l'on
       déballe le transport, ici.

    ## Ce qui rend le retrait SÛR, et non seulement plausible

    Le dépôt refuse par défaut : on ne retire rien dont on ne puisse prouver
    que ce n'est pas du contenu. La preuve tient en une phrase, et c'est le
    critère d'acceptation lui-même :

        le reste, une fois la clôture ôtée, est un document JSON COMPLET.

    Si `json.loads` accepte le reste, alors tout caractère retiré se situe
    strictement APRÈS la fin d'un document complet. Or tout le contenu d'un
    document JSON vit à l'intérieur de la valeur analysée. Un caractère
    au-delà ne peut donc pas en être du contenu — par construction, et vérifié
    par l'analyse, pas par une intention.

    C'est ce qui distingue cette règle d'un `rstrip('`')` : celui-ci mutilerait
    une sortie tronquée au milieu d'une citation contenant un backtick, là où
    celle-ci la laisse intacte parce que le reste ne parse pas.

    ## Ce qu'elle refuse, et pourquoi chaque refus compte

    - **tout caractère autre qu'un backtick** — et notamment `]` ou `}`. Trois
      sorties historiques de modèles locaux traînent un `]` ou un
      `]}` résiduel. Ce sont des symboles STRUCTURANTS : leur retrait est une
      faute de balance, il appartient à `core/reparation_json.py`, qui les
      répare déjà et sait le prouver. Les traiter ici fusionnerait une règle de
      transport et une règle de structure, et brouillerait les deux.
    - **plus de trois backticks** — au-delà, ce n'est plus une clôture, et on
      ne retire pas ce qu'on ne sait pas nommer.
    - **un reste qui ne parse pas** — on rend alors le texte INTACT, pour que
      le filet de balance le voie tel que le modèle l'a écrit.

    ## Une limite ASSUMÉE : clôture résiduelle ET faute de balance

    Une sortie qui porterait les deux à la fois n'est récupérée par AUCUN des
    deux filets, et il faut le dire plutôt que le laisser découvrir.

    Ici, le reste ne parse pas — puisqu'il est déséquilibré — donc la clôture
    n'est pas retirée et le texte part INTACT. Au filet de balance ensuite, le
    backtick n'est pas un symbole structurant : il ne compte dans aucun solde,
    et l'unique opération que le filet s'autorise ne rendrait toujours pas un
    document parsable. La sortie coûte donc une génération entière.

    Jamais observé : sur les cinquante sorties brutes conservées, une seule
    porte un backtick, et sa balance est parfaite (7/7 et 7/7).

    Et la limite ne se lève pas à bon compte. Il faudrait enchaîner les deux
    règles, donc retirer la clôture AVANT de savoir si le reste est un
    document — c'est-à-dire renoncer à la preuve même qui autorise ce retrait.
    Le prix serait de rendre `reparation_json` dépendant d'un texte déjà
    modifié par une autre règle, et sa comparaison des littéraux ne vaudrait
    plus. La limite est donc tenue, et dite.

    ## Comptabilité

    Une sortie déballée puis valide compte PASS_DIRECT, comme aujourd'hui pour
    une sortie encadrée de ```…```. C'est cohérent : l'enveloppe n'a jamais
    fait partie du document. Ce n'est PAS une réparation, et cela ne doit pas
    en prendre le nom — `PASS_REPAIRED` reste le signal, à surveiller, qu'une
    faute de STRUCTURE du modèle a dû être corrigée.

    Le déballage n'en est pas muet pour autant : il RELÈVE ce qu'il a retiré,
    et `core/report.py` le porte au journal de génération. Voir
    `enveloppe_observee` ci-dessus pour la raison de ce canal.
    """
    retire: list[str] = []
    nettoye = texte.strip()

    # Règle 1 — enveloppe ouvrante. Comportement historique, mot pour mot.
    if nettoye.startswith(_CLOTURE):
        nettoye = nettoye.split("\n", 1)[-1].rsplit(_CLOTURE, 1)[0].strip()
        retire.append(OUVRANTE)

    # Règle 2 — clôture résiduelle. Elle s'applique APRÈS la règle 1, et non à
    # sa place : une enveloppe ouverte par ``` et refermée par un seul backtick
    # laisse ce backtick derrière elle, `rsplit` n'ayant rien trouvé à couper.
    if nettoye.endswith("`"):
        sans_cloture = nettoye.rstrip("`")
        if len(nettoye) - len(sans_cloture) <= len(_CLOTURE):
            try:
                json.loads(sans_cloture)
            except json.JSONDecodeError:
                pass  # reste incomplet : on ne touche à rien, le filet le verra brut
            else:
                nettoye = sans_cloture.strip()
                retire.append(CLOTURE_RESIDUELLE)
        # plus de trois backticks : ce n'est pas une clôture, on ne retire rien

    _ENVELOPPE_OBSERVEE.set("+".join(retire))
    return nettoye


class LLMError(RuntimeError):
    """Panne d'un moteur LLM : injoignable, clé absente, réponse hors contrat.

    Ce type est le marqueur DE FAIT de « service dégradé, réponds 503 » dans ce
    dépôt : cinq `except` d'`api/main.py` le traduisent en HTTP 503, et trois
    exceptions en héritent pour bénéficier de la même traduction
    (`embed.EmbedIndisponible`, `semantic.SemanticIndexIncompatible`,
    `semantic.InvalidEmbedding`). Le laisser dans `llm_ollama` signifiait que
    retirer le moteur Ollama emportait la sémantique 503 du reste du code.

    `llm_ollama` le RÉ-EXPORTE, et il faut que ce soit la même classe et non un
    alias parallèle : un `except llm_ollama.LLMError` doit attraper l'erreur
    d'un moteur cloud, sans quoi une panne Mistral sortirait en 500 brut.
    """


class LLMMalformedOutputError(LLMError):
    """Le modèle a RÉPONDU, mais sa sortie est inexploitable.

    À distinguer d'une panne de service — injoignable, clé révoquée, quota
    épuisé — et c'est toute la raison d'être de ce type. Les deux levaient
    jusqu'ici la même erreur de moteur, ce qui rendait impossible un `except`
    correct chez l'appelant : on réessayait trois fois un service mort, ou
    jamais un JSON malformé.

    L'asymétrie qui le justifie : redemander à un service mort rend la MÊME
    panne et triple l'attente — avec `MLX_TIMEOUT` à 900 s, cela fait quarante-
    cinq minutes avant d'annoncer l'indisponibilité. Redemander à un modèle qui
    a bafouillé, en revanche, a de vraies chances d'aboutir : c'est MESURÉ, la
    panne du compte-rendu local change de mode d'une exécution à l'autre — JSON
    malformé, puis JSON valide mais hors contrat.

    Il reste un `LLMError`, donc les cinq `except` d'`api/main.py` continuent de
    le traduire en 503 sans rien savoir de lui.

    Les moteurs ne le lèvent qu'en UN point : quand leur boucle interne de
    réessai s'épuise sur du JSON malformé. Une erreur de transport, un modèle
    introuvable ou un serveur zombie gardent le type du moteur.

    ## Pourquoi l'erreur PORTE la sortie brute

    Le message la tronque à 300 caractères — c'est bon pour un journal, inutile
    pour agir. Or `core/report.py` sait réparer une faute de
    balance sur une sortie complète plutôt que de payer une génération entière.
    Pour cela il lui faut le texte ENTIER et le motif d'arrêt, et ce sont les
    deux seules choses que le moteur possède et que le contrat perdait.

    `finish_reason` est indispensable ET non substituable : sans lui, on ne
    distingue pas un document complet mal fermé d'un document coupé au milieu.
    Le premier se répare, le second jamais. Les moteurs qui ne l'exposent pas
    laissent `None`, et le réparateur refuse — on n'invente pas une complétude.
    """

    def __init__(self, *args: Any, brut: str = "",
                 finish_reason: Optional[str] = None) -> None:
        super().__init__(*args)
        self.brut = brut
        self.finish_reason = finish_reason


class LLMEngine(Protocol):
    """Contrat commun à tous les moteurs de génération de texte.

    La surface est volontairement réduite à ce que la production appelle
    vraiment, mesuré par grep : `chat`, `chat_json`, `is_available`. Tout le
    reste (`warm_up`, `ensure_ready`, `list_models`, `ctx_for`, `embed`) n'a
    aucun appelant de production, ou relève d'un autre métier, et reste sur
    l'implémentation qui le porte — les CLI d'audit les utilisent directement.

    Un contrat plus large aurait été plus coûteux à honorer pour le prochain
    moteur sans rendre un seul service : `ensure_ready` nomme `ollama serve`
    dans ses messages, et `embed` suppose un espace vectoriel scellé que
    `core/embed.py` gère seul.

    `Protocol` est du typage STRUCTUREL : rien ne le vérifie à l'exécution, et
    `make check` ne type-check pas. `tests/unit/test_llm_interface.py` exerce
    donc chaque moteur du registre à travers la façade — le même choix, et pour
    la même raison, que `tests/unit/test_stt_contrat.py`.
    """

    name: str

    def is_available(self, *, api_key: Optional[str] = None) -> bool:
        """Le moteur peut-il répondre ? Ne lève jamais : il RÉPOND faux."""
        ...

    def pret_declare(self) -> bool:
        """Cet environnement est-il ÉQUIPÉ pour ce moteur ? Sans rien démarrer.

        Une question différente de `is_available`, et le nom porte la
        différence : ceci est DÉCLARATIF — un paquet installé, une clé posée, un
        démon qui répond — quand `is_available` est PROUVÉ, par une génération
        réelle. Un moteur peut être déclaré prêt et échouer à l'usage ; l'inverse
        n'arrive pas.

        La distinction n'est pas théorique. `MlxEngine.is_available()` démarre
        un `mlx_lm.server` et attend deux jetons : c'est le bon contrat pour un
        appel réel, et un désastre pour `/api/config`, que l'interface appelle à
        chaque chargement — 2,1 Go de poids par ouverture de page.

        Contraintes, dans cet ordre :

        1. **ne démarre aucun processus et ne charge aucun modèle** — c'est la
           raison d'être de la méthode, et un test le vérifie en piégeant le
           démarrage plutôt qu'en regardant la valeur rendue ;
        2. **ne lève jamais** — appelée par la route de configuration, sans
           `try`, pour tous les moteurs à la fois : une exception y rendrait un
           500 sur une page qui doit toujours s'afficher ;
        3. **rapide** — un appel réseau reste permis s'il est local et borné
           (le démon Ollama), jamais un aller-retour vers un service distant.
        """
        ...

    def modele_servi(self, model: Optional[str] = None) -> str:
        """Le nom du modèle que CE moteur servira réellement pour cet appel.

        Le contrat ci-dessus est volontairement étroit, et son critère est
        d'élargir seulement si cela rend un service. Celui-ci en rend un, et il
        a été payé trois fois.

        `core/pipeline.py`, `core/report.py` et `core/provenance.py` calculaient
        chacun leur modèle par un `if provider in ("mistral", "cloud") ... else
        config.LLM_MODEL`. Le partage est BINAIRE : à l'arrivée du troisième
        moteur, un document produit par MLX a annoncé `qwen3.5:9b` — un modèle
        qui n'avait pas tourné — dans son front-matter ET dans son fingerprint.
        Le défaut avait DÉJÀ été corrigé une fois pour Mistral,
        par un `if/else` que le fournisseur suivant a rouvert.

        La résolution appartient donc au moteur, seul à savoir ce qu'il sert, et
        non à une table tenue ailleurs — une table serait un second endroit à
        oublier, soit exactement le mode de défaillance qu'on ferme ici.

        Ne lève jamais et rend toujours une chaîne non vide : un appelant s'en
        sert pour ÉCRIRE la provenance dans un document autoritatif, et une
        exception y remplacerait une vérité par une panne.
        """
        ...

    def chat(
        self,
        system: str,
        user: str,
        *,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        seed: Optional[int] = None,
    ) -> str:
        ...

    def chat_json(
        self,
        system: str,
        user: str,
        *,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        max_tokens: Optional[int] = None,
        schema: Optional[dict] = None,
        retries: int = 2,
    ) -> dict[str, Any]:
        """Réponse JSON parsée, avec ses contraintes de sortie.

        Les trois derniers arguments sont l'exacte raison pour laquelle
        `core/report.py` court-circuitait la façade : sans eux, l'interface
        n'était pas l'unique porte. Ils sont un CONTRAT, pas une option — un
        moteur qui les accepterait pour les ignorer produirait un compte-rendu
        hors contrat sans qu'aucun signal ne l'annonce. La manière d'y répondre
        dépend du moteur (grammaire de décodage, directive de prompt…), et
        chaque implémentation documente la sienne.
        """
        ...


class LLMEngineDechargeable(LLMEngine, Protocol):
    """Capacité OPTIONNELLE : rendre la mémoire tenue par le modèle.

    Séparée du contrat requis, parce qu'elle n'a de sens que pour un moteur
    local — un moteur cloud ne tient rien à rendre. `llm.unload()` dispatche
    sur la présence de la méthode et ne fait rien si elle manque : le
    déchargement arrive en fin de pipeline, quand le transcript est déjà écrit,
    et ne doit jamais faire échouer ce qui a réussi.
    """

    def unload(self) -> bool:
        ...
