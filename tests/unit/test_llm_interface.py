"""L'interface de moteur LLM : une seule porte, un seul type d'erreur.

Trois propriétés sont contrôlées ici, et aucune ne l'était avant ce fichier.

1. **Aucune exception de moteur ne recopie son message.** La façade normalisait
   déjà ainsi le chemin Mistral (`type(exc).__name__` seul, jamais le message,
   parce qu'un corps de réponse d'API peut contenir la clé) — mais UNIQUEMENT
   ce chemin. Une exception inattendue du moteur local ressortait telle quelle.
   Tant que la normalisation dépend du moteur, ajouter un moteur rouvre le trou :
   la règle est vérifiée pour CHAQUE fournisseur du registre, par paramétrage,
   donc un moteur ajouté demain est contrôlé sans qu'on y pense.

2. **`chat_json` porte les contraintes de sortie.** `report.py` appelait le
   module Ollama en direct pour obtenir `num_predict`, `retries` et
   `json_schema` : la façade ne savait pas les porter, et tant qu'un
   contournement existe l'interface n'est pas l'unique porte.

3. **Le marqueur « réponds 503 » est neutre.** `LLMError` appartenait au module
   Ollama, alors que cinq `except` d'`api/main.py` et trois sous-classes en
   dépendent : retirer Ollama emportait la sémantique 503 de tout le dépôt.

Les imports de `core.llm_base` sont LOCAUX aux tests qui en ont besoin. Un
import au niveau module ferait échouer la COLLECTE du fichier entier tant que
le module neutre n'existe pas, et un échec de collecte masque le message de
chaque test — or c'est précisément ce message qui doit prouver le défaut.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core import llm

pytestmark = pytest.mark.unit

RACINE = Path(__file__).parents[2]

# Une clé plausible, et un corps d'erreur qui la renvoie en écho : c'est le cas
# réel (une API qui répète l'en-tête d'authentification dans son 401).
# Assemblée à l'exécution : le contrôle d'export ne doit pas y lire une clé.
CLE = "sk-" + "abcdefghijklmnopqrstuvwxyz0123456789"
FUITE = f'HTTP 401 {{"error":"invalid_api_key","provided":"{CLE}"}}'


def _fournisseurs() -> list[str]:
    """Tous les fournisseurs annoncés — pas une liste gelée dans le test.

    Un fournisseur ajouté au registre entre donc automatiquement sous la règle
    de sécurité ci-dessous. Une liste recopiée ici aurait laissé le prochain
    moteur hors contrôle, en restant verte.
    """
    return list(llm.FOURNISSEURS)


def _saboter(monkeypatch, methode: str, panne) -> None:
    """Installe la même panne sur CHAQUE moteur du registre.

    La version précédente nommait deux modules — `llm.llm_ollama` et
    `llm.llm_mistral` — et remplaçait leurs fonctions de niveau module. Le
    paramétrage promettait pourtant qu'« un moteur ajouté demain est contrôlé
    sans qu'on y pense » : c'était faux de la moitié du test. Le moteur MLX,
    ajouté aujourd'hui, n'a AUCUNE fonction de niveau module — sa mécanique vit
    dans la classe — et les trois contrôles de non-fuite seraient partis
    interroger un vrai serveur au lieu de vérifier quoi que ce soit.

    On sabote donc les INSTANCES du registre : c'est ce que la façade appelle
    réellement, quelle que soit la forme interne du moteur.
    """
    for moteur in llm.MOTEURS.values():
        monkeypatch.setattr(moteur, methode, panne, raising=False)


# ---------- 1. La normalisation des erreurs, uniforme et sans fuite ----------

@pytest.mark.parametrize("fournisseur", _fournisseurs())
def test_aucune_exception_de_moteur_ne_recopie_son_message(monkeypatch, fournisseur):
    """Le message d'une exception INATTENDUE ne doit jamais sortir de la façade.

    Réseau, 401, 429, timeout, JSON inattendu : un moteur laisse passer les
    erreurs de son SDK ou de son client HTTP, et leur texte peut contenir la
    clé. Seul le TYPE est conservé — assez pour diagnostiquer, incapable de
    publier un secret.
    """
    def fuite(*args, **kwargs):
        raise RuntimeError(FUITE)

    _saboter(monkeypatch, "chat", fuite)

    with pytest.raises(llm.LLMError) as capture:
        llm.chat("système", "utilisateur", provider=fournisseur, api_key=CLE)

    message = str(capture.value)
    assert CLE not in message, message
    assert "invalid_api_key" not in message, message
    # Le type, lui, est conservé : sans lui le 503 ne dirait plus rien.
    assert "RuntimeError" in message, message


@pytest.mark.parametrize("fournisseur", _fournisseurs())
def test_chat_json_normalise_aussi(monkeypatch, fournisseur):
    """Même règle sur l'autre méthode : c'est celle du compte-rendu."""
    def fuite(*args, **kwargs):
        raise RuntimeError(FUITE)

    _saboter(monkeypatch, "chat_json", fuite)

    with pytest.raises(llm.LLMError) as capture:
        llm.chat_json("système", "utilisateur", provider=fournisseur, api_key=CLE)

    assert CLE not in str(capture.value)
    assert "RuntimeError" in str(capture.value)


@pytest.mark.parametrize("fournisseur", _fournisseurs())
def test_le_diagnostic_d_une_LLMError_survit(monkeypatch, fournisseur):
    """Assainir ne doit pas rendre le 503 inutile.

    Une `LLMError` est déjà à la frontière : son message a été composé par le
    moteur, qui sait ce qu'il peut publier (`_erreur_sure` côté Mistral). Elle
    passe donc telle quelle — sinon « service indisponible » remplacerait
    « rate_limit_exceeded » et l'utilisateur ne saurait plus quoi faire.
    """
    def panne(*args, **kwargs):
        raise llm.LLMError("rate_limit_exceeded")

    _saboter(monkeypatch, "chat", panne)

    with pytest.raises(llm.LLMError) as capture:
        llm.chat("système", "utilisateur", provider=fournisseur, api_key=CLE)

    assert "rate_limit_exceeded" in str(capture.value)


# ---------- 2. Les contraintes de sortie traversent la façade ----------

def test_chat_json_porte_les_contraintes_sur_le_moteur_local(monkeypatch):
    """`max_tokens`, `schema`, `retries` — les trois raisons du contournement."""
    vus: dict = {}

    def faux_chat_json(system, user, **kwargs):
        vus.update(kwargs)
        return {"ok": True}

    # Le bouchon vise l'INSTANCE du registre : le moteur local n'a plus de
    # fonction de niveau module, sa mécanique vit dans la classe.
    monkeypatch.setattr(llm.MOTEURS["mlx"], "chat_json", faux_chat_json)
    schema = {"type": "object", "properties": {"sections": {"type": "array"}}}

    resultat = llm.chat_json(
        "système", "utilisateur", provider="mlx",
        max_tokens=6144, schema=schema, retries=1,
    )

    assert resultat == {"ok": True}
    # Les noms du CONTRAT, plus ceux d'une implémentation. Le test épinglait
    # `num_predict` et `json_schema` — le vocabulaire d'Ollama — parce qu'il
    # bouchonnait ses fonctions de module. Le bouchon vise désormais l'instance
    # du registre, donc ce que la FAÇADE transmet. La façon dont chaque moteur
    # honore ces contraintes lui appartient et se vérifie chez lui.
    assert vus["max_tokens"] == 6144
    assert vus["schema"] == schema
    assert vus["retries"] == 1


def test_chat_json_porte_les_contraintes_sur_le_moteur_cloud(monkeypatch):
    """Le moteur cloud ne les JETTE pas en silence.

    Il ne sait pas contraindre son décodage par une grammaire, comme le fait
    Ollama avec `format` : il transmet donc le schéma dans la directive et
    borne sa sortie par le paramètre `max_tokens` de l'API. Un moteur qui
    accepterait ces arguments pour les ignorer serait la pire des réponses —
    le compte-rendu sortirait hors contrat sans qu'aucun signal ne l'annonce.
    """
    vus: dict = {}

    # `_completer` et non `chat` : c'est lui la porte unique vers l'API.
    # `chat` en est la projection « texte seul » — il jette le
    # `finish_reason`, dont `chat_json` a besoin pour dire au filet de
    # réparation si la sortie était COMPLÈTE.
    def faux_chat(system, user, **kwargs):
        vus.update(kwargs)
        vus["system"] = system
        return '{"ok": true}', "stop"

    monkeypatch.setattr(llm.llm_mistral, "_completer", faux_chat)
    schema = {"type": "object", "properties": {"sections": {"type": "array"}}}

    resultat = llm.chat_json(
        "système", "utilisateur", provider="mistral", api_key=CLE,
        max_tokens=6144, schema=schema, retries=1,
    )

    assert resultat == {"ok": True}
    assert vus["max_tokens"] == 6144
    assert "sections" in vus["system"], "le schéma doit atteindre le modèle"


def test_le_moteur_cloud_reessaie_vraiment(monkeypatch):
    """`retries` doit RÉESSAYER, pas seulement être accepté."""
    essais = {"n": 0}

    def bancal(system, user, **kwargs):
        essais["n"] += 1
        return ("pas du json" if essais["n"] == 1 else '{"ok": true}'), "stop"

    monkeypatch.setattr(llm.llm_mistral, "_completer", bancal)

    assert llm.chat_json(
        "s", "u", provider="mistral", api_key=CLE, retries=1
    ) == {"ok": True}
    assert essais["n"] == 2


# ---------- 3. Une seule porte, un seul type d'erreur ----------

def _modules_importes(chemin: Path) -> set[str]:
    """Modules importés, lus par AST — pas par recherche de texte.

    Une recherche de texte compterait une mention en commentaire : impossible
    alors d'expliquer le contournement retiré sans faire rougir le contrôle.
    """
    arbre = ast.parse((RACINE / chemin).read_text(encoding="utf-8"))
    noms: set[str] = set()
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.ImportFrom):
            noms.update(alias.name for alias in noeud.names)
            if noeud.module:
                noms.add(noeud.module.split(".")[-1])
        elif isinstance(noeud, ast.Import):
            noms.update(alias.name.split(".")[-1] for alias in noeud.names)
    return noms


@pytest.mark.parametrize("module", ["core/report.py", "core/rag.py", "core/profiling.py"])
def test_la_production_n_atteint_plus_un_moteur_en_direct(module):
    """Deux appelants doublaient la façade — dont un sans rien y gagner.

    Tant qu'un chemin de production nomme une implémentation, « changer de
    moteur ne toucherait qu'une classe » est faux : il faudrait aussi passer
    par ici. `core/embed.py` reste dehors — les embeddings sont un autre
    métier, avec leur propre routage.
    """
    assert "llm_ollama" not in _modules_importes(Path(module)), module


def test_le_marqueur_503_est_neutre_et_partage():
    """Un seul objet classe, sinon un `except` sur l'un raterait l'autre.

    Une ré-exportation doit être la MÊME classe, pas un alias parallèle.
    """
    from core import embed, llm_base, llm_mistral, semantic

    assert llm.LLMError is llm_base.LLMError
    for exception in (
        embed.EmbedIndisponible,
        semantic.SemanticIndexIncompatible,
        semantic.InvalidEmbedding,
        llm_mistral.MistralLLMError,
    ):
        assert issubclass(exception, llm_base.LLMError), exception


def test_le_registre_couvre_tous_les_fournisseurs_annonces():
    """Le type fermé et le registre ne doivent pas diverger.

    `FOURNISSEURS` alimente `/api/config` et le contrôle de sécurité ci-dessus.
    Un nom annoncé sans moteur derrière rendrait un 500 à l'appel, et un moteur
    non annoncé sortirait du paramétrage de la règle de non-fuite.
    """
    for fournisseur in llm.FOURNISSEURS:
        assert llm.moteur(fournisseur).name in llm.MOTEURS


def test_un_fournisseur_inconnu_reste_refuse():
    """Jamais de repli silencieux sur un moteur par défaut."""
    with pytest.raises(llm.FournisseurInconnu):
        llm.moteur("ollamaa")


# ---------------------------------------------------------------------------
# `modele_servi` — la résolution du modèle réellement employé
#
# Trois sites calculaient chacun `mistral ... sinon config.LLM_MODEL`. Le
# partage est BINAIRE : l'arrivée de MLX en troisième moteur a fait annoncer
# `qwen3.5:9b` à des documents produits par MLX — front-matter ET fingerprint.
# Et ce défaut avait DÉJÀ été corrigé une fois pour Mistral, par
# un `if/else` que le fournisseur suivant a rouvert.
#
# Les contrôles ci-dessous visent la CLASSE, pas l'occurrence : un quatrième
# moteur doit être incapable de la rouvrir en restant vert.


def _identifiants_references(chemin: Path) -> set[str]:
    """Les identifiants RÉELLEMENT référencés par le code, docstrings exclues.

    `Name.id` et `Attribute.attr` : une constante nommée dans un commentaire ou
    une docstring ne produit aucun de ces nœuds, et n'est donc pas comptée.
    """
    arbre = ast.parse(chemin.read_text(encoding="utf-8"))
    noms: set[str] = set()
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Name):
            noms.add(noeud.id)
        elif isinstance(noeud, ast.Attribute):
            noms.add(noeud.attr)
    return noms


def _modele_attendu(fournisseur: str) -> str:
    """Ce que CHAQUE fournisseur doit servir, déclaré explicitement.

    Une table écrite à la main, et c'est délibéré : un moteur ajouté au registre
    sans entrée ici fait ÉCHOUER le paramétrage. Dériver l'attendu du code
    testé rendrait ce contrôle tautologique — il passerait encore le jour où la
    résolution redeviendrait fausse.
    """
    from core import config

    table = {
        "mistral": config.MISTRAL_CHAT_MODEL or "ministral-14b-2512",
        "cloud": config.MISTRAL_CHAT_MODEL or "ministral-14b-2512",
        "mlx": config.MLX_LLM_MODEL,
        "openai": config.OPENAI_CHAT_MODEL or "gpt-5.6-luna",
        "anthropic": config.ANTHROPIC_CHAT_MODEL or "claude-haiku-5-5",
        "compatible": config.COMPATIBLE_MODEL or "serveur-compatible-non-configure",
    }
    assert fournisseur in table, (
        f"fournisseur « {fournisseur} » ajouté au registre sans déclarer le "
        "modèle qu'il sert — c'est exactement l'oubli qui a produit des "
        "documents annonçant un moteur qui n'avait pas tourné"
    )
    return table[fournisseur]


@pytest.mark.parametrize("fournisseur", _fournisseurs() + ["cloud"])
def test_chaque_fournisseur_resout_SON_modele(fournisseur):
    assert llm.modele_servi(fournisseur) == _modele_attendu(fournisseur)


@pytest.mark.parametrize("fournisseur", _fournisseurs() + ["cloud"])
def test_la_resolution_rend_toujours_une_chaine_non_vide(fournisseur):
    resolu = llm.modele_servi(fournisseur)
    assert isinstance(resolu, str) and resolu.strip()


@pytest.mark.parametrize("fournisseur", ["mistral", "cloud"])
def test_mistral_NON_CONFIGURE_rend_quand_meme_un_nom(monkeypatch, fournisseur):
    """Le contrôle précédent passe à vide sur une machine configurée.

    `MISTRAL_CHAT_MODEL` vaut `ministral-14b-2512` sur le poste de
    développement — c'est-à-dire exactement le repli. Retirer le repli n'y
    changerait donc RIEN, et le test resterait vert en ne prouvant plus rien.
    Le cas qui compte est celui d'une machine SANS Mistral configuré : il doit
    être construit, pas espéré.

    Un champ `model` vide dans un document autoritatif n'informe personne, et
    un fingerprint scellé sur une chaîne vide ne se compare à rien.
    """
    from core import llm_mistral

    monkeypatch.setattr(llm_mistral, "MISTRAL_CHAT_MODEL", "")
    assert llm.modele_servi(fournisseur) == "ministral-14b-2512"


def test_mlx_n_annonce_JAMAIS_le_modele_ollama():
    """Le défaut exact, nommé — pour qu'une régression se lise sans enquête."""
    from core import config

    assert llm.modele_servi("mlx") != config.LLM_MODEL
    assert llm.modele_servi("mlx") == config.MLX_LLM_MODEL


