"""Memoglyph — API FastAPI (backend). Le moteur vit dans `core/`, l'UI est un client.

Lancement :  uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Literal, Optional

from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

import threading

from core import config, pipeline, stt
from core import faithful_backfill
from core import sensitivity
from core import llm, resources
from core import docid, library, sqlite_db
from core import ecl
from core import semantic
from core import voiceprint
from core import maintenance
from core import projects as project_store
from . import auth, partages, portee
from .jobs import GROUPES_COMPTE_RENDU, JobManager, JobsBusy, plan_etapes

config.load_env()  # charge .env (HF_TOKEN) au démarrage de l'API

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Démarrage de l'API (remplace on_event, déprécié)."""
    if auth.preparer_installation():
        # Le code lui-même n'est jamais journalisé : `./start.sh` le lit dans
        # son fichier, lisible par le seul propriétaire.
        print(f"[auth] aucun compte : code d'installation prêt dans {auth.CODE_PATH}")
    _startup_reindex()
    _startup_finops()
    yield


# Documentation interactive fermée : `/docs`, `/redoc` et
# `/openapi.json` décrivaient toute l'API sans session.
app = FastAPI(title="Memoglyph API", version="0.6.0", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)

# CORS : autorise le serveur de dev Vite (port piloté par l'env, défaut 8770).
_ui_port = config.getenv_compat("UI_PORT", "8770")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"http://localhost:{_ui_port}", f"http://127.0.0.1:{_ui_port}",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Authentification ----------

# Seules routes /api ouvertes sans session. Tout le reste exige une session,
# vérifié sur `app.routes` par `tests/unit/test_auth_routes_protegees.py`.
ROUTES_PUBLIQUES = frozenset({
    ("GET", "/api/health"),
    ("GET", "/api/auth/etat"),
    ("POST", "/api/auth/installation"),
    ("POST", "/api/auth/connexion"),
    ("POST", "/api/auth/deconnexion"),
})

# Routes que le compagnon de capture appelle avec son jeton d'appareil,
# en plus de `/api/health`, déjà publique. Partout ailleurs, un jeton seul ne
# vaut rien : il ne lit pas une réunion, ne crée pas d'autre jeton, n'administre
# rien. Vérifié sur `app.routes` par `tests/integration/test_jetons_appareil.py`.
ROUTES_DU_COMPAGNON = frozenset({
    ("POST", "/api/transcribe"),
    ("GET", "/api/jobs/{job_id}"),
    ("GET", "/api/jobs/{job_id}/events"),
    ("GET", "/api/projects"),
    # Chantier 003 : « en attente : N traitements ». Avec un jeton, la route ne rend que les
    # comptes du compte et SES propres captures (`traitements_du_compte`).
    ("GET", "/api/jobs"),
})
_MOTIFS_DU_COMPAGNON = tuple(
    (methode, re.compile(re.sub(r"\{[^}]+\}", "[^/]+", chemin)))
    for methode, chemin in ROUTES_DU_COMPAGNON
)


def _route_du_compagnon(methode: str, chemin: str) -> bool:
    return any(methode == m and motif.fullmatch(chemin) for m, motif in _MOTIFS_DU_COMPAGNON)


def _jeton_porteur(request: Request) -> Optional[str]:
    """Le jeton de « Authorization: Bearer <jeton> », sinon `None`."""
    schema, _, jeton = request.headers.get("authorization", "").partition(" ")
    if schema.lower() != "bearer":
        return None
    return jeton.strip() or None


@app.middleware("http")
async def _session_obligatoire(request: Request, call_next):
    """Toute route /api exige une session valide, local compris.

    Un middleware plutôt qu'une dépendance par route : une route ajoutée plus
    tard est protégée sans qu'on y pense, et la liste des exceptions tient en
    un seul endroit.
    """
    chemin = request.url.path
    if (chemin.startswith("/api/") and request.method != "OPTIONS"
            and (request.method, chemin) not in ROUTES_PUBLIQUES):
        utilisateur = auth.utilisateur_de_session(request.cookies.get(auth.COOKIE))
        if utilisateur is None and _route_du_compagnon(request.method, chemin):
            utilisateur = auth.utilisateur_de_jeton(_jeton_porteur(request))
            # Le compagnon suit son travail sans en lire le texte (`_vue_du_compagnon`).
            request.state.par_jeton = utilisateur is not None
            if utilisateur is not None:
                request.state.jeton_id = auth.id_du_jeton(_jeton_porteur(request))
        if utilisateur is None:
            return JSONResponse({"detail": "Connexion requise."}, status_code=401)
        request.state.utilisateur = utilisateur
    return await call_next(request)


_BOUCLE_LOCALE = {"127.0.0.1", "::1", "localhost"}
_METHODES_MODIFIANTES = {"POST", "PUT", "PATCH", "DELETE"}


def _derriere_le_proxy(request: Request) -> bool:
    """Requête relayée par le proxy de Vite, sur la même machine (`xfwd`)."""
    return bool(request.client and request.client.host in _BOUCLE_LOCALE)


def _adresse_client(request: Request) -> str:
    """Adresse du navigateur : derrière le proxy local, celle qu'il transmet."""
    transmise = request.headers.get("x-forwarded-for", "") if _derriere_le_proxy(request) else ""
    if transmise:
        return transmise.split(",")[0].strip()
    return request.client.host if request.client else "inconnue"


def _port_interface() -> str:
    """Port de l'interface : celui que `./start.sh` a retenu, sinon la configuration."""
    try:
        for ligne in (config.ROOT / ".memoglyph-ports").read_text(encoding="utf-8").splitlines():
            cle, _, valeur = ligne.partition("=")
            if cle.strip() == "UI_PORT" and valeur.strip():
                return valeur.strip()
    except OSError:
        pass
    return config.getenv_compat("UI_PORT", "8770")


def _origines_admises(request: Request) -> set[str]:
    hote = request.headers.get("host", "")
    schema = request.url.scheme
    if _derriere_le_proxy(request):
        hote = request.headers.get("x-forwarded-host", "").split(",")[0].strip() or hote
        schema = request.headers.get("x-forwarded-proto", "").split(",")[0].strip() or schema
    port_ui = _port_interface()
    return {f"{schema}://{hote}", f"http://localhost:{port_ui}", f"http://127.0.0.1:{port_ui}"}


@app.middleware("http")
async def _origine_controlee(request: Request, call_next):
    """Refuse une requête qui modifie l'état depuis une page d'une autre origine.

    Le cookie `SameSite=Strict` couvre déjà l'essentiel ; ce contrôle ferme le
    reste en mode LAN. Une requête sans en-tête `Origin` (`curl`, scripts) passe :
    seuls les navigateurs l'envoient, et c'est d'eux que vient le risque.
    """
    origine = request.headers.get("origin")
    if (origine and request.method in _METHODES_MODIFIANTES
            and request.url.path.startswith("/api/")
            and origine not in _origines_admises(request)):
        return JSONResponse({"detail": "Origine refusée."}, status_code=403)
    return await call_next(request)


def _trop_de_tentatives(exc: auth.TropDeTentatives) -> HTTPException:
    return HTTPException(429, str(exc), headers={"Retry-After": str(exc.secondes)})


def _poser_cookie(reponse: Response, request: Request, jeton: str) -> None:
    reponse.set_cookie(
        auth.COOKIE, jeton, httponly=True, samesite="strict",
        secure=request.url.scheme == "https",
        max_age=int(auth.DUREE_SESSION.total_seconds()), path="/")


def _profil(utilisateur: auth.Utilisateur) -> dict:
    return {"identifiant": utilisateur.identifiant, "admin": utilisateur.admin}


class Identifiants(BaseModel):
    identifiant: str
    mot_de_passe: str


class Installation(Identifiants):
    code: str


class NouveauCompte(Identifiants):
    admin: bool = False


@app.get("/api/auth/etat")
def auth_etat(request: Request) -> dict:
    """Ce que l'interface doit montrer : installation, connexion ou application."""
    utilisateur = auth.utilisateur_de_session(request.cookies.get(auth.COOKIE))
    return {
        "installation_requise": auth.installation_requise(),
        "connecte": utilisateur is not None,
        "utilisateur": _profil(utilisateur) if utilisateur else None,
    }


@app.post("/api/auth/installation")
def auth_installation(corps: Installation, request: Request, response: Response) -> dict:
    """Crée le compte administrateur avec le code d'installation, puis connecte."""
    cles = ["installation", f"installation:{_adresse_client(request)}"]
    try:
        auth.verifier_attente(cles)
        utilisateur = auth.installer(corps.code, corps.identifiant, corps.mot_de_passe)
    except auth.TropDeTentatives as exc:
        raise _trop_de_tentatives(exc) from exc
    except auth.ErreurAuth as exc:
        auth.noter_echec(cles)
        raise HTTPException(400, str(exc)) from exc
    _non_classe_du_compte_cree(utilisateur)
    _poser_cookie(response, request, auth.ouvrir_session(utilisateur))
    return {"utilisateur": _profil(utilisateur)}


@app.post("/api/auth/connexion")
def auth_connexion(corps: Identifiants, request: Request, response: Response) -> dict:
    # Les échecs comptent par identifiant, qu'il existe ou non, et par adresse :
    # le blocage ne révèle donc pas si un compte existe.
    compte = auth.cle_compte(corps.identifiant)
    cles = [compte, f"adresse:{_adresse_client(request)}"]
    try:
        auth.verifier_attente(cles)
    except auth.TropDeTentatives as exc:
        raise _trop_de_tentatives(exc) from exc
    utilisateur = auth.verifier_identifiants(corps.identifiant, corps.mot_de_passe)
    if utilisateur is None:
        auth.noter_echec(cles)
        # Même message dans les deux cas : ne pas dire si l'identifiant existe.
        raise HTTPException(401, "Identifiant ou mot de passe incorrect.")
    auth.effacer_echecs(compte)
    _poser_cookie(response, request, auth.ouvrir_session(utilisateur))
    return {"utilisateur": _profil(utilisateur)}


@app.post("/api/auth/deconnexion")
def auth_deconnexion(request: Request, response: Response) -> dict:
    auth.revoquer_session(request.cookies.get(auth.COOKIE))
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


def _non_classe_du_compte_cree(utilisateur: auth.Utilisateur) -> None:
    """Le « Non classé » d'un compte naît avec lui (installation comprise) : une lecture
    ne le crée jamais (`api.portee`). Sur une bibliothèque vierge, l'administrateur de
    l'installation reçoit celui de la racine.

    Le compte est déjà créé quand cet appel échoue (manifeste illisible) : l'échec est
    journalisé et le compte reste utilisable ; sa première écriture recrée le « Non
    classé » ou rend la même erreur en 409, visible.
    """
    try:
        portee.creer_non_classe(utilisateur.identifiant)
    except HTTPException as exc:
        print(f"[projets] « Non classé » de {utilisateur.identifiant} non créé : {exc.detail}")


def _exiger_admin(request: Request) -> auth.Utilisateur:
    """404 et non 403 pour un compte ordinaire : comme pour un document hors de
    sa portée (`api.portee`), la route n'existe pas pour lui."""
    utilisateur = getattr(request.state, "utilisateur", None)
    if utilisateur is None or not utilisateur.admin:
        raise HTTPException(404, "Not Found")
    return utilisateur


@app.get("/api/admin/utilisateurs")
def admin_lister_comptes(request: Request) -> list[dict]:
    _exiger_admin(request)
    return auth.lister_comptes()


@app.post("/api/admin/utilisateurs", status_code=201)
def admin_creer_compte(corps: NouveauCompte, request: Request) -> dict:
    """Pas d'inscription libre : seul un administrateur crée un compte."""
    _exiger_admin(request)
    try:
        utilisateur = auth.creer_compte(corps.identifiant, corps.mot_de_passe,
                                        admin=corps.admin)
    except auth.ErreurAuth as exc:
        raise HTTPException(400, str(exc)) from exc
    _non_classe_du_compte_cree(utilisateur)
    return {"utilisateur": _profil(utilisateur)}


@app.post("/api/admin/utilisateurs/{identifiant}/desactivation")
def admin_desactiver_compte(identifiant: str, request: Request) -> dict:
    """Le compte ne se connecte plus ; ses sessions sont révoquées. Aucune suppression."""
    _exiger_admin(request)
    try:
        auth.desactiver_compte(identifiant)
    except auth.ErreurAuth as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@app.post("/api/admin/utilisateurs/{identifiant}/reactivation")
def admin_reactiver_compte(identifiant: str, request: Request) -> dict:
    _exiger_admin(request)
    try:
        auth.reactiver_compte(identifiant)
    except auth.ErreurAuth as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


class NouveauJeton(BaseModel):
    nom: str


@app.get("/api/jetons")
def lister_jetons(request: Request) -> list[dict]:
    """Les jetons d'appareil du compte : nom, création, dernier usage. Jamais le jeton."""
    return auth.lister_jetons(portee.utilisateur(request))


@app.post("/api/jetons", status_code=201)
def creer_jeton(corps: NouveauJeton, request: Request) -> dict:
    """Crée un jeton pour le compagnon de capture ; il n'est rendu qu'ici, une fois."""
    try:
        description, jeton = auth.creer_jeton(portee.utilisateur(request), corps.nom)
    except auth.ErreurAuth as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"jeton": description, "secret": jeton}


class ReglageCloudUltra(BaseModel):
    # Champ inconnu refusé : aucun `identifiant` ne désigne un autre compte que celui
    # de la session.
    model_config = {"extra": "forbid"}
    autorise: bool


