# Compagnon de capture : spécification

> **Statut : proposition, rien n'est implémenté.** Ce document fixe le périmètre,
> les prérequis et les choix à arbitrer avant la première ligne de code. Chaque
> choix tranché reçoit un identifiant de décision et passe de la section 5 à la
> section 4.5.

## 1. Objet

Une petite application native macOS, dans la barre de menus, qui **enregistre** une
réunion de visio ou un webinar en replay et en **confie le fichier** à Memoglyph.
Memoglyph reste un seul produit : le compagnon ne transcrit pas, ne résume pas et
ne stocke rien d'autre que l'enregistrement en cours.

**Pourquoi une application native :** une page web ne peut ni capter le son d'une
autre application (Zoom, Teams, un navigateur), ni savoir qu'une application utilise
le micro. Seule une application macOS, avec les autorisations de l'utilisateur, y a
accès.

**Hors périmètre :**
- la transcription en direct (le découpage STT progressif est écarté) ;
- un robot qui rejoint la réunion ;
- Windows et Linux ;
- **le dédoublonnage des captures** (parqué) : quand plusieurs participants ont le
  compagnon, une seule personne clique « Enregistrer », comme on le fait aujourd'hui
  dans Zoom ou Teams. Reconnaître une réunion déjà capturée (par exemple par
  l'identifiant d'un événement de calendrier) viendra plus tard, si le besoin
  apparaît.

## 2. Cas d'usage

| # | Cas | Sources audio | Déclenchement |
|---|---|---|---|
| U1 | Visio en direct (Zoom, Teams, Meet dans le navigateur) | le micro et le son de l'application de visio | proposé à la détection, ou manuel |
| U2 | Webinar ou vidéo en replay | le son de l'application choisie, **sans micro** | manuel |
| U3 | Surveiller Memoglyph, et le piloter s'il est sur ce Mac | – | voir l'état, ouvrir l'interface ; démarrer et arrêter en local seulement |

Rien ne démarre seul : le compagnon **propose**, l'utilisateur **confirme**.

## 3. Architecture

```
┌──────────────── Mac ──────────────────────────────────────────────┐
│  Zoom / Teams / navigateur ──son──┐                               │
│  Micro ──────────────────────────┤                                │
│                                   ▼                               │
│                        Compagnon (Swift, barre de menus)          │
│                        écrit sur disque, au fil de l'eau          │
│                                   │ file d'envoi (réessaie)       │
│                                   ▼                               │
│        Memoglyph local : http://127.0.0.1:<port>/api/transcribe   │
└───────────────────────────────────┼───────────────────────────────┘
                                    └── ou Memoglyph distant (§6, phase 4)
```

- **Destination** : un réglage, « Ce Mac » ou « Un serveur » (adresse et jeton), toujours
  affiché (section 4.5). Par défaut, le Memoglyph local, port lu dans
  `.memoglyph-ports`.
- **Enregistrement toujours local d'abord.** L'envoi est un second temps, avec une
  file qui réessaie : rien n'est perdu si Memoglyph est arrêté, si le réseau coupe ou
  si le Mac se met en veille.
- **Jeton d'appareil** : rangé dans le trousseau macOS. La connexion est
  obligatoire partout, local compris : le compagnon envoie toujours son jeton (section 4.4).

## 4. Prérequis

### 4.1 Machine

