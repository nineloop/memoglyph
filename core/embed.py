"""Embeddings de texte, LOCAUX par défaut.

La recherche par le sens et le RAG passaient toujours par Ollama
(`semantic.py` appelait `llm_ollama.embed` en dur, sans routage). Un
utilisateur en mode Mistral Cloud sans Ollama voyait donc sa transcription
réussir puis l'Assistant rester muet : la moitié de l'application ne servait
à rien.

Ce module rend la vectorisation **autonome** — ni Ollama, ni appel cloud, ni
facture d'API — dans les deux modes de traitement. Le modèle local retrouve
mieux les passages que l'existant : ce n'est pas un compromis pour gagner en
portabilité, c'est un gain.

DEUX RÔLES DISTINCTS. `embed_documents` et `embed_query` sont séparés parce que
les modèles de recherche attendent un préfixe différent pour un passage et pour
une question. `semantic._embed` servait les deux sans les distinguer — ce qui
privait nomic de ses préfixes et lui coûtait une nette part de son rappel. Le rôle est
désormais porté par l'appel, pas deviné.

UN SEUL ESPACE VECTORIEL par corpus : `semantic_index_meta` enregistre le
modèle et la dimension, et toute divergence lève `SemanticIndexIncompatible`,
qui déclenche une reconstruction explicite. On ne mélange jamais deux espaces
dans le même index — les distances y seraient incomparables, et la recherche
rendrait des résultats faux sans le dire.
"""

from __future__ import annotations

import threading

from .config import EMBED_LOCAL_MODEL
from .llm_base import LLMError

# Ce que chaque modèle attend. Se tromper de « pooling » n'est pas une nuance :
# bge-m3 perd nettement en rappel sous mean pooling au lieu de CLS.
_PROFILS: dict[str, dict] = {
    "intfloat/multilingual-e5-small": {
        "dim": 384, "pooling": "mean", "doc": "passage: ", "query": "query: ",
    },
    "intfloat/multilingual-e5-base": {
        "dim": 768, "pooling": "mean", "doc": "passage: ", "query": "query: ",
    },
    "intfloat/multilingual-e5-large": {
        "dim": 1024, "pooling": "mean", "doc": "passage: ", "query": "query: ",
    },
    "BAAI/bge-m3": {
        "dim": 1024, "pooling": "cls", "doc": "", "query": "",
    },
}

# Chargé à la première utilisation et CONSERVÉ : recharger coûte quelques
# secondes, à comparer aux ~20 ms d'une requête. `unload()` le libère avant une
# étape lourde, comme `stt.unload()` le fait pour les modèles de transcription.
_cache: dict[str, object] = {}
_VERROU = threading.RLock()


class EmbedIndisponible(LLMError):
    """Le moteur d'embeddings n'a pas pu être chargé ou interrogé.

    Hérite de `LLMError` parce que c'est le marqueur de fait de « service
    dégradé, réponds 503 » dans ce dépôt : les routes de recherche et de
    réindexation ne capturent que lui, et `semantic.SemanticIndexIncompatible`
    suit déjà cette convention. En `RuntimeError`, une absence de poids ou de
    `transformers` sortait en 500 brut, indistinguable d'un bug.

    Le type vient maintenant de `llm_base`, module neutre, et non plus du
    module d'un moteur : ces embeddings-ci n'ont rien à voir avec Ollama —
    le défaut est local (`transformers`), et le lien n'était qu'un accident
    d'emplacement du type d'erreur.
    """


def profil(model: str = EMBED_LOCAL_MODEL) -> dict:
    """Caractéristiques du modèle. Un modèle inconnu est REFUSÉ plutôt que
    deviné : un mauvais pooling ou un préfixe manquant dégrade la recherche en
    silence, sans jamais lever."""
    if model not in _PROFILS:
        raise EmbedIndisponible(
            f"Modèle d'embedding inconnu : {model!r}. "
            f"Connus : {', '.join(sorted(_PROFILS))}"
        )
    return _PROFILS[model]


def dimension(model: str = EMBED_LOCAL_MODEL) -> int:
    return profil(model)["dim"]


