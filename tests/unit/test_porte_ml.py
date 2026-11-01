"""La CI ne peut pas exécuter les tests ML — elle peut dire qu'ils sont en retard.

`make test-ml` est la SEULE vérification du pipeline réel : attribution des
locuteurs sur une réunion à réponse connue, monotonie des horodatages, absence de boucle de
répétition, normalisation des vecteurs d'embedding. La CI ne peut pas la
remplacer, et pas par oubli : son profil léger n'a ni `torch` ni `mlx-whisper`.

Une porte manuelle que rien ne force à franchir finit par ne plus l'être. Le
harnais ML a déjà passé des jours cassé — six tests en erreur de
setup — sans que rien ne le signale.

Ce contrôle ne remplace pas la porte. Il rend son OUBLI visible : la CI compare
le dernier passage consigné à l'état du code, et signale la dérive.

## Pourquoi une EMPREINTE DE CONTENU, et non un SHA de commit

Le marqueur retenait un SHA de commit, puis exigeait que ce commit reste présent
dans l'historique. C'est incompatible avec les squash-merges : la branche porte
le commit, le squash en crée un NOUVEAU sur main, l'original disparaît de
l'historique distant — et `ml-gate.json` continuait de pointer vers lui. La
porte échouait alors sur main et sur toute branche dérivée, sans qu'aucun test
ML n'ait régressé.

Le marqueur retient désormais l'empreinte SHA-256 du CONTENU de chaque fichier
surveillé. Le contenu SURVIT au squash ; la porte ne dépend plus ni de la
stratégie de merge ni de la profondeur du clone. La dérive se mesure en
NOMBRE DE FICHIERS surveillés dont le contenu a changé depuis le dernier passage.

## Pourquoi surveiller `core/` + `run_ml_tests.py` et rien d'autre

Un commit de documentation ou de tests UI n'invalide pas un passage ML. Étendre
la surveillance à tout le dépôt rendrait le contrôle bruyant, et un contrôle
bruyant se fait désactiver — c'est ce qui est arrivé à un premier test-méta.
La mesure (chemins ET empreintes) est partagée avec l'écrivain du marqueur,
`run_ml_tests`, pour qu'ils ne puissent pas diverger.
"""

from __future__ import annotations

import json
from pathlib import Path

from run_ml_tests import (
    CHEMINS_SURVEILLES,
    empreintes_surveillees,
    fichiers_surveilles,
)

RACINE = Path(__file__).resolve().parents[2]
MARQUEUR = RACINE / "ml-gate.json"

# Seuil délibérément GÉNÉREUX. Le but est d'attraper l'oubli durable — « le
# pipeline a beaucoup dérivé depuis le dernier passage ML » — pas de réclamer un
# passage à chaque fichier touché. Un seuil serré transformerait le contrôle en
# nuisance, et une nuisance se contourne. Exprimé maintenant en NOMBRE DE
# FICHIERS surveillés modifiés (le contenu, pas les commits qui l'ont produit).
FICHIERS_TOLERES = 25


def _marqueur() -> dict:
    assert MARQUEUR.is_file(), (
        f"{MARQUEUR.name} absent — lancez `make test-ml` et committez le "
        f"marqueur. Sans lui, rien ne dit quand le pipeline réel a été vérifié.")
    return json.loads(MARQUEUR.read_text(encoding="utf-8"))


def _derive(marqueur: dict) -> int:
    """Nombre de fichiers surveillés dont le contenu diffère du dernier passage.

    Compte les MODIFIÉS, les AJOUTÉS (présents maintenant, absents du marqueur)
    et les RETIRÉS (consignés, disparus). Aucun accès à l'historique git : c'est
    ce qui rend la mesure insensible au squash-merge et au clone superficiel.
    """
    actuelles = empreintes_surveillees(RACINE)
    consignees = marqueur["watched"]
    tous = set(actuelles) | set(consignees)
    return sum(1 for f in tous if actuelles.get(f) != consignees.get(f))


def test_le_pipeline_reel_a_ete_verifie_recemment() -> None:
    """Le contrôle lui-même."""
    marqueur = _marqueur()
    retard = _derive(marqueur)
    assert retard <= FICHIERS_TOLERES, (
        f"{retard} fichiers surveillés modifiés depuis le dernier passage des "
        f"tests ML ({marqueur['date']}).\nLa CI ne peut pas les exécuter — "
        f"lancez `make test-ml` et committez `{MARQUEUR.name}`.")


