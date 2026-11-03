# Local ou en ligne

Ce qui reste sur le Mac par défaut, ce qui peut partir vers un fournisseur en ligne, et ce qui ne part jamais sans autorisation. Les citations et leurs limites sont dans [limites.md](limites.md) ; les réglages dans [configuration.md](configuration.md).

## Le réglage de départ : tout en local

Le pipeline par défaut n'utilise que des modèles locaux : Whisper et un modèle de langage MLX (`Qwen3-4B-Instruct`) servi sur le Mac. La diarisation (pyannote) est facultative et demande une installation à part (voir [installation.md](installation.md)). Par défaut, la recherche sémantique utilise un modèle d'embeddings local ; `MEMOGLYPH_EMBED_BACKEND=ollama` conserve l'ancien comportement (un serveur Ollama).

## Les fournisseurs en ligne, facultatifs

Trois fournisseurs en ligne sont facultatifs pour les comptes rendus, l'Assistant et les faits de la réunion : **Mistral**, **OpenAI** et **Anthropic** (Claude). Mistral peut aussi transcrire. Il faut les choisir explicitement ; ils envoient alors de l'audio et/ou du texte à leur API.

- **Mistral** : les comptes rendus et l'Assistant utilisent `ministral-14b-2512`, que le palier par défaut de Mistral sert (`mistral-small` y a un quota nul). Voxtral transcrit une réunion de plus de 50 minutes en tranches qui se recouvrent, envoyées au rythme de la limite audio du palier par défaut (3 600 secondes d'audio par minute) ; les horodatages sont recalés sur la réunion entière et les locuteurs raccordés d'une tranche à l'autre.
- **Anthropic** : les comptes rendus et l'Assistant utilisent Claude Haiku 5.5, les faits de la réunion Claude Sonnet 5.5.

## Ce qui ne part pas : l'ultra-confidentiel

Un document étiqueté **ultra-confidentiel** ne part vers aucun fournisseur en ligne par défaut : la transcription, le compte rendu, les faits et les extraits de l'Assistant restent locaux. Chaque compte peut autoriser le cloud pour ses propres documents ultra-confidentiels dans **Confidentialité** (inactif par défaut, confirmé à l'activation, effectif aussitôt à la désactivation). Ce réglage n'ouvre rien pour les documents partagés ni pour un autre compte, et chaque envoi autorisé est consigné sans contenu (voir [SECURITY.md](../SECURITY.md)).

## Un serveur de modèle auto-hébergé

Un serveur de modèle hébergé par l'utilisateur (MLX, ou un serveur compatible OpenAI comme Ollama, LM Studio ou vLLM, réglé dans `.env`) ne compte comme local que sur l'adresse de bouclage de cette machine. Sur tout autre hôte, il s'affiche **Cloud privé** et suit les règles du cloud, ultra-confidentiel compris.

## Une sensibilité par réunion

Chaque réunion porte une seule sensibilité, relue avant chaque appel en ligne. **Confidentiel** est une étiquette pour les personnes : elle n'oriente rien d'elle-même, et c'est l'humain qui décide de ce qui part où.

---

[Retour au README](../README.fr.md)
