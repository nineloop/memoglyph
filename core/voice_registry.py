"""Décisions HUMAINES sur les voix — autoritatives, versionnées, hors base.

Le registre vocal mélangeait deux natures de données dans la même table SQLite :
ce qu'un humain a décidé (nommer une voix, en fusionner deux) et ce qu'une
machine a calculé (vecteurs, centroïdes, rattachements). L'index est pourtant
déclaré **dérivé et reconstructible** — mais reconstruire perdait les noms et
les fusions, parce qu'ils n'existaient nulle part ailleurs.

Trois conséquences, toutes réelles :

1. L'identité d'une voix était un `INTEGER PRIMARY KEY AUTOINCREMENT`, donc
   **née en base**. Après reconstruction, « voix 3 » ne désignait plus la même
   personne. Un invariant du projet dit l'inverse : rien d'important ne naît
   dans l'index.
2. Un nom saisi à la main disparaissait à la première réindexation.
3. Une fusion était appliquée destructivement : l'effet était gardé, jamais la
   décision. Impossible de la rejouer, ni de savoir qu'elle avait eu lieu.

Ce module tient donc le versant humain dans `output/.voices.json`, à côté du
Markdown qui fait foi. La base redevient purement dérivée : on peut la vider
sans rien perdre de ce qu'un humain a établi.

IDENTITÉ STABLE. Chaque voix reçoit un ULID, comme les documents et les
réunions. Il survit à la reconstruction, au changement d'extracteur et au
renommage du produit.

ESPACE VECTORIEL. Le fichier enregistre le modèle d'extraction, sa révision, la
dimension et la normalisation. Deux extracteurs produisent des vecteurs
**incomparables** : une divergence déclenche une reconstruction EXPLICITE, jamais
un mélange silencieux — et les noms et fusions, eux, y survivent.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import docid

SCHEMA_VERSION = 1

# Résolu à l'APPEL, jamais figé à l'import. Un chemin calculé au chargement du
# module échappe à toute redirection de `OUTPUT_DIR` faite ensuite : les tests
# croient être isolés et écrivent dans le corpus réel, avec
# `voiceprint.SIDECAR` comme ici. `FICHIER` reste exposé pour
# qu'un test puisse forcer un chemin explicitement.
FICHIER: Optional[Path] = None

_VERROU = threading.RLock()


class RegistreIllisible(RuntimeError):
    """Le fichier existe mais n'a pas pu être lu.

    Distinct de « absent », qui est un état normal. On n'écrit jamais par-dessus
    une lecture ratée : ce serait effacer des décisions humaines.
    """


class ExtracteurIncompatible(RuntimeError):
    """Les vecteurs stockés viennent d'un autre extracteur.

    Comparer des vecteurs de deux espaces rendrait des distances dépourvues de
    sens, sans que rien ne le signale. On exige une reconstruction explicite.
    """


def _chemin(root: Optional[Path] = None) -> Path:
    if root is not None:
        return root / ".voices.json"
    if FICHIER is not None:
        return FICHIER
    from .config import OUTPUT_DIR

    return OUTPUT_DIR / ".voices.json"


def _vide() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "extracteur": None,
        "voix": {},      # uid ULID -> {label, label_source, created_at}
        "fusions": [],   # journal APPEND-ONLY : {source, cible, at}
    }


def lire(root: Optional[Path] = None, *, strict: bool = False) -> dict:
    """Charge les décisions humaines. `strict` avant toute réécriture."""
    p = _chemin(root)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _vide()
    except Exception as exc:  # noqa: BLE001 — JSON corrompu, droits, I/O
        if strict:
            raise RegistreIllisible(f"registre des voix illisible ({p}) : {exc}") from exc
        print(f"[voix] registre illisible ({p}) — décisions humaines ignorées ce tour")
        return _vide()
    if not isinstance(data, dict):
        if strict:
            raise RegistreIllisible(f"registre des voix malformé ({p})")
        return _vide()
    base = _vide()
    base.update({k: v for k, v in data.items() if k in base})
    return base


def _ecrire(data: dict, root: Optional[Path] = None) -> None:
    """Écriture atomique : un plantage ne doit pas laisser un fichier tronqué."""
    p = _chemin(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".json.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
    finally:
        tmp.unlink(missing_ok=True)


# ---------- Espace vectoriel ----------

def specification_extracteur(
    *, modele: str, dimension: int, revision: str = "", normalisation: str = "l2",
    pretraitement: str = "",
) -> dict:
    """Décrit l'espace vectoriel. Deux specs différentes = vecteurs incomparables."""
    return {
        "modele": modele,
        "revision": revision,
        "dimension": int(dimension),
        "normalisation": normalisation,
        "pretraitement": pretraitement,
    }


