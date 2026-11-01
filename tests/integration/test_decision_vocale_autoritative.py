"""Une décision vocale ne doit jamais réussir en base et échouer dans l'autoritatif.

Règle : `.voices.json` est AUTORITATIF, SQLite est
dérivé et reconstructible. Les écritures autoritatives étaient pourtant
best-effort et avalaient toute exception : sur disque plein, droits ou magasin
illisible, l'API rendait un succès, l'utilisateur voyait son nom — et il
disparaissait à la réindexation suivante.

Prouver le chemin passant ne suffit pas : ces tests prouvent le chemin qui
échoue, qui est le seul où le bug vivait.
"""

from __future__ import annotations

import pytest

from core import voice_registry, voiceprint


def _vec(*xs: float) -> list[float]:
    return list(xs) + [0.0] * (256 - len(xs))


@pytest.fixture
def deux_voix(isolated_stores):
    voiceprint.index_file("a.md", {"Speaker 1": _vec(1.0)})
    voiceprint.index_file("b.md", {"Speaker 1": _vec(0.0, 1.0)})
    voix = voiceprint.list_voices()
    assert len(voix) == 2
    return [v["id"] for v in voix]


def _casser_le_magasin(monkeypatch, cible: str = "_ecrire") -> None:
    """Injecte l'échec sur l'ÉCRITURE elle-même.

    Patcher `nommer` et `fusionner` ne viserait que le premier de plusieurs
    appels possibles : le cas où une écriture SUIVANTE échoue resterait
    invisible. En visant `_ecrire`, on couvre le disque plein réel, quel que
    soit le découpage interne.
    """
    def refuser(*a, **k):
        raise OSError("disque plein (simulé)")
    monkeypatch.setattr(voice_registry, cible, refuser)


def test_un_nom_non_consigne_ne_reste_pas_en_base(deux_voix, monkeypatch) -> None:
    """Le faux succès : nom visible, puis effacé à la réindexation."""
    vid = deux_voix[0]
    _casser_le_magasin(monkeypatch)

    with pytest.raises(OSError):
        voiceprint.rename(vid, "Alice")

    apres = {v["id"]: v["label"] for v in voiceprint.list_voices()}
    assert apres[vid] is None, "la base a été mutée alors que l'autoritatif a refusé"


def test_une_fusion_non_journalisee_ne_detruit_pas_la_source(deux_voix, monkeypatch) -> None:
    """Sans ça, la source disparaissait de la base et la fusion n'existait
    nulle part : la réindexation rendait deux voix, décision perdue."""
    source, cible = deux_voix
    _casser_le_magasin(monkeypatch)

    with pytest.raises(OSError):
        voiceprint.merge_voices(source, cible)

    restantes = {v["id"] for v in voiceprint.list_voices()}
    assert source in restantes, "la source a été supprimée sans que la fusion soit journalisée"
    assert len(restantes) == 2


def test_le_chemin_passant_marche_toujours(deux_voix) -> None:
    """La sévérité ne doit pas casser l'usage normal."""
    vid = deux_voix[0]
    voiceprint.rename(vid, "Alice")

    assert {v["id"]: v["label"] for v in voiceprint.list_voices()}[vid] == "Alice"
    uids = voice_registry.lire()["voix"]
    assert any(v.get("label") == "Alice" for v in uids.values())


def test_une_fusion_reussie_est_journalisee_avant_la_suppression(deux_voix) -> None:
    source, cible = deux_voix
    voiceprint.merge_voices(source, cible)

    fusions = voice_registry.lire()["fusions"]
    assert len(fusions) == 1
    assert {v["id"] for v in voiceprint.list_voices()} == {cible}


def test_une_decision_est_publiee_en_UNE_seule_ecriture(deux_voix, monkeypatch) -> None:
    """Nommer en `nommer()` puis `enregistrer_membres()`, deux écritures
    atomiques indépendantes, rendrait l'état ambigu : un échec de la seconde
    publierait déjà le nom, laisserait SQLite ancien et annoncerait un échec.
   """
    ecritures = []
    vrai = voice_registry._ecrire
    monkeypatch.setattr(voice_registry, "_ecrire",
                        lambda d, r=None: (ecritures.append(1), vrai(d, r))[1])

    voiceprint.rename(deux_voix[0], "Alice")
    assert len(ecritures) == 1, f"{len(ecritures)} écritures pour une seule décision"

    ecritures.clear()
    voiceprint.merge_voices(deux_voix[0], deux_voix[1])
    assert len(ecritures) == 1, f"{len(ecritures)} écritures pour une seule fusion"


def test_une_fusion_reessayee_ne_double_pas_le_journal(deux_voix, monkeypatch) -> None:
    """Le journal des fusions est APPEND-ONLY : deux entrées pour une seule
    décision humaine seraient une réécriture de l'histoire."""
    source, cible = deux_voix
    # L'échec porte sur la DEUXIÈME écriture, pas la première : c'est le seul
    # cas qui distingue les deux conceptions. Avec deux `_ecrire()` séparés, la
    # fusion serait déjà journalisée quand la seconde échoue, et le réessai en
    # ajouterait une seconde pour une seule décision humaine. Avec une écriture
    # unique, rien n'est publié et le réessai en produit exactement une.
    #
    # Un drapeau plutôt que `monkeypatch.undo()`, qui défait AUSSI l'isolation
    # des magasins posée par la fixture et ferait écrire dans le corpus réel.
    etat = {"n": 0, "casse": True}
    vrai = voice_registry._ecrire

    def parfois(data, root=None):
        etat["n"] += 1
        if etat["casse"] and etat["n"] >= 2:
            raise OSError("disque plein (simulé) à la seconde écriture")
        return vrai(data, root)

    monkeypatch.setattr(voice_registry, "_ecrire", parfois)
    try:
        voiceprint.merge_voices(source, cible)
    except OSError:
        pass

    etat["casse"] = False
    voix_restantes = {v["id"] for v in voiceprint.list_voices()}
    if source in voix_restantes:      # la fusion a échoué : on réessaie
        voiceprint.merge_voices(source, cible)

    assert len(voice_registry.lire()["fusions"]) == 1
