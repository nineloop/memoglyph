"""Les seuils de la fusion RRF, et le fait qu'ils PILOTENT.

Réglés sur la vraie `semantic.retrieve` (rappel@5 et MRR) ; la raison de chaque
borne est dans `core/semantic.py`, au-dessus des constantes.
"""

from __future__ import annotations

import pytest

from core import semantic

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _aucun_test_ne_laisse_les_seuils_modifies():
    """Détecte la POLLUTION, pas seulement celle qu'on a déjà commise.

    Un test de ce fichier modifie forcément les seuils — c'est ce qu'il mesure.
    Le danger n'est pas la modification, c'est l'oubli de la défaire : la suite
    reste verte tant qu'aucun test postérieur n'en dépend, donc le défaut ne se
    voit qu'au jour où l'ordre change.

    Cas réel : un test restaurait `RRF_K = 10` à la main alors que la valeur
    livrée était 30, la constante ayant changé APRÈS l'écriture du test. Une
    valeur recopiée est une valeur qui divergera.

    Cette garde ne dit pas QUELLE valeur est juste : elle exige seulement que
    l'état de sortie soit celui de l'entrée. C'est la seule formulation qui ne
    se périme pas au prochain réglage.
    """
    avant = (semantic.RRF_K, semantic.RRF_PROFONDEUR)
    yield
    apres = (semantic.RRF_K, semantic.RRF_PROFONDEUR)
    assert apres == avant, (
        f"ce test a laissé les seuils modifiés : {avant} → {apres}. "
        "Utiliser `monkeypatch.setattr`, qui restaure sans recopier de valeur.")


# ---------- Les seuils RRF, mesurés ----------

def test_la_constante_RRF_K_PILOTE_reellement_la_fusion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`k` était keyword-only avec `= RRF_K` : gelé dans `__kwdefaults__`.

    Changer `RRF_K` ne changeait donc RIEN, et aucun des deux appelants ne passe
    `k=`. Découvert en balayant les seuils : une mesure qui patchait la constante
    rendait des lignes toutes identiques, d'où « k n'a aucun effet » — vrai de
    la mesure, faux du code.

    Une constante qu'on ne peut pas changer n'est pas un réglage, c'est un
    littéral avec un nom.
    """
    # Le cas qui BASCULE, et il fallait le construire : un premier jeu était
    # symétrique — deux morceaux aux rangs inversés ont le même score à tout k,
    # donc l'ordre ne changeait jamais. Le test ne pouvait pas échouer.
    #
    # Ici, A paraît UNE fois très haut, B paraît DEUX fois plus bas. C'est
    # exactement l'arbitrage que k règle : petit k récompense un rang
    # excellent, grand k récompense la présence dans les deux listes.
    a_seul_mais_premier = [1, 7, 9]
    b_deux_fois_mais_bas = [2, 3, 4, 9]

    # `monkeypatch` et non une affectation : il restaure l'état SANS qu'aucune
    # valeur ait à être recopiée. C'est la duplication qui avait divergé.
    monkeypatch.setattr(semantic, "RRF_K", 1)
    petit = semantic._fusion_rrf(a_seul_mais_premier, b_deux_fois_mais_bas)
    monkeypatch.setattr(semantic, "RRF_K", 100_000)
    grand = semantic._fusion_rrf(a_seul_mais_premier, b_deux_fois_mais_bas)

    assert petit[0] == 1, f"k=1 devrait couronner le rang 1 : {petit}"
    assert grand[0] == 9, f"k énorme devrait couronner la double présence : {grand}"
    assert petit != grand, (
        "les deux k rendent le même ordre — la constante ne pilote rien")


def test_la_PROFONDEUR_ne_descend_pas_sous_le_plancher_mesure() -> None:
    """Le seul signal du balayage qui sorte du bruit.

    Une profondeur de 25 perd du rappel@5 à TOUS les k essayés ; 50 est le
    premier palier qui tienne.

    Ce test ne prétend pas que 50 est optimal ; il interdit de descendre sous
    le plancher sans refaire la mesure. Un réglage baissé « pour aller plus
    vite » coûterait des réponses sans que rien ne le dise.
    """
    assert semantic.RRF_PROFONDEUR >= 50, (
        f"profondeur {semantic.RRF_PROFONDEUR} sous le plancher mesuré (50) : "
        "refaire la mesure de la recherche hybride avant de la baisser")


def test_RRF_K_reste_dans_la_region_MESUREE() -> None:
    """Au-delà de 30, la grille est plate : 60, 120 et 300 rendent la même
    chose. La région basse fait légèrement mieux, aux deux profondeurs utiles.

    Le plafond est à 30 parce qu'au-dessus on quitte la zone où le réglage agit
    — et un réglage qui n'agit pas se croit choisi alors qu'il est hérité.
    """
    assert 1 <= semantic.RRF_K <= 30, (
        f"k={semantic.RRF_K} hors de la région mesurée [1, 30] : au-delà la "
        "grille est plate, refaire la mesure de la recherche hybride pour justifier")
