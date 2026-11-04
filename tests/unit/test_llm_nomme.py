"""Les fournisseurs en ligne nommés : OpenRouter et Groq (chantier 006, tranche B).

Deux préréglages d'un moteur partagé, paramétrés par un profil. Aucun appel réel : le client est construit par l'attribut de
module `llm_nomme.client_sans_fuite`, que ces tests remplacent par un client à `httpx.MockTransport` (aucun socket ; la garde
réseau de la suite le vérifie).

Le module est importé PAR LES TESTS (fixture `nomme`) et non en tête de fichier : avant son existence, chaque test échoue
pour la même cause (le fournisseur est absent), au lieu d'une erreur de collecte unique.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path

import httpx
import openai
import pytest

from core import config, llm

pytestmark = pytest.mark.unit

NOMS = ["openrouter", "groq"]
CLE = "cle-fictive-de-test-0123456789"
CLE_UTILISATEUR = "cle-saisie-a-l-ecran-9876543210"
MODELE = "auteur/un-modele-de-test"


@pytest.fixture
def nomme():
    return importlib.import_module("core.llm_nomme")


@pytest.fixture(autouse=True)
def environnement_propre(monkeypatch):
    """Aucune clé ni aucun modèle hérités de l'environnement de la personne qui lance les tests."""
    for nom in NOMS:
        for variable in (f"{nom.upper()}_API_KEY", f"{nom.upper()}_MODEL", f"MEMOGLYPH_{nom.upper()}_MODEL"):
            monkeypatch.setenv(variable, "x")        # enregistre l'état d'origine, rendu après le test
            monkeypatch.delenv(variable)


class Transport:
    """Un client simulé : enregistre les requêtes, répond avec le contenu demandé."""

    def __init__(self, contenu: str = "réponse de test", statut: int = 200, corps: dict | None = None) -> None:
        self.requetes: list[httpx.Request] = []
        self.contenu, self.statut, self.corps = contenu, statut, corps
        self.exception: Exception | None = None         # levée par le transport (délai, connexion)
        self.reponse_ok: dict | None = None             # corps d'une réponse 200 hors du cas ordinaire
        self.entetes_reponse: dict = {}                 # en-têtes de la réponse d'erreur (`Retry-After`…)

    def fabrique(self, base_url: str, *, api_key: str) -> openai.OpenAI:
        def traiter(requete: httpx.Request) -> httpx.Response:
            self.requetes.append(requete)
            if self.exception is not None:
                raise self.exception
            if self.reponse_ok is not None:
                return httpx.Response(200, json=self.reponse_ok)
            if self.statut != 200:
                return httpx.Response(self.statut, headers=self.entetes_reponse,
                                      json=self.corps or {"error": {"message": "corps-brut-du-service"}})
            return httpx.Response(200, json={"choices": [{"message": {"content": self.contenu}, "finish_reason": "stop"}]})
        return openai.OpenAI(base_url=base_url, api_key=api_key, max_retries=0,
                             http_client=httpx.Client(transport=httpx.MockTransport(traiter), trust_env=False))


@pytest.fixture
def transport(monkeypatch, nomme):
    t = Transport()
    monkeypatch.setattr(nomme, "client_sans_fuite", t.fabrique)
    return t


def _pret(monkeypatch, nom: str, nomme) -> None:
    profil = nomme.PROFILS[nom]
    monkeypatch.setenv(profil.variable_cle, CLE)
    monkeypatch.setenv(f"MEMOGLYPH_{profil.variable_modele}", MODELE)


# ---------- T020 : l'identité « en ligne » ----------

@pytest.mark.parametrize("nom", NOMS)
def test_ancres_le_fournisseur_est_au_registre_et_dans_la_disponibilite(nom, nomme) -> None:
    """Sans ces ancres, « jamais local » serait vrai trivialement tant que le nom n'existe pas (`est_local` rend déjà faux
    pour tout nom hors `mlx` et `compatible`) : le ROUGE de ce fichier vient de l'ABSENCE du fournisseur ; la PROPRIÉTÉ
    d'identité est prouvée par les mutants (`auto_heberge` vrai, `host` défini, `est_local` qui lit l'adresse)."""
    assert nom in llm.FOURNISSEURS and nom in llm.MOTEURS
    assert llm.moteur(nom).name == nom
    assert nom in llm.disponibilite_declaree()


