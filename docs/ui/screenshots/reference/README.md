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
| `07-voices.png` | Voix (registre + détail) | réel — **état vide** : la démo ne contient pas de profil de voix |
| `08-duplicates.png` | Doublons | réel (1 groupe) — **doublon fabriqué** avec un extrait de 20 s importé deux fois ; pris après cet import |
| `09-trash.png` | Corbeille | réel (2 éléments : le transcript et le compte rendu d'une même réunion) |
| `10-maintenance.png` | Maintenance (état & index) | réel — pris avant l'import du doublon (0 doublon) |
| `11-versions.png` | Réunion — Versions | réel |
| `12-information.png` | Réunion — Informations | réel — pris après l'import du doublon (la barre latérale compte 4 réunions) ; la carte « Usage et coût » dit « Aucun usage enregistré. » |
| `13-library-empty.png` | Bibliothèque — **état vide** | portée projet réellement vide |
| `15-share.png` | Réunion — fenêtre de partage | réel — aucun partage encore |

### `desktop/dark/` — 1440×900, écrans produit clés
`01-transcribe.png` · `02-library.png` · `03-meeting-report.png` · `04-transcript-player.png`

### `mobile/light/` — 390×844
`01-transcribe.png` · `02-library.png` · `03-meeting-report.png` · `04-transcript-player.png` — le dock flottant (navigation primaire) se voit sur chacune ; il n'a plus de capture à part.

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
3. un extrait de deux minutes d'ES2002a, importé sans compte rendu ;
4. la corbeille : le transcript et le compte rendu de la réunion IS1009a, archivés
   (les deux éléments de `09-trash`) ;
5. un **doublon fabriqué** : un extrait de 20 s, importé deux fois par le pipeline réel
   (transcription seule, sans locuteurs ni compte rendu) sous le nom `doublon-fabrique`.
   Il est importé **après** toutes les captures qui ne doivent pas le montrer :
   `08-duplicates` est donc pris après l'import, `10-maintenance` avant (0 doublon), et
   `12-information` (reprise après l'import) montre 4 réunions dans la barre latérale.

La démo ne contient **aucun profil de voix** : `07-voices` montre l'état vide.

## Couverture & limites connues

- **Réel** : toutes les captures sont réelles. L'Assistant est montré sans question
  posée (état d'accueil, aucun appel au modèle) ; l'état vide de la Bibliothèque vient
  d'un projet réellement vide.
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

## Provenance de cette base

Prise le 14 novembre 2026 sur la démo locale (`http://127.0.0.1:9200`, l'API en 9201),
avec les sources de l'application du commit indiqué. Le script de capture utilisé est
une copie de `capture.mjs`, qui en diffère sur quatre points : une session enregistrée
au lieu de la connexion par identifiant ; `BASE` sans valeur par défaut ; une sortie
refusée hors du dossier de travail prévu ; et une liste d'écrans à prendre (certains
écrans se prennent après l'import du doublon, d'autres avant). Le `capture.mjs` livré ici
signale désormais chaque échec (« ✗ ») et sort en erreur, au lieu d'annoncer « ✓ » sur
un écran de chargement.

| Images | Commit des sources |
|---|---|
| 20 images (toutes sauf les trois ci-dessous) | `12013bd` |
| `desktop/light/12-information.png` | `06fae57` |
| `mobile/light/03-meeting-report.png`, `mobile/light/04-transcript-player.png` | `996502f` (fil d'Ariane coupé par une ellipse sous 820 px) |
