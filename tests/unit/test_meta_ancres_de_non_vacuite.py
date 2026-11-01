"""Un test qui affirme « la liste des fautes est vide » doit prouver qu'il regarde.

Motif nº2 des portes mortes.

`assert fautes == []` est vrai de deux façons : parce qu'il n'y a pas de faute,
ou parce que le balayage ne voit plus rien. Les deux sont VERTS et
indiscernables. Sept des dix-sept portes mortes relevées avaient cette forme :

- `test_secrets_dans_les_erreurs.py` cherchait `_sans_secret(x)` à un argument
  et asseyait `assert not nus` — supprimer TOUS les appels vidait la liste ;
- `test_library_hierarchie.py` construisait `expected` DEPUIS la liste qu'il
  comparait, une tautologie ;
- `test_profil_ci_suffit.py` en aurait souffert sans son ancre.

Le remède : toute assertion de vacuité doit être accompagnée d'une ANCRE — une
assertion qui échoue si la source du balayage devient vide.

Ce test est un test-MÉTA : il lit les autres tests par AST et exige l'ancre.
"""

from __future__ import annotations

import ast
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]

# Fonctions de test dispensées d'ancre, avec leur raison. Une dispense doit
# décrire pourquoi la vacuité de la source est IMPOSSIBLE ou sans conséquence.
DISPENSES = {
    # La source est un littéral écrit dans le test lui-même : elle ne peut pas
    # devenir vide sans que le test cesse d'exister.
    "test_le_parseur_ignore_les_COMMENTAIRES_du_ddl",
    # Deux entrées ont été retirées : les tests qu'elles
    # dispensaient ont disparu avec le mini-analyseur de flux, remplacé par la
    # règle structurelle des erreurs Mistral. C'est
    # `test_les_dispenses_correspondent_a_des_tests_EXISTANTS` qui l'a signalé
    # — le garde a fait son travail sur sa propre liste.
}

# Noms d'ancre reconnus : une assertion sur une TAILLE, ou une appartenance
# explicite. C'est ce qui distingue « j'ai regardé et n'ai rien trouvé » de
# « je n'ai pas regardé ».
_APPELS_DE_TAILLE = {"len", "sum", "count"}

# Le contrôle ne vise QUE les balayages du monde extérieur.
#
# Première version : toute `assert x == []` dans un test. Elle signalait
# 83 fonctions, dont `test_empty_body_has_no_chunks` — où l'entrée est vide PAR
# CONSTRUCTION et c'est justement le sujet. Un contrôle qui crie sur tout se
# fait désactiver ; celui-ci doit crier sur ce qui compte.
#
# Le danger n'existe que si la collection est DÉRIVÉE d'un balayage : fichiers
# du disque, source lue par AST, lignes d'une base, sortie d'un sous-processus.
# Là, une source devenue vide rend l'assertion vraie sans rien prouver.
# `run`, `parse`, `splitlines` et `read_text` ont été RETIRÉS : trop
# génériques, ils attrapaient `JobManager.run` ou la lecture d'un fichier connu.
# Un détecteur bruyant se fait désactiver — donc il ne vise que les balayages
# dont la vacuité est réellement possible et silencieuse.
_BALAYAGES = {
    "glob", "rglob", "iterdir", "listdir", "walk", "scandir",
    "getsource", "check_output",
}


def _fichiers_de_test() -> list[Path]:
    return sorted(RACINE.glob("tests/**/*.py"))


def _est_assertion_de_vacuite(noeud: ast.Assert) -> bool:
    """`assert x == []`, `assert x == {}`, `assert not x`, `assert x == set()`."""
    test = noeud.test
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return isinstance(test.operand, (ast.Name, ast.Attribute, ast.Subscript))
    if isinstance(test, ast.Compare) and len(test.ops) == 1:
        if not isinstance(test.ops[0], ast.Eq):
            return False
        droite = test.comparators[0]
        if isinstance(droite, (ast.List, ast.Dict)) and not getattr(droite, "elts", None) \
                and not getattr(droite, "keys", None):
            return True
        if isinstance(droite, ast.Call) and getattr(droite.func, "id", None) == "set" \
                and not droite.args:
            return True
    return False


