# Fonctionnalités

Ce que fait Memoglyph, l'état du projet, les exemples d'usage et le parcours produit.

## Ce qui fonctionne

Memoglyph transforme localement un fichier audio ou vidéo en un espace de travail
Markdown : transcription horodatée, locuteurs, comptes-rendus, recherche et
questions sur un corpus de réunions.

Le moteur est optimisé pour Apple Silicon. **Aucun service cloud n'est
nécessaire** : en mode local — le défaut — l'audio, les transcriptions et les
index restent sur la machine. Cinq fournisseurs cloud optionnels existent pour
les comptes-rendus, l'Assistant et les faits de la réunion (**Mistral**, **OpenAI**,
**Anthropic**, Claude, **OpenRouter** et **Groq**), et Mistral peut aussi assurer la transcription. Avec Anthropic, le
compte rendu et l'Assistant passent par Claude Haiku 5.5, les faits par Claude Sonnet 5.5 ; il faut les choisir explicitement, et ils envoient alors l'audio
et/ou le texte à l'API correspondante. La recherche par le sens s'appuie
**toujours** sur un modèle local ; le RAG, lui, RÉPOND avec le fournisseur choisi
— seule la recherche des passages reste locale.

> **État du projet.** L'application est utilisable pour transcrire,
> classer et rechercher. Le compte-rendu passe par un **unique pipeline riche par
> strates** (MAIN / SECONDARY / CLOSING / NOISE, rétention non destructive),
> identique pour tous les fournisseurs LLM ; seul le *profil d'exécution* varie
> selon la capacité (local vs distant). Les comptes-rendus affichent des
> horodatages compacts, conservent leurs extraits exacts dans une annexe dont
> l'intégrité est vérifiée déterministiquement, et un clic sur `[source]`
> positionne la lecture audio à l'instant cité. Une citation est un repère pour
> vérifier, pas une preuve : la transcription elle-même peut se tromper, et une
> citation n'*implique* pas forcément l'affirmation (l'étude P0–P5 a explicitement
> refusé d'intégrer les juges LLM/NLI actuels). Une **étiquette de sensibilité** à trois
> niveaux est portée par chaque document ; par défaut, un document **ultra-confidentiel**
> ne part vers aucun fournisseur cloud (transcription, compte-rendu, faits, Assistant),
> sauf si son propriétaire l'autorise pour ses propres documents. Voir
> [SPEC.md](../SPEC.md) pour la conception. L'historique des décisions mesurées est
> tenu dans un journal interne, non publié.

- Transcription Whisper `large-v3-turbo` (local) ou Mistral Voxtral Mini (API cloud) avec langue détectée ou imposée. Au-delà de 50 minutes, Voxtral transcrit en tranches qui se recouvrent de 30 s, envoyées au rythme de la limite audio du palier par défaut de Mistral (3 600 s d'audio par minute) ; les horodatages sont recalés sur la réunion entière et les locuteurs raccordés d'une tranche à l'autre par leur temps de parole commun dans le recouvrement.
- Diarisation optionnelle avec pyannote (local, modèle `speaker-diarization-community-1`, installation à part) ou native Voxtral (Mistral API) et identification vocale entre réunions.
- **Génération automatique du compte-rendu** en fin de pipeline, via un **pipeline riche par strates** (extraction atomique bornée → validation déterministe d'horodatage → classification fermée + veto non destructif → structure bornée → rendu fidèle par unité → garde de contenance), avec barre de progression SSE en 6 étapes. Un seam unique (`report.generate_report`) sert **tous** les fournisseurs ; l'ancien `make_report` reste comme repli.
- **Plusieurs fournisseurs LLM, un seul contrat** : MLX local (`mlx_lm.server`, défaut), Mistral Chat API, OpenAI (Responses API), Anthropic (API Messages, Claude), ou les services en ligne OpenRouter et Groq (clé et modèle dans `.env`). Le fournisseur est choisi dans l'UI ; la chaîne de génération est la même, seul le profil d'exécution (tranche/lots/plafonds) change, résolu **par capacité** (`llm.est_local`), jamais par un nom de fournisseur.
- **Timeline UI adaptive** : affiche dynamiquement les détails du pipeline selon le moteur STT sélectionné.
- **Gabarits interrogatifs par genre** (`projet`, `steerco`, `interview`, `entretien_embauche`, `internal_briefing`, `town_hall`, `keynote`, `webinar`, `analyse_sportive`, `generique`) avec droit à l'abstention déterministe (*"S'il n'y en a aucun, réponds « aucun »"*).
- Projets isolés, avec une boîte **Non classé** lorsque aucun projet n'est sélectionné.
- **Bibliothèque** organisée par réunion et lecteur audio synchronisé avec les balises d'horodatage.
- **Archivage unitaire** depuis le lecteur de document ou l'Espace Réunion vers la Corbeille.
- Recherche **hybride** : plein-texte FTS5 et sémantique (`sqlite-vec`) fusionnés par Reciprocal Rank Fusion.
- Assistant RAG local ou cloud (Mistral / OpenAI / Anthropic / OpenRouter / Groq), borné au projet courant ou explicitement étendu à tous.
- Comptes-rendus Markdown via MLX local, Mistral, OpenAI, Anthropic, OpenRouter ou Groq, avec des citations à vérifier contre l'audio et des réserves de qualité visibles.
- **Étiquette de sensibilité** par réunion (`standard` / `confidential` / `ultra_confidential`), changée en une action pour toutes ses versions — par défaut, un document `ultra_confidential` ne part vers aucun fournisseur cloud : transcription, compte-rendu, faits et extraits de l'Assistant restent locaux. Chaque compte peut autoriser le cloud pour SES documents ultra-confidentiels dans **Confidentialité** (inactif par défaut, confirmé à l'activation, effectif aussitôt à la désactivation) ; le réglage n'ouvre rien pour ce qui est partagé ni pour un autre compte, et chaque envoi autorisé est consigné sans contenu (voir [SECURITY.md](../SECURITY.md)). Une réunion porte une seule sensibilité ; une valeur inconnue est refusée (422), et la sensibilité est relue avant chaque appel cloud. **Confidentiel** est une étiquette pour les personnes : elle n'oriente rien d'elle-même, c'est l'humain qui décide de ce qui part où.
- **Cloud privé** : un serveur de modèle que vous hébergez (MLX, ou un « Serveur compatible OpenAI » comme Ollama, LM Studio ou vLLM, réglé par `.env`) ne compte comme local que sur la boucle locale de cette machine. Sur un autre hôte, il s'affiche « Cloud privé » et suit les règles du cloud, ultra-confidentiel compris.
- **Suivi d'usage & coût IA (FinOps)** : télémétrie par exécution dans un magasin séparé `usage.db`, **usage & coût par document** dans l'onglet Information et **tableau de bord Usage** borné projet avec drill-down forensique. Le coût local est estimé (hypothèse), le coût cloud suit des tarifs ratifiés et datés.
- **Serveur MCP en lecture seule** pour les agents (`./memoglyph mcp <identifiant>`) : projets, réunions, recherche, comptes rendus et, s'ils sont activés, faits de la réunion, bornés à un compte ; l'ultra-confidentiel du compte seulement s'il autorise le cloud pour lui ([docs/mcp.md](mcp.md)).
- **Interface bilingue FR/EN** : détection de la langue du navigateur et bascule manuelle persistée (catalogue maison, sans dépendance i18n).
- Détection des doublons, Corbeille restaurable, purge vérifiée et maintenance des index.
- Archives d'écoute et FLAC fidèle à l'entrée des modèles pour retraitement.

Les fichiers `.md` sont la source de vérité. SQLite ne contient que des index dérivés et reconstructibles.

## Exemples d'usage (Use Cases)

Memoglyph adapte automatiquement la structure du compte-rendu au genre de la réunion grâce à des gabarits interrogatifs ciblés :

- 📁 **Réunion de Projet / Sync d'équipe (`projet`)** : Extrait les décisions adoptées, les engagements individuels attribués (*qui fait quoi*), les jalons/dates limites annoncés, les questions ouvertes et les blocages/dépendances.
- 📊 **Comité de Pilotage (`steerco`)** : Synthétise l'état d'avancement, les écarts de planning/budget, les arbitrages rendus, les alertes majeures et les demandes d'approbation soumises.
- 🎙️ **Interview & Podcast (`interview`)** : Identifie les thèses de la personne interrogée, les faits notables, les annonces et les conseils/recommandations partagés.
- ⚙️ **Briefing Opérationnel (`internal_briefing`)** : Clarifie les consignes terrain, les contraintes, les cas particuliers et le point de contact/canal d'escalade référent.
- 🏛️ **Réunion Plénière / Town Hall (`town_hall`)** : Présente les orientations stratégiques, les réponses apportées aux questions des collaborateurs et les rendez-vous à venir.
- 🎓 **Webinaire ou Keynote (`webinar`, `keynote`)** : Restitue la démonstration, les arguments clés, les données présentées et la session de Q&A.

## Parcours produit

1. Choisir un projet actif ou conserver **Non classé**.
2. Déposer un fichier audio ou vidéo dans **Transcrire** et cocher les enrichissements souhaités (Locuteurs, Profilage, Compte-rendu automatique).
3. Suivre le traitement en temps réel via la **timeline à 6 étapes** (Préparation → STT → Diarisation → Profilage → Compte-rendu Map-Reduce → Finalisation).
4. Ouvrir la réunion dans la **Bibliothèque** pour relire le transcript, écouter l'audio synchronisé et consulter le compte-rendu généré.
5. Utiliser **Recherche** ou **Assistant** pour interroger le corpus en langage naturel.
6. Archiver unitairement un transcript ou un compte-rendu depuis le lecteur vers la **Maintenance → Corbeille** pour restauration ou purge définitive.

## En bref

- **Vérifiable** : chaque affirmation cite son horodatage, et un chiffre qui n'a jamais été prononcé est écarté.
- **Local par défaut** : Whisper et un modèle local sur Apple Silicon. Mistral, OpenAI, Anthropic, OpenRouter, Groq ou un serveur auto-hébergé compatible OpenAI, seulement si vous les choisissez.
- **Pour vous, ou pour une petite équipe, auto-hébergé** : des comptes, un espace privé chacun, un partage en lecture seule.
- **Niveaux de confidentialité** : une réunion ultra-confidentielle n'est jamais envoyée à un modèle cloud, sauf si son propriétaire l'autorise.
- **Prêt pour les agents** : un serveur MCP en lecture seule, borné à un compte.
- **Aucune télémétrie** : l'usage est noté en local ; rien ne part ailleurs qu'aux fournisseurs choisis et, au premier usage, aux téléchargements de modèles depuis Hugging Face.

---

[Retour au README](../README.fr.md)