@app.get("/api/compte/cloud-ultra")
def lire_cloud_ultra(request: Request) -> dict:
    """Le réglage du compte de la session : autorise-t-il le cloud pour son ultra-confidentiel ?

    Session seulement : un jeton d'appareil ne l'ouvre pas (`ROUTES_DU_COMPAGNON`), et
    aucun paramètre ne désigne un autre compte, administrateur compris.
    """
    return auth.cloud_ultra(portee.utilisateur(request))


@app.put("/api/compte/cloud-ultra")
def regler_cloud_ultra(request: Request, corps: ReglageCloudUltra) -> dict:
    """Active ou désactive le réglage pour le compte de la session, seul. Vaut aussitôt :
    chaque envoi relit le réglage, y compris dans un travail déjà lancé."""
    return auth.regler_cloud_ultra(portee.utilisateur(request), corps.autorise)


@app.delete("/api/jetons/{jeton_id}")
def retirer_jeton(jeton_id: str, request: Request) -> dict:
    if not auth.retirer_jeton(portee.utilisateur(request), jeton_id):
        raise HTTPException(404, "Jeton inconnu")
    return {"ok": True}


# Relevé des traitements en cours, lu par `stop.sh` : à côté des fichiers PID,
# lisible par le seul propriétaire. Il ne sert qu'à l'arrêt, d'où un fichier
# local plutôt qu'une route : rien ne passe par le réseau.
TRAITEMENTS_PATH = sqlite_db.ROOT / ".memoglyph-traitements.json"
jobs = JobManager(releve=TRAITEMENTS_PATH)
UPLOAD_DIR = config.CACHE_DIR / "uploads"
ALL_PROJECTS_SCOPE = portee.TOUS
SSE_HEARTBEAT_SECONDS = 15.0
SSE_POLL_SECONDS = 0.2


def _libelle_portee(view_scope: str, ids: frozenset) -> str:
    """`all`, ou l'identité du projet seul de la portée : le champ `scope` des réponses d'usage.

    "" pour une portée vide : le « Non classé » d'un compte qui n'en a pas encore.
    """
    if (view_scope or "").strip() == ALL_PROJECTS_SCOPE:
        return ALL_PROJECTS_SCOPE
    return next(iter(ids), "")


def _portee_lecture(request: Request, view_scope: str = "") -> project_store.Portee:
    """La portée de LECTURE : celle du compte, ou ce qui lui est partagé (`shared`).

    Seules les routes qui lisent l'emploient ; une route qui écrit passe par
    `_portee`, qui ignore les partages : l'invité ne modifie rien.
    """
    return portee.lecture(portee.utilisateur(request), view_scope)


def _noms_visibles(request: Request, lu: project_store.Portee) -> frozenset[str]:
    """Les projets dont les noms de voix peuvent être rendus pour la portée lue :
    ceux du compte, ou, pour ce qui est partagé, ceux de qui partage. Une voix
    entièrement à lui garde son nom chez l'invité (Q12) ; seules ses
    occurrences dans ce qui est partagé sont montrées."""
    if isinstance(lu, project_store.PorteeLecture):
        return lu.proprietaires
    return _projets_du_compte(request)


def _portee(request: Request, view_scope: str = "") -> frozenset[str]:
    """La portée de consultation du compte de la session : tout passe par `api.portee`."""
    return portee.resoudre(portee.utilisateur(request), view_scope)


def _portee_avec_herites(request: Request, view_scope: str = "") -> frozenset:
    """`_portee`, plus les éléments sans projet (corbeille, usage) pour un
    administrateur en vue « tous mes projets » : ils datent d'avant les
    identités de projet, et ne sont à aucun compte."""
    compte = portee.utilisateur(request)
    ids = portee.resoudre(compte, view_scope)
    if compte.admin and (view_scope or "").strip() == ALL_PROJECTS_SCOPE:
        return ids | {None}
    return ids


def _portee_maintenance(request: Request, view_scope: str = "") -> Optional[str]:
    """Maintenance de la bibliothèque : réservée aux administrateurs.

    `all` désigne ici toute la bibliothèque, tous comptes confondus : c'est
    l'objet d'une réindexation ou d'un backfill. Sinon, un projet de sa portée.
    Vide = son « Non classé » ; s'il n'existe pas encore, `portee.AUCUN_PROJET` :
    ces fonctions lisent une chaîne vide comme « toute la bibliothèque ».
    """
    compte = _exiger_admin(request)
    scope = (view_scope or "").strip()
    if scope == ALL_PROJECTS_SCOPE:
        return None
    if not scope:
        inbox = portee.non_classe(compte)
        return inbox.id if inbox else portee.AUCUN_PROJET
    return portee.projet(compte, scope)[1].id


def _projets_du_compte(request: Request) -> frozenset[str]:
    """Tous les projets du compte de la session, quelle que soit la portée affichée :
    la limite au-delà de laquelle un nom de voix n'est jamais rendu (Q11)."""
    return portee.tous_mes_projets(portee.utilisateur(request))


def _trash_items_from_view_scope(request: Request, view_scope: str = "") -> list[dict]:
    ids = _portee_avec_herites(request, view_scope)
    return [item for item in library.trash_items() if item.get("project_id") in ids]


def _trash_item_in_scope(
    request: Request, archived_name: str, view_scope: str = "",
) -> Optional[dict]:
    safe_name = Path(archived_name).name
    if safe_name != archived_name:
        return None
    return next(
        (
            item for item in _trash_items_from_view_scope(request, view_scope)
            if item["name"] == safe_name
        ),
        None,
    )


def _require_document_scope(name: str, project_id: project_store.Portee) -> None:
    """Un document adressé par identifiant doit vivre dans la portée demandée.

    404 et non 403 : un 403 confirmerait au demandeur que le document EXISTE
    dans un autre projet, ce qui est déjà une fuite.

    `library.visible_in_project` résout le nom par les quatre clés de
    `transcript_path` (nom, document_id, source, titre) puis interroge le
    DOSSIER. Le `project_id` du front-matter est déclaratif : il voyage avec un
    .md recopié et rendrait celui-ci visible depuis son projet d'origine.
    """
    if not library.visible_in_project(name, project_id):
        raise HTTPException(404, "Transcript introuvable dans cette portée")


def _plan_dans_la_portee(name: str, project_id: project_store.Portee) -> list[dict]:
    """Le plan d'archivage, RÉDUIT aux cibles visibles depuis `project_id`.

    Leçon sur la forme du garde plutôt que sur son emplacement :
    `_require_document_scope` couvrait le document
    DEMANDÉ, pas les cibles DÉRIVÉES. Or `library.archive_plan` compose sa liste
    à partir du `project_id` DÉCLARÉ dans le front-matter — celui-là même que le
    cloisonnement a décidé de ne pas croire.

    Un `.md` recopié dans un dossier en gardant le projet de son origine était
    donc autorisé, et son plan tirait les documents de l'autre projet : le GET
    en divulguait le nom et le titre, le DELETE les archivait pour de bon.

    La propriété à tenir est donc plus forte : toute cible d'une mutation
    composée doit être visible dans la portée qui l'autorise.

    On RÉDUIT plutôt que de refuser : un document mal rangé est une anomalie du
    corpus, déjà signalée par `misplaced_documents` en maintenance. Refuser
    interdirait à quelqu'un d'archiver SON document à cause du désordre d'un
    autre. Ce qui est retiré n'est pas caché — le plan rend `excluded`.
    """
    return [
        item for item in library.archive_plan(name)
        if library.visible_in_project(item["name"], project_id)
    ]


def _reunion_de_la_portee(meeting_id: str, portee_appelant: project_store.Portee) -> dict:
    """LA réunion de l'appelant (`library.reunion_dans_la_portee`) : ses documents dans UN
    projet de sa portée, tous visibles. Le document homonyme d'un autre projet ou d'un autre
    compte n'en fait jamais partie et ne la bloque pas. 404 si aucun projet de la portée ne
    la porte ; 409 si plusieurs de ses projets la portent (désigner le projet)."""
    try:
        meeting = library.reunion_dans_la_portee(meeting_id, portee_appelant)
    except library.ReunionAmbigue as exc:
        raise HTTPException(409, "Plusieurs de vos projets portent cette réunion : choisissez "
                                 "le projet (portée) ou passez par l'un de ses documents.") from exc
    if meeting is None:
        raise HTTPException(404, "Réunion introuvable dans cette portée")
    return meeting


def _require_meeting_scope(meeting: dict, project_id: project_store.Portee) -> None:
    """Une réunion n'est dans la portée que si TOUS ses documents y sont.

    Exiger l'unanimité plutôt qu'un seul membre : ces routes détruisent ou
    déplacent la réunion ENTIÈRE. Se contenter d'un membre visible laisserait
    une portée agir sur les documents d'une autre.
    """
    names = [
        item["name"]
        for item in (*meeting.get("transcripts", []), *meeting.get("reports", []))
    ]
    if not names or not all(
        library.visible_in_project(name, project_id) for name in names
    ):
        raise HTTPException(404, "Réunion introuvable dans cette portée")


def _proprietaire_du_dossier(directory: Path) -> Optional[str]:
    """Le compte propriétaire du projet de ce dossier (manifeste), ou None."""
    try:
        return project_store.load(directory).owner
    except project_store.ProjectManifestError:
        return None


def _destination_projet(request: Request, destination_project_id: str = ""):
    """Le dossier ET le projet d'une destination d'écriture (identité et nom, pour le suivi)."""
    if (destination_project_id or "").strip() == ALL_PROJECTS_SCOPE:
        raise HTTPException(400, "« Tous les projets » est une portée, pas une destination")
    return portee.projet(portee.utilisateur(request), destination_project_id, ecriture=True)


def _destination_project_dir(request: Request, destination_project_id: str = "") -> Path:
    """Résout une destination d'écriture parmi les projets du compte ; vide =
    son « Non classé », créé s'il manque ; `all` n'est jamais accepté."""
    return _destination_projet(request, destination_project_id)[0]


# Résultat de la réindexation de démarrage. Sans cet état, un échec ne laissait
# qu'une ligne sur stdout : l'application servait ensuite un index figé en
# affirmant que tout allait bien.
def _fournisseur_valide(brut: str) -> str:
    """Refuse un fournisseur inconnu en 400 plutôt que de retomber sur Ollama.

    Une valeur périmée dans le `localStorage` de l'interface faisait changer de
    moteur en silence : l'utilisateur croyait parler à Mistral et parlait à
    Qwen. Un défaut permissif est ici un mensonge, pas une commodité.
    """
    # Le défaut vient du registre. Il était écrit `"ollama"` ici : après la
    # sortie d'Ollama, une valeur VIDE — cas courant d'un client qui n'envoie
    # pas le champ — serait devenue un 400 au lieu du comportement par défaut.
    valeur = (brut or "").strip() or llm.FOURNISSEUR_DEFAUT
    try:
        return llm.valider_fournisseur(valeur)
    except llm.FournisseurInconnu as exc:
        raise HTTPException(400, str(exc)) from exc


# Le vocabulaire fermé de `core/sensitivity` : une valeur inconnue est refusée (422),
# au PATCH comme à la transcription, au lieu d'être ramenée à `standard`, ce qui
# rétrogradait en silence un document ultra-confidentiel sur une faute de frappe
# (T2). La lecture, elle, reste douce (`sensitivity.normaliser`).
_SensibiliteDemandee = sensitivity.Sensibilite


def _refuser_le_cloud(sensibilite: object, *, local: bool, proprietaire: Optional[str]) -> None:
    """403 si un document de cette sensibilité partirait vers un fournisseur cloud.

    La règle est `sensitivity.verifier_envoi`, avec l'autorisation du PROPRIÉTAIRE
    du document (`proprietaire`, lu dans le manifeste de son projet), jamais celle
    du compte qui appelle. Chaque route l'appelle avant toute lecture destinée au
    modèle, toute réservation de travail et tout envoi ; le cœur la réapplique
    juste avant chaque appel au moteur, réglage relu.
    """
    if not sensitivity.cloud_refuse(sensibilite, local=local):
        return  # rien à autoriser : `auth.db` n'est lu que pour un envoi ultra-confidentiel
    try:
        sensitivity.verifier_envoi(sensibilite, local=local,
                                   autorise=sensitivity.autorise(proprietaire))
    except sensitivity.EnvoiRefuse as exc:
        raise HTTPException(403, sensitivity.REFUS_CLOUD) from exc


@app.exception_handler(sensitivity.EnvoiRefuse)
async def _envoi_refuse(_request: Request, _exc: sensitivity.EnvoiRefuse) -> JSONResponse:
    """Un refus du cœur (réglage désactivé entre la garde de route et l'envoi) : le même 403."""
    return JSONResponse({"detail": sensitivity.REFUS_CLOUD}, status_code=403)


ETAT_DEMARRAGE: dict = {
    "index_ok": True, "detail": "",
    # Ces deux-là n'existaient pas. Leurs échecs ne partaient qu'en
    # `print`, et `/api/health` répondait « ok » quoi qu'il arrive. On ne
    # pouvait donc pas distinguer « index vide » de « backfill échoué ».
    # `None` = pas encore tenté, et c'est une troisième valeur nécessaire :
    # le backfill sémantique est asynchrone.
    "voix_ok": None, "voix_detail": "",
    "semantic_ok": None, "semantic_detail": "",
}


