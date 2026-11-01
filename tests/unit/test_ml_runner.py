from __future__ import annotations

import builtins
import signal
import subprocess
import sys
from pathlib import Path

import pytest

import run_ml_tests


pytestmark = pytest.mark.unit


def test_preflight_verifies_everything_without_importing_mlx(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import config

    audio = tmp_path / "tests" / "fixtures" / "audio" / "reference.wav"
    audio.parent.mkdir(parents=True)
    audio.touch()
    for nom in run_ml_tests.AMI_FICHIERS:
        corpus = tmp_path / "demo" / "ami" / nom
        corpus.parent.mkdir(parents=True, exist_ok=True)
        corpus.touch()

    real_import = builtins.__import__
    events: list[str] = []

    def guarded_import(name, *args, **kwargs):
        if name == "mlx_whisper" or name.startswith("mlx_whisper."):
            raise AssertionError("le préflight ne doit jamais importer MLX")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(config, "load_env", lambda: None)
    monkeypatch.setattr(config, "HF_TOKEN", "token-present")
    monkeypatch.setattr(
        run_ml_tests.importlib.util,
        "find_spec",
        lambda name: events.append(name) or object(),
    )
    # Le cache Hugging Face de la MACHINE ne doit pas décider du résultat : sans
    # ce figeage, ce test passerait chez qui a téléchargé le modèle et
    # rougirait ailleurs — le défaut qui a déjà fait rougir la CI une fois.
    monkeypatch.setattr(
        run_ml_tests, "modele_mlx_dans_le_cache", lambda modele, env: True)

    plans = run_ml_tests.runtime_preflight(strict=True, root=tmp_path)

    assert [plan.path for plan in plans] == list(run_ml_tests.ml_modules())
    assert all(plan.runnable for plan in plans)
    # `transformers` s'ajoute à la liste : c'est le prérequis DÉCLARÉ de
    # `tests/unit/test_embed.py`, localisé lui aussi sans être importé.
    # `mlx_lm` de même, pour `tests/ml/test_llm_mlx_reel.py`.
    # La sonde `ollama` a disparu de la liste avec le retrait d'Ollama : plus
    # aucun module ML n'en dépend, ni pour le profilage ni pour les embeddings.
    assert events == ["mlx_whisper", "transformers", "mlx_lm"]


def test_optional_mode_runs_eligible_modules_and_skips_the_rest(capsys) -> None:
    plans = [
        run_ml_tests.ModulePlan("tests/ml/ready.py"),
        run_ml_tests.ModulePlan("tests/ml/missing.py", ("média absent",)),
    ]
    calls: list[str] = []

    def fake_run(path: str) -> run_ml_tests.ResultatModule:
        calls.append(path)
        # `executes=1` : un module qui a RÉELLEMENT exécuté un test. Rendre 0
        # ici ferait de ce test un succès pour la mauvaise raison.
        return run_ml_tests.ResultatModule(returncode=0, executes=1)

    assert run_ml_tests.execute(plans, strict=False, run_module=fake_run) == 0
    assert calls == ["tests/ml/ready.py"]
    assert "IGNORÉ" in capsys.readouterr().out


def test_strict_mode_fails_preflight_without_starting_mlx(capsys) -> None:
    plans = [
        run_ml_tests.ModulePlan("tests/ml/ready.py"),
        run_ml_tests.ModulePlan("tests/ml/missing.py", ("HF_TOKEN absent",)),
    ]
    calls: list[str] = []

    assert run_ml_tests.execute(
        plans,
        strict=True,
        run_module=lambda path: calls.append(path) or run_ml_tests.ResultatModule(0, 1),
    ) == 2
    assert calls == []
    output = capsys.readouterr().out
    assert "PRÉFLIGHT STRICT EN ÉCHEC" in output
    assert "HF_TOKEN absent" in output


def test_each_ml_module_runs_in_its_own_python_process() -> None:
    calls: list[list[str]] = []

    def fake_subprocess(command, **kwargs):
        calls.append(command)
        assert kwargs["cwd"] == run_ml_tests.ROOT
        return subprocess.CompletedProcess(command, 0)

    assert run_ml_tests.run_pytest_module(
        "tests/ml/test_stt_real.py",
        subprocess_run=fake_subprocess,
    ).returncode == 0
    assert calls[0][:7] == [
        sys.executable,
        "-m",
        "pytest",
        "tests/ml/test_stt_real.py",
        "-m",
        "ml",
        "-v",
    ]
    assert len(calls) == 1
    # Le rapport machine, en plus de `-v` que lit l'humain — voir `_compte_junit`.
    rapports = [a for a in calls[0] if a.startswith("--junit-xml=")]
    assert len(rapports) == 1, calls[0]
    assert str(run_ml_tests.ROOT) not in rapports[0], (
        f"le rapport JUnit atterrit dans le dépôt : {rapports[0]}")


def test_native_abort_becomes_an_actionable_diagnostic() -> None:
    diagnostic = run_ml_tests.failure_diagnostic(
        "tests/ml/test_stt_real.py",
        -signal.SIGABRT,
    )

    assert "SIGABRT" in diagnostic
    assert "tests/ml/test_stt_real.py" in diagnostic
    assert "processus parent est resté actif" in diagnostic
    assert "Metal/MLX" in diagnostic


# ---------- Motif nº1 : dériver de la COLLECTE, pas d'une liste ----------

def test_tout_module_marque_ml_est_pris_en_charge_par_le_lanceur() -> None:
    """`ML_MODULES` était écrit à la main — et avait DÉJÀ divergé.

    `tests/unit/test_embed.py` porte le marqueur `ml` et n'y
    figurait pas. La suite rapide le désélectionne (« 14 deselected »), ce
    lanceur l'ignorait : seul contrôle que les vecteurs d'embedding sont
    unitaires — prémisse du bras dense de la fusion RRF — il n'a JAMAIS tourné
    une seule fois. Retirer `normalize()` de `core/embed.py` ne changeait rien.
    """
    reels = set(run_ml_tests.ml_modules())
    assert reels, "la collecte ne rend aucun module `ml`"
    oublies = reels - {plan.path for plan in run_ml_tests.runtime_preflight(
        strict=False)}
    assert oublies == set(), (
        f"module(s) marqué(s) `ml` que le lanceur ignore : {sorted(oublies)}")


def test_la_collecte_voit_le_module_orphelin_retrouve() -> None:
    """Ancre de non-vacuité, et régression nommée.

    Si la dérivation cessait de fonctionner, elle retomberait sur
    `ML_MODULES_DECLARES` et ce test dirait pourquoi.
    """
    reels = run_ml_tests.ml_modules()
    assert len(reels) >= 3, f"seulement {len(reels)} module(s) : {reels}"
    assert "tests/unit/test_embed.py" in reels, (
        "l'orphelin retrouvé a disparu de la collecte — "
        f"modules vus : {reels}")


def test_les_prerequis_sont_associes_par_CHEMIN_et_non_par_position(tmp_path) -> None:
    """`ML_MODULES[0]`/`[1]` liait le préflight à une liste de longueur deux.

    Contre `preflight`, la fonction PURE, et non `runtime_preflight`.

    Premier jet : ce test appelait `runtime_preflight` et affirmait que
    `test_embed.py` était exécutable. Il passait chez moi — `transformers` y est
    installé — et a fait ROUGIR LA CI, qui n'installe que le groupe `dev`. Le
    correctif contre les contrôles dépendants de l'environnement avait donc
    introduit un contrôle dépendant de l'environnement.

    L'association par chemin est une propriété STRUCTURELLE : elle ne dépend
    d'aucun paquet installé, et se vérifie en faisant varier UN fait à la fois.
    """
    def plans(**faits):
        base = dict(
            root=tmp_path, env={}, hf_token=None, mlx_available=True,
            transformers_available=True,
            mlx_lm_available=True, mlx_llm_model="org/modele",
            mlx_llm_model_present=True,
        )
        return {p.path: p for p in run_ml_tests.preflight(**{**base, **faits})}

    tous = plans()
    assert len(tous) >= 3, f"seulement {len(tous)} plan(s) : {sorted(tous)}"
    assert "tests/unit/test_embed.py" in tous, sorted(tous)

    # Un fait qui ne concerne QUE `test_embed` ne doit bloquer que lui.
    sans_transformers = plans(transformers_available=False)
    assert sans_transformers["tests/unit/test_embed.py"].missing == (
        "package transformers absent",)
    assert "package transformers absent" not in (
        sans_transformers["tests/ml/test_stt_real.py"].missing)

    # Et réciproquement : un fait propre au STT ne doit pas atteindre `embed`.
    sans_mlx = plans(mlx_available=False)
    assert "package mlx_whisper absent" in sans_mlx["tests/ml/test_stt_real.py"].missing
    assert sans_mlx["tests/unit/test_embed.py"].missing == ()

    # Sans cette dernière assertion, tout ce qui précède passerait sur un
    # préflight qui ne rendrait JAMAIS de prérequis.
    assert sans_transformers["tests/unit/test_embed.py"].missing != ()


# ---------- Motif nº3 : aucune sortie de secours muette ----------

def test_zero_test_execute_n_est_PAS_un_succes(monkeypatch, capsys) -> None:
    """`return 0` après zéro test était indiscernable de « tout est passé ».

    Reproduit sur un clone frais avec les modules rendus inimportables :
    EXIT=0. Un portail qui rend « succès » sans avoir rien exécuté n'est pas
    un portail — et `make test-ml` est présenté comme LE point d'entrée des
    tests sur vrais modèles dans README.md et AGENTS.md.
    """
    monkeypatch.setattr(
        run_ml_tests, "runtime_preflight",
        lambda **k: [run_ml_tests.ModulePlan("tests/ml/x.py", ("média absent",))])

    code = run_ml_tests.main([])

    assert code != 0, "zéro test exécuté ne doit pas rendre 0"
    sortie = capsys.readouterr().out
    assert "AUCUN TEST" in sortie, sortie


def test_une_collecte_en_ERREUR_leve_au_lieu_de_deviner(monkeypatch) -> None:
    """Ce critère manquait — la mutation survivait.

    Le code retour était ignoré. Une collecte en erreur sans nœud faisait
    retomber `ml_modules()` sur `ML_MODULES_DECLARES` : précisément la liste
    écrite à la main dont le correctif de motif voulait sortir, et qui a laissé
    `tests/unit/test_embed.py` sans exécution.
    """
    import subprocess

    import pytest

    run_ml_tests.modules_marques_ml.cache_clear()
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a[0] if a else [], 2, stdout="", stderr="ERROR collecting tests/x.py"))

    with pytest.raises(run_ml_tests.CollecteMLImpossible, match="a échoué"):
        run_ml_tests.ml_modules()
    run_ml_tests.modules_marques_ml.cache_clear()


