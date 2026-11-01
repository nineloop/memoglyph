"""Serveur MCP en lecture seule : `./memoglyph mcp <identifiant>`.

Expose aux agents (Claude, Codex, Cursor…) ce que Memoglyph sait déjà : projets,
réunions, recherche, comptes rendus avec leurs citations horodatées, faits de l'ECL.

- **Lancé sur la machine**, comme `reset-password` : qui lance le serveur a accès
  aux données. Il sert UN compte, nommé au lancement, et ne lit que sa portée de
  lecture (`portee.lecture`) : ses projets et ce qui lui est partagé, rien d'autre.
- **Lecture seule par construction** : aucun outil n'appelle une fonction qui écrit.
  Pas de session ouverte, pas d'index reconstruit, pas de modèle de génération.
- **Un client MCP peut être un modèle cloud** : un document `ultra_confidential`
  n'est rendu que si c'est un document DU COMPTE servi et que ce compte autorise le
  cloud pour son ultra-confidentiel (`sensitivity.autorise`, réglage relu à chaque
  appel). Sinon il n'est jamais rendu, comme pour l'Assistant en cloud ; seul le
  nombre d'éléments écartés l'est. Ce qui est partagé avec le compte n'emporte
  jamais d'ultra-confidentiel (Q16, `library.documents_lisibles`).
- **Transport stdio, sur le SDK officiel `mcp`** (`MCPServer`, `stdio_server`) : le
  SDK tient le protocole (poignée de main, JSON-RPC, validation des arguments, codes
  d'erreur). La sortie standard ne porte que le protocole ; tout le reste part sur
  l'erreur standard.

Deux étages : `Lecteur` porte la logique métier (portée, ultra-confidentiel, outils)
et se teste sans transport ; `construire` l'expose en outils MCP.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from typing import Annotated, Callable, Optional

from fastapi import HTTPException
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from core import ecl, library, semantic, sensitivity
from core import projects as project_store

from . import auth, portee

VERSION = "1"
INSTRUCTIONS = ("Lecture seule. Le contenu ultra-confidentiel n'est rendu que si le compte "
                "l'autorise pour un fournisseur cloud.")

OUTILS: dict[str, str] = {
    "lister_projets": "Les projets du compte, et ce qui lui est partagé.",
    "lister_reunions": "Les réunions d'un projet (ou de tous les projets du compte si `projet` est vide).",
    "rechercher": "Recherche hybride (mots et sens) dans les transcripts et comptes rendus.",
    "lire_compte_rendu": "Le compte rendu d'une réunion, avec ses citations horodatées [source HH:MM:SS].",
    "lire_faits": "Les faits de l'ECL d'une réunion (décisions, actions, problèmes, chiffres, "
                  "et la marque d'une extraction partielle), "
                  "chacun avec sa citation horodatée et son statut « à vérifier ».",
}



def outils_proposes() -> dict[str, str]:
    """Les outils servis : `lire_faits` seulement avec l'option des faits (`config.FAITS`)."""
    return {nom: description for nom, description in OUTILS.items()
            if nom != "lire_faits" or ecl.actives()}


# Annoncé à chaque outil : aucune écriture, aucun accès hors de la bibliothèque locale.
_LECTURE_SEULE = ToolAnnotations(read_only_hint=True, destructive_hint=False,
                                 idempotent_hint=True, open_world_hint=False)

# `strict` : ni nombre pour un texte, ni texte ou booléen pour un entier, comme la
# validation écrite à la main que le SDK remplace.
_Projet = Annotated[str, Field(description="identifiant du projet ; vide : tous", strict=True)]
_Reunion = Annotated[str, Field(description="identifiant de la réunion", strict=True)]


class Introuvable(Exception):
    """Hors portée ou inexistant : même réponse, pour ne pas dire que la chose existe."""


class ParametresInvalides(Exception):
    pass


def _texte(params: dict, cle: str, *, requis: bool = False) -> str:
    valeur = params.get(cle, "")
    if not isinstance(valeur, str) or (requis and not valeur.strip()):
        raise ParametresInvalides(f"« {cle} » : texte {'non vide ' if requis else ''}attendu")
    return valeur.strip()


