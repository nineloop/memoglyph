"""Les huit étapes du pipeline expérimental, en fonctions pures.

Étapes LLM (1, 3, 5, 6) : passent par la façade `core.llm` (donc Qwen local ET
Mistral, même code). Étapes déterministes (2, 4, 7) : code pur.

Aucune étape n'est abandonnée en silence : un échec (JSON illisible, appel en
erreur) est ENREGISTRÉ dans le journal de diagnostic et remonté.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from typing import Optional

from core import factcheck, llm, sensitivity

from . import prompts, guard, timestamps
from .chunking import est_tokens

THEMES = tuple(f"T{i}" for i in range(1, 6))
FALLBACK = ("other", "dialogic_noise", "off_topic")

# --- Strates de contenu (hiérarchie non destructive) ---
# MAIN     : T1..T5 (thématique) — sections pleines, rendues par LLM (fidèle 1:1).
# SECONDARY: `other` — on-sujet sans thème principal — « Autres points ».
# CLOSING  : `off_topic` (queue) + `closing` (démoté) — clôture/annonces, bref.
# NOISE    : `dialogic_noise` court — peut rester hors du corps (provenance gardée).
# Seule la strate NOISE peut disparaître du corps. SECONDARY et CLOSING sont
# rendues VERBATIM (texte de l'unité + [gid]) : compact, traçable, garde trivial.
SECONDARY_BUCKET = "other"
CLOSING_BUCKETS = ("off_topic", "closing")
NOISE_BUCKET = "dialogic_noise"


def collect_tiers(units: list[dict], final: dict[str, str]) -> dict:
    """Répartit les unités validées dans les strates SECONDARY/CLOSING/NOISE
    (fonction pure, sans LLM). MAIN (T*) est rendue à part par `render_and_guard`.

    Aucune unité n'est SUPPRIMÉE : `other`→SECONDARY, `off_topic`/`closing`→CLOSING,
    `dialogic_noise`→NOISE (hors corps mais tracée). Ordre chronologique.
    """
    byid = {u["gid"]: u for u in units}

    def _rows(pred):
        gids = [u["gid"] for u in units if pred(final.get(u["gid"]))]
        gids.sort(key=lambda g: byid[g]["start"])
        return [{"gid": g, "kind": byid[g]["kind"], "start": byid[g]["start"],
                 "text": byid[g]["text"]} for g in gids]

    return {
        "secondary": _rows(lambda b: b == SECONDARY_BUCKET),
        "closing": _rows(lambda b: b in CLOSING_BUCKETS),
        "noise": _rows(lambda b: b == NOISE_BUCKET),
    }


# ---------- utilitaires ----------
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s)).strip()


def b4_guard_unit(text, support, byid) -> tuple[bool, Optional[str], dict]:
    """Garde déterministe d'une synthèse B4 (thèse/tension). Réutilise le garde de
    contenance de l'étape 7. Renvoie (accepté?, raison_de_rejet, détails).

    Rejette : texte vide, support vide, ou tout nombre/entité nommée du texte
    absent des unités CITÉES. Aucun appariement flou. Un débordement purement
    sémantique (sans nombre/entité nouveau) n'est PAS détectable ainsi — assumé.
    """
    text = (text or "").strip()
    if not text:
        return False, "empty_text", {}
    if not support:
        return False, "empty_support", {}
    sup_txt = " ".join(byid[g]["text"] for g in support if g in byid)
    chk = guard.check_sentence(text, sup_txt)
    if not chk["ok"]:
        return False, "containment", {"missing_numbers": chk["missing_numbers"],
                                      "missing_entities": chk["missing_entities"]}
    return True, None, {}


def _call(system: str, user: str, *, provider: str, seed: int, max_tokens: int,
          calls: list, envoi: sensitivity.Envois = None) -> Optional[dict]:
    """Un appel LLM BORNÉ via la façade `chat_json` (max_tokens + reprises + réparation).

    `max_tokens` est essentiel : sans borne, Qwen part parfois en boucle
    (mesuré : 51 441 caractères, ~15k jetons) et la latence explose. Journalise.

    `envoi` : le document source, contrôlé par la façade avant l'appel. Un refus
    de la règle d'envoi traverse : ce n'est pas un appel en échec à compter.
    """
    t0 = time.perf_counter()
    err = None
    obj: Optional[dict] = None
    try:
        obj = llm.chat_json(system, user, provider=provider, seed=seed,
                            max_tokens=max_tokens, retries=2, envoi=envoi)
    except sensitivity.EnvoiRefuse:
        raise
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
    dt = round(time.perf_counter() - t0, 2)
    calls.append({
        "latency_s": dt, "error": err,
        "in_tok_est": est_tokens(system + user),
        "out_tok_est": est_tokens(json.dumps(obj, ensure_ascii=False)) if obj else 0,
        "parsed": isinstance(obj, dict),
    })
    return obj if isinstance(obj, dict) else None


# ---------- Étape 1 : extraction atomique ----------
def extract_atomic(chunks: list[str], *, provider: str, seed: int, diag: dict,
                   max_tokens: int = 5000, envoi: sensitivity.Envois = None) -> list[dict]:
    calls: list = []
    raw_units: list[dict] = []
    failed_chunks: list[int] = []
    for idx, chunk in enumerate(chunks, 1):
        # Borne FINIE ⇒ pas d'emballement, assez haute pour ne pas amputer
        # l'extraction (mesuré : 2000 tronquait la 1re tranche dense). Réglée par
        # le profil d'exécution ; défaut = celle du profil prudent (5000).
        obj = _call(prompts.EXTRACT_SYSTEM, prompts.extract_user(chunk),
                    provider=provider, seed=seed, max_tokens=max_tokens, calls=calls,
                    envoi=envoi)
        arr = (obj or {}).get("units")
        if not isinstance(arr, list):
            failed_chunks.append(idx)
            continue
        for u in arr:
            if isinstance(u, dict):
                raw_units.append(u)
    diag["stage1_extract"] = {
        "chunks": len(chunks), "failed_chunks": failed_chunks,
        "raw_units": len(raw_units), "calls": calls,
        "latency_s": round(sum(c["latency_s"] for c in calls), 1),
        "in_tok_est": sum(c["in_tok_est"] for c in calls),
        "out_tok_est": sum(c["out_tok_est"] for c in calls),
    }
    return raw_units


# ---------- Étape 2 : validation déterministe + consolidation ----------
# Une unité est une reformulation du modèle : un chiffre qu'elle ajoute passerait
# ensuite le garde de l'étape 7, qui compare le rendu aux unités elles-mêmes. Ses
# chiffres doivent donc figurer dans la parole autour de son horodatage, dans la
# fenêtre de l'ancrage de l'ECL (`core/ecl.py`, ±30 s).
FENETRE_CHIFFRES_S = 30
_REPLIQUE = re.compile(r"\[(\d{1,2}):(\d{2}):(\d{2})\]\s*(.*)$")


def _secondes(horodatage: str) -> Optional[int]:
    parties = (horodatage or "").split(":")
    if len(parties) != 3 or not all(p.isdigit() for p in parties):
        return None
    h, m, s = (int(p) for p in parties)
    return h * 3600 + m * 60 + s


def _paroles(lines: list[str]) -> list[tuple[int, str]]:
    """(secondes, texte) de chaque réplique, sans le locuteur ni l'horodatage."""
    out: list[tuple[int, str]] = []
    for ln in lines:
        m = _REPLIQUE.search(ln)
        if m:
            out.append((int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]), m[4]))
    return out


