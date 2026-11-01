"""Aucune étape du tronc ne part sans graine. Le témoin, pas la confiance.

## La brèche que ce fichier ferme

Trouvée en auditant le vrai chemin produit. `profiling.profile` était
le SEUL appel LLM du tronc à partir sans graine — et c'était le plus coûteux à
laisser libre :

    audio → profiling  ← AUCUNE GRAINE
          → le profil est ÉCRIT dans le `.md`
          → classify_genre le RELIT
          → gabarit → nombre de sections → compte-rendu entier

Un profil qui varie fait donc varier un document entier, deux étapes plus loin,
sans qu'aucune trace ne relie les deux.

Une mesure qui part de transcripts DÉJÀ profilés, dont le bloc de profil est figé
sur disque, est reproductible pour cette raison : elle certifie « transcript profilé
→ compte-rendu » là où l'utilisateur vit « audio → compte-rendu ».

## Pourquoi un test STRUCTUREL et non un test de comportement

Un test qui vérifierait « le profilage rend deux fois la même chose » ne
couvrirait que le profilage. La règle à tenir est plus large : **toute étape LLM
qui influence le document final porte une graine**. Une sixième étape ajoutée
demain sans graine rouvrirait la brèche exactement de la même façon, et aucun
test de comportement existant ne le verrait.

On lit donc le CODE, comme le fait déjà le témoin anti-fuite de clé de
`llm_mistral`. Le défaut est conservateur : un appel légitimement libre doit
être inscrit ci-dessous avec sa raison.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[2] / "core"

# Les modules du TRONC : ceux qu'un compte-rendu traverse. Les moteurs
# (`llm_mlx`, `llm_mistral`, `llm_ollama`) sont exclus — ce sont eux qui
# REÇOIVENT la graine, ils ne la décident pas.
TRONC = ("profiling.py", "report.py")

# Les appels délibérément SANS graine, et pourquoi. Y inscrire un appel est un
# choix qu'on assume par écrit, pas un oubli qu'on tolère.
LIBRES = {
    # `rag.py` répond à une question de l'utilisateur dans l'interface. Sa
    # sortie n'entre dans aucun document versionné et ne conditionne aucune
    # étape suivante : la figer n'apporterait rien et rendrait deux questions
    # identiques ennuyeuses à relire.
    "rag.py": "réponse interactive, hors document versionné",
}


def _appels_llm(source: str):
    """Les appels `llm.chat(...)` et `llm.chat_json(...)`, avec leurs mots-clés."""
    arbre = ast.parse(source)
    for noeud in ast.walk(arbre):
        if not isinstance(noeud, ast.Call):
            continue
        fonction = noeud.func
        if not isinstance(fonction, ast.Attribute):
            continue
        if fonction.attr not in ("chat", "chat_json"):
            continue
        if not (isinstance(fonction.value, ast.Name)
                and fonction.value.id == "llm"):
            continue
        yield noeud, {mot.arg for mot in noeud.keywords if mot.arg}


@pytest.mark.parametrize("fichier", TRONC)
def test_chaque_appel_llm_du_tronc_porte_une_graine(fichier) -> None:
    """La règle, vérifiée sur le code plutôt que sur une intention."""
    chemin = RACINE / fichier
    sans_graine = [
        noeud.lineno
        for noeud, mots in _appels_llm(chemin.read_text(encoding="utf-8"))
        if "seed" not in mots
    ]
    assert not sans_graine, (
        f"{fichier} : appel(s) LLM sans `seed` ligne(s) {sans_graine}. "
        f"Toute étape qui influence le document final doit être rejouable — "
        f"voir `config.PROFILE_SEED`. Si l'appel doit rester libre, inscris-le "
        f"dans `LIBRES` avec sa raison.")


def test_le_profilage_transmet_la_graine_jusqu_au_moteur() -> None:
    """La graine TRAVERSE `profile` → `analyze` → `llm.chat_json`.

    Le test structurel ci-dessus verrait un `seed=` littéral posé à l'appel.
    Celui-ci vérifie qu'il vient bien du paramètre, donc qu'un appelant — le
    test de reproductibilité, une mesure — peut le CHANGER. Une graine codée en
    dur serait reproductible et inutilisable pour mesurer.
    """
    from core import config, profiling

    vues: list = []

    class _FauxLLM:
        FOURNISSEUR_DEFAUT = "mlx"

        @staticmethod
        def cle_mistral(_provider, _cle):  # le profilage filtre la clé par la façade
            return None

        @staticmethod
        def chat_json(*a, **k):
            vues.append(k.get("seed"))
            return {"conversation": {"type": "t", "summary": "s", "topics": []}}

    origine = profiling.llm
    profiling.llm = _FauxLLM
    try:
        profiling.analyze(_transcript(), diarized=False, speaker_labels=[])
        profiling.analyze(_transcript(), diarized=False, speaker_labels=[],
                          seed=1234)
    finally:
        profiling.llm = origine

    assert vues == [config.PROFILE_SEED, 1234], (
        "la graine doit venir du paramètre, pas d'une constante à l'appel")


def _transcript():
    from core.stt_base import Segment, Transcript
    return Transcript(
        segments=[Segment(start=0.0, end=1.0, text="Bonjour.", speaker=None)],
        full_text="Bonjour.", language="fr")


def test_les_appels_LIBRES_sont_declares_avec_leur_raison() -> None:
    """Un appel hors graine existe, il est nommé, et il porte sa justification.

    Sans ce test, `LIBRES` pourrait se vider ou se remplir sans qu'on s'en
    aperçoive — et une liste d'exemptions qu'on ne regarde plus est le meilleur
    moyen de faire rentrer par la porte ce qu'on a chassé par la fenêtre.
    """
    for fichier, raison in LIBRES.items():
        assert (RACINE / fichier).exists(), f"{fichier} n'existe plus"
        assert len(raison) > 20, f"{fichier} : la raison doit être une phrase"
        sans_graine = [
            noeud.lineno
            for noeud, mots in _appels_llm(
                (RACINE / fichier).read_text(encoding="utf-8"))
            if "seed" not in mots]
        assert sans_graine, (
            f"{fichier} ne porte plus d'appel libre — retire-le de `LIBRES` "
            f"plutôt que de laisser une exemption sans objet")
