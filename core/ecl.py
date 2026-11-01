"""ECL, phase 1 : les faits d'une réunion, extraits et ancrés sur la parole.

Une extraction dédiée par réunion, par le modèle configuré, de faits typés :
décision, action (avec son porteur nommé et son locuteur), problème ouvert,
chiffre ou échéance. Le modèle peut s'abstenir (`{"faits": []}`).

Chaque fait proposé passe un contrôle déterministe avant d'être gardé :

1. la citation figure mot pour mot dans une à trois répliques consécutives, la
   première à `FENETRE_S` secondes de l'horodatage annoncé ;
2. chaque chiffre et nom propre de l'énoncé figure dans la citation ou parmi les
   libellés des voix qu'elle couvre (E7, même garde que les phrases du
   compte-rendu, `experimental.guard`).

Avant ce contrôle, le décor qu'un modèle recopie de l'extrait est retiré : préfixe
« **Locuteur** [HH:MM:SS] », gras, accent du type. Cela ne fait rien accepter de
plus. Un fait qui échoue est refusé et compté par motif, jamais gardé. Horodatage et
locuteur sont ceux de la réplique trouvée, pas ceux que le modèle annonce. Le
porteur d'une action n'est gardé que s'il est nommé dans la citation ; à défaut,
un engagement à la première personne (« I'll », « je vais ») revient à la voix
qui le prend ; sinon il reste « non nommé » (`None`), sans rien deviner.

Stockage, comme les autres index (règle 3 de `docs/ecl.md`) :
- `output/.ecl/<document_id>.json` fait foi, artefact durable comme l'audio ;
- la table `ecl_faits` de l'index en dérive et se reconstruit (`reindexer`).

La lecture (`faits`) passe par la table `transcripts` : un document archivé,
hors de la portée du compte, ou fermé par le retrait d'un partage n'apporte
aucun fait. Une `PorteeLecture` exclut déjà l'ultra-confidentiel (Q16).

Faits périmés : le fichier porte l'empreinte des répliques dont il a été tiré
(`empreinte`). À la synchronisation d'un document (`library.sync_file`) et à
`reindexer`, et là seulement, un fichier dont l'empreinte diffère de celle du
transcript, ou qui n'en a pas, voit chaque fait revérifié par `controler`, sans
modèle (`reverifier`). Un fait qui échoue devient `perime`, avec son motif : gardé
dans le fichier et dans l'historique, jamais servi comme actuel. La lecture
n'écrit rien et ne relit aucun transcript, comme l'index (`library._conn`). Une
nouvelle extraction remplace le tout.

L'extraction prend la réservation des travaux lourds (`resources.heavy`) avant de
décharger ou d'appeler un moteur : sans effet dans le pipeline ou le compte rendu,
qui la tiennent déjà ; une relance seule attend son tour comme une transcription.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import config, ecl_consignes, library, llm, resources, sensitivity, sqlite_db
from . import projects as project_store

TYPES = ("decision", "action", "probleme", "chiffre")
# Message des surfaces des faits (routes, MCP, `ecl-extraire`) quand l'option est coupée.
DESACTIVES = "Faits de la réunion désactivés : MEMOGLYPH_FAITS=1 les active."


def actives() -> bool:
    """L'option des faits de la réunion (`config.FAITS`), relue à chaque appel."""
    return bool(config.FAITS)

