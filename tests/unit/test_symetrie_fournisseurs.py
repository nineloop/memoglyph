"""Les deux fournisseurs traversent le MÊME tronc métier. Éprouvé, pas supposé.

## Ce que ce fichier vérifie, et ce qu'il ne vérifie pas

Le filet de réparation et le nettoyage de transport ont été éprouvés sur des
sorties MLX. Le câblage côté Mistral existe — `_completer` remonte le
`finish_reason` — mais **aucun test ne l'exerçait** : les trois
tests d'intégration passaient tous `provider="mlx"`.

Un câblage non éprouvé n'est pas un câblage. Ces tests bouchonnent la **couche
HTTP** et rien d'autre : la sortie simulée traverse ensuite pour de vrai

    déballage de transport → parse → réparation → validation métier → statut

exactement comme en production. Un bouchon posé APRÈS ces couches ne prouverait
que lui-même.

## Ce qui reste hors de portée

L'identité octet pour octet des sorties Mistral. `random_seed` est un EFFORT du
service, pas une garantie ; l'affirmer sans mesure serait exactement le motif
que ce dépôt traque. Le critère cloud reste la stabilité FONCTIONNELLE, et il
se vérifie contre le vrai service, pas ici.
"""
from __future__ import annotations

import json

import pytest

from core import llm_mistral, report

TRANSCRIPT = (
    "## Transcript\n\n"
    "**Speaker 1** [00:00:04] L'équipe valide le lancement mardi.\n"
)


def _payload(n: int = 2) -> dict:
    return {"sections": [
        {"question_id": i, "title": f"Section {i}", "answer_status": "answered",
         "claims": [{"text": f"Affirmation {i}.",
                     "primary_evidence": {
                         "timestamp": "00:00:04",
                         "quote": "L'équipe valide le lancement mardi."},
                     "secondary_evidence": None}]}
        for i in range(1, n + 1)]}


def _brut(n: int = 2) -> str:
    return json.dumps(_payload(n), ensure_ascii=False)


def _mistral_rend(monkeypatch, sorties, motif="stop"):
    """Bouchonne la SEULE couche HTTP. Tout le reste est le vrai code.

    `_completer` est la porte unique vers l'API — `chat` en est
    la projection « texte seul ». C'est donc ici, et pas plus haut, que le
    bouchon doit se poser pour que `chat_json` fasse son vrai travail :
    déballage, parse, et remontée de la sortie brute avec l'erreur.
    """
    restantes = list(sorties)
    appels = {"n": 0}

    def _completer(system, user, **kwargs):
        appels["n"] += 1
        return (restantes.pop(0) if restantes else sorties[-1]), motif

    monkeypatch.setattr(llm_mistral, "_completer", _completer)
    return appels


def _generer(monkeypatch, sorties, motif="stop", attendues=2):
    journal: dict = {}
    appels = _mistral_rend(monkeypatch, sorties, motif)
    try:
        rendu = report._generer_sous_contrat(
            "instruction", TRANSCRIPT, attendues=attendues, language="fr",
            provider="mistral", mistral_api_key="cle-de-test",
            journal=journal)
    except report.llm.LLMError:
        rendu = None
    return rendu, journal, appels


# ---------------------------------------------------------------------------
# Les sept cas exigés, tous par le chemin Mistral
# ---------------------------------------------------------------------------


def test_mistral_json_valide_donne_PASS_DIRECT(monkeypatch) -> None:
    rendu, journal, appels = _generer(monkeypatch, [_brut()])

    assert journal["status"] == "PASS_DIRECT"
    assert journal["repair"] == {"applied": False}
    assert journal["envelope"] is None
    assert "Affirmation 1." in rendu
    assert appels["n"] == 1