def _porte_une_ancre(portee: ast.AST) -> bool:
    """Une assertion sur une TAILLE, ou une appartenance explicite."""
    for noeud in ast.walk(portee):
        if not isinstance(noeud, ast.Assert):
            continue
        for sous in ast.walk(noeud.test):
            if isinstance(sous, ast.Call) and getattr(sous.func, "id", None) in _APPELS_DE_TAILLE:
                return True
            if isinstance(sous, ast.Compare) and any(
                    isinstance(op, (ast.In, ast.NotIn, ast.GtE, ast.Gt)) for op in sous.ops):
                return True
    return False


def _balaye_le_monde(fonction: ast.FunctionDef) -> bool:
    """La fonction lit-elle le disque, la source, une base ou un sous-processus ?

    On remonte aussi aux HELPERS appelés : `_fonctions_sans_ancre` ne balaye
    pas elle-même, elle appelle `_fichiers_de_test`. Un helper dont le nom
    évoque un balayage compte.
    """
    for noeud in ast.walk(fonction):
        if not isinstance(noeud, ast.Call):
            continue
        nom = getattr(noeud.func, "attr", None) or getattr(noeud.func, "id", None) or ""
        if nom in _BALAYAGES:
            return True
        if nom.startswith("_") and any(
                mot in nom for mot in ("fichier", "source", "balay", "imports",
                                       "colonne", "suivi", "module", "interpolation")):
            return True
    return False


def _helpers_appeles(fonction: ast.FunctionDef) -> set[str]:
    """Noms des fonctions locales appelées — le LIEN entre contrôle et ancre."""
    return {
        nom for noeud in ast.walk(fonction)
        if isinstance(noeud, ast.Call)
        and (nom := getattr(noeud.func, "id", None))
        and nom.startswith("_")
    }


def _fonctions_sans_ancre() -> list[str]:
    """Fonctions dont le balayage n'est ancré par RIEN de lié.

    L'ancre était cherchée à l'échelle du MODULE — or une assertion sans
    rapport, `assert len([1]) == 1` posée n'importe où, suffisait à exempter
    tous les balayages du fichier, présents et futurs.
    Le contrôle prouvait « ce module possède une assertion de taille », pas que
    le balayage surveillé était ancré.

    La relation est maintenant EXPLICITE : l'ancre doit être dans la fonction
    elle-même, ou dans une fonction voisine qui appelle le MÊME helper de
    balayage. Le découpage en deux tests reste possible — c'est le bon —, mais
    il doit être traçable.
    """
    fautes: list[str] = []
    for fichier in _fichiers_de_test():
        arbre = ast.parse(fichier.read_text(encoding="utf-8"))
        fonctions = [n for n in ast.walk(arbre)
                     if isinstance(n, ast.FunctionDef) and n.name.startswith("test")]
        # Helpers de balayage couverts par une ancre, quelque part dans le module.
        helpers_ancres: set[str] = set()
        for fonction in fonctions:
            if _porte_une_ancre(fonction):
                helpers_ancres |= _helpers_appeles(fonction)

        for noeud in fonctions:
            if noeud.name in DISPENSES:
                continue
            vacuites = [n for n in ast.walk(noeud)
                        if isinstance(n, ast.Assert) and _est_assertion_de_vacuite(n)]
            if not vacuites or not _balaye_le_monde(noeud):
                continue
            ancre_liee = (
                _porte_une_ancre(noeud)
                or bool(_helpers_appeles(noeud) & helpers_ancres)
            )
            if not ancre_liee:
                fautes.append(
                    f"{fichier.relative_to(RACINE)}:{vacuites[0].lineno} "
                    f"{noeud.name}")
    return fautes


def test_toute_assertion_de_vacuite_porte_une_ancre() -> None:
    """Le contrôle de motif lui-même."""
    fautes = _fonctions_sans_ancre()
    assert fautes == [], (
        "assertion(s) de vacuité sans ancre : ces tests sont verts si le "
        "balayage cesse de voir quoi que ce soit.\n  " + "\n  ".join(fautes)
    )


