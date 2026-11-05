# 📝 Memoglyph — Spécification

Application **locale par défaut** (Apple Silicon, MacBook Pro M3 Pro 18 Go) qui
transforme des enregistrements audio/vidéo en un **corpus interrogeable** :
transcription, diarisation, profils, recherche, réponses citées, comptes-rendus
et identification des voix.

**Ce qui sort de la machine, et quand.** En mode local — le défaut — rien ne
sort : Whisper MLX, pyannote, le LLM MLX et la vectorisation tournent sur l'appareil.
Des **fournisseurs cloud optionnels et explicites** existent :

- **Mistral** — transcription (Voxtral) *et* comptes-rendus/profilage/RAG (Chat) ;
  choisi dans l'interface, il envoie **l'audio** à l'API de transcription et **le
  texte** à l'API de chat.
- **OpenAI** (`gpt-5.6-luna`, ajouté 08/2026) — comptes-rendus/profilage/RAG
  seulement (pas de STT) ; envoie **le texte** à l'API Responses.

Aucun n'est actif sans action de l'utilisateur, et l'écran de transcription
l'annonce sur la tuile correspondante. Le choix se fait **par capacité**
(local vs distant), pas par un nom de fournisseur codé en dur (§ Fournisseurs).

La vectorisation reste **toujours locale**, dans tous les modes : la recherche
par le sens et le RAG n'envoient rien nulle part — seule la génération de la
réponse RAG passe par le fournisseur choisi.

L'historique des décisions est tenu dans un journal interne, non publié.

---

## 1. Objectifs & non-objectifs

**Objectifs**
- Entrée : audio (`mp3, m4a, wav, flac, ogg…`) ou vidéo (`mp4, mov, mkv, webm…`).
- Transcription locale via **Whisper MLX** — langue détectée automatiquement, ou
  forçable (le jeton de langue conditionne toute la passe de décodage).
- Sortie : un `.md` lisible — front-matter YAML + corps horodaté.
- **Diarisation** optionnelle (« qui parle ») et **profils LLM** optionnels.
- Exploiter le corpus : recherche plein-texte **et** sémantique, réponses
  **citées** (RAG), comptes-rendus, **signature vocale** entre fichiers.
- Traitement **batch** : on privilégie la précision et la robustesse sur longs
  fichiers plutôt que la latence.

**Non-objectifs**
- Pas de transcription en temps réel. La capture d'un appel passe par le compagnon, en bêta.
- Pas de dépendance OBLIGATOIRE à un service distant : tout le produit doit
  rester utilisable hors ligne. Le mode cloud Mistral est un choix explicite de
  l'utilisateur, jamais un prérequis — et aucune fonction ne doit devenir
  inaccessible sans lui.
- Pas de traduction du corpus.

---

## 2. Principes directeurs

Ce sont les invariants ; tout le reste en découle.

1. **Le `.md` est la source de vérité.** SQLite, vecteurs et index sont
   **dérivés et reconstructibles** en rescannant `output/`.
2. **Le nom de fichier est la clé.** Unique tous projets confondus (corbeille
   comprise) → les URLs et l'API ne dépendent pas de l'emplacement.
3. **Tout écrit reste sous `output/`.** Les noms de projet sont validés *et*
   vérifiés par résolution.
4. **Une inférence LLM n'est jamais une donnée d'autorité.** Elle propose ; le
   code vérifie ; l'utilisateur valide.
5. **Rien ne disparaît en silence.** Une suppression partielle est signalée, pas
   maquillée en succès.

---

## 3. Architecture

```text
        ┌──────────────┐        ┌──────────────────────────────┐
        │  ui/ React   │───────▶│  api/ FastAPI (jobs + SSE)   │
        │ (Vite + TS)  │◀───────│  aucune logique métier       │
        └──────────────┘        └───────────────┬──────────────┘
                                                │
                            ┌───────────────────▼───────────────────┐
                            │            core/  (moteur pur)        │
                            │  media → stt → diarisation → merge    │
                            │       → profiling → writer_md         │
                            └───────────────────┬───────────────────┘
                                                │
   ┌────────────────────────────────────────────▼──────────────────────────────┐
   │  output/                      SOURCE DE VÉRITÉ                            │
   │   ├── <projet>/*.md           transcripts (front-matter + corps)          │
   │   ├── .audio/                 écoute + faithful/ FLAC + originals/         │
   │   ├── .voiceprints.json       empreintes vocales (base64, 256 d)          │
   │   └── .trash/                 archivés + manifest.json (identité)         │
   ├───────────────────────────────────────────────────────────────────────────┤
   │  index.db                     INDEX DÉRIVÉ (reconstructible)              │
   │   transcripts · transcripts_fts · chunks + vec_chunks · voices/members    │
   └───────────────────────────────────────────────────────────────────────────┘
```

`core/` ne connaît ni React ni FastAPI : il reste réutilisable par l'API, un CLI
ou un futur agent.

---

## 4. Structure du projet