def test_le_marqueur_porte_les_champs_du_contrat() -> None:
    marqueur = _marqueur()
    assert set(marqueur) == {"date", "modules", "watched"}, sorted(marqueur)
    assert marqueur["date"].startswith("20"), marqueur["date"]
    assert len(marqueur["modules"]) >= 3, marqueur["modules"]
    # ANCRE DE NON-VACUITÉ nº1 : un marqueur aux empreintes vides rendrait la
    # dérive éternellement nulle. Le pipeline compte des dizaines de fichiers.
    watched = marqueur["watched"]
    assert isinstance(watched, dict) and len(watched) >= 30, len(watched)
    assert all(len(h) == 64 and all(c in "0123456789abcdef" for c in h)
               for h in watched.values()), "empreintes non SHA-256"


def test_le_marqueur_NE_stocke_PLUS_de_commit() -> None:
    """Le cœur du correctif : la porte ne dépend plus de l'existence permanente
    d'un commit de branche, que le squash-merge efface. Un retour discret au
    champ `commit` rouvrirait exactement ce défaut."""
    assert "commit" not in _marqueur()


def test_le_marqueur_designe_des_modules_QUI_EXISTENT() -> None:
    """Motif nº1 : dériver du réel. Un module renommé rendrait le marqueur
    rassurant sur un test qui ne tourne plus."""
    manquants = [m for m in _marqueur()["modules"] if not (RACINE / m).is_file()]
    assert manquants == [], f"module(s) consigné(s) mais absent(s) : {manquants}"


def test_le_marqueur_couvre_le_pipeline_surveille() -> None:
    """ANCRE DE NON-VACUITÉ nº2 : le marqueur doit empreinter le pipeline RÉEL.

    S'il n'empreintait qu'une poignée de fichiers, ou des chemins qui ne
    correspondent plus au dépôt, la dérive serait sous-estimée et le seuil
    inerte. On exige donc une couverture large des fichiers actuellement suivis
    sous les chemins surveillés.
    """
    suivis = set(fichiers_surveilles(RACINE))
    assert len(suivis) > 30, (
        f"les chemins surveillés {CHEMINS_SURVEILLES} ne rendent que "
        f"{len(suivis)} fichiers — ils ne correspondent plus au dépôt")
    consignes = set(_marqueur()["watched"])
    # Au dernier passage, le marqueur couvrait le pipeline. Un peu de dérive est
    # tolérée (c'est tout le sujet du seuil) ; une couverture QUASI VIDE, non.
    couverts = len(suivis & consignes)
    assert couverts >= len(suivis) - FICHIERS_TOLERES, (
        f"le marqueur ne couvre que {couverts}/{len(suivis)} fichiers du "
        f"pipeline — empreinte partielle ou chemins désynchronisés")


def test_la_derive_REAGIT_a_une_divergence() -> None:
    """ANCRE DE NON-VACUITÉ nº3 — motif : aucune sortie de secours muette.

    `retard <= 25` doit être vrai parce que le passage est récent, jamais parce
    que le calcul ne mesure rien. Face à un marqueur aux empreintes vides, la
    dérive doit valoir le nombre de fichiers surveillés — largement au-dessus du
    seuil. Un calcul qui rendrait 0 ici serait éternellement vert.
    """
    vide = {"date": "2026-01-01T00:00:00+00:00", "modules": ["a", "b", "c"],
            "watched": {}}
    retard = _derive(vide)
    assert retard == len(fichiers_surveilles(RACINE)) > FICHIERS_TOLERES, retard

    # Et il doit RÉAGIR à une seule divergence : fausser une empreinte du vrai
    # marqueur augmente la dérive d'exactement un.
    reel = _marqueur()
    base = _derive(reel)
    falsifie = {**reel, "watched": {**reel["watched"]}}
    un_fichier = next(iter(falsifie["watched"]))
    falsifie["watched"][un_fichier] = "0" * 64
    assert _derive(falsifie) == base + 1


def test_le_marqueur_est_VERSIONNE() -> None:
    """S'il était gitignoré, la CI ne le verrait jamais et le contrôle
    échouerait à chaque clone — ou pire, serait retiré pour cette raison."""
    import subprocess

    verdict = subprocess.run(
        ["git", "ls-files", "--error-unmatch", MARQUEUR.name],
        cwd=RACINE, capture_output=True, text=True)
    assert verdict.returncode == 0, (
        f"{MARQUEUR.name} n'est pas suivi par git — la CI ne peut pas le lire")
