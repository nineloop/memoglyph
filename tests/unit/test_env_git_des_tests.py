"""Les tests ne désignent jamais un dépôt git par l'environnement hérité.

Le crochet pré-push lancé depuis un arbre lié (`git worktree`) pose un `GIT_DIR`
absolu. Hérité, il fait agir le `git init` d'un test sur ce dépôt-là, qui reçoit
`core.bare = true`. Chaque cas rejoue ce crochet dans un pytest en sous-processus,
avec les variables tournées vers un FAUX dépôt de `tmp_path` : jamais le vrai.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[2]

# Écrit ici, pas importé de `tests/conftest.py` : retirer une variable de la liste
# du conftest doit faire échouer ce test.
VARIABLES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY")


def _git(*args: str, cwd: Path) -> str:
    env = {k: v for k, v in os.environ.items() if k not in VARIABLES}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                          capture_output=True, text=True).stdout


@pytest.fixture
def faux_depot(tmp_path: Path) -> Path:
    """Un dépôt normal, que le crochet simulé désigne par `GIT_DIR`."""
    d = tmp_path / "faux"
    d.mkdir()
    _git("init", "-q", "-b", "main", cwd=d)
    assert _git("config", "--get", "core.bare", cwd=d).strip() == "false"
    return d


def _pytest(*args: str, env_git: dict[str, str], cwd: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in VARIABLES}
    env.update(env_git)
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
                          cwd=cwd, env=env, capture_output=True, text=True, timeout=300)


def _bare(depot: Path) -> str:
    return _git("config", "--get", "core.bare", cwd=depot).strip()


def test_le_test_d_export_sous_un_git_dir_herite_laisse_le_depot_intact(tmp_path: Path, faux_depot: Path) -> None:
    """Le cas de l'incident : un test d'export, lancé comme par le crochet, dont la
    fixture fait un `git init` nu dans `tmp_path`, puis `git add` et `git commit`.
    Le module est écrit ici, à la forme de celui de l'incident, pour que ce cas ne
    dépende d'aucun fichier exclu de l'export public."""
    module = _faux_arbre(tmp_path, (
        "import subprocess\nimport pytest\n"
        "@pytest.fixture\n"
        "def depot(tmp_path):\n"
        "    d = tmp_path / 'depot'\n    d.mkdir()\n"
        "    subprocess.run(['git', 'init', '-q', '-b', 'main'], cwd=d, check=True)\n"
        "    (d / 'README.md').write_text('# Produit\\n', encoding='utf-8')\n"
        "    subprocess.run(['git', 'add', '-A'], cwd=d, check=True)\n"
        "    subprocess.run(['git', '-c', 'user.name=Dev', '-c', 'user.email=dev@example.com',\n"
        "                    'commit', '-q', '-m', 'init'], cwd=d, check=True)\n"
        "    return d\n"
        "def test_export(depot):\n"
        "    assert (depot / '.git').is_dir()\n"
    ))
    fini = _pytest("-p", "tests.conftest", str(module),
                   env_git={"GIT_DIR": str(faux_depot / ".git")}, cwd=RACINE)
    assert fini.returncode == 0, fini.stdout[-2000:]
    assert _bare(faux_depot) == "false"


def _faux_arbre(tmp_path: Path, corps: str) -> Path:
    """Un module de test qui agit À LA COLLECTE, chargé avec `tests/conftest.py`."""
    arbre = tmp_path / "arbre"
    arbre.mkdir()
    (arbre / "test_a_la_collecte.py").write_text(corps, encoding="utf-8")
    return arbre / "test_a_la_collecte.py"


def test_un_git_lance_a_la_collecte_n_atteint_pas_le_depot_herite(tmp_path: Path, faux_depot: Path) -> None:
    module = _faux_arbre(tmp_path, (
        "import subprocess\nfrom pathlib import Path\n"
        "subprocess.run(['git', 'init', '-q', str(Path(__file__).parent / 'neuf')], check=True)\n"
        "def test_rien():\n    pass\n"
    ))
    fini = _pytest("-p", "tests.conftest", str(module),
                   env_git={"GIT_DIR": str(faux_depot / ".git")}, cwd=RACINE)
    assert fini.returncode == 0, fini.stdout[-2000:]
    assert _bare(faux_depot) == "false"
    assert (module.parent / "neuf" / ".git").is_dir()


@pytest.mark.parametrize("variable", VARIABLES)
def test_chaque_variable_de_depot_est_retiree_avant_la_collecte(tmp_path: Path, faux_depot: Path,
                                                               variable: str) -> None:
    vues = tmp_path / "vues.txt"
    module = _faux_arbre(tmp_path, (
        "import os\nfrom pathlib import Path\n"
        f"Path({str(vues)!r}).write_text(' '.join(sorted(k for k in os.environ if k.startswith('GIT_'))))\n"
        "def test_rien():\n    pass\n"
    ))
    fini = _pytest("-p", "tests.conftest", str(module),
                   env_git={variable: str(faux_depot / ".git")}, cwd=RACINE)
    assert fini.returncode == 0, fini.stdout[-2000:]
    assert variable not in vues.read_text(encoding="utf-8").split()