# Porteur d'une action : gardé seulement s'il est DIT. Nommé dans la parole à
# ±FENETRE_CHIFFRES_S de l'unité, ou voix de la réplique de l'unité quand elle s'y
# engage à la première personne (même règle que l'ECL, `core/ecl.py`). Sinon None :
# rien n'est deviné. Repère grammatical, pas une liste de personnes.
_LOCUTEUR = re.compile(r"^\*\*(?P<locuteur>[^*\n]+?)\*\*\s*\[")
_PREMIERE_PERSONNE = re.compile(
    r"\b(i'll|i will|i'm going to|i am going to|je vais|je m'en occupe|je m'en charge)\b")


def _mots(texte: str) -> str:
    """Mots en minuscules, ponctuation retirée, entourés d'espaces (recherche par mot entier)."""
    texte = unicodedata.normalize("NFKC", texte or "").casefold().replace("’", "'")
    return " " + " ".join(re.findall(r"[\w']+", texte)) + " "


def porteur_dit(owner: object, start: str, lines: Optional[list[str]]) -> Optional[str]:
    """Le porteur proposé s'il est dit autour de `start`, sinon None."""
    porteur = str(owner or "").strip()
    t0 = _secondes(start)
    if not porteur or t0 is None or lines is None:
        return None
    cherche = _mots(porteur)
    if cherche.strip() == "":
        return None
    for ln in lines:
        m = _REPLIQUE.search(ln)
        if not m:
            continue
        sec = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
        if abs(sec - t0) > FENETRE_CHIFFRES_S:
            continue
        if cherche in _mots(m[4]):
            return porteur
        voix = _LOCUTEUR.match(ln.strip())
        if (sec == t0 and voix and _mots(voix["locuteur"]) == cherche
                and _PREMIERE_PERSONNE.search(_mots(m[4]))):
            return porteur
    return None