def _startup_finops() -> None:
    """Initialise le catalogue FinOps embarqué (tarifs officiels + alias + hypothèses locales)
    au démarrage normal — ZÉRO action manuelle. Idempotent, best-effort : un échec n'empêche
    JAMAIS le démarrage, la STT ni les rapports (l'init avale ses propres erreurs). Ne reprice
    jamais l'historique.
    """
    try:
        from core import usage_store  # noqa: PLC0415 — import local, sans coût sur les autres chemins
        res = usage_store.initialize_catalogue()
        if res.get("ok"):
            print(f"[usage] catalogue FinOps initialisé au démarrage : {res}")
        else:
            print("[usage] catalogue FinOps non initialisé (best-effort) — sans effet sur le produit")
    except Exception as exc:  # noqa: BLE001 — la télémétrie ne peut JAMAIS casser le démarrage
        try:
            print(f"[usage] init FinOps ignorée au démarrage (best-effort) : {type(exc).__name__}")
        except Exception:  # noqa: BLE001
            pass


def _reindex_faits() -> None:
    """Faits ECL : la table dérive des fichiers `output/.ecl/`, comme l'index des `.md`.

    Option des faits coupée (`config.FAITS`) : pas de réindex, les fichiers restent tels quels.
    """
    if not ecl.actives():
        print("[ecl] faits de la réunion désactivés : pas de reindex des faits")
        return
    try:
        print(f"[ecl] reindex au démarrage : {ecl.reindexer()}")
    except Exception as exc:  # noqa: BLE001
        print(f"[ecl] reindex ignoré : {exc}")


def _startup_reindex() -> None:
    """Synchronise l'index SQLite avec output/ au démarrage, puis backfill sémantique."""
    # Installation neuve : crée l'identité du projet système. Sur un corpus
    # ancien non migré, `ensure_inbox` échoue au lieu d'inventer silencieusement
    # un nouvel ID ; le démarrage est volontairement bloqué.
    project_store.ensure_inbox(library.OUTPUT_DIR)
    try:
        stats = library.reindex()
        print(f"[index] reindex au démarrage : {stats}")
        ETAT_DEMARRAGE["index_ok"] = True
        ETAT_DEMARRAGE["detail"] = ""
    except Exception as exc:  # noqa: BLE001
        # `library.reindex()` est TOUT-OU-RIEN : un seul .md invalide laisse le
        # corpus entier non réindexé. C'est un bon choix — un index à moitié
        # reconstruit serait pire. Mais l'échec ne partait qu'en `print` sur la
        # sortie standard : l'application démarrait, `/api/health` répondait
        # « ok », et l'utilisateur pouvait travailler des heures sur un index
        # figé sans le savoir. On le retient pour que l'interface le dise.
        print(f"[index] reindex ignoré : {exc}")
        ETAT_DEMARRAGE["index_ok"] = False
        ETAT_DEMARRAGE["detail"] = str(exc)

    _reindex_faits()

    # Rattachement des voix (empreintes lues dans le front-matter des .md, pur
    # Python, pas de dépendance externe → synchrone et best-effort).
    try:
        stats = voiceprint.reindex_all()
        ETAT_DEMARRAGE["voix_ok"] = True
        ETAT_DEMARRAGE["voix_detail"] = ""
        print(f"[voix] backfill : {stats}")
    except Exception as exc:  # noqa: BLE001
        ETAT_DEMARRAGE["voix_ok"] = False
        ETAT_DEMARRAGE["voix_detail"] = str(exc)
        print(f"[voix] backfill ignoré : {exc}")

    def _semantic_backfill() -> None:
        # En tâche de fond : les embeddings peuvent être lents.
        #
        # Pas de garde `llm_ollama.is_available()` ici : le backend
        # d'embeddings est LOCAL par défaut depuis `core/embed.py` et ne passe
        # plus par Ollama. Un corpus en mode Mistral-only, ou simplement une
        # machine sans Ollama, n'était donc jamais backfillé au démarrage — la
        # recherche par le sens restait vide sans que rien ne le dise.
        #
        # Le reste de la garde est parti avec le repli d'embeddings Ollama
        # lui-même : il n'y a plus de configuration à vérifier.
        try:
            stats = semantic.reindex_all()
            ETAT_DEMARRAGE["semantic_ok"] = True
            ETAT_DEMARRAGE["semantic_detail"] = ""
            print(f"[semantic] backfill : {stats}")
        except Exception as exc:  # noqa: BLE001
            ETAT_DEMARRAGE["semantic_ok"] = False
            ETAT_DEMARRAGE["semantic_detail"] = str(exc)
            print(f"[semantic] backfill ignoré : {exc}")

    threading.Thread(target=_semantic_backfill, daemon=True).start()


# ---------- Méta ----------

@app.get("/api/health")
def health() -> dict:
    """État réel du démarrage, pas un « ok » de complaisance.

    Répondait toujours `{"status": "ok"}` : un index figé, un backfill vocal
    raté ou une recherche sémantique jamais construite passaient inaperçus
    pendant des heures.

    `degraded` plutôt que `error` : l'application RÉPOND, une partie de ses
    fonctions est seulement dégradée. Confondre les deux ferait redémarrer un
    service qui marche.
    """
    composants = {
        "index": ETAT_DEMARRAGE["index_ok"],
        "voix": ETAT_DEMARRAGE["voix_ok"],
        "semantique": ETAT_DEMARRAGE["semantic_ok"],
    }
    details = {
        cle: ETAT_DEMARRAGE[champ]
        for cle, champ in (("index", "detail"), ("voix", "voix_detail"),
                           ("semantique", "semantic_detail"))
        if ETAT_DEMARRAGE[champ]
    }
    degrade = any(v is False for v in composants.values())
    return {
        "status": "degraded" if degrade else "ok",
        "composants": composants,   # `None` = pas encore terminé (asynchrone)
        "details": details,
    }


@app.get("/api/config")
def get_config(request: Request) -> dict:
    """Infos d'environnement pour l'UI (moteurs, disponibilité déclarée…).

    `default_project_id` : le « Non classé » du compte, ou "" s'il n'existe pas encore.
    Une lecture ne le crée pas ; une destination vide le crée au premier envoi.
    """
    inbox = portee.non_classe(portee.utilisateur(request))
    # Sondé UNE fois : trois clés en dérivent, et sonder par clé multiplierait
    # les allers-retours vers le démon Ollama à chaque chargement de page.
    llm_ready = llm.disponibilite_declaree()
    return {
        "engines": list(stt.ENGINES),
        "default_engine": "whisper",
        # Depuis le type fermé de `core/llm.py` : l'interface ne propose plus
        # que ce que le backend accepte réellement.
        # `llm.propose` : hors `HORS_INTERFACE`, et prêt quand le moteur l'exige (un
        # serveur compatible n'est proposé qu'avec une URL et un modèle).
        "llm_providers": [p for p in llm.FOURNISSEURS if p != "cloud" and llm.propose(p)],
        # Option des faits de la réunion : coupée, l'interface masque l'onglet « Faits ».
        "faits": ecl.actives(),
        # Classement LOCAL vs DISTANT dérivé de la CAPACITÉ (`llm.est_local`),
        # jamais d'un nom de fournisseur en dur : l'UI garde la distinction produit
        # « local ↔ cloud » sans supposer que « cloud == Mistral ». Un moteur cloud
        # ajouté (OpenAI) y apparaît sans toucher cette route.
        "local_providers": [p for p in llm.FOURNISSEURS
                            if p != "cloud" and llm.est_local(p)],
        # Serveurs hébergés par l'utilisateur hors de la boucle locale : « cloud privé ».
        "private_cloud_providers": [p for p in llm.FOURNISSEURS
                                    if p != "cloud" and llm.auto_heberge(p) and not llm.est_local(p)],
        # Le mode local est MLX (Ollama sort de la cible). Le défaut
        # vient du registre, jamais d'un nom écrit ici.
        "default_llm_provider": llm.FOURNISSEUR_DEFAUT,
        # Vocabulaire FERMÉ de sensibilité, depuis le type de `core/sensitivity`.
        # MÉTADONNÉE seulement : l'UI le propose, rien ne l'applique à l'exécution
        # (pas de routage). Exposé ici pour qu'un futur étage de politique et
        # l'UI parlent la même liste, sans « valeurs en dur » côté client.
        "sensitivity_levels": list(sensitivity.SENSIBILITES),
        "default_sensitivity": sensitivity.SENSIBILITE_DEFAUT,
        "default_project": library.DEFAULT_PROJECT,
        "default_project_id": inbox.id if inbox else "",
        "whisper_model": config.WHISPER_MODEL,
        "llm_model": config.LLM_MODEL,
        "mistral_chat_model": config.MISTRAL_CHAT_MODEL,
        # Le panneau expert affichait `whisper_model` quel que soit le moteur :
        # un utilisateur en mode cloud y lisait le nom d'un modèle qui ne
        # tournait pas.
        "mistral_stt_model": config.MISTRAL_STT_MODEL,
        "max_upload_mb": config.MAX_UPLOAD_MB,
        "supported_extensions": sorted(config.SUPPORTED_EXTS),
        "diarization_available": bool(config.HF_TOKEN),
        # LA source : `{fournisseur → prêt}` construit depuis le registre, donc
        # un moteur ajouté y apparaît sans qu'on touche cette route.
        #
        # Ce dictionnaire remplace un calcul qui NOMMAIT deux implémentations
        # (`llm_ollama.is_available() or bool(MISTRAL_API_KEY)`) et ignorait le
        # troisième moteur : MLX pouvait être au registre et rester invisible
        # dans la disponibilité annoncée. Même défaut que le modèle résolu par
        # un `if/else`, un étage plus haut.
        #
        # « Déclaré » et non « prouvé » : aucun moteur n'est démarré ici. La
        # route est appelée à chaque chargement de l'interface, et prouver
        # MLX y chargerait 2,1 Go par ouverture de page.
        "llm_ready": llm_ready,
        # `{fournisseur → modèle réellement servi}`, depuis la résolution de
        # `llm.modele_servi`. Sans lui, l'interface n'avait AUCUN nom à afficher pour MLX :
        # elle ne connaissait que `llm_model` (le modèle Ollama) et
        # `mistral_chat_model`. Elle aurait donc nommé Qwen-Ollama devant un
        # utilisateur servi par MLX — le même mensonge que celui des documents,
        # à l'écran cette fois.
        "llm_models": {nom: llm.modele_servi(nom) for nom in llm.MOTEURS},
        "llm_available": any(llm_ready.values()),
        # `ollama_available` et `mistral_api_key_configured` sont RETIRÉES.
        # Elles nommaient deux moteurs dans une charge utile qui
        # décrit désormais le registre entier, et `llm_ready` les remplace
        # exactement. Les garder aurait obligé à publier une clé `ollama` pour
        # un fournisseur qui n'existe plus — donc à mentir pour rester
        # compatible.
    }


# ---------- Transcription (job + progression SSE) ----------

MODES_DE_CAPTURE = ("visio", "replay")
LONGUEUR_MAX_APPLICATION = 80


def _provenance_capture(application: str, mode: str, capture_id: str = "") -> Optional[dict]:
    """Provenance d'un enregistrement du compagnon de capture, ou `None` sans elle.

    Refusée en 400 plutôt que corrigée : elle est écrite dans le `.md`, qui fait
    foi, et une valeur inventée y resterait. `capture_id` (un UUID) y est rangé aussi :
    c'est ce qui rend le renvoi d'une capture idempotent, même après un redémarrage.
    """
    application = " ".join((application or "").split())
    mode = (mode or "").strip().lower()
    capture_id = (capture_id or "").strip().lower()
    if capture_id and not _UUID.fullmatch(capture_id):
        raise HTTPException(400, "Identifiant de capture invalide : un UUID est attendu")
    if mode and mode not in MODES_DE_CAPTURE:
        raise HTTPException(400, f"Mode de capture inconnu : {mode!r} "
                                 f"(attendu : {', '.join(MODES_DE_CAPTURE)})")
    if len(application) > LONGUEUR_MAX_APPLICATION or not application.isprintable():
        raise HTTPException(400, "Application de capture invalide : "
                                 f"{LONGUEUR_MAX_APPLICATION} caractères imprimables au plus")
    provenance = {"application": application or None, "mode": mode or None,
                  "capture_id": capture_id or None}
    return {k: v for k, v in provenance.items() if v} or None


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# Où chercher `capture_id` dans un .md : le front matter est en tête, jamais au-delà.
_TETE_DU_FRONT_MATTER = 16 * 1024


def _reunion_de_la_capture(compte: auth.Utilisateur, capture_id: str) -> Optional[dict]:
    """Le transcript DU COMPTE déjà écrit pour cette capture (`{"meeting", "meeting_id", "project_id"}`), ou `None`.

    La recherche porte sur tous les projets du compte (la destination a pu changer entre deux
    essais) et jamais sur ceux des autres. Un document encore marqué `interrupted` (écriture
    précoce d'un traitement qui tourne ou qui a échoué) ne compte pas : renvoyer la capture après
    un échec doit la retraiter.
    """
    for dossier, _projet in portee.projets_visibles(compte):
        try:
            fichiers = sorted(p for p in dossier.glob("*.md") if p.is_file())
        except OSError:
            continue
        for fichier in fichiers:
            try:
                with fichier.open("r", encoding="utf-8", errors="replace") as f:
                    tete = f.read(_TETE_DU_FRONT_MATTER)
                if capture_id not in tete:
                    continue
                meta, _ = library._split_frontmatter(fichier.read_text(encoding="utf-8"))
            except OSError:
                continue
            capture = meta.get("capture")
            if (isinstance(capture, dict) and capture.get("capture_id") == capture_id
                    and not meta.get("interrupted")):
                return _reponse_deja_recue(fichier.name, meta)
    return None


