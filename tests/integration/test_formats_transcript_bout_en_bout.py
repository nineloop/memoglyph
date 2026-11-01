"""Chaque format de transcript lu par la porte unique va jusqu'au CR et à l'ECL.

Un transcript par format (`[MM:SS]`, `[75:10]`, « Nom [HH:MM:SS] », « [HH:MM:SS]
Nom : » répété) : le CR cite des sources `[source HH:MM:SS]` que la garde a
trouvées, l'ECL ancre ses faits sur la réplique, locuteur compris.

Corpus synthétique écrit ici. Les deux modèles sont simulés : celui du CR recopie
ce que l'extrait lui montre, comme on le lui demande ; celui de l'ECL propose la
citation mot pour mot.
"""
from __future__ import annotations

import json
import re

import pytest

from core import docid, ecl, library, llm, projects
from core.experimental import app_report, pipeline, prompts
from core.experimental.chunking import transcript_lines, verifier_citable

pytestmark = [pytest.mark.integration]

PROFIL = "# Essai\n\n## Profil\n\n**Thèmes :** Tarifs\n\n## Transcript\n\n"
PRIX = "après discussion on fixe le prix de vente à 25 euros"
GRILLE = "Bruno prépare la grille tarifaire pour jeudi"

# format → (lignes du transcript, horodatages attendus, locuteurs attendus des deux répliques)
FORMATS = {
    "court": ([f"[00:10] {PRIX}", f"[00:38] {GRILLE}"],
              ["00:00:10", "00:00:38"], [None, None]),
    "au-dela-de-l-heure": ([f"[75:10] {PRIX}", f"[75:38] {GRILLE}"],
                           ["01:15:10", "01:15:38"], [None, None]),
    "nom-avant": ([f"Alice [00:00:10] {PRIX}", f"Bruno Martin [00:00:38] {GRILLE}"],
                  ["00:00:10", "00:00:38"], ["Alice", "Bruno Martin"]),
    "nom-deux-points": ([f"[00:00:10] Alice : {PRIX}", f"[00:00:38] Bruno : {GRILLE}",
                         "[00:00:45] Alice : merci à tous", "[00:00:50] Bruno : à jeudi"],
                        ["00:00:10", "00:00:38"], ["Alice", "Bruno"]),
}


def _corps(format_: str) -> str:
    return PROFIL + "\n".join(FORMATS[format_][0]) + "\n"


# ---------- Compte rendu ----------

_LIGNE_CANONIQUE = re.compile(r"^(?:\*\*(?P<qui>[^*]+)\*\* )?\[(?P<ts>\d{2}:\d{2}:\d{2})\] (?P<texte>.+)$")
INVENTEE = "Le prix de vente est fixé à 99 euros."


def _modele_du_cr(system: str, user: str, **_options) -> dict:
    """Recopie l'extrait : une unité par réplique, avec l'horodatage qu'il porte."""
    if system == prompts.EXTRACT_SYSTEM:
        unites = []
        for ligne in user.splitlines():
            m = _LIGNE_CANONIQUE.match(ligne.strip())
            if m:
                unites.append({"kind": "fact", "text": m["texte"], "start": m["ts"],
                               "end": None, "speaker": m["qui"]})
        return {"units": unites}
    if system == prompts.CLASSIFY_SYSTEM:
        ids = re.findall(r"^(g\d+) \|", user, re.MULTILINE)
        return {"assignments": [{"id": g, "bucket": "T1"} for g in ids]}
    if system == prompts.STRUCTURE_SYSTEM:
        return {"source_shape": "thematic", "thesis": {}, "tensions": [], "sections": []}
    if system == prompts.RENDER_SYSTEM:
        unites = re.findall(r"^(g\d+) \| \w+ \| (.+)$", user, re.MULTILINE)
        # Une phrase de plus, qui invente un chiffre : la garde doit la refuser.
        return {"sentences": [{"text": texte, "support": [g]} for g, texte in unites]
                + [{"text": INVENTEE, "support": [unites[0][0]]}]}
    raise AssertionError(f"appel inattendu : {system[:40]}")


