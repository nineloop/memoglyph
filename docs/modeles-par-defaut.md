# Modèles par défaut

Les modèles utilisés quand rien n'est précisé.

| Composant | Modèle |
|---|---|
| Transcription (local, défaut) | `mlx-community/whisper-large-v3-turbo` |
| Transcription (cloud) | `voxtral-mini-2602` (Mistral API) |
| Diarisation (local) | `pyannote/speaker-diarization-community-1` |
| Diarisation (cloud) | Native Voxtral (Mistral API) |
| LLM local (défaut) | `mlx-community/Qwen3-4B-Instruct-2507-4bit` via `mlx_lm.server` |
| Faits de la réunion, ECL (local) | `ibm-granite/granite-4.2-8b-q4-mlx`, réflexion coupée ; repli sur le LLM local s'il est absent |
| LLM cloud (Mistral) | `ministral-14b-2512` (Mistral API) |
| LLM cloud (OpenAI) | `gpt-5.6-luna` (Responses API) |
| LLM cloud (Anthropic) | `claude-haiku-5-5` (API Messages) |
| Faits de la réunion, ECL (cloud, Anthropic) | `claude-sonnet-5-5` |
| Embeddings (local, recherche/RAG) | `intfloat/multilingual-e5-large` |

---

[Retour au README](../README.fr.md)
