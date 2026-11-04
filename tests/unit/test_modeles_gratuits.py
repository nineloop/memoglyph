"""La règle des modèles gratuits (chantier 006, tranche C) : la règle seule, puis son passage par la façade.

Un modèle que le SERVICE marque comme gratuit ne reçoit jamais un document « ultra-confidentiel » (décision du 4/11, 13:55 :
« public · normal » et « confidentiel » passent) : plus strict que la règle du nuage, qui accepte l'ultra autorisé par le compte,
qui laisse passer le « confidentiel » et accepte l'« ultra-confidentiel » autorisé par le compte. La règle ne regarde NI le
nuage NI l'autorisation. Elle vit en UN endroit (`core/modeles_gratuits.py`, constante `SENSIBILITES_PERMISES`) : l'assouplir ou
la retirer est un changement d'une ligne.

Les modules sont importés PAR LES TESTS (fixtures) : avant leur existence, chaque test échoue pour la même cause.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import logging

import pytest

from core import llm, sensitivity
from tests.unit.test_llm_nomme import CLE, MODELE, Transport, environnement_propre  # noqa: F401 — fixture autouse réutilisée

pytestmark = pytest.mark.unit

GRATUIT = "auteur/un-modele:free"
ROUTEUR = "openrouter/free"
PAYANT = "auteur/un-modele-payant"


@pytest.fixture
def mg():
    return importlib.import_module("core.modeles_gratuits")


@pytest.fixture
def nomme():
    return importlib.import_module("core.llm_nomme")


# ---------- la reconnaissance : par le service, jamais par le prix ni par l'adresse ----------

@pytest.mark.parametrize("modele,attendu", [
    (GRATUIT, True), (ROUTEUR, True), ("X/Y:FREE", True), ("  auteur/m:free ", True),
    (PAYANT, False), ("auteur/m:free-extra", False), ("auteur/m:freedom", False), ("openrouter/free-foo", False),
    ("auteur/free", False), ("", False),
])
def test_openrouter_reconnait_le_suffixe_et_le_routeur_gratuits(modele, attendu, mg) -> None:
    assert mg.est_gratuit("openrouter", modele) is attendu


@pytest.mark.parametrize("modele", [GRATUIT, ROUTEUR, PAYANT, "llama-3.3-70b-versatile"])
def test_groq_n_a_aucun_modele_gratuit_marque_par_le_service(modele, mg) -> None:
    """Chez Groq la gratuité est un palier de COMPTE, sans marqueur par modèle (research.md D4) : jamais gratuit, même si
    l'identifiant ressemble à celui d'OpenRouter."""
    assert mg.est_gratuit("groq", modele) is False


@pytest.mark.parametrize("fournisseur", ["mlx", "compatible", "mistral", "openai", "anthropic", "ollama", "inconnu"])
def test_les_autres_fournisseurs_ne_sont_jamais_concernes(fournisseur, mg) -> None:
    assert mg.est_gratuit(fournisseur, GRATUIT) is False
    assert mg.est_gratuit(fournisseur, ROUTEUR) is False


def test_la_regle_est_dans_une_seule_constante(mg) -> None:
    assert mg.SENSIBILITES_PERMISES == frozenset({"standard", "confidential"})


# ---------- verifier : la règle seule ----------

@pytest.mark.parametrize("sensibilite", ["ultra_confidential"])
@pytest.mark.parametrize("modele", [GRATUIT, ROUTEUR])
def test_un_modele_gratuit_refuse_l_ultra_confidentiel(sensibilite, modele, mg) -> None:
    with pytest.raises(sensitivity.ModeleGratuitRefuse) as exc:
        mg.verifier("openrouter", modele, [sensibilite])
    assert isinstance(exc.value, sensitivity.EnvoiRefuse), "un refus du cœur : jamais absorbé comme une panne"
    assert exc.value.code == "model.free.restricted"


@pytest.mark.parametrize("sensibilite", ["standard", "confidential", None, "", "valeur-inconnue"])
def test_un_modele_gratuit_accepte_le_public_normal_le_confidentiel_et_ce_qui_se_lit_comme_tel(sensibilite, mg) -> None:
    """`normaliser` : absent ou inconnu se lit « standard » (compatibilité ascendante) — la règle suit la lecture du système."""
    mg.verifier("openrouter", GRATUIT, [sensibilite])


def test_un_modele_non_gratuit_ne_declenche_pas_la_regle(mg) -> None:
    for sensibilite in ("confidential", "ultra_confidential", "standard"):
        mg.verifier("openrouter", PAYANT, [sensibilite])
        mg.verifier("groq", GRATUIT, [sensibilite])       # Groq : jamais gratuit


def test_un_seul_document_ultra_parmi_plusieurs_suffit_au_refus(mg) -> None:
    with pytest.raises(sensitivity.ModeleGratuitRefuse):
        mg.verifier("openrouter", GRATUIT, ["standard", "confidential", "ultra_confidential"])


def test_un_envoi_sans_document_est_accepte_car_rien_ne_part(mg) -> None:
    """Décision écrite : une liste vide, c'est un appel qui ne porte aucun texte de document (mesure, test)."""
    mg.verifier("openrouter", GRATUIT, [])