def test_deux_moteurs_distincts_ne_servent_pas_le_meme_modele():
    """Attrape le copier-coller : trois moteurs, trois modèles.

    Les alias sont exclus — `cloud` DOIT servir comme `mistral`, c'est sa
    définition.
    """
    servis = {f: llm.modele_servi(f) for f in llm.MOTEURS}
    assert len(set(servis.values())) == len(servis), servis


def test_mlx_ignore_un_modele_impose_plutot_que_de_mentir():
    """Un serveur `mlx_lm` sert le modèle avec lequel il a été lancé.

    `chat` REFUSE toute autre demande. Rendre ici le nom demandé décrirait donc
    une génération qui n'aura pas lieu — une provenance fausse par anticipation.
    """
    from core import config

    assert llm.modele_servi("mlx", "un/autre-modele") == config.MLX_LLM_MODEL


def test_un_fournisseur_inconnu_est_refuse_AUSSI_a_la_resolution():
    with pytest.raises(llm.FournisseurInconnu):
        llm.modele_servi("ollamaa")


# La surface du contrat, DÉCLARÉE et non dérivée. Une mutation l'a exigé :
# retirer `modele_servi` du `Protocol` ne faisait rougir personne, puisque
# `Protocol` est du typage structurel et que les moteurs gardaient leur
# méthode. Un test qui lirait la liste DANS le Protocol vérifierait alors une
# méthode de moins, sans un mot — il mesurerait le code par lui-même.
CONTRAT_LLM_ENGINE = frozenset({
    "is_available", "pret_declare", "modele_servi", "chat", "chat_json",
})


