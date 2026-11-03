## Local ou en ligne, au choix

Par défaut, tout tourne sur le Mac : Whisper pour la transcription et un modèle de langage MLX local pour les comptes rendus et l'Assistant. La recherche sémantique utilise aussi, par défaut, un modèle d'embeddings local.

Un modèle en ligne peut s'ajouter au besoin : Mistral, OpenAI ou Anthropic, ou tout service au format OpenAI, tel que Groq, par le fournisseur `compatible`. Mistral peut aussi transcrire, avec Voxtral.

Les deux se combinent selon la sensibilité de chaque réunion. Un document étiqueté ultra-confidentiel ne part vers aucun fournisseur en ligne, sauf si son propriétaire l'autorise pour son compte. Détails : [docs/local-et-en-ligne.md](docs/local-et-en-ligne.md).

Il n'y a aucune télémétrie. L'usage est noté en local, et rien ne quitte la machine, sauf les appels aux fournisseurs choisis et, au premier usage, les téléchargements de modèles depuis Hugging Face.