| Prérequis | État |
|---|---|
| macOS avec les *process taps* de Core Audio (capture du son d'une application, macOS 14.2 à 14.4 et plus) | présent (macOS 27 sur la machine de développement) |
| Xcode et Swift | présents (Xcode 27, Swift 6.4) |
| Compte Apple gratuit dans Xcode (« Personal Team ») : signature stable, donc autorisations accordées une fois | à faire, gratuit |
| Autorisations accordées par l'utilisateur : micro, et capture audio du système | au premier lancement |

### 4.2 Memoglyph, côté local

| Prérequis | État |
|---|---|
| `/api/transcribe` accepte un fichier (jusqu'à 2 Go), avec projet, diarisation et compte-rendu | présent |
| Genre de compte-rendu `webinar` | présent |
| Jetons d'appareil : créés et retirés par le compte dans « Appareils », section 4.4 | présent |
| Provenance de la capture : `capture_application` (80 caractères au plus) et `capture_mode` (`visio` ou `replay`), écrites dans le front-matter (`capture`) et affichées dans l'onglet Information | présent |
| Accepter **deux pistes** (micro, système) : un WAV à deux canaux se transcrit, les deux canaux mêlés en mono avant le STT | présent |
| Exploiter les deux pistes : annulation d'écho, voir C4 ; piste « propriétaire », voir C14. L'archive audio reste mono | à faire, après la phase 1 |
| Erreurs de traitement lisibles, pour que le compagnon affiche la raison d'un échec (NOW n° 2 de la feuille de route) | à faire, en version réduite |

### 4.3 Memoglyph, côté distant (phase 4 seulement)

| Prérequis | État |
|---|---|
| Authentification | présente, jetons d'appareil compris (section 4.4) ; prérequis dur de tout hébergement |
| Cloisonnement multi-utilisateurs par espaces de travail | absent ; décidé, spécification à part à écrire |
| HTTPS | à mettre en place |
| Pipeline sur Linux, sans MLX ni puce Apple | non commencé (piste « hébergement souverain », PARKED) |
| Routage selon la sensibilité (une réunion confidentielle reste locale) | NEXT de la feuille de route |

### 4.4 Authentification

La connexion est obligatoire partout, en local comme à distance, par un
seul chemin de code. Le compagnon n'a pas de navigateur : il s'authentifie par un **jeton
d'appareil**, rangé dans le trousseau macOS.

- **Créer** : le compte ouvre « Appareils » dans la barre du haut, nomme l'appareil et
  copie le jeton (préfixe `mgj_`). Il n'est affiché qu'à ce moment ; Memoglyph n'en garde
  que l'empreinte, dans `auth.db`.
- **Envoyer** : chaque appel porte `Authorization: Bearer <jeton>`. Le jeton n'ouvre que
  les routes du compagnon : `POST /api/transcribe`, `GET /api/jobs/{job_id}` et
  `GET /api/jobs/{job_id}/events`, `GET /api/projects` (les projets du compte), plus
  `GET /api/health`, publique. Partout ailleurs, un jeton seul reçoit 401 : il ne lit
  pas une réunion, ne crée pas d'autre jeton et n'administre rien. Le lien de fin de
  traitement ouvre l'interface, où l'on se connecte.
- **Retirer** : un par un, dans « Appareils » ; le dernier usage de chaque jeton y est
  affiché. Désactiver un compte retire ses jetons, et une réactivation ne les rend pas.

S'authentifier ne suffit pas : à distance, Memoglyph sera **multi-utilisateurs**, par
espaces de travail. Ce cloisonnement change le cœur du produit (index,
recherche, RAG, voix, télémétrie) et fera l'objet d'une spécification à part. C'est un
prérequis du distant, pas du compagnon : le compagnon n'envoie qu'au projet que son
jeton autorise.

### 4.5 Décisions prises

**Surveiller partout, piloter seulement ce Mac.**

- Le compagnon a **une destination**, choisie dans ses réglages : « Ce Mac » ou « Un
  serveur » (adresse et jeton). Au premier lancement, il cherche une installation
  locale de Memoglyph et propose « Ce Mac » s'il en trouve une.
- La destination est **toujours affichée** : en tête du menu (« ● Memoglyph — ce Mac »)
  et dans la confirmation d'un enregistrement, à côté du rappel de consentement. Un
  enregistrement envoyé à un serveur quitte la machine.
- Ce que le menu propose se déduit de ce que le compagnon **peut faire**, jamais d'un
  nom de type :

  | Fonction | Ce Mac | Un serveur |
  |---|---|---|
  | État : en marche, injoignable, occupé | oui | oui |
  | Avancement du traitement en cours | oui | oui |
  | Ouvrir l'interface | oui | oui |
  | Enregistrer et envoyer | oui | oui, avec le jeton |
  | Démarrer, arrêter, redémarrer (par `start.sh` et `stop.sh`) | oui | non |
  | Lancer au démarrage du Mac | oui | sans objet |
  | Journaux | oui | non |

- **Piloter** suppose une installation locale dont le compagnon connaît le dossier, et
  une destination sur la machine.
- **Destination injoignable** : en local, le compagnon démarre Memoglyph puis envoie ;
  à distance, il garde l'enregistrement en file d'attente et réessaie, sans jamais
  tenter de démarrer un serveur. Un serveur tourne sous son propre gestionnaire de
  services.
- **Suite possible, non engagée** : deux destinations connues, avec un choix par
  enregistrement prérempli par le routage selon la sensibilité.

**Authentification gérée par l'application, sans fournisseur externe.**

- Comptes dans un magasin SQLite séparé, mots de passe hachés en Argon2id, sessions par
  cookie (`HttpOnly`, `Secure`, `SameSite=Strict`) stockées hachées et révocables.
- Jetons d'appareil pour le compagnon : générés dans l'interface, stockés hachés,
  révocables un par un.
- Pas de JWT. Réinitialisation du mot de passe par `memoglyph reset-password
  <utilisateur>`, sur la machine, sans e-mail.
- Un seul point d'entrée répond à « qui appelle ? » : un fournisseur d'identité (OIDC,
  LDAP) peut y être branché par un contributeur, le produit n'en dépend pas.
- **Amendement : connexion obligatoire partout, local compris**, un seul chemin
  de code. Au premier lancement, un code d'installation à usage unique s'affiche dans
  le terminal, et l'interface ne montre que l'écran « Créer le compte administrateur ».
  Pas d'inscription libre ni de compte par défaut. Le compagnon s'authentifie donc par
  son jeton d'appareil, en local comme à distance.

**Multi-utilisateurs par espaces de travail, avec partage.**

- Un utilisateur est lié à un espace de travail, qui contient plusieurs projets.
- **Amendement : partage v1 d'une réunion ou d'un projet**, en lecture seule,
  avec des comptes existants, sans lien public. Pas de partage d'espace entier en v1.
- Les voix deviennent globales **à l'espace de travail**, et non plus au corpus entier.
- Spécification à part, à écrire : le cloisonnement.

### 4.6 Consentement

Enregistrer une conversation privée sans le consentement des participants est
interdit en droit français. Le compagnon :
- ne démarre jamais seul ;
- affiche un indicateur permanent pendant l'enregistrement ;
- rappelle, au démarrage d'une visio, que les participants doivent être prévenus
  (choix C6).

Pour un replay, la question est celle de l'usage privé du contenu.

### 4.7 Le menu Visio et l'installation

**Le menu « Démarrer en Visio »** (`ChoixVisio.menu`, `CaptureCore/Visio.swift`) se
calcule à chaque ouverture, depuis les processus qui ont une sortie audio et les
applications ouvertes :

1. **les appels en cours** en tête, « Appel en cours : <application> » : le groupe de
   l'application, auxiliaires compris, joue **et** prend le micro, éventuellement par deux
   auxiliaires différents ; sans appel, une ligne le dit (« Aucun appel détecté : lancez
   l'appel, puis rouvrez ce menu ») ;
2. **une ligne par application ouverte**, celles qui jouent d'abord (●). Un processus va à
   l'application dont l'identifiant est le sien ou son préfixe suivi d'un point, le plus
   long l'emporte ; la ligne vise l'identifiant du parent, qui couvre ses auxiliaires. Le
   processus GPU de WebKit porte le son des pages de Safari ;
3. **« Autres processus… »** : ce qui joue sans appartenir à une application ouverte ;
4. **« Tout le son du Mac »** : un tap global, micro compris, qui exclut le compagnon.

Le compagnon ne se liste jamais lui-même.

**Installation** : `make companion` assemble un paquet `.app` signé (ad hoc par défaut,
`SIGN_IDENTITY` sinon), l'installe dans `/Applications`, ou dans `~/Applications` avec un
message si `/Applications` n'est pas accessible en écriture, puis le montre dans le Finder
(`open -R`). `DEST=<dossier>` choisit un autre dossier, sans repli ; `OPEN_R=` vide
n'ouvre rien. La capture d'un appel est en **bêta**, pour macOS seulement.

## 5. Choix à arbitrer

Chaque ligne est un choix ouvert, avec une recommandation.

| # | Question | Options | Recommandation |
|---|---|---|---|
| C1 | Version minimale de macOS | 13 (ScreenCaptureKit seul) · 14.4 (process taps) · 15 | **14.4** : capture par application sans pilote virtuel |
| C2 | API de capture du son | process taps de Core Audio · ScreenCaptureKit | **process taps** : son d'une application précise, permission audio seule ; ScreenCaptureKit demande l'autorisation d'enregistrement de l'écran |
| C3 | Pistes | un fichier mono mixé · un fichier à deux canaux (micro, système) · deux fichiers | **deux canaux dans un fichier** : un seul envoi, et l'écho comme la piste « propriétaire » restent exploitables |
| C4 | Écho sans casque | casque recommandé seulement · annulation d'Apple (VPIO) · annulation par Memoglyph, avec la piste système comme référence · mode « micro seul » | **casque recommandé en phase 1, puis annulation par Memoglyph** ; l'annulation d'Apple ne traite que le son joué par l'application elle-même, pas celui de Zoom (à confirmer pendant l'essai) |
| C5 | Détection d'une visio | manuelle seulement · micro en usage (indicateur orange) · application qui utilise le micro (par processus) · calendrier | **phase 1 manuelle ; phase 2 « micro en usage », puis identification de l'application** |
| C6 | Consentement | rappel à chaque démarrage · indicateur seul · message prêt à coller dans la conversation de la visio | **rappel au démarrage et indicateur permanent** |
| C7 | Format d'enregistrement | WAV · FLAC · AAC (M4A) | **WAV écrit par tranches** pendant la capture (robuste au plantage), compressé à l'envoi |
| C8 | Envoi | à la fin · en continu, par tranches | **à la fin**, avec la file d'attente |
| C10 | Techno de l'application | Swift et SwiftUI (`MenuBarExtra`) · Tauri · Electron | **Swift** : accès direct aux API audio, application légère |
| C11 | Emplacement du code | dossier `compagnon/` du dépôt · dépôt séparé | **dossier du dépôt**, versionné avec le reste ; tests Swift en local, car la CI macOS coûte dix fois plus de minutes |
| C12 | Signature | compte gratuit (usage personnel) · Developer ID, 99 $ par an (distribution) | **compte gratuit** tant que l'usage est personnel ; le Developer ID est une dépense à décider pour distribuer |
| C13 | Cible distante | VPS complet (audio envoyé) · hybride : traitement local, résultats synchronisés | **hybride d'abord** : l'audio ne quitte pas le Mac |
| C14 | Piste « propriétaire » | ignorée · indice pour la diarisation (« cette voix est le propriétaire ») | **indice**, après la phase 1 ; son effet reste à évaluer |

## 6. Phasage

| Phase | Contenu | Sortie attendue |
|---|---|---|
| 0. Essai technique, ~1 jour | **outil livré** : [`compagnon/`](../compagnon/README.md), `memoglyph-capture` ; son d'une application (process tap) et micro sur deux canaux, écrits par tranches. Vérifié sous macOS 27 sur `afplay`. **Premier essai sur une vraie visio réussi** (Zoom, avec casque, installé par `make companion`) ; restent à essayer Teams, Meet et l'écho sans casque | C1, C2, C3 et C4 confirmés ou corrigés |
| 1. Version minimale, 3 à 5 jours | barre de menus, modes visio et replay, démarrer et arrêter l'enregistrement, écriture par tranches, destination affichée, état de Memoglyph, envoi au Memoglyph local, lien vers la réunion, indicateur et rappel de consentement | U1 et U2 en manuel |
| 2. Détection, 3 à 5 jours | micro en usage, puis application identifiée (Zoom, Teams, navigateur pour Meet) ; proposition d'enregistrer | U1 proposé automatiquement |
| 3. Robustesse et pilotage local, 3 à 5 jours | longues sessions, veille, changement de casque, reprise ; pilotage de « Ce Mac » ; annulation d'écho par Memoglyph (C4) | usage quotidien |
| 4. Distant | après l'authentification, le cloisonnement par espaces de travail et le HTTPS de Memoglyph ; d'abord l'hybride (C13) ; le compagnon n'a que la destination « Un serveur » à activer | Memoglyph sur un serveur |

## 7. Risques et inconnues

- **Autorisations sous macOS 27** : l'API des process taps est récente et peu
  documentée ; l'essai technique doit le vérifier avant tout.
- **Micros Bluetooth** : le signal « micro en usage » ne les voit pas toujours
  (rapport de développeurs).
- **Meet dans le navigateur** : la détection voit le navigateur, pas l'onglet.
- **Qualité de l'annulation d'écho** quand deux personnes parlent en même temps.
- **Consommation** pendant de longues sessions, et coexistence avec le LLM local.

## 8. Références

Projets open source comparables, à lire avant d'écrire du code. Les licences sont à
vérifier avant toute reprise : Apache-2.0 et MIT sont compatibles, GPL non.

| Projet | Intérêt |
|---|---|
| [insidegui/AudioCap](https://github.com/insidegui/AudioCap) | exemple de référence des process taps (permission, PID de l'application) |
| [makeusabrew/audiotee](https://github.com/makeusabrew/audiotee) | capture du son système en ligne de commande, par process taps |
| [ofcRS/kleoth](https://github.com/ofcRS/kleoth) | compagnon de barre de menus, micro et autres participants, Apache-2.0 |
| [okaris/quill](https://github.com/okaris/quill) | deux pistes séparées (micro, système), process taps |
| [RecapAI/Recap](https://github.com/RecapAI/Recap) | Swift ; détection de réunion annoncée |
| [kurama/mute](https://github.com/kurama/mute) | détection « micro en usage » sans API privée, MIT |
| Hyprnote, devenu [Char](https://char.com/changelog) | deux canaux et annulation d'écho intégrée |
