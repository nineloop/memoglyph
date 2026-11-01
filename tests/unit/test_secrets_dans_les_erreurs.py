"""Une erreur d'API ne doit jamais publier la clé.

Le défaut : les erreurs Mistral passaient pour normalisées
« sans recopier le message d'origine ». C'était vrai du
`except Exception` de la façade — mais il n'est atteint qu'APRÈS
`except MistralLLMError`, qui faisait `str(exc)` sur un message construit avec
`response.text`, c'est-à-dire le corps brut de la réponse API.

Le chemin le plus PROBABLE (une erreur HTTP 401 ou 429) contournait donc
l'assainissement et remontait jusqu'au 503 rendu à l'utilisateur.
"""

from __future__ import annotations

import pytest

from core import llm_mistral

# Assemblée à l'exécution : le contrôle d'export ne doit pas y lire une clé.
CLE = "sk-" + "abcdefghijklmnopqrstuvwxyz0123456789"


@pytest.fixture(autouse=True)
def _cle_configuree(monkeypatch):
    monkeypatch.setattr(llm_mistral, "MISTRAL_API_KEY", CLE)


def test_la_cle_configuree_est_effacee() -> None:
    """Cas réel : une API qui renvoie l'en-tête d'authentification en écho."""
    corps = f'{{"error":"invalid_api_key","provided":"{CLE}"}}'

    propre = llm_mistral._sans_secret(corps)

    assert CLE not in propre
    assert "***" in propre


def test_un_jeton_porteur_est_efface_meme_inconnu() -> None:
    """La clé n'est pas toujours celle qu'on a configurée : un proxy, un autre
    compte, un jeton de session peuvent apparaître dans le corps."""
    propre = llm_mistral._sans_secret(
        "Authorization: Bearer zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz refused")

    assert "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz" not in propre


def test_le_message_est_borne() -> None:
    """Un corps d'erreur peut faire des kilo-octets ; un message d'erreur n'est
    pas un journal, et un 503 illisible n'aide personne."""
    # Du texte RÉALISTE : une longue suite de « a » forme un jeton au sens de
    # la regex et se fait effacer avant d'être tronquée. Mon premier exemple
    # testait donc le rédacteur, pas la borne.
    corps = " ".join(["service temporarily unavailable, retry later."] * 200)

    propre = llm_mistral._sans_secret(corps)

    assert len(propre) <= 320
    assert propre.endswith("…")


def test_le_diagnostic_utile_survit() -> None:
    """Assainir ne doit pas rendre l'erreur inutilisable : le code HTTP et la
    nature du problème doivent rester lisibles."""
    propre = llm_mistral._sans_secret('{"error":{"type":"rate_limit_exceeded"}}')

    assert "rate_limit_exceeded" in propre


# ---------- Motif nº1 : dériver de la RÉALITÉ, pas d'une liste de noms ----------
#
# Les deux contrôles remplacés ici balayaient la SOURCE ligne par ligne :
#
#   · l'un cherchait `{response.text|data|cleaned}` — trois noms de variables
#     GELÉS. Interpoler `body`, `payload` ou `contenu` passait sans un mot ;
#   · l'autre collectait les appels `_sans_secret(x)` à un seul argument et
#     asseyait `assert not nus`. Supprimer TOUS les appels rendait la liste
#     vide, donc le test vert. Le contrôle du garde-fou disparaissait avec lui.
#
# On dérive maintenant de l'AST : toute valeur INTERPOLÉE dans une erreur levée
# doit passer par `_sans_secret` avec au moins deux arguments (la valeur et la
# clé active). La règle ne dépend d'aucun nom de variable.