```text
memoglyph/
├── core/                  # moteur pur (aucune dépendance UI/HTTP)
│   ├── config.py          # constantes + variables d'environnement
│   ├── media.py           # ffmpeg : WAV 16k (travail) + archive audio (durable)
│   ├── stt_base.py        # types partagés (Segment, Transcript, callbacks)
│   ├── stt.py             # fabrique de moteurs + cache
│   ├── stt_whisper.py     # Whisper MLX (+ transcription progressive)
│   ├── stt_mistral.py     # Voxtral Mini (API) : diarisation native + compression d'envoi
│   ├── diarization.py     # pyannote : tours de parole + empreintes
│   ├── merge.py           # fusion STT ↔ diarisation (« Speaker N »)
│   ├── llm.py             # façade fournisseurs LLM (registre MOTEURS, par capacité)
│   ├── llm_base.py        # contrat LLMEngine (Protocol) + erreurs neutres
│   ├── llm_mlx.py         # moteur LOCAL (défaut) : supervise mlx_lm.server (Qwen3-4B)
│   ├── llm_mistral.py     # moteur cloud Mistral (chat + embeddings)
│   ├── llm_openai.py      # moteur cloud OpenAI (Responses API + Structured Outputs)
│   ├── embed.py           # vectorisation LOCALE du texte (recherche par le sens)
│   ├── profiling.py       # stats de parole + analyse LLM (rôles, résumé)
│   ├── writer_md.py       # génération du .md + unicité des noms
│   ├── pipeline.py        # orchestrateur bout en bout
│   ├── docid.py           # identités ULID (document, réunion, projet)
│   ├── projects.py        # identité persistante des projets (manifeste)
│   ├── sqlite_db.py       # chemin canonique de l'index + connexion (WAL)
│   ├── library.py         # index SQLite + FTS5 + projets + corbeille
│   ├── semantic.py        # chunking + embeddings + KNN (sqlite-vec)
│   ├── rag.py             # question → passages → réponse citée
│   ├── report.py          # seam generate_report (→ experimental/) + make_report hérité
│   ├── experimental/      # PIPELINE RICHE DE PRODUCTION (malgré le nom) — voir §12
│   │   ├── app_report.py  #   make_local_report : chemin CR de l'app (tous fournisseurs)
│   │   ├── pipeline.py    #   run_stages : les 8 étapes ; make_experimental_report (CLI, drapeau)
│   │   ├── stages.py      #   strates MAIN/SECONDARY/CLOSING/NOISE + veto non destructif
│   │   ├── profiles.py    #   profil d'exécution résolu PAR CAPACITÉ (est_local)
│   │   ├── timestamps.py  #   normalisation d'horodatage commune (canonicalize/resolve)
│   │   ├── guard.py       #   garde de contenance (nombres/entités) déterministe
│   │   └── prompts.py / chunking.py  # invites+schémas ; découpage borné en tokens
│   ├── sensitivity.py     # étiquette de sensibilité (3 niveaux, MÉTADONNÉE SEULE)
│   ├── report_citations.py # extraction et validation des citations du CR
│   ├── provenance.py      # traçabilité des éléments produits
│   ├── artifacts.py       # artefacts dérivés attachés à un document
│   ├── names.py           # garde-fous sur les noms déduits par le LLM (pur)
│   ├── voiceprint.py      # registre de voix + clustering + annexe
│   ├── backfill.py        # empreintes des transcripts antérieurs
│   ├── backfill_ids.py    # attribution d'ULID aux .md antérieurs (bibliothèque)
│   ├── backfill_identity.py # meeting_id / project_id des .md antérieurs
│   ├── faithful_backfill.py # FLAC modèles depuis les originaux subsistants
│   ├── dedup.py           # détection de doublons + recommandation
│   ├── maintenance.py     # état des index dérivés et retraitements
│   ├── purge.py           # archivage réversible / suppression définitive
│   ├── resources.py       # jeton de travail LOURD (RAM unifiée partagée)
│   ├── quality.py         # stabilité de la langue (dérive en traduction)
│   ├── factcheck.py       # nombres du CR vérifiés contre le transcript
│   └── templates.py       # gabarits de CR par genre, en questions (pur)
├── api/                   # FastAPI : frontière HTTP
│   ├── main.py            # endpoints + validation + traduction des erreurs
│   └── jobs.py            # jobs bornés (concurrence, éviction, SSE)
├── ui/                    # React (Vite + TS)
├── tests/                 # unit/ + integration/
└── output/                # corpus (gitignoré)
```

---

## 5. Chaîne de traitement

```text
média → media.to_wav16k (WAV 16k mono, transitoire)
      → stt (Whisper en UNE passe — le découpage progressif dégradait tout)
      → diarisation pyannote (audio ENTIER : cohérence globale des locuteurs)
      → merge (SPEAKER_00 → « Speaker 1 », ordre d'apparition DANS LE TEXTE
               produit, pas dans les tours de diarisation)
      → profiling LLM (optionnel : type, résumé, rôle/ton/points clés)
      → writer_md → output/<projet>/<nom>.md
      → indexation : library.sync_file + semantic + voiceprint
      → artefacts durables : archive audio + empreintes
```

Progression réelle (`core/pipeline.py`) : préparation 1→10 %, transcription
10→50 %, diarisation 50→65 %, profilage 65→75 %, écriture du Markdown 76 %,
compte-rendu automatique 80→98 %, finalisation 100 %. L'interface reprend ces
mêmes bornes (`ui/src/components/TranscribePanel.tsx`, `timelineItems`).

---

## 5bis. Fournisseurs LLM et STT — moteurs interchangeables

Les moteurs sont sélectionnés **par capacité**, jamais par une comparaison de nom
de fournisseur dans le tronc. Deux registres parallèles :

**LLM** (`core/llm.py`, façade `MOTEURS`). Type fermé
`Fournisseur = Literal["mistral", "cloud", "mlx", "openai"]`, défaut
`FOURNISSEUR_DEFAUT = "mlx"`, alias `cloud → mistral`. Un fournisseur inconnu lève
`FournisseurInconnu` — jamais de repli silencieux.

| Fournisseur | Fichier | Local ? | Modèle par défaut | Notes |
|---|---|---|---|---|
| `mlx` (**défaut**) | `llm_mlx.py` | **oui** (`est_local`) | `mlx-community/Qwen3-4B-Instruct-2507-4bit` | supervise `mlx_lm.server` sur `127.0.0.1:8772` |
| `mistral` (`cloud`) | `llm_mistral.py` | non | `ministral-14b-2512` | Chat + embeddings `mistral-embed` |
| `openai` | `llm_openai.py` | non | `gpt-5.6-luna` | Responses API, Structured Outputs |
| ~~`ollama`~~ | — | — | — | **retiré du produit** : hors `MOTEURS` et hors type `Fournisseur` |

**STT** (`core/stt.py`, registre `_REGISTRY`). Défaut `whisper`, alias
`voxtral → mistral`. Le moteur est choisi **par requête** (champ `engine`), pas par
une variable d'environnement.

| Moteur | Fichier | Local ? | Modèle par défaut |
|---|---|---|---|
| `whisper` (**défaut**) | `stt_whisper.py` | **oui** (MLX) | `mlx-community/whisper-large-v3-turbo` |
| `mistral` (`voxtral`) | `stt_mistral.py` | non | `voxtral-mini-2602` (diarisation native) |

### Contrat de moteur — `core/llm_base.py`

`LLMEngine` (Protocol, surface volontairement minimale) : `name`,
`is_available(*, api_key)` (ne lève jamais), `pret_declare()` (disponibilité
déclarative, sans démarrer de serveur), `modele_servi(model)` (le modèle
**réellement servi** — source unique pour la provenance), `chat(...)`,
`chat_json(..., max_tokens, schema, retries)`. Capacité optionnelle
`LLMEngineDechargeable.unload()` — implémentée par `mlx` (et `ollama`), pas par les
moteurs cloud.