def chiffres_hors_parole(text: str, start: str, paroles: list[tuple[int, str]]) -> list[str]:
    """Chiffres de `text` absents de la parole à ±FENETRE_CHIFFRES_S de `start`."""
    t0 = _secondes(start)
    if t0 is None:
        return []
    proches = [t for sec, t in paroles if abs(sec - t0) <= FENETRE_CHIFFRES_S]
    return factcheck.chiffres_non_ancres(text, proches)


# Une unité typée (décision, action, question ouverte) de moins de mots que ceci n'a
# pas de contenu à ranger en rubrique : « Decide now. », « Three. », « Mm-hmm. ».
# Elle redevient un `fact`, comptée. Un mot : une suite sans blanc qui contient au
# moins une lettre ou un chiffre (« Mm-hmm. » en est un).
MOTS_MIN_TYPEE = 3
# Un `fact` au même énoncé qu'une unité typée à cet écart ou moins porte la même
# parole : il est retiré, compté et listé (la marge d'horodatage de la couverture).
DOUBLON_FAIT_S = 15


def enonce_normalise(texte: str) -> str:
    """L'énoncé à la normalisation près : minuscules, sans accent, ponctuation et
    symboles remplacés par un blanc, blancs réduits. Déterministe, sans modèle."""
    return _norm(texte)


def _mots_pleins(texte: str) -> int:
    return sum(1 for t in texte.split() if re.search(r"[^\W_]", t))