FENETRE_S = 30
# Une citation couvre au plus ce nombre de répliques consécutives.
REPLIQUES_MAX = 3
# 2 : empreinte de la source et statut de chaque fait (actuel ou périmé).
VERSION = 2
STATUTS = ("actuel", "perime")
# Secondes qu'une extraction attend la réservation des travaux lourds, comme la
# transcription : une relance de fond attend son tour plutôt que d'échouer.
ATTENTE_RESERVATION = 900
# Une citation plus courte n'ancre rien : « yes, okay » figure dans toute réunion.
MOTS_MIN_CITATION = 3
# Tranche de transcript par appel, comme l'extraction atomique du compte-rendu.
BORNE_TRANCHE = 2000
# Une tranche dense (listes longues, sans boucle) peut dépasser 2 500 jetons de
# sortie : coupée, elle perdait tous ses faits.
MAX_TOKENS = 4000
# Plafond de sortie par mode : un modèle cloud qui réfléchit avant de répondre (Haiku,
# Sonnet) prend sa réflexion sur ce budget ; à 4 000, une tranche de fin de réunion était
# coupée. Le local garde 4 000.
MAX_TOKENS_PAR_MODE = {"local": MAX_TOKENS, "cloud": 16000}
# Transcript entier en un seul appel quand sa longueur estimée (jetons) reste sous cette
# borne ; None : toujours par tranches. La sortie garde alors le budget des tranches
# qu'elle remplace, dans la limite du serveur local. En un seul appel, le modèle local
# rend moins de faits et en manque davantage, même sur une réunion courte ; aucun seuil
# ne tient, d'où None.
BORNE_CONTEXTE: Optional[int] = None
MAX_TOKENS_CONTEXTE = 16000
# Affichage C (local) : une citation de moins de mots que ceci s'étend à la réplique suivante.
MOTS_MIN_AFFICHAGE = 8
MOTIFS = ("incomplet", "type_inconnu", "horodatage_illisible", "citation_trop_courte",
          "citation_absente", "contenance")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ecl_faits (
    id          TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    meeting_id  TEXT,
    type        TEXT NOT NULL,
    horodatage  TEXT NOT NULL,
    secondes    INTEGER NOT NULL,
    citation    TEXT NOT NULL,
    enonce      TEXT NOT NULL,
    porteur     TEXT,
    locuteur    TEXT,
    mode        TEXT,
    fournisseur TEXT,
    affiche     TEXT,
    statut      TEXT,
    motif_peremption TEXT
);
CREATE INDEX IF NOT EXISTS idx_ecl_faits_document ON ecl_faits(document_id);
"""

# Consigne imposée pour toutes les extractions (mesure seulement) ; sinon celle du mode
# (`ecl_consignes` : « gabarit » en local, « définitions » en cloud).
_SYSTEME: Optional[str] = None
CONSIGNES = {"local": ecl_consignes.GABARIT, "cloud": ecl_consignes.DEFINITIONS}


# L'énoncé dans la langue de la citation : c'est elle que la contenance (E7) compare.
_LANGUES = {"fr": "français", "en": "anglais", "de": "allemand", "es": "espagnol",
            "it": "italien"}
# Le même rappel, écrit dans la langue de la réunion et placé après le transcript : une
# consigne en français seule laissait le modèle local écrire presque tous ses énoncés
# en français dans des réunions anglaises.
_RAPPEL_LANGUE = {"en": 'Write every "enonce" in English, the language of the meeting.',
                  "de": 'Schreibe jedes "enonce" auf Deutsch, in der Sprache der Besprechung.',
                  "es": 'Escribe cada "enonce" en español, el idioma de la reunión.',
                  "it": 'Scrivi ogni "enonce" in italiano, la lingua della riunione.'}


class ExtractionEnPanne(RuntimeError):
    """Aucun appel au modèle n'a rendu de réponse exploitable : rien n'est écrit."""


# ---------- Contrôle déterministe ----------

_HORODATAGE = re.compile(r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})")


def secondes(horodatage: str) -> Optional[int]:
    m = _HORODATAGE.search(str(horodatage or ""))
    if not m:
        return None
    h, mi, s = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
    return h * 3600 + mi * 60 + s


def normaliser(texte: str) -> str:
    """Mots en minuscules, ponctuation retirée : « mot pour mot », sans la typographie."""
    texte = unicodedata.normalize("NFKC", texte or "").casefold().replace("’", "'")
    return " ".join(re.findall(r"[\w']+", texte))


# Mots-outils propres à une langue (ni « a », ni « en », ni « on », ni « car », communs au
# français et à l'anglais) : assez pour dire dans quelle langue une phrase courte est écrite.
_MOTS_OUTILS = {
    "fr": frozenset("le la les de des du une et est sont pour que qui dans sur avec pas il elle "
                    "nous vous ils elles ce cette ces sera doit au aux leur son sa par un ne plus été aussi "
                    "également encore très mais où se".split()),
    "en": frozenset("the and is are for that which with not it we you this these will should to "
                    "of be has have their its by from an if but would can they was were been also only".split()),
}


def langue_probable(texte: str) -> Optional[str]:
    """« fr », « en », ou None quand les mots-outils ne départagent pas."""
    mots = normaliser(texte).replace("'", " ").split()
    comptes = sorted(((sum(m in outils for m in mots), langue) for langue, outils in _MOTS_OUTILS.items()),
                     reverse=True)
    (premier, langue), (second, _) = comptes[0], comptes[1]
    return langue if premier > second else None


def repliques(body: str) -> list[dict]:
    """Les répliques horodatées du transcript : horodatage, secondes, locuteur, texte.

    Lues par la porte du compte-rendu (`chunking.lire_repliques`) : mêmes formats,
    même horodatage `HH:MM:SS`, même règle du locuteur « Nom : ».
    """
    from .experimental.chunking import lire_repliques  # noqa: PLC0415

    return [{"horodatage": r.horodatage, "secondes": secondes(r.horodatage),
             "locuteur": r.locuteur, "texte": r.texte} for r in lire_repliques(body)]


def ancrer(citation: str, a_secondes: int, liste: list[dict]) -> Optional[list[dict]]:
    """Les répliques consécutives, une à `REPLIQUES_MAX`, où la citation figure mot pour mot.

    La première commence à `FENETRE_S` secondes de l'horodatage annoncé ; c'est
    elle qui donne horodatage et locuteur. La fenêtre la plus courte l'emporte :
    une réplique seule d'abord. La transcription coupe une phrase en segments,
    et un fait se construit souvent sur deux tours (« on retient X ? » « oui ») :
    le modèle peut citer les deux.
    """
    cherchee = f" {normaliser(citation)} "
    proches = [i for i, rep in enumerate(liste) if abs(rep["secondes"] - a_secondes) <= FENETRE_S]
    for longueur in range(1, REPLIQUES_MAX + 1):
        for i in proches:
            couvertes = liste[i:i + longueur]
            if len(couvertes) == longueur and cherchee in (
                    f" {normaliser(' '.join(r['texte'] for r in couvertes))} "):
                return couvertes
    return None


# Un engagement pris à la première personne : son porteur est la voix qui le dit.
# Repère grammatical, pas une liste de personnes ; « you » ne désigne personne.
_PREMIERE_PERSONNE = re.compile(
    r"\b(i'll|i will|i'm going to|i am going to|je vais|je m'en occupe|je m'en charge)\b")


def _premiere_personne(citation: str, couvertes: list[dict]) -> Optional[str]:
    if not _PREMIERE_PERSONNE.search(normaliser(citation)):
        return None
    for rep in couvertes:
        if _PREMIERE_PERSONNE.search(normaliser(rep["texte"])):
            return rep["locuteur"]
    return None


# Le décor qu'un modèle recopie de l'extrait : préfixe de ligne et gras. Retiré avant
# le contrôle, il ne fait rien accepter de plus : le reste doit figurer mot pour mot.
_PREFIXE = re.compile(
    r"\*\*[^*\n]{1,60}\*\*\s*(?:\[\d{1,2}:\d{2}(?::\d{2})?\]\s*:?|:)\s*"
    r"|\[\d{1,2}:\d{2}(?::\d{2})?\]\s*")
_GRAS = re.compile(r"\*\*|__")


def sans_decor(texte: str, *, prefixes: bool = True) -> str:
    if prefixes:
        texte = _PREFIXE.sub(" ", texte)
    return " ".join(_GRAS.sub("", texte).split())


def type_lu(brut: object) -> str:
    """Le type sans accent ni casse : « Problème » se lit `probleme`."""
    texte = unicodedata.normalize("NFKD", str(brut or "").strip().casefold())
    return "".join(c for c in texte if not unicodedata.combining(c))


def controler(brut: object, liste: list[dict]) -> tuple[Optional[dict], Optional[str]]:
    """Un fait proposé par le modèle : (fait gardé, None) ou (None, motif de refus)."""
    from .experimental import guard  # noqa: PLC0415

    if not isinstance(brut, dict):
        return None, "incomplet"
    type_ = type_lu(brut.get("type"))
    citation = sans_decor(str(brut.get("citation") or ""))
    enonce = sans_decor(str(brut.get("enonce") or ""), prefixes=False)
    if not citation or not enonce:
        return None, "incomplet"
    if type_ not in TYPES:
        return None, "type_inconnu"
    a_secondes = secondes(brut.get("horodatage"))
    if a_secondes is None:
        return None, "horodatage_illisible"
    if len(normaliser(citation).split()) < MOTS_MIN_CITATION:
        return None, "citation_trop_courte"
    couvertes = ancrer(citation, a_secondes, liste)
    if couvertes is None:
        return None, "citation_absente"
    replique = couvertes[0]
    # E7 contre la citation et les locuteurs qu'elle couvre : « Speaker 2 retient X »
    # ne nomme personne d'autre que la voix citée.
    voix = " ".join(r["locuteur"] for r in couvertes if r["locuteur"])
    if not guard.check_sentence(enonce, f"{citation} {voix}")["ok"]:
        return None, "contenance"
    porteur = str(brut.get("porteur") or "").strip() or None
    if type_ != "action" or (porteur and f" {normaliser(porteur)} " not in f" {normaliser(citation)} "):
        porteur = None
    if type_ == "action" and porteur is None:
        porteur = _premiere_personne(citation, couvertes)
    return {"type": type_, "horodatage": replique["horodatage"],
            "secondes": replique["secondes"], "citation": citation,
            "enonce": enonce, "enonce_statut": "reformulé",
            "porteur": porteur, "locuteur": replique["locuteur"],
            "affiche": affichage(citation, couvertes, liste)}, None


def identite(document_id: str, fait: dict) -> str:
    """Identité stable d'un fait : la même parole, au même endroit, garde la même."""
    cle = "|".join((document_id, fait["type"], fait["horodatage"], normaliser(fait["citation"])))
    return hashlib.sha256(cle.encode("utf-8")).hexdigest()[:16]