def test_une_collecte_PARTIELLE_leve_aussi(monkeypatch) -> None:
    """Second cas : erreur APRÈS quelques nœuds imprimés.

    Le set n'était pas vide, donc cette liste TRONQUÉE devenait la réalité et
    le module orphelin restait ignoré — sans un mot.
    """
    import subprocess

    import pytest

    run_ml_tests.modules_marques_ml.cache_clear()
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a[0] if a else [], 2,
            stdout="tests/ml/test_stt_real.py::test_a\n",
            stderr="ERROR collecting tests/unit/test_embed.py"))

    with pytest.raises(run_ml_tests.CollecteMLImpossible):
        run_ml_tests.ml_modules()
    run_ml_tests.modules_marques_ml.cache_clear()


def test_une_collecte_VIDE_leve_plutot_que_de_replier(monkeypatch) -> None:
    """Code retour 0 mais aucun test `ml` : le marqueur a disparu, ou la
    commande ne regarde plus le bon dossier. Les deux méritent un échec."""
    import subprocess

    import pytest

    run_ml_tests.modules_marques_ml.cache_clear()
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0] if a else [], 0,
                                                    stdout="", stderr=""))

    with pytest.raises(run_ml_tests.CollecteMLImpossible, match="AUCUN test"):
        run_ml_tests.ml_modules()
    run_ml_tests.modules_marques_ml.cache_clear()