def test_la_phrase_dit_ce_qui_se_passe_et_quoi_faire(mg) -> None:
    with pytest.raises(sensitivity.ModeleGratuitRefuse) as exc:
        mg.verifier("openrouter", GRATUIT, ["ultra_confidential"])
    texte = str(exc.value)
    assert texte == ("Un modèle gratuit ne reçoit pas de document ultra-confidentiel : choisissez un modèle payant "
                     "ou un autre fournisseur.")
    assert texte != sensitivity.REFUS_CLOUD, "ce n'est pas la phrase du refus du nuage"
    assert GRATUIT not in texte, "pas d'identifiant de modèle dans la phrase : elle est la même pour tous"


def test_la_regle_ne_lit_pas_l_autorisation_du_nuage(mg, monkeypatch) -> None:
    def interdit(*a, **k):
        raise AssertionError("la règle des gratuits ne consulte pas l'autorisation du compte")
    monkeypatch.setattr(sensitivity, "autorise", interdit)
    with pytest.raises(sensitivity.ModeleGratuitRefuse):
        mg.verifier("openrouter", GRATUIT, ["ultra_confidential"])


def test_un_modele_non_gratuit_ne_lit_meme_pas_la_sensibilite(mg) -> None:
    """Aucune lecture, aucun effet de bord pour tout ce qui n'est pas gratuit : le comportement existant est INCHANGÉ."""
    class Piege:
        def __eq__(self, autre):
            raise AssertionError("la sensibilité n'a pas à être lue pour un modèle non gratuit")
        __hash__ = None

    mg.verifier("openrouter", PAYANT, [Piege()])
    mg.verifier("mistral", GRATUIT, [Piege()])


# ---------- UN seul endroit : ni `sensitivity.controler` (filtré par rag.py) ni un import caché ----------

def _noms_dans(module) -> set[str]:
    arbre = ast.parse(inspect.getsource(module))
    noms: set[str] = set()
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Import):
            noms.update(a.name for a in noeud.names)
        elif isinstance(noeud, ast.ImportFrom):
            noms.add(noeud.module or "")
            noms.update(a.name for a in noeud.names)
        elif isinstance(noeud, (ast.ClassDef, ast.FunctionDef)):
            noms.add(noeud.name)
        elif isinstance(noeud, ast.Name):
            noms.add(noeud.id)
        elif isinstance(noeud, ast.Attribute):
            noms.add(noeud.attr)
    return noms


def test_le_module_de_sensibilite_ne_connait_pas_la_regle_des_gratuits(mg) -> None:
    """`rag.py` FILTRE les passages sur `EnvoiRefuse` autour de `sensitivity.controler` : une règle logée là serait un filtre
    silencieux au lieu d'un refus. La règle arrive à `autoriser_envoi` par un rappel (`avant_trace`), jamais par un import."""
    noms = _noms_dans(sensitivity)
    assert "ModeleGratuitRefuse" in noms, "ancre : la classe de l'exception vit bien dans ce module"
    assert not any("modeles_gratuits" in n for n in noms)


def test_controler_ne_lit_aucun_modele_et_ne_refuse_pas_un_confidentiel(mg, monkeypatch) -> None:
    def interdit(*a, **k):
        raise AssertionError("`sensitivity.controler` n'appelle pas la règle des gratuits")
    monkeypatch.setattr(mg, "verifier", interdit)
    envoi = sensitivity.Envoi(sensibilite="confidential", proprietaire="alice", document_id="d")
    assert sensitivity.controler(envoi, local=False) is False


# ---------- le passage par la façade (la revérification du cœur, avant chaque appel) ----------

