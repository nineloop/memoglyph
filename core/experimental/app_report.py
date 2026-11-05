"""Compte-rendu LOCAL riche pour l'application (chemin produit).

Route empruntée par l'app pour les runs LOCAUX (Qwen/MLX) : réutilise les mêmes
étapes que le rapport expérimental (`run_stages`) mais ASSEMBLE un compte-rendu
compatible avec la bibliothèque, la vue rapport et le retour-à-la-source de l'UI.

Contrat de sortie IDENTIQUE au CR hérité (`core.report.make_report`) :
`{name, content, genre, warnings, generation}`, document `type: report` écrit
dans le dossier du transcript source puis indexé (FTS + sémantique).

Compatibilité UI (vérifiée sur le code du front) :
- citations cliquables = jetons INLINE `[source HH:MM:SS]` dans la partie
  éditoriale, AVANT le marqueur `evidence` (seule cette partie reçoit le clic) ;
- pas de tableau Markdown (le parseur de l'UI n'en rend pas) ;
- marqueurs `evidence` / `generated-notices` conservés pour la vue rapport ;
- l'interprétation (thèse/tensions) est visiblement étiquetée.

N'est PAS derrière le drapeau expérimental : c'est le chemin LOCAL par défaut de
l'app. Le drapeau ne gouverne plus que l'outil CLI d'artefacts `.experimental`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional

from core import attente_quota, config, docid, library, llm, provenance, semantic, writer_md
from core import report_citations
from core import sensitivity

from . import pipeline as _pipeline
from . import prompts as _prompts
from . import stages as _stages
from .chunking import verifier_citable


def _fr(language: Optional[str]) -> bool:
    return (language or "fr").lower().startswith("fr")


def make_local_report(
    name: str,
    *,
    provider: str = llm.FOURNISSEUR_DEFAUT,
    seed: Optional[int] = None,
    mistral_api_key: Optional[str] = None,
    progress: Optional[Callable[[float, object], None]] = None,
) -> dict:
    """Génère + sauvegarde le CR LOCAL riche d'un transcript. Contrat hérité."""
    seed = config.EXPERIMENTAL_SEED if seed is None else seed
    item = library.get_transcript(name)
    if item is None:
        raise ValueError(f"Transcript introuvable : {name}")
    meta = item.get("meta") or {}
    # Avant tout appel au modèle : sans réplique horodatée, rien à citer.
    verifier_citable(item.get("body") or "")
    # La règle d'envoi avant le premier appel (`sensitivity.controler`), avec
    # l'autorisation du PROPRIÉTAIRE du projet du transcript. Chaque appel au modèle
    # la relit ensuite par la façade : un réglage désactivé en cours de route arrête
    # l'appel suivant.
    envoi = library.envoi_du_document(name, meta)
    ultra_autorise = sensitivity.controler(envoi, local=llm.est_local(provider))

    # Une EXÉCUTION FinOps (best-effort, dark) borne toute la génération : les
    # appels modèle des étapes ET du classifieur de genre s'y rattachent via
    # `contextvars`. N'altère aucun contrat produit ; hors contexte, la
    # télémétrie serait simplement un no-op.
    from core import telemetry  # noqa: PLC0415

    # Le quota d'un fournisseur nommé atteint (429) : le moteur attend le délai annoncé puis réessaie, et la pause se DIT dans la
    # progression (à la dernière fraction, puis la dernière étape est republiée). Le contexte est ouvert ICI, dans le fil qui exécute
    # le traitement, et couvre les étapes comme le classifieur de genre. Ouvert dans un traitement déjà ouvert (une transcription),
    # il en partage le plafond.
    with telemetry.execution(
        kind="report",
        document_id=meta.get("id"),
        meeting_id=meta.get("meeting_id"),
        project_id=meta.get("project_id"),
        provider=provider,
        model=llm.modele_servi(provider),
    ) as execution, attente_quota.suivre(progress) as _progress:
        out = _pipeline.run_stages(name, item, provider=provider, seed=seed,
                                   mistral_api_key=mistral_api_key, progress=_progress,
                                   envoi=envoi)

        # Genre : réutilise le classifieur hérité (même moteur) pour l'étiquette UI.
        # Il ne lève jamais (retombe sur « generique ») — import paresseux pour ne
        # pas charger `core.report` sur le chemin par défaut.
        from core import report as _report  # noqa: PLC0415
        # `detail` : d'où vient le genre et la réponse BRUTE du classifieur ; gardés dans le diagnostic et dans le
        # résultat, jamais dans le front matter (un modèle peut répéter du transcript).
        detail_genre: dict = {}
        with telemetry.stage("genre"):
            genre = _report.classify_genre(item.get("body") or "", out["language"],
                                           provider=provider, mistral_api_key=mistral_api_key,
                                           envoi=envoi, detail=detail_genre)
        out["diag"]["genre"] = dict(detail_genre)

        # Découplage ECL / CR : le CR n'extrait plus les faits de la réunion. Ils se
        # lancent à part, à la demande (onglet « Faits », route POST de la réunion) ou par
        # `memoglyph ecl-extraire` ; les rubriques du CR viennent des seules unités.
        warnings: list[str] = []

        # ULID STABLE du CR, minté ICI pour rattacher l'exécution FinOps à l'artefact produit
        # par un ID (pas par un nom de fichier fragile). Le même id est écrit dans le
        # front-matter du CR → liaison version-de-rapport ↔ exécution robuste au renommage.
        report_id = docid.nouveau()
        # Un document devenu ultra-confidentiel EN COURS, parti avec autorisation : la
        # façade a marqué l'exécution (`sensitivity.tracer`), la provenance le dit (T3).
        ultra_autorise = ultra_autorise or bool(getattr(execution, "envoi_ultra_autorise", False))
        content = _assemble_app_report(name, meta, genre, out, report_id=report_id,
                                       ultra_autorise=ultra_autorise,
                                       modeles_servis=list(getattr(execution, "modeles_servis", []) or []))

        # Stockage IDENTIQUE au CR hérité : dossier du transcript source, index FTS +
        # sémantique. Le CR reste dans le projet de sa source.
        src_path = library.transcript_path(name)
        target_dir = src_path.parent if src_path is not None else writer_md.OUTPUT_DIR
        out_path = writer_md.write_md(
            content, writer_md.unique_path(f"{out['title']} — CR", target_dir)
        )
        telemetry.set_produced_artifact(report_id)
        library.sync_file(out_path)
        # Un CR réussi lève la réserve laissée par un échec précédent.
        if src_path is not None:
            library.ecrire_champ_meta(src_path, "report_error", None)
        try:
            semantic.index_name(out_path.name)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Compte-rendu écrit mais absent de la recherche par le sens : {exc}")
            print(f"[app_report] indexation sémantique ignorée : {exc}")

        return {"name": out_path.name, "content": content, "genre": genre,
                "warnings": warnings or None,
                "generation": {"engine": "local_hybrid_v1", **out["diag"]["totals"],
                               "classifieur": dict(detail_genre)}}