# ---------- Trou nº2 : un module ENTIÈREMENT sauté n'est pas un succès ----------
#
# Mesuré sur un module jetable dont l'unique test fait
# `pytest.importorskip("paquet_absent")` : pytest rend « 1 skipped » et
# **EXIT=0**. `execute()` lit ce 0 comme un succès, `main()` appelle
# `ecrire_marqueur()`, et `ml-gate.json` consigne un passage de tests ML qui
# n'a jamais eu lieu — la CI lit ce marqueur pour décider si le pipeline réel a
# été vérifié.
#
# C'est le cas zéro-module du docstring de `run_ml_tests.py` (« AUCUN TEST N'A
# ÉTÉ EXÉCUTÉ — ce n'est pas un succès »), appliqué au grain du MODULE.


def _module_ml_jetable(dossier: Path, nom: str, corps: str) -> str:
    """Écrit un module `ml` minimal et rend son chemin relatif.

    Un vrai fichier lancé par un vrai pytest : le comportement mesuré ici est
    celui de pytest, pas celui d'un faux `run_module` qui rendrait ce que je
    lui demande de rendre.
    """
    (dossier / nom).write_text(
        f"import pytest\n\npytestmark = pytest.mark.ml\n\n\n{corps}",
        encoding="utf-8")
    return nom