def _ultra(valeur: object) -> bool:
    return sensitivity.cloud_refuse(valeur, local=False)


def _document_ultra(nom: str, meeting_id: Optional[str]) -> bool:
    """Le document, ou un autre de sa réunion, est-il ultra-confidentiel (Q16) ?

    Le calcul du CR, de l'ECL et de l'Assistant (`library.sensibilite_effective`).
    """
    meta = (library.get_transcript(nom) or {}).get("meta") or {}
    return _ultra(library.sensibilite_effective(meta.get("sensitivity"), meeting_id or meta.get("meeting_id"),
                                                meta.get("project_id")))


class Lecteur:
    """Les cinq outils pour un compte, sans transport : un dictionnaire d'arguments en
    entrée, un dictionnaire en sortie, `Introuvable` ou `ParametresInvalides` sinon."""

    def __init__(self, compte: auth.Utilisateur) -> None:
        self.compte = compte

    # ---------- Ultra-confidentiel ----------

    def _ouvert(self, noms: list[str]) -> bool:
        """L'ultra-confidentiel de ces documents peut-il être rendu, maintenant ?

        Seulement si CHACUN appartient au compte servi (manifeste de son projet) et que
        ce compte autorise le cloud pour son ultra-confidentiel, réglage relu à chaque
        appel. Un document d'un autre compte, ou d'un projet sans propriétaire, reste
        fermé : l'autorisation est celle du propriétaire, jamais celle de qui lit.
        """
        if not noms or not sensitivity.autorise(self.compte.identifiant):
            return False
        return all(project_store.meme_compte(library.proprietaire_du_document(nom),
                                             self.compte.identifiant) for nom in noms)

    def _tracer(self, noms: list[str]) -> None:
        """La ligne de journal d'un rendu ultra-confidentiel autorisé : compte, identifiants."""
        for nom in noms:
            meta = (library.get_transcript(nom) or {}).get("meta") or {}
            sensitivity.tracer(sensitivity.Envoi(sensibilite=sensitivity.ULTRA_CONFIDENTIEL,
                                                 proprietaire=self.compte.identifiant,
                                                 document_id=meta.get("id")),
                               fournisseur="mcp")

    def _ferme(self, ultra: bool, noms: list[str]) -> bool:
        """Vrai si l'ultra-confidentiel doit rester caché ; trace le rendu autorisé sinon."""
        if not ultra:
            return False
        if self._ouvert(noms):
            self._tracer(noms)
            return False
        return True

    # ---------- Portée ----------

    def _portees(self, projet: str = "") -> list[project_store.Portee]:
        """Un projet nommé, sinon tous ceux du compte PUIS ce qui lui est partagé : deux
        portées de lecture distinctes, comme à l'écran (`portee.TOUS`, `portee.PARTAGES`).

        `portee.tous_mes_projets` ne rend que les projets existants et n'écrit rien,
        « Non classé » compris. Une portée vide est sautée, jamais lue comme « sans
        filtre ».
        """
        try:
            if projet:
                return [portee.lecture(self.compte, projet)]
            propres = portee.tous_mes_projets(self.compte)
            return [*([propres] if propres else []), portee.lecture(self.compte, portee.PARTAGES)]
        except HTTPException as exc:
            raise Introuvable("Projet introuvable") from exc

    def _reunion(self, meeting_id: str) -> tuple[dict, project_store.Portee]:
        """La réunion du compte : ses documents dans UN projet d'une de ses portées de lecture
        (`library.reunion_dans_la_portee`), jamais le document homonyme d'un autre compte."""
        reunion, lue = None, None
        for portee_lue in self._portees():
            try:
                reunion = library.reunion_dans_la_portee(meeting_id, portee_lue)
            except library.ReunionAmbigue as exc:
                raise Introuvable("Plusieurs projets du compte portent cette réunion") from exc
            if reunion is not None:
                lue = portee_lue
                break
        if reunion is None:
            raise Introuvable("Réunion introuvable")
        noms = [d["name"] for d in (*reunion["transcripts"], *reunion["reports"])]
        if self._ferme(any(_ultra(library.sensibilite_effective(d.get("sensitivity"), meeting_id,
                                                               d.get("project_id")))
                           for d in (*reunion["transcripts"], *reunion["reports"])), noms):
            raise Introuvable("Réunion ultra-confidentielle : rendue seulement si le compte "
                              "autorise le cloud pour son ultra-confidentiel")
        return reunion, lue

    # ---------- Outils ----------

    def lister_projets(self, _params: dict) -> dict:
        propres = [{"id": p.id, "nom": p.name} for _d, p in portee.projets_visibles(self.compte)]
        partages = [{"type": p["type"], "cible": p["cible"]} for p in portee.partages_actifs(self.compte)]
        return {"projets": propres, "partages": partages}

    def lister_reunions(self, params: dict) -> dict:
        reunions, ecartees, vues = [], 0, set()
        for r in (r for lue in self._portees(_texte(params, "projet"))
                  for r in library.list_meetings(project_id=lue)):
            if (r.get("project_id"), r.get("id")) in vues:
                continue
            vues.add((r.get("project_id"), r.get("id")))
            documents = (*r.get("transcripts", []), *r.get("reports", []))
            if self._ferme(any(_ultra(library.sensibilite_effective(d.get("sensitivity"), r.get("id"),
                                                                   d.get("project_id")))
                               for d in documents),
                           [d["name"] for d in documents]):
                ecartees += 1
                continue
            reunions.append({"meeting_id": r.get("id"), **{cle: r.get(cle) for cle in (
                "title", "date", "duration", "language", "n_speakers", "project")},
                "sensibilite": r.get("sensitivity")})
        return {"reunions": reunions, "ecartees_ultra_confidentielles": ecartees}

    def rechercher(self, params: dict) -> dict:
        requete = _texte(params, "requete", requis=True)
        limite = params.get("limite", 10)
        if not isinstance(limite, int) or isinstance(limite, bool) or not 1 <= limite <= 50:
            raise ParametresInvalides("« limite » : entier de 1 à 50 attendu")
        resultats, ecartes, vus = [], 0, set()
        for r in (r for lue in self._portees(_texte(params, "projet"))
                  for r in semantic.search(requete, k=limite, project_id=lue)):
            if r["name"] in vus:
                continue
            vus.add(r["name"])
            if self._ferme(_document_ultra(r["name"], r.get("meeting_id")), [r["name"]]):
                ecartes += 1
                continue
            resultats.append({cle: r.get(cle) for cle in ("meeting_id", "title", "name", "origine",
                                                          "ts", "snippet")})
        return {"resultats": resultats[:limite], "ecartes_ultra_confidentiels": ecartes}

    def lire_compte_rendu(self, params: dict) -> dict:
        reunion, _lue = self._reunion(_texte(params, "reunion", requis=True))
        rapports = reunion.get("reports", [])
        if not rapports:
            raise Introuvable("Cette réunion n'a pas encore de compte rendu")
        nom = rapports[0]["name"]  # le plus récent
        document = library.get_transcript(nom) or {}
        return {"meeting_id": reunion.get("id"), "titre": reunion.get("title"),
                "document": nom,
                "compte_rendu": library.h1_lisible(document.get("body", ""), reunion.get("title"))}

    def lire_faits(self, params: dict) -> dict:
        if not ecl.actives():
            raise Introuvable(ecl.DESACTIVES)
        meeting_id = _texte(params, "reunion", requis=True)
        reunion, lue = self._reunion(meeting_id)
        # Les faits des seuls documents de CETTE réunion (son projet), jamais d'un homonyme.
        documents = {d.get("id") for d in (*reunion["transcripts"], *reunion["reports"])}
        faits = []
        partielles: dict[str, Optional[dict]] = {}
        # Les faits actuels seuls : un fait périmé (source corrigée depuis) n'est jamais servi.
        # Aucun taux de précision : « à vérifier » est la seule mention, une mise en garde.
        for fait in (f for f in ecl.faits(lue, meeting_id=meeting_id) if f["document_id"] in documents):
            mode = fait.get("mode") or "local"
            if fait["document_id"] not in partielles:
                contenu = ecl.lire_fichier(fait["document_id"]) or {}
                # Extraction partielle : tranches en échec sur tranches, None si complète.
                partielles[fait["document_id"]] = contenu.get("partielle")
            faits.append({**{cle: fait.get(cle) for cle in ("type", "horodatage", "citation", "enonce",
                                                           "porteur", "locuteur")},
                          "a_verifier": mode != "cloud",
                          "extraction_partielle": partielles[fait["document_id"]]})
        return {"meeting_id": meeting_id, "faits": faits}