@pytest.mark.parametrize("nom", NOMS)
def test_une_adresse_locale_injectee_ne_rend_jamais_un_fournisseur_nomme_local(nom, nomme, monkeypatch) -> None:
    profil = nomme.avec_adresse(nomme.PROFILS[nom], "http://127.0.0.1:9/v1")      # réservé aux tests
    monkeypatch.setitem(llm.MOTEURS, nom, nomme.MoteurNomme(profil))
    assert llm.est_local(nom) is False
    assert llm.auto_heberge(nom) is False
    assert not hasattr(llm.MOTEURS[nom], "host"), "un hôte porté par le moteur ferait décider l'adresse"
    assert llm.profil_prudent(nom) is True, "capacité du modèle inconnue (décision à confirmer) ; sans rapport avec le local"


def test_ancre_le_compatible_avec_une_adresse_locale_est_bien_local(monkeypatch) -> None:
    """L'ancre du test précédent : le même cas, sur le moteur qui se décide PAR L'ADRESSE, est bien local."""
    monkeypatch.setattr(config, "COMPATIBLE_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setattr(config, "COMPATIBLE_MODEL", "m")
    assert llm.est_local("compatible") is True


@pytest.mark.parametrize("nom", NOMS)
def test_le_moteur_nomme_ne_herite_d_aucun_attribut_du_compatible(nom, nomme) -> None:
    from core import llm_compatible
    moteur = llm.MOTEURS[nom]
    assert not isinstance(moteur, llm_compatible.CompatibleEngine)
    assert moteur.auto_heberge is False and moteur.propose_si_pret is True


# ---------- T021 : prêt, clé, modèle ----------

@pytest.mark.parametrize("nom", NOMS)
@pytest.mark.parametrize("cle,modele,attendu", [
    (False, False, False), (True, False, False), (False, True, False), (True, True, True)])
def test_pret_seulement_avec_une_cle_et_un_modele(nom, cle, modele, attendu, nomme, monkeypatch) -> None:
    profil = nomme.PROFILS[nom]
    if cle:
        monkeypatch.setenv(profil.variable_cle, CLE)
    if modele:
        monkeypatch.setenv(f"MEMOGLYPH_{profil.variable_modele}", MODELE)
    assert llm.pret_declare(nom) is attendu
    assert llm.propose(nom) is attendu
    assert llm.disponibilite_declaree()[nom] is attendu


@pytest.mark.parametrize("nom", NOMS)
def test_la_cle_et_le_modele_sont_relus_a_chaque_appel(nom, nomme, monkeypatch) -> None:
    profil = nomme.PROFILS[nom]
    assert llm.pret_declare(nom) is False
    _pret(monkeypatch, nom, nomme)
    assert llm.pret_declare(nom) is True                                     # posée sans redémarrage
    assert llm.modele_servi(nom) == MODELE
    monkeypatch.setenv(f"MEMOGLYPH_{profil.variable_modele}", "autre/modele")
    assert llm.modele_servi(nom) == "autre/modele"                           # modèle changé : relu
    monkeypatch.delenv(profil.variable_cle)
    assert llm.pret_declare(nom) is False                                    # clé retirée : plus prêt


@pytest.mark.parametrize("nom", NOMS)
def test_le_modele_vient_de_la_configuration_sans_aucune_liste_figee(nom, nomme) -> None:
    assert llm.modele_servi(nom) == nomme.modele_non_configure(nom) == f"{nom}-non-configure"      # aucun défaut, un nom par profil
    # Aucune constante de la forme « auteur/modèle » dans le module : ni identifiant isolé, ni collection. `openrouter/free`,
    # le routeur qui ne retombe que sur des modèles gratuits (research.md R1 O4), est le seul identifiant que le code peut nommer
    # (il entre avec la règle des gratuits).
    import ast
    import re
    forme = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.:-]+$")
    arbre = ast.parse(Path(nomme.__file__).read_text(encoding="utf-8"))
    chaines = [n.value for n in ast.walk(arbre) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert "https://openrouter.ai/api/v1" in chaines, "ancre : le balayage voit bien les constantes du module"
    figes = [c for c in chaines if forme.match(c) and c != "openrouter/free"]
    assert figes == [], f"identifiants de modèle figés dans le code : {figes}"


@pytest.mark.parametrize("nom", NOMS)
def test_load_env_pose_une_cle_ajoutee_au_env_isole(nom, nomme, monkeypatch, tmp_path) -> None:
    """CONTRAT RÉEL de la relecture (même que pour OpenAI et Mistral) : `load_env` ajoute au processus ce que le `.env`
    porte et n'y est pas déjà ; la lecture du moteur est ensuite VIVANTE. Uniquement l'`.env` ISOLÉ des tests."""
    profil = nomme.PROFILS[nom]
    fichier = tmp_path / ".env"
    fichier.write_text(f"{profil.variable_cle}={CLE}\nMEMOGLYPH_{profil.variable_modele}={MODELE}\n", encoding="utf-8")
    monkeypatch.setenv("MEMOGLYPH_ENV_FILE", str(fichier))
    assert llm.pret_declare(nom) is False
    config.load_env()
    assert llm.pret_declare(nom) is True


@pytest.mark.parametrize("nom", NOMS)
def test_limite_connue_une_cle_deja_exportee_n_est_pas_remplacee_par_load_env(nom, nomme, monkeypatch, tmp_path) -> None:
    """LIMITE du chargeur, constatée et NON étendue ici : une variable déjà posée l'emporte, donc un `.env` modifié ou
    vidé après coup ne change rien sans redémarrage (la clé de l'environnement reste la clé lue)."""
    profil = nomme.PROFILS[nom]
    fichier = tmp_path / ".env"
    fichier.write_text(f"{profil.variable_cle}=cle-du-fichier\n", encoding="utf-8")
    monkeypatch.setenv("MEMOGLYPH_ENV_FILE", str(fichier))
    monkeypatch.setenv(profil.variable_cle, CLE)
    config.load_env()
    assert os.environ[profil.variable_cle] == CLE


# ---------- le moteur : requêtes, clés, client ----------

@pytest.mark.parametrize("nom,adresse", [("openrouter", "https://openrouter.ai/api/v1"),
                                         ("groq", "https://api.groq.com/openai/v1")])
def test_la_requete_va_a_l_adresse_fixe_du_profil_avec_la_cle_et_le_modele(nom, adresse, nomme, monkeypatch, transport) -> None:
    _pret(monkeypatch, nom, nomme)
    assert llm.chat("système", "utilisateur", provider=nom, envoi=None) == "réponse de test"
    requete = transport.requetes[0]
    assert str(requete.url) == f"{adresse}/chat/completions"
    assert requete.headers["authorization"] == f"Bearer {CLE}"
    assert f'"model":"{MODELE}"' in requete.content.decode().replace(" ", "")


@pytest.mark.parametrize("nom", NOMS)
def test_une_cle_par_requete_n_est_jamais_envoyee(nom, nomme, monkeypatch, transport) -> None:
    """Décision : clés seulement dans `.env`. La façade transmet `api_key=` (clé Mistral saisie à l'écran) : elle ne part pas."""
    _pret(monkeypatch, nom, nomme)
    llm.chat("s", "u", provider=nom, api_key=CLE_UTILISATEUR, envoi=None)
    vu = transport.requetes[0]
    assert vu.headers["authorization"] == f"Bearer {CLE}"
    assert CLE_UTILISATEUR not in str(vu.headers) and CLE_UTILISATEUR not in vu.content.decode()


@pytest.mark.parametrize("nom", NOMS)
def test_chat_json_rend_la_table_du_modele(nom, nomme, monkeypatch, transport) -> None:
    _pret(monkeypatch, nom, nomme)
    transport.contenu = '{"a": 1}'
    assert llm.chat_json("s", "u", provider=nom, envoi=None) == {"a": 1}
    assert "JSON" in transport.requetes[0].content.decode()


@pytest.mark.parametrize("nom", NOMS)
def test_une_panne_ordinaire_est_une_LLMError_sans_la_cle_ni_le_corps(nom, nomme, monkeypatch, transport) -> None:
    from core.llm_base import LLMError
    _pret(monkeypatch, nom, nomme)
    transport.statut = 500
    with pytest.raises(LLMError) as erreur:
        llm.chat("s", "u", provider=nom, envoi=None)
    texte = str(erreur.value)
    assert CLE not in texte and "corps-brut-du-service" not in texte


@pytest.mark.parametrize("nom", NOMS)
def test_le_client_n_est_pas_garde_d_un_test_a_l_autre(nom, nomme, monkeypatch) -> None:
    """Les instances du registre vivent toute la session : un client à transport simulé d'un test ne doit pas fuir
    dans le suivant, ni contourner le point d'injection posé après sa création."""
    _pret(monkeypatch, nom, nomme)
    premier, second = Transport("un"), Transport("deux")
    monkeypatch.setattr(nomme, "client_sans_fuite", premier.fabrique)
    assert llm.chat("s", "u", provider=nom, envoi=None) == "un"
    monkeypatch.setattr(nomme, "client_sans_fuite", second.fabrique)
    assert llm.chat("s", "u", provider=nom, envoi=None) == "deux"
    assert len(premier.requetes) == 1 and len(second.requetes) == 1
    monkeypatch.setenv(nomme.PROFILS[nom].variable_cle, "autre-cle-de-test")      # clé changée : client reconstruit
    llm.chat("s", "u", provider=nom, envoi=None)
    assert second.requetes[-1].headers["authorization"] == "Bearer autre-cle-de-test"


def test_les_deux_profils_sont_deux_instances_sans_etat_de_classe(nomme, monkeypatch) -> None:
    assert llm.MOTEURS["openrouter"] is not llm.MOTEURS["groq"]
    assert nomme.PROFILS["openrouter"].variable_cle != nomme.PROFILS["groq"].variable_cle
    assert nomme.PROFILS["openrouter"].nom != nomme.PROFILS["groq"].nom
    a, b = Transport("a"), Transport("b")
    monkeypatch.setattr(nomme, "client_sans_fuite", lambda base, *, api_key: (a if "openrouter" in base else b).fabrique(base, api_key=api_key))
    for nom in NOMS:
        _pret(monkeypatch, nom, nomme)
    llm.chat("s", "u", provider="openrouter", envoi=None)
    assert len(a.requetes) == 1 and len(b.requetes) == 0
    llm.chat("s", "u", provider="groq", envoi=None)             # le second profil n'hérite pas du client du premier
    assert len(a.requetes) == 1 and len(b.requetes) == 1


@pytest.mark.parametrize("nom", NOMS)
def test_une_cle_saisie_a_l_ecran_ne_rend_pas_le_fournisseur_disponible(nom, nomme) -> None:
    """Sans clé dans l'environnement, la clé relayée par la façade (`api_key=`) ne rend rien disponible."""
    assert llm.is_available(nom, api_key=CLE_UTILISATEUR) is False


def test_l_adresse_n_est_configurable_par_aucune_variable(nomme) -> None:
    """Adresse FIXE : le module ne lit AUCUNE variable d'environnement nommée en littéral (seules celles du profil, qui sont des
    attributs). Un réglage d'adresse ferait mentir le libellé « OpenRouter »."""
    import ast
    arbre = ast.parse(Path(nomme.__file__).read_text(encoding="utf-8"))
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Call):
            nom_appel = ast.unparse(noeud.func)
            if nom_appel in ("os.getenv", "config.getenv_compat", "os.environ.get", "getenv", "getenv_compat"):
                assert not (noeud.args and isinstance(noeud.args[0], ast.Constant)), (
                    f"variable d'environnement lue en littéral : {ast.unparse(noeud)} (l'adresse ne se règle pas)")
        if isinstance(noeud, ast.Subscript) and ast.unparse(noeud.value) == "os.environ":
            raise AssertionError(f"lecture directe de os.environ : {ast.unparse(noeud)}")