def test_un_module_ENTIEREMENT_saute_ne_compte_PAS_comme_reussi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd
) -> None:
    """« 1 skipped » + EXIT=0 était indiscernable de « tout est passé ».

    `capfd` et non `capsys` : la sortie de pytest vient d'un SOUS-PROCESSUS,
    elle passe par le descripteur de fichier et non par `sys.stdout`.
    """
    chemin = _module_ml_jetable(
        tmp_path, "test_tout_saute.py",
        'def test_a():\n'
        '    pytest.importorskip("paquet_absent_de_cet_environnement")\n'
        '    assert False\n')
    monkeypatch.setattr(run_ml_tests, "ROOT", tmp_path)

    code = run_ml_tests.execute([run_ml_tests.ModulePlan(chemin)], strict=False)

    assert code != 0, (
        "un module dont AUCUN test n'a été exécuté est compté comme réussi")
    assert "AUCUN TEST EXÉCUTÉ" in capfd.readouterr().out


def test_un_module_qui_execute_REELLEMENT_reste_un_succes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ancre de non-vacuité : sans elle, un `return 1` inconditionnel rendrait
    le test précédent vert et la porte ML inutilisable."""
    chemin = _module_ml_jetable(
        tmp_path, "test_qui_tourne.py", "def test_a():\n    assert True\n")
    monkeypatch.setattr(run_ml_tests, "ROOT", tmp_path)

    assert run_ml_tests.execute(
        [run_ml_tests.ModulePlan(chemin)], strict=False) == 0


def test_un_module_PARTIELLEMENT_saute_reste_un_succes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La règle porte sur « zéro exécuté », pas sur « un sauté ».

    `tests/ml/test_stt_real.py` saute légitimement certains tests selon le
    média disponible : un contrôle qui rougirait au premier `skip` serait
    retiré dans la semaine.
    """
    chemin = _module_ml_jetable(
        tmp_path, "test_moitie.py",
        'def test_a():\n    pytest.importorskip("paquet_absent")\n\n\n'
        'def test_b():\n    assert True\n')
    monkeypatch.setattr(run_ml_tests, "ROOT", tmp_path)

    assert run_ml_tests.execute(
        [run_ml_tests.ModulePlan(chemin)], strict=False) == 0