def test_le_contrat_LLMEngine_ne_perd_pas_de_methode_en_silence():
    from core import llm_base

    declarees = {
        nom for nom, valeur in vars(llm_base.LLMEngine).items()
        if callable(valeur) and not nom.startswith("_")
    }
    assert declarees == set(CONTRAT_LLM_ENGINE), (
        "la surface de LLMEngine a changé. Si c'est voulu, mettre à jour "
        "CONTRAT_LLM_ENGINE ci-dessus — et vérifier que chaque moteur suit."
    )


# ---------------------------------------------------------------------------
# `pret_declare` — la sonde qui ne démarre rien
#
# `MlxEngine.is_available()` démarre un `mlx_lm.server` et exige deux jetons :
# le bon contrat pour un appel réel, un désastre pour `/api/config`, que
# l'interface charge à chaque ouverture — 2,1 Go par page.
#
# Le contrôle central n'est donc PAS la valeur rendue. Une sonde qui rend la
# bonne réponse en démarrant un serveur serait juste et inutilisable : c'est le
# COÛT qui est le sujet, et il se vérifie en piégeant ce qui ne doit pas être
# appelé.


def _mouchards(monkeypatch, classe, methodes: tuple[str, ...]) -> list[str]:
    """Des pièges qui ENREGISTRENT au lieu de lever.

    Un piège qui lève ne prouve rien ici : `is_available()` porte un
    `except Exception`, et il AVALE l'`AssertionError` du piège avant de rendre
    `False`. Le test voyait alors un booléen bien formé, produit par le chemin
    coûteux qu'il prétendait interdire — vert sur exactement le défaut visé.

    Mesuré : c'est la seule mutation que des pièges qui lèvent laissaient
    passer, et c'était la plus importante.
    """
    appels: list[str] = []

    def mouchard_pour(nom: str):
        def mouchard(*a, **k):
            appels.append(nom)
            return None
        return mouchard

    for methode in methodes:
        monkeypatch.setattr(classe, methode, mouchard_pour(methode))
    return appels


