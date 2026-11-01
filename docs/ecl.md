# Faits de la réunion (ECL)

> **Livré, expérimental et désactivé par défaut** : les **faits de la réunion**, extraits
> d'une réunion à la fois et ancrés sur leur citation horodatée (`core/ecl.py`).
> `MEMOGLYPH_FAITS=1` les active : l'onglet « Faits », ses routes, l'outil MCP
> `lire_faits` et `memoglyph ecl-extraire` ; en local, le modèle
> `ibm-granite/granite-4.2-8b-q4-mlx` (4,9 Go) n'est à télécharger que si on les active.
> Coupés, rien n'est lancé ni chargé, les faits déjà extraits restent sur disque (et
> leur revérification sans modèle à la synchronisation aussi), et reviennent si l'option
> est réactivée. L'extraction est découplée du compte rendu : elle se lance à la demande,
> dans l'onglet « Faits » de la réunion. **Non construit** : la mémoire entre réunions
> (liens entre faits, entités, suivi d'une décision d'une réunion à l'autre). L'ECL est
> construit comme une amélioration du RAG maison, dans le processus, sur SQLite : aucun
> service ni interface à côté de Memoglyph.
>
> Le sigle prête à confusion : chez Cognee, « ECL » désigne un pipeline d'ingestion
> (*Extract, Cognify, Load*). Ici, il désigne la mémoire des réunions qu'un compte peut
> lire.

## Livré : les faits d'une réunion

### Extraction ancrée par réunion

| Élément | Où | Ce qu'il fait |
|---|---|---|
| Extraction dédiée | `core/ecl.py`, `extraire` | une extraction par réunion, par le modèle configuré, tranche par tranche du transcript : décision, action (porteur nommé et locuteur), problème ouvert, chiffre ou échéance ; abstention permise |
| Contrôle déterministe | `controler`, `ancrer` | la citation figure mot pour mot dans une à trois répliques consécutives, la première à 30 s de l'horodatage ; chaque chiffre et nom propre de l'énoncé figure dans la citation ou parmi les libellés des voix citées (comme la garde du CR). Horodatage et locuteur sont ceux de la première réplique citée. Porteur : nommé dans la citation, sinon la voix d'un engagement à la première personne (« I'll », « je vais »), sinon « non nommé ». Un fait refusé est compté par motif, jamais gardé |
| Fichier qui fait foi | `output/.ecl/<document_id>.json` | les faits du document, l'énoncé marqué « reformulé », les refus comptés, le modèle et les appels, l'empreinte des répliques dont ils sont tirés (`empreinte`) ; artefact durable, comme l'audio |
| Table dérivée | `ecl_faits` dans l'index | reconstruite au démarrage et par la réindexation de l'interface depuis les fichiers des documents vivants (`reindexer`) |
| Faits périmés | `reverifier`, appelée par `library.sync_file` et `reindexer` | une empreinte qui diffère de celle du transcript, ou absente (ancien fichier), fait rejouer `controler` sur chaque fait, sans modèle ; un fait qui échoue devient `perime` avec son motif (`motif_peremption`), gardé dans le fichier et dans `versions/`, jamais servi comme actuel (`faits`, MCP) ; l'écran le montre à part. La lecture n'écrit rien et ne relit aucun transcript ; une nouvelle extraction complète remplace le tout |
| Réservation | `resources.heavy` dans `extraire` | prise avant de décharger le moteur du CR ou d'appeler un modèle ; sans effet sous le pipeline et le CR qui la tiennent déjà ; une relance seule attend son tour (900 s) comme une transcription |
| Lecture bornée | `faits(portee)` | jointure sur l'index des documents, avec la portée de `api/portee.py` : ses projets, ses partages actifs, jamais l'ultra-confidentiel en partage |
| Cycle de vie | `core/purge.py` | l'archive retire les lignes et garde le fichier ; la restauration les rend ; la purge efface les deux, et `purge.traces` les compte (`ecl_faits`, `ecl_fichier`) |
| Déclenchement | onglet « Faits » de la réunion (`POST /api/meetings/{id}/ecl`), `./memoglyph ecl-extraire` | à la demande, à part du compte rendu : le CR n'appelle pas l'extraction, et ses rubriques Décisions, Actions et Questions ouvertes viennent de ses propres unités ; une rubrique vide est omise, le CR n'affiche pas de titre sans contenu. Une panne de l'un n'enlève rien à l'autre |
| Extraction partielle | `extraire` | k tranches sur n en échec (0 < k < n) : l'extraction est marquée `partielle` (tranches en échec, tranches). Elle ne remplace pas une extraction courante, qui reste celle qui fait foi : elle va dans l'historique, marquée. Sans extraction courante, elle devient la courante, marquée, jusqu'à une relance complète ; l'onglet le dit et invite à relancer |
| Existant | `./memoglyph ecl-extraire [--appliquer] [--journal <fichier>] [--forcer]` | liste les transcripts citables sans faits ou aux faits périmés (simulation), puis les extrait et affiche faits gardés et refusés ; `--journal` écrit chaque proposition et son sort hors de la bibliothèque ; le serveur du modèle démarré par la commande est arrêté à la fin. Refuse de tourner pendant que l'application tourne (relevé de `tools/sauvegarder.py`), sauf `--forcer` : la réservation ne coordonne pas deux processus |
| Corpus synthétique | `tests/integration/test_ecl.py` | décision modifiée puis annulée, contradiction, deux versions d'une réunion, extraction en panne, partage retiré, ultra-confidentiel ; provenance jusqu'à la source, cycle de vie qui suit la source, et aucun fait inventé gardé |

