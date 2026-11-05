# `docs/` — index

Deux catégories, à ne pas confondre. La **source de vérité** de l'architecture
reste le **code**, puis [../SPEC.md](../SPEC.md) et [../README.fr.md](../README.fr.md).

## Guide d'utilisation et de référence

Le détail que le [README](../README.fr.md) ne répète pas : une ligne par sujet. Ces documents sont en français.

| Fichier | Sujet |
|---|---|
| [fonctionnalites.md](fonctionnalites.md) | Ce que fait Memoglyph, état du projet, exemples d'usage, parcours produit |
| [installation.md](installation.md) | Prérequis, démarrage, arrêt et relance, mode réseau local, modèles, diarisation |
| [comptes-et-partage.md](comptes-et-partage.md) | Connexion, comptes, attribution de la bibliothèque, protections de l'API |
| [limites.md](limites.md) | Limites connues |
| [overview.md](overview.md) | Aperçu en anglais : questions et réponses courtes sur ce que fait Memoglyph |
| [local-et-en-ligne.md](local-et-en-ligne.md) | Ce qui reste sur le Mac, ce qui peut partir vers un fournisseur en ligne |
| [architecture.md](architecture.md) | Organisation du code, des fichiers et des données |
| [configuration.md](configuration.md) | Variables d'environnement et réglages |
| [provenance.md](provenance.md) | Provenance des générations |
| [sauvegarde.md](sauvegarde.md) | Sauvegarde et restauration |
| [maintenance.md](maintenance.md) | Archives historiques, corbeille et suppression, portée des projets et des voix |
| [developpement.md](developpement.md) | Commandes de développement, tests sur vrais modèles |
| [modeles-par-defaut.md](modeles-par-defaut.md) | Les modèles utilisés par défaut |

## Référence courante

Documents décrivant un mécanisme *actuel* du produit. À maintenir avec le code.

| Fichier | Sujet | Note |
|---|---|---|
| [REPORT_TEMPLATES.md](REPORT_TEMPLATES.md) | Choix du gabarit de CR par genre, de bout en bout | La **sélection du genre** (type du profil d'abord, définitions, garde-fou du sport) décrit le code actuel ; le contrat de génération de production est désormais le pipeline riche par strates ([../SPEC.md §12bis](../SPEC.md)), pas seulement l'ancien `make_report`. |
| [STATUTS_GENERATION.md](STATUTS_GENERATION.md) | Les 4 statuts de génération (`PASS_DIRECT` / `PASS_REPAIRED` / `PASS_RETRY` / `FAIL`) | Courant |
| [manual-release-smoke.md](manual-release-smoke.md) | Checklist manuelle de smoke avant release | Vivant |
| [corpus-demo.md](corpus-demo.md) | Corpus de démo public (AMI, CC BY 4.0) : contenu, récupération vérifiée, attribution | Courant |

## FinOps — référence courante (implémenté en V1)

Le suivi local d'usage & coût IA est **livré et accepté** (V1 ;
magasin séparé `usage.db`). Ces documents décrivent le mécanisme *actuel* ; à
maintenir avec le code.

| Fichier | Sujet | Note |
|---|---|---|
| [finops-pricing.md](finops-pricing.md) | Provenance & ratification des tarifs, hypothèses de coût local | Courant |
| [finops-usage-daily.md](finops-usage-daily.md) | Agrégat dérivé `usage_daily` + API de lecture bornée projet | Courant |

## Mécanismes livrés récemment

Le compagnon de capture (bêta), le serveur MCP et les faits de la réunion (expérimental).

| Fichier | Sujet | Note |
|---|---|---|
| [compagnon-capture.md](compagnon-capture.md) | Compagnon de barre de menus : capture d'une visio ou d'un replay, envoi à Memoglyph | Livré en bêta, macOS seulement ; spécification d'origine |
| [mcp.md](mcp.md) | Serveur MCP en lecture seule : lancement, outils, garanties (portée du compte, jamais d'ultra-confidentiel, aucune écriture) | Livré |
| [ecl.md](ecl.md) | Faits de la réunion (ECL) : décisions, actions, problèmes et chiffres d'une réunion, ancrés sur leur citation | Livré, expérimental et désactivé par défaut (`MEMOGLYPH_FAITS=1`) |

Les captures d'écran de référence de l'interface vivent sous `ui/screenshots/reference/`
(voir [leur README](ui/screenshots/reference/README.md)).