# Rubriques du CR : (type d'unité, intitulé FR, intitulé EN), dans l'ordre du document.
RUBRIQUES = (("decision", "Décisions", "Decisions"), ("action", "Actions", "Actions"),
             ("open_question", "Questions ouvertes", "Open questions"))
def _type_de_rubrique(support, byid: dict) -> Optional[str]:
    """Le type de rubrique d'une phrase : celui de la première de ses unités qui en a un."""
    kinds = {byid[g]["kind"] for g in (support or []) if g in byid}
    return next((k for k, _fr, _en in RUBRIQUES if k in kinds), None)


def _repartir_en_rubriques(rendered: dict, byid: dict) -> tuple[list[dict], dict]:
    """(lignes des rubriques, rendu restant) : une phrase en rubrique quitte sa section.

    Une ligne est la phrase rendue (étape 6) de son unité, passée par le garde de
    l'étape 7 ; pour les strates SECONDARY et CLOSING, le texte verbatim de l'unité,
    comme ces strates le rendent déjà. La strate NOISE reste hors du corps. Rien
    n'est réécrit ni dupliqué : chaque phrase est rendue une fois, ici ou dans sa
    section.
    """
    lignes: list[dict] = []
    sections = []
    for sec in rendered["sections"]:
        restantes = []
        for phrase in sec["sentences"]:
            kind = _type_de_rubrique(phrase["support"], byid)
            if kind is None:
                restantes.append(phrase)
                continue
            unites = [byid[g] for g in phrase["support"] if g in byid]
            lignes.append({"kind": kind, "text": phrase["text"], "support": phrase["support"],
                           "start": min(u["start"] for u in unites),
                           "owner": next((u.get("owner") for u in unites
                                          if u["kind"] == "action" and u.get("owner")), None)})
        sections.append({**sec, "sentences": restantes})
    restant = {**rendered, "sections": sections}
    for strate in ("secondary", "closing"):
        gardees = []
        for r in rendered.get(strate) or []:
            if r.get("kind") in _prompts.RUBRIC_KINDS:
                lignes.append({"kind": r["kind"], "text": r["text"], "support": [r["gid"]],
                               "start": r["start"], "owner": (byid.get(r["gid"]) or {}).get("owner")})
            else:
                gardees.append(r)
        restant[strate] = gardees
    return lignes, restant