# ---------- Extraction ----------

def modele_cloud(fournisseur: str) -> Optional[str]:
    """Le modèle propre à l'ECL chez ce fournisseur cloud ; None : celui du compte rendu."""
    if fournisseur == "anthropic":
        return config.ECL_ANTHROPIC_MODEL or None
    return None


def _modele(system: str, user: str, *, moteur=None, envoi: sensitivity.Envois = None,
            **options) -> dict:
    """L'appel au modèle, seul point que les tests remplacent ; `moteur` : celui de l'ECL locale.

    `moteur` est toujours LOCAL (`moteur_local`, Granite sur MLX) : rien ne sort de la
    machine, la règle d'envoi n'a rien à y contrôler. Tout autre appel passe par la
    façade, qui l'applique avec `envoi`.
    """
    if moteur is not None:
        options.pop("provider", None)
        options.pop("api_key", None)
        return moteur.chat_json(system, user, **options)
    return llm.chat_json(system, user, envoi=envoi, **options)


_moteur_ecl = None


def _modele_present(modele: str) -> bool:
    """Le modèle est-il là sans téléchargement : chemin local, ou dépôt dans le cache HF ?"""
    if Path(modele).expanduser().exists():
        return True
    try:
        from huggingface_hub import try_to_load_from_cache  # noqa: PLC0415
    except ImportError:  # sans le paquet, on ne prétend pas le modèle présent
        return False
    try:
        return isinstance(try_to_load_from_cache(modele, "config.json"), str)
    except Exception:  # noqa: BLE001 — cache illisible : même réponse
        return False