@pytest.mark.parametrize("nom", NOMS)
@pytest.mark.parametrize("exception,mot", [
    (httpx.ReadTimeout("délai"), "délai"),
    (httpx.ConnectError("injoignable"), "injoignable"),
], ids=["delai", "connexion"])
def test_un_delai_ou_une_connexion_perdue_est_une_LLMError_lisible(nom, exception, mot, nomme, monkeypatch, transport) -> None:
    from core.llm_base import LLMError
    _pret(monkeypatch, nom, nomme)
    transport.exception = exception
    with pytest.raises(LLMError, match=mot):
        llm.chat("s", "u", provider=nom, envoi=None)


@pytest.mark.parametrize("nom", NOMS)
def test_une_reponse_sans_contenu_textuel_est_une_LLMError(nom, nomme, monkeypatch, transport) -> None:
    from core.llm_base import LLMError
    _pret(monkeypatch, nom, nomme)
    transport.reponse_ok = {"choices": [{"message": {"content": None}, "finish_reason": "stop"}]}
    with pytest.raises(LLMError, match="sans contenu"):
        llm.chat("s", "u", provider=nom, envoi=None)


@pytest.mark.parametrize("nom", NOMS)
def test_un_json_qui_n_est_pas_une_table_est_refuse(nom, nomme, monkeypatch, transport) -> None:
    from core.llm_base import LLMError
    _pret(monkeypatch, nom, nomme)
    transport.contenu = "[1, 2, 3]"
    with pytest.raises(LLMError, match="pas une table"):
        llm.chat_json("s", "u", provider=nom, envoi=None)