# ---------- Règle STRUCTURELLE : le constructeur n'accepte que des littéraux ----------
#
# Ce bloc remplace un mini-analyseur de flux maison. Il a fallu l'étendre
# QUATRE fois en une journée — noms gelés, concaténation et `.format()`,
# portées de fonction, ordre des réaffectations — et à chaque fois une revue
# extérieure trouvait la forme suivante. Trois restaient invisibles au moment
# de la rupture, toutes mesurées :
#
#     msg: str = f"boom {secret}"      → []   (annotation)
#     raise E(msg := f"boom {secret}") → []   (walrus)
#     raise E("".join(["boom ", x]))   → []   (join)
#
# **Chaque trou protégeait précisément une fuite de clé.** Compléter une
# dixième fois n'aurait pas fermé le motif. Règle retenue, portée par
# `_erreur_sure` dans `core/llm_mistral.py` : on inverse le problème.
#
# « Retrouver l'origine de toute chaîne » devient « refuser toute chaîne dont
# l'origine n'est pas manifestement littérale ». Ce test n'a plus besoin de
# comprendre ni le flux ni la composition.

# La SEULE fonction autorisée à composer un message dynamique — c'est elle qui
# assainit. L'exemption est nommée, pas déduite : un motif du genre « toute
# fonction commençant par `_erreur` » rouvrirait la porte au premier helper qui
# y ressemble.
CONSTRUCTEUR_SUR = "_erreur_sure"