def test_le_meta_controle_regarde_bien_les_tests() -> None:
    """ANCRE de ce test-méta. Sans elle, il serait le premier de ses propres
    fautes : `assert fautes == []` sur un balayage vide."""
    fichiers = _fichiers_de_test()
    assert len(fichiers) > 40, f"seulement {len(fichiers)} fichier(s) de test"

    vacuites = 0
    for fichier in fichiers:
        arbre = ast.parse(fichier.read_text(encoding="utf-8"))
        vacuites += sum(1 for n in ast.walk(arbre)
                        if isinstance(n, ast.Assert) and _est_assertion_de_vacuite(n))
    assert vacuites >= 10, (
        f"seulement {vacuites} assertion(s) de vacuité détectée(s) — le "
        f"détecteur ne reconnaît plus les formes qu'il doit surveiller")

    # …et le filtre « balayage du monde » doit laisser passer QUELQUE CHOSE :
    # s'il n'attrapait plus aucune fonction, le contrôle principal serait vert
    # sur un ensemble vide — sa propre faute.
    surveillees = 0
    for fichier in fichiers:
        arbre = ast.parse(fichier.read_text(encoding="utf-8"))
        for noeud in ast.walk(arbre):
            if isinstance(noeud, ast.FunctionDef) and noeud.name.startswith("test"):
                if _balaye_le_monde(noeud) and any(
                        isinstance(n, ast.Assert) and _est_assertion_de_vacuite(n)
                        for n in ast.walk(noeud)):
                    surveillees += 1
    assert surveillees >= 5, (
        f"seulement {surveillees} fonction(s) sous surveillance — le filtre "
        f"« balayage » ne reconnaît plus les tests qu'il doit couvrir")


def test_le_detecteur_reconnait_les_QUATRE_formes_de_vacuite() -> None:
    """Garde sur l'instrument : une forme non reconnue est une porte ouverte."""
    formes = [
        "assert fautes == []",
        "assert manquants == {}",
        "assert not intrus",
        "assert restants == set()",
    ]
    for source in formes:
        noeud = ast.parse(source).body[0]
        assert _est_assertion_de_vacuite(noeud), f"forme non reconnue : {source}"

    # …et ne se déclenche PAS sur une assertion ordinaire.
    for source in ("assert x == 3", "assert nom in vus", "assert len(x) > 2"):
        noeud = ast.parse(source).body[0]
        assert not _est_assertion_de_vacuite(noeud), f"faux positif : {source}"


def test_les_dispenses_correspondent_a_des_tests_EXISTANTS() -> None:
    """Une dispense périmée est une porte ouverte sur un nom qu'on ne relit plus."""
    noms = set()
    for fichier in _fichiers_de_test():
        arbre = ast.parse(fichier.read_text(encoding="utf-8"))
        noms |= {n.name for n in ast.walk(arbre)
                 if isinstance(n, ast.FunctionDef) and n.name.startswith("test")}
    mortes = DISPENSES - noms
    assert mortes == set(), f"dispense(s) sans test correspondant : {sorted(mortes)}"


def test_une_ancre_SANS_RAPPORT_ne_blanchit_pas_un_balayage() -> None:
    """Le contre-exemple exact : une ancre sans rapport.

    `assert len([1]) == 1` posé n'importe où dans un module exemptait tous ses
    balayages vides. Le lien doit passer par le HELPER commun.
    """
    module = ast.parse(
        "def _balaye():\n"
        "    return list(RACINE.glob('**/*.py'))\n"
        "def test_controle():\n"
        "    fautes = [p for p in _balaye() if bad(p)]\n"
        "    assert fautes == []\n"
        "def test_sans_rapport():\n"
        "    assert len([1]) == 1\n"
    )
    fonctions = [n for n in ast.walk(module)
                 if isinstance(n, ast.FunctionDef) and n.name.startswith("test")]
    helpers_ancres = set()
    for f in fonctions:
        if _porte_une_ancre(f):
            helpers_ancres |= _helpers_appeles(f)
    controle = next(f for f in fonctions if f.name == "test_controle")
    assert not (_helpers_appeles(controle) & helpers_ancres), (
        "une ancre sans rapport blanchit encore le balayage")

    # …et une ancre LIÉE, elle, doit compter.
    lie = ast.parse(
        "def _balaye():\n"
        "    return list(RACINE.glob('**/*.py'))\n"
        "def test_controle():\n"
        "    fautes = [p for p in _balaye() if bad(p)]\n"
        "    assert fautes == []\n"
        "def test_ancre():\n"
        "    assert len(_balaye()) > 10\n"
    )
    fns = [n for n in ast.walk(lie)
           if isinstance(n, ast.FunctionDef) and n.name.startswith("test")]
    ancres = set()
    for f in fns:
        if _porte_une_ancre(f):
            ancres |= _helpers_appeles(f)
    ctrl = next(f for f in fns if f.name == "test_controle")
    assert _helpers_appeles(ctrl) & ancres, (
        "une ancre LIÉE par le même helper doit compter")