def _avec_porteur(texte: str, porteur: Optional[str], fr: bool) -> str:
    """Le porteur, quand le texte ne le porte pas déjà ; libellé fixe, aucune rédaction."""
    if not porteur or _stages._mots(porteur) in _stages._mots(texte):
        return texte
    return f"{texte} (porteur : {porteur})" if fr else f"{texte} (owner: {porteur})"


def _assemble_app_report(name: str, meta: dict, genre: str, out: dict,
                         report_id: Optional[str] = None, ultra_autorise: bool = False,
                         modeles_servis: Optional[list] = None) -> str:
    struct, rendered = out["struct"], out["rendered"]
    units, diag = out["units"], out["diag"]
    title, language = out["title"], out["language"]
    byid = {u["gid"]: u for u in units}
    fr = _fr(language)

    def _src(support) -> str:
        # Jetons INLINE cliquables (retour-à-la-source de l'UI).
        toks = [f"[source {byid[g]['start']}]" for g in (support or []) if g in byid]
        return " ".join(toks)

    # Unités du corps (thèmes T*) qu'aucune phrase rendue ne cite : phrase refusée
    # par le garde, appel de rendu sans réponse, thème au-delà des cinq rendus.
    # Elles sont déclarées, horodatage et type seulement : leur texte, réécrit
    # par le modèle, n'a pas passé le garde.
    rendues = {g for sec in rendered["sections"] for s in sec["sentences"] for g in s["support"]}
    non_rendues = sorted((g for g, b in (out.get("final") or {}).items()
                          if b in _stages.THEMES and g in byid and g not in rendues),
                         key=lambda g: (byid[g]["start"], int(g[1:]) if g[1:].isdigit() else 0))

    from core import report as _report  # noqa: PLC0415
    modele_reel = llm.modele_servi(diag["provider"])
    now = datetime.now()
    failed = diag["stages"]["stage1_extract"]["failed_chunks"]
    fm = {
        "id": report_id or docid.nouveau(),
        **_report._identity_fields(meta),
        "title": f"{title} — compte-rendu",
        "date": now.strftime("%Y-%m-%d"),
        "type": "report",
        "source_transcript": name,
        # Le niveau de la réunion à la génération, noté pour mémoire (même règle que
        # le CR cloud) : celui qui s'applique reste celui de la réunion.
        "sensitivity": library.sensibilite_effective(meta.get("sensitivity"), meta.get("meeting_id"),
                                                     meta.get("project_id")),
        # Présent seulement quand ce CR ultra-confidentiel a été produit par un fournisseur
        # cloud sous l'autorisation du propriétaire : la provenance le dit.
        "envoi_ultra_autorise": True if ultra_autorise else None,
        "language": language,
        "model": modele_reel,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "genre": genre,
        "report_engine": "local_hybrid_v1",
        # Les modèles qui ont RÉELLEMENT répondu quand le fournisseur en choisit un par appel (un routeur) : `model` ne dit alors
        # que le nom demandé. Absent pour un fournisseur qui n'en dit rien.
        "models_served": list(modeles_servis) if modeles_servis else None,
        # Réserves surfacées par l'aside qualité (tableaux) — jamais de perte
        # silencieuse : les tranches d'extraction en échec sont déclarées.
        "map_chunks_failed": failed or None,
        "units_not_rendered": len(non_rendues) or None,
        # Diagnostic de strates (ignoré par l'UI mais tracé) — invariant de
        # partition déjà garanti en amont.
        "tier_counts": {k: diag["totals"][k] for k in
                        ("main_units", "secondary_units", "closing_units",
                         "noise_units_omitted")},
        "pipeline": provenance.report_fingerprint(
            source_pipeline=meta.get("pipeline"),
            model=modele_reel,
            language=language,
            genre=genre,
            num_ctx=config.EXPERIMENTAL_CHUNK_TOKENS,
            questions=[],
        ),
    }

    heading = "Compte-rendu" if fr else "Report"
    p: list[str] = [f"# {heading} — {title}", ""]

    # --- Couche INTERPRÉTATION (visiblement étiquetée) ---
    thesis = struct.get("thesis") or {}
    tensions = struct.get("tensions") or []
    if thesis.get("text") or tensions:
        p += [("## Lecture (interprétation)" if fr else "## Reading (interpretation)"), ""]
        p += [("*Interprétation appuyée sur les unités citées, non vérifiée mot à mot.*"
               if fr else
               "*Interpretation grounded in the cited units, not verbatim-verified.*"), ""]
        if thesis.get("text"):
            lbl = "Thèse (interprétation)" if fr else "Thesis (interpretation)"
            p += [f"**{lbl} :** {thesis['text']} {_src(thesis.get('support'))}".rstrip(), ""]
        if tensions:
            p += [("### Tensions (interprétation)" if fr else "### Tensions (interpretation)"), ""]
            for t in tensions:
                if t.get("text"):
                    p.append(f"- {t['text']} {_src(t.get('support'))}".rstrip())
            p.append("")

    # --- RUBRIQUES : décisions, actions, questions ouvertes (après l'interprétation,
    #     avant les sections) ; une phrase placée ici quitte sa section. ---
    lignes_rubriques, rendered = _repartir_en_rubriques(rendered, byid)
    for kind, titre_fr, titre_en in RUBRIQUES:
        lignes = [(_stages._secondes(r["start"]) or 0,
                   f"- {_avec_porteur(r['text'], r['owner'], fr)} {_src(r['support'])}".rstrip())
                  for r in lignes_rubriques if r["kind"] == kind]
        if not lignes:
            continue  # rubrique vide : omise
        p += [f"## {titre_fr if fr else titre_en}", ""]
        p += [ligne for _t, ligne in sorted(lignes, key=lambda x: x[0])]
        p.append("")

    # --- MAIN : sections thématiques (ordre de la structure si dispo) ---
    order = [s.get("label") for s in (struct.get("sections") or []) if s.get("label")]
    secs = sorted(rendered["sections"],
                  key=lambda s: order.index(s["label"]) if s["label"] in order else 99)
    for sec in secs:
        if not sec["sentences"]:
            continue
        p += [f"## {sec['label']}", ""]
        para = " ".join(f"{s['text']} {_src(s['support'])}".rstrip() for s in sec["sentences"])
        p += [para, ""]

    # --- SECONDARY : « Autres points » (verbatim, cliquable) ---
    secondary = rendered.get("secondary") or []
    if secondary:
        p += [("## Autres points" if fr else "## Other points"), ""]
        for r in secondary:
            p.append(f"- {r['text']} [source {r['start']}]")
        p.append("")

    # --- CLOSING : « Clôture / autres séquences » (verbatim, cliquable) ---
    closing = rendered.get("closing") or []
    if closing:
        p += [("## Clôture / autres séquences" if fr else "## Closing / other segments"), ""]
        for r in closing:
            p.append(f"- {r['text']} [source {r['start']}]")
        p.append("")

    # --- Marqueur EVIDENCE : rend l'éditorial cliquable ET liste les sources ---
    cited = sorted(
        {g for sec in secs for s in sec["sentences"] for g in s["support"]}
        | {g for t in tensions for g in (t.get("support") or [])}
        | set((thesis.get("support") or []))
        | {r["gid"] for r in secondary} | {r["gid"] for r in closing}
        | {g for r in lignes_rubriques for g in r["support"] if g in byid},
        key=lambda g: int(g[1:]))
    p += ["", report_citations.EVIDENCE_MARKER, ""]
    p += [("### Unités citées" if fr else "### Cited units"), ""]
    for g in cited:
        u = byid[g]
        p.append(f"- [source {u['start']}] {u['kind']} — {u['text']}")
    p.append("")

    # --- Marqueur NOTICES : transparence (aucune perte silencieuse) ---
    noise = rendered.get("noise") or []
    b4rej = diag["stages"].get("stage5_structure", {}).get("b4_rejected", [])
    rej = diag["stages"]["stage6_render"]["rejected_sentences"]
    p += [report_citations.GENERATED_NOTICE_MARKER, ""]
    notes: list[str] = []
    if failed:
        notes.append(f"- {len(failed)} tranche(s) d'extraction en échec : {failed}"
                     if fr else f"- {len(failed)} extraction chunk(s) failed: {failed}")
    if noise:
        spans = ", ".join(r["start"] for r in noise)
        notes.append(f"- {len(noise)} passage(s) de bruit dialogique laissés hors du corps "
                     f"(tracés) : {spans}" if fr else
                     f"- {len(noise)} dialogic-noise passage(s) kept out of the body "
                     f"(still traced): {spans}")
    sans_chiffre = diag["stages"].get("stage2_validate", {}).get("rejected_unsupported_numbers", 0)
    if sans_chiffre:
        notes.append(f"- {sans_chiffre} unité(s) écartée(s) : un chiffre absent de la parole "
                     f"à 30 s de leur horodatage." if fr else
                     f"- {sans_chiffre} unit(s) dropped: a figure absent from the speech within "
                     f"30 s of their timestamp.")
    if b4rej:
        notes.append(f"- {len(b4rej)} synthèse(s) d'interprétation rejetée(s) par le garde "
                     f"de contenance." if fr else
                     f"- {len(b4rej)} interpretation synthese(s) rejected by the containment guard.")
    if rej:
        notes.append(f"- {len(rej)} phrase(s) rejetée(s) par le garde déterministe."
                     if fr else f"- {len(rej)} sentence(s) rejected by the deterministic guard.")
    doublons = diag["stages"].get("stage2_validate", {}).get("fact_duplicates_removed", 0)
    if doublons:
        notes.append(f"- {doublons} unité(s) de type fait retirée(s) : même énoncé qu'une décision, "
                     f"une action ou une question ouverte à {_stages.DOUBLON_FAIT_S} s près." if fr else
                     f"- {doublons} fact unit(s) removed: same statement as a decision, action or "
                     f"open question within {_stages.DOUBLON_FAIT_S} s.")
    if non_rendues:
        notes.append(f"- {len(non_rendues)} unité(s) du corps non rendue(s), listées ci-dessous."
                     if fr else f"- {len(non_rendues)} body unit(s) not rendered, listed below.")
    if notes:
        p += [("## Transparence" if fr else "## Transparency"), ""] + notes
    if non_rendues:
        p += ["", ("## Unités non rendues" if fr else "## Units not rendered"), ""]
        p += [f"- {byid[g]['start']} · {byid[g]['kind']}" for g in non_rendues]

    return writer_md._yaml_frontmatter(fm) + "\n\n" + "\n".join(p).strip() + "\n"