**Frontière de secret.** Toute exception d'un moteur est normalisée en `LLMError`
ne publiant que `type(exc).__name__` — jamais le message d'origine (qui pourrait
contenir une URL, une clé ou un chemin). Un échec cloud rend HTTP 503.

### Sélection dans l'UI (par capacité, pas en dur)

`GET /api/config` expose `llm_providers`, `local_providers`
(= fournisseurs où `est_local`), `default_llm_provider`, et la disponibilité
déclarée par fournisseur. L'UI persiste **seulement** la chaîne du fournisseur
(`localStorage: memoglyph.llmProvider`) ; aucune clé n'est stockée côté client
(elles restent dans `.env`, côté serveur). Choisir un fournisseur cloud couple le
STT : Mistral amène Voxtral, les autres cloud (OpenAI) retombent sur Whisper local.

Les **embeddings restent toujours locaux** (`MEMOGLYPH_EMBED_BACKEND=local`,
`intfloat/multilingual-e5-large`), y compris en mode cloud.

**Cloud privé.** Un serveur de modèle hébergé par l'utilisateur (MLX, ou le fournisseur
`compatible`, « Serveur compatible OpenAI » : Ollama, LM Studio, vLLM, réglé par `.env`)
n'est local que si son hôte est sur la boucle locale (`llm.est_local`). Ailleurs, il
s'affiche « Cloud privé » et suit la règle d'envoi du cloud, ultra-confidentiel compris.

---

## 5ter. Langue de l'interface (FR/EN)

L'UI est **bilingue français/anglais**. Le mécanisme est un
petit catalogue maison, **sans bibliothèque i18n** : `ui/src/lib/messages.ts`
(catalogues typés) + `ui/src/lib/i18n.ts` (résolution + interpolation `{param}`) +
`ui/src/components/LangProvider.tsx` (contexte). La langue est détectée depuis le
navigateur puis surchargée par un choix utilisateur persisté
(`localStorage: memoglyph.lang`, même convention que `llmProvider`). Règle de
conception : ce qui **décrit** un état reste dérivé en TypeScript ; le catalogue ne
porte jamais une phrase par combinaison d'état — la partie variable passe par des
paramètres. Toutes les surfaces UI sont traduisibles. Les messages d'erreur du
backend restent en français (hors périmètre de cette couche).

---

## 6. Format du `.md`

```markdown
---
title: reunion-equipe
date: 2026-07-19
duration: "00:42:15"
language: fr
engine: whisper
model: mlx-community/whisper-large-v3-turbo
source: Réunion équipe.mp4
diarization: true
n_speakers: 3
profiled: true
generated_at: "2026-07-19T10:15:00"
pipeline:
  schema_version: 1
  kind: transcript
  pipeline_version: 2
  application_version: 0.6.0
  code:
    revision: 0123456789abcdef0123456789abcdef01234567
    dirty: false
    implementation_sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
  stt:
    engine: whisper
    model: mlx-community/whisper-large-v3-turbo
    language_requested: auto
    chunk_seconds: 0.0
  audio:
    normalization_version: 1
    sample_rate_hz: 16000
    channels: 1
    sample_format: s16
    input_pcm_sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
  lineage:
    derived_from_document_id: null
  fingerprint: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
speakers:
  Speaker 1: {role: "animateur", talk_time: "12:30", share: "30%"}
---

# reunion-equipe

## 🗣️ Profil de la conversation
…

## Transcript

**Speaker 1** [00:00:04] Bonjour à tous, on démarre.
```

À noter :
- Le bloc `pipeline` est écrit à la génération et fait partie de la source de
  vérité. Il consigne les paramètres effectifs, les versions des transformations
  déterministes et un hash du code pertinent. Celui d'un compte-rendu référence
  en plus le fingerprint de son transcript source, le gabarit et le hash des
  questions réellement posées.
- `audio.input_pcm_sha256` identifie le flux PCM 16 kHz mono effectivement
  consommé par les modèles. Une régénération additive porte en plus
  `derived_from_document_id` à la racine et dans le fingerprint : l'ancien et le
  nouveau document gardent chacun leur ULID.
- Le fingerprint identifie un contexte de génération ; il ne rend pas
  déterministe un modèle qui ne l'est pas. Les documents antérieurs sans bloc ne
  sont pas backfillés par déduction et portent implicitement la provenance
  `legacy_unknown`.
- Aucun token, hôte de service ou chemin local n'entre dans le bloc. Les
  constantes de version de `core/provenance.py` sont incrémentées lorsqu'un
  changement sémantique n'est pas déjà visible dans les paramètres consignés.
- Les **empreintes vocales ne sont PAS dans le `.md`** (≈1,4 ko de base64 par
  locuteur le rendrait illisible) : elles vivent dans `output/.voiceprints.json`.
- Les libellés `Speaker N` sont **locaux au fichier** ; le lien entre fichiers est
  établi par le registre de voix, pas par ces libellés.
- `report_error` n'existe que si le compte-rendu demandé pendant la transcription a
  échoué : sa cause, affichée dans l'onglet Information, levée par le prochain
  compte-rendu réussi. Un compte-rendu cite `[source HH:MM:SS]` : un transcript
  sans horodatage est refusé avant tout appel au modèle (400), avec ou sans
  diarisation. Dans le compte-rendu, `units_not_rendered` compte les unités du
  corps qu'aucune phrase rendue ne cite, listées (horodatage et type) sous
  « Unités non rendues ».
- `capture` n'existe que pour un enregistrement du compagnon de capture
  (`docs/compagnon-capture.md`) : `{application: zoom.us, mode: visio}`, mode
  `visio` ou `replay`. Il vient des champs `capture_application` et
  `capture_mode` de `POST /api/transcribe`, refusés en 400 s'ils sont invalides.

---

## 7. Magasins et cycle de vie

| Magasin | Contenu | Reconstructible ? |
|---|---|---|
| `output/<projet>/*.md` | transcripts | **source de vérité** |
| `output/.audio/` | audio compressé (~24 Mo/h) + `originals/` | non (re-créable si la source existe) |
| `output/.voiceprints.json` | empreintes 256 d | oui, en re-diarisant |
| `output/.voices.json` | **décisions HUMAINES sur les voix** : noms, fusions | **NON — autoritatif** |
| `output/.verite-vocale.json` | décisions d'une écoute à l'aveugle des voix | **NON — décisions humaines** |
| `output/.trash/` + `manifest.json` | archivés + leur identité | — |
| `index.db` | index, FTS, chunks+vecteurs, voix | **oui**, en rescannant `output/` |
| `auth.db` | comptes, sessions, partages, jetons d'appareil (empreintes seulement) | **NON — autoritatif** |