def moteur_local() -> tuple[Optional[object], Optional[str]]:
    """(moteur MLX de l'ECL, avertissement) : Granite s'il est là, sinon None et la raison.

    Sans lui, l'ECL retombe sur le moteur du compte rendu : dégradée, pas en panne.
    """
    global _moteur_ecl
    modele = config.ECL_MLX_MODEL
    if not modele or modele == config.MLX_LLM_MODEL:
        return None, None
    if not _modele_present(modele):
        return None, (f"modèle de l'ECL {modele} absent : faits extraits par le modèle du compte "
                      f"rendu ({config.MLX_LLM_MODEL})")
    if _moteur_ecl is None or getattr(_moteur_ecl, "_model_configure", None) != modele:
        from .llm_mlx import MlxEngine  # noqa: PLC0415
        _moteur_ecl = MlxEngine(model=modele, gabarit={"enable_thinking": False})
    return _moteur_ecl, None


def affichage(citation: str, couvertes: list[dict], liste: list[dict]) -> str:
    """Affichage C : la citation, étendue à la réplique suivante quand elle ne se suffit pas."""
    if len(normaliser(citation).split()) >= MOTS_MIN_AFFICHAGE or not couvertes:
        return citation
    suivante = liste.index(couvertes[-1]) + 1 if couvertes[-1] in liste else len(liste)
    return f"{citation} {liste[suivante]['texte']}" if suivante < len(liste) else citation


def decouper(lignes: list[str], mode: str) -> tuple[list[str], int]:
    """(tranches, plafond de sortie par appel) : une seule sous `BORNE_CONTEXTE`."""
    from .experimental.chunking import est_tokens, token_chunks  # noqa: PLC0415

    tranches = token_chunks(lignes, BORNE_TRANCHE)
    plafond = MAX_TOKENS_PAR_MODE[mode]
    if (BORNE_CONTEXTE is not None and len(tranches) > 1
            and sum(est_tokens(ligne + "\n") for ligne in lignes) <= BORNE_CONTEXTE):
        return ["\n".join(lignes)], min(plafond * len(tranches), MAX_TOKENS_CONTEXTE)
    return tranches, plafond


def extraire(name: str, *, provider: str = llm.FOURNISSEUR_DEFAUT, seed: Optional[int] = None,
             mistral_api_key: Optional[str] = None, journal: Optional[list] = None,
             attente: float = ATTENTE_RESERVATION) -> dict:
    """Extrait, contrôle et enregistre les faits d'un transcript ; rend le bilan.

    Lève `ExtractionEnPanne` si aucun appel n'a abouti : le fichier précédent,
    s'il existe, reste tel quel.

    `journal` (mesure seulement) reçoit chaque fait proposé, gardé ou refusé avec
    son motif, et chaque appel en échec. Il ne rejoint ni le fichier de faits ni
    l'index : un refus n'y devient pas un fait.

    La réservation des travaux lourds est prise avant tout : l'extraction décharge le
    moteur du compte rendu et charge le sien, ce qu'une transcription ou un compte rendu
    en cours ne doit pas subir. Réentrante par fil, elle est sans effet sous le pipeline
    et le compte rendu ; une exception la rend.
    """
    with resources.heavy(wait=attente, label="extraction des faits"):
        return _extraire(name, provider=provider, seed=seed, mistral_api_key=mistral_api_key,
                         journal=journal)