def test_la_sonde_MLX_ne_demarre_AUCUN_serveur(monkeypatch):
    moteur_mlx = llm.MOTEURS["mlx"]
    appels = _mouchards(
        monkeypatch, type(moteur_mlx),
        ("_assurer_serveur", "_demarrer", "_completion", "_port_ouvert"),
    )

    resultat = llm.pret_declare("mlx")

    assert appels == [], (
        f"la sonde déclarée a touché la supervision du serveur : {appels} — "
        "c'est exactement le coût qu'elle existe pour éviter"
    )
    assert isinstance(resultat, bool)


def test_la_sonde_MISTRAL_lit_la_config_A_L_APPEL(monkeypatch):
    """Une clé ajoutée au `.env` sans redémarrage doit se voir.

    `llm_mistral.is_available()` lit `MISTRAL_API_KEY` importé au niveau MODULE,
    figé à l'import. `load_env()` rafraîchit `config.MISTRAL_API_KEY` après
    lecture du fichier : c'est donc `config` qu'il faut interroger, et ce test
    est ce qui distingue les deux lectures.

    Il vaut aussi comme contrôle de coût : aucun aller-retour distant n'est
    permis ici, et lire une constante n'en fait aucun.
    """
    from core import config

    monkeypatch.setattr(config, "MISTRAL_API_KEY", "")
    assert llm.pret_declare("mistral") is False

    monkeypatch.setattr(config, "MISTRAL_API_KEY", "cle-posee-apres-l-import")
    assert llm.pret_declare("mistral") is True


