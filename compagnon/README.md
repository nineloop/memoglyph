# Compagnon de capture : phase 0

Outil en ligne de commande, macOS seulement, qui enregistre **le son d'une application**
(Zoom, Teams, un navigateur, un lecteur) et **le micro**, dans un WAV à deux canaux. C'est
la phase 0 de [la spécification](../docs/compagnon-capture.md) : un essai technique qui
confirme ou corrige les choix C1 à C4 avant d'écrire l'application de barre de menus.
Il n'envoie rien à Memoglyph : on dépose le fichier dans l'interface, comme un autre média.

## Construire et tester

Xcode (ou ses outils en ligne de commande) suffit : aucune dépendance externe, rien à
télécharger.

```bash
cd compagnon
swift build -c release        # binaire : .build/release/memoglyph-capture
swift test                    # tests de la logique pure (WAV, canaux, arguments, alertes)
```

Les tests Swift tournent en local seulement : la CI ne les lance pas, les minutes macOS
coûtant dix fois plus (C11).

## Utilisation

```bash
memoglyph-capture applications            # processus qui jouent du son en ce moment
memoglyph-capture applications --tous     # aussi ceux qui ont ouvert l'audio
memoglyph-capture enregistrer --app us.zoom.xos --sortie visio.wav
memoglyph-capture enregistrer --app 4242 --sans-micro --duree 60 --sortie replay.wav
memoglyph-capture reparer visio.wav       # après un arrêt brutal
```

- `--app` prend un **identifiant de paquet** (`us.zoom.xos`, `com.microsoft.teams2`), qui
  couvre aussi les processus auxiliaires de l'application, un **nom d'exécutable**
  (`afplay`) ou un **PID**.
- Le fichier : PCM 16 bits, deux canaux à la fréquence de l'entrée (souvent 48 kHz),
  **micro à gauche, son de l'application à droite** (C3). Avec `--sans-micro` (replay,
  webinar), la gauche reste silencieuse.
- **Écrit par tranches d'une seconde** (C7) : un arrêt brutal ne perd au plus que la
  dernière seconde, et `reparer` recale l'en-tête sur ce qui est sur le disque.
- **Ctrl-C** arrête proprement ; `--duree` arrête seul.
- **Jamais par-dessus un fichier existant** : la création refuse un chemin qui existe
  (`O_EXCL`, code 6), sans rien écraser.
- **Une erreur d'écriture** (disque plein, clé retirée) arrête la capture aussitôt, code 6 ;
  ce qui est écrit jusque-là se répare avec `reparer`.
- Rien ne démarre sans vous, et l'outil rappelle de prévenir les participants d'une visio
  (C6).

| Code de sortie | Sens |
|---|---|
| 0 | enregistrement réussi |
| 1 | arguments invalides |
| 2 | aucun processus audio ne correspond à `--app` |
| 3 | micro refusé, ou demande d'autorisation sans réponse |
| 4 | **aucun son reçu de l'application** : elle n'a rien joué, ou macOS n'autorise pas la capture |
| 5 | le micro n'a rien envoyé |
| 6 | erreur de Core Audio |

Un fichier muet n'est jamais présenté comme réussi : macOS n'offre pas d'API publique pour
savoir si la capture du son d'une application est autorisée, et un tap non autorisé rend
du silence. C'est donc le bilan de l'enregistrement qui le révèle (code 4).

## Comment ça marche

- **Process tap** de Core Audio (C2, macOS 14.4 et plus) : le mélange stéréo des processus
  visés, sans pilote ni périphérique virtuel, et sans l'autorisation d'enregistrer l'écran.
  L'utilisateur continue d'entendre la visio.
- Le tap et le micro par défaut entrent dans un **appareil agrégé privé**, créé pour la
  durée de l'enregistrement puis détruit : une seule horloge, donc les deux canaux restent
  alignés.
- Les raisons d'usage (micro, son du système) sont dans un `Info.plist` embarqué dans le
  binaire. macOS attribue les autorisations à l'application qui lance l'outil (le
  Terminal, par exemple).

## Application de barre de menus

```bash
make companion                            # depuis la racine du dépôt
```

`make companion` compile un paquet `Memoglyph compagnon.app` (avec
`Sources/memoglyph-compagnon/Info.plist`), le signe, le copie dans `~/Applications`, puis
affiche la marche à suivre pour les autorisations. Deux variables :

- `DEST` : le dossier de destination (par défaut `~/Applications`) ;
- `SIGN_IDENTITY` : une identité de signature du trousseau. Sans elle, la signature est
  **ad hoc** (`codesign -s -`) : macOS rattache les autorisations à l'empreinte du paquet,
  qui change à chaque recompilation, et peut donc **redemander le micro et le son du
  système** après une mise à jour. Avec une identité stable, elles restent.

Au premier démarrage en Visio, macOS demande le **micro** (Réglages Système >
Confidentialité et sécurité > Microphone) et le **son des applications** (même panneau,
« Enregistrement de l'écran et de l'audio du système », partie audio du système). Le
paquet porte son propre nom dans ces réglages ; lancé hors du paquet
(`.build/release/memoglyph-compagnon`), le compagnon les emprunte à l'application qui le
lance, le Terminal par exemple.

