"""Pipeline de rapport par strates.

Le compte-rendu de l'application y passe par `app_report`, derrière le seam
`report.generate_report`. `pipeline.make_experimental_report` est une autre entrée,
hors du chemin par défaut : elle écrit sous `output/.experimental/` et refuse de
tourner tant que `config.EXPERIMENTAL_REPORT` est faux.

Architecture (8 étapes) :
  1 extraction atomique bornée en tokens   (LLM)
  2 validation timestamp/support + conso    (code)
  3 classification fermée par thème profil   (LLM)
  4 veto d'exclusion déterministe            (code)
  5 structure bornée (thèse/tensions/plan)   (LLM)
  6 rendu fidèle une-phrase-par-unité        (LLM)
  7 garde de contenance / provenance         (code)
  8 assemblage rapport + diagnostics         (code)
"""