def test_mlx_sans_son_paquet_est_declare_NON_pret(monkeypatch):
    moteur_mlx = llm.MOTEURS["mlx"]

    def absent(self):
        from core.llm_mlx import MlxLLMError

        raise MlxLLMError("paquet absent")

    monkeypatch.setattr(type(moteur_mlx), "_verifier_paquet", absent)
    assert llm.pret_declare("mlx") is False


@pytest.mark.parametrize("nom_moteur", sorted(llm.MOTEURS))
def test_la_sonde_declaree_ne_LEVE_jamais(monkeypatch, nom_moteur):
    """Contrainte nº 2 : la route de configuration l'appelle sans `try`.

    Une exception ici rendrait 500 sur la page qui doit toujours s'afficher —
    y compris, et surtout, quand un moteur est en panne.
    """
    moteur_ = llm.MOTEURS[nom_moteur]

    def explose(*a, **k):
        raise RuntimeError("panne interne du moteur")

    for methode in ("_verifier_paquet", "_port_ouvert"):
        if hasattr(type(moteur_), methode):
            monkeypatch.setattr(type(moteur_), methode, explose)
    monkeypatch.setattr(moteur_, "is_available", explose, raising=False)

    try:
        resultat = llm.pret_declare(nom_moteur)
    except Exception as exc:  # noqa: BLE001 — c'est l'objet du test
        raise AssertionError(f"{nom_moteur} a levé {type(exc).__name__}") from exc
    assert isinstance(resultat, bool)