@pytest.mark.parametrize("nom", NOMS)
def test_un_json_invalide_apres_les_reprises_leve_la_sortie_inexploitable(nom, nomme, monkeypatch, transport) -> None:
    from core.llm_base import LLMMalformedOutputError
    _pret(monkeypatch, nom, nomme)
    transport.contenu = "ceci n'est pas du JSON"
    with pytest.raises(LLMMalformedOutputError):
        llm.chat_json("s", "u", provider=nom, retries=2, envoi=None)
    assert len(transport.requetes) == 3                                       # un essai, puis deux reprises



# ---------- T041 : le 429 (quota atteint) ----------

PHRASE_QUOTA = "Quota atteint"


@pytest.mark.parametrize("nom,libelle", [("openrouter", "OpenRouter"), ("groq", "Groq")])
@pytest.mark.parametrize("entetes", [{}, {"Retry-After": "37"}], ids=["sans_retry_after", "avec_retry_after"])
def test_un_429_est_une_LimiteDeDemandes_dite_sans_le_corps_ni_la_cle(nom, libelle, entetes, nomme, monkeypatch, transport) -> None:
    _pret(monkeypatch, nom, nomme)
    transport.statut, transport.entetes_reponse = 429, entetes
    with pytest.raises(nomme.LimiteDeDemandes) as exc:
        llm.chat("s", "u", provider=nom, envoi=None)
    texte = str(exc.value)
    assert isinstance(exc.value, nomme.NommeLLMError) and isinstance(exc.value, llm.LLMError)
    assert texte.startswith(f"{PHRASE_QUOTA} chez {libelle}")
    assert "attendez" in texte and "autre modèle" in texte
    assert CLE not in texte and "corps-brut-du-service" not in texte
    assert "37" not in texte, "aucune durée d'attente promise : l'en-tête est facultatif et le service seul sait"
    assert len(transport.requetes) == 1, "pas de nouvel essai : la panne est dite, le quota ne se contourne pas"


