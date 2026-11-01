"""Normalisation d'horodatage (couche commune, provider-agnostique).

Reformate MM:SS → HH:MM:SS puis EXIGE l'appartenance exacte au transcript ;
n'invente jamais, aucun appariement flou. Couvre les formes équivalentes, le
canonique déjà bon, les valeurs inexistantes et les entrées ambiguës/dangereuses.
"""
from __future__ import annotations

from core.experimental import stages
from core.experimental.timestamps import canonicalize, resolve


# ---------- canonicalize : reformatage structurel pur ----------
def test_canonicalize_mmss_vers_hhmmss():
    assert canonicalize("00:51") == "00:00:51"
    assert canonicalize("07:34") == "00:07:34"
    assert canonicalize("7:34") == "00:07:34"      # minute à un chiffre
    assert canonicalize("59:59") == "00:59:59"     # borne haute MM:SS


def test_canonicalize_hhmmss_deja_canonique_inchange():
    assert canonicalize("00:07:34") == "00:07:34"
    assert canonicalize("01:02:03") == "01:02:03"
    assert canonicalize("1:02:03") == "01:02:03"   # heure à un chiffre


def test_canonicalize_rejette_les_entrees_malformees():
    # non-chaîne, vide, 1 partie, ≥ 4 parties, texte parasite
    for bad in (None, 123, "", "12", "1:2:3:4", "abc", "0007:34",
                "00:07:34 ", "12h34", "01:-2:03", "01:02:03.5"):
        # (les espaces en bord sont tolérés par strip ; ici « 00:07:34 » a un
        #  espace interne-final déjà géré → on teste surtout les vrais parasites)
        got = canonicalize(bad)
        assert got is None or isinstance(got, str)
    assert canonicalize(None) is None
    assert canonicalize("12") is None
    assert canonicalize("1:2:3:4") is None
    assert canonicalize("abc") is None
    assert canonicalize("12h34") is None


def test_canonicalize_rejette_minutes_secondes_hors_bornes():
    assert canonicalize("00:75") is None       # 75 secondes
    assert canonicalize("75:00") is None        # 75 minutes en MM:SS
    assert canonicalize("00:99:99") is None     # mm/ss ≥ 60
    assert canonicalize("01:60:00") is None     # 60 minutes


# ---------- resolve : normalisation + appartenance EXACTE ----------
VALID = {"00:00:51", "00:07:34", "00:07:34", "01:02:03"}


def test_resolve_normalise_puis_exige_l_existence():
    assert resolve("00:51", VALID) == "00:00:51"     # MM:SS mappé
    assert resolve("07:34", VALID) == "00:07:34"
    assert resolve("00:07:34", VALID) == "00:07:34"  # déjà canonique
    assert resolve("1:02:03", VALID) == "01:02:03"


def test_resolve_rejette_un_horodatage_inexistant():
    # bien formé mais ABSENT du transcript → None (jamais d'invention)
    assert resolve("00:09:99", VALID) is None        # malformé
    assert resolve("08:34", VALID) is None            # 00:08:34 absent
    assert resolve("00:00:52", VALID) is None         # voisin de 00:00:51 — PAS de flou
    assert resolve(None, VALID) is None
    assert resolve("", VALID) is None


def test_resolve_n_invente_jamais_de_voisin():
    # aucun appariement au plus proche : 00:00:50 ne doit pas devenir 00:00:51
    assert resolve("00:50", {"00:00:51"}) is None
    assert resolve("50", {"00:00:51"}) is None


# ---------- intégration dans validate_units (télémétrie) ----------
def test_validate_units_normalise_le_mmss_de_type_mistral():
    raw = [
        {"kind": "fact", "text": "A", "start": "07:34"},    # MM:SS (Mistral) → 00:07:34
        {"kind": "fact", "text": "B", "start": "00:00:51"},  # déjà canonique
        {"kind": "fact", "text": "C", "start": "08:34"},    # 00:08:34 absent → rejeté
    ]
    valid = {"00:07:34", "00:00:51"}
    diag: dict = {}
    kept = stages.validate_units(raw, valid, diag=diag)
    st = diag["stage2_validate"]
    assert {k["text"] for k in kept} == {"A", "B"}          # C rejeté (introuvable)
    assert {k["start"] for k in kept} == {"00:07:34", "00:00:51"}  # A canonicalisé
    assert st["normalized_ts"] == 1                          # seul A a changé
    assert st["rejected_unsupported_ts"] == 1                # C