def validate_units(raw_units: list[dict], valid_ts: set[str], *, diag: dict,
                   lines: Optional[list[str]] = None) -> list[dict]:
    """Unités gardées. Avec `lines`, une unité dont un chiffre manque à la parole
    proche est écartée et comptée (`rejected_unsupported_numbers`), jamais réécrite.

    Deux requalifications, comptées : une unité typée trop courte redevient `fact`
    (`rubric_fragments_to_fact`) ; un `fact` doublon d'une unité typée (même énoncé
    normalisé, à `DOUBLON_FAIT_S` près) est retiré (`fact_duplicates_removed`, les
    vingt premiers listés dans `fact_duplicates_units`) : sa parole reste portée par
    l'unité typée, dans sa rubrique.
    """
    kept: list[dict] = []
    fragments = 0
    doublons_faits: list[dict] = []

    def _retirer_fait(fait: dict, typee: dict) -> None:
        kept.remove(fait)
        doublons_faits.append({"start": fait["start"], "text": fait["text"],
                               "kept_kind": typee["kind"], "kept_start": typee["start"]})
    rejected_ts = 0
    rejected_numbers: list[dict] = []
    paroles = _paroles(lines) if lines is not None else None
    stuffed = 0
    exact_dups = 0
    bad_kind = 0
    normalized_ts = 0
    owner_dropped = 0
    for u in raw_units:
        start = u.get("start")
        if isinstance(start, list):
            stuffed += 1
            start = start[0] if start else None
        # Réconciliation d'horodatage (commune, provider-agnostique) : reformate
        # MM:SS → HH:MM:SS puis EXIGE l'appartenance exacte. Rejette (jamais
        # d'invention) un horodatage introuvable. Inerte si déjà canonique.
        canon = timestamps.resolve(start, valid_ts)
        if canon is None:
            rejected_ts += 1
            continue
        if canon != start:
            normalized_ts += 1
        start = canon
        kind = u.get("kind")
        if kind not in prompts.ALLOWED_KINDS:
            bad_kind += 1
            kind = "fact"
        text = (u.get("text") or "").strip()
        if not text:
            continue
        if kind in prompts.RUBRIC_KINDS and _mots_pleins(text) < MOTS_MIN_TYPEE:
            fragments += 1
            kind = "fact"
        meme = next((k for k in kept if _norm(k["text"]) == _norm(text) and k["start"] == start), None)
        # Au même horodatage, l'unité typée l'emporte sur le `fact` venu avant elle.
        remplace = meme if meme is not None and meme["kind"] == "fact" and kind in prompts.RUBRIC_KINDS \
            else None
        if meme is not None and remplace is None:
            exact_dups += 1
            continue
        if paroles is not None:
            manquants = chiffres_hors_parole(text, start, paroles)
            if manquants:
                rejected_numbers.append({"start": start, "text": text, "missing_numbers": manquants})
                continue
        unite = {"kind": kind, "text": text, "start": start,
                 "end": u.get("end"), "speaker": u.get("speaker")}
        # Le porteur n'a de sens que pour une action, et seulement s'il est dit : la
        # clé n'existe que sur les actions.
        owner = porteur_dit(u.get("owner"), start, lines) if kind == "action" else None
        if str(u.get("owner") or "").strip() and owner is None:
            owner_dropped += 1
        if kind == "action":
            unite["owner"] = owner
        if remplace is not None:
            _retirer_fait(remplace, unite)
        kept.append(unite)
    # Le `fact` qui redit une unité typée à `DOUBLON_FAIT_S` près.
    typees = [k for k in kept if k["kind"] in prompts.RUBRIC_KINDS]
    for fait in [k for k in kept if k["kind"] == "fact"]:
        t0 = _secondes(fait["start"])
        typee = next((t for t in typees if _norm(t["text"]) == _norm(fait["text"])
                      and t0 is not None and (t1 := _secondes(t["start"])) is not None
                      and abs(t1 - t0) <= DOUBLON_FAIT_S), None)
        if typee is not None:
            _retirer_fait(fait, typee)
    for i, k in enumerate(kept, 1):
        k["gid"] = f"g{i}"
    chrono = [k["gid"] for k in sorted(kept, key=lambda k: k["start"])]
    diag["stage2_validate"] = {
        "kept": len(kept), "rejected_unsupported_ts": rejected_ts,
        "rejected_unsupported_numbers": len(rejected_numbers),
        "unsupported_numbers_units": rejected_numbers[:20],
        "normalized_ts": normalized_ts,
        "stuffed_start": stuffed, "exact_dups_removed": exact_dups,
        "bad_kind_coerced": bad_kind, "owner_dropped": owner_dropped,
        "rubric_fragments_to_fact": fragments,
        "fact_duplicates_removed": len(doublons_faits),
        "fact_duplicates_units": doublons_faits[:20],
        "chronology_len": len(chrono),
    }
    return kept


