# `docs/` — index

Deux catégories, à ne pas confondre. La **source de vérité** de l'architecture
reste le **code**, puis [../SPEC.md](../SPEC.md) et [../README.fr.md](../README.fr.md).

## Référence courante

Documents décrivant un mécanisme *actuel* du produit. À maintenir avec le code.

| Fichier | Sujet | Note |
|---|---|---|
| [REPORT_TEMPLATES.md](REPORT_TEMPLATES.md) | Choix du gabarit de CR par genre, de bout en bout | Relevé dans le code avant le pipeline riche. La **sélection du genre** reste valable ; le contrat de génération de production est désormais le pipeline riche par strates ([../SPEC.md §12bis](../SPEC.md)), pas seulement l'ancien `make_report`. |
| [STATUTS_GENERATION.md](STATUTS_GENERATION.md) | Les 4 statuts de génération (`PASS_DIRECT` / `PASS_REPAIRED` / `PASS_RETRY` / `FAIL`) | Courant |
| [manual-release-smoke.md](manual-release-smoke.md) | Checklist manuelle de smoke avant release | Vivant |
| [corpus-demo.md](corpus-demo.md) | Corpus de démo public (AMI, CC BY 4.0) : contenu, récupération vérifiée, attribution | Courant |

## FinOps — référence courante (implémenté en V1)

La télémétrie d'usage & coût IA est **livrée et acceptée** (V1 ;
magasin séparé `usage.db`). Ces documents décrivent le mécanisme *actuel* ; à
maintenir avec le code.

| Fichier | Sujet | Note |
|---|---|---|
| [finops-pricing.md](finops-pricing.md) | Provenance & ratification des tarifs, hypothèses de coût local | Courant |
| [finops-usage-daily.md](finops-usage-daily.md) | Agrégat dérivé `usage_daily` + API de lecture bornée projet | Courant |

## Conception à venir

Spécifications de chantiers non commencés : périmètre, prérequis, choix à arbitrer.

| Fichier | Sujet | Note |
|---|---|---|
| [compagnon-capture.md](compagnon-capture.md) | Compagnon de barre de menus : capture d'une visio ou d'un replay, envoi à Memoglyph | Proposition, choix ouverts (section 5) |
| [mcp.md](mcp.md) | Serveur MCP en lecture seule : lancement, outils, garanties (portée du compte, jamais d'ultra-confidentiel, aucune écriture) | Livré |
| [ecl.md](ecl.md) | Faits de la réunion (ECL) : décisions, actions, problèmes et chiffres d'une réunion, ancrés sur leur citation | Livré, expérimental et désactivé par défaut (`MEMOGLYPH_FAITS=1`) |

Les captures d'écran de référence de l'interface vivent sous `ui/screenshots/reference/`
(voir [leur README](ui/screenshots/reference/README.md)).
