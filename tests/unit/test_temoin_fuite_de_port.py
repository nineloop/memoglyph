"""Le témoin anti-fuite ne doit pas accuser un innocent.

Le témoin de `conftest.py` échoue quand un listener
occupe le port de PRODUCTION après un test. Dans sa première forme il ne
mesurait qu'APRÈS : il prouvait donc « un listener existe maintenant », jamais
« ce test l'a démarré ». Un résidu antérieur — run manuel, application ouverte,
fuite d'une exécution précédente — faisait accuser le premier test venu.

C'est la classe de contrôle qui mesure une grandeur VOISINE de celle qu'il
annonce. Ce dépôt en a catalogué une quinzaine ; celui-ci en était un.

On teste ici la LOGIQUE des deux verdicts, sur des ports jetables : ouvrir un
vrai listener sur le port de production pour éprouver le témoin reviendrait à
provoquer la panne qu'on cherche à interdire.
"""

from __future__ import annotations

import socket
from contextlib import closing

import pytest

from tests.conftest import _listener_sur

pytestmark = pytest.mark.unit


pytest_plugins = ["pytester"]


def _verdict(occupe_avant: bool, occupe_apres: bool) -> str:
    """La décision du témoin, telle qu'elle DOIT être.

    Recopiée à dessein : ces quatre premiers tests décrivent la propriété. Ils
    ne suffisent PAS — une mutation qui retire la mesure initiale de la vraie
    fixture les laissait tous verts. C'est cette mutation qu'il faut tuer, et
    `test_la_fixture_REELLE_...` plus bas s'en charge en exerçant le témoin de
    `conftest.py` dans une vraie session pytest.
    """
    if occupe_avant:
        return "precondition"
    if occupe_apres:
        return "fuite"
    return "ok"


def test_un_port_deja_pris_AVANT_n_accuse_pas_le_test():
    """Le cas que la première version confondait avec une fuite."""
    assert _verdict(occupe_avant=True, occupe_apres=True) == "precondition"


def test_un_port_libre_avant_et_pris_apres_est_bien_une_FUITE():
    assert _verdict(occupe_avant=False, occupe_apres=True) == "fuite"


def test_un_port_libre_des_deux_cotes_ne_dit_rien():
    assert _verdict(occupe_avant=False, occupe_apres=False) == "ok"


def test_les_deux_verdicts_sont_DISTINCTS():
    """Sans la mesure initiale, les deux cas rendraient le même verdict.

    C'est exactement ce que faisait la version d'origine : « occupé après »
    suffisait à accuser, que le port ait été libre ou non avant.
    """
    assert _verdict(True, True) != _verdict(False, True)


def test_la_sonde_voit_un_listener_reel_et_son_absence():
    """Le témoin du témoin : la sonde doit répondre dans les DEUX sens.

    Une sonde qui rendrait toujours `False` laisserait passer toutes les fuites
    en restant verte — le motif du contrôle décoratif. On l'éprouve sur un port
    éphémère, jamais sur celui de production.
    """
    with closing(socket.socket()) as serveur:
        serveur.bind(("127.0.0.1", 0))
        serveur.listen(1)
        port = serveur.getsockname()[1]
        assert _listener_sur(port) is True, "un listener réel n'est pas vu"

    # Fermé : la sonde doit le dire aussi, sinon elle accuserait sans fin.
    assert _listener_sur(port) is False


def test_la_sonde_ne_se_detecte_PAS_elle_meme():
    """Un piège qui fait conclure de travers.

    `ps -eo command | grep "[m]lx_lm"` se détecte lui-même : la chaîne cherchée
    figure dans la ligne de commande du shell qui exécute le grep. Un tel
    contrôle annonçait « encore présent » après un arrêt réussi.

    Une sonde TCP ne peut pas avoir ce défaut — elle observe un port, pas une
    ligne de commande. Ce test fige la propriété : sur un port certainement
    libre, la réponse est `False`, quoi que fasse le processus qui interroge.
    """
    with closing(socket.socket()) as sonde:
        sonde.bind(("127.0.0.1", 0))
        port_libre = sonde.getsockname()[1]
    # Le socket est fermé : plus personne n'écoute sur ce port.
    assert _listener_sur(port_libre) is False


def test_la_fixture_REELLE_distingue_precondition_et_fuite(pytester, monkeypatch):
    """Le contrôle qui TUE la mutation — les quatre premiers ne le faisaient pas.

    Retirer la mesure initiale de `conftest.py` laissait les tests de logique
    verts : ils recopient la décision au lieu de l'exercer. Ici on lance une
    VRAIE session pytest, avec un listener ouvert avant le test, et l'on exige
    le verdict « PRÉCONDITION » — celui que seule la mesure initiale peut
    produire.

    Le port est ÉPHÉMÈRE : éprouver le témoin sur le port de production
    reviendrait à provoquer la panne qu'il interdit.
    """
    with closing(socket.socket()) as serveur:
        serveur.bind(("127.0.0.1", 0))
        serveur.listen(1)
        port = serveur.getsockname()[1]

        pytester.makeconftest(f"""
import pytest
from tests.conftest import _listener_sur


class _Config:
    MLX_PORT = {port}


@pytest.fixture(autouse=True)
def temoin(request, monkeypatch):
    import core.config
    monkeypatch.setattr(core.config, "MLX_PORT", {port})
    from tests.conftest import aucun_listener_sur_le_port_de_production as f
    gen = f.__wrapped__(request)
    next(gen)
    yield
    try:
        next(gen)
    except StopIteration:
        pass
""")
        pytester.makepyfile("def test_innocent(): pass")
        resultat = pytester.runpytest("-p", "no:cacheprovider")

    sortie = resultat.stdout.str()
    assert "PRÉCONDITION" in sortie, sortie[-2000:]
    assert "a laissé un listener" not in sortie, (
        "le témoin accuse un test innocent d'une fuite antérieure"
    )
