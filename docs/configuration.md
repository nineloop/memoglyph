# Configuration utile

Variables d'environnement et réglages.

Les réglages se lisent sous le préfixe `MEMOGLYPH_*`. Les clés et modèles cloud sont lus **sans préfixe**
(`MISTRAL_*`, `OPENAI_*`). Liste complète et défauts : [SPEC.md §14](../SPEC.md).

| Variable | Usage |
|---|---|
| `HF_TOKEN` | accès au modèle pyannote gated (diarisation) |
| `MEMOGLYPH_MLX_LLM_MODEL` | modèle LLM local, `mlx-community/Qwen3-4B-Instruct-2507-4bit` par défaut |
| `MEMOGLYPH_FAITS` | `1` active les faits de la réunion (ECL), expérimentaux, désactivés par défaut : onglet « Faits », ses routes, l'outil MCP `lire_faits`, `ecl-extraire` |
| `MEMOGLYPH_ECL_MLX_MODEL` | modèle local des faits de la réunion (ECL), `ibm-granite/granite-4.2-8b-q4-mlx` par défaut ; vide : celui du LLM local |
| `ANTHROPIC_API_KEY` | clé API Anthropic (moteur `anthropic`, Claude Haiku 5.5 par défaut) |
| `MEMOGLYPH_ECL_ANTHROPIC_MODEL` | modèle des faits de la réunion (ECL) chez Anthropic, `claude-sonnet-5-5` par défaut |
| `MISTRAL_API_KEY` | clé API Mistral pour STT Voxtral et LLM Chat |
| `MISTRAL_STT_MODEL` | modèle STT Mistral, `voxtral-mini-2602` par défaut |
| `MISTRAL_CHAT_MODEL` | modèle LLM Mistral, `ministral-14b-2512` par défaut (`mistral-small` a un plafond nul au palier par défaut) |
| `MEMOGLYPH_MISTRAL_STT_TRANCHE_S` | au-delà de cette durée, Voxtral transcrit en tranches d'au plus cette durée, `3000` (50 min) par défaut ; `0` : jamais découper |
| `MEMOGLYPH_MISTRAL_STT_RECOUVREMENT_S` | recouvrement entre deux tranches Voxtral, `30` s par défaut |
| `MEMOGLYPH_MISTRAL_AUDIO_S_PAR_MINUTE` | limite audio du palier Mistral suivie par les envois, `3600` s d'audio par minute par défaut |
| `OPENAI_API_KEY` | clé API OpenAI pour les comptes-rendus (Responses API) |
| `MEMOGLYPH_OPENAI_CHAT_MODEL` | modèle LLM OpenAI, `gpt-5.6-luna` par défaut |
| `OPENROUTER_API_KEY` | clé API OpenRouter (fournisseur `openrouter`, en ligne) |
| `MEMOGLYPH_OPENROUTER_MODEL` | identifiant du modèle chez OpenRouter ; aucun défaut : sans lui, le fournisseur n'est pas proposé |
| `GROQ_API_KEY` | clé API Groq (fournisseur `groq`, en ligne) |
| `MEMOGLYPH_GROQ_MODEL` | identifiant du modèle chez Groq ; aucun défaut : sans lui, le fournisseur n'est pas proposé |
| `MEMOGLYPH_MLX_PORT` | port épinglé du `mlx_lm.server`, `8772` par défaut |
| `MEMOGLYPH_PORT_BASE` | port UI de base, API sur le suivant |
| `MEMOGLYPH_ATTENTE_S` | attente maximale du démarrage par `start.sh`, `900` s par défaut |
| `MEMOGLYPH_UI_HOST` | écoute UI, `127.0.0.1` par défaut ; préférer `--lan` pour `0.0.0.0` |
| `MEMOGLYPH_ALLOWED_HOSTS` | mode LAN : noms d'hôte autorisés en plus de l'IP et de `.local` (CSV) |
| `MEMOGLYPH_USAGE_DB` | chemin du magasin de suivi FinOps `usage.db` (tests / emplacement alternatif) |
| `MEMOGLYPH_EMBED_LOCAL_MODEL` | modèle d'embeddings local, `intfloat/multilingual-e5-large` par défaut |
| `MEMOGLYPH_KEEP_FAITHFUL` | conserve le FLAC fidèle, activé par défaut |

Pour OpenRouter et Groq : après avoir ajouté ou changé une clé dans `.env`, redémarrer l'instance. Les clés ne se saisissent que dans `.env` (jamais à l'écran) ; l'adresse de chaque service est fixe et aucun réglage ne la change.

Exemple de ports imposés :

```bash
MEMOGLYPH_UI_PORT=9200 MEMOGLYPH_API_PORT=9201 ./start.sh
```

Les logs de développement sont écrits dans `memoglyph-api.log` et `memoglyph-ui.log`. La
documentation interactive de l'API (`/docs`, `/redoc`, `/openapi.json`) est fermée : elle
décrivait toute l'API à qui n'était pas connecté.

---

[Retour au README](../README.fr.md)