@pytest.mark.parametrize("nom", NOMS)
def test_le_429_garde_son_type_a_travers_la_facade_et_porte_son_code(nom, nomme, monkeypatch, transport) -> None:
    """`llm._normalise` laisse passer une `LLMError` telle quelle ; le code vient du NOM de classe (`core/erreurs.py`)."""
    from core import erreurs
    _pret(monkeypatch, nom, nomme)
    transport.statut = 429
    with pytest.raises(nomme.LimiteDeDemandes) as exc:
        llm.chat_json("s", "u", provider=nom, envoi=None)
    assert erreurs.code_de(exc.value) == ("step.error.rateLimited", {})


@pytest.mark.parametrize("statut", [400, 401, 402, 403, 404, 500, 503])
def test_ancre_les_autres_statuts_restent_une_panne_ordinaire(statut, nomme, monkeypatch, transport) -> None:
    """L'ancre du 429 : un autre statut n'est PAS une limite de demandes (il garde sa phrase « a répondu N »)."""
    _pret(monkeypatch, "groq", nomme)
    transport.statut = statut
    with pytest.raises(nomme.NommeLLMError, match=str(statut)) as exc:
        llm.chat("s", "u", provider="groq", envoi=None)
    assert type(exc.value).__name__ != "LimiteDeDemandes"      # par le nom : l'ancre est verte avant même que la classe existe