def test_le_marqueur_n_est_PAS_ecrit_quand_un_module_n_a_RIEN_execute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La conséquence réelle du trou nº2, bout en bout.

    Le dépôt jetable est initialisé pour que `ecrire_marqueur` PUISSE écrire :
    sans lui, `git ls-files` échouerait et l'absence de marqueur ne prouverait
    rien — un test vert pour la mauvaise raison.
    """
    import subprocess as sp

    chemin = _module_ml_jetable(
        tmp_path, "test_tout_saute.py",
        'def test_a():\n    pytest.importorskip("paquet_absent")\n')
    # `env` sans GIT_* : sous un hook (pre-push) ou depuis un worktree lié, un
    # `GIT_DIR` ambiant écraserait `cwd` et viserait le mauvais dépôt.
    genv = run_ml_tests._git_env()
    sp.run(["git", "init", "-q"], cwd=tmp_path, check=True, env=genv)
    sp.run(["git", "add", "-A"], cwd=tmp_path, check=True, env=genv)
    sp.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit",
            "-qm", "init"], cwd=tmp_path, check=True, env=genv)
    monkeypatch.setattr(run_ml_tests, "ROOT", tmp_path)
    monkeypatch.setattr(run_ml_tests, "runtime_preflight",
                        lambda **k: [run_ml_tests.ModulePlan(chemin)])

    code = run_ml_tests.main([])

    # Le marqueur d'abord : c'est LUI le dégât. Le code de sortie n'est que le
    # chemin par lequel il s'écrit.
    assert not (tmp_path / run_ml_tests.MARQUEUR).exists(), (
        "`ml-gate.json` consigne un passage de tests ML qui n'a pas eu lieu")
    assert code != 0


def test_un_rapport_JUNIT_absent_compte_pour_ZERO_execute() -> None:
    """Le compteur penche du côté qui ALERTE.

    Si le rapport manque — crash natif avant écriture, option perdue — on ne
    sait pas ce qui a tourné. « On ne sait pas » ne doit jamais se lire
    « tout est passé » : c'est la forme même du défaut qu'on répare.
    """
    def faux_subprocess(command, **kwargs):
        return subprocess.CompletedProcess(command, 0)  # n'écrit aucun XML

    resultat = run_ml_tests.run_pytest_module(
        "tests/ml/test_stt_real.py", subprocess_run=faux_subprocess)

    assert resultat.returncode == 0
    assert resultat.executes == 0, (
        "sans rapport lisible, le lanceur suppose que des tests ont tourné")


# ---------- Trou nº3 : un module inconnu était « exécutable » par défaut ----------


def test_un_module_ML_NON_DECLARE_est_refuse_au_lieu_d_etre_devine(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """`prerequis.get(chemin, ())` réputait exécutable un module inconnu.

    Ajouter un test ML sans déclarer son prérequis produisait donc le trou nº2
    en silence : le module part, tous ses tests sautent faute du modèle, pytest
    rend 0, et la porte consigne un passage. La chaîne entière tenait sur un
    `.get()` avec valeur par défaut.
    """
    monkeypatch.setattr(run_ml_tests, "ml_modules", lambda: (
        "tests/ml/test_stt_real.py",
        "tests/ml/test_ami_reponse_connue.py",
        "tests/unit/test_embed.py",
        "tests/ml/test_tout_neuf.py",
    ))

    with pytest.raises(RuntimeError, match="sans prérequis déclarés") as capture:
        run_ml_tests.runtime_preflight(strict=False)

    assert "tests/ml/test_tout_neuf.py" in str(capture.value)
    assert isinstance(capture.value, run_ml_tests.PrerequisNonDeclares)


def test_les_modules_DECLARES_ne_sont_pas_refuses() -> None:
    """Ancre de non-vacuité : un refus systématique casserait `make test-ml`
    et les trois modules de `ml-gate.json` avec lui."""
    plans = run_ml_tests.runtime_preflight(strict=False)
    assert {p.path for p in plans} == set(run_ml_tests.ml_modules())


def test_le_prerequis_de_test_embed_est_DECLARE_et_non_supppose(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le troisième module de `ml-gate.json` était l'exemple vivant du trou.

    `tests/unit/test_embed.py::test_le_modele_local_produit_des_vecteurs_
    normalises` charge un modèle par `transformers` et ne saute PAS tout seul.
    Sans prérequis déclaré il partait « exécutable » quoi qu'il arrive.
    """
    from core import config

    monkeypatch.setattr(config, "EMBED_BACKEND", "local")
    monkeypatch.setattr(run_ml_tests.importlib.util, "find_spec", lambda nom: None)

    plans = {p.path: p for p in run_ml_tests.runtime_preflight(strict=False)}
    embed = plans["tests/unit/test_embed.py"]

    assert not embed.runnable, (
        "`transformers` absent et le module d'embeddings reste « exécutable »")
    assert any("transformers" in m for m in embed.missing), embed.missing



def test_execute_n_ecrit_RIEN_dans_le_depot(tmp_path, monkeypatch) -> None:
    """`execute()` doit rester PURE — trouvé par mutation.

    L'écriture du marqueur y avait été placée. Un test existant appelle
    `execute()` avec des modules fictifs : il a ÉCRASÉ le vrai `ml-gate.json`
    du dépôt avec `["tests/ml/ready.py"]`. Un effet de bord échappé dans le
    monde réel depuis une suite de tests.
    """
    monkeypatch.setattr(run_ml_tests, "ROOT", tmp_path)
    run_ml_tests.execute(
        [run_ml_tests.ModulePlan("tests/ml/x.py", ())],
        strict=False, run_module=lambda path: run_ml_tests.ResultatModule(0, 1))
    assert not (tmp_path / run_ml_tests.MARQUEUR).exists(), (
        "`execute()` écrit dans le dépôt — l'effet de bord est revenu")