def test_mistral_une_faute_de_balance_donne_PASS_REPAIRED(monkeypatch) -> None:
    """Le gain doit être le MÊME que sur MLX : une génération, pas deux."""
    rendu, journal, appels = _generer(monkeypatch, [_brut()[:-1]])

    assert journal["status"] == "PASS_REPAIRED"
    assert journal["repair"]["operation"] == "insert_closing_brace"
    assert journal["repair"]["original_sha"] != journal["repair"]["repaired_sha"]
    assert "Affirmation 1." in rendu
    # DEUX appels, et non un — c'est la mesure, pas un défaut de ce test.
    #
    # La boucle interne de `llm_mistral.chat_json` n'est pas supprimée par la
    # graine, contrairement à celle de MLX : elle rejoue une fois avant de
    # lever, donc AVANT que le filet ne voie la sortie. Sur le chemin cloud, la
    # réparation épargne la reprise MÉTIER, pas la reprise MOTEUR — le gain de
    # coût y vaut la moitié de celui mesuré en local.
    #
    # L'asymétrie est justifiée et documentée dans `llm_mistral.chat_json`.
    # Ce test la FIXE pour qu'elle cesse d'être invisible dans la facture.
    assert appels["n"] == 2, "la boucle interne du moteur brûle un appel payant"


def test_mistral_deux_operations_sont_refusees_et_la_reprise_est_CONSERVEE(
        monkeypatch) -> None:
    """Le refus rend la main à la boucle métier — il ne la court-circuite pas.

    C'est la propriété qui rend le filet sûr : quand il ne sait pas, le
    comportement d'avant lui reprend exactement la main.
    """
    # Deux symboles en trop, donc DEUX opérations : écart de 2, vérifié.
    # `_brut()[:-1] + "]}"` n'en demandait qu'une — remplacer le `]` final par
    # une `}` suffisait — et le test passait en croyant mesurer un refus.
    casse = _brut() + "]}"
    rendu, journal, appels = _generer(monkeypatch, [casse, casse, _brut()])

    assert journal["status"] == "PASS_RETRY"
    assert journal["attempt"] == 2
    # Trois appels facturés pour un document : la 1re tentative métier en brûle
    # DEUX (la boucle interne du moteur rejoue avant de lever), le filet refuse,
    # la 2ᵉ tentative en coûte un et aboutit. En local la même séquence en
    # coûterait deux — voir le commentaire de coût dans `llm_mistral.chat_json`.
    assert appels["n"] == 3
    assert "Affirmation 1." in rendu
    # Le refus est CONSIGNÉ, pas avalé : sans cette trace, un filet qui
    # refuserait tout ressemblerait à un filet qui n'a rien eu à faire.
    refus = [r for r in journal["repairs_attempted"] if not r["applied"]]
    assert len(refus) == 1
    assert refus[0]["refused"] == "aucune opération unique ne répare"


def test_mistral_residu_markdown_est_du_TRANSPORT_pas_une_reparation(
        monkeypatch) -> None:
    """Une clôture résiduelle compte PASS_DIRECT. Jamais PASS_REPAIRED.

    Les deux comptes ne se mélangent pas : le modèle n'a commis aucune faute de
    structure, et le dire autrement ferait paraître le filet plus utile qu'il
    n'est.
    """
    rendu, journal, appels = _generer(monkeypatch, [_brut() + "`"])

    assert journal["status"] == "PASS_DIRECT"
    assert journal["repair"] == {"applied": False}
    assert journal["envelope"] == "residual_fence"
    assert "Affirmation 1." in rendu
    assert appels["n"] == 1


def test_mistral_sortie_tronquee_n_est_jamais_reparee(monkeypatch) -> None:
    """`finish_reason != stop` : compléter reviendrait à écrire la fin."""
    rendu, journal, appels = _generer(
        monkeypatch, [_brut()[:-1]], motif="length")

    assert journal["status"] == "FAIL"
    assert journal["repair"]["applied"] is False
    assert rendu is None
    assert appels["n"] == 6, "la boucle métier a bien repris (×2 côté moteur)"


def test_mistral_reparable_mais_hors_contrat_est_refuse(monkeypatch) -> None:
    """Parsable ≠ conforme. Le nombre de sections vient du GABARIT."""
    rendu, journal, appels = _generer(
        monkeypatch, [_brut(2)[:-1]], attendues=3)

    assert journal["status"] == "FAIL"
    assert journal["repair"]["applied"] is False
    assert rendu is None


