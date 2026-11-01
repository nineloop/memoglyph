"""Étapes DÉTERMINISTES du pipeline expérimental — sans LLM.

Couvre le découpage borné, la validation timestamp/dédup, le veto d'exclusion et
le garde de contenance. Ces briques portent les garanties d'intégrité.
"""
from __future__ import annotations

from core.experimental import guard, stages
from core.experimental.chunking import token_chunks, est_tokens, profile_themes


# ---------- découpage borné en tokens ----------
def test_token_chunks_respecte_la_borne_et_les_frontieres():
    lines = [f"**Speaker 1** [00:0{i}:00] réplique numéro {i} avec du texte" for i in range(9)]
    chunks = token_chunks(lines, bound_tokens=80, overhead_tokens=20)
    assert len(chunks) >= 2  # doit couper
    for c in chunks:
        assert est_tokens(c) <= 80  # jamais au-dessus de la borne
    # aucune réplique perdue ni coupée
    assert sum(len(c.splitlines()) for c in chunks) == len(lines)


def test_profile_themes_lit_le_bloc_existant():
    body = "## Transcript\n**Thèmes :** Alpha · Beta · Gamma\n"
    assert profile_themes(body) == ["Alpha", "Beta", "Gamma"]
    assert profile_themes("aucun bloc") == []


# ---------- validation timestamp/support + dédup ----------
def test_validate_rejette_timestamp_absent_et_dedoublonne():
    raw = [
        {"kind": "fact", "text": "A", "start": "00:00:01"},
        {"kind": "fact", "text": "A", "start": "00:00:01"},     # doublon exact
        {"kind": "fact", "text": "B", "start": "00:99:99"},     # ts absent
        {"kind": "bogus", "text": "C", "start": "00:00:02"},    # kind coercé
    ]
    diag = {}
    kept = stages.validate_units(raw, {"00:00:01", "00:00:02"}, diag=diag)
    texts = {k["text"] for k in kept}
    assert texts == {"A", "C"}                       # B rejeté (ts), doublon retiré
    assert diag["stage2_validate"]["rejected_unsupported_ts"] == 1
    assert diag["stage2_validate"]["exact_dups_removed"] == 1
    assert all(k["gid"].startswith("g") for k in kept)


# ---------- veto d'exclusion déterministe ----------
def test_veto_rescue_contenu_hors_queue_et_garde_la_queue():
    units = [
        {"gid": "g1", "text": "Le monde est fini.", "start": "00:03:42", "kind": "fact"},
        {"gid": "g2", "text": "Un theme central developpe ici.", "start": "00:03:50", "kind": "argument"},
        {"gid": "g3", "text": "Foudre sur un immeuble.", "start": "00:08:00", "kind": "event"},
    ]
    classified = {"g1": "off_topic", "g2": "T1", "g3": "off_topic"}
    diag = {}
    final, _ = stages.apply_veto(classified, units, diag=diag)
    assert final["g1"] == "T1"        # hors queue → réassigné au thème voisin
    assert final["g3"] == "off_topic" # bloc de queue → conservé exclu
    assert any(o["gid"] == "g1" for o in diag["stage4_veto"]["overrides"])


# ---------- scelleur de segment de clôture (nouveau locuteur de queue) ----------
def _u(gid, start, spk, text="texte de contenu suffisant ici", kind="fact"):
    return {"gid": gid, "start": start, "speaker": spk, "text": text, "kind": kind}


def test_veto_demote_bulletin_de_fin_sans_le_supprimer():
    # Bulletin de fin d'un NOUVEAU locuteur (Speaker 3) qui tient la parole
    # JUSQU'À LA FIN : DÉMOTION vers la strate CLÔTURE, jamais suppression.
    units = [_u(f"g{i}", f"00:0{i}:00", "Speaker 1") for i in range(8)]
    units += [_u("g8", "00:08:00", "Speaker 1", "election closing", kind="event"),
              _u("g9", "00:08:30", "Speaker 3", "la foudre s abat sur un immeuble"),
              _u("g10", "00:08:40", "Speaker 3", "la charpente s embrase")]
    classified = {**{f"g{i}": "T1" for i in range(8)},
                  "g8": "off_topic", "g9": "T1", "g10": "T1"}
    diag = {}
    final, _ = stages.apply_veto(classified, units, diag=diag)
    assert final["g8"] == final["g9"] == final["g10"] == "closing"     # démoté, pas supprimé
    assert all(final[f"g{i}"] == "T1" for i in range(8))               # corps intact
    assert diag["stage4_veto"]["demoted_tail"] == ["g8", "g9", "g10"]
    # NON DESTRUCTIF : les unités démotées survivent en strate CLÔTURE.
    tiers = stages.collect_tiers(units, final)
    assert [r["gid"] for r in tiers["closing"]] == ["g8", "g9", "g10"]


def test_veto_ne_demote_pas_une_fin_normale_sans_nouveau_locuteur():
    # Fin normale : les DEUX locuteurs sont présents tout du long (ex. verdict
    # d'une rétro) — aucun locuteur n'apparaît seulement en queue.
    spk = ["Speaker 1", "Speaker 2"]
    units = [_u(f"g{i}", f"00:0{i}:00", spk[i % 2]) for i in range(10)]
    classified = {**{f"g{i}": "T1" for i in range(10)}, "g8": "dialogic_noise"}
    diag = {}
    final, _ = stages.apply_veto(classified, units, diag=diag)
    assert not diag["stage4_veto"]["demoted_tail"]    # aucun nouveau locuteur -> rien démoté
    assert final["g9"] == "T1"                         # verdict de fin préservé