def _extraire(name: str, *, provider: str, seed: Optional[int], mistral_api_key: Optional[str],
              journal: Optional[list]) -> dict:
    from .experimental.chunking import verifier_citable  # noqa: PLC0415

    # Graine fixe, comme le CR : deux extractions du même transcript se comparent.
    seed = config.EXPERIMENTAL_SEED if seed is None else seed
    item = library.get_transcript(name)
    if item is None:
        raise ValueError(f"Transcript introuvable : {name}")
    meta, body = item.get("meta") or {}, item.get("body") or ""
    document_id = meta.get("id")
    if not document_id:
        raise ValueError(f"Transcript sans identité : {name}")
    verifier_citable(body)
    liste = repliques(body)
    lignes = [f"**{r['locuteur']}** [{r['horodatage']}] {r['texte']}" if r["locuteur"]
              else f"[{r['horodatage']}] {r['texte']}" for r in liste]
    code = str(meta.get("language") or "fr")[:2].lower()
    langue = _LANGUES.get(code, meta.get("language"))
    rappel = f"\n\n{_RAPPEL_LANGUE[code]}" if code in _RAPPEL_LANGUE else ""
    # Le mode suit le fournisseur : consigne, plafond et affichage en dépendent.
    fournisseur = llm.moteur(provider).name
    mode = "local" if llm.est_local(fournisseur) else "cloud"
    # La règle d'envoi avant le premier appel, avec l'autorisation du propriétaire du
    # projet du transcript ; chaque appel la relit ensuite par la façade.
    envoi = library.envoi_du_document(name, meta)
    ultra_autorise = sensitivity.controler(envoi, local=mode == "local")
    systeme = _SYSTEME or CONSIGNES[mode]
    moteur, avertissement = moteur_local() if mode == "local" else (None, None)
    if moteur is not None:
        llm.unload(provider)  # la mémoire du moteur du CR, rendue avant de charger celui de l'ECL
    options = {"moteur": moteur} if moteur is not None else {}
    # Le modèle de l'ECL chez Anthropic (Sonnet) ; le compte rendu garde le sien.
    modele_ecl = modele_cloud(fournisseur) if mode == "cloud" else None
    if modele_ecl:
        options["model"] = modele_ecl

    appels = echecs = cites = 0
    refus = dict.fromkeys(MOTIFS, 0)
    gardes: dict[str, dict] = {}
    tranches, plafond = decouper(lignes, mode)
    for numero, tranche in enumerate(tranches, 1):
        appels += 1
        try:
            reponse = _modele(systeme, f"Langue de la réunion, et des énoncés : {langue}\n\n{tranche}{rappel}",
                              provider=provider, api_key=llm.cle_mistral(provider, mistral_api_key), seed=seed,
                              max_tokens=plafond, retries=2, envoi=envoi, **options)
        except sensitivity.EnvoiRefuse:
            raise  # un refus de la règle d'envoi n'est pas une tranche en panne
        except Exception as exc:  # noqa: BLE001 — une tranche en panne est comptée, pas fatale
            echecs += 1
            _noter(journal, document_id, numero, echec=f"{type(exc).__name__}: {exc}"[:300])
            continue
        proposes = reponse.get("faits") if isinstance(reponse, dict) else None
        if not isinstance(proposes, list):
            echecs += 1
            _noter(journal, document_id, numero, echec="réponse sans liste « faits »")
            continue
        for brut in proposes:
            fait, motif = controler(brut, liste)
            if fait is None:
                refus[motif] += 1
                _noter(journal, document_id, numero, propose=brut, motif=motif)
                continue
            if code in _MOTS_OUTILS and langue_probable(fait["enonce"]) not in (None, code):
                # Un énoncé dans une autre langue que la réunion : sa citation le remplace,
                # mot pour mot et dans la bonne langue, plutôt qu'une phrase à traduire.
                fait = {**fait, "enonce": fait["citation"], "enonce_statut": "cité"}
                cites += 1
            fid = identite(document_id, fait)
            _noter(journal, document_id, numero, propose=brut, garde=fid)
            gardes.setdefault(fid, {"id": fid, "document_id": document_id,
                                    "meeting_id": meta.get("meeting_id"), **fait,
                                    "affiche": fait["affiche"] if mode == "local" else None,
                                    "mode": mode, "fournisseur": fournisseur,
                                    "statut": "actuel", "motif_peremption": None})
    if moteur is not None:
        moteur.unload()  # Granite rend sa mémoire ; le CR rechargera le sien au besoin
    if appels and echecs == appels:
        raise ExtractionEnPanne(
            f"Extraction ECL en panne : aucun des {appels} appel(s) au modèle n'a abouti")
    contenu = {
        "version": VERSION, "document_id": document_id, "meeting_id": meta.get("meeting_id"),
        "extrait_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # Les répliques dont ces faits sont tirés : une source corrigée depuis se voit.
        "empreinte": empreinte(body),
        "modele": moteur.modele_servi() if moteur is not None else (modele_ecl or llm.modele_servi(provider)),
        "mode": mode, "fournisseur": fournisseur, "avertissement": avertissement,
        "appels": appels, "appels_en_echec": echecs,
        "refus": refus, "enonces_cites": cites,
        "faits": sorted(gardes.values(), key=lambda f: (f["secondes"], f["type"], f["id"])),
    }
    if ultra_autorise:
        # Provenance : ces faits d'un ultra-confidentiel viennent d'un fournisseur cloud,
        # sous l'autorisation du propriétaire. Absent sinon.
        contenu["envoi_ultra_autorise"] = True
    # Extraction PARTIELLE : k tranches sur n en échec, 0 < k < n. Ses faits manquent ceux
    # des tranches perdues ; elle ne remplace donc pas une extraction courante, qui reste
    # celle qui fait foi : elle va dans l'historique, marquée. Sans extraction courante,
    # elle devient la courante, marquée ; une relance complète la remplacera.
    partielle = {"tranches_en_echec": echecs, "tranches": appels} if echecs else None
    if partielle:
        contenu["partielle"] = partielle
    with _VERROU:  # fichier et index ensemble : une revérification ne s'intercale pas
        remplace = not (partielle and chemin(document_id).exists())
        if remplace:
            ecrire(contenu)
            indexer_contenu(contenu)
        else:
            _ranger_partielle(contenu)
    return {"document_id": document_id, "gardes": len(contenu["faits"]), "mode": mode,
            "avertissement": avertissement, "partielle": partielle, "remplace": remplace,
            "refuses": sum(refus.values()), "refus": refus, "enonces_cites": cites,
            "appels": appels, "appels_en_echec": echecs}