def extracteur_enregistre(root: Optional[Path] = None) -> Optional[dict]:
    return lire(root).get("extracteur")


def verifier_extracteur(spec: dict, root: Optional[Path] = None) -> None:
    """Lève si les vecteurs stockés viennent d'un AUTRE extracteur.

    Un registre neuf (sans spec) est accepté : il n'y a rien à contredire.
    """
    stocke = extracteur_enregistre(root)
    if stocke is None or stocke == spec:
        return
    ecarts = [
        f"{cle} : {stocke.get(cle)!r} -> {spec.get(cle)!r}"
        for cle in sorted(set(stocke) | set(spec))
        if stocke.get(cle) != spec.get(cle)
    ]
    raise ExtracteurIncompatible(
        "Les empreintes stockées viennent d'un autre extracteur ; "
        "une reconstruction est requise. Écarts — " + " ; ".join(ecarts)
    )


def declarer_extracteur(spec: dict, root: Optional[Path] = None) -> None:
    """Enregistre l'espace vectoriel courant. Les noms et fusions sont CONSERVÉS.

    C'est le point central : changer d'extracteur invalide les vecteurs, jamais
    les décisions humaines. On rebâtit les rattachements, pas les noms.
    """
    with _VERROU:
        data = lire(root, strict=True)
        data["extracteur"] = spec
        _ecrire(data, root)


# ---------- Décisions humaines ----------

def nouvelle_voix(root: Optional[Path] = None) -> str:
    """Forge une identité de voix STABLE (ULID) et la retourne.

    N'ÉCRIT RIEN. L'entrée n'apparaît qu'au premier contenu réel — des membres
    (`enregistrer_membres`) ou un nom (`nommer`), qui la créent tous deux au
    besoin.

    Écrire ici produisait des fantômes : `_recompute_centroid` supprime une voix
    dès qu'elle perd sa dernière empreinte (`core/voiceprint.py:349`), et une
    réindexation vide transitoirement chaque voix. La base les supprimait,
    l'entrée du magasin survivait. Mesuré après une seule réindexation :
    37 entrées pour 15 voix réelles, dont 18 sans membres ni nom.

    Sans gravité en soi — une entrée vide ne peut capter aucune décision
    humaine — mais elles s'accumulent, et un fichier de décisions humaines
    encombré de fantômes finit par masquer les vraies.
    """
    return docid.nouveau()


def elaguer(root: Optional[Path] = None) -> dict:
    """Retire les entrées SANS aucun contenu : ni nom, ni membres, ni fusion.

    Conservateur par construction : une entrée qui porte la moindre trace de
    décision humaine — un nom, même effacé depuis, ou une participation à une
    fusion — n'est jamais retirée. On n'élague que ce qui ne dit rien.
    """
    with _VERROU:
        data = lire(root, strict=True)
        impliquees = {f["source"] for f in data.get("fusions", [])}
        impliquees |= {f["cible"] for f in data.get("fusions", [])}
        avant = len(data["voix"])
        data["voix"] = {
            uid: entree for uid, entree in data["voix"].items()
            if entree.get("label") or entree.get("membres") or uid in impliquees
        }
        retirees = avant - len(data["voix"])
        if retirees:
            _ecrire(data, root)
    return {"retirees": retirees, "restantes": len(data["voix"])}