# ---------- Étape 3 : classification fermée ----------
def classify_units(units: list[dict], themes: list[str], *, provider: str, seed: int,
                   diag: dict, batch: int = 10, max_tokens: int = 800,
                   envoi: sensitivity.Envois = None) -> dict[str, str]:
    labels = {f"T{i}": lbl for i, lbl in enumerate(themes[:5], 1)}
    allowed = set(labels) | set(FALLBACK)
    bucket_block = "\n".join(f"{k} = {v}" for k, v in labels.items()) + (
        "\nother = on-topic but fits no theme"
        "\ndialogic_noise = interjection/question/filler with no information"
        "\noff_topic = unrelated to the subject"
    )
    calls: list = []
    result: dict[str, str] = {}
    ids = {u["gid"] for u in units}
    for k in range(0, len(units), batch):
        sub = units[k:k + batch]
        block = "\n".join(f'{u["gid"]} | {u["kind"]} | {u["text"]}' for u in sub)
        obj = _call(prompts.CLASSIFY_SYSTEM, prompts.classify_user(bucket_block, block),
                    provider=provider, seed=seed, max_tokens=max_tokens, calls=calls,
                    envoi=envoi)
        arr = (obj or {}).get("assignments")
        if not isinstance(arr, list):
            continue
        for o in arr:
            if isinstance(o, dict) and o.get("id") in ids and o.get("bucket") in allowed:
                result[o["id"]] = o["bucket"]
    # Toute unité non classée (batch en échec) tombe déterministiquement en 'other'
    unclassified = [g for g in ids if g not in result]
    for g in unclassified:
        result[g] = "other"
    diag["stage3_classify"] = {
        "classified": len(result), "unclassified_forced_other": len(unclassified),
        "labels": labels, "calls": calls,
        "latency_s": round(sum(c["latency_s"] for c in calls), 1),
    }
    return result