def test_le_marqueur_n_est_PAS_ecrit_si_un_module_echoue(tmp_path, monkeypatch) -> None:
    """Sinon il certifierait un passage qui n'a pas eu lieu.

    C'est la propriété portante : la CI lit ce marqueur pour décider si le
    pipeline réel a été vérifié. Un marqueur écrit après un échec vaudrait
    moins que pas de marqueur du tout — il RASSURE à tort.
    """
    # `execute` accepte `run_module` en paramètre : le harnais est injectable,
    # inutile de monkeypatcher le module.
    monkeypatch.setattr(run_ml_tests, "ROOT", tmp_path)
    monkeypatch.setattr(run_ml_tests, "runtime_preflight",
                        lambda **k: [run_ml_tests.ModulePlan("tests/ml/x.py", ())])
    monkeypatch.setattr(run_ml_tests, "execute", lambda *a, **k: 1)

    code = run_ml_tests.main([])

    assert code == 1
    assert not (tmp_path / run_ml_tests.MARQUEUR).exists(), (
        "marqueur écrit alors qu'un module a ÉCHOUÉ")


def test_le_marqueur_EST_ecrit_apres_un_passage_complet(tmp_path, monkeypatch) -> None:
    """Garde sur l'instrument : un marqueur jamais écrit rendrait le test
    précédent vert sans rien prouver."""
    import json

    monkeypatch.setattr(run_ml_tests, "ROOT", tmp_path)
    # `ecrire_marqueur` empreinte les fichiers surveillés via `git ls-files`
    # dans `root` : on initie un dépôt jetable pour que l'écriture aboutisse.
    # (Ce dépôt ne porte pas de `core/`, donc `watched` y est légitimement vide —
    # ce qu'on prouve ici, c'est que le marqueur EST écrit, au bon format.)
    import subprocess
    # `env` sans GIT_* : hermétique sous un hook ou depuis un worktree lié.
    genv = run_ml_tests._git_env()
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, env=genv)
    (tmp_path / "x").write_text("x")
    subprocess.run(["git", "add", "x"], cwd=tmp_path, check=True, env=genv)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=tmp_path, check=True, env=genv)

    monkeypatch.setattr(run_ml_tests, "runtime_preflight",
                        lambda **k: [run_ml_tests.ModulePlan(
                            "tests/ml/test_stt_real.py", ())])
    monkeypatch.setattr(run_ml_tests, "execute", lambda *a, **k: 0)

    code = run_ml_tests.main([])

    assert code == 0
    marqueur = json.loads((tmp_path / run_ml_tests.MARQUEUR).read_text())
    assert marqueur["modules"] == ["tests/ml/test_stt_real.py"]
    # Nouveau format, insensible au squash : plus de SHA de commit, une empreinte
    # de contenu par fichier surveillé.
    assert "commit" not in marqueur
    assert isinstance(marqueur["watched"], dict)


def test_un_CHEMIN_LOCAL_de_modele_est_reconnu_meme_avec_des_barres(tmp_path) -> None:
    """Le critère excluait tout chemin réel.

    `modele_mlx_dans_le_cache` ne prenait sa branche « chemin local » que si le
    nom ne contenait AUCUNE barre. Or tout chemin en contient — `/opt/mlx/qwen`
    partait donc dans la branche Hugging Face et était cherché sous
    `models--opt--mlx--qwen`, absent par construction. Le module MLX était
    déclaré non exécutable alors que les poids étaient sur le disque.
    """
    dossier = tmp_path / "modeles" / "qwen-local"
    dossier.mkdir(parents=True)
    env = {"HOME": str(tmp_path / "vide")}

    assert run_ml_tests.modele_mlx_dans_le_cache(str(dossier), env) is True
    # Et un identifiant Hugging Face absent du cache reste absent : la nouvelle
    # branche ne doit pas rendre vrai pour tout le monde.
    assert run_ml_tests.modele_mlx_dans_le_cache("mlx-community/inexistant", env) is False