**Deux opérations de suppression**, centralisées dans `core/purge.py` :
- `archive()` — **réversible** : le `.md` part en corbeille, les index *dérivés*
  sont purgés, mais audio et empreintes sont **conservés** (sinon « récupérable »
  serait faux). `restore()` le remet dans son projet d'origine et signale toute
  reconstruction d'index incomplète.
- `purge_transcript()` — **définitif** : les 8 traces disparaissent. `traces()` et
  `is_clean()` permettent de le **vérifier**, pas de l'espérer.

La Corbeille expose en plus une purge exacte par nom archivé. Les routes de
lecture, restauration, purge individuelle et vidage sont toutes filtrées par le
`view_scope` explicite. Un échec partiel conserve le Markdown et son entrée de
manifeste afin que l'élément reste visible et reprenable. Si l'écriture du
manifeste échoue après l'effacement du fichier, le Markdown est remis en place.
Les artefacts durables ne sont retirés que si aucune autre copie active ou
archivée ne partage encore l'identité.

L'archivage d'un transcript est précédé d'un plan déterministe : les
comptes-rendus dont `source_document_id` désigne son ULID sont listés puis
archivés avant leur source. Le repli par `source_transcript` n'est utilisé que
pour les anciens CR sans identifiant stable. La cascade ne s'étend jamais aux
autres versions de la même réunion.

### Portée projet : une frontière d'accès, pas un filtre d'affichage