def _arguments_de_construction() -> list[tuple[int, str]]:
    """[(ligne, expression)] pour chaque `MistralLLMError(...)` HORS du
    constructeur sûr."""
    import ast
    import inspect

    arbre = ast.parse(inspect.getsource(llm_mistral))

    # Les nœuds INTÉRIEURS au constructeur sûr, exclus par identité.
    #
    # Sauter simplement sa « portée » ne suffisait pas : `ast.walk` depuis le
    # module descend dans TOUTES les fonctions, y compris celle-là. L'exclusion
    # doit porter sur les nœuds eux-mêmes.
    exemptes = set()
    for noeud in ast.walk(arbre):
        if isinstance(noeud, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and noeud.name == CONSTRUCTEUR_SUR:
            exemptes |= {id(n) for n in ast.walk(noeud)}

    trouves: list[tuple[int, str]] = []
    for noeud in ast.walk(arbre):
        if not isinstance(noeud, ast.Call) or id(noeud) in exemptes:
            continue
        nom = getattr(noeud.func, "id", None) or getattr(noeud.func, "attr", None)
        if nom != "MistralLLMError":
            continue
        for argument in noeud.args:
            trouves.append((argument.lineno, ast.unparse(argument)))
    return trouves


def _est_litteral(expression: str) -> bool:
    """Vrai si l'expression est une CONSTANTE — pas une chaîne composée.

    Une f-string sans champ (`f"texte"`) est acceptée : elle ne porte aucune
    valeur. Dès qu'un champ apparaît, elle est refusée.
    """
    import ast

    noeud = ast.parse(expression, mode="eval").body
    if isinstance(noeud, ast.Constant):
        return isinstance(noeud.value, str)
    if isinstance(noeud, ast.JoinedStr):
        return all(isinstance(m, ast.Constant) for m in noeud.values)
    return False


def test_MistralLLMError_ne_recoit_que_des_litteraux() -> None:
    """La règle structurelle. Toute donnée dynamique passe par `_erreur_sure`.

    Elle est CONSERVATRICE, et c'est voulu : une forme légitime refusée coûte
    un appel au constructeur sûr, une forme dangereuse acceptée coûte la clé.
    """
    fautes = [
        f"llm_mistral.py:{ligne} → {expression}"
        for ligne, expression in _arguments_de_construction()
        if not _est_litteral(expression)
    ]
    assert fautes == [], (
        "argument NON LITTÉRAL passé à `MistralLLMError` — la clé peut fuir. "
        "Utilisez `_erreur_sure(prefixe_litteral, valeur, cle)` :\n  "
        + "\n  ".join(fautes)
    )


def test_le_balayage_VOIT_bien_les_constructions() -> None:
    """ANCRE DE NON-VACUITÉ.

    `assert fautes == []` est vrai si le balayage ne trouve plus rien —
    module renommé, exception renommée, AST changé. C'est exactement ainsi que
    les contrôles précédents devenaient creux.
    """
    trouves = _arguments_de_construction()
    assert len(trouves) >= 2, (
        f"seulement {len(trouves)} construction(s) vue(s) — le balayage ne "
        f"regarde plus le bon code : {trouves}")


def test_toute_donnee_dynamique_passe_par_le_constructeur_SUR() -> None:
    """L'autre moitié : `_erreur_sure` doit être réellement utilisé.

    Sans ce test, supprimer tous ses appels rendrait le contrôle précédent
    vert — il ne resterait que des littéraux. C'est le piège dans lequel une
    version antérieure est tombée : `assert not []`.
    """
    import ast
    import inspect

    arbre = ast.parse(inspect.getsource(llm_mistral))
    appels = [
        n for n in ast.walk(arbre)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_erreur_sure"
    ]
    assert len(appels) >= 5, (
        f"seulement {len(appels)} appel(s) à `_erreur_sure` — les erreurs "
        f"dynamiques ont-elles disparu, ou contournent-elles le garde ?")
    for appel in appels:
        assert len(appel.args) >= 2, (
            f"llm_mistral.py:{appel.lineno} — `_erreur_sure` sans valeur à "
            f"assainir : {ast.unparse(appel)}")
        # Le PRÉFIXE doit être littéral, comme la valeur doit être assainie.
        #
        # La rupture n'avait fermé que la SECONDE entrée de la fabrique. Le
        # premier argument entrait verbatim, et le test exemptait toute la
        # fonction : `_erreur_sure(f"prefix {cle} : ", "corps", cle)` publiait
        # la clé. Reproduit.
        prefixe = ast.unparse(appel.args[0])
        assert _est_litteral(prefixe), (
            f"llm_mistral.py:{appel.lineno} — préfixe NON littéral : "
            f"{prefixe}. Toute valeur dynamique, y compris un `status_code`, "
            f"passe par le second argument et donc par `_sans_secret`.")


@pytest.mark.parametrize("expression,litteral", [
    ('"texte simple"', True),
    ('f"texte sans champ"', True),
    ('f"boom {contenu}"', False),
    ('"boom " + contenu', False),
    ('"boom {}".format(contenu)', False),
    ('msg', False),
    ('(msg := f"boom {secret}")', False),
    ('"".join(["boom ", secret])', False),
    ('_sans_secret(data, key)', False),
])
def test_le_detecteur_de_litteral_tranche_bien(expression: str, litteral: bool) -> None:
    """Les TROIS formes qui échappaient au flux — annotation, walrus, join —
    plus celles qu'il voyait. Aucune n'a besoin d'un cas syntaxique dédié :
    elles échouent toutes pour la même raison, ne pas être une constante.
    """
    assert _est_litteral(expression) is litteral, expression


def test_l_exemption_ne_couvre_QUE_le_constructeur_sur() -> None:
    """Une exemption large rouvrirait la porte.

    `_erreur_sure` est nommé, pas déduit d'un motif : une règle du genre
    « toute fonction commençant par `_erreur` » exempterait le premier helper
    qui y ressemble.
    """
    import ast
    import inspect

    arbre = ast.parse(inspect.getsource(llm_mistral))
    exemptes = [
        n.name for n in ast.walk(arbre)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == CONSTRUCTEUR_SUR
    ]
    assert exemptes == [CONSTRUCTEUR_SUR], exemptes

    # Et le balayage doit VOIR les constructions hors exemption : si l'exemption
    # devenait large, `_arguments_de_construction` rendrait une liste vide et
    # la règle serait vide de sens.
    assert len(_arguments_de_construction()) >= 2


def _erreur_sure_reelle(prefixe: str, brut: object, *actives):
    """Raccourci vers la vraie fabrique, pour les tests d'exécution."""
    return llm_mistral._erreur_sure(prefixe, brut, *actives)


def test_le_constructeur_sur_efface_la_cle_configuree(monkeypatch) -> None:
    cle = "sk-" + "configuree-1234567890"
    monkeypatch.setattr(llm_mistral, "MISTRAL_API_KEY", cle)
    erreur = _erreur_sure_reelle("Erreur : ", f"echo {cle} ici",
                                 llm_mistral.MISTRAL_API_KEY)
    assert cle not in str(erreur)
    assert "Erreur : " in str(erreur)


def test_le_constructeur_sur_efface_une_cle_BYOK_courte_et_ponctuee() -> None:
    courte = "abc.def+123"
    erreur = _erreur_sure_reelle("Erreur : ", f"corps contenant {courte}", courte)
    assert courte not in str(erreur)


def test_le_constructeur_sur_borne_un_corps_long() -> None:
    erreur = _erreur_sure_reelle("Erreur : ", "x" * 5000)
    assert len(str(erreur)) < 500, len(str(erreur))


def test_le_constructeur_sur_efface_la_cle_dans_le_PREFIXE() -> None:
    """Contre-exemple exact du préfixe non littéral, rejoué.

    Le test AST impose un préfixe littéral à l'appel ; cet assainissement-ci
    couvre ce qui passerait quand même — réflexion, appel dynamique, futur
    appelant hors du module. Deux verrous plutôt qu'un, sur une frontière de
    secrets.
    """
    secret = "byok.short+secret"
    erreur = _erreur_sure_reelle(f"prefix {secret} : ", "corps sain", secret)
    assert secret not in str(erreur), str(erreur)


def test_le_constructeur_sur_garde_le_diagnostic_utile() -> None:
    """Assainir ne doit pas rendre le message inutile."""
    erreur = _erreur_sure_reelle("Erreur API Mistral (429) : ", "rate limit exceeded")
    assert "429" in str(erreur) and "rate limit" in str(erreur)


def test_une_cle_courte_ou_ponctuee_est_effacee_aussi(monkeypatch) -> None:
    """Le motif générique ne suffit pas, et une mutation l'a prouvé.

    Il n'attrape que les suites de 32 caractères alphanumériques. Une clé
    courte, ou contenant un point ou un `+`, y échappe entièrement — seul le
    remplacement de la clé RÉELLEMENT configurée la couvre. Sans ce test,
    supprimer ce remplacement ne faisait échouer aucun test.
    """
    courte = "abc.def+123"
    monkeypatch.setattr(llm_mistral, "MISTRAL_API_KEY", courte)

    propre = llm_mistral._sans_secret(f'{{"error":"bad key {courte}"}}')

    assert courte not in propre
    assert "***" in propre


def test_une_cle_passee_par_requete_est_effacee_aussi(monkeypatch) -> None:
    """Le cas déterminant : BYOK.

    Une première correction n'effaçait que la clé GLOBALE. Or `chat()` et
    `embed()` acceptent un `api_key` par requête. Une clé passée ainsi, courte
    ou ponctuée, échappait au remplacement de la globale ET aux motifs
    génériques — et l'amont peut la réimprimer dans `response.text`.

    Clé globale A, appel avec clé B : c'est B qui doit disparaître.
    """
    monkeypatch.setattr(llm_mistral, "MISTRAL_API_KEY", "globale-" + "z" * 40)
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    byok = "abc.def+123"

    propre = llm_mistral._sans_secret(f'{{"error":"bad key {byok}"}}', byok)

    assert byok not in propre
    assert "***" in propre


# ---------- Ce que la rupture a RETIRÉ, et pourquoi ----------
#
# Quatre tests vivaient ici : `test_toute_frontiere_d_erreur_transmet_la_cle_active`,
# `test_deux_affectations_du_meme_nom_respectent_l_ORDRE_source`,
# `test_une_branche_CONDITIONNELLE_est_traitee_par_prudence` et
# `test_un_RAISE_dans_un_except_est_bien_vu`.
#
# Ils vérifiaient le mini-analyseur de flux — portées, ordre des
# réaffectations, gestionnaires `except`. Cet analyseur n'existe plus : la
# règle structurelle rend ces questions sans objet, puisqu'un argument non
# littéral est refusé quelle que soit la manière dont il a été composé.
#
# Les retirer est délibéré, et c'est la seule chose honnête à faire : les
# garder aurait donné quatre tests verts sur du code disparu — précisément la
# forme de contrôle creux que cette journée a passé à traquer.
#
# Ce qu'ils protégeaient reste couvert, autrement :
#   · les formes de composition        → `test_le_detecteur_de_litteral_tranche_bien`
#   · l'assainissement effectif        → les quatre tests d'exécution de `_erreur_sure`
#   · l'usage RÉEL du constructeur sûr → `test_toute_donnee_dynamique_passe_par_le_constructeur_SUR`