# ---------- T044 : le raisonnement, la coupure, le vide ----------

def _reponse(contenu, motif="stop", **message):
    return {"choices": [{"message": {"content": contenu, **message}, "finish_reason": motif}]}


@pytest.mark.parametrize("nom", NOMS)
@pytest.mark.parametrize("brut,attendu", [
    ("<think>je réfléchis à voix haute</think>La réponse.", "La réponse."),
    ("<think>a</think>\n\n<think>b</think>Texte final", "Texte final"),
    ("<THINK>majuscules</THINK>Texte", "Texte"),
    ("Début <think>au milieu</think> fin", "Début  fin"),
    ("</think>Texte après une balise fermante seule", "Texte après une balise fermante seule"),
    ("Texte sans raisonnement", "Texte sans raisonnement"),
], ids=["simple", "deux_blocs", "casse", "milieu", "fermante_seule", "sans"])
def test_le_raisonnement_dans_le_contenu_est_retire_du_texte(nom, brut, attendu, nomme, monkeypatch, transport) -> None:
    _pret(monkeypatch, nom, nomme)
    transport.reponse_ok = _reponse(brut)
    assert llm.chat("s", "u", provider=nom, envoi=None) == attendu.strip()


@pytest.mark.parametrize("nom", NOMS)
def test_un_raisonnement_jamais_ferme_n_est_jamais_rendu_comme_reponse(nom, nomme, monkeypatch, transport) -> None:
    """Un bloc ouvert puis coupé (limite de jetons) ne contient QUE du raisonnement : il n'y a pas de réponse à rendre."""
    from core.llm_base import LLMError
    _pret(monkeypatch, nom, nomme)
    transport.reponse_ok = _reponse("<think>je réfléchis et je ne conclus jamais", motif="length")
    with pytest.raises(LLMError):
        llm.chat("s", "u", provider=nom, envoi=None)


@pytest.mark.parametrize("nom", NOMS)
def test_un_bloc_ouvert_jamais_ferme_est_retire_meme_sans_motif_de_coupure(nom, nomme, monkeypatch, transport) -> None:
    """Le motif d'arrêt peut mentir ou manquer : le retrait du bloc ouvert ne dépend pas de `length`."""
    from core.llm_base import LLMError
    _pret(monkeypatch, nom, nomme)
    transport.reponse_ok = _reponse("Réponse utile.<think>suite de raisonnement jamais fermée", motif="stop")
    assert llm.chat("s", "u", provider=nom, envoi=None) == "Réponse utile."
    transport.reponse_ok = _reponse("<think>seulement du raisonnement, jamais fermé", motif="stop")
    with pytest.raises(LLMError, match="vide|aucun texte"):
        llm.chat("s", "u", provider=nom, envoi=None)