def _noter(journal: Optional[list], document_id: str, tranche: int, **entree) -> None:
    if journal is not None:
        journal.append({"document_id": document_id, "tranche": tranche, **entree})


# ---------- Fichier qui fait foi ----------

def dossier() -> Path:
    return library.OUTPUT_DIR / ".ecl"


def chemin(document_id: str) -> Path:
    if not re.fullmatch(r"[0-9A-Za-z]+", document_id or ""):
        raise ValueError(f"Identité de document invalide : {document_id!r}")
    return dossier() / f"{document_id}.json"


def versions(document_id: str) -> list[Path]:
    """Les extractions précédentes d'un document, de la plus ancienne à la plus récente."""
    chemin(document_id)  # valide l'identité
    return sorted((dossier() / "versions").glob(f"{document_id}-*.json"))


def _garder_version(cible: Path) -> None:
    """Règle 4 : une extraction remplacée est gardée, datée, jamais écrasée."""
    if not cible.exists():
        return
    try:
        date = str(json.loads(cible.read_text(encoding="utf-8")).get("extrait_le") or "")
    except (OSError, ValueError):
        date = ""
    marque = re.sub(r"[^0-9A-Za-z]", "", date) or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    rangement = dossier() / "versions"
    rangement.mkdir(parents=True, exist_ok=True)
    version, n = rangement / f"{cible.stem}-{marque}.json", 1
    while version.exists():
        version, n = rangement / f"{cible.stem}-{marque}-{n}.json", n + 1
    version.write_bytes(cible.read_bytes())


def _ranger_partielle(contenu: dict) -> Path:
    """Une extraction partielle, rangée dans l'historique sans toucher la courante."""
    rangement = dossier() / "versions"
    rangement.mkdir(parents=True, exist_ok=True)
    marque = re.sub(r"[^0-9A-Za-z]", "", str(contenu.get("extrait_le") or ""))
    version, n = rangement / f"{contenu['document_id']}-{marque}-partielle.json", 1
    while version.exists():
        version, n = rangement / f"{contenu['document_id']}-{marque}-partielle-{n}.json", n + 1
    version.write_text(json.dumps(contenu, ensure_ascii=False, indent=1), encoding="utf-8")
    return version


# Une écriture du fichier de faits à la fois dans ce processus : version gardée, puis
# remplacement, sans qu'une autre écriture (relance, revérification) ne s'intercale.
# Réentrant : `extraire` et `reverifier` le tiennent autour d'`ecrire` et de l'index.
_VERROU = threading.RLock()


def ecrire(contenu: dict) -> Path:
    cible = chemin(contenu["document_id"])
    cible.parent.mkdir(parents=True, exist_ok=True)
    with _VERROU:
        _garder_version(cible)
        # Un fichier provisoire propre à cette écriture, remplacé d'un coup : le fichier
        # qui fait foi est toujours entier, l'ancien ou le nouveau.
        descripteur, provisoire = tempfile.mkstemp(dir=cible.parent, prefix=f".{cible.stem}-",
                                                   suffix=".tmp")
        try:
            with os.fdopen(descripteur, "w", encoding="utf-8") as sortie:
                sortie.write(json.dumps(contenu, ensure_ascii=False, indent=1))
            os.replace(provisoire, cible)
        except BaseException:
            Path(provisoire).unlink(missing_ok=True)
            raise
    return cible


# ---------- Source corrigée : revérification sans modèle ----------

def empreinte(body: str) -> str:
    """Empreinte des répliques d'un transcript (horodatage, locuteur, texte).

    Le front-matter n'en fait pas partie, ni les sections hors répliques : changer un
    titre ou une étiquette ne périme aucun fait.
    """
    lignes = [f"{r['horodatage']}\t{r['locuteur'] or ''}\t{r['texte']}" for r in repliques(body)]
    return hashlib.sha256("\n".join(lignes).encode("utf-8")).hexdigest()