def test_la_disponibilite_couvre_TOUT_le_registre_et_pas_les_alias():
    """Un moteur ajouté doit apparaître sans qu'on touche à la route.

    `api/main.py` calculait `llm_ollama.is_available() or bool(MISTRAL_API_KEY)`
    : deux implémentations nommées en dur, et le troisième moteur absent de la
    disponibilité annoncée. Il pouvait être au registre et rester invisible.
    """
    declaree = llm.disponibilite_declaree()
    assert set(declaree) == set(llm.MOTEURS)
    assert "cloud" not in declaree, "un alias n'est pas un moteur"
    assert all(isinstance(valeur, bool) for valeur in declaree.values())


def test_la_route_de_config_ne_nomme_plus_de_moteur_pour_la_disponibilite():
    """Garde STATIQUE : la couche API ne doit plus câbler d'implémentation.

    Le reste du fichier nomme encore `llm_ollama` — pour `except LLMError` et
    pour le repli d'embeddings, qui relèvent des lots suivants. Ce contrôle
    vise `get_config` seule, là où le défaut vivait.
    """
    arbre = ast.parse((RACINE / "api/main.py").read_text(encoding="utf-8"))
    fonctions = [
        noeud for noeud in ast.walk(arbre)
        if isinstance(noeud, (ast.FunctionDef, ast.AsyncFunctionDef))
        and noeud.name == "get_config"
    ]
    # Le témoin de ce contrôle : si `get_config` disparaît ou est renommée, un
    # parcours de zéro fonction rendrait vert sans rien avoir regardé.
    assert len(fonctions) == 1, "get_config introuvable — le contrôle regarderait le vide"
    references = {
        noeud.id for noeud in ast.walk(fonctions[0]) if isinstance(noeud, ast.Name)
    }
    assert "llm_ollama" not in references, (
        "get_config nomme une implémentation — la disponibilité vient du "
        "registre, par llm.disponibilite_declaree()"
    )


@pytest.mark.parametrize("nom_moteur", sorted(llm.MOTEURS))
def test_chaque_moteur_du_registre_honore_TOUT_le_contrat(nom_moteur):
    """`Protocol` ne vérifie rien à l'exécution : ce test est la vérification.

    Un moteur ajouté sans `modele_servi` ne casserait qu'au moment de publier
    la provenance d'un vrai document — c'est-à-dire trop tard.
    """
    moteur_ = llm.MOTEURS[nom_moteur]
    manquantes = [
        methode for methode in sorted(CONTRAT_LLM_ENGINE)
        if not callable(getattr(moteur_, methode, None))
    ]
    assert not manquantes, f"{nom_moteur} n'honore pas {manquantes}"