@pytest.mark.parametrize("nom", NOMS)
def test_le_json_apres_un_raisonnement_est_lu(nom, nomme, monkeypatch, transport) -> None:
    _pret(monkeypatch, nom, nomme)
    transport.reponse_ok = _reponse('<think>je prépare {"faux": 1}</think>```json\n{"vrai": 2}\n```')
    assert llm.chat_json("s", "u", provider=nom, envoi=None) == {"vrai": 2}


@pytest.mark.parametrize("nom", NOMS)
def test_un_champ_de_raisonnement_separe_est_ignore(nom, nomme, monkeypatch, transport) -> None:
    """OpenRouter met le raisonnement dans un champ à part (`reasoning`) : il n'entre jamais dans le texte rendu."""
    _pret(monkeypatch, nom, nomme)
    transport.reponse_ok = _reponse("La réponse.", reasoning="mon raisonnement secret", reasoning_content="autre")
    texte = llm.chat("s", "u", provider=nom, envoi=None)
    assert texte == "La réponse." and "secret" not in texte


@pytest.mark.parametrize("nom", NOMS)
@pytest.mark.parametrize("contenu", ["", "   \n ", "<think>seulement ceci</think>"], ids=["vide", "blancs", "seulement_raisonnement"])
@pytest.mark.parametrize("motif", ["stop", "length"])
def test_un_contenu_vide_est_une_panne_dite_et_non_un_texte_vide(nom, contenu, motif, nomme, monkeypatch, transport) -> None:
    """Un 200 sans texte (ou dont tout le texte est du raisonnement) ne doit pas passer pour un compte rendu vide réussi."""
    from core.llm_base import LLMError
    _pret(monkeypatch, nom, nomme)
    transport.reponse_ok = _reponse(contenu, motif=motif)
    with pytest.raises(LLMError, match="vide|sans réponse|aucun texte"):
        llm.chat("s", "u", provider=nom, envoi=None)


@pytest.mark.parametrize("nom", NOMS)
def test_une_reponse_coupee_par_la_limite_de_jetons_est_une_panne_dite(nom, nomme, monkeypatch, transport) -> None:
    """Comme pour le « compatible » : un JSON coupé n'est pas réessayé comme s'il était simplement mal formé."""
    from core.llm_base import LLMError
    _pret(monkeypatch, nom, nomme)
    transport.reponse_ok = _reponse('{"a": [1, 2', motif="length")
    with pytest.raises(LLMError, match="limite de jetons|coupée"):
        llm.chat_json("s", "u", provider=nom, max_tokens=10, retries=2, envoi=None)
    assert len(transport.requetes) == 1


# ---------- T045 : les en-têtes ----------

@pytest.mark.parametrize("nom", NOMS)
def test_aucun_en_tete_d_attribution_n_est_envoye_et_la_cle_n_est_que_dans_authorization(nom, nomme, monkeypatch, transport) -> None:
    """Rien n'identifie l'instance : ni `HTTP-Referer`, ni `X-Title`, ni `X-OpenRouter-*`, ni organisation ni projet. Les
    en-têtes `x-stainless-*` du SDK sont GARDÉS (décision prise pour ce chantier) ; la clé n'apparaît que dans `authorization`."""
    _pret(monkeypatch, nom, nomme)
    llm.chat("s", "u", provider=nom, envoi=None)
    requete = transport.requetes[0]
    noms = {k.lower() for k in requete.headers.keys()}
    assert "authorization" in noms and any(n.startswith("x-stainless-") for n in noms), "ancres : la capture est bien celle d'une requête du SDK"
    interdits = {n for n in noms if n in {"http-referer", "referer", "x-title", "openai-organization", "openai-project"}
                 or n.startswith("x-openrouter") or n.startswith("x-groq")}
    assert interdits == set()
    for cle, valeur in requete.headers.items():
        if cle.lower() != "authorization":
            assert CLE not in valeur, f"la clé ne doit être que dans `authorization` (vue dans {cle})"
    assert CLE not in requete.url.path and CLE not in str(requete.url.query)
    assert CLE not in requete.content.decode()