# ---------- Mutations pures ----------
#
# Séparées des fonctions publiques pour qu'une DÉCISION puisse être publiée en
# UNE seule écriture : nommer une voix, c'est
# `nommer()` puis `enregistrer_membres()`, donc deux `_ecrire()` atomiques mais
# indépendants. Si le second échouait, le nom était déjà publié, SQLite restait
# ancien, et l'API annonçait un échec — état ambigu, invisible à un test qui
# n'injecte l'échec que dans le premier appel.

def _entree(data: dict, uid: str) -> dict:
    return data["voix"].setdefault(uid, {
        "label": None, "label_source": None,
        "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    })


def _appliquer_nom(data: dict, uid: str, label: Optional[str], source: str) -> None:
    entree = _entree(data, uid)
    if source == "auto" and entree.get("label_source") == "manual":
        return  # ne jamais écraser une décision humaine par une déduction
    entree["label"] = (label or "").strip() or None
    entree["label_source"] = source if entree["label"] else None


def _appliquer_membres(data: dict, uid: str, membres: list[tuple[str, str]]) -> None:
    _entree(data, uid)["membres"] = sorted({_cle_membre(d, s) for d, s in membres})


def _appliquer_fusion(data: dict, source_uid: str, cible_uid: str) -> None:
    data["fusions"].append({
        "source": source_uid, "cible": cible_uid,
        "at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    })


def nommer_avec_membres(uid: str, label: Optional[str],
                        membres: list[tuple[str, str]], *,
                        source: str = "manual", root: Optional[Path] = None) -> None:
    """Nomme une voix ET fixe ses membres, en UNE écriture. Tout ou rien."""
    if source not in ("manual", "auto"):
        raise ValueError(f"Provenance de nom inconnue : {source!r}")
    with _VERROU:
        data = lire(root, strict=True)
        _appliquer_nom(data, uid, label, source)
        _appliquer_membres(data, uid, membres)
        _ecrire(data, root)


def fusionner_avec_membres(source_uid: str, cible_uid: str,
                           membres: list[tuple[str, str]],
                           root: Optional[Path] = None) -> None:
    """Journalise une fusion ET transfère les membres, en UNE écriture.

    Sans ça, réessayer après un échec du second appel ajoutait une SECONDE
    entrée au journal append-only pour une seule décision humaine.
    """
    if source_uid == cible_uid:
        raise ValueError("Une voix ne se fusionne pas avec elle-même")
    with _VERROU:
        data = lire(root, strict=True)
        _appliquer_fusion(data, source_uid, cible_uid)
        _appliquer_membres(data, cible_uid, membres)
        _ecrire(data, root)


def nommer(uid: str, label: Optional[str], *, source: str = "manual",
           root: Optional[Path] = None) -> None:
    """Nomme une voix. `manual` prime toujours sur `auto` (déduit d'un profil LLM).

    Un nom déduit par un LLM n'est jamais autoritatif — invariant du projet. On
    enregistre donc SA PROVENANCE, pas seulement sa valeur : sans elle, on ne
    saurait plus si un humain a validé ce nom ou si une machine l'a proposé.
    """
    if source not in ("manual", "auto"):
        raise ValueError(f"Provenance de nom inconnue : {source!r}")
    with _VERROU:
        data = lire(root, strict=True)
        _appliquer_nom(data, uid, label, source)
        _ecrire(data, root)


def fusionner(source_uid: str, cible_uid: str, root: Optional[Path] = None) -> None:
    """Journalise une fusion. APPEND-ONLY : la décision survit à son effet.

    Auparavant seule la conséquence était appliquée en base ; la décision
    n'existait nulle part. Après reconstruction, deux voix qu'un humain avait
    déclarées identiques redevenaient distinctes, sans trace.
    """
    if source_uid == cible_uid:
        raise ValueError("Une voix ne se fusionne pas avec elle-même")
    with _VERROU:
        data = lire(root, strict=True)
        _appliquer_fusion(data, source_uid, cible_uid)
        _ecrire(data, root)


def cible_effective(uid: str, root: Optional[Path] = None,
                    _data: Optional[dict] = None) -> str:
    """Suit la chaîne de fusions jusqu'à la voix survivante.

    A fusionnée dans B, puis B dans C : demander A doit rendre C. Le garde-fou
    sur les cycles n'est pas théorique — une fusion en aller-retour bouclerait.
    """
    data = _data if _data is not None else lire(root)
    redirections = {f["source"]: f["cible"] for f in data.get("fusions", [])}
    vus = {uid}
    courant = uid
    while courant in redirections:
        courant = redirections[courant]
        if courant in vus:
            break  # cycle : on s'arrête sur la dernière voix atteinte
        vus.add(courant)
    return courant


def etiquette(uid: str, root: Optional[Path] = None) -> tuple[Optional[str], Optional[str]]:
    """(nom, provenance) de la voix, en suivant les fusions."""
    data = lire(root)
    entree = data["voix"].get(cible_effective(uid, _data=data)) or {}
    return entree.get("label"), entree.get("label_source")


def _cle_membre(document_id: str, speaker_local: str) -> str:
    """Clé d'un locuteur dans un document. Séparateur nul : il ne peut pas
    apparaître dans un nom de document ni dans « Speaker N », donc aucune
    collision par concaténation."""
    return f"{document_id}\u0000{speaker_local}"


def enregistrer_membres(uid: str, membres: list[tuple[str, str]],
                        root: Optional[Path] = None) -> None:
    """Retient QUELS locuteurs composent cette voix : `(document_id, speaker_local)`.

    C'est la seule clé qui survive à une reconstruction. Le `voice_id` entier
    disparaît avec la base, et le vecteur change avec l'extracteur — mais
    « Speaker 2 du document X » reste « Speaker 2 du document X ». C'est donc par
    là qu'on retrouve à quelle voix un nom appartenait.
    """
    with _VERROU:
        data = lire(root, strict=True)
        _appliquer_membres(data, uid, membres)
        _ecrire(data, root)


def identite_pour(membres: list[tuple[str, str]],
                  root: Optional[Path] = None) -> Optional[str]:
    """Retrouve l'identité d'une voix reconstruite, par recouvrement de membres.

    Après reconstruction, un cluster n'a plus d'identité : on lui rend celle de
    la voix connue avec laquelle il partage le plus de locuteurs. Sans ce
    rattachement, le nom saisi par l'humain resterait dans le fichier sans que
    rien ne sache à quelle voix il s'applique.

    Retourne `None` si aucun recouvrement — mieux vaut une voix anonyme qu'un
    nom attribué à la mauvaise personne.
    """
    data = lire(root)
    cles = {_cle_membre(d, s) for d, s in membres}
    if not cles:
        return None
    meilleur, score = None, 0
    for uid, entree in data["voix"].items():
        commun = len(cles & set(entree.get("membres") or []))
        if commun > score:
            meilleur, score = uid, commun
    return cible_effective(meilleur, _data=data) if meilleur else None


def etat(root: Optional[Path] = None) -> dict:
    """Vue d'ensemble, pour le diagnostic et l'interface."""
    data = lire(root)
    nommees = [u for u, v in data["voix"].items() if v.get("label")]
    return {
        "schema_version": data.get("schema_version"),
        "extracteur": data.get("extracteur"),
        "voix": len(data["voix"]),
        "voix_nommees": len(nommees),
        "nommees_manuellement": sum(
            1 for u in nommees if data["voix"][u].get("label_source") == "manual"
        ),
        "fusions": len(data.get("fusions", [])),
    }
