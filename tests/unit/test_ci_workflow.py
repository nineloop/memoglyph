"""Le workflow de CI ne passe qu'une fois par événement utile, sans perdre de test.

Trois règles, lues sur `.github/workflows/tests.yml` par le parseur YAML :
- `push` ne vise que `main` ; une branche n'est testée que par sa PR ;
- un nouveau push annule le passage en cours de sa PR, jamais celui d'une fusion ;
- une modification de la seule documentation ne lance rien. Les documents que
  des tests lisent, et tout ce qui entre dans un dossier gardé contre les
  fuites, relancent la CI : sinon une PR qui les casse, ou qui y glisse un
  `.md`, passerait sans elle.

La troisième se vérifie par le filtre rejoué comme GitHub l'évalue, sur les
fichiers que git suit : il n'écarte que de la documentation. Les documents lus
et les dossiers gardés sont tirés du source des tests, pas recopiés à la main.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

RACINE = Path(__file__).resolve().parents[2]
WORKFLOW = RACINE / ".github" / "workflows" / "tests.yml"
GARDE_ARTEFACTS = RACINE / "tests" / "test_artefacts_hors_depot.py"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _declencheurs() -> dict:
    # YAML 1.1 lit la clé `on` comme le booléen True.
    donnees = _workflow()
    return donnees.get("on", donnees.get(True))


def _motif(glob: str) -> re.Pattern:
    """Un motif de filtre GitHub : `**` traverse les dossiers, `*` non."""
    regex = ""
    i = 0
    while i < len(glob):
        if glob.startswith("**/", i):
            regex += "(?:.*/)?"
            i += 3
        elif glob.startswith("**", i):
            regex += ".*"
            i += 2
        elif glob[i] == "*":
            regex += "[^/]*"
            i += 1
        else:
            regex += re.escape(glob[i])
            i += 1
    return re.compile(regex + r"\Z")


def _lance_la_ci(chemin: str, motifs: list[str]) -> bool:
    """Évaluation de GitHub : le dernier motif qui correspond l'emporte."""
    retenu = False
    for motif in motifs:
        negatif = motif.startswith("!")
        if _motif(motif.lstrip("!")).match(chemin):
            retenu = not negatif
    return retenu


def _documents_lus_par_les_tests() -> set[str]:
    """Les `.md` du dépôt que le source des tests nomme : chaîne littérale qui
    désigne un fichier suivi par git, à la racine ou sous un dossier."""
    import subprocess

    suivis = set(subprocess.run(["git", "ls-files", "*.md", "**/*.md"], cwd=RACINE,
                                capture_output=True, text=True, check=True).stdout.split())
    # Un test exclu de l'export public (`export-ignore`) lit des documents exclus avec lui :
    # les ré-inclure ferait nommer ces fichiers par le workflow, qui est exporté. Ses
    # documents ne relancent donc pas la CI à eux seuls. Le relevé porte sur les autres.
    tests_py = [str(f.relative_to(RACINE)) for f in (RACINE / "tests").rglob("*.py")]
    attributs = subprocess.run(["git", "check-attr", "-z", "--stdin", "export-ignore"], cwd=RACINE,
                               input="\0".join(tests_py) + "\0", capture_output=True, text=True,
                               check=True).stdout.split("\0")
    exclus = {c for c, _a, v in zip(attributs[0::3], attributs[1::3], attributs[2::3]) if v == "set"}
    lus: set[str] = set()
    for fichier in (RACINE / "tests").rglob("*.py"):
        if fichier.resolve() == Path(__file__).resolve():
            continue  # ses propres cas de test nomment des documents exclus
        if str(fichier.relative_to(RACINE)) in exclus:
            continue
        for noeud in ast.walk(ast.parse(fichier.read_text(encoding="utf-8"))):
            if isinstance(noeud, ast.Constant) and isinstance(noeud.value, str) \
                    and noeud.value.endswith(".md") and noeud.value in suivis:
                lus.add(noeud.value)
    return lus


def _dossiers_gardes() -> set[str]:
    """Les dossiers dont le garde d'artefacts relève les fichiers suivis : les
    arguments de `_fichiers_suivis(...)` et le tuple `DOSSIERS_INTERDITS`."""
    dossiers: set[str] = set()
    for noeud in ast.walk(ast.parse(GARDE_ARTEFACTS.read_text(encoding="utf-8"))):
        if isinstance(noeud, ast.Call) and getattr(noeud.func, "id", "") == "_fichiers_suivis" \
                and noeud.args and isinstance(noeud.args[0], ast.Constant):
            dossiers.add(noeud.args[0].value)
        if isinstance(noeud, ast.Assign) and any(getattr(c, "id", "") == "DOSSIERS_INTERDITS"
                                                 for c in noeud.targets):
            dossiers |= {e.value for e in noeud.value.elts}
    return dossiers


