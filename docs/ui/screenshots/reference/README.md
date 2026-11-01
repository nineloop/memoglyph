# Baseline visuelle de l'interface

Jeu de captures de référence de l'interface Memoglyph.
But : pouvoir **revoir, partager et comparer** l'UI d'une itération à
l'autre. Les images sont régénérables par un script Playwright déterministe.

> Ces captures pilotent la **vraie application** (front + backend locaux). Aucune
> donnée n'est fabriquée dans le code de production ; le script ne fait que
> naviguer et photographier des états réels.

## Inventaire

### `desktop/light/` — 1440×900, toutes les destinations
| Fichier | Écran | Données |
|---|---|---|
| `00-connexion.png` | Connexion (champs vides) | réel — avant toute session |
| `01-transcribe.png` | Transcrire (pipeline) | état initial (dropzone) |
| `02-library.png` | Bibliothèque — galerie de projets + contenus récents | réel (vue globale) |
| `03-meeting-report.png` | Réunion — Compte-rendu (lecture éditoriale) | réel |
| `04-transcript-player.png` | Transcript — frise d'onde + locuteurs | réel (diarisé, onde décodée) |
| `05-search.png` | Recherche groupée par réunion (`q=remote control`) | réel — titres humains, retour horodaté |
| `06-assistant.png` | Assistant (intro) | réel — pas d'appel LLM |
| `07-voices.png` | Voix (registre + détail) | réel (4 voix) |
| `08-duplicates.png` | Doublons | réel (1 groupe) |
| `09-trash.png` | Corbeille | réel (1 élément) |
| `10-maintenance.png` | Maintenance (état & index) | réel |
| `11-versions.png` | Réunion — Versions | réel |
| `12-information.png` | Réunion — Informations | réel |
| `13-library-empty.png` | Bibliothèque — **état vide** | portée projet réellement vide |
| `15-share.png` | Réunion — fenêtre de partage | réel — aucun partage encore |

### `desktop/dark/` — 1440×900, écrans produit clés
`01-transcribe.png` · `02-library.png` · `03-meeting-report.png` · `04-transcript-player.png`

### `mobile/light/` — 390×844
`00-dock.png` (dock flottant, navigation primaire) · `01-transcribe.png` · `02-library.png` · `03-meeting-report.png` · `04-transcript-player.png`

### `mobile/dark/`
Réservé aux itérations futures (non peuplé dans cette baseline).

## Paramètres de capture

- **Viewports** : desktop `1440×900`, mobile `390×844`, `deviceScaleFactor: 1`.
- **Langue** : forcée à `fr` (`localStorage['memoglyph.lang']`) pour la reproductibilité.
- **Thème** : forcé via `localStorage['memoglyph.theme']` (`light` / `dark`) — aucun flash, aucune bascule animée.
- **Stabilité** : animations/transitions/caret neutralisés avant la capture ; curseur déplacé en (0,0) ; pas de survol, pas de toast, capture au niveau du **viewport** (dimensions constantes), **sans chrome navigateur**.
- **Confidentialité** : aucune clé/API, aucun chemin de fichier local, aucune donnée personnelle. Les identifiants ULID et titres de médias affichés ne sont pas secrets.

## Démarrer l'application

Backend (API stable, non modifiée) et front, en local :

```bash
# 1) Backend FastAPI (port 8771)
.venv/bin/python -m uvicorn api.main:app --port 8771

# 2) Front Vite (port 8780, proxifie /api -> 8771)
npm --prefix ui run dev -- --port 8780 --strictPort
```

Pour photographier la bibliothèque de démo plutôt que la sienne, le backend se lance
sur une **copie neuve** de la bibliothèque de démo, sans `.env`, avec une copie fraîche
de `core/` et `api/` : `--app-dir <copie>`. Ses chemins (`output/`, `index.db`,
`usage.db`) sont alors ceux de la copie. Le premier compte s'y crée par le code
d'installation, puis `memoglyph attribuer-bibliotheque <compte> --appliquer` lui
donne les projets.

Les captures montrent l'application telle qu'elle est livrée : l'option des faits
(`MEMOGLYPH_FAITS=1`) est coupée, aucun onglet Faits n'y paraît.

## Régénérer les captures

```bash
# une fois : outil de capture
npm --prefix ui i -D playwright
npx --prefix ui playwright install chromium

# puis, les deux serveurs lancés :
MEETING=<ulid> EMPTY_PROJECT=<ulid> node docs/ui/screenshots/reference/capture.mjs
```

Variables d'environnement : `MEETING` (id de réunion pour l'espace document),
`EMPTY_PROJECT` (id d'un projet vide pour l'état vide), `IDENTIFIANT` et
`MOT_DE_PASSE` (un compte d'essai de la racine capturée : la connexion est
obligatoire) sont **requises**. `PLAYWRIGHT` désigne, au besoin, le dossier d'un paquet
`playwright` déjà installé ailleurs sur la machine, dont les navigateurs sont en cache,
à la place de l'installation dans `ui/`. Facultatives :
`BASE` (défaut `http://localhost:8780`), `QUERY` (requête de `05-search`, défaut
`remote control`) et `ONLY` (liste de noms d'écrans à refaire, par exemple
`ONLY=08-duplicates,09-trash`).

## Corpus capturé

Les captures montrent la **bibliothèque de démo**, produite en local sur le corpus
public AMI (CC BY 4.0, voir [`docs/corpus-demo.md`](../../../corpus-demo.md)), jamais
une bibliothèque réelle :

1. un extrait de 12 min 58 s de la réunion IS1009a, à partir de 61 s (le début est un
   silence), importé sous le titre « AMI IS1009a, réunion de lancement d'une
   télécommande », avec diarisation, profil et compte-rendu ;
2. un projet « Projet vide », pour `13-library-empty` ;
3. le même extrait importé une seconde fois en transcription seule (le doublon de
   `08-duplicates`), et un extrait de deux minutes d'ES2002a importé et archivé
   (l'élément de `09-trash`). Les captures étant refaites en un seul passage, le doublon
   paraît aussi dans la bibliothèque et la recherche ;

## Couverture & limites connues

- **Réel** : toutes les captures sauf l'intro Assistant (état d'accueil, sans appel
  au modèle) et l'état vide Bibliothèque (portée projet réellement vide).
- **Non capturé** : la **réponse** de l'Assistant (nécessite le modèle local, non
  déterministe) ; `mobile/dark/`.
- **Reprendre l'écoute** : la position de lecture et la dernière écoute sont
  mémorisées **côté client** (`localStorage`) — état honnête, sans capacité
  backend simulée. Le bandeau « Reprendre l'écoute » n'apparaît donc que lorsqu'une
  écoute a réellement eu lieu ; il est absent des captures à froid (contexte vierge).
- **Architecture** : « Doublons » et « Corbeille » sont des onglets de
  `MaintenancePanel` ; leur écran porte donc le titre « Maintenance » (comportement
  actuel du produit, non modifié ici).
- **Titres humains — limites backend** : la bibliothèque, la recherche et la
  reprise affichent le nom de source humain quand il est disponible. Deux
  charges utiles ne le portent pas et retombent honnêtement sur le slug :
  - `SearchResult` d'un **compte-rendu** : `source` nul → le groupe recherche
    garde « `<slug>` — compte-rendu ».
  - `VoiceOccurrence` (registre des voix) : pas de champ `source` → les
    occurrences affichent le slug. **Requis backend** : ajouter `source` à
    `VoiceOccurrence` (et à `SearchResult` des rapports) pour un titre humain.