@pytest.fixture
def moteur(nomme, transport_vierge, monkeypatch):
    """Les deux moteurs nommés, prêts, sur un transport simulé qui compte les requêtes."""
    for nom in ("openrouter", "groq"):
        profil = nomme.PROFILS[nom]
        monkeypatch.setenv(profil.variable_cle, CLE)
        monkeypatch.setenv(f"MEMOGLYPH_{profil.variable_modele}", MODELE)
    monkeypatch.setattr(nomme, "client_sans_fuite", transport_vierge.fabrique)
    return transport_vierge


@pytest.fixture
def transport_vierge():
    return Transport()


def _modele_de(monkeypatch, nom, nomme, modele):
    monkeypatch.setenv(f"MEMOGLYPH_{nomme.PROFILS[nom].variable_modele}", modele)


def _envoi(sensibilite, **kw):
    return sensitivity.Envoi(sensibilite=sensibilite, proprietaire=kw.pop("proprietaire", "alice"),
                             document_id=kw.pop("document_id", "doc-1"))


@pytest.mark.parametrize("appel", ["chat", "chat_json"])
@pytest.mark.parametrize("sensibilite", ["ultra_confidential"])
def test_la_facade_refuse_un_gratuit_avant_tout_appel_au_service(appel, sensibilite, moteur, nomme, monkeypatch) -> None:
    _modele_de(monkeypatch, "openrouter", nomme, GRATUIT)
    monkeypatch.setattr(sensitivity, "autorise", lambda proprietaire: True)       # même autorisé au nuage
    with pytest.raises(sensitivity.ModeleGratuitRefuse):
        getattr(llm, appel)("s", "u", provider="openrouter", envoi=_envoi(sensibilite))
    assert moteur.requetes == [], "aucune requête n'est partie"


def test_la_facade_accepte_le_public_normal_avec_un_gratuit(moteur, nomme, monkeypatch) -> None:
    _modele_de(monkeypatch, "openrouter", nomme, GRATUIT)
    assert llm.chat("s", "u", provider="openrouter", envoi=_envoi("standard")) == "réponse de test"
    assert len(moteur.requetes) == 1


def test_la_facade_accepte_un_confidentiel_avec_un_gratuit(moteur, nomme, monkeypatch) -> None:
    _modele_de(monkeypatch, "openrouter", nomme, GRATUIT)
    assert llm.chat("s", "u", provider="openrouter", envoi=_envoi("confidential")) == "réponse de test"
    assert len(moteur.requetes) == 1


def test_la_facade_accepte_un_appel_sans_document_avec_un_gratuit(moteur, nomme, monkeypatch) -> None:
    _modele_de(monkeypatch, "openrouter", nomme, GRATUIT)
    assert llm.chat("s", "u", provider="openrouter", envoi=None) == "réponse de test"


def test_la_facade_ne_touche_pas_un_modele_payant_ni_groq(moteur, nomme, monkeypatch) -> None:
    monkeypatch.setattr(sensitivity, "autorise", lambda proprietaire: True)
    _modele_de(monkeypatch, "openrouter", nomme, PAYANT)
    llm.chat("s", "u", provider="openrouter", envoi=_envoi("ultra_confidential"))
    _modele_de(monkeypatch, "groq", nomme, GRATUIT)
    llm.chat("s", "u", provider="groq", envoi=_envoi("ultra_confidential"))
    assert len(moteur.requetes) == 2


def test_un_document_passe_en_ultra_pendant_le_travail_arrete_l_appel_suivant(moteur, nomme, monkeypatch) -> None:
    """La revérification : l'envoi porte « standard » (figé au départ), le document est relu « ultra-confidentiel » juste avant
    l'appel (le compte a autorisé le cloud : c'est la règle des gratuits qui refuse)."""
    _modele_de(monkeypatch, "openrouter", nomme, GRATUIT)
    monkeypatch.setattr(sensitivity, "autorise", lambda proprietaire: True)
    llm.chat("s", "u", provider="openrouter", envoi=_envoi("standard"))
    monkeypatch.setattr(sensitivity, "_resolveur_sensibilite", lambda document_id: "ultra_confidential")
    with pytest.raises(sensitivity.ModeleGratuitRefuse):
        llm.chat("s", "u", provider="openrouter", envoi=_envoi("standard"))
    assert len(moteur.requetes) == 1, "seul le premier appel est parti"