# ---------- Étape 4 : veto d'exclusion déterministe ----------
def apply_veto(classified: dict[str, str], units: list[dict], *, diag: dict
               ) -> tuple[dict[str, str], list[str]]:
    byid = {u["gid"]: u for u in units}
    chron = sorted(units, key=lambda u: u["start"])
    gids_chron = [u["gid"] for u in chron]

    # --- Heuristique de segment de clôture (déterministe, NON DESTRUCTIVE) ---
    # Un bulletin/segment de fin (ex. météo TV) est souvent THÉMATISÉ à tort par
    # le classifieur (« canicule/orages/foudre » jugé proche du climat) et n'est
    # donc jamais en off_topic. Le seul signal fiable est STRUCTUREL : un locuteur
    # qui n'apparaît QUE dans la dernière portion du fil = changement de segment
    # (présentateur météo, autre sujet). MAIS ce signal ne DÉTRUIT plus rien : il
    # DÉMOTE seulement vers la strate CLÔTURE (rendue brièvement). Coût d'un faux
    # positif = « montré brièvement », jamais « perdu ».
    #
    # Garde-fou anti-débordement (cas du débat télévisé) : on ne démote QUE si le nouveau
    # locuteur tient la parole JUSQU'À LA FIN. Si un locuteur du corps reprend
    # APRÈS lui (ex. un modérateur donne la parole « je te laisse conclure » puis
    # un intervenant principal conclut), ce n'était pas un segment de clôture mais
    # une simple passation — on ne démote RIEN, la conclusion reste en strate
    # principale. Aucun filtrage sémantique/flou.
    demoted: set[str] = set()
    n = len(gids_chron)
    if n >= 8:
        tail0 = int(n * 0.75)
        body_spk = {byid[g].get("speaker") for g in gids_chron[:tail0]}
        new_tail_spk = {byid[g].get("speaker") for g in gids_chron[tail0:]} - body_spk - {None}
        if new_tail_spk:
            # 1er index où un NOUVEAU locuteur de queue prend la parole.
            p = next(i for i in range(n) if byid[gids_chron[i]].get("speaker") in new_tail_spk)
            # Segment de clôture GENUINE : de p à la fin, seuls des nouveaux
            # locuteurs de queue parlent. Sinon (locuteur principal qui reprend)
            # = interjection, pas un segment — on ne démote rien.
            if all(byid[g].get("speaker") in new_tail_spk for g in gids_chron[p:]):
                demoted = set(gids_chron[p:])   # le segment du nouveau locuteur (ex. météo)
                # + la passation contiguë du présentateur juste avant (même
                #   locuteur), BORNÉE à la zone de queue.
                handoff = byid[gids_chron[p - 1]].get("speaker") if p > 0 else None
                j = p - 1
                while j >= tail0 and handoff and byid[gids_chron[j]].get("speaker") == handoff:
                    demoted.add(gids_chron[j])
                    j -= 1

    # bloc de queue contigu classé off_topic : NON supprimé — reste en strate
    # CLÔTURE (rendu brièvement). Le reste des off_topic est réassigné (rescousse).
    tail: set[str] = set()
    for g in reversed(gids_chron):
        if classified.get(g) == "off_topic":
            tail.add(g)
        else:
            break

    def nearest_theme(gid: str) -> str:
        pos = gids_chron.index(gid)
        for d in range(1, len(gids_chron)):
            for j in (pos - d, pos + d):
                if 0 <= j < len(gids_chron) and classified.get(gids_chron[j]) in THEMES:
                    return classified[gids_chron[j]]
        return "other"

    final: dict[str, str] = {}
    overrides: list[dict] = []
    for u in units:
        g = u["gid"]
        b = classified.get(g, "other")
        if g in demoted:
            # DÉMOTION (jamais suppression) : segment de clôture → strate CLÔTURE.
            final[g] = "closing"
            if b != "closing":
                overrides.append({"gid": g, "from": b, "to": "closing_demoted_tail"})
        elif b == "off_topic" and g not in tail:
            nb = nearest_theme(g)
            final[g] = nb
            overrides.append({"gid": g, "from": "off_topic", "to": nb})
        elif b == "dialogic_noise" and len(re.findall(r"\w+", byid[g]["text"])) > 8:
            nb = nearest_theme(g)
            final[g] = nb
            overrides.append({"gid": g, "from": "dialogic_noise", "to": nb})
        else:
            final[g] = b
    dist: dict[str, int] = {}
    for b in final.values():
        dist[b] = dist.get(b, 0) + 1
    diag["stage4_veto"] = {"overrides": overrides, "distribution": dict(sorted(dist.items())),
                           "demoted_tail": sorted(demoted, key=lambda g: byid[g]["start"])}
    return final, gids_chron