def test_push_ne_vise_que_main_et_les_branches_passent_par_leur_pr() -> None:
    on = _declencheurs()
    assert on["push"]["branches"] == ["main"]
    assert "pull_request" in on
    assert "branches" not in (on["pull_request"] or {}), "toute PR doit être testée"
    assert set(on) == {"push", "pull_request"}


def test_un_push_annule_le_passage_de_sa_pr_jamais_une_fusion() -> None:
    concurrence = _workflow()["concurrency"]
    groupe = concurrence["group"]
    assert "github.event.pull_request.number" in groupe
    # Sur `main`, un groupe par commit : deux fusions ne s'annulent jamais.
    assert "github.ref == 'refs/heads/main' && github.sha" in groupe
    assert concurrence["cancel-in-progress"] == "${{ github.ref != 'refs/heads/main' }}"


def test_le_filtre_est_le_meme_sur_les_deux_evenements() -> None:
    on = _declencheurs()
    assert on["push"]["paths"] == on["pull_request"]["paths"]
    assert "paths-ignore" not in on["push"] and "paths-ignore" not in on["pull_request"]


@pytest.mark.parametrize("chemin, attendu", [
    ("core/report.py", True),
    ("ui/src/App.tsx", True),
    (".github/workflows/tests.yml", True),
    ("docs/ui/screenshots/reference/capture.mjs", False),
    ("docs/sauvegarde.md", False),
    ("docs/ecl.md", True),
    ("docs/REPORT_TEMPLATES.md", True),
    ("CONTRIBUTING.md", False),
    ("compagnon/README.md", False),
    ("ui/README.md", False),
    ("tests/fixtures/nouveau/transcript.md", True),
    ("core/consigne.md", True),
    ("README.md", True),
    ("README.fr.md", True),
    ("SECURITY.md", True),
    ("output/Client/reunion.md", True),
])
def test_le_filtre_ne_laisse_passer_sans_ci_que_des_documents(chemin, attendu) -> None:
    assert _lance_la_ci(chemin, _declencheurs()["pull_request"]["paths"]) is attendu


def test_le_filtre_n_ecarte_que_de_la_documentation() -> None:
    """Parmi les fichiers que git suit, ceux qu'une PR pourrait changer sans CI."""
    import subprocess

    suivis = subprocess.run(["git", "ls-files"], cwd=RACINE, capture_output=True,
                            text=True, check=True).stdout.splitlines()
    motifs = _declencheurs()["pull_request"]["paths"]
    ecartes = [f for f in suivis if not _lance_la_ci(f, motifs)]
    # Ancre : le filtre écarte bien quelque chose, et voit le code.
    assert len(ecartes) >= 5 and len(suivis) - len(ecartes) >= 200, (len(suivis), len(ecartes))
    hors_doc = sorted(f for f in ecartes if not (f.endswith(".md") or f.startswith("docs/")))
    assert hors_doc == [], f"fichiers hors documentation écartés de la CI : {hors_doc}"


def test_chaque_document_lu_par_un_test_relance_la_ci() -> None:
    """Un test qui se met à lire un nouveau document sans que le filtre le
    ré-inclue échoue ici : sinon une PR qui casse ce document passerait sans CI."""
    lus = _documents_lus_par_les_tests()
    # Ancre : le relevé voit les documents lus aujourd'hui.
    assert len(lus) >= 4, lus
    assert {"SECURITY.md", "README.md", "README.fr.md"} <= lus
    motifs = _declencheurs()["pull_request"]["paths"]
    oublies = sorted(d for d in lus if not _lance_la_ci(d, motifs))
    assert oublies == [], f"documents lus par un test mais exclus de la CI : {oublies}"


def test_tout_md_ajoute_dans_un_dossier_garde_relance_la_ci() -> None:
    """Un `.md` peut porter du verbatim. Une PR qui n'ajoute qu'un `.md` dans un
    dossier gardé doit lancer le garde, donc la CI."""
    gardes = _dossiers_gardes()
    # Ancre : le relevé lit bien les dossiers du garde.
    assert {"inputs", "output"} <= gardes, gardes
    motifs = _declencheurs()["pull_request"]["paths"]
    oublies = sorted(d for d in gardes if not _lance_la_ci(f"{d}/nouveau/fichier.md", motifs))
    assert oublies == [], f"dossiers gardés dont un .md passerait sans CI : {oublies}"