def test_le_controle_REEL_refuse_une_ancre_sans_rapport(tmp_path, monkeypatch) -> None:
    """Le test voisin exerce la LOGIQUE ; celui-ci exerce `_fonctions_sans_ancre`.

    La distinction n'est pas cosmétique : rétablir l'ancre au niveau du module
    dans `_fonctions_sans_ancre` ne faisait tomber AUCUN test — la mutation
    survivait, parce que seuls les helpers étaient testés.
    """
    faux = tmp_path / "test_faux.py"
    faux.write_text(
        "from pathlib import Path\n"
        "RACINE = Path('/')\n"
        "def test_balayage_sans_ancre():\n"
        "    fautes = [p for p in RACINE.glob('**/*.py') if bad(p)]\n"
        "    assert fautes == []\n"
        "def test_sans_rapport():\n"
        "    assert len([1]) == 1\n",
        encoding="utf-8")

    monkeypatch.setitem(globals(), "_fichiers_de_test", lambda: [faux])
    monkeypatch.setitem(globals(), "RACINE", tmp_path)

    fautes = _fonctions_sans_ancre()
    assert any("test_balayage_sans_ancre" in f for f in fautes), (
        f"une ancre SANS RAPPORT blanchit encore le balayage : {fautes}")


def test_le_controle_REEL_accepte_une_ancre_LIEE(tmp_path, monkeypatch) -> None:
    """Garde sur l'instrument : le resserrage ne doit pas interdire le bon
    découpage en deux tests voisins reliés par le même helper."""
    faux = tmp_path / "test_lie.py"
    faux.write_text(
        "from pathlib import Path\n"
        "RACINE = Path('/')\n"
        "def _balaye():\n"
        "    return list(RACINE.glob('**/*.py'))\n"
        "def test_controle():\n"
        "    fautes = [p for p in _balaye() if bad(p)]\n"
        "    assert fautes == []\n"
        "def test_ancre():\n"
        "    assert len(_balaye()) > 10\n",
        encoding="utf-8")

    monkeypatch.setitem(globals(), "_fichiers_de_test", lambda: [faux])
    monkeypatch.setitem(globals(), "RACINE", tmp_path)

    assert _fonctions_sans_ancre() == []


def test_le_detecteur_de_balayage_reconnait_un_appel_DIRECT() -> None:
    """Garde sur l'instrument, ajoutée après une mutation survivante.

    Retirer la reconnaissance de `_BALAYAGES` laissait les quatre tests verts :
    l'heuristique de NOM de helper suffisait à satisfaire l'ancre. Le chemin
    principal n'était donc gardé par rien, et un test appelant `glob()`
    directement serait sorti de la surveillance sans un mot.
    """
    direct = ast.parse(
        "def test_x():\n"
        "    fautes = [p for p in RACINE.glob('**/*.py') if bad(p)]\n"
        "    assert fautes == []\n"
    ).body[0]
    assert _balaye_le_monde(direct), (
        "un appel DIRECT à `glob` n'est plus reconnu comme un balayage")

    for methode in ("rglob", "listdir", "getsource", "check_output"):
        noeud = ast.parse(
            f"def test_y():\n    x = mod.{methode}('a')\n    assert x == []\n"
        ).body[0]
        assert _balaye_le_monde(noeud), f"`{methode}` n'est plus reconnu"

    # …et une fonction qui ne balaye RIEN ne doit pas être surveillée.
    inerte = ast.parse(
        "def test_z():\n    x = [1, 2]\n    assert not x\n").body[0]
    assert not _balaye_le_monde(inerte), "faux positif sur une liste littérale"
