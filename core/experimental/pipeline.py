"""Pilote du pipeline expérimental : orchestre les 8 étapes, mesure, assemble le
rapport, et persiste DEUX artefacts SÉPARÉS du rapport normal.

Ne touche à rien du chemin par défaut. Refuse de tourner si le drapeau est bas.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Callable, Optional

from core import config, library, llm, messages, sensitivity
from core import telemetry
from core import writer_md

from . import stages, profiles
from .chunking import transcript_lines, profile_themes, token_chunks


class ExtractionVide(RuntimeError):
    """Le modèle a répondu, mais aucune unité n'a survécu à la validation.

    Distincte d'une panne de service (tous les appels refusés : `LLMError`, 503) :
    la machine a fait son travail et n'a rien retenu. Le message porte la cause et
    les comptes, pour un refus lisible plutôt qu'un 500.
    """

    def __init__(self, appels: int, refuses: int, premier_refus: Optional[str] = None) -> None:
        cause = (f"Extraction vide : aucune unité retenue sur {appels} appel(s) au modèle, "
                 f"dont {refuses} refusé(s)")
        if premier_refus:
            cause += f" ; premier refus : {premier_refus}"
        super().__init__(cause + ".")
        self.appels = appels
        self.refuses = refuses

EXPERIMENTAL_DIR = config.OUTPUT_DIR / ".experimental"


def _slug(name: str) -> str:
    return writer_md.slugify(name.rsplit(".", 1)[0])


def run_stages(
    name: str,
    item: dict,
    *,
    provider: str,
    seed: int,
    mistral_api_key: Optional[str] = None,
    progress: Optional[Callable[[float, object], None]] = None,
    profile: Optional["profiles.ExecProfile"] = None,
    envoi: sensitivity.Envois = None,
) -> dict:
    """Exécute les 8 étapes riches et renvoie tout le nécessaire à l'assemblage.

    Cœur PARTAGÉ entre le rapport expérimental (artefacts `.experimental`) et le
    rapport LOCAL de l'application — aucune duplication de pipeline. `progress`
    reçoit `(fraction 0..1, Message)` et est mappé sur les étapes ; il ne prétend
    JAMAIS qu'une étape échouée a réussi (une extraction totalement vide lève).

    `profile` : profil d'EXÉCUTION (tranche/lots/plafonds). Par défaut résolu PAR
    CAPACITÉ (`profiles.resolve` : local → prudent inchangé, cloud → grand-
    contexte). Ne change AUCUN contrat produit ; seul le nombre d'appels varie.

    `envoi` : le document source (`library.envoi_du_document`), transmis à chaque
    appel au modèle pour que la façade applique la règle d'envoi.
    """
    prof = profile or profiles.resolve(provider)
    meta = item.get("meta") or {}
    body = item.get("body") or ""
    language = meta.get("language")
    # Titre LISIBLE (titre saisi, sinon nom de la source) : il porte le H1 et le
    # titre du CR, jamais le slug du fichier.
    title = library.titre_lisible(meta.get("title"), meta.get("source"), nom=name)

    if not llm.is_available(provider=provider, api_key=llm.cle_mistral(provider, mistral_api_key)):
        raise llm.LLMError(f"Le service LLM ({provider}) n'est pas disponible.")

    diag: dict = {"name": name, "provider": provider, "seed": seed,
                  "model": llm.modele_servi(provider), "exec_profile": prof.name,
                  "stages": {}}
    t0 = time.perf_counter()

    lines, valid_ts = transcript_lines(body)
    themes = profile_themes(body)
    chunks = token_chunks(lines, prof.chunk_tokens)
    diag["input"] = {"lines": len(lines), "distinct_timestamps": len(valid_ts),
                     "themes": themes, "chunks": len(chunks)}

    def _p(frac: float, msg: object) -> None:
        if progress:
            progress(frac, msg)

    st = diag["stages"]
    # `telemetry.stage(...)` balise la DIMENSION `stage` des appels modèle (best-
    # effort, no-op hors d'une exécution ouverte). Ne change aucun contrat.
    _p(0.05, messages.compte_rendu_extraction())
    with telemetry.stage("extract"):
        raw_units = stages.extract_atomic(chunks, provider=provider, seed=seed, diag=st,
                                          max_tokens=prof.extract_max_tokens, envoi=envoi)
    units = stages.validate_units(raw_units, valid_ts, diag=st, lines=lines)
    if not units:
        appels = st.get("stage1_extract", {}).get("calls", [])
        refus = [c["error"] for c in appels if c.get("error")]
        # Tous les appels refusés, c'est une panne de SERVICE (débit, quota,
        # clé), pas une extraction vide : `LLMError` devient un 503 qui la
        # nomme. Le message vient de la façade `llm`, déjà assaini.
        # Sans cela, une suite de 429 finissait en 500 muet.
        if appels and len(refus) == len(appels):
            raise llm.LLMError(
                f"Extraction impossible, {len(appels)} appel(s) au modèle refusé(s). "
                f"Premier refus : {refus[0]}")
        raise ExtractionVide(len(appels), len(refus), refus[0] if refus else None)
    _p(0.45, messages.compte_rendu_organisation())
    with telemetry.stage("classify"):
        classified = stages.classify_units(units, themes, provider=provider, seed=seed, diag=st,
                                           batch=prof.classify_batch,
                                           max_tokens=prof.classify_max_tokens, envoi=envoi)
    final, _ = stages.apply_veto(classified, units, diag=st)
    with telemetry.stage("structure"):
        struct = stages.structure(units, final, themes, provider=provider, seed=seed, diag=st,
                                  max_tokens=prof.structure_max_tokens, envoi=envoi)
    _p(0.70, messages.compte_rendu_redaction())
    with telemetry.stage("render"):
        rendered = stages.render_and_guard(units, final, themes, provider=provider, seed=seed,
                                           diag=st, batch=prof.render_batch,
                                           max_tokens=prof.render_max_tokens, envoi=envoi)

    diag["total_latency_s"] = round(time.perf_counter() - t0, 1)
    # Strates : MAIN (T*) rendues par LLM ; SECONDARY/CLOSING rendues verbatim ;
    # NOISE (dialogic court) hors corps mais tracée. AUCUNE unité supprimée.
    diag["totals"] = {
        "validated_units": len(units),
        "main_units": sum(1 for b in final.values() if b in stages.THEMES),
        "secondary_units": st["stage6_render"]["secondary_units"],
        "closing_units": st["stage6_render"]["closing_units"],
        "noise_units_omitted": st["stage6_render"]["noise_units_omitted"],
        "kept_sentences": st["stage6_render"]["kept_sentences"],
        "rejected_sentences": len(st["stage6_render"]["rejected_sentences"]),
        "in_tok_est": (st["stage1_extract"]["in_tok_est"]
                       + sum(c["in_tok_est"] for c in st["stage3_classify"]["calls"])),
        "out_tok_est": (st["stage1_extract"]["out_tok_est"]
                        + st["stage6_render"]["out_tok_est"]),
    }
    # Invariant non destructif : chaque unité validée est placée dans exactement
    # une strate (MAIN/SECONDARY/CLOSING/NOISE). Rien ne disparaît en silence.
    t = diag["totals"]
    assert (t["main_units"] + t["secondary_units"] + t["closing_units"]
            + t["noise_units_omitted"]) == len(units), "unité perdue entre les strates"

    return {"struct": struct, "rendered": rendered, "units": units, "final": final,
            "themes": themes, "diag": diag, "title": title, "language": language}


def make_experimental_report(
    name: str,
    *,
    provider: str = llm.FOURNISSEUR_DEFAUT,
    seed: Optional[int] = None,
    mistral_api_key: Optional[str] = None,
) -> dict:
    """Génère le rapport expérimental d'un transcript. Retourne les chemins + diag.

    Garde-fou dur : ne s'exécute que si `config.EXPERIMENTAL_REPORT` est vrai.
    Écrit les artefacts `.experimental` (comparaison technique), distincts du
    compte-rendu de l'application.
    """
    if not config.EXPERIMENTAL_REPORT:
        raise RuntimeError(
            "Pipeline expérimental désactivé. Mettre MEMOGLYPH_EXPERIMENTAL_REPORT=1."
        )
    seed = config.EXPERIMENTAL_SEED if seed is None else seed
    item = library.get_transcript(name)
    if item is None:
        raise ValueError(f"Transcript introuvable : {name}")

    out = run_stages(name, item, provider=provider, seed=seed,
                     mistral_api_key=mistral_api_key,
                     envoi=library.envoi_du_document(name, item.get("meta") or {}))
    struct, rendered = out["struct"], out["rendered"]
    units, final, diag = out["units"], out["final"], out["diag"]
    title, language = out["title"], out["language"]

    report_md = _assemble(title, language, struct, rendered, units, final, diag)

    EXPERIMENTAL_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slug(name)
    report_path = EXPERIMENTAL_DIR / f"{slug}.experimental.md"
    diag_path = EXPERIMENTAL_DIR / f"{slug}.experimental.diag.json"
    report_path.write_text(report_md, encoding="utf-8")
    diag_path.write_text(json.dumps(diag, indent=2, ensure_ascii=False), encoding="utf-8")

    return {"report_path": str(report_path), "diag_path": str(diag_path), "diag": diag}


def _assemble(title, language, struct, rendered, units, final, diag) -> str:
    byid = {u["gid"]: u for u in units}
    fr = (language or "fr").lower().startswith("fr")
    now = datetime.now()
    fm = {
        "title": f"{title} — rapport expérimental",
        "type": "experimental_report",
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "provider": diag["provider"], "model": diag["model"], "seed": diag["seed"],
        "pipeline": "experimental_hybrid_v1",
        "validated_units": diag["totals"]["validated_units"],
        "kept_sentences": diag["totals"]["kept_sentences"],
        "rejected_sentences": diag["totals"]["rejected_sentences"],
    }
    out: list[str] = [writer_md._yaml_frontmatter(fm), ""]
    banner = ("> ⚗️ **RAPPORT EXPÉRIMENTAL** — pipeline hors production, derrière un "
              "drapeau. Chaque phrase est tracée à une unité atomique validée "
              "(timestamp vérifié) ; les nombres/entités non contenus dans l'unité "
              "citée ont été rejetés par un garde déterministe."
              if fr else
              "> ⚗️ **EXPERIMENTAL REPORT** — non-production, flag-gated. Every "
              "sentence traces to a validated atomic unit (checked timestamp); "
              "numbers/entities not contained in the cited unit were rejected by a "
              "deterministic guard.")
    out += [f"# {title}", "", banner, ""]

    shape = struct.get("source_shape")
    thesis = struct.get("thesis") or {}
    tensions = struct.get("tensions") or []
    # Thèse + tensions = couche d'INTERPRÉTATION (I). Le garde B4 déterministe a
    # écarté toute synthèse au support vide ou introduisant un nombre/une entité
    # absent des unités citées ; mais un débordement PUREMENT sémantique n'est pas
    # détectable sans appariement flou. On l'ENCADRE donc explicitement : cette
    # couche est marquée « interprétation, non vérifiée mot à mot », distincte des
    # sections factuelles (chaque phrase y trace à une unité validée).
    interp = ("*Lecture — interprétation appuyée sur les unités citées, "
              "non vérifiée sémantiquement mot à mot.*" if fr else
              "*Interpretation — grounded in the cited units, not verbatim-"
              "verified.*")
    if shape:
        out += [f"*{shape}*", ""]
    if thesis.get("text") or tensions:
        out += [interp, ""]
    if thesis.get("text"):
        lbl = "Thèse (interprétation)" if fr else "Thesis (interpretation)"
        out += [f"**{lbl} :** {thesis['text']} {_ids(thesis.get('support'))}", ""]
    if tensions:
        out += [("## Tensions (interprétation)" if fr else "## Tensions (interpretation)"), ""]
        for t in tensions:
            if t.get("text"):
                out.append(f"- {t['text']} {_ids(t.get('support'))}")
        out.append("")

    # Sections dans l'ordre proposé par la structure si possible, sinon T1..T5.
    order = [s.get("label") for s in (struct.get("sections") or []) if s.get("label")]
    secs = rendered["sections"]
    secs = sorted(secs, key=lambda s: order.index(s["label"]) if s["label"] in order else 99)
    for sec in secs:
        if not sec["sentences"]:
            continue
        out += [f"## {sec['label']}", ""]
        para = " ".join(f"{s['text']} {_ids(s['support'])}" for s in sec["sentences"])
        out += [para, ""]

    # --- SECONDARY : `other` (on-sujet, hors thème). Rendu VERBATIM, compact :
    #     jamais supprimé, découvrable, tracé. ---
    secondary = rendered.get("secondary") or []
    if secondary:
        out += [("## Autres points" if fr else "## Other points"), ""]
        for r in secondary:
            out.append(f"- {r['text']} [{r['gid']}]")
        out.append("")

    # --- CLOSING : off_topic + segment de clôture démoté (météo, annonces,
    #     sign-off). Rendu VERBATIM, bref : conservé, jamais supprimé. ---
    closing = rendered.get("closing") or []
    if closing:
        out += [("## Clôture / autres séquences" if fr
                 else "## Closing / other segments"), ""]
        for r in closing:
            out.append(f"- {r['text']} [{r['gid']}]")
        out.append("")

    # Annexe de preuve : chaque gid cité → son unité (kind, timestamp, texte).
    # Inclut MAIN + interprétation + SECONDARY + CLOSING (mêmes garanties de
    # traçabilité pour toutes les strates rendues).
    cited = sorted({g for sec in secs for s in sec["sentences"] for g in s["support"]}
                   | {g for t in tensions for g in (t.get("support") or [])}
                   | set(thesis.get("support") or [])
                   | {r["gid"] for r in secondary}
                   | {r["gid"] for r in closing},
                   key=lambda g: int(g[1:]))
    out += [("## Sources (unités validées)" if fr else "## Sources (validated units)"), ""]
    for g in cited:
        u = byid[g]
        out.append(f"- **{g}** `[{u['start']}]` *{u['kind']}* — {u['text']}")
    out.append("")

    # JAMAIS d'abandon silencieux. NB : plus AUCUNE suppression de contenu validé —
    # off_topic/clôture sont désormais RENDUS (strates SECONDARY/CLOSING). Ici on
    # ne déclare que : tranches en échec, phrases rejetées par le garde, synthèses
    # B4 rejetées, et le BRUIT dialogique court volontairement laissé hors corps
    # (mais tracé : timestamps listés, unités dans le diag).
    rej = diag["stages"]["stage6_render"]["rejected_sentences"]
    failed = diag["stages"]["stage1_extract"]["failed_chunks"]
    b4rej = diag["stages"].get("stage5_structure", {}).get("b4_rejected", [])
    noise = rendered.get("noise") or []
    if rej or failed or b4rej or noise:
        out += [("## Écarté par le pipeline (transparence)" if fr
                 else "## Dropped by the pipeline (transparency)"), ""]
        if failed:
            out.append(f"- {len(failed)} tranche(s) d'extraction en échec : {failed}"
                       if fr else f"- {len(failed)} extraction chunk(s) failed: {failed}")
        if noise:
            spans = ", ".join(r["start"] for r in noise)
            out.append(
                f"- {len(noise)} unité(s) de BRUIT dialogique laissées hors du corps "
                f"(tracées) : {spans}" if fr else
                f"- {len(noise)} dialogic-NOISE unit(s) kept out of the body "
                f"(still traced): {spans}")
        for r in rej:
            reason = r.get("reason")
            detail = (r.get("missing_numbers") or []) + (r.get("missing_entities") or [])
            out.append(f"- [{reason}] « {r.get('text','')[:80]} » "
                       f"{('→ ' + ', '.join(map(str, detail))) if detail else ''}")
        for r in b4rej:
            detail = (r.get("missing_numbers") or []) + (r.get("missing_entities") or [])
            out.append(f"- [{r.get('kind')}:{r.get('reason')}] « {r.get('text','')[:80]} » "
                       f"{('→ ' + ', '.join(map(str, detail))) if detail else ''}")
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def _ids(support) -> str:
    return "[" + " ".join(support or []) + "]" if support else ""