def _reponse_deja_recue(nom: str, identite: dict) -> dict:
    """`{"deja_recue": true, "meeting": nom}` + l'identité de la réunion quand on la connaît, pour que
    le compagnon l'ouvre elle-même (comme pour un traitement fini)."""
    reponse = {"deja_recue": True, "meeting": nom}
    for cle in ("meeting_id", "project_id"):
        if isinstance(identite.get(cle), str) and identite[cle]:
            reponse[cle] = identite[cle]
    return reponse


def _deja_recue_par_travail(job) -> dict:
    """Réponse « déjà reçue » pour un traitement du compte qui porte la même capture."""
    with job.lock:
        resultat = job.result if job.status == "done" and isinstance(job.result, dict) else {}
    nom = resultat.get("name")
    return _reponse_deja_recue(nom, resultat) if nom else {"deja_recue": True, "job_id": job.id}


@app.post("/api/transcribe")
async def transcribe(
    request: Request,
    file: UploadFile,
    engine: str = Form("whisper"),
    language: str = Form(""),
    mistral_api_key: str = Form(""),
    llm_provider: str = Form(""),
    diarize: bool = Form(False),
    profile: bool = Form(False),
    report_on: bool = Form(True),
    timestamps: bool = Form(True),
    # Vocabulaire fermé, comme le PATCH (T2) : une valeur inconnue rend 422 avant
    # toute réservation et tout envoi ; absente (ou vide, que FastAPI lit comme absente),
    # elle vaut `standard`.
    sensitivity_level: _SensibiliteDemandee = Form(sensitivity.SENSIBILITE_DEFAUT),
    destination_project_id: str = Form(""),
    capture_application: str = Form(""),
    capture_mode: str = Form(""),
    capture_id: str = Form(""),
) -> dict:
    # La provenance d'abord : invalide, elle est refusée avant tout travail. Puis la capture déjà
    # reçue (réponse perdue, renvoi) : elle ne dépend ni de la destination ni de la place.
    capture = _provenance_capture(capture_application, capture_mode, capture_id)
    capture_id = (capture or {}).get("capture_id", "")
    compte = portee.utilisateur(request)
    if capture_id:
        deja = _reunion_de_la_capture(compte, capture_id)
        if deja:
            return deja
    # Refuse une portée, un projet ou un fournisseur inconnu AVANT de réserver
    # un job et d'uploader le fichier. Valider dans le job de fond serait trop
    # tard : la réponse HTTP est déjà partie en 200, et l'utilisateur ne saurait
    # qu'à la fin que son choix de moteur était invalide.
    target_dir, projet_destination = _destination_projet(request, destination_project_id)
    llm_provider = _fournisseur_valide(llm_provider)
    # Avant la réservation et l'envoi : la transcription, puis le profil et le CR
    # quand ils sont demandés, restent locaux pour un document ultra-confidentiel,
    # sauf si le propriétaire du projet de destination autorise le cloud.
    proprietaire = _proprietaire_du_dossier(target_dir)
    _refuser_le_cloud(sensitivity_level, local=stt.est_local(engine), proprietaire=proprietaire)
    if report_on or profile:
        _refuser_le_cloud(sensitivity_level, local=llm.est_local(llm_provider),
                          proprietaire=proprietaire)
    if report_on and not timestamps:
        # Chaque phrase d'un CR cite `[source HH:MM:SS]` : sans horodatage, il
        # finirait sur zéro unité, après la transcription entière.
        raise HTTPException(400, "Un compte-rendu exige les horodatages : activez-les "
                                 "ou retirez le compte-rendu.")

    safe_name = Path(file.filename or "upload").name
    if safe_name in {"", ".", ".."}:
        safe_name = "upload"
    suivi = dict(
        # Le suivi : de quoi montrer ce traitement depuis n'importe quel écran (tranche B).
        titre=Path(safe_name).stem, projet_id=projet_destination.id,
        projet_nom=projet_destination.name,
        jeton=getattr(request.state, "jeton_id", None),
        plan=plan_etapes(diarize=diarize, profile=profile, report_on=report_on))
    try:
        if capture_id:
            # Recherche et création sous le même verrou : deux envois rapprochés de la même capture
            # ne donnent qu'un traitement. Un traitement en échec ne compte pas.
            job, cree = jobs.create_or_find_capture(compte.id, capture_id, **suivi)
            if not cree:
                return _deja_recue_par_travail(job)
        else:
            job = jobs.create(owner=compte.id, nature="transcription", **suivi)
    except JobsBusy as exc:
        raise HTTPException(429, str(exc))

    job_upload_dir = UPLOAD_DIR / job.id
    job_upload_dir.mkdir(parents=True, exist_ok=True)
    dest = job_upload_dir / safe_name
    # Écriture EN FLUX avec plafond : `await file.read()` chargeait tout le
    # fichier en mémoire, sans limite (une vidéo peut faire des Go).
    limit = config.MAX_UPLOAD_MB * 1024 * 1024
    written = 0
    try:
        with dest.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > limit:
                    raise HTTPException(
                        413, f"Fichier trop volumineux (limite : {config.MAX_UPLOAD_MB} Mo)"
                    )
                out.write(chunk)
    except Exception:
        # L'upload n'aboutira pas : rendre le slot, sinon le job reste « pending »
        # pour toujours et bloque les transcriptions suivantes.
        jobs.discard(job)
        dest.unlink(missing_ok=True)
        job_upload_dir.rmdir()
        raise

    def target_locked(cb):
        try:
            out_path, content = pipeline.run(
                dest,
                engine_name=engine,
                language=language.strip() or None,
                mistral_api_key=mistral_api_key.strip() or None,
                llm_provider=llm_provider,  # déjà validé à la frontière
                diarize_on=diarize,
                profile_on=profile,
                report_on=report_on,
                timestamps=timestamps,
                sensitivity=sensitivity_level,
                capture=capture,
                progress=cb,
                output_dir=target_dir,
                # Texte affiché au fil de l'eau (transcription progressive).
                on_partial=lambda chunk: jobs.push(job, {"type": "partial", "text": chunk}),
            )
            library.sync_file(out_path)  # met à jour l'index FTS immédiatement
            # Ces deux index sont DÉRIVÉS : leur échec ne doit pas perdre le
            # Markdown, qui fait foi. Mais il était avalé par un `pass` nu —
            # or l'index sémantique passe TOUJOURS par Ollama (semantic.py
            # appelle llm_ollama.embed sans routage). En mode Mistral Cloud sans
            # Ollama, la transcription réussissait puis « Recherche par le sens »
            # et l'Assistant restaient muets, sans que rien ne l'explique.
            avertissements: list[str] = []
            for etiquette, indexer in (
                ("recherche par le sens", semantic.index_name),
                ("registre des voix", voiceprint.index_name),
            ):
                try:
                    indexer(out_path.name)
                except Exception as exc:  # noqa: BLE001 — le Markdown reste intact
                    avertissements.append(f"{etiquette} indisponible : {exc}")
                    jobs.push(job, {"type": "warning", "text": avertissements[-1]})
            resultat = {"name": out_path.name, "content": content}
            # L'identité de la réunion, pour que le compagnon ouvre la réunion elle-même
            # (`?projet=…&reunion=…`) plutôt que la bibliothèque.
            meta, _ = library._split_frontmatter(out_path.read_text(encoding="utf-8"))
            for cle in ("meeting_id", "project_id"):
                if isinstance(meta.get(cle), str) and meta[cle]:
                    resultat[cle] = meta[cle]
            # Une diarisation manquée laisse le transcript sans locuteurs : l'enrichissement
            # est optionnel (la transcription survit), mais son échec se dit ici aussi.
            if isinstance(meta.get("diarization_error"), str) and meta["diarization_error"].strip():
                avertissements.append(f"diarisation indisponible, locuteurs non identifiés : {meta['diarization_error']}")
                jobs.push(job, {"type": "warning", "text": avertissements[-1]})
            if avertissements:  # absent quand tout va bien : pas de bruit inutile
                resultat["warnings"] = avertissements
            return resultat
        finally:
            dest.unlink(missing_ok=True)
            try:
                job_upload_dir.rmdir()
            except OSError:
                pass

    def target(cb):
        # Un seul jeton couvre STT, puis les deux indexations dérivées. Sans
        # cette enveloppe, pipeline.run rendait le jeton avant semantic/voix.
        # L'attente du créneau se VOIT : `en_attente` tant qu'un autre calcul lourd occupe la machine.
        def _en_attente() -> None:
            with job.lock:
                job.en_attente = True

        def _obtenu() -> None:
            with job.lock:
                job.en_attente = False

        with resources.heavy(wait=config.ATTENTE_DE_FOND_API_S, label="traitement",
                             on_wait=_en_attente, on_acquired=_obtenu):
            return target_locked(cb)

    jobs.run(job, target)
    return {"job_id": job.id}


def _job_du_compte(request: Request, job_id: str):
    """Un job n'est lisible que par le compte qui l'a lancé.

    Avec un JETON d'appareil (le compagnon), seulement par le jeton qui l'a lancé : il suit ses propres
    captures, jamais un traitement lancé par la session ni par un autre jeton du même compte (strict
    nécessaire). Même réponse 404 que pour un job inconnu : rien ne distingue les deux.
    """
    job = jobs.get(job_id)
    if not job or job.owner != portee.utilisateur(request).id:
        raise HTTPException(404, "Job inconnu")
    if getattr(request.state, "par_jeton", False):
        jeton = getattr(request.state, "jeton_id", None)
        if jeton is None or job.jeton != jeton:
            raise HTTPException(404, "Job inconnu")
    return job


@app.get("/api/jobs")
def traitements_du_compte(request: Request) -> dict:
    """Les traitements du compte connecté : les actifs d'abord, puis l'historique récent.

    Même contrôle de propriétaire que `_job_du_compte`. Avec un JETON d'appareil, la liste se limite
    aux traitements nés de ce jeton : aucun titre, projet ni état d'une autre réunion. Les comptes
    (`actifs`, `en_attente`) ne comptent jamais que les traitements du compte.
    """
    du_compte = jobs.list_for(portee.utilisateur(request).id)
    attente = [j for j in du_compte if j.status == "pending"
               or (j.status == "running" and j.en_attente)]
    en_cours = [j for j in du_compte if j.status == "running" and not j.en_attente]
    if getattr(request.state, "par_jeton", False):
        jeton = getattr(request.state, "jeton_id", None)
        visibles = [j for j in du_compte if jeton is not None and j.jeton == jeton]
    else:
        visibles = du_compte
    # Les actifs d'abord (stable : l'ordre récent → ancien est conservé dans chaque groupe).
    visibles.sort(key=lambda j: j.status not in ("pending", "running"))
    return {
        "traitements": [JobManager.entree_liste(j) for j in visibles],
        # « N en cours · M en attente » : un traitement qui attend le créneau n'est PAS en cours.
        "actifs": len(en_cours),
        "en_attente": len(attente),
    }


def _vue_du_compagnon(request: Request, donnees: dict) -> Optional[dict]:
    """Ce qu'un jeton d'appareil voit d'un travail ou d'un de ses événements.

    L'état, la progression, les messages, l'erreur, le nom et l'identité de la
    réunion (`meeting_id`, `project_id`, pour l'ouvrir) ; jamais le texte du
    transcript ni du CR. Le jeton sert à déposer un
    enregistrement, pas à lire une réunion. `None` : événement retenu en entier
    (le texte partiel d'une transcription).
    """
    if not getattr(request.state, "par_jeton", False):
        return donnees
    if donnees.get("type") == "partial":
        return None
    vue = dict(donnees)
    if isinstance(vue.get("result"), dict):
        vue["result"] = {k: vue["result"][k] for k in ("name", "warnings", "meeting_id", "project_id")
                         if k in vue["result"]}
    return vue


@app.get("/api/jobs/{job_id}")
def job_status(request: Request, job_id: str) -> dict:
    job = _job_du_compte(request, job_id)
    return _vue_du_compagnon(request, jobs.snapshot(job))