def test_un_document_passe_en_confidentiel_pendant_le_travail_n_arrete_plus_rien(moteur, nomme, monkeypatch) -> None:
    _modele_de(monkeypatch, "openrouter", nomme, GRATUIT)
    llm.chat("s", "u", provider="openrouter", envoi=_envoi("standard"))
    monkeypatch.setattr(sensitivity, "_resolveur_sensibilite", lambda document_id: "confidential")
    llm.chat("s", "u", provider="openrouter", envoi=_envoi("standard"))
    assert len(moteur.requetes) == 2


def test_une_relecture_impossible_refuse_l_appel_echec_ferme(moteur, nomme, monkeypatch) -> None:
    _modele_de(monkeypatch, "openrouter", nomme, GRATUIT)

    def casse(document_id):
        raise RuntimeError("index illisible")
    monkeypatch.setattr(sensitivity, "_resolveur_sensibilite", casse)
    with pytest.raises(sensitivity.EnvoiRefuse):
        llm.chat("s", "u", provider="openrouter", envoi=_envoi("standard"))
    assert moteur.requetes == []


def test_l_ultra_non_autorise_garde_sa_propre_phrase_et_passe_avant_la_regle_des_gratuits(moteur, nomme, monkeypatch) -> None:
    """Ordre des deux règles : le refus du nuage vient D'ABORD. Un ultra non autorisé reste « le nuage refuse » (et non « le
    modèle gratuit refuse ») : le message ne change pas pour ce cas, qui existait déjà."""
    _modele_de(monkeypatch, "openrouter", nomme, GRATUIT)
    monkeypatch.setattr(sensitivity, "autorise", lambda proprietaire: False)
    with pytest.raises(sensitivity.EnvoiRefuse) as exc:
        llm.chat("s", "u", provider="openrouter", envoi=_envoi("ultra_confidential"))
    assert type(exc.value) is sensitivity.EnvoiRefuse and str(exc.value) == sensitivity.REFUS_CLOUD
    assert moteur.requetes == []


def test_un_envoi_refuse_par_les_gratuits_n_ecrit_pas_de_ligne_d_envoi_autorise(moteur, nomme, monkeypatch, caplog) -> None:
    """La trace « ultra-confidentiel envoyé avec autorisation » ne doit jamais décrire un envoi qui n'a pas eu lieu."""
    _modele_de(monkeypatch, "openrouter", nomme, GRATUIT)
    monkeypatch.setattr(sensitivity, "autorise", lambda proprietaire: True)
    with caplog.at_level(logging.INFO, logger="memoglyph.envoi"):
        with pytest.raises(sensitivity.ModeleGratuitRefuse):
            llm.chat("s", "u", provider="openrouter", envoi=_envoi("ultra_confidential"))
    assert [r for r in caplog.records if r.name == "memoglyph.envoi"] == []


def test_ancre_un_ultra_autorise_avec_un_modele_payant_part_et_ecrit_sa_ligne(moteur, nomme, monkeypatch, caplog) -> None:
    """L'ancre du test précédent : le même envoi, avec un modèle non gratuit, part ET est tracé."""
    _modele_de(monkeypatch, "openrouter", nomme, PAYANT)
    monkeypatch.setattr(sensitivity, "autorise", lambda proprietaire: True)
    with caplog.at_level(logging.INFO, logger="memoglyph.envoi"):
        llm.chat("s", "u", provider="openrouter", envoi=_envoi("ultra_confidential", document_id="doc-7"))
    lignes = [r.getMessage() for r in caplog.records if r.name == "memoglyph.envoi"]
    assert len(lignes) == 1 and "doc-7" in lignes[0]
    assert len(moteur.requetes) == 1


@pytest.mark.parametrize("porte,relue,attendu", [
    ("standard", "confidential", "confidential"),
    ("standard", "ultra_confidential", "ultra_confidential"),
    ("confidential", "ultra_confidential", "ultra_confidential"),
    ("confidential", "standard", "confidential"),       # la relecture ne fait que relever
    ("standard", "standard", "standard"),
    ("standard", None, "standard"),                      # document inconnu du résolveur : la valeur portée vaut
    ("ultra_confidential", "standard", "ultra_confidential"),
])
def test_la_relecture_ne_fait_que_relever_la_sensibilite(porte, relue, attendu, monkeypatch) -> None:
    monkeypatch.setattr(sensitivity, "_resolveur_sensibilite", lambda document_id: relue)
    assert sensitivity.sensibilite_actuelle(_envoi(porte)) == attendu