Le `project_id` **borne l'accès**. Toute route adressant un document par
identifiant l'applique — par ordre de gravité : destruction (`DELETE
/api/transcript/{name}`, `DELETE /api/meetings/{id}`), génération de CR
(`POST /api/transcript/{name}/report`), lecture (`GET /api/transcript/{name}`,
`/download`, `/audio`, `GET /api/meetings/{id}`), déplacement (les deux
`PATCH …/project`), énumération (`/archive-plan`).

Trois règles, tenues par l'unique helper `core.library.visible_in_project` :

1. **`view_scope` est OBLIGATOIRE** sur ces routes — aucune valeur par défaut.
   Une portée vide désigne la boîte de réception, pas « tout » : un défaut
   ferait de chaque appel étourdi un 404 muet sur les documents classés. Un
   paramètre requis rend **422**, ce qui se voit. Panne bruyante plutôt que
   rétrécissement silencieux.
2. **Le dossier fait foi, pas le front-matter.** Le `project_id` du `.md` est
   déclaratif : il voyage avec un fichier recopié et le rendrait visible depuis
   son projet d'origine. Le manifeste du dossier, lui, ne ment pas. Le garde
   résout d'abord le chemin par les **quatre** clés de `transcript_path` (nom,
   document_id, source, titre) — n'en filtrer qu'une laisserait trois portes
   ouvertes.
3. **Fermeture par défaut.** Un document dont la portée n'est pas résoluble
   n'est visible depuis aucune portée nommée. Seule `view_scope=all` élargit,
   et c'est un geste **explicite** — les liens partagés `?projet=all`
   continuent donc de fonctionner.

Le refus est un **404**, jamais un 403 : un 403 confirmerait au demandeur que le
document existe ailleurs, ce qui est déjà une fuite. Une réunion n'est dans la
portée que si **tous** ses documents y sont — ces routes agissent sur l'ensemble.

### Session ou jeton d'appareil

Toute route `/api` exige une session (cookie), sauf les routes publiques de
connexion et `/api/health` (`ROUTES_PUBLIQUES`). Un **jeton d'appareil**,
créé par le compte dans « Appareils » (`GET`, `POST /api/jetons`,
`DELETE /api/jetons/{jeton_id}`), remplace la session pour le compagnon de
capture, par `Authorization: Bearer`, sur les seules `ROUTES_DU_COMPAGNON` :
transcription, état et événements d'un travail, liste des projets du compte.
Ailleurs, un jeton seul reçoit 401. Il porte la portée de son compte, comme une
session ; le test d'étanchéité l'éprouve sur chaque route.

---

## 8. Signature vocale

> **L'identité vocale est commune aux projets d'un compte, jamais partagée entre
> comptes.** Une voix nommée dans un projet est reconnue et nommée dans
> les autres projets du même compte : reconnaître la même personne d'une réunion
> à l'autre suppose un espace de voix commun, et cloisonner le registre par
> projet reviendrait à réapprendre chaque interlocuteur dans chaque projet.
> Entre comptes, la frontière tient : une voix appartient au propriétaire des
> projets de ses documents, une empreinte ne rejoint qu'une voix de ce
> propriétaire, et le nom d'une voix qui déborderait d'un compte (voix mêlée
> d'avant le cloisonnement, scindée à la réindexation) ne lui est jamais rendu.
> Les routes `/api/voices/*` passent par la portée du compte.

Chaque diarisation produit un vecteur de **256 dimensions par locuteur**. Le
registre rattache un locuteur local à une **voix du même compte** par cosinus
(clustering online incrémental, ids et libellés stables).

Seuil **0.5** : les voix différentes et la même personne entre fichiers en restent
loin, avec de la marge des deux côtés.

### Ce qu'un nom peut désigner (et ce qu'il ne peut pas)

Un rôle nomme l'**auteur** des mots ; une empreinte nomme la **voix** qui les
prononce. Le modèle de données confond les deux, et c'est correct tant qu'ils
coïncident — ce qui cesse d'être vrai avec le doublage ou la traduction
simultanée. Un plateau français recevant des invités américains en voix off
produit une diarisation des **interprètes** : nommer un de ces locuteurs
« Hugo Lambert » ferait apprendre au registre la voix de l'interprète, qui
rattacherait ensuite à Hugo Lambert tout autre fichier doublé par la même personne.
Le registre étant le seul magasin qui traverse les fichiers, l'erreur serait
**globale et silencieuse**.

Le nom déduit du profil LLM n'est donc **jamais** appliqué automatiquement : il
est proposé, l'utilisateur valide.

### Garde-fous

Ils vivent dans `core/names.py`, **source unique** de la règle, et s'appliquent
**avant l'écriture du `.md`** — pas seulement à l'étape suggestion, sinon un nom
douteux reste affiché dans le transcript alors même que le registre l'a rejeté.
Deux signaux structurels, les seuls vérifiables en code :

- un même nom porté par **plusieurs locuteurs** → devinette manifeste ;
- **plus de 8 locuteurs** → sur-segmentation, ou plateau à interprètes, ou
  montage d'extraits : dans tous ces cas voix et auteur ont cessé de coïncider.

Le rôle générique (« invité ») est conservé : il est vérifiable, le nom ne l'est pas.

### Choix du modèle

Le choix se fait en **essais répétés** : à `temperature=0.2` sans seed le modèle
n'est pas déterministe, et un passage unique peut classer « propre » un modèle qui
échoue en le répétant. Critère retenu — conserver un nom **légitime**, sur un
fichier où un locuteur se présente réellement. Un modèle qui ne nomme jamais
personne est écarté : son absence d'hallucination n'est pas de la rigueur mais de
l'abstention.

---

## 9. Stabilité de la langue (indicateur de qualité)

Whisper bascule parfois en **traduction** en cours de fichier sans le signaler :
« description fidèle du poète » ressort en `a loyal description of the poet`. Le
transcript est alors silencieusement faux — et rien dans le `.md` ne le trahit.

`core/quality.py` mesure la part de segments dont la langue s'écarte de celle
déclarée : quasi nulle sur un transcript sain, nettement plus haute sur un plateau
à interprètes. Le calcul est fait **à la volée** sur le `.md` (donc valable
rétroactivement, sans re-transcrire) et exposé dans le lecteur.

Deux partis pris :

- C'est un **avertissement, pas un classifieur** : le seuil est un ordre de
  grandeur, il ne décide de rien automatiquement.
- Une paire de langues non couverte (seul `fr`/`en` l'est) renvoie **`None` — « non
  vérifié » — et jamais `0.0`**. Annoncer « aucun mélange » sur une paire qu'on
  n'a pas su examiner serait une garantie inventée, ce que l'interface
  distingue explicitement de « vérifié et propre ».

## 10. Compte-rendu : des questions, pas des rubriques

> **Où vit le CR aujourd'hui.** Le seam unique de l'application est
> `report.generate_report(name, provider=…)`, qui délègue à
> `core.experimental.app_report.make_local_report` — le **pipeline riche par
> strates** décrit en §12bis — pour **tous** les fournisseurs (mlx/mistral/openai).
> L'ancien `report.make_report` reste comme repli/référence. Les principes §10–§12
> ci-dessous (questions plutôt que rubriques, intégrité des citations, contrôle des
> chiffres) restent les invariants produit ; le pipeline riche les applique.


Le générateur demandait « couvre quand c'est pertinent : participants, résumé,
décisions, actions (avec responsable + échéance)… ». Sur un plateau télé qui n'en
contient aucune, il produisait des décisions, des prochaines étapes et une
conclusion factuelle inventée.

**C'était un défaut de conception, pas de prompt.** Un titre de section exige
d'être rempli ; une consigne conditionnelle ne le désarme pas — l'abstention est
un problème non résolu que le prompt système ne répare pas, et un modèle sommé
d'extraire là où il n'y a rien invente presque toujours.

Trois changements, dans `core/templates.py` (pur) et `core/report.py` :

1. **Les gabarits sont des listes de questions**, pas des rubriques. Une question
   autorise « aucun ». Les travaux évalués emploient cette forme (QMSum
   query-focused, SCOPE).
2. **Le genre choisit les questions** — `analyse_sportive`, `interview`,
   `generique`. Il est déduit du **profil déjà calculé**, donc sans appel
   supplémentaire coûteux, via une **liste fermée** vérifiée en code.
3. **Le défaut est inversé** : en cas de doute, le gabarit le plus **sobre**.
   Une erreur de classification donne un CR moins riche, jamais plus inventif.

Huit genres existent. Seuls **`projet` et `steerco`** interrogent décisions et
engagements ; les six autres n'en portent aucune trace, ce qu'un test vérifie.
Leurs questions encodent les définitions de référence — une décision est une
proposition **adoptée**, une action un **engagement public** — et le responsable
n'est demandé que « si le dialogue le dit », son attribution restant peu fiable
dans la littérature. Il leur manque encore un contrôle de densité des actions.

Sans profil, le classifieur juge sur du texte brut : les genres à engagements ne
lui sont alors **pas proposés**, et une seconde barrière en code les rejette.
Moins de preuve, moins d'options risquées.

On ne demande plus non plus un document **rédigé** : ni introduction, ni
conclusion. Un compte-rendu n'est pas un texte suivi — AutoMin exclut
délibérément la cohérence textuelle de son barème.

⚠️ Aucun travail publié n'établit que « poser une question réduit le meublage ».
Le raisonnement est cohérent, il n'est pas démontré.

## 11. Vérification des chiffres d'un compte-rendu

Un CR réel a affirmé « finir à **6 buts partout** », « perdre aux **tirs au but** »
et « la France a marqué plus de buts (**5-3**) ». Les trois sont faux : le match
s'est terminé 6-4 dans le temps réglementaire.

> ⚠️ **DÉSARMÉ.** `FACTCHECK_NUMBERS` vaut `False` : ses alertes se sont révélées
> fausses. Le code reste en place et testé, mais il ne tourne pas. Ce qui suit décrit ce qu'il ferait, pas
> ce qu'il fait.

`core/factcheck.py` applique le patron **HERMAN** (Findings EMNLP 2020) : tout
nombre affirmé par le CR doit exister dans le transcript. Déterministe, sans
modèle, quelques millisecondes. Les scores énoncés à l'oral sont normalisés
(« un but à zéro » → `1-0`, « six partout » → `6-6`) sans quoi la source ne
fournirait que des chiffres isolés.

Sur le cas réel il signale `6-6`, `2028` (« Mondial 2028 », jamais prononcé) et
`90`. Le bruit structurel est écarté : titres du gabarit, libellés « Speaker N »,
puces numérotées.

**Ce qu'il ne fait pas, et c'est structurel.** Il ne voit que les nombres
**absents** de la source. Le « 5-3 » du CR échappe au contrôle parce qu'il *est*
dans le transcript — c'est l'attribution qui est fausse (5-3 était l'avance
adverse). Détecter cela suppose de vérifier le couple (nombre, entité), hors du
domaine d'un test d'appartenance. Un test porte ce nom explicitement pour que la
limite ne soit pas oubliée.

Rien n'est réécrit : un bloc d'avertissement est **ajouté** au CR et la liste part
en front-matter (`unsupported_numbers`). Le lecteur voit le chiffre *et* le doute.

## 12. Citations d'un compte-rendu : intégrité, pas entailment

Le pipeline de CR v4 ne demande plus au modèle de composer le Markdown. Qwen
renvoie un JSON borné en sections, affirmations atomiques et preuves
`{timestamp, quote}` ; Memoglyph valide la forme puis rend le document. Le profil
LLM du transcript reste utilisable pour choisir le gabarit, mais seule la
section prononcée — locuteurs, horodatages et texte — entre dans la génération.
Une sortie LLM antérieure ne peut donc plus se recopier comme source.

Le CR entier contient au maximum **10 affirmations pertinentes**, toutes
sections et abstentions confondues. Cette borne existe à la fois dans le prompt
et dans le validateur déterministe. Le schéma JSON transmis au moteur impose au
plus deux affirmations par question et deux extraits par affirmation : le modèle
ne peut pas transformer une réunion longue en inventaire illimité.

Le corps éditorial reste compact :

```markdown
- Une affirmation atomique. [source 00:12:34]
```

Seule une preuve dont l'extrait existe exactement au timestamp reçoit ce
marqueur. Une preuve candidate altérée reste annoncée comme « source à vérifier »
et ne prend jamais l'apparence d'une preuve valide. Les extraits complets vivent
après `<!-- memoglyph:evidence -->` dans une annexe autoritaire :

```markdown
### claim-001
- Une affirmation atomique.
> [source 00:12:34] Extrait exact du tour horodaté.
```

L'UI masque cette annexe derrière **Sources et vérifications** et transforme les
horodatages valides du corps en boutons vers le passage du transcript. Une
question sans réponse produit une abstention explicite `> [source none]`, jamais
comptée comme preuve. Les anciens CR sans annexe conservent leur rendu.

`core/report_citations.py` vérifie sans modèle que l'horodatage existe et que
l'extrait, normalisé uniquement pour la casse, les accents et les espaces, est
présent dans ce tour. Il signale aussi affirmation non citée, citation vide,
altérée ou orpheline et format hors contrat. Il ne juge pas si l'extrait
**implique** l'affirmation ; le statut qualifie donc explicitement sa portée :
`citation_integrity_verified`.

Le résumé `citation_audit` et le détail `citation_warnings` naissent dans le
front-matter Markdown. Une anomalie ajoute une réserve visible sans retirer ni
réécrire l'affirmation du modèle. Un séparateur machine invisible distingue ces
réserves générées du corps réauditable. Le genre du gabarit est affiché dans le
rendu du CR. Les anciens CR restent inchangés et ne sont jamais déclarés
conformes par rétroactivité.

## 12bis. Le pipeline de compte-rendu riche (production)

Le CR de production passe par `core/experimental/` — nommé « experimental » pour
des raisons historiques, mais c'est le **chemin par défaut de l'application**
(`generate_report` → `app_report.make_local_report`, appelé par
`api/main.py`). Il n'est **pas** derrière un drapeau : le drapeau
`MEMOGLYPH_EXPERIMENTAL_REPORT` (défaut `0`) ne gouverne QUE l'outil CLI séparé
`make_experimental_report`, qui écrit des artefacts sous `output/.experimental/`.

**Un seul contrat produit, tous fournisseurs.** `run_stages` exécute huit étapes ;
seul le **profil d'exécution** (`core/experimental/profiles.py`) diffère, résolu
par `profiles.resolve(provider) = CONSERVATIVE si est_local(provider) sinon
LARGE_CONTEXT`. Les deux profils ne changent que le nombre d'appels (tranche,
lots, plafonds), jamais le contrat.

| # | Étape | Nature | Rôle |
|---|---|---|---|
| 1 | Extraction atomique bornée (~2000 tok/tranche) | LLM | unités atomiques `{texte, support[]}` |
| 2 | Validation horodatage/support + consolidation | code | appartenance exacte, rejette le non fondé, dédup, chronologie |
| 3 | Classification fermée (thème par unité) | LLM | `T1..T5` / `other` / `off_topic` / `dialogic_noise` |
| 4 | Veto déterministe **non destructif** | code | rétrograde une clôture de fin, jamais ne supprime |
| 5 | Structure bornée (thèse / tensions / plan) | LLM | ossature référencée par id |
| 6 | Rendu fidèle une-unité-par-phrase | LLM | pas de synthèse libre |
| 7 | Garde de contenance (nombres/entités) | code | rejette toute phrase introduisant un token absent du support |
| 8 | Assemblage + diagnostics | code | strates + provenance |

### Strates de contenu — rétention non destructive (`stages.py`)

Toute unité validée atterrit dans **exactement une** strate ; un `assert` du
pipeline vérifie que `MAIN + SECONDARY + CLOSING + NOISE == unités validées` (aucune
perte silencieuse).

| Strate | Alimentée par | Rendu | Dans le corps |
|---|---|---|---|
| **MAIN** | thèmes `T1..T5` | sections LLM fidèles | oui |
| **SECONDARY** | `other` (hors thème, sur le sujet) | verbatim compact | « Autres points » |
| **CLOSING** | `off_topic` (queue) + `closing` (rétrogradé) | verbatim compact | « Clôture / autres séquences » |
| **NOISE** | `dialogic_noise` court | — | non, mais **tracé** (diag + transparence) |

Le veto (étape 4) ne **rétrograde** en CLOSING qu'un vrai segment de clôture (un
nouveau locuteur qui tient la parole jusqu'à la fin) ; si un locuteur du corps
reprend ensuite, c'était une interjection et rien n'est rétrogradé. Le filtrage
destructif effaçait de vraies conclusions.

### Retour à la source

Les citations sont des jetons `[source HH:MM:SS]` en ligne (annexe des unités
citées après `<!-- memoglyph:evidence -->`). L'UI (`MarkdownView.tsx`) transforme
un jeton valide en puce cliquable qui bascule sur l'onglet du transcript source
et positionne la lecture audio à l'instant (`TranscriptReader` → `seek`). Une
question sans réponse produit `[source none]`, jamais comptée comme preuve.

### Normalisation d'horodatage (`experimental/timestamps.py`)

Couche commune, provider-agnostique, AVANT la validation d'unité. `canonicalize`
reformate structurellement (`MM:SS` → `00:MM:SS`, `H:MM:SS` → `0H:MM:SS`) et
rejette l'invalide (`mm/ss ≥ 60`, non numérique…) ; `resolve` EXIGE ensuite
l'appartenance exacte au jeu d'horodatages du transcript. N'invente rien, aucun
appariement flou.

### Provenance du CR

Front-matter : `model` = `llm.modele_servi(provider)` (le modèle **réellement**
servi, donc MLX/Mistral/OpenAI ne se confondent plus), `report_engine:
local_hybrid_v1`, `genre`, `source_transcript`, `sensitivity`, `tier_counts`
(main/secondary/closing/noise), et `pipeline = provenance.report_fingerprint(…)`
(`REPORT_PIPELINE_VERSION = 6`). Le champ `generation` porte l'un des quatre
statuts (`PASS_DIRECT` / `PASS_REPAIRED` / `PASS_RETRY` / `FAIL`, voir
[docs/STATUTS_GENERATION.md](docs/STATUTS_GENERATION.md)).

---

## 12ter. Sensibilité de la réunion

`core/sensitivity.py` porte une étiquette à **trois** valeurs (`Literal` fermé),
défaut `standard` :

| Valeur | Sens |
|---|---|
| `standard` | par défaut |
| `confidential` | confidentiel |
| `ultra_confidential` | ultra-confidentiel |

La sensibilité est une propriété de la **réunion** : une seule valeur pour toutes ses
versions, transcripts et comptes rendus. Sa source de vérité est le registre des réunions
(`.memoglyph-sensibilite.json` à la racine de la bibliothèque, `library.fixer_sensibilite_reunion`),
un fichier à côté des `.md`, jamais l'index. Un réglage y est rangé par **projet et
réunion** (`project_id`, `meeting_id`), la portée des droits d'accès : deux comptes ou
deux projets dont des documents portent le même `meeting_id` ne partagent jamais de
réglage, et le niveau d'un document ne tient compte que des documents de son projet. Les
deux routes vérifient les mêmes droits, sur la réunion du projet qui contient le document
(ou, pour la route de la réunion, du seul projet de l'appelant qui la porte : 409 s'il y
en a plusieurs).

Plus généralement, une réunion est un `meeting_id` **dans un projet**. Toute route qui
lit ou modifie une réunion (lecture, faits, déplacement, archivage et suppression,
sensibilité, partage, MCP) agit sur ses documents dans un projet de la portée de
l'appelant (`library.reunion_dans_la_portee`) : le document homonyme d'un autre projet
ou d'un autre compte n'est jamais lu, montré, déplacé ni archivé, et il ne bloque pas
l'appelant. Le déplacement d'un document n'emporte que les membres de sa réunion dans
son projet. `PATCH /api/meetings/{id}/sensitivity`, ou
`PATCH /api/transcript/{name}/sensitivity` depuis n'importe lequel de ses documents, la
change en une action, sans réécrire aucun `.md` : l'ordre et le contenu des versions ne
bougent pas. La réponse rend le niveau appliqué.

Un réglage retient le niveau et les documents qu'il couvre, ceux de la réunion à cet
instant, par leur identifiant stable (`id`). Un document arrivé ensuite (nouvelle
transcription, compte rendu importé, document regroupé) ne peut que relever la réunion :
marqué plus strict à sa création, il impose son niveau. Le réglage suivant le couvre à
son tour.

Sans entrée au registre, le niveau d'une réunion est le plus strict de ses documents (le
`sensitivity` de leur front-matter, noté à leur création, et repris par un compte rendu
à sa génération). `./memoglyph sensibilite-reunions [--appliquer]` l'inscrit au registre,
à la demande seulement, et range par projet les entrées d'un format antérieur (rangées
par réunion seule, le niveau seul ou avec ses documents couverts) : chacune va dans chaque
projet où vivent les documents qu'elle couvre, au niveau de l'ancienne. Idempotente, elle
ne réécrit aucun `.md` et ne change aucun niveau appliqué.

L'écran, l'API (`sensitivity` des réunions et des éléments de liste,
`sensitivity_effective` d'un document), le RAG et le MCP lisent ce même niveau
(`library.sensibilite_effective`). La règle d'envoi en dépend : une réunion
ultra-confidentielle ne part vers aucun fournisseur cloud sans l'autorisation de son
propriétaire (`sensitivity.verifier_envoi`), relue avant chaque appel, et ne se partage pas.

---

## 12quater. Faits de la réunion (ECL)

Les **faits de la réunion** (décisions, actions avec leur porteur, problèmes ouverts,
chiffres et échéances) sont extraits par `core/ecl.py`, **à part du compte rendu** : le CR
n'appelle pas l'extraction, et ses rubriques Décisions, Actions et Questions ouvertes
viennent de ses propres unités. L'extraction se lance à la demande, dans l'onglet
« Faits » de la réunion (`POST /api/meetings/{id}/ecl`) ou par `./memoglyph ecl-extraire`.

- Chaque fait est ancré : sa citation figure mot pour mot dans le transcript, à 30 s de
  son horodatage ; un fait refusé est compté, jamais gardé.
- Le mode (local ou cloud) suit le fournisseur choisi, par capacité (`llm.est_local`), et
  la même règle d'envoi que le CR : la sensibilité de la réunion est relue avant chaque
  appel cloud.
- **Extraction partielle** : k tranches sur n en échec (0 < k < n). Elle ne remplace pas
  l'extraction courante, qui fait foi : elle va dans l'historique, marquée. Sans
  extraction courante, elle devient la courante, marquée, jusqu'à une relance complète.
- La mémoire entre réunions (liens entre faits, entités) est envisagée, pas construite.

Détail, livré et envisagé : [docs/ecl.md](docs/ecl.md).

## 13. Concurrence et ressources

- Jobs **bornés** (`MEMOGLYPH_MAX_ACTIVE_JOBS`) : STT + diarisation + LLM
  simultanés saturent 18 Go de RAM unifiée. Dépassement → HTTP 429.
- Upload **en flux** et plafonné (`MEMOGLYPH_MAX_UPLOAD_MB`).
- Annexe d'empreintes et manifeste : écriture **atomique** *et* **verrouillée**.
  L'atomicité protège d'un plantage, pas d'une course — sans verrou, deux jobs
  concurrents perdaient des entrées.
- Mémoire : `stt.unload()` avant l'appel LLM ; le moteur `mlx` local est déchargé
  via `LLMEngineDechargeable.unload()` (les moteurs cloud n'ont rien à décharger).

---

## 14. Réglages (variables d'environnement)

> **Préfixe.** Les réglages se lisent sous `MEMOGLYPH_*` (`config.getenv_compat`). Les secrets et modèles cloud
> sont lus **sans préfixe** (`MISTRAL_API_KEY`, `MISTRAL_STT_MODEL`,
> `MISTRAL_CHAT_MODEL`, `OPENAI_API_KEY`, `HF_TOKEN`, `OLLAMA_HOST`). Défauts
> relevés dans `core/config.py`.

**Secrets & modèles cloud** (sans préfixe)

| Variable | Défaut | Rôle |
|---|---|---|
| `HF_TOKEN` | — | requis pour la diarisation (pyannote) |
| `MISTRAL_API_KEY` | — | clé Mistral (STT Voxtral + Chat + embeddings) |
| `MISTRAL_STT_MODEL` | `voxtral-mini-2602` | modèle STT Mistral |
| `MISTRAL_CHAT_MODEL` | `ministral-14b-2512` | modèle chat Mistral |
| `OPENAI_API_KEY` | — | clé OpenAI (comptes-rendus, Responses API) |
| `OPENROUTER_API_KEY` | — | clé OpenRouter (fournisseur `openrouter`, en ligne) |
| `GROQ_API_KEY` | — | clé Groq (fournisseur `groq`, en ligne) |

**LLM / CR** (préfixe `MEMOGLYPH_`)

| Variable | Défaut | Rôle |
|---|---|---|
| `MEMOGLYPH_MLX_LLM_MODEL` | `mlx-community/Qwen3-4B-Instruct-2507-4bit` | LLM local (fournisseur `mlx`, défaut). Doit être **sans raisonnement** |
| `MEMOGLYPH_OPENAI_CHAT_MODEL` | `gpt-5.6-luna` | LLM OpenAI |
| `MEMOGLYPH_OPENROUTER_MODEL` | — | modèle choisi chez OpenRouter ; sans lui, le fournisseur n'est pas proposé |
| `MEMOGLYPH_GROQ_MODEL` | — | modèle choisi chez Groq ; sans lui, le fournisseur n'est pas proposé |
| `MEMOGLYPH_MLX_PORT` | `8772` | port ÉPINGLÉ de `mlx_lm.server` (pas 8080) |
| `MEMOGLYPH_MLX_MAX_TOKENS` | `16384` | plafond de génération (le défaut `mlx_lm` de 512 couperait un CR) |
| `MEMOGLYPH_MLX_SONDE_TIMEOUT` | `60` | délai de la sonde de disponibilité MLX |
| `MEMOGLYPH_REPORT_SEED` / `_PROFILE_SEED` | `42` | graines de reproductibilité |
| `MEMOGLYPH_FACTCHECK_NUMBERS` | `0` | vérificateur de chiffres (DÉSARMÉ, §11) |
| `MEMOGLYPH_EXPERIMENTAL_REPORT` | `0` | n'active QUE le CLI d'artefacts `output/.experimental/` — le CR de l'app est riche quoi qu'il arrive (§12bis) |
| ~~`MEMOGLYPH_LLM_MODEL`~~ | *(aucun)* | **legacy Ollama**, hors produit ; requis par les seuls outils d'audit qui appellent Ollama |

**Recherche / embeddings** (toujours locaux)

| Variable | Défaut | Rôle |
|---|---|---|
| `MEMOGLYPH_EMBED_BACKEND` | `local` | `local` = modèle embarqué (recherche + RAG sans réseau), seule valeur prise en charge ; l'ancien moteur Ollama est retiré |
| `MEMOGLYPH_EMBED_LOCAL_MODEL` | `intfloat/multilingual-e5-large` | modèle d'embeddings local (dim 1024). Changer impose une réindexation |

**Audio / STT / ressources**

| Variable | Défaut | Rôle |
|---|---|---|
| `MEMOGLYPH_STT_CHUNK_SEC` | `0` | transcription progressive (0 = une passe, défaut) |
| `MEMOGLYPH_MISTRAL_UPLOAD_KBPS` | `64` | débit AAC de l'audio envoyé au STT Mistral (`0` = WAV brut) |
| `MEMOGLYPH_KEEP_AUDIO` / `_KEEP_FAITHFUL` / `_KEEP_UPLOAD` | `1` / `1` / `0` | conservation archive / FLAC fidèle / média source |
| `MEMOGLYPH_AUDIO_CODEC` / `_BITRATE` | `aac` / `48k` | format d'archive |
| `MEMOGLYPH_VOICE_THRESHOLD` | `0.5` | cosinus de rattachement d'une voix |
| `MEMOGLYPH_MAX_UPLOAD_MB` | `2048` | plafond d'upload |
| `MEMOGLYPH_MAX_ACTIVE_JOBS` / `_MAX_HEAVY_TASKS` | `2` / `1` | jobs en file / travaux LOURDS simultanés |
| `MEMOGLYPH_MAX_JOB_HISTORY` / `_MAX_JOB_HISTORY_TOTAL` | `50` / `200` | traitements terminés gardés en mémoire, **par compte** / au total (un compte très actif n'efface pas l'historique d'un autre) |
| `MEMOGLYPH_PORT_BASE` | `8770` | UI `8770` / API `8771` |
| `MEMOGLYPH_ATTENTE_S` | `900` | attente maximale du démarrage par `start.sh`, réindexation comprise |
| `MEMOGLYPH_UI_HOST` | `127.0.0.1` | écoute Vite ; `--lan` utilise `0.0.0.0` |

---

## 15. Cas limites gérés

- Format non reconnu → refus explicite avec la liste des extensions.
- Vidéo sans piste audio → message dédié (ffmpeg).
- Diarisation sans `HF_TOKEN` / conditions non acceptées → erreur guidée, le
  reste du pipeline n'est pas impacté.
- Nom de projet dangereux (`..`, `.trash`, `a/b`) → HTTP 400.
- Le WAV temporaire est supprimé en fin de run, **même en cas d'erreur**.

---

## 16. Vérification

```bash
make check      # pytest + vitest + lint + build (ce que fait la CI)
make test-cov   # couverture Python
```

Le build front utilise `tsc -b` : `tsc --noEmit` ne vérifie **pas** la même chose
(il ignore les fichiers de test) — toujours passer par `make check`.