- **Démarrer en Visio** (micro et son de l'application), **en bêta, macOS seulement** :
  - en tête, **« Appel en cours : … »** pour chaque application qui, auxiliaires compris,
    joue du son ET prend le micro (les deux peuvent venir de deux processus différents,
    comme Teams) ; un clic démarre sur elle. Sans appel, une ligne grisée invite à lancer
    l'appel puis à rouvrir le menu ;
  - puis une ligne par application ouverte qui a l'audio ouvert, par son nom, celles qui
    jouent en tête (●). La ligne vise l'application et tous ses processus auxiliaires
    (`com.microsoft.teams2` couvre `com.microsoft.teams2.modulehost`) ;
  - **« Safari (son des pages web) »** vise `com.apple.WebKit.GPU`, d'où sort le son des
    pages de Safari : elle capte **toutes les pages WebKit**, y compris celles d'autres
    applications qui affichent du web avec WebKit ;
  - les démons et processus sans application sont sous **« Autres processus… »** ;
  - **« Tout le son du Mac »** capte tout ce qui joue, sauf le compagnon lui-même.

  Le son d'un navigateur comprend **tous ses onglets** : une vidéo qui joue dans un autre
  onglet pendant l'appel serait enregistrée avec lui. Une confirmation rappelle de
  prévenir les participants et de mettre un casque.
- **Démarrer en Replay** (son de l'application seul) : voir plus bas.
- Pas d'icône dans le Dock (`LSUIElement`, politique `.accessory`).
- Pendant l'enregistrement seulement, une **pastille flottante** : temps écoulé, niveaux
  du micro et de la visio, dernière alerte, bouton **Stop** (⌘.). Elle est exclue du
  partage d'écran (`sharingType = .none`).
- **Alertes en direct** : micro à zéro exact depuis 3 s, micro par défaut changé, micro qui
  sature (au-dessus de 0,99 ; l'écriture écrête proprement), écho (l'enveloppe du micro suit
  celle de la visio pendant 5 s). Seuils dans `CaptureCore/Surveillance.swift`.
- **Replay** : le menu propose les processus qui jouent en ce moment, **un par PID**, avec
  l'application parente quand elle se déduit. Un service partagé comme
  `com.apple.WebKit.GPU` (le son de Safari et d'autres applications) n'est jamais pris en
  entier. Si la page est rechargée, le processus change : la pastille le signale et le menu
  propose « Reprendre sur… » le nouveau.
- Les captures vont dans `~/Documents/Memoglyph/Captures/` (`visio-AAAA-MM-JJ-HHhMMSS.wav`) ;
  à l'arrêt, le même bilan que la commande : un fichier muet n'est jamais « réussi », ni
  envoyé.

### Envoi à Memoglyph

- **Relier** : dans Memoglyph, « Appareils », créez un jeton (`mgj_…`), puis menu du
  compagnon > « Relier à Memoglyph… » : l'adresse de l'interface (par défaut
  `http://127.0.0.1:8770`, qui relaie `/api`) et le jeton. Le jeton est vérifié sur la
  liste des projets, puis rangé **dans le trousseau de macOS seulement** (service
  `org.memoglyph.compagnon`) : ni fichier, ni journal, ni message.
- **Envoyer** : à l'arrêt d'une capture réussie, elle part vers `POST /api/transcribe`
  avec sa provenance (application, `visio` ou `replay`). Le menu suit le traitement, puis
  propose d'ouvrir la réunion elle-même dans l'interface, où l'on se connecte. Le travail
  rend au jeton l'identité de la réunion (`meeting_id`, projet), jamais son texte.
- **Projet** : « Projet d'envoi » propose ceux du compte ; sans choix, le projet par défaut
  du compte. Le compagnon ne crée jamais de projet.
- **Memoglyph arrêté ou jeton refusé** : la capture reste dans le dossier, listée dans
  `.envoi-en-attente.json`, et le menu propose « Renvoyer ». Le compagnon ne démarre pas
  Memoglyph.
- La destination est affichée en tête du menu et dans chaque confirmation.

### Casque Bluetooth

En mode appel, un casque Bluetooth fait passer l'entrée à 24 000 Hz : le fichier est écrit
à cette fréquence, sans conséquence pour la transcription.
- Sans le paquet `.app` de `make companion`, macOS attribue les autorisations à
  l'application qui lance le compagnon (le Terminal), comme pour la commande.

| Module | Rôle |
|---|---|
| `Sources/CaptureCore` | logique pure, testée : `EcrivainWAV`, `Melange`, `Arguments`, choix des processus, `Surveillant` (alertes), `EtatPastille`, `Rappels`, `Colonnes`, `ClientMemoglyph` (requêtes et réponses), `FileRenvoi`, `ChoixReplay`, `ChoixVisio` |
| `Sources/CaptureAudio` | Core Audio : liste des processus, tap, agrégat, autorisation du micro, niveaux en direct |
| `Sources/memoglyph-capture` | la commande |
| `Sources/memoglyph-compagnon` | l'application de barre de menus (AppKit), sa pastille, le trousseau et les appels réseau |