def _revoir(contenu: dict, liste: list[dict]) -> tuple[list[dict], int]:
    """(faits revérifiés, nombre de périmés) : `controler` rejoue sur chaque fait gardé.

    Un fait qui passe reprend l'ancrage de la réplique trouvée (horodatage, locuteur,
    porteur, affichage) et redevient actuel ; un fait qui échoue est gardé tel quel,
    marqué `perime` avec le motif du refus.
    """
    revus, perimes = [], 0
    for fait in contenu.get("faits") or []:
        propose = {cle: fait.get(cle) for cle in ("type", "horodatage", "citation", "enonce", "porteur")}
        controle, motif = controler(propose, liste)
        if controle is None:
            revus.append({**fait, "statut": "perime", "motif_peremption": motif})
            perimes += 1
            continue
        ancrage = {cle: controle[cle] for cle in ("horodatage", "secondes", "locuteur", "porteur")}
        if fait.get("affiche") is not None:  # affichage C : local seulement
            ancrage["affiche"] = controle["affiche"]
        revus.append({**fait, **ancrage, "statut": "actuel", "motif_peremption": None})
    return revus, perimes


def reverifier(document_id: Optional[str], body: str) -> Optional[dict]:
    """Revérifie et réécrit le fichier de faits si la source a changé ; None sinon.

    Appelée à la synchronisation d'un document et par `reindexer`, jamais à la
    lecture. Sans fichier, ou à empreinte égale, rien n'est lu au-delà du fichier ni
    écrit. Rend le contenu réécrit, que l'appelant réindexe.
    """
    if not document_id or not re.fullmatch(r"[0-9A-Za-z]+", document_id):
        return None
    with _VERROU:
        contenu = lire_fichier(document_id)
        if not contenu or contenu.get("document_id") != document_id:
            return None
        actuelle = empreinte(body)
        if contenu.get("empreinte") == actuelle:
            return None
        faits_revus, perimes = _revoir(contenu, repliques(body))
        contenu = {**contenu, "version": VERSION, "empreinte": actuelle, "faits": faits_revus,
                   "reverifie_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "faits_perimes": perimes}
        ecrire(contenu)
        return contenu


def synchroniser(document_id: Optional[str], body: str) -> Optional[dict]:
    """`reverifier`, puis l'index des faits du document : appelée par `library.sync_file`."""
    with _VERROU:
        contenu = reverifier(document_id, body)
        if contenu is not None:
            indexer_contenu(contenu)
        return contenu


def lire_fichier(document_id: str) -> Optional[dict]:
    try:
        return json.loads(chemin(document_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def effacer_fichier(document_id: str) -> None:
    """Purge définitive : le fichier, ses versions précédentes et ses lignes dérivées."""
    chemin(document_id).unlink(missing_ok=True)
    for version in versions(document_id):
        version.unlink(missing_ok=True)
    desindexer(document_id)


# ---------- Tables dérivées ----------

def _conn() -> sqlite3.Connection:
    conn = sqlite_db.connect(library.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    # Une table créée avant les modes n'a pas ces colonnes : elles s'ajoutent, vides,
    # et `reindexer` les remplit depuis les fichiers qui font foi.
    presentes = {r[1] for r in conn.execute("PRAGMA table_info(ecl_faits)")}
    for colonne in ("mode", "fournisseur", "affiche", "statut", "motif_peremption"):
        if colonne not in presentes:
            conn.execute(f"ALTER TABLE ecl_faits ADD COLUMN {colonne} TEXT")
    return conn


_COLONNES = ("id", "document_id", "meeting_id", "type", "horodatage", "secondes",
             "citation", "enonce", "porteur", "locuteur", "mode", "fournisseur", "affiche",
             "statut", "motif_peremption")


def _inserer(conn: sqlite3.Connection, contenu: dict) -> int:
    conn.execute("DELETE FROM ecl_faits WHERE document_id = ?", (contenu["document_id"],))
    faits = contenu.get("faits") or []
    conn.executemany(
        f"INSERT OR REPLACE INTO ecl_faits ({', '.join(_COLONNES)}) "
        f"VALUES ({', '.join('?' * len(_COLONNES))})",
        [tuple(f.get(c) for c in _COLONNES) for f in faits])
    return len(faits)


def indexer_contenu(contenu: dict) -> int:
    conn = _conn()
    try:
        n = _inserer(conn, contenu)
        conn.commit()
        return n
    finally:
        conn.close()


def indexer(name: str) -> int:
    """Rend à l'index les faits d'un document restauré, depuis son fichier."""
    item = library.get_transcript(name)
    document_id = ((item or {}).get("meta") or {}).get("id")
    contenu = lire_fichier(document_id) if document_id else None
    return indexer_contenu(contenu) if contenu else 0


def desindexer(document_id: str) -> None:
    """Archive : les lignes dérivées partent, le fichier reste pour la restauration."""
    conn = _conn()
    try:
        conn.execute("DELETE FROM ecl_faits WHERE document_id = ?", (document_id,))
        conn.commit()
    finally:
        conn.close()


def reindexer() -> dict:
    """Reconstruit `ecl_faits` depuis les fichiers des documents vivants de l'index.

    Le corps de chaque document vient de l'index (`transcripts.body`) : un fichier dont
    l'empreinte diffère, ou qui n'en a pas, est d'abord revérifié (`reverifier`). Deux
    temps : les fichiers, sous `_VERROU` et sans transaction ouverte, puis la table, sans
    `_VERROU` ; une écriture concurrente prend toujours le verrou avant la base, jamais
    l'inverse.
    """
    conn = _conn()
    try:
        vivants = {r[0]: r[1] or "" for r in conn.execute(
            "SELECT id, body FROM transcripts WHERE id IS NOT NULL")}
    finally:
        conn.close()
    fichiers = [f for f in (sorted(dossier().glob("*.json")) if dossier().exists() else [])
                if f.stem in vivants]
    reverifies = sum(1 for f in fichiers if reverifier(f.stem, vivants[f.stem]) is not None)
    conn = _conn()
    try:
        conn.execute("DELETE FROM ecl_faits")
        documents = faits_total = perimes = 0
        illisibles: list[str] = []
        for fichier in fichiers:
            contenu = lire_fichier(fichier.stem)
            if not contenu or contenu.get("document_id") != fichier.stem:
                illisibles.append(fichier.name)
                continue
            faits_total += _inserer(conn, contenu)
            perimes += sum(1 for f in contenu.get("faits") or [] if f.get("statut") == "perime")
            documents += 1
        conn.commit()
        return {"documents": documents, "faits": faits_total, "illisibles": illisibles,
                "reverifies": reverifies, "perimes": perimes}
    finally:
        conn.close()


def traces(document_id: Optional[str]) -> tuple[Optional[int], bool]:
    """(lignes dérivées, fichier présent) d'un document ; `None` si l'index est illisible."""
    if not document_id:
        return 0, False
    try:
        fichier = chemin(document_id).exists()
    except ValueError:
        return 0, False
    try:
        conn = _conn()
    except sqlite3.Error:
        return None, fichier
    try:
        lignes = conn.execute("SELECT COUNT(*) FROM ecl_faits WHERE document_id = ?",
                              (document_id,)).fetchone()[0]
    except sqlite3.Error:
        lignes = None
    finally:
        conn.close()
    return lignes, fichier


# ---------- Lecture bornée ----------

def faits(portee: project_store.Portee, *, type_: Optional[str] = None,
          meeting_id: Optional[str] = None, statut: str = "actuel") -> list[dict]:
    """Les faits que la portée laisse lire, résolue par `api.portee.lecture`.

    Jointure sur `transcripts` : un document archivé ou purgé n'y est plus, une
    portée de partage ne retient que ses documents lisibles. Par défaut les faits
    actuels seuls ; `statut="perime"` rend ceux dont la source a changé, pour les
    montrer comme tels. Une ligne d'avant les statuts est actuelle. Lecture seule :
    rien n'est revérifié ni écrit ici.
    """
    if statut not in STATUTS:
        raise ValueError(f"Statut de fait inconnu : {statut!r}")
    condition, valeurs = project_store.filtre_sql("t.project_id", portee)
    clauses = [condition] if condition else []
    clauses.append("COALESCE(f.statut, 'actuel') = ?")
    valeurs = [*valeurs, statut]
    if type_:
        clauses.append("f.type = ?")
        valeurs = [*valeurs, type_]
    if meeting_id:
        clauses.append("f.meeting_id = ?")
        valeurs = [*valeurs, meeting_id]
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = _conn()
    try:
        lignes = conn.execute(
            f"SELECT f.*, t.name AS document, t.date AS date FROM ecl_faits f "
            f"JOIN transcripts t ON t.id = f.document_id {where} "
            f"ORDER BY t.date, f.document_id, f.secondes, f.id", valeurs).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in lignes]


def aux_faits_perimes() -> set[str]:
    """Les documents de l'index dont au moins un fait est périmé (par nom)."""
    conn = _conn()
    try:
        return {r[0] for r in conn.execute(
            "SELECT DISTINCT t.name FROM ecl_faits f JOIN transcripts t ON t.id = f.document_id "
            "WHERE f.statut = 'perime'")}
    finally:
        conn.close()


def a_extraire() -> tuple[list[str], list[str]]:
    """(transcripts sans fichier de faits ou aux faits périmés, transcripts non citables)."""
    from .experimental.chunking import TranscriptNonCitable, verifier_citable  # noqa: PLC0415

    perimes = aux_faits_perimes()
    conn = _conn()
    try:
        lignes = conn.execute(
            "SELECT name, id, body FROM transcripts "
            "WHERE id IS NOT NULL AND (type IS NULL OR type != 'report') ORDER BY name").fetchall()
    finally:
        conn.close()
    restants, non_citables = [], []
    for r in lignes:
        if chemin(r["id"]).exists() and r["name"] not in perimes:
            continue
        try:
            verifier_citable(r["body"] or "")
        except TranscriptNonCitable:
            non_citables.append(r["name"])
            continue
        restants.append(r["name"])
    return restants, non_citables