# ---------- Étape 5 : structure bornée ----------
def structure(units: list[dict], final: dict[str, str], themes: list[str], *,
              provider: str, seed: int, diag: dict, max_tokens: int = 1200,
              envoi: sensitivity.Envois = None) -> dict:
    labels = {f"T{i}": lbl for i, lbl in enumerate(themes[:5], 1)}
    byid = {u["gid"]: u for u in units}
    shown: set[str] = set()
    blocks: list[str] = []
    for tkey, label in labels.items():
        mem = [g for g, b in final.items() if b == tkey]
        reps = sorted(mem, key=lambda g: -len(byid[g]["text"]))[:3]
        if not reps:
            continue
        blocks.append(f"THEME {label}:")
        for g in reps:
            blocks.append(f"  {g} | {byid[g]['text']}")
            shown.add(g)
    calls: list = []
    obj = _call(prompts.STRUCTURE_SYSTEM, prompts.structure_user("\n".join(blocks)),
                provider=provider, seed=seed, max_tokens=max_tokens, calls=calls,
                envoi=envoi)
    obj = obj if isinstance(obj, dict) else {}

    def _clean_support(items):
        out = []
        for it in items or []:
            if isinstance(it, dict):
                sup = [g for g in (it.get("support") or []) if g in shown]
                out.append({**it, "support": sup})
        return out

    cleaned = {
        "source_shape": obj.get("source_shape"),
        "thesis": (lambda t: {"text": t.get("text"),
                              "support": [g for g in (t.get("support") or []) if g in shown]}
                   )(obj.get("thesis") or {}),
        "tensions": _clean_support(obj.get("tensions")),
        "sections": _clean_support(obj.get("sections")),
    }
    fabricated = [g for it in [obj.get("thesis") or {}, *(obj.get("tensions") or []),
                               *(obj.get("sections") or [])]
                  for g in (it.get("support") or []) if g not in shown]

    # --- Garde B4 déterministe (thèse + tensions) — RÉUTILISE le garde de
    #     contenance de l'étape 7. Une synthèse ne peut : avoir un support vide,
    #     ni introduire un nombre / une entité nommée absent de ses unités citées.
    #     Échec ⇒ REJET déclaré (jamais gardé en silence). Aucun appariement flou,
    #     aucune réécriture. NB : un débordement PUREMENT sémantique (ni nombre ni
    #     entité nouveaux) reste hors de portée d'un contrôle déterministe — c'est
    #     assumé et reporté, pas masqué.
    b4_rejected: list[dict] = []

    def _b4_accept(text, support, kind):
        ok, reason, detail = b4_guard_unit(text, support, byid)
        if ok:
            return {"text": (text or "").strip(), "support": support}
        b4_rejected.append({"kind": kind, "text": (text or "").strip(),
                            "reason": reason, **detail})
        return None

    th = cleaned["thesis"]
    kept_thesis = _b4_accept(th.get("text"), th.get("support"), "thesis")
    cleaned["thesis"] = kept_thesis or {"text": None, "support": []}
    cleaned["tensions"] = [t for t in (
        _b4_accept(x.get("text"), x.get("support"), "tension") for x in cleaned["tensions"]) if t]

    diag["stage5_structure"] = {
        "shown_ids": len(shown), "fabricated_support_dropped": sorted(set(fabricated)),
        "b4_rejected": b4_rejected, "ok": bool(obj), "calls": calls,
        "latency_s": round(sum(c["latency_s"] for c in calls), 1),
    }
    return cleaned


# ---------- Étape 6 + 7 : rendu fidèle par unité + garde ----------
# Le modèle de rendu recopie parfois l'identifiant de l'unité en tête de phrase
# (« Unit g87: … ») : la garde refusait alors le nombre 87,
# absent de l'unité, et la phrase était perdue. Ce préfixe, suivi d'un séparateur
# (deux-points, tiret ou point), est retiré AVANT la garde et compté
# (`unit_prefixes_removed`). Seule la tête de la phrase est touchée ; un identifiant
# employé comme sujet (« Unit g89 asks… ») n'est pas un préfixe et reste refusé.
_PREFIXE_D_UNITE = re.compile(r"^\s*unit[ée]?\s+(?:id\s+)?g\d+\s*[:.\-–—]\s*", re.IGNORECASE)


def sans_prefixe_d_unite(texte: str) -> str:
    """La phrase sans un préfixe d'identifiant d'unité en tête (« Unit g87: »)."""
    return _PREFIXE_D_UNITE.sub("", texte, count=1)


# L'identifiant employé comme SUJET, sans séparateur (« Unit g89 asks whether… »,
# IS1009c) : le retirer laisserait un fragment (« asks whether… »), le garder fait refuser
# la phrase pour son nombre. La phrase est remplacée par l'énoncé verbatim de son unité,
# comme les strates SECONDARY et CLOSING sont rendues, sans modèle, et comptée
# (`unit_subject_fallbacks`). L'unité est celle que la phrase rend (son support), jamais
# celle que l'identifiant écrit nomme : un modèle qui écrit « Unit g12 » pour rendre g89
# retombe sur g89. Un identifiant au milieu de la phrase n'est jamais touché.
# Appliqué APRÈS le retrait du préfixe à séparateur : ce qui reste en tête est un sujet.
_SUJET_D_UNITE = re.compile(r"^\s*unit[ée]?\s+(?:id\s+)?g\d+\b", re.IGNORECASE)