Les liens entre faits (remplace, confirme, contredit), la non-corroboration entre deux
versions d'une même réunion et la coexistence des états successifs d'une décision ne sont
pas construits.

**Qualité de l'extraction.**
- Le contrôle tolère le décor recopié par le modèle (préfixe de ligne, gras, accent du type) sans rien accepter de plus.
- La précision dépend du modèle et de la consigne : le modèle local par défaut est le moins précis, une consigne à définitions et exemples aide. D'où la mention « à vérifier » sur chaque fait.
- Le modèle de chaque mode est donné ci-dessous.

**Deux modes**, choisis par le fournisseur du compte rendu (`llm.est_local`) :

| | Local | Cloud |
|---|---|---|
| Consigne (`core/ecl_consignes.py`) | « gabarit » de réunion | « définitions » avec exemples |
| Modèle | Granite 4.2 8B réflexion coupée (`ECL_MLX_MODEL`), moteur MLX propre à l'ECL ; absent, l'ECL le signale dans les réserves du CR et retombe sur le modèle du CR | chez Anthropic, Sonnet 5.5 (`ECL_ANTHROPIC_MODEL`), propre à l'ECL ; ailleurs, celui du compte rendu |
| Plafond de sortie (`MAX_TOKENS_PAR_MODE`) | 4 000 jetons | 16 000 jetons |
| Affichage | C : la citation, étendue à la réplique suivante sous `MOTS_MIN_AFFICHAGE` mots (champ `affiche`) ; l'énoncé en second | l'énoncé et sa citation |
| Mention à l'écran | « à vérifier », une mise en garde ; le modèle exécuté est affiché (repli sur le modèle du CR compris) | aucune |

Aucune surface (interface, API, MCP) n'affiche de mesure de précision des faits, quel que soit le modèle.

Le mode et le fournisseur sont notés avec chaque fait (fichier qui fait foi, table dérivée,
`reindexer`), et la route `GET /api/meetings/{id}/ecl` les lit tels quels.

**Moteur Anthropic** (`core/llm_anthropic.py`, fournisseur `anthropic`) : Claude Haiku 5.5 par
défaut, Sonnet 5.5 au choix. L'interface le propose comme les autres fournisseurs ; pour les faits,
il sert Sonnet 5.5 (`ECL_ANTHROPIC_MODEL`), et il est proposé par défaut pour les relancer en cloud. Le mode cloud a un plafond de 16 000 jetons : Haiku réfléchit avant de répondre, et un plafond de
4 000 jetons coupait des tranches de fin de réunion, là où se trouvent les actions. La consigne
« définitions » lui sert telle quelle.

### La transcription et les chiffres

Trois défauts de la transcription et des chiffres, et ce qui en est fait :

- **Boucles de répétition de Whisper.** Whisper peut répéter un même caractère des
  centaines de fois, **sans espace**, en passe unique. Le détecteur par mots (8 mots, 60 %)
  ne le voyait pas.
  - `core/artifacts.py` (politique `artifacts.scrub`, version 2) reconnaît aussi une suite
    de vingt répétitions au moins d'un motif d'une à trois lettres.
  - `core/stt_whisper.py` re-décode **une fois** un segment en boucle sur sa propre fenêtre,
    sans le contexte précédent. S'il boucle encore, la politique le replie avec sa marque et
    le consigne (`repetition_loops`), ce qui l'affiche en réserve dans la réunion.
- **Chiffres ajoutés par une unité du compte rendu.** Une unité non ancrée pouvait
  ajouter un chiffre absent de la parole (note « sur sept » reportée d'une échelle dite
  plus tôt, taille de boutons). Le garde de l'étape 7 ne les voyait pas : il compare le
  rendu aux unités elles-mêmes.
  - `core/experimental/stages.py` écarte maintenant, dès la validation, une unité dont un
    chiffre manque à la parole à ±30 s de son horodatage (`factcheck.chiffres_non_ancres` :
    chiffres et nombres en lettres, « douze cinquante » soutient « 12,50 »).
  - Elle est comptée (`rejected_unsupported_numbers`) et signalée dans la transparence du
    compte rendu, jamais réécrite.
- **« 1250 » pour 12,50 €.** C'est une erreur de la transcription : Whisper écrit « 1250 »
  là où l'orateur dit « twelve fifty ». Aucun contrôle du texte ne peut la voir.
  - Une consigne de formatage des montants (`initial_prompt`) ne corrige pas l'erreur, et
    elle change le reste de la transcription : des phrases déplacées ou perdues dès le début.
  - **Non retenue.** C'est une limite connue des README : un montant à décimales peut
    perdre sa virgule, et seule l'écoute de la citation le corrige.
