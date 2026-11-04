"""Moteur « fournisseur en ligne nommé » : OpenRouter et Groq (chantier 006).

Deux préréglages d'un même moteur, paramétrés par un PROFIL (nom, libellé, variable de clé, variable de modèle, adresse). Ils
parlent le protocole OpenAI à un service EN LIGNE connu sous son nom : chacun apparaît à l'écran sous son vrai nom, plusieurs
peuvent être prêts à la fois, et le fournisseur « compatible » (un serveur que l'utilisateur héberge) reste tel quel.

L'IDENTITÉ dit « en ligne », jamais l'adresse (principe X de la constitution) :
- `auto_heberge = False`, défini ICI et non hérité : un moteur qui l'aurait vrai s'afficherait « Private cloud » ;
- aucune propriété `host` : `llm.est_local` ne regarde l'hôte que pour `mlx` et `compatible`, jamais pour un nom d'ici, et un
  hôte porté par le moteur ferait décider l'adresse ;
- l'adresse est FIXE, dans le profil : aucun réglage ne la change (seuls les tests en injectent une, `avec_adresse`).

La clé et le modèle sont relus À CHAQUE APPEL (`os.getenv`, `config.getenv_compat`) : pas de constante lue à l'import, donc rien
de périmé. Ils viennent du seul `.env` : une clé passée par requête (`api_key=`, la clé Mistral saisie à l'écran que la façade
relaie) n'est JAMAIS envoyée. Aucune liste de modèles dans le code : le modèle se choisit dans la configuration, et sans lui le
fournisseur n'est pas proposé.

Le schéma JSON voyage dans la DIRECTIVE, comme pour le « compatible » : le support de `response_format` dépend du modèle et du
fournisseur sous-jacent (documentation de chaque service). Ce qui est garanti est ce que l'on VÉRIFIE au retour.

La clé n'apparaît dans aucun message : les erreurs du SDK sont résumées par leur type et leur statut HTTP, jamais par leur corps.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field, replace
from typing import Any, Optional

import openai

from . import attente_quota, config, telemetry
from .llm_base import LLMError, LLMMalformedOutputError, retirer_enveloppe_markdown
from .llm_client_openai import client_sans_fuite

# Délai d'une génération, en secondes : un compte rendu en ligne peut être long.
DELAI_S = 600.0


def modele_non_configure(nom: str) -> str:
    """Le nom rendu tant qu'aucun modèle n'est configuré : `modele_servi` rend toujours un nom, et celui-ci ne passe jamais pour
    un vrai modèle (le fournisseur n'est alors ni proposé ni appelable). UN PAR PROFIL : deux moteurs ne servent jamais le
    même modèle, et `/api/config` ne doit pas annoncer le même nom pour deux fournisseurs."""
    return f"{nom}-non-configure"


class NommeLLMError(LLMError):
    """Panne d'un fournisseur nommé : non configuré, injoignable, délai dépassé, réponse hors contrat."""


class LimiteDeDemandes(NommeLLMError):
    """Le service a répondu 429 : le quota (de la clé, du modèle ou du palier gratuit) est atteint.

    Une panne COMME LES AUTRES pour la chaîne (la tranche est perdue, la dégradation est la même) ; son nom de classe lui donne
    son propre code de message (`core/erreurs.py`). Aucun délai n'est promis : l'en-tête `Retry-After` est facultatif."""


class _SortieInexploitable(NommeLLMError, LLMMalformedOutputError):
    """Le modèle a répondu, mais pas du JSON exploitable après tous les essais."""


@dataclass(frozen=True)
class ProfilFournisseur:
    """Ce qui distingue un service d'un autre. Aucun tarif, aucune liste de modèles, aucune clé."""

    nom: str                  # le nom du registre : ce que `llm.est_local` voit ; jamais dérivé de l'adresse
    libelle: str              # pour les messages du serveur ; les libellés d'écran vivent dans l'interface (fr et en)
    variable_cle: str         # relue à chaque appel
    variable_modele: str      # suffixe lu par `config.getenv_compat` (préfixe `MEMOGLYPH_`)
    adresse: str              # FIXE (profil, code) : valeurs lues sur la documentation officielle (research.md R1 O1, R2 G1)
    # Capacité du modèle choisi inconnue : profil d'exécution prudent, comme le « compatible ». Sans rapport avec « local ».
    profil_prudent: bool = True
    # Ce que LE SERVICE marque comme gratuit (jamais le prix, jamais l'adresse). Vide : aucun marqueur par modèle (Groq : la
    # gratuité est un palier de compte, research.md D4).
    suffixe_gratuit: str = ""
    gratuits_fixes: tuple[str, ...] = ()
    # Champs ajoutés au CORPS de chaque requête de ce service (`extra_body` du SDK). Vide : rien.
    parametres_requete: dict = field(default_factory=dict)


PROFILS: dict[str, ProfilFournisseur] = {
    "openrouter": ProfilFournisseur(
        nom="openrouter", libelle="OpenRouter", variable_cle="OPENROUTER_API_KEY",
        variable_modele="OPENROUTER_MODEL", adresse="https://openrouter.ai/api/v1",
        # Le suffixe `:free` et le routeur `openrouter/free` : documentation officielle (research.md R1 O3).
        suffixe_gratuit=":free", gratuits_fixes=("openrouter/free",),
        # Un modèle de raisonnement peut dépenser TOUT le budget de sortie à raisonner et ne rien écrire (`finish_reason: "length"`,
        # contenu vide). À l'essai réel du 4/11, 5 appels d'extraction sur 6 ont usé tout le plafond de sortie (usage.db) : la cause
        # (le raisonnement) est une INFÉRENCE, le modèle qui répondait n'étant pas enregistré. Documenté (https://openrouter.ai/docs/guides/best-practices/
        # reasoning-tokens, lu le 2026-11-04, page non datée) : `reasoning.effort` alloue une PART de la limite de sortie (« low » :
        # environ 20 %) pour les modèles qui le gèrent. `reasoning.exclude` ne ferait que CACHER le raisonnement (il compte toujours
        # dans le budget) : non envoyé. Effet sur les autres modèles du routeur : la page ne le dit pas.
        parametres_requete={"reasoning": {"effort": "low"}}),
    "groq": ProfilFournisseur(
        nom="groq", libelle="Groq", variable_cle="GROQ_API_KEY",
        variable_modele="GROQ_MODEL", adresse="https://api.groq.com/openai/v1"),
}


def est_gratuit(profil: ProfilFournisseur, modele: str) -> bool:
    """Ce modèle est-il marqué gratuit PAR LE SERVICE ? Casse et espaces ignorés : se tromper vers « gratuit » refuse plus."""
    m = (modele or "").strip().lower()
    if not m:
        return False
    return (bool(profil.suffixe_gratuit) and m.endswith(profil.suffixe_gratuit)) or m in profil.gratuits_fixes


_BLOC_RAISONNEMENT = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_OUVERTURE_SEULE = re.compile(r"<think>.*\Z", re.IGNORECASE | re.DOTALL)
_FERMETURE = re.compile(r"</think>", re.IGNORECASE)


def retirer_raisonnement(texte: str) -> str:
    """Le texte sans le raisonnement que certains modèles écrivent DANS le contenu (`<think>…</think>`, Groq).

    - les blocs fermés sont retirés ;
    - un bloc ouvert jamais fermé (coupure) ne contient que du raisonnement : retiré jusqu'à la fin ;
    - une fermeture seule (le gabarit du modèle a posé l'ouverture dans le prompt) : tout ce qui la précède est du raisonnement.
    Le raisonnement rendu dans un champ SÉPARÉ (OpenRouter) n'est jamais lu : seul `message.content` l'est."""
    texte = _BLOC_RAISONNEMENT.sub("", texte)
    texte = _OUVERTURE_SEULE.sub("", texte)
    reste = _FERMETURE.split(texte)
    return reste[-1] if len(reste) > 1 else texte


def avec_adresse(profil: ProfilFournisseur, adresse: str) -> ProfilFournisseur:
    """RÉSERVÉ AUX TESTS : le même profil avec une autre adresse (transport simulé, adresse locale).

    N'est lu par aucune route ni aucun réglage : l'adresse d'un fournisseur nommé n'est pas configurable."""
    return replace(profil, adresse=adresse)


def _retry_after(exc: openai.APIStatusError) -> Optional[str]:
    """L'en-tête `Retry-After` d'un refus, tel quel (texte) ; absent si la réponse n'en porte pas. Jamais le corps."""
    reponse = getattr(exc, "response", None)
    entetes = getattr(reponse, "headers", None)
    return entetes.get("retry-after") if entetes is not None else None


class MoteurNomme:
    """Un service en ligne au format OpenAI, joint par l'adresse FIXE de son profil. Implémente `LLMEngine`."""

    # EN LIGNE, jamais « auto-hébergé » : défini ici pour qu'aucun héritage ne le rende vrai.
    auto_heberge = False
    # Proposé à l'écran seulement s'il est prêt (`llm.propose`).
    propose_si_pret = True

    def __init__(self, profil: ProfilFournisseur) -> None:
        self.profil = profil
        self.name = profil.nom
        self.profil_prudent = profil.profil_prudent
        # Cache PAR INSTANCE, clé sur (fabrique, adresse, clé) : un client à transport simulé d'un test ne fuit pas dans le
        # suivant, et une fabrique remplacée après coup n'est pas contournée. Aucun état de classe.
        self._client: Optional[openai.OpenAI] = None
        self._client_pour: Optional[tuple] = None

    # ---------- configuration (relue à chaque appel) ----------

    def _cle(self) -> str:
        return (os.getenv(self.profil.variable_cle) or "").strip()

    def _modele(self) -> str:
        return (config.getenv_compat(self.profil.variable_modele, "") or "").strip()

    def _sdk(self) -> openai.OpenAI:
        cible = (client_sans_fuite, self.profil.adresse, self._cle())
        if self._client is None or self._client_pour != cible:
            self._client = client_sans_fuite(self.profil.adresse, api_key=self._cle())
            self._client_pour = cible
        return self._client

    # ---------- Contrat ----------

    def pret_declare(self) -> bool:
        """Une clé ET un modèle. Aucun appel réseau : la route de configuration l'appelle à chaque ouverture de l'interface."""
        return bool(self._cle() and self._modele())

    def is_available(self, *, api_key: Optional[str] = None) -> bool:
        # `api_key` (la clé saisie à l'écran) est ignorée : seule la clé du `.env` compte.
        return self.pret_declare()

    def modele_servi(self, model: Optional[str] = None) -> str:
        return model or self._modele() or modele_non_configure(self.name)

    def _completion(self, system: str, user: str, *, model: Optional[str], max_tokens: Optional[int],
                    temperature: float, seed: Optional[int]) -> tuple[str, Optional[str]]:
        """(texte, motif d'arrêt). Chaque panne devient une `NommeLLMError` lisible, sans la clé ni le corps."""
        libelle = self.profil.libelle
        if not self.pret_declare():
            raise NommeLLMError(
                f"{libelle} n'est pas configuré : renseignez {self.profil.variable_cle} et "
                f"MEMOGLYPH_{self.profil.variable_modele} dans le fichier .env.")
        cible = self.modele_servi(model)
        options: dict[str, Any] = {}
        if max_tokens is not None:
            options["max_tokens"] = max_tokens
        if seed is not None:
            options["seed"] = seed
        if self.profil.parametres_requete:
            options["extra_body"] = dict(self.profil.parametres_requete)
        # Jusqu'à `REESSAIS_MAX` reprises, et seulement dans un traitement qui a ouvert `attente_quota` : voir ce module. Chaque requête réseau
        # est UN appel et laisse sa ligne d'usage (le code seul d'un refus, jamais le corps).
        for tentative in range(1, attente_quota.REESSAIS_MAX + 2):
            t0 = time.perf_counter()
            try:
                reponse = self._sdk().chat.completions.create(
                    model=cible,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    temperature=temperature, stream=False, timeout=DELAI_S, **options)
                break
            except openai.RateLimitError as exc:    # avant APIStatusError, dont il hérite
                self._noter_refus(cible, t0, "429")
                if tentative <= attente_quota.REESSAIS_MAX and attente_quota.attendre_avant_reessai(_retry_after(exc)):
                    continue
                raise LimiteDeDemandes(
                    f"Quota atteint chez {libelle} : attendez un moment ou choisissez un autre modèle.") from exc
            except openai.APITimeoutError as exc:   # avant APIConnectionError, dont il hérite
                self._noter_refus(cible, t0, "timeout")
                raise NommeLLMError(f"{libelle} : aucune réponse en {DELAI_S:g} s, délai dépassé.") from exc
            except openai.APIConnectionError as exc:
                self._noter_refus(cible, t0, "connection")
                raise NommeLLMError(f"{libelle} est injoignable : vérifiez la connexion.") from exc
            except openai.APIStatusError as exc:
                self._noter_refus(cible, t0, str(exc.status_code))
                raise NommeLLMError(f"{libelle} a répondu {exc.status_code} pour le modèle {cible!r}.") from exc
            except openai.APIError as exc:
                self._noter_refus(cible, t0, "api")
                raise NommeLLMError(f"{libelle} a rendu une réponse hors contrat ({type(exc).__name__}).") from exc
        usage = getattr(reponse, "usage", None)
        servi = getattr(reponse, "model", None)         # le modèle QUI A RÉPONDU (un routeur peut en choisir un par appel)
        choix = (reponse.model_dump().get("choices") or [{}])[0]
        message = choix.get("message") if isinstance(choix, dict) else None
        contenu = message.get("content") if isinstance(message, dict) else None
        motif = choix.get("finish_reason") if isinstance(choix, dict) else None
        # Le motif d'arrêt AVANT le type du contenu : un contenu nul avec `length` est la limite de jetons (le raisonnement a
        # probablement mangé le budget), pas une réponse « sans contenu textuel ».
        code = None
        if not isinstance(contenu, str):
            code = "length" if motif == "length" else "no_text"
        else:
            contenu = retirer_raisonnement(contenu)
            if motif == "length":
                code = "length"
            elif not contenu.strip():
                code = "empty"
        telemetry.record_model_call(provider=self.name, model=cible, latency_s=time.perf_counter() - t0,
                                    status="truncated" if code == "length" else ("error" if code else "ok"),
                                    usage=usage, usage_source="provider_reported" if usage is not None else None,
                                    served_model=servi if isinstance(servi, str) and servi else None, error_code=code)
        if code == "no_text":
            raise NommeLLMError(f"Réponse de {libelle} sans contenu textuel.")
        if code == "length":
            vide = not isinstance(contenu, str) or not contenu.strip()
            raise NommeLLMError(f"Réponse de {libelle} coupée par la limite de jetons (max_tokens)"
                                + (" : aucun texte n'a été rendu, le raisonnement du modèle a probablement consommé tout le budget."
                                   if vide else "."))
        if code == "empty":
            raise NommeLLMError(f"Réponse de {libelle} vide : aucun texte n'a été rendu.")
        return contenu, motif

    def _noter_refus(self, cible: str, t0: float, code: str) -> None:
        """La ligne d'usage d'un appel refusé : le CODE seul (statut HTTP, `timeout`, `connection`…), jamais le corps ni la clé."""
        telemetry.record_model_call(provider=self.name, model=cible, latency_s=time.perf_counter() - t0,
                                    status="error", error_code=code)

    def chat(self, system: str, user: str, *, model: Optional[str] = None,
             api_key: Optional[str] = None, seed: Optional[int] = None) -> str:
        return self._completion(system, user, model=model, max_tokens=None, temperature=0.2,
                                seed=seed)[0].strip()

    def chat_json(self, system: str, user: str, *, model: Optional[str] = None,
                  api_key: Optional[str] = None, max_tokens: Optional[int] = None,
                  schema: Optional[dict] = None, retries: int = 2, temperature: float = 0.1,
                  seed: Optional[int] = None) -> dict[str, Any]:
        directive = system + "\n\nCRUCIAL : réponds EXCLUSIVEMENT en JSON valide."
        if schema is not None:
            directive += "\n\nSchéma JSON à respecter STRICTEMENT :\n" + json.dumps(schema, ensure_ascii=False)
        # Avec une graine, le service rejoue la même sortie : un nouvel essai ne changerait rien.
        essais = 1 if seed is not None else retries + 1
        dernier, motif = "", None
        for _ in range(essais):
            contenu, motif = self._completion(directive, user, model=model, max_tokens=max_tokens,
                                              temperature=temperature, seed=seed)
            dernier = retirer_enveloppe_markdown(contenu)
            try:
                donnees = json.loads(dernier)
            except json.JSONDecodeError:
                continue
            if isinstance(donnees, dict):
                return donnees
            raise NommeLLMError(
                f"Le JSON de {self.profil.libelle} n'est pas une table : {type(donnees).__name__}.")
        raise _SortieInexploitable(
            f"Réponse de {self.profil.libelle} invalide après {essais} essai(s) : {dernier[:300]!r}",
            brut=dernier, finish_reason=motif)