def _rendu(outil: Callable[[dict], dict], params: dict) -> CallToolResult:
    """Le résultat d'un outil, en texte JSON (`content`) et en objet (`structuredContent`).

    `Introuvable` reste une erreur d'outil lisible par l'agent, mot pour mot ;
    `ParametresInvalides` suit le chemin des arguments refusés par le SDK.
    """
    try:
        contenu = outil(params)
    except ParametresInvalides as exc:
        raise ToolError(str(exc)) from exc
    except Introuvable as exc:
        return CallToolResult(content=[TextContent(type="text", text=str(exc))], is_error=True)
    texte = json.dumps(contenu, ensure_ascii=False, indent=1)
    return CallToolResult(content=[TextContent(type="text", text=texte)], structured_content=contenu)


def construire(compte: auth.Utilisateur) -> MCPServer:
    """Le serveur MCP du compte : les outils de `Lecteur`, avec leurs arguments typés.

    Les outils sont `async` sans rien attendre : le SDK exécute un outil synchrone
    dans un fil à part, alors qu'ici chaque appel reste sur le fil de la boucle, un à
    la fois, comme les connexions SQLite et le modèle d'encodage l'ont toujours vu.
    """
    lecteur = Lecteur(compte)
    serveur = MCPServer("memoglyph", version=VERSION, instructions=INSTRUCTIONS)
    outil = {nom: serveur.tool(name=nom, description=description, annotations=_LECTURE_SEULE)
             for nom, description in outils_proposes().items()}

    @outil["lister_projets"]
    async def lister_projets() -> CallToolResult:
        return _rendu(lecteur.lister_projets, {})

    @outil["lister_reunions"]
    async def lister_reunions(projet: _Projet = "") -> CallToolResult:
        return _rendu(lecteur.lister_reunions, {"projet": projet})

    @outil["rechercher"]
    async def rechercher(requete: Annotated[str, Field(strict=True)], projet: _Projet = "",
                         limite: Annotated[int, Field(ge=1, le=50, strict=True)] = 10) -> CallToolResult:
        return _rendu(lecteur.rechercher, {"requete": requete, "projet": projet, "limite": limite})

    @outil["lire_compte_rendu"]
    async def lire_compte_rendu(reunion: _Reunion) -> CallToolResult:
        return _rendu(lecteur.lire_compte_rendu, {"reunion": reunion})

    if "lire_faits" in outil:   # option des faits coupée : l'outil n'est pas proposé
        @outil["lire_faits"]
        async def lire_faits(reunion: _Reunion) -> CallToolResult:
            return _rendu(lecteur.lire_faits, {"reunion": reunion})

    return serveur


def servir(compte: auth.Utilisateur) -> None:
    """Sert le compte sur `sys.stdin` et `sys.stdout` jusqu'à la fin de l'entrée.

    Adossés aux descripteurs 0 et 1, `stdio_server` sert le protocole sur un double
    privé et détourne le descripteur 1 vers l'erreur standard : une écriture parasite,
    même d'une bibliothèque en C, manque le protocole. Le `redirect_stdout` couvre en
    plus `sys.stdout` quand il n'est pas adossé au descripteur 1 (un test, un appelant
    Python) : un module bavard (chargement d'un modèle…) n'y écrit pas.
    """
    serveur = construire(compte)

    async def principal() -> None:
        # Dans cet ordre : `stdio_server` retient d'abord le vrai `sys.stdout`, puis la
        # redirection détourne le reste. `MCPServer.run_stdio_async` ne laisse pas
        # glisser la redirection entre les deux, d'où le serveur de bas niveau, celui
        # que le transport en mémoire du SDK lance lui aussi.
        async with stdio_server() as (lecture, ecriture):
            with contextlib.redirect_stdout(sys.stderr):
                await serveur._lowlevel_server.run(
                    lecture, ecriture, serveur._lowlevel_server.create_initialization_options())

    asyncio.run(principal())