def test_veto_ne_demote_pas_conclusion_quand_locuteur_principal_reprend():
    # Cas du débat télévisé : un modérateur (Speaker 5, seulement en queue) donne la
    # parole (« je te laisse conclure ») PUIS un locuteur PRINCIPAL (Speaker 2)
    # conclut. Le nouveau locuteur ne tient PAS jusqu'à la fin -> AUCUNE démotion :
    # la conclusion reste en strate principale, jamais supprimée.
    spk = ["Speaker 1", "Speaker 2"]
    units = [_u(f"g{i}", f"00:0{i}:00", spk[i % 2]) for i in range(8)]
    units += [_u("g8", "00:08:00", "Speaker 5", "je te laisse conclure", kind="event"),
              _u("g9", "00:08:30", "Speaker 2",
                 "l Espagne et l Allemagne prouvent que la hausse ne pose pas de probleme",
                 kind="argument"),
              _u("g10", "00:08:40", "Speaker 2", "la dette publique reste soutenable",
                 kind="argument")]
    classified = {**{f"g{i}": "T1" for i in range(8)},
                  "g8": "dialogic_noise", "g9": "T1", "g10": "T1"}
    diag = {}
    final, _ = stages.apply_veto(classified, units, diag=diag)
    assert not diag["stage4_veto"]["demoted_tail"]    # rien démoté
    assert final["g9"] == final["g10"] == "T1"         # conclusion en strate PRINCIPALE


# ---------- strates de contenu (SECONDARY / CLOSING / NOISE) ----------
def test_other_survit_en_secondary():
    units = [_u("g1", "00:00:01", "S1", "un point on-sujet hors thème")]
    tiers = stages.collect_tiers(units, {"g1": "other"})
    assert [r["gid"] for r in tiers["secondary"]] == ["g1"]
    assert tiers["closing"] == [] and tiers["noise"] == []


def test_off_topic_survit_en_closing():
    units = [_u("g1", "00:00:01", "S1", "aparte hors sujet")]
    tiers = stages.collect_tiers(units, {"g1": "off_topic"})
    assert [r["gid"] for r in tiers["closing"]] == ["g1"]
    assert tiers["secondary"] == [] and tiers["noise"] == []


def test_dialogic_court_omis_du_corps_mais_trace():
    units = [_u("g1", "00:00:01", "S1", "merci beaucoup")]
    tiers = stages.collect_tiers(units, {"g1": "dialogic_noise"})
    # hors corps (ni MAIN, ni SECONDARY, ni CLOSING) mais tracé (provenance).
    assert tiers["secondary"] == [] and tiers["closing"] == []
    assert [r["gid"] for r in tiers["noise"]] == ["g1"]
    assert tiers["noise"][0]["start"] == "00:00:01"


def test_aucune_unite_validee_perdue_entre_les_strates():
    # Chaque unité validée est placée dans exactement une strate — jamais perdue.
    units = [_u("g1", "00:00:01", "S1", kind="fact"),      # -> MAIN (T1)
             _u("g2", "00:00:02", "S1", "hors theme"),      # -> SECONDARY
             _u("g3", "00:00:03", "S1", "aparte"),          # -> CLOSING
             _u("g4", "00:00:04", "S1", "voila")]           # -> NOISE
    final = {"g1": "T1", "g2": "other", "g3": "off_topic", "g4": "dialogic_noise"}
    tiers = stages.collect_tiers(units, final)
    main = sum(1 for b in final.values() if b in stages.THEMES)
    total = main + len(tiers["secondary"]) + len(tiers["closing"]) + len(tiers["noise"])
    assert total == len(units)


# ---------- garde B4 (thèse / tensions) ----------
def test_b4_guard_accepte_synthese_contenue():
    byid = {"g1": {"text": "La France gagne en 2018 face à l'Argentine."},
            "g2": {"text": "Elle échoue en 2021."}}
    ok, reason, _ = stages.b4_guard_unit(
        "La France gagne en 2018 puis échoue en 2021.", ["g1", "g2"], byid)
    assert ok and reason is None


def test_b4_guard_rejette_support_vide():
    ok, reason, _ = stages.b4_guard_unit("Une tension quelconque.", [], {})
    assert not ok and reason == "empty_support"


def test_b4_guard_rejette_nombre_fabrique():
    byid = {"g1": {"text": "La France gagne le titre."}}
    ok, reason, detail = stages.b4_guard_unit(
        "La France gagne le titre en 2018.", ["g1"], byid)
    assert not ok and reason == "containment" and "2018" in detail["missing_numbers"]


def test_b4_guard_rejette_entite_fabriquee():
    byid = {"g1": {"text": "L'équipe échoue en demi-finale."}}
    ok, reason, detail = stages.b4_guard_unit(
        "L'équipe échoue en demi-finale contre l'Espagne.", ["g1"], byid)
    assert not ok and reason == "containment" and "Espagne" in detail["missing_entities"]


# ---------- garde de contenance / provenance ----------
def test_guard_rejette_nombre_et_entite_absents():
    support = "L'equipe gagne en 2018 face a l'Espagne."
    ok = guard.check_sentence("Victoire en 2018 contre l'Espagne.", support)
    assert ok["ok"]
    bad_num = guard.check_sentence("Victoire en 98 contre l'Espagne.", support)
    assert not bad_num["ok"] and "98" in bad_num["missing_numbers"]
    bad_ent = guard.check_sentence("Victoire en 2018 contre l'Argentine.", support)
    assert not bad_ent["ok"] and "Argentine" in bad_ent["missing_entities"]
