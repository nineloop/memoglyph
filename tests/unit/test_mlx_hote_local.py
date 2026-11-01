"""« Local » veut dire « sur cette machine » : un `MLX_HOST` distant est un fournisseur cloud.

`llm.est_local` décidait par le NOM du moteur : `mlx` était local quel que soit l'hôte
que le client joint (`MLX_HOST`). Un serveur MLX sur une autre machine recevait donc un
document ultra-confidentiel sans contrôle (T4). L'hôte distant n'est pas interdit : il est compté comme cloud.
"""
from __future__ import annotations

import pytest

from core import config, llm, sensitivity, stt

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("hote", ["127.0.0.1", "127.8.9.10", "localhost", "LocalHost", "::1", "[::1]",
                                  " 127.0.0.1 "])
def test_un_hote_de_la_boucle_locale_reste_local(monkeypatch, hote) -> None:
    monkeypatch.setattr(config, "MLX_HOST", hote)
    assert llm.est_local("mlx")


@pytest.mark.parametrize("hote", ["10.20.30.40", "192.168.1.5", "mlx.example.org", "0.0.0.0", "::",
                                  "localhost.example.org", "128.0.0.1", ""])
def test_un_hote_hors_de_la_boucle_locale_compte_comme_cloud(monkeypatch, hote) -> None:
    appels: list = []

    class FauxMlx:
        """Sans `host` : l'hôte est celui de la configuration. Ne joint rien."""
        name = "mlx"

        def chat(self, system, user, **_k):
            appels.append(user)
            return "réponse"

    monkeypatch.setitem(llm.MOTEURS, "mlx", FauxMlx())
    monkeypatch.setattr(config, "MLX_HOST", hote)
    assert not llm.est_local("mlx")
    with pytest.raises(sensitivity.EnvoiRefuse):
        llm.chat("s", "TEXTE", provider="mlx",
                 envoi=sensitivity.Envoi(sensibilite="ultra_confidential", proprietaire=None))
    assert appels == [], "le moteur n'a pas été appelé"


def test_l_hote_du_moteur_l_emporte_sur_la_configuration(monkeypatch) -> None:
    """L'hôte que le moteur joint réellement (`MlxEngine.host`), pas seulement la variable."""
    monkeypatch.setattr(config, "MLX_HOST", "127.0.0.1")
    monkeypatch.setattr(llm.MOTEURS["mlx"], "_host_configure", "10.0.0.7")
    assert not llm.est_local("mlx")


def test_les_fournisseurs_cloud_restent_cloud_et_whisper_reste_local(monkeypatch) -> None:
    monkeypatch.setattr(config, "MLX_HOST", "127.0.0.1")
    assert [p for p in llm.FOURNISSEURS if llm.est_local(p)] == ["mlx"]
    # La transcription locale tourne dans le processus : aucun hôte n'entre en jeu.
    monkeypatch.setattr(config, "MLX_HOST", "10.20.30.40")
    assert stt.est_local("whisper") and not stt.est_local("mistral")