@pytest.mark.parametrize(
    "module", ["core/pipeline.py", "core/report.py", "core/provenance.py"]
)
def test_aucun_site_de_production_ne_recalcule_le_modele(module):
    """LE garde-fou structurel : plus aucun partage binaire dans le code.

    Les trois sites testaient `provider in ("mistral", "cloud")` puis
    retombaient sur `config.LLM_MODEL`. Tant qu'une telle expression peut
    réapparaître, le quatrième moteur rouvrira le défaut — et les tests de
    valeurs ci-dessus resteraient verts, puisqu'ils n'interrogent que la façade.

    Le contrôle est STATIQUE, donc il tient même sur les chemins qu'aucun test
    d'intégration n'exécute.

    Il lit l'AST et non le texte. Première version en recherche de chaîne :
    elle rougissait sur `provenance.py` parce que la DOCSTRING y raconte
    l'ancien défaut. Un contrôle textuel confond le code et le récit du code,
    et aurait poussé à effacer l'explication pour faire taire le test.
    """
    references = _identifiants_references(RACINE / module)
    interdits = {"LLM_MODEL", "MISTRAL_CHAT_MODEL"} & references
    assert not interdits, (
        f"{module} résout un modèle lui-même via {sorted(interdits)} — la "
        "résolution appartient au moteur, par llm.modele_servi(). C'est ce "
        "recalcul local qui a fait annoncer le modèle Ollama à des documents "
        "produits ailleurs, deux fois."
    )


def test_le_dechargement_est_une_capacite_optionnelle(monkeypatch):
    """Un moteur cloud ne tient aucune mémoire : ne rien faire est la réponse.

    L'absence de capacité ne doit pas lever — `pipeline.py` appelle ceci en
    fin de traitement, quand le transcript est déjà écrit.
    """
    appels: list[str] = []
    monkeypatch.setattr(llm.MOTEURS["mlx"], "unload", lambda *a, **k: appels.append("x") or True)

    assert llm.unload(provider="mlx") is True
    assert appels == ["x"]
    assert llm.unload(provider="mistral") is False
    assert appels == ["x"]
    # Même un fournisseur invalide ne lève pas ICI, à l'inverse de `chat` : le
    # déchargement arrive après l'écriture du transcript, et ne doit pas faire
    # échouer un traitement qui a réussi.
    assert llm.unload(provider="ollamaa") is False


def test_un_moteur_dont_le_dechargement_LEVE_ne_casse_pas_ce_qui_a_reussi(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le code contredisait sa propre docstring.

    `llm.unload` annonce que `False` couvre « tentative échouée » et qu'il « ne
    doit jamais faire échouer ce qui a réussi ». Or il appelait le déchargement
    du moteur sans rien rattraper.

    Latent tant qu'Ollama est le seul moteur à en avoir un — il absorbe ses
    propres pannes. Mais l'interface promet qu'un moteur se branche par une
    classe et une entrée de registre, et rien dans le protocole n'impose à son
    `unload()` de ne pas lever.

    Le chemin d'impact est réel : `core/pipeline.py:362` appelle `unload` APRÈS
    l'écriture du transcript et AVANT `report(1.0)` et le `return`. Un moteur
    dont le déchargement lève transformerait un traitement réussi en échec
    final — après que l'utilisateur a déjà son fichier.

    La garde vit à la FAÇADE, pas dans chaque moteur : une propriété qu'il faut
    réimplémenter à chaque implémentation est une propriété qu'on oubliera à la
    suivante.
    """
    class MoteurQuiLeveEnDechargeant:
        def is_available(self) -> bool:
            return True

        def unload(self) -> bool:
            raise RuntimeError("le processus du moteur ne répond plus")

    monkeypatch.setitem(llm.MOTEURS, "mlx", MoteurQuiLeveEnDechargeant())

    assert llm.unload(provider="mlx") is False


def test_une_INTERRUPTION_pendant_le_déchargement_traverse(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """La garde absorbe la panne d'un moteur, pas la décision de s'arrêter.

    Sans cette distinction, un Ctrl-C pendant le déchargement serait avalé et
    le pipeline continuerait comme si de rien n'était. Écrit après avoir muté
    `except Exception` en `except BaseException` et constaté que RIEN ne
    rougissait : le commentaire affirmait la propriété, aucun test ne la
    tenait.
    """
    class MoteurInterrompu:
        def is_available(self) -> bool:
            return True

        def unload(self) -> bool:
            raise KeyboardInterrupt

    monkeypatch.setitem(llm.MOTEURS, "mlx", MoteurInterrompu())

    with pytest.raises(KeyboardInterrupt):
        llm.unload(provider="mlx")
