"""Le moteur d'embeddings doit refuser ce qu'il ne sait pas faire, et ne jamais
confondre un passage avec une question."""

from __future__ import annotations

import pytest

from core import embed


def test_un_modele_inconnu_est_refuse_pas_devine() -> None:
    """Un pooling ou un préfixe erroné dégrade la recherche EN SILENCE : le
    modèle n'échoue pas, il rend simplement de moins bons résultats. Mieux vaut
    refuser au chargement que rendre des réponses discrètement mauvaises."""
    with pytest.raises(embed.EmbedIndisponible, match="inconnu"):
        embed.profil("un/modele-jamais-mesure")


def test_chaque_modele_connu_declare_pooling_et_prefixes() -> None:
    for nom in ("intfloat/multilingual-e5-base", "BAAI/bge-m3"):
        p = embed.profil(nom)
        assert p["pooling"] in ("mean", "cls")
        assert isinstance(p["doc"], str) and isinstance(p["query"], str)
        assert p["dim"] > 0


def test_la_dimension_annoncee_suit_le_modele() -> None:
    """La table `vec0` fige la dimension dans son schéma : une valeur erronée
    ferait échouer chaque insertion."""
    assert embed.dimension("intfloat/multilingual-e5-small") == 384
    assert embed.dimension("intfloat/multilingual-e5-base") == 768
    assert embed.dimension("intfloat/multilingual-e5-large") == 1024


def test_passage_et_question_n_ont_pas_le_meme_prefixe() -> None:
    """`semantic._embed` servait les deux rôles sans les distinguer, ce qui
    privait le modèle des préfixes qu'il attend, et le rappel en souffrait."""
    p = embed.profil("intfloat/multilingual-e5-base")
    assert p["doc"] != p["query"]


def test_unload_vide_le_cache(monkeypatch) -> None:
    """`unload()` doit libérer la RAM unifiée avant une étape lourde, comme le
    fait `stt.unload()` pour les modèles de transcription."""
    monkeypatch.setitem(embed._cache, "faux/modele", ("tok", "mod"))
    assert embed._cache
    embed.unload()
    assert not embed._cache


def test_une_liste_vide_ne_charge_aucun_modele() -> None:
    """Indexer un document sans passage ne doit pas payer un chargement."""
    embed.unload()
    assert embed.embed_documents([]) == []
    assert not embed._cache


# Les deux tests du repli Ollama (`search_document:` / `search_query:`, et son
# indisponibilité) sont partis avec le repli lui-même. La
# vectorisation est LOCALE, sans alternative.


@pytest.mark.ml
def test_le_modele_local_produit_des_vecteurs_normalises() -> None:
    """Vérification de bout en bout — nécessite les poids du modèle."""
    import math

    vecteurs = embed.embed_documents(["Jean Lefèvre a été nommé en 2012."])
    question = embed.embed_query("Quand a-t-il été nommé ?")

    assert len(vecteurs[0]) == embed.dimension()
    assert len(question) == embed.dimension()
    # La recherche compare des cosinus : les vecteurs doivent être unitaires.
    assert math.isclose(math.sqrt(sum(x * x for x in question)), 1.0, rel_tol=1e-4)