@app.get("/api/jobs/{job_id}/events")
async def job_events(
    request: Request,
    job_id: str,
    last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    job = _job_du_compte(request, job_id)
    try:
        cursor = int(last_event_id) if last_event_id is not None else 0
        if cursor < 0:
            raise ValueError
    except ValueError as exc:
        raise HTTPException(400, "Last-Event-ID invalide") from exc

    async def gen():
        nonlocal cursor
        loop = asyncio.get_running_loop()
        last_activity = loop.time()
        yield "retry: 1000\n\n"
        while True:
            new, status = jobs.events_after(job, cursor)
            for event in new:
                cursor = event.id
                vue = _vue_du_compagnon(request, event.payload)
                if vue is None:
                    continue
                payload = json.dumps(vue, ensure_ascii=False)
                yield f"id: {event.id}\ndata: {payload}\n\n"
                last_activity = loop.time()
                if event.payload.get("type") in ("done", "error"):
                    return
            if status in ("done", "error"):
                return
            now = loop.time()
            if now - last_activity >= SSE_HEARTBEAT_SECONDS:
                yield ": heartbeat\n\n"
                last_activity = now
            await asyncio.sleep(SSE_POLL_SECONDS)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------- Bibliothèque ----------

@app.get("/api/library")
def get_library(
    request: Request,
    q: str = "",
    sort: str = "modified",
    order: str = "desc",
    language: str = "",
    diarized: Optional[bool] = None,
    profiled: Optional[bool] = None,
    view_scope: str = "",
) -> list[dict]:
    project_id = _portee_lecture(request, view_scope)
    return library.list_transcripts(
        q=q or None, sort=sort, order=order,
        language=language or None, diarized=diarized, profiled=profiled,
        project_id=project_id,
    )


@app.get("/api/library/languages")
def get_languages(request: Request) -> list[str]:
    return library.languages(_portee(request, ALL_PROJECTS_SCOPE))


@app.get("/api/search")
def search(request: Request, q: str = "", mode: str = "fts", limit: int = 30, view_scope: str = "") -> list[dict]:
    limit = max(1, min(limit, 200))  # borne : évite une requête démesurée
    """Recherche dans les transcripts. mode='fts' (mots exacts) ou 'semantic' (sens).

    `view_scope` vaut un ID projet ; `all` élargit explicitement à tous les projets du compte.
    """
    project_id = _portee_lecture(request, view_scope)
    if mode == "semantic":
        try:
            return semantic.search(q, k=limit, project_id=project_id)
        except resources.ResourcesBusy as exc:
            # 429 comme pour les jobs : la machine ne traite qu'une tâche
            # lourde à la fois, et un refus explicite vaut mieux qu'une
            # attente inexpliquée de plusieurs minutes.
            raise HTTPException(429, str(exc))
        except llm.LLMError as exc:
            raise HTTPException(503, f"Recherche sémantique indisponible : {exc}")
    return library.search(q, limit=limit, project_id=project_id)


@app.post("/api/search/reindex")
def reindex_semantic(request: Request, view_scope: str = "") -> dict:
    """(Re)construit l'index sémantique pour les transcripts pas encore indexés."""
    try:
        return semantic.reindex_all(project_id=_portee_maintenance(request, view_scope))
    except resources.ResourcesBusy as exc:
        raise HTTPException(429, str(exc))
    except llm.LLMError as exc:
        raise HTTPException(503, f"Réindexation indisponible : {exc}")


# ---------- RAG : interroger le corpus ----------

class RagScope(BaseModel):
    # `shared` : ce qui est partagé avec le compte (Q15).
    mode: Literal["project", "all", "shared"]
    project_id: Optional[str] = None


class AskBody(BaseModel):
    question: str
    scope: RagScope
    llm_provider: str = ""
    mistral_api_key: Optional[str] = None


@app.post("/api/ask")
def ask(request: Request, body: AskBody) -> dict:
    """Répond à une question à partir des transcripts (retrieval + réponse citée).

    `scope` est obligatoire : aucun élargissement implicite du corpus.
    """
    q = (body.question or "").strip()
    if not q:
        raise HTTPException(400, "Question vide")
    from core import rag
    compte = portee.utilisateur(request)
    if body.scope.mode == "shared":
        project_id = portee.lecture(compte, body.scope.project_id or portee.PARTAGES)
    elif body.scope.mode == "all":
        if body.scope.project_id is not None:
            raise HTTPException(400, "Une portée globale ne prend pas de project_id")
        project_id = portee.tous_mes_projets(compte)
    else:
        if not body.scope.project_id:
            raise HTTPException(400, "project_id requis pour une portée projet")
        project_id = portee.projet(compte, body.scope.project_id)[1].id
    # Validé ICI, pas dans `rag.ask` : `FournisseurInconnu` hérite de
    # `ValueError` et n'est capturé par aucun `except` de cette route — un
    # fournisseur inconnu sortait donc en 500 brut. Le refus du fournisseur
    # inconnu ne doit pas couvrir que `/api/transcribe`.
    fournisseur = _fournisseur_valide(body.llm_provider)
    try:
        return rag.ask(
            q,
            project_id=project_id,
            provider=fournisseur,
            mistral_api_key=body.mistral_api_key,
        )
    except rag.PassagesUltraConfidentiels as exc:
        raise HTTPException(403, str(exc)) from exc
    except resources.ResourcesBusy as exc:
        # 429 comme pour les jobs : la machine ne traite qu'une tâche
        # lourde à la fois, et un refus explicite vaut mieux qu'une
        # attente inexpliquée de plusieurs minutes.
        raise HTTPException(429, str(exc))
    except llm.LLMError as exc:
        raise HTTPException(503, f"Assistant indisponible : {exc}")


@app.post("/api/library/reindex")
def post_reindex(request: Request) -> dict:
    """Réindexe les `.md`, puis les faits ECL comme au démarrage : un transcript corrigé à la
    main y voit ses faits revérifiés (`ecl.reindexer`)."""
    _exiger_admin(request)
    stats = library.reindex()
    try:
        faits = ecl.reindexer()
    except Exception as exc:  # noqa: BLE001 — l'index des `.md` est fait ; l'échec des faits est dit
        faits = {"erreur": f"{type(exc).__name__}: {exc}"}
    return {**stats, "ecl": faits}


# ---------- Réunions (parent stable, documents enfants) ----------

@app.get("/api/meetings")
def get_meetings(
    request: Request,
    q: str = "",
    sort: str = "modified",
    order: str = "desc",
    language: str = "",
    diarized: Optional[bool] = None,
    profiled: Optional[bool] = None,
    view_scope: str = "",
) -> list[dict]:
    return library.list_meetings(
        q=q or None,
        sort=sort,
        order=order,
        language=language or None,
        diarized=diarized,
        profiled=profiled,
        project_id=_portee_lecture(request, view_scope),
    )


@app.get("/api/meetings/{meeting_id}")
def read_meeting(request: Request, meeting_id: str, view_scope: str) -> dict:
    return _reunion_de_la_portee(meeting_id, _portee_lecture(request, view_scope))


def _mode_du_modele(modele: Optional[str]) -> Optional[str]:
    """« local » ou « cloud » selon le fournisseur qui sert ce modèle, pour le compte rendu ou
    pour l'ECL (`ecl.modele_cloud`) ; None si aucun ne le sert."""
    for fournisseur in llm.MOTEURS:
        if modele and modele in (llm.modele_servi(fournisseur), ecl.modele_cloud(fournisseur)):
            return "local" if llm.est_local(fournisseur) else "cloud"
    return None


def _exiger_les_faits() -> None:
    """Option des faits coupée : la route répond sans rien lire ni lancer."""
    if not ecl.actives():
        raise HTTPException(404, ecl.DESACTIVES)


@app.get("/api/meetings/{meeting_id}/ecl")
def read_meeting_facts(request: Request, meeting_id: str, view_scope: str) -> dict:
    """Les faits ECL d'une réunion, dans la portée de lecture (`ecl.faits`).

    Même garde que la réunion elle-même ; `ecl.faits` écarte en plus l'archivé, le
    purgé et l'ultra-confidentiel en partage. Le mode (local ou cloud) vient du modèle
    noté dans le fichier qui fait foi ; un fait qui n'est pas de source cloud reconnue
    est « à vérifier ». Aucun taux de précision n'est rendu, quel que soit le modèle.

    `faits` ne contient que les faits actuels ; `perimes`, à part, ceux dont la source a
    changé depuis l'extraction, avec le motif de leur échec. Lecture seule : rien n'est
    revérifié ici (`ecl.reverifier` tourne à la synchronisation et au réindex).
    """
    _exiger_les_faits()
    project_id = _portee_lecture(request, view_scope)
    meeting = _reunion_de_la_portee(meeting_id, project_id)
    # Les faits des seuls documents de CETTE réunion (son projet), jamais d'un homonyme.
    documents = {d.get("id") for d in (*meeting["transcripts"], *meeting["reports"])}
    modeles: dict[str, Optional[str]] = {}
    partielles: dict[str, Optional[dict]] = {}

    def pour_l_ecran(fait: dict) -> dict:
        if fait["document_id"] not in modeles:
            contenu = ecl.lire_fichier(fait["document_id"]) or {}
            modeles[fait["document_id"]] = contenu.get("modele")
            # Extraction partielle (tranches en échec) : la marque du fichier qui fait foi.
            partielles[fait["document_id"]] = contenu.get("partielle")
        modele = modeles[fait["document_id"]]
        # Le mode est noté avec chaque fait ; un fichier d'avant les modes le déduit du modèle.
        mode = fait.get("mode") or _mode_du_modele(modele)
        return {
            **{cle: fait.get(cle) for cle in ("id", "document_id", "document", "type", "horodatage",
                                               "secondes", "citation", "enonce", "porteur", "locuteur",
                                               "fournisseur", "affiche", "motif_peremption")},
            "statut": fait.get("statut") or "actuel",
            "enonce_statut": "reformulé", "modele": modele, "mode": mode,
            "a_verifier": mode != "cloud",
            "partielle": partielles[fait["document_id"]],
        }

    return {"meeting_id": meeting_id,
            "faits": [pour_l_ecran(f) for f in ecl.faits(project_id, meeting_id=meeting_id)
                      if f["document_id"] in documents],
            "perimes": [pour_l_ecran(f) for f in ecl.faits(project_id, meeting_id=meeting_id,
                                                           statut="perime")
                        if f["document_id"] in documents]}


class EclRelanceBody(BaseModel):
    # Optionnel pour que la garde de portée réponde avant la validation : un autre
    # compte reçoit 404, pas le détail du corps attendu.
    provider: Optional[str] = None


@app.post("/api/meetings/{meeting_id}/ecl")
def rerun_meeting_facts(request: Request, meeting_id: str, body: EclRelanceBody, view_scope: str) -> dict:
    """Relance l'extraction des faits d'une réunion avec le fournisseur choisi (tâche de fond).

    Écriture : portée du compte seulement (`_portee`), jamais un partage ni un invité.
    Le fournisseur est l'un de ceux que l'écran propose (`llm.HORS_INTERFACE` exclu) ;
    un fournisseur cloud est refusé pour un document ultra-confidentiel. L'extraction
    précédente est gardée, datée (`ecl.versions`).
    """
    _exiger_les_faits()
    meeting = _reunion_de_la_portee(meeting_id, _portee(request, view_scope))
    fournisseur = body.provider or ""
    if fournisseur not in llm.FOURNISSEURS or fournisseur == "cloud" or not llm.propose(fournisseur):
        raise HTTPException(400, f"Fournisseur non proposé : {fournisseur}")
    transcripts = meeting.get("transcripts") or []
    if not transcripts:
        raise HTTPException(404, "Réunion sans transcript")
    retenu = transcripts[0]
    # La sensibilité EFFECTIVE de la réunion (`library.sensibilite_effective`) : un
    # transcript `standard` d'une réunion ultra-confidentielle reste ultra-confidentiel.
    envoi = library.envoi_du_document(retenu["name"])
    _refuser_le_cloud(envoi.sensibilite, local=llm.est_local(fournisseur), proprietaire=envoi.proprietaire)
    try:
        job = jobs.create(owner=portee.utilisateur(request).id, nature="faits")
    except JobsBusy as exc:
        raise HTTPException(429, str(exc))

    def target(cb):
        from core import telemetry  # noqa: PLC0415
        cb(0.05, "Extraction des faits de la réunion")
        # Une exécution FinOps à elle : hors de ce contexte, la télémétrie est un no-op,
        # et le coût d'une relance (Sonnet en cloud) échappait au suivi.
        with telemetry.execution(
            kind="ecl", document_id=retenu.get("id"), meeting_id=meeting_id,
            project_id=retenu.get("project_id"), provider=fournisseur,
            model=ecl.modele_cloud(fournisseur) or llm.modele_servi(fournisseur),
        ), telemetry.stage("ecl"):
            bilan = ecl.extraire(retenu["name"], provider=fournisseur)
        cb(1.0, "Faits extraits")
        return bilan

    jobs.run(job, target)
    return {"job_id": job.id}


class MeetingMoveBody(BaseModel):
    destination_project_id: str


@app.patch("/api/meetings/{meeting_id}/project")
def move_meeting(request: Request, meeting_id: str, body: MeetingMoveBody, view_scope: str) -> dict:
    """Déplace explicitement toute la réunion et chacun de ses documents.

    Sans borne, une portée pouvait ASPIRER chez elle la réunion d'une autre —
    un déplacement non consenti vaut une exfiltration.
    """
    source = _reunion_de_la_portee(meeting_id, _portee(request, view_scope))
    cible = portee.projet(portee.utilisateur(request), body.destination_project_id,
                          ecriture=True)[1]
    try:
        destination = library.move_meeting_to_project_id(meeting_id, cible.id,
                                                         source_project_id=source["project_id"])
    except (library.UnsafeProject, project_store.ProjectManifestError) as exc:
        raise HTTPException(400, str(exc))
    if destination is None:
        raise HTTPException(404, "Réunion introuvable ou conflit dans la destination")
    meeting = library.get_meeting(meeting_id, project_id=cible.id)
    if meeting is None:  # ne devrait jamais arriver après un déplacement réussi
        raise HTTPException(500, "Réunion déplacée mais absente de l'index")
    return meeting


@app.delete("/api/meetings/{meeting_id}")
def delete_meeting(request: Request, meeting_id: str, view_scope: str, hard: bool = False) -> dict:
    """Archive toute la réunion par défaut ; `hard=true` est irréversible.

    `view_scope` est OBLIGATOIRE : la route la plus destructrice du service ne
    doit pas pouvoir être appelée sans dire depuis où.
    """
    meeting = _reunion_de_la_portee(meeting_id, _portee(request, view_scope))
    # Rapports d'abord : une interruption ne doit jamais laisser un rapport
    # actif dont le transcript source a déjà disparu.
    names = [
        *sorted(item["name"] for item in meeting["reports"]),
        *sorted(item["name"] for item in meeting["transcripts"]),
    ]
    from core import purge

    if hard:
        failures = []
        for name in names:
            result = purge.purge_transcript(name)
            if not result["clean"]:
                failures.extend(result["errors"])
        if failures:
            raise HTTPException(500, "Suppression incomplète : " + " ; ".join(failures))
        return {"deleted_meeting": meeting_id, "documents": names}

    archived = []
    for name in names:
        result = purge.archive(name)
        if not result["archived"] or result["errors"]:
            details = result["errors"] or [f"{name} introuvable"]
            raise HTTPException(
                500,
                f"Archivage partiel ({len(archived)}/{len(names)}) : "
                + " ; ".join(details),
            )
        archived.append(name)
    return {"archived_meeting": meeting_id, "documents": archived}


@app.get("/api/transcript/{name}")
def read_transcript(request: Request, name: str, view_scope: str) -> dict:
    _require_document_scope(name, _portee_lecture(request, view_scope))
    item = library.get_transcript(name)
    if item is None:
        raise HTTPException(404, "Transcript introuvable")
    # Rattachement des locuteurs aux voix globales : {Speaker N -> {voice_id, display}}
    try:
        item["voices"] = voiceprint.assignments(
            name, compte=_noms_visibles(request, _portee_lecture(request, view_scope)))
    except Exception:  # noqa: BLE001
        item["voices"] = {}
    # Audio conservé ? (évite une sonde HEAD côté client — FastAPI répond 405)
    try:
        from core import media
        item["has_audio"] = media.audio_path(name).exists()
    except Exception:  # noqa: BLE001
        item["has_audio"] = False
    # La sensibilité EFFECTIVE, celle de la réunion (`library.sensibilite_effective`), que
    # l'écran affiche et que la règle d'envoi applique. `meta.sensitivity` reste le niveau
    # noté dans le `.md` à sa création.
    meta = item.get("meta") or {}
    item["sensitivity_effective"] = library.sensibilite_effective(meta.get("sensitivity"),
                                                                  meta.get("meeting_id"),
                                                                  library.projet_du_document(name))
    # Stabilité de la langue : mesurée à la volée sur le .md (source de vérité),
    # donc valable aussi pour les transcripts antérieurs, sans re-transcrire.
    # `None` = non vérifié (paire de langues non couverte) ≠ 0 = vérifié propre.
    try:
        from core import quality
        # Le COMPTE accompagne la proportion : `is_unstable` en a besoin pour
        # exiger de la confiance avant de crier. Sans lui, `2/40` déclenchait
        # une alerte sur une mesure compatible avec « propre », et `8/8` sur un
        # message vocal court n'en déclenchait aucune.
        mix, segments = quality.mesure_langue(
            quality.transcript_texts(item.get("body") or ""),
            (item.get("meta") or {}).get("language"),
        )
        item["language_mix"] = mix
        item["language_unstable"] = quality.is_unstable(mix, segments)
    except Exception:  # noqa: BLE001
        item["language_mix"] = None
        item["language_unstable"] = False
    return item


# ---------- FinOps : usage & coût d'un document (LECTURE SEULE) ----------
#
# Expose la télémétrie DÉJÀ fiable pour UN transcript/CR dans l'onglet Informations. N'écrit
# jamais `usage.db` (via `usage_store.read_connect`, `mode=ro`). Ne rend AUCUN texte de
# prompt/transcript/CR ni secret (les tables n'en contiennent pas). Best-effort : si la
# télémétrie est indisponible, on rend `{available: false}` — jamais une erreur produit.
def _finops_execution_for(meta: dict):
    from core import usage_store  # noqa: PLC0415
    doc_id = meta.get("id")
    if meta.get("type") == "report":
        # Le CR est lié par SON ULID (produced_artifact_id), pas par son nom de fichier.
        return usage_store.execution_summary(artifact_id=doc_id, kind="report"), doc_id
    # Transcript : exécution STT rattachée par le document_id du transcript.
    return usage_store.execution_summary(document_id=doc_id, kind="stt"), doc_id


def _finops_profil_for(meta: dict) -> Optional[dict]:
    """L'exécution `profile` d'un transcript (profilage LLM du pipeline), ou None.

    Rattachée, comme la STT, par le document_id du transcript ; un CR n'en a pas.
    """
    if meta.get("type") == "report" or not meta.get("id"):
        return None
    from core import usage_store  # noqa: PLC0415
    return usage_store.execution_summary(document_id=meta["id"], kind="profile")


def _finops_public(summary: dict) -> dict:
    """Contrat d'affichage : uniquement des faits sûrs, noms provider/model VERBATIM."""
    in_tok, out_tok = summary.get("in_tok"), summary.get("out_tok")
    tokens = None if (in_tok is None and out_tok is None) else (in_tok or 0) + (out_tok or 0)
    return {
        "available": True,
        "execution_id": summary["execution_id"],
        "kind": summary["kind"],
        "status": summary["status"],
        "provider": summary["provider_primary"],
        "model": summary["model_primary"],
        "cost": summary["cost"],
        "currency": summary["cost_currency"],
        "cost_basis": summary["cost_basis"],           # cloud_derived | local_imputed | mixed | None
        "pricing_provenance": summary.get("pricing_provenance"),
        "calls": summary["n_calls"],
        "in_tok": in_tok,
        "out_tok": out_tok,
        "tokens": tokens,
        "audio_seconds": summary["audio_seconds"],
        "wall_clock_s": summary["wall_clock_s"],
        "started_at": summary["started_at"],
        "ended_at": summary["ended_at"],
    }


@app.get("/api/transcript/{name}/finops")
def read_finops(request: Request, name: str, view_scope: str) -> dict:
    """Résumé compact usage & coût du document `name` (transcript ou CR). `{available:false}` si
    la télémétrie n'a pas d'exécution liée ou est indisponible — jamais bloquant."""
    _require_document_scope(name, _portee(request, view_scope))
    item = library.get_transcript(name)
    if item is None:
        raise HTTPException(404, "Transcript introuvable")
    meta = item.get("meta") or {}
    try:
        summary, _doc_id = _finops_execution_for(meta)
    except Exception:  # noqa: BLE001 — l'affichage FinOps ne casse jamais la lecture du document
        summary = None
    try:
        profil = _finops_profil_for(meta)
    except Exception:  # noqa: BLE001
        profil = None
    # Un transcript porte aussi son profilage, au même contrat, sous `profile` : présent
    # seulement quand l'exécution existe, même sans exécution STT liée.
    sortie = _finops_public(summary) if summary else {"available": False}
    if profil:
        sortie["profile"] = _finops_public(profil)
    return sortie


@app.get("/api/transcript/{name}/finops/execution/{execution_id}")
def read_finops_execution(request: Request, name: str, execution_id: str, view_scope: str) -> dict:
    """Détail forensique d'UNE exécution, borné au document `name` (scope réutilisé). Les lignes
    `model_call` (étape/latence/jetons/coût/statut) sans aucun contenu. `{available:false}` sinon."""
    _require_document_scope(name, _portee(request, view_scope))
    item = library.get_transcript(name)
    if item is None:
        raise HTTPException(404, "Transcript introuvable")
    meta = item.get("meta") or {}
    from core import usage_store  # noqa: PLC0415
    try:
        detail = usage_store.execution_detail(execution_id)
    except Exception:  # noqa: BLE001
        detail = None
    if not detail:
        return {"available": False}
    # L'exécution doit APPARTENIR à ce document scopé (id du CR OU document_id du transcript),
    # sinon on refuse : pas de lecture d'exécution arbitraire via un document autorisé.
    ex = detail["execution"]
    doc_id = meta.get("id")
    if ex.get("produced_artifact_id") != doc_id and ex.get("document_id") != doc_id:
        raise HTTPException(404, "Exécution introuvable pour ce document")
    return {"available": True, "execution": _finops_public(ex), "calls": detail["calls"]}


# ---------- Usage (agrégat FinOps, phase 3A — dark, lecture seule) ----------
# Séries agrégées lues depuis `usage_daily` (jamais un scan de `model_call`). Alimente le futur
# tableau de bord Usage ; AUCUNE UI ici. L'agrégat est BORNÉ PAR PROJET : `view_scope` suit la
# convention de l'app — une portée projet ne renvoie QUE ce projet, tous les projets du compte
# ne sont servis QUE sur `view_scope=all` explicite (jamais par défaut), et jamais ceux d'un autre
# compte.
# L'agrégat ne porte ni document ni texte. Lecture seule stricte : n'écrit jamais `usage.db`.
@app.get("/api/usage/rollup")
def read_usage_rollup(
    request: Request,
    view_scope: str = "",
    group_by: str = "day",
    start: Optional[str] = None,
    end: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    stage: Optional[str] = None,
    kind: Optional[str] = None,
) -> dict:
    """Agrégat usage & coût groupé par une dimension, borné au projet de `view_scope`.

    `view_scope` : un ID de projet -> ce projet seul ; `all` -> tous les projets du compte
    (opt-in explicite) ; vide -> son « Non classé ». `group_by` ∈
    {day, project_id, provider, model, stage, exec_kind, cost_basis}. Filtres : plage de jours
    (`start`/`end`), `provider`, `model`, `stage`, `kind`. Jamais un journal d'exécutions."""
    from core import usage_store  # noqa: PLC0415
    dim = group_by if group_by in usage_store._ROLLUP_DIMS else "day"
    project_arg = _portee_avec_herites(request, view_scope)
    rows = usage_store.usage_rollup(
        start=start, end=end, project_id=project_arg, provider=provider, model=model,
        stage=stage, kind=kind, group_by=dim,
    )
    return {"group_by": dim, "scope": _libelle_portee(view_scope, project_arg), "rows": rows}


# Drill-down progressif (phase 3B) : liste d'exécutions BORNÉE + détail forensique. Lecture seule,
# bornée par projet (même `view_scope`), paginée/plafonnée, requêtes indexées sur les faits bruts.
# JAMAIS un journal géant par défaut, jamais de texte de prompt/CR/secret, jamais d'écriture.
@app.get("/api/usage/executions")
def read_usage_executions(
    request: Request,
    view_scope: str = "",
    start: Optional[str] = None,
    end: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    stage: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Liste bornée d'exécutions pour le drill-down, bornée au projet de `view_scope`.

    `view_scope` : un projet -> ce projet seul ; `all` -> tous les projets du compte ; vide -> son
    « Non classé ». Paginée (`limit` plafonné, `offset`) ; filtres plage de jours / provider / model /
    stage / kind. Champs SÛRS uniquement — jamais un journal géant ni du contenu."""
    from core import usage_store  # noqa: PLC0415
    project_arg = _portee_avec_herites(request, view_scope)
    res = usage_store.list_executions(
        project_id=project_arg, start=start, end=end, provider=provider, model=model,
        stage=stage, kind=kind, limit=limit, offset=offset,
    )
    res["scope"] = _libelle_portee(view_scope, project_arg)
    return res


@app.get("/api/usage/executions/{execution_id}")
def read_usage_execution_detail(request: Request, execution_id: str, view_scope: str = "") -> dict:
    """Détail forensique d'UNE exécution (lignes model_call), BORNÉ au projet de `view_scope`.

    Réutilise le contrat forensique de la phase 2. Refuse (404) une exécution inconnue comme une
    exécution hors de la portée : la réponse ne dit pas si l'identifiant existe chez un autre
    compte."""
    from core import usage_store  # noqa: PLC0415
    scope_projects = _portee_avec_herites(request, view_scope)
    detail = usage_store.execution_detail(execution_id)
    if not detail:
        raise HTTPException(404, "Exécution hors de la portée projet")
    ex = detail["execution"]
    # L'exécution DOIT appartenir à un projet de la portée.
    if ex.get("project_id") not in scope_projects:
        raise HTTPException(404, "Exécution hors de la portée projet")
    return {"available": True, "execution": ex, "calls": detail["calls"]}


# ---------- Voix (signature vocale) ----------

class VoiceRename(BaseModel):
    label: Optional[str] = None


class VoiceMerge(BaseModel):
    target_id: int


@app.get("/api/voices")
def list_voices(request: Request, view_scope: str = "") -> list[dict]:
    """Voix du compte visibles dans la portée + provenance des occurrences ; `all` = mes voix."""
    lu = _portee_lecture(request, view_scope)
    return voiceprint.list_voices(project_id=lu, compte=_noms_visibles(request, lu))


def _require_voice_scope(request: Request, voice_id: int, project_id: project_store.Portee) -> None:
    """La voix est visible dans la portée ET entièrement au compte.

    404 sans recopier l'identifiant : rien ne révèle qu'une voix existe ailleurs.
    """
    if not (voiceprint.visible_in_project(voice_id, project_id)
            and voiceprint.appartient_au_compte(voice_id, _projets_du_compte(request))):
        raise HTTPException(404, "Voix introuvable dans cette portée")


@app.patch("/api/voices/{voice_id}")
def rename_voice(request: Request, voice_id: int, body: VoiceRename, view_scope: str = "") -> dict:
    """Nomme (ou dénomme) une voix ; le nom se propage à tous ses transcripts."""
    project_id = _portee(request, view_scope)
    _require_voice_scope(request, voice_id, project_id)
    try:
        voiceprint.rename(voice_id, body.label)
    except voiceprint.UnknownVoice as exc:
        raise HTTPException(404, str(exc))
    return {"id": voice_id, "label": (body.label or "").strip() or None}


@app.post("/api/voices/{voice_id}/merge")
def merge_voice(request: Request, voice_id: int, body: VoiceMerge, view_scope: str = "") -> dict:
    """Fusionne la voix {voice_id} dans {target_id} (même personne détectée en double)."""
    project_id = _portee(request, view_scope)
    _require_voice_scope(request, voice_id, project_id)
    # Deux voix de comptes différents ne se fusionnent jamais : la cible doit
    # passer le même garde.
    _require_voice_scope(request, body.target_id, project_id)
    try:
        voiceprint.merge_voices(voice_id, body.target_id)
    except voiceprint.UnknownVoice as exc:
        raise HTTPException(404, str(exc))
    return {"merged": voice_id, "into": body.target_id}


@app.post("/api/voices/reindex")
def reindex_voices(request: Request, view_scope: str = "") -> dict:
    """Recalcule le rattachement des voix depuis les empreintes des .md."""
    try:
        return voiceprint.reindex_all(project_id=_portee_maintenance(request, view_scope))
    except resources.ResourcesBusy as exc:
        raise HTTPException(429, str(exc))


@app.post("/api/voices/backfill")
def backfill_voices(request: Request, view_scope: str = "") -> dict:
    """Re-diarise les transcripts diarisés antérieurs pour leur ajouter les
    empreintes vocales manquantes (job en tâche de fond, progression SSE)."""
    from core import backfill
    project_id = _portee_maintenance(request, view_scope)
    try:
        job = jobs.create(owner=portee.utilisateur(request).id, nature="voix")
    except JobsBusy as exc:
        raise HTTPException(429, str(exc))

    def target(cb):
        return backfill.backfill_all(progress=cb, project_id=project_id)

    jobs.run(job, target)
    return {"job_id": job.id}


@app.get("/api/voices/backfill/preview")
def backfill_preview(request: Request, view_scope: str = "") -> dict:
    """Nombre de transcripts éligibles au backfill (diarisés, sans empreintes, audio dispo)."""
    from core import backfill
    project_id = _portee_maintenance(request, view_scope)
    return {"eligible": [p.name for p in backfill.eligible(project_id)]}


# ---------- Maintenance bornée par projet ----------

@app.get("/api/maintenance")
def maintenance_status(request: Request, view_scope: str = "") -> dict:
    etat = maintenance.status(project_id=_portee_maintenance(request, view_scope))
    # L'échec de réindexation au démarrage ne laissait qu'une trace sur stdout.
    # Il complète le diagnostic vivant du corpus : ce dernier dit si l'index
    # PEUT être reconstruit, celui-ci dit s'il l'a été depuis le lancement.
    etat["corpus"] = {
        **etat.get("corpus", {}),
        "reindexation_demarrage_ok": ETAT_DEMARRAGE["index_ok"],
        "reindexation_demarrage_detail": ETAT_DEMARRAGE["detail"],
    }
    return etat


@app.post("/api/maintenance/backfill/faithful")
def backfill_faithful_archives(request: Request, view_scope: str = "") -> dict:
    """Crée en tâche de fond les FLAC fidèles dont l'original subsiste."""
    project_id = _portee_maintenance(request, view_scope)
    try:
        job = jobs.create(owner=portee.utilisateur(request).id, nature="archives")
    except JobsBusy as exc:
        raise HTTPException(429, str(exc))

    def target(cb):
        return faithful_backfill.run(
            root=library.OUTPUT_DIR,
            project_id=project_id,
            progress=cb,
        )

    jobs.run(job, target)
    return {"job_id": job.id}


@app.post("/api/maintenance/reindex/{kind}")
def maintenance_reindex(request: Request, kind: Literal["semantic", "voices"], view_scope: str = "") -> dict:
    project_id = _portee_maintenance(request, view_scope)
    if kind == "semantic":
        try:
            with resources.heavy(label="réindexation sémantique"):
                return semantic.reindex_all(project_id=project_id)
        except resources.ResourcesBusy as exc:
            raise HTTPException(429, str(exc))
        except llm.LLMError as exc:
            raise HTTPException(503, f"Réindexation indisponible : {exc}")
    try:
        return voiceprint.reindex_all(project_id=project_id)
    except resources.ResourcesBusy as exc:
        raise HTTPException(429, str(exc))


@app.get("/api/transcript/{name}/download")
def download_transcript(request: Request, name: str, view_scope: str) -> FileResponse:
    _require_document_scope(name, _portee_lecture(request, view_scope))
    p = library.transcript_path(name)
    if p is None:
        raise HTTPException(404, "Transcript introuvable")
    return FileResponse(p, media_type="text/markdown", filename=p.name)


class ReportBody(BaseModel):
    # Le provider DOIT suivre le choix de l'utilisateur, comme pour /ask et la
    # transcription. Sans lui, cet endpoint forçait Ollama : un utilisateur
    # Mistral-only ne pouvait générer AUCUN compte-rendu à la demande.
    # Corps optionnel + défaut Ollama : un POST sans corps reste valide.
    llm_provider: str = ""
    mistral_api_key: Optional[str] = None


def _controles_compte_rendu(request: Request, name: str, view_scope: str,
                            opts: ReportBody) -> tuple[str, dict]:
    """Les contrôles COMMUNS aux deux routes de génération, dans l'ordre : `(fournisseur, transcript)`.

    Tout passe AVANT la réservation du créneau lourd et avant tout appel au modèle : un refus est une
    erreur HTTP et ne crée rien. Le garde de portée précède la lecture : cette route lit le transcript
    ENTIER et peut l'expédier à un LLM **cloud** ; vérifier après coup laisserait le texte sortir.
    """
    # AVANT le `try` : `FournisseurInconnu` hérite de `ValueError`, et un `except ValueError` plus bas le
    # transformerait en **404** — « transcript introuvable » pour un fournisseur mal orthographié. Et avant
    # le garde de portée : cette validation est PURE (aucune lecture, aucun appel réseau), elle ne
    # divulgue donc rien sur l'existence du document.
    fournisseur = _fournisseur_valide(opts.llm_provider)
    _require_document_scope(name, _portee(request, view_scope))
    from core.experimental import chunking
    item = library.get_transcript(name)
    # Sensibilité EFFECTIVE, celle de la réunion (`library.sensibilite_effective`), comme le cœur.
    envoi = library.envoi_du_document(name, (item or {}).get("meta") or {})
    _refuser_le_cloud(envoi.sensibilite, local=llm.est_local(fournisseur), proprietaire=envoi.proprietaire)
    try:
        # Avant la réservation et avant tout appel au modèle.
        chunking.verifier_citable((item or {}).get("body") or "")
    except chunking.TranscriptNonCitable as exc:
        raise HTTPException(400, str(exc)) from exc
    return fournisseur, item or {}


@app.post("/api/transcript/{name}/report")
def make_report(request: Request, name: str, view_scope: str, body: Optional[ReportBody] = None) -> dict:
    """Génère un compte-rendu structuré du transcript et le sauvegarde (biblio).

    Synchrone : la réponse porte le compte rendu. Pour le suivre dans la vue des traitements, voir
    `POST …/report/jobs`.
    """
    from core import report
    from core.experimental import chunking
    from core.experimental.pipeline import ExtractionVide
    opts = body or ReportBody()
    fournisseur, _item = _controles_compte_rendu(request, name, view_scope, opts)
    try:
        # Le Map-Reduce d'un CR est un travail LOURD : sans réservation il
        # tournait en concurrence d'une transcription, les deux se disputant la
        # RAM unifiée.
        # `wait=0` : chemin INTERACTIF, on refuse tout de suite plutôt que de
        # faire patienter sans signal (la transcription, elle, attend son tour).
        with resources.heavy(wait=0, label="compte-rendu"):
            # Seam UNIQUE : route locale → CR riche, cloud → CR hérité (inchangé).
            # L'utilisateur ne choisit pas de mode ; la route découle du moteur.
            return report.generate_report(
                name,
                provider=fournisseur,
                mistral_api_key=(opts.mistral_api_key or None),
            )
    except chunking.TranscriptNonCitable as exc:
        raise HTTPException(400, str(exc)) from exc
    except ExtractionVide as exc:
        # Le travail a tourné et n'a rien retenu : 422, avec la cause et les comptes.
        raise HTTPException(422, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    except resources.ResourcesBusy as exc:
        # 429 comme pour les jobs : la machine ne traite qu'une tâche
        # lourde à la fois, et un refus explicite vaut mieux qu'une
        # attente inexpliquée de plusieurs minutes.
        raise HTTPException(429, str(exc))
    except llm.LLMError as exc:
        raise HTTPException(503, f"Génération indisponible : {exc}")


@app.post("/api/transcript/{name}/report/jobs")
def lancer_compte_rendu(request: Request, name: str, view_scope: str,
                        body: Optional[ReportBody] = None) -> dict:
    """Régénère un compte rendu comme un TRAITEMENT : suivi dans la vue des traitements et l'indicateur.

    Mêmes contrôles que `POST …/report`, tous avant la création du traitement (un refus ne crée rien).
    Le résultat s'AJOUTE aux versions existantes (`unique_path`) : aucune n'est écrasée. L'attente du
    créneau lourd est celle d'un travail de fond, et se voit (`en_attente`).
    """
    from core import report
    opts = body or ReportBody()
    fournisseur, item = _controles_compte_rendu(request, name, view_scope, opts)
    meta = item.get("meta") or {}
    src = library.transcript_path(name)
    projet = project_store.load(src.parent) if src is not None else None
    cle = (opts.mistral_api_key or None)   # dans la fermeture seulement : ni traitement, ni événement, ni message
    try:
        job = jobs.create(
            owner=portee.utilisateur(request).id, nature="compte-rendu",
            titre=library.titre_lisible(meta.get("title"), meta.get("source"), nom=name),
            projet_id=projet.id if projet else None, projet_nom=projet.name if projet else "",
            jeton=getattr(request.state, "jeton_id", None), plan=list(GROUPES_COMPTE_RENDU))
    except JobsBusy as exc:
        raise HTTPException(429, str(exc))

    def target(cb):
        def _en_attente() -> None:
            with job.lock:
                job.en_attente = True

        def _obtenu() -> None:
            with job.lock:
                job.en_attente = False

        with resources.heavy(wait=config.ATTENTE_DE_FOND_API_S, label="compte-rendu",
                             on_wait=_en_attente, on_acquired=_obtenu):
            sortie = report.generate_report(name, provider=fournisseur, mistral_api_key=cle, progress=cb)
        resultat = {"name": sortie["name"], "genre": sortie.get("genre")}
        if sortie.get("warnings"):
            resultat["warnings"] = sortie["warnings"]
        # L'identité de la réunion, pour que « Ouvrir » mène à elle (comme pour une transcription).
        ecrit = library.transcript_path(sortie["name"])
        nouveau, _ = library._split_frontmatter(ecrit.read_text(encoding="utf-8")) if ecrit else ({}, "")
        for cle_meta in ("meeting_id", "project_id"):
            if isinstance(nouveau.get(cle_meta), str) and nouveau[cle_meta]:
                resultat[cle_meta] = nouveau[cle_meta]
        return resultat

    jobs.run(job, target)
    return {"job_id": job.id}


@app.get("/api/transcript/{name}/archive-plan")
def transcript_archive_plan(request: Request, name: str, view_scope: str) -> dict:
    """Liste exactement le transcript et les CR liés qui seront archivés."""
    project_id = _portee(request, view_scope)
    _require_document_scope(name, project_id)
    detail = library.get_transcript(name)
    if detail is None:
        raise HTTPException(404, "Transcript introuvable")
    documents = _plan_dans_la_portee(name, project_id)
    if not documents:
        raise HTTPException(404, "Transcript introuvable")
    # Règle : PAS de compte des exclus.
    #
    # Un champ `excluded` voulait « dire combien sans dire quoi ». Il est
    # incohérent : ces routes justifient leurs 404 plutôt que des 403 par « ne
    # pas confirmer qu'un objet existe ailleurs », et un compte dit exactement
    # cela. Il n'avait en outre aucun usage — le type TypeScript ne
    # le déclarait pas, aucun composant ne l'affichait : invisible à
    # l'utilisateur, lisible par un client d'API.
    #
    # L'anomalie qui le produit — un document dont le dossier et le
    # front-matter divergent — reste exposée par `/api/maintenance`, qui est du
    # diagnostic et non une réponse à un lecteur.
    return {"requested": name, "documents": documents}


@app.delete("/api/transcript/{name}")
def delete_transcript(request: Request, name: str, view_scope: str, hard: bool = False) -> dict:
    """Supprime un transcript.

    `hard=false` (défaut) : **archivage réversible** — le .md part en corbeille,
    les index dérivés sont purgés, l'audio et les empreintes sont CONSERVÉS.
    `hard=true` : **suppression définitive** de toutes les traces, corbeille
    comprise. Irréversible.
    """
    from core import purge

    project_id = _portee(request, view_scope)
    _require_document_scope(name, project_id)
    detail = library.get_transcript(name)
    if detail is None:
        raise HTTPException(404, "Transcript introuvable")
    plan = _plan_dans_la_portee(name, project_id)

    if hard:
        # La suppression directe ne porte que sur un document encore actif et
        # visible dans la portée demandée. Un document archivé se supprime via
        # /api/trash/{nom_archivé}, qui identifie une copie exacte et son projet.
        if purge.is_clean(name):
            raise HTTPException(404, "Transcript introuvable")
        targets = [item["name"] for item in plan] or [name]
        failures = []
        before = {}
        deleted = []
        for target in targets:
            result = purge.purge_transcript(target)
            before[target] = result["before"]
            if result["clean"]:
                deleted.append(target)
            else:
                failures += result["errors"]
        if failures:
            raise HTTPException(
                500,
                f"Suppression partielle ({len(deleted)}/{len(targets)}) : "
                + " ; ".join(failures),
            )
        return {
            "deleted": name, "documents": deleted, "clean": True, "before": before,
        }

    targets = [item["name"] for item in plan]
    archived = []
    for target in targets:
        result = purge.archive(target)
        if not result["archived"] or result["errors"]:
            details = result["errors"] or [f"{target} introuvable"]
            raise HTTPException(
                500,
                f"Archivage partiel ({len(archived)}/{len(targets)}) : "
                + " ; ".join(details),
            )
        archived.append(target)
    return {"archived": name, "documents": archived}


@app.get("/api/trash")
def get_trash(request: Request, view_scope: str = "") -> list[dict]:
    """Transcripts archivés, encore récupérables."""
    return _trash_items_from_view_scope(request, view_scope)


@app.post("/api/trash/{archived_name}/restore")
def restore_from_trash(request: Request, archived_name: str, view_scope: str = "") -> dict:
    """Restaure un transcript archivé dans son projet d'origine."""
    from core import purge

    if _trash_item_in_scope(request, archived_name, view_scope) is None:
        raise HTTPException(404, "Introuvable en corbeille dans cette portée")
    restored = purge.restore_detailed(archived_name)
    if restored is None:
        raise HTTPException(404, "Introuvable en corbeille, ou un transcript porte déjà ce nom")
    dest = restored["path"]
    return {
        "restored": dest.name,
        "project": library._project_of(dest),
        "warnings": restored["errors"],
    }


@app.delete("/api/trash/{archived_name}")
def delete_from_trash(request: Request, archived_name: str, view_scope: str = "") -> dict:
    """Supprime définitivement un élément exact, sans masquer un échec partiel."""
    from core import purge

    if _trash_item_in_scope(request, archived_name, view_scope) is None:
        raise HTTPException(404, "Introuvable en corbeille dans cette portée")
    result = purge.purge_archived(archived_name)
    if not result["deleted"]:
        raise HTTPException(
            500, "Suppression incomplète : " + " ; ".join(result["errors"]),
        )
    return result


@app.delete("/api/trash")
def empty_trash(request: Request, view_scope: str = "") -> dict:
    """Vide la corbeille — suppression définitive des archivés.

    Les éléments dont un artefact durable n'a pas pu être supprimé sont
    CONSERVÉS (avec leur manifeste) pour que le nettoyage reste reprenable.
    """
    from core import purge

    names = {item["name"] for item in _trash_items_from_view_scope(request, view_scope)}
    result = purge.purge_trash(names)
    if result["errors"]:
        raise HTTPException(
            500,
            f"Nettoyage partiel : {result['removed']} supprimé(s), "
            f"{len(result['kept'])} conservé(s) — " + " ; ".join(result["errors"]),
        )
    return result


@app.get("/api/transcript/{name}/audio")
def transcript_audio(request: Request, name: str, view_scope: str) -> FileResponse:
    """Sert l'archive audio conservée (lecture, vérification d'un passage)."""
    from core import media
    # L'audio est la matière PREMIÈRE : plus révélateur encore que le transcript.
    _require_document_scope(name, _portee_lecture(request, view_scope))
    p = media.audio_path(name)
    if not p.exists():
        raise HTTPException(404, "Aucun audio conservé pour ce transcript")
    # Type déduit de l'extension : tout ce qui n'était pas .m4a était servi
    # comme "audio/ogg", y compris un MP3 ou un WAV. Les navigateurs le
    # tolèrent souvent, ce n'est pas garanti.
    types = {".m4a": "audio/mp4", ".opus": "audio/ogg", ".ogg": "audio/ogg",
             ".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac",
             ".aac": "audio/aac"}
    return FileResponse(p, media_type=types.get(p.suffix.lower(), "application/octet-stream"))


# ---------- Doublons ----------

@app.get("/api/duplicates")
def get_duplicates(request: Request, view_scope: str = "") -> list[dict]:
    """Groupes de transcripts en doublon, avec le membre recommandé à garder."""
    from core import dedup
    return dedup.find_groups(project_id=_portee(request, view_scope))


# ---------- Projets ----------

class ProjectBody(BaseModel):
    name: str


class MoveBody(BaseModel):
    destination_project_id: str


@app.get("/api/projects")
def get_projects(request: Request) -> list[dict]:
    """Les projets du compte + nombre de réunions (un projet = un dossier de output/).

    Un administrateur voit aussi les projets sans propriétaire, et les dossiers
    sans manifeste encore à migrer (`id` nul). Lecture seule : un « Non classé » qui
    n'existe pas encore n'est pas dans la liste, et rien n'est créé ici.
    """
    compte = portee.utilisateur(request)
    ids = portee.tous_mes_projets(compte)
    return [
        p for p in library.projects()
        if p["id"] in ids or (p["id"] is None and compte.admin)
    ]


@app.post("/api/projects")
def create_project(request: Request, body: ProjectBody) -> dict:
    """Crée un projet du compte et son identité persistante dans le manifeste.

    Une écriture : le « Non classé » du compte est créé d'abord s'il manque, et sur
    une bibliothèque vierge la racine lui est attribuée (`portee.creer_non_classe`).
    """
    try:
        project_store.ensure_inbox(library.OUTPUT_DIR)
    except project_store.ProjectManifestError as exc:
        raise HTTPException(409, str(exc))
    portee.creer_non_classe(portee.utilisateur(request).identifiant)
    try:
        name = library.safe_project_name(body.name)
    except library.UnsafeProject as exc:
        raise HTTPException(400, str(exc))
    if name is None:
        raise HTTPException(400, "Nom de projet invalide")
    compte = portee.utilisateur(request)
    try:
        library.project_dir(name)  # refuse un nom qui sortirait de output/
        _d, project = project_store.create_owned(
            library.OUTPUT_DIR, name=name, owner=compte.identifiant,
        )
    except project_store.ProjectManifestError as exc:
        raise HTTPException(409, str(exc))
    except library.UnsafeProject as exc:
        raise HTTPException(400, str(exc))
    return {
        "id": project.id, "project": project.name, "name": project.name,
        "kind": project.kind, "owner": project.owner, "count": 0,
    }


# ---------- Partages v1 ----------

class NouveauPartage(BaseModel):
    type: Literal["projet", "reunion"]
    cible: str
    invite: str


def _decrire_partage(partage: dict) -> dict:
    """Le partage et le nom de sa cible : le projet, ou le titre de la réunion."""
    if partage["type"] == "projet":
        nom = next((p.name for _d, p in project_store.all_projects(library.OUTPUT_DIR)
                    if p.id == partage["cible"]), None)
    else:
        try:
            reunion = library.reunion_dans_la_portee(
                partage["cible"], frozenset(portee.projets_de(partage["proprietaire"])))
        except library.ReunionAmbigue:
            reunion = None
        nom = reunion.get("title") if reunion else None
    return {**partage, "nom": nom}


@app.post("/api/partages", status_code=201)
def creer_partage(request: Request, corps: NouveauPartage) -> dict:
    """Partage en lecture seule un projet ou une réunion du compte avec un autre compte.

    Seul le propriétaire partage, et seulement ce qu'il détient : un projet sans
    propriétaire ne se partage pas, l'administrateur l'attribue d'abord. Q16 :
    une réunion qui contient un document ultra-confidentiel ne se partage pas.
    """
    compte = portee.utilisateur(request)
    mes_projets = portee.projets_de(compte.identifiant)
    if corps.type == "projet":
        if corps.cible not in mes_projets:
            raise HTTPException(404, "Projet introuvable")
    else:
        reunion = _reunion_de_la_portee(corps.cible, frozenset(mes_projets))
        if any(library.reunion_ultra_confidentielle(corps.cible, d.get("project_id"))
               for d in (*reunion["transcripts"], *reunion["reports"])):
            raise HTTPException(
                409, "Cette réunion contient un document ultra-confidentiel : elle ne se partage pas.")
    invite = next((c for c in auth.lister_comptes()
                   if c["identifiant"].casefold() == corps.invite.strip().casefold()), None)
    if invite is None or not invite["actif"]:
        raise HTTPException(400, "Aucun compte actif ne porte cet identifiant.")
    if project_store.meme_compte(invite["identifiant"], compte.identifiant):
        raise HTTPException(400, "Un partage se fait avec un autre compte.")
    partage = partages.partager(compte.identifiant, invite["identifiant"], corps.type, corps.cible)
    return {"partage": _decrire_partage(partage)}


@app.get("/api/partages")
def lister_partages(request: Request) -> dict:
    """Ce que le compte partage (`donnes`) et ce qui lui est partagé (`recus`)."""
    compte = portee.utilisateur(request)
    return {
        "donnes": [_decrire_partage(p) for p in partages.donnes(compte.identifiant)],
        "recus": [_decrire_partage(p) for p in portee.partages_actifs(compte)],
    }


@app.delete("/api/partages/{partage_id}")
def retirer_partage(request: Request, partage_id: str) -> dict:
    """Retire un partage du compte ; il ne s'ouvre plus dès la requête suivante."""
    if not partages.retirer(portee.utilisateur(request).identifiant, partage_id):
        raise HTTPException(404, "Partage introuvable")
    return {"retire": partage_id}


@app.patch("/api/transcript/{name}/project")
def move_transcript(request: Request, name: str, body: MoveBody, view_scope: str) -> dict:
    """Déplace un transcript vers un projet, avec toute sa réunion DANS SON PROJET (déplace
    les .md + resync l'index). Chaque document déplacé doit être dans la portée d'écriture
    de l'appelant ; un document homonyme d'un autre projet n'est jamais emporté."""
    portee_ecriture = _portee(request, view_scope)
    _require_document_scope(name, portee_ecriture)
    meeting_id = ((library.get_transcript(name) or {}).get("meta") or {}).get("meeting_id")
    if docid.est_valide(meeting_id):
        reunion = library.get_meeting(meeting_id, project_id=library.projet_du_document(name))
        if reunion is None:
            raise HTTPException(404, "Transcript introuvable dans cette portée")
        _require_meeting_scope(reunion, portee_ecriture)
    cible = portee.projet(portee.utilisateur(request), body.destination_project_id,
                          ecriture=True)[1]
    try:
        dest = library.move_to_project_id(name, cible.id)
    except (library.UnsafeProject, project_store.ProjectManifestError) as exc:
        raise HTTPException(400, str(exc))
    if dest is None:
        raise HTTPException(404, "Transcript introuvable ou nom déjà pris dans la cible")
    destination = project_store.load(dest.parent if dest.parent != library.OUTPUT_DIR else library.OUTPUT_DIR)
    return {
        "name": dest.name,
        "project": destination.name,
        "project_id": destination.id,
    }


class SensitivityBody(BaseModel):
    sensitivity: _SensibiliteDemandee


def _regler_reunion(meeting_id: str, project_id: Optional[str], valeur: object,
                    portee_ecriture: frozenset) -> dict:
    """LA vérification des deux routes de sensibilité, puis le réglage.

    La réunion est celle du projet `project_id` : ses seuls documents, jamais ceux d'une
    réunion homonyme d'un autre projet ou d'un autre compte. Le projet doit être dans la
    portée d'écriture de l'appelant, et chacun de ces documents aussi
    (`_require_meeting_scope`) : un invité en lecture seule reçoit 404.
    """
    if not docid.est_valide(meeting_id) or not project_id \
            or not project_store.dans_la_portee(project_id, portee_ecriture):
        raise HTTPException(404, "Réunion introuvable dans cette portée")
    meeting = library.get_meeting(meeting_id, project_id=project_id)
    if meeting is None:
        raise HTTPException(404, "Réunion introuvable dans cette portée")
    _require_meeting_scope(meeting, portee_ecriture)
    applique = library.fixer_sensibilite_reunion(meeting_id, project_id, valeur)
    return {"meeting_id": meeting_id, "sensitivity": applique,
            "documents": [d["name"] for d in (*meeting["transcripts"], *meeting["reports"])]}


@app.patch("/api/transcript/{name}/sensitivity")
def set_sensitivity(request: Request, name: str, body: SensitivityBody, view_scope: str) -> dict:
    """Change la sensibilité de la RÉUNION de ce document, dans le projet qui le contient,
    pour toutes ses versions, dès l'appel suivant au fournisseur, travail en cours compris.
    Même vérification que la route de la réunion (`_regler_reunion`). Aucun contenu
    modifié. Rend le niveau que la réunion porte désormais."""
    portee_ecriture = _portee(request, view_scope)
    _require_document_scope(name, portee_ecriture)
    detail = library.get_transcript(name)
    if detail is None:
        raise HTTPException(404, "Document introuvable")
    meeting_id = (detail.get("meta") or {}).get("meeting_id")
    if not docid.est_valide(meeting_id):
        # Document d'avant les identités : sa propre réunion, son front-matter.
        return {"name": name, "sensitivity": library.set_sensitivity(name, body.sensitivity)}
    regle = _regler_reunion(meeting_id, library.projet_du_document(name), body.sensitivity,
                            portee_ecriture)
    return {"name": name, "sensitivity": regle["sensitivity"]}


@app.patch("/api/meetings/{meeting_id}/sensitivity")
def set_meeting_sensitivity(request: Request, meeting_id: str, body: SensitivityBody,
                            view_scope: str) -> dict:
    """Change la sensibilité d'une réunion, pour toutes ses versions, en une action.

    La réunion est cherchée dans les projets que l'appelant peut écrire : une réunion
    homonyme d'un autre compte n'en fait jamais partie. Si plusieurs de ses projets portent
    ce `meeting_id`, la réponse est 409 : la route d'un document désigne alors la bonne.
    Rend le niveau appliqué et les documents qu'il couvre.
    """
    portee_ecriture = _portee(request, view_scope)
    if not docid.est_valide(meeting_id):
        raise HTTPException(404, "Réunion introuvable dans cette portée")
    meeting = _reunion_de_la_portee(meeting_id, portee_ecriture)
    return _regler_reunion(meeting_id, meeting["project_id"], body.sensitivity, portee_ecriture)