def garder_phrase(s: dict, byid: dict, label: str) -> tuple[Optional[dict], Optional[dict], str]:
    """Une phrase rendue par le modèle : (phrase gardée, refus, retouche).

    `retouche` vaut « prefixe » (préfixe d'identifiant retiré), « repli » (énoncé de
    l'unité à la place d'une phrase dont l'identifiant est le sujet) ou « ». La garde de
    contenance s'applique au texte retouché, contre les unités citées.
    """
    sup = [g for g in (s.get("support") or []) if g in byid]
    brut = (s.get("text") or "").strip()
    text = sans_prefixe_d_unite(brut).strip()
    retouche = "prefixe" if text != brut else ""
    if sup and _SUJET_D_UNITE.match(text):
        sup = sup[:1]
        text = byid[sup[0]]["text"].strip()
        retouche = "repli"
    if not text or not sup:
        return None, {"section": label, "reason": "empty_or_unsupported",
                      "text": text, "support": s.get("support")}, retouche
    sup_txt = " ".join(byid[g]["text"] for g in sup)
    g_ok = guard.check_sentence(text, sup_txt)
    if not g_ok["ok"]:
        return None, {"section": label, "reason": "containment",
                      "text": text, "support": sup, **g_ok}, retouche
    return {"text": text, "support": sup}, None, retouche

def render_and_guard(units: list[dict], final: dict[str, str], themes: list[str], *,
                     provider: str, seed: int, diag: dict, batch: int = 12,
                     max_tokens: int = 1500, envoi: sensitivity.Envois = None) -> dict:
    labels = {f"T{i}": lbl for i, lbl in enumerate(themes[:5], 1)}
    byid = {u["gid"]: u for u in units}
    calls: list = []
    rejected: list[dict] = []
    sections: list[dict] = []
    kept_total = 0
    retouches = {"prefixe": 0, "repli": 0}
    for tkey, label in labels.items():
        mem = sorted([g for g, b in final.items() if b == tkey], key=lambda g: byid[g]["start"])
        if not mem:
            continue
        kept_sents: list[dict] = []
        for k in range(0, len(mem), batch):
            sub = mem[k:k + batch]
            block = "\n".join(f'{g} | {byid[g]["kind"]} | {byid[g]["text"]}' for g in sub)
            obj = _call(prompts.RENDER_SYSTEM, prompts.render_user(block),
                        provider=provider, seed=seed, max_tokens=max_tokens, calls=calls,
                        envoi=envoi)
            for s in (obj or {}).get("sentences", []) if isinstance(obj, dict) else []:
                gardee, refus, retouche = garder_phrase(s, byid, label)
                if retouche:
                    retouches[retouche] += 1
                if refus is not None:
                    rejected.append(refus)
                    continue
                kept_sents.append(gardee)
        kept_total += len(kept_sents)
        sections.append({"theme": tkey, "label": label, "sentences": kept_sents,
                         "unit_count": len(mem)})
    # Strates SECONDARY / CLOSING / NOISE (déterministe, verbatim — pas de LLM).
    # Rien n'est supprimé : `other`/`off_topic`/`closing` sont RENDUS brièvement ;
    # `dialogic_noise` reste hors corps mais est TRACÉ (provenance/diag).
    tiers = collect_tiers(units, final)
    diag["stage6_render"] = {
        "sections": len(sections), "kept_sentences": kept_total,
        "rejected_sentences": rejected, "calls": calls,
        "unit_prefixes_removed": retouches["prefixe"],
        "unit_subject_fallbacks": retouches["repli"],
        "secondary_units": len(tiers["secondary"]),
        "closing_units": len(tiers["closing"]),
        "noise_units_omitted": len(tiers["noise"]),
        "latency_s": round(sum(c["latency_s"] for c in calls), 1),
        "out_tok_est": sum(c["out_tok_est"] for c in calls),
    }
    return {"sections": sections, "secondary": tiers["secondary"],
            "closing": tiers["closing"], "noise": tiers["noise"]}
