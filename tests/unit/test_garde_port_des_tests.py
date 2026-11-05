"""Aucun test ne peut toucher aux ports de production.

Le serveur de modèle local de l'utilisateur écoute sur 8772 (et l'application sur 8770 et 8771). Un traitement de test
resté vivant après la fin de son test a déjà fini par lancer un vrai `mlx_lm.server` sur ce port : la garde ne doit
pas dépendre d'une habitude (`MEMOGLYPH_MLX_PORT=8773` à la main) mais être posée par la suite elle-même, avant tout
import de `core`, et par chaque lancement de `pytest` du `Makefile`.

Les essais de lancement n'utilisent JAMAIS le vrai serveur : une commande inoffensive (`echo`) qui NOMME `mlx_lm.server`
et un port, de quoi éprouver la garde sans jamais risquer de lancer le serveur qu'on veut interdire.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from core import config

pytestmark = pytest.mark.unit

PORTS_DE_PRODUCTION = range(8770, 8773)
RACINE = Path(__file__).resolve().parents[2]


def test_pendant_les_tests_le_port_mlx_est_hors_des_ports_de_production() -> None:
    assert config.MLX_PORT not in PORTS_DE_PRODUCTION, f"config.MLX_PORT = {config.MLX_PORT}"


def test_l_environnement_de_la_suite_force_un_port_d_essai() -> None:
    port = config.getenv_compat("MLX_PORT", "")
    assert port.isdigit() and int(port) not in PORTS_DE_PRODUCTION, f"MEMOGLYPH_MLX_PORT = {port!r}"


@pytest.mark.parametrize("port", [8770, 8771, 8772])
@pytest.mark.parametrize("forme", ["separe", "egal"])
def test_un_test_ne_peut_pas_lancer_un_serveur_sur_un_port_de_production(port: int, forme: str) -> None:
    option = ["--port", str(port)] if forme == "separe" else [f"--port={port}"]
    processus = None
    try:
        with pytest.raises(RuntimeError, match="production"):
            processus = subprocess.Popen(["echo", "mlx_lm.server", *option])   # noqa: S603, S607 — inoffensif
    finally:
        if processus is not None:           # la garde est absente : on n'a lancé qu'un `echo`, on l'attend
            processus.wait(10)


def test_un_serveur_sur_un_port_d_essai_reste_possible() -> None:
    """Ancre : la garde ne refuse pas tout lancement, seulement ceux qui visent la production."""
    processus = subprocess.Popen(["echo", "mlx_lm.server", "--port", "8773"], stdout=subprocess.DEVNULL)  # noqa: S603, S607
    assert processus.wait(10) == 0


def test_un_autre_programme_que_le_serveur_n_est_pas_concerne() -> None:
    processus = subprocess.Popen(["echo", "--port", "8772"], stdout=subprocess.DEVNULL)  # noqa: S603, S607
    assert processus.wait(10) == 0


def test_chaque_lancement_de_pytest_du_makefile_pose_le_port_d_essai() -> None:
    lignes = [ligne for ligne in (RACINE / "Makefile").read_text(encoding="utf-8").splitlines()
              if re.search(r"-m pytest", ligne) and not ligne.lstrip().startswith("#")]
    assert lignes, "ancre : le Makefile lance bien pytest"
    for ligne in lignes:
        assert "MEMOGLYPH_MLX_PORT=8773" in ligne, ligne


def test_la_garde_reconnait_la_vraie_ligne_de_commande_du_serveur() -> None:
    """Pas seulement l'`echo` de substitution : l'`argv` réel de `llm_mlx`, sur un port de production puis d'essai."""
    from core import llm_mlx
    from tests.conftest import _refuser_un_serveur_de_production

    argv = llm_mlx.MlxEngine(port=8772, model="org/m", host="127.0.0.1").commande_serveur()
    assert "mlx_lm.server" in argv and "8772" in argv, "ancre : c'est bien la ligne de lancement du serveur"
    with pytest.raises(RuntimeError, match="production"):
        _refuser_un_serveur_de_production(argv)
    _refuser_un_serveur_de_production(llm_mlx.MlxEngine(port=8773, model="org/m", host="127.0.0.1").commande_serveur())