# ---------------------------------------------------------------------------
# La symétrie elle-même
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fournisseur", ["mlx", "mistral"])
def test_le_meme_defaut_donne_le_meme_statut_sur_les_deux_chemins(
        monkeypatch, fournisseur) -> None:
    """Une faute identique doit produire un verdict identique, des deux côtés.

    C'est la promesse du tronc commun : le fournisseur change le TRANSPORT, pas
    le contrat. Si les deux divergeaient ici, une mesure faite sur un moteur
    ne dirait rien de l'autre, que le produit sert aussi.
    """
    casse = _brut()[:-1]
    journal: dict = {}

    if fournisseur == "mistral":
        _mistral_rend(monkeypatch, [casse])
    else:
        def _chat_json(*a, **k):
            raise report.llm.LLMMalformedOutputError(
                "sortie mlx", brut=casse, finish_reason="stop")
        monkeypatch.setattr(report.llm, "chat_json", _chat_json)

    report._generer_sous_contrat(
        "instruction", TRANSCRIPT, attendues=2, language="fr",
        provider=fournisseur, mistral_api_key="cle-de-test", journal=journal)

    assert journal["status"] == "PASS_REPAIRED"
    assert journal["repair"]["operation"] == "insert_closing_brace"
    assert journal["attempt"] == 1


def test_l_asymetrie_de_reprise_interne_est_DOCUMENTEE_des_deux_cotes() -> None:
    """Une asymétrie justifiée s'écrit. Sinon elle sera « corrigée » à tort.

    MLX supprime sa boucle interne quand une graine est posée : MESURÉ, trois
    fois sur trois, la seconde sortie a la même empreinte que la première —
    7 345 jetons et 310 s jetés pour rien.

    Mistral la conserve, et c'est DÉLIBÉRÉ : `random_seed` est un effort du
    service, pas un RNG local qu'on réamorce. Tant que le déterminisme strict
    n'est pas démontré, la reprise peut servir.

    Ce test ne vérifie pas le comportement — les deux sont corrects — il
    vérifie que le RAISONNEMENT est écrit là où quelqu'un le cherchera.
    """
    from pathlib import Path
    racine = Path(__file__).resolve().parents[2] / "core"

    mlx = (racine / "llm_mlx.py").read_text(encoding="utf-8")
    assert "essais = 1 if seed is not None" in mlx
    assert "empreinte SHA-256 que la première" in mlx or "même empreinte" in mlx

    mistral = (racine / "llm_mistral.py").read_text(encoding="utf-8")
    boucle = mistral.split("for _ in range(retries + 1):")[0][-1400:]
    assert "random_seed" in boucle, (
        "la boucle Mistral doit dire POURQUOI elle n'est pas supprimée comme "
        "celle de MLX — sans quoi l'asymétrie passera pour une dette à "
        "uniformiser")


def test_le_tronc_ne_branche_pas_sur_le_fournisseur() -> None:
    """Le fournisseur choisit un TRANSPORT, jamais une règle métier.

    Un `if provider == …` dans `core/report.py` ou `core/report_citations.py`
    signifierait que les deux chemins ne rendent pas le même contrat — et une
    mesure faite sur l'un cesserait de valoir pour l'autre.

    L'exception tolérée est le passage de la clé d'API : c'est du transport.
    """
    from pathlib import Path
    racine = Path(__file__).resolve().parents[2] / "core"

    for fichier in ("report.py", "report_citations.py", "reparation_json.py"):
        source = (racine / fichier).read_text(encoding="utf-8")
        for numero, ligne in enumerate(source.splitlines(), 1):
            nue = ligne.split("#")[0]
            if 'provider ==' not in nue and 'fournisseur ==' not in nue:
                continue
            assert "api_key" in nue, (
                f"{fichier}:{numero} branche sur le fournisseur ailleurs que "
                f"pour la clé d'API : {ligne.strip()}")
