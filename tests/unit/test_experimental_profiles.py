"""Profil d'exécution du pipeline riche (tunables, mêmes contrats).

Vérifie : la résolution PAR CAPACITÉ (local → prudent, cloud → grand-contexte) ;
que le profil prudent égale EXACTEMENT les défauts Qwen (garde anti-régression) ;
et que les réglages (max_tokens, batch) sont bien transmis aux étapes.
"""
from __future__ import annotations

from core.experimental import profiles, stages


# ---------- résolution par capacité ----------
def test_resolve_local_prudent_cloud_grand_contexte():
    assert profiles.resolve("mlx") is profiles.CONSERVATIVE
    assert profiles.resolve("mistral") is profiles.LARGE_CONTEXT
    assert profiles.resolve("cloud") is profiles.LARGE_CONTEXT


def test_profil_prudent_egale_les_defauts_qwen():
    # Garde-fou : le profil prudent NE DOIT PAS dévier du comportement Qwen actuel.
    # `extract_max_tokens` 5000 (et non plus 3500) depuis les types décision, action et
    # question ouverte : voir `profiles.CONSERVATIVE`.
    c = profiles.CONSERVATIVE
    assert (c.chunk_tokens, c.extract_max_tokens, c.classify_batch,
            c.classify_max_tokens, c.structure_max_tokens, c.render_batch,
            c.render_max_tokens) == (2000, 5000, 10, 800, 1200, 12, 1500)


def test_grand_contexte_ne_regroupe_que_le_rendu():
    # Choix MESURÉ : SEUL le rendu est regroupé
    # (postérieur à la classification ⇒ neutre en contenu/strates). Tranche et
    # lot de classification restent au niveau prudent — agrandir la tranche perd
    # de la rétention, et agrandir la classification amplifie le rayon d'un échec
    # JSON de mistral-small. Ce test verrouille ce choix contre une dérive.
    g, c = profiles.LARGE_CONTEXT, profiles.CONSERVATIVE
    assert g.render_batch > c.render_batch          # seul gain d'appels retenu
    assert g.chunk_tokens == c.chunk_tokens          # rétention d'extraction préservée
    assert g.extract_max_tokens == c.extract_max_tokens
    assert g.classify_batch == c.classify_batch      # rayon d'échec JSON borné


# ---------- plumberie : les réglages atteignent bien les étapes ----------
def test_extract_atomic_honore_max_tokens(monkeypatch):
    captured: list[int] = []

    def fake_call(system, user, *, provider, seed, max_tokens, calls, envoi=None):
        captured.append(max_tokens)
        calls.append({"latency_s": 0.0, "error": None, "in_tok_est": 1,
                      "out_tok_est": 1, "parsed": True})
        return {"units": []}

    monkeypatch.setattr(stages, "_call", fake_call)
    stages.extract_atomic(["chunk"], provider="mistral", seed=42, diag={}, max_tokens=8000)
    assert captured == [8000]


def test_classify_units_honore_batch_et_max_tokens(monkeypatch):
    captured: list[int] = []

    def fake_call(system, user, *, provider, seed, max_tokens, calls, envoi=None):
        captured.append(max_tokens)
        calls.append({"latency_s": 0.0, "error": None, "in_tok_est": 1,
                      "out_tok_est": 1, "parsed": True})
        return {"assignments": []}

    monkeypatch.setattr(stages, "_call", fake_call)
    units = [{"gid": f"g{i}", "kind": "fact", "text": "x", "start": "00:00:01"}
             for i in range(25)]
    stages.classify_units(units, ["T"], provider="mistral", seed=42, diag={},
                          batch=40, max_tokens=3000)
    # batch=40 ≥ 25 unités ⇒ UN SEUL appel, à 3000 jetons
    assert captured == [3000]


def test_render_and_guard_honore_batch_et_max_tokens(monkeypatch):
    captured: list[int] = []

    def fake_call(system, user, *, provider, seed, max_tokens, calls, envoi=None):
        captured.append(max_tokens)
        calls.append({"latency_s": 0.0, "error": None, "in_tok_est": 1,
                      "out_tok_est": 1, "parsed": True})
        return {"sentences": []}

    monkeypatch.setattr(stages, "_call", fake_call)
    units = [{"gid": f"g{i}", "kind": "fact", "text": "x", "start": f"00:00:0{i}"}
             for i in range(5)]
    final = {f"g{i}": "T1" for i in range(5)}
    stages.render_and_guard(units, final, ["Theme"], provider="mistral", seed=42,
                            diag={}, batch=40, max_tokens=6000)
    # batch=40 ≥ 5 unités du thème ⇒ un seul appel de rendu, à 6000 jetons
    assert captured == [6000]