def _moteur(model: str):
    """(tokenizer, modèle) — chargé une seule fois par nom."""
    with _VERROU:
        if model in _cache:
            return _cache[model]
        profil(model)  # refuse tôt un modèle inconnu
        try:
            from transformers import AutoModel, AutoTokenizer
        except ImportError as exc:  # pragma: no cover — dépendance déjà requise
            raise EmbedIndisponible(f"transformers indisponible : {exc}") from exc
        try:
            tok = AutoTokenizer.from_pretrained(model)
            mod = AutoModel.from_pretrained(model)
            mod.eval()
        except Exception as exc:  # noqa: BLE001 — réseau, disque, poids absents
            raise EmbedIndisponible(
                f"Chargement du modèle d'embedding {model!r} impossible : {exc}"
            ) from exc
        _cache[model] = (tok, mod)
        return _cache[model]


def unload() -> None:
    """Libère le modèle (RAM unifiée). Rechargé au prochain appel.

    Ce que cette fonction rend RÉELLEMENT, mesuré : **8 à 10 % de
    l'empreinte physique**. Un modèle e5-base coûte 560 Mo d'empreinte, dont 504
    survivent à l'appel. L'essentiel du poids est mappé depuis le fichier de
    poids, et ces pages-là sont récupérées par le système sous pression sans
    passer par nous.

    Ne pas l'appeler entre deux recherches : le rechargement coûte des secondes
    pour rendre quelques dizaines de mégaoctets. Elle garde son sens avant un
    travail lourd d'une autre nature — transcription, compte-rendu — où l'on
    veut rendre tout ce qui peut l'être.

    Avertissement sur la MESURE : `ps rss` et `resource.ru_maxrss` comptent les
    pages mappées sur fichier et annoncent ~1,3 Go pour ce même modèle. C'est
    `vmmap -summary` (« Physical footprint ») qui dit ce qui pèse vraiment sur
    la mémoire de la machine. Mesuré ainsi, l'écart entre e5-base et e5-large
    est de +168 Mo, et non les +667 Mo qu'annonce `ps rss`.
    """
    with _VERROU:
        _cache.clear()
    try:
        import gc

        import torch

        gc.collect()
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
    except Exception:  # noqa: BLE001 — libérer est un bonus, jamais bloquant
        pass


def _encoder(textes: list[str], prefixe: str, model: str, lot: int) -> list[list[float]]:
    import torch

    tok, mod = _moteur(model)
    strategie = profil(model)["pooling"]
    sorties: list[list[float]] = []
    for debut in range(0, len(textes), lot):
        morceau = [prefixe + t for t in textes[debut:debut + lot]]
        enc = tok(morceau, padding=True, truncation=True,
                  max_length=512, return_tensors="pt")
        with torch.no_grad():
            etats = mod(**enc).last_hidden_state
        if strategie == "cls":
            vecs = etats[:, 0]
        else:
            masque = enc["attention_mask"].unsqueeze(-1).float()
            vecs = (etats * masque).sum(1) / masque.sum(1).clamp(min=1e-9)
        # Normalisés : la recherche compare des cosinus, pas des distances brutes.
        vecs = torch.nn.functional.normalize(vecs, p=2, dim=1)
        sorties.extend(v.tolist() for v in vecs)
    return sorties


def embed_documents(
    textes: list[str], *, model: str = EMBED_LOCAL_MODEL, lot: int = 16
) -> list[list[float]]:
    """Vectorise des PASSAGES destinés à être retrouvés."""
    if not textes:
        return []
    return _encoder(textes, profil(model)["doc"], model, lot)


def embed_query(
    texte: str, *, model: str = EMBED_LOCAL_MODEL
) -> list[float]:
    """Vectorise une QUESTION. Rôle distinct d'un passage : les deux ne
    s'encodent pas de la même façon, et les confondre dégrade le rappel."""
    return _encoder([texte], profil(model)["query"], model, 1)[0]


# Le repli `EMBED_BACKEND == "ollama"` est parti avec Ollama.
#
# Il était OPT-IN — le défaut est `local` depuis la refonte de ce module, donc
# personne ne l'utilisait sans l'avoir demandé. Le garder aurait maintenu une
# seconde implémentation d'un espace vectoriel SCELLÉ : deux façons de produire
# des vecteurs censés se comparer, dont une seule est mesurée. C'est le genre de
# chemin qu'on découvre cassé le jour où quelqu'un l'emprunte.