@pytest.mark.parametrize("format_", sorted(FORMATS))
def test_le_cr_cite_des_sources_trouvees_par_la_garde(monkeypatch, format_) -> None:
    monkeypatch.setattr(llm, "is_available", lambda **k: True)
    monkeypatch.setattr(llm, "modele_servi", lambda p: "modele-test")
    monkeypatch.setattr(llm, "chat_json", _modele_du_cr)
    corps = _corps(format_)
    horodatages = FORMATS[format_][1]
    meta = {"id": docid.nouveau(), "meeting_id": docid.nouveau(), "project_id": docid.nouveau(),
            "title": "Essai", "language": "fr"}

    verifier_citable(corps)
    sortie = pipeline.run_stages("essai.md", {"meta": meta, "body": corps}, provider="mistral", seed=42)
    contenu = app_report._assemble_app_report("essai.md", meta, "generique", sortie)

    lues, valides = transcript_lines(corps)
    etapes = sortie["diag"]["stages"]
    assert set(horodatages) <= valides
    assert etapes["stage2_validate"]["kept"] == len(lues)
    assert etapes["stage2_validate"]["rejected_unsupported_ts"] == 0
    assert etapes["stage2_validate"]["rejected_unsupported_numbers"] == 0
    # La garde a lu les répliques : toutes rendues, l'inventée refusée.
    assert etapes["stage6_render"]["kept_sentences"] == len(lues)
    refusees = etapes["stage6_render"]["rejected_sentences"]
    assert [(r["text"], r["reason"], r["missing_numbers"]) for r in refusees] == [
        (INVENTEE, "containment", ["99"])]
    cites = re.findall(r"\[source ([^\]]+)\]", contenu.split("<!-- memoglyph:evidence -->")[0])
    assert len(cites) == len(lues)
    assert set(cites) == valides
    assert all(re.fullmatch(r"\d{2}:\d{2}:\d{2}", ts) for ts in cites)
    assert {u["start"]: u["speaker"] for u in sortie["units"]}[horodatages[1]] == FORMATS[format_][2][1]
    assert INVENTEE not in contenu


# ---------- ECL ----------

PROPOSES = [
    {"type": "decision", "horodatage": "00:00:12", "citation": "on fixe le prix de vente à 25 euros",
     "enonce": "Le prix de vente est fixé à 25 euros."},
    {"type": "action", "horodatage": "00:00:40", "citation": GRILLE,
     "enonce": "Bruno prépare la grille tarifaire.", "porteur": "Bruno"},
]


class FauxModele:
    """Propose les deux faits, à l'horodatage de l'extrait décalé de deux secondes."""

    def __init__(self, decalage_s: int = 0):
        self.decalage_s, self.extraits = decalage_s, []

    def __call__(self, system, user, **options):
        self.extraits.append(user)
        faits = []
        for fait in PROPOSES:
            h, m, s = (int(p) for p in fait["horodatage"].split(":"))
            t = h * 3600 + m * 60 + s + self.decalage_s
            faits.append({**fait, "horodatage": f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"})
        return {"faits": faits}


@pytest.fixture
def bibliotheque(isolated_stores, monkeypatch):
    from core import semantic
    from core.config import EMBED_DIM
    vecteur = [1.0] + [0.0] * (EMBED_DIM - 1)
    monkeypatch.setattr(semantic.embed, "embed_documents", lambda textes, **_k: [vecteur] * len(textes))
    monkeypatch.setattr(semantic.embed, "embed_query", lambda t, **_k: vecteur)
    sortie = isolated_stores["output_dir"]
    projet = projects.ensure_inbox(sortie)
    for format_ in FORMATS:
        (sortie / f"{format_}.md").write_text(
            f"---\nid: {docid.nouveau()}\nmeeting_id: {docid.nouveau()}\nproject_id: {projet.id}\n"
            f"title: {format_}\ndate: '2026-03-01'\nlanguage: fr\nsensitivity: standard\n---\n\n"
            + _corps(format_), encoding="utf-8")
    library.reindex()
    return sortie


@pytest.mark.parametrize("format_", sorted(FORMATS))
def test_l_ecl_ancre_ses_faits_sur_la_replique(monkeypatch, bibliotheque, format_) -> None:
    lignes, horodatages, locuteurs = FORMATS[format_]
    # Le modèle annonce chaque horodatage à deux secondes près.
    debut = sum(int(p) * f for p, f in zip(horodatages[0].split(":"), (3600, 60, 1)))
    faux = FauxModele(decalage_s=debut - 12 + 2)
    monkeypatch.setattr(ecl, "_modele", faux)

    bilan = ecl.extraire(f"{format_}.md")

    assert (bilan["gardes"], bilan["refuses"]) == (2, 0)
    assert len(faux.extraits) == 1
    # L'extrait donné au modèle porte la forme canonique, jamais celle du fichier.
    assert f"[{horodatages[0]}]" in faux.extraits[0]
    contenu = json.loads((bibliotheque / ".ecl" / f"{bilan['document_id']}.json").read_text(encoding="utf-8"))
    decision, action = sorted(contenu["faits"], key=lambda f: f["type"], reverse=True)
    assert (decision["type"], decision["horodatage"], decision["locuteur"]) == (
        "decision", horodatages[0], locuteurs[0])
    assert (action["type"], action["horodatage"], action["locuteur"], action["porteur"]) == (
        "action", horodatages[1], locuteurs[1], "Bruno")
    assert decision["secondes"] == debut
    assert decision["citation"] in lignes[0] and action["citation"] in lignes[1]
