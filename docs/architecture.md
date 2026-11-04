# Architecture et données

Organisation du code, des fichiers et des données.

```mermaid
flowchart TD
    subgraph UI ["Interface Utilisateur (React + Vite)"]
        Nav["Navigation — Espace : Transcrire | Bibliothèque | Recherche | Assistant | Voix · Données : Doublons | Corbeille | Maintenance"]
        Timeline["Timeline SSE 6 étapes (0 à 100%)"]
        Player["Lecteur audio synchronisé aux timestamps"]
    end

    subgraph API ["Serveur API (FastAPI)"]
        Router["Router REST & Tâches de fond (/api/transcribe, /api/library)"]
        JobSupervisor["Superviseur de jobs & événements SSE"]
    end

    subgraph Core ["Pipeline de Traitement Local (Python Core)"]
        AudioPrep["FFmpeg : Normalisation PCM 16kHz mono"]
        STTEngine["STT : Whisper Large v3 Turbo (MLX Apple Silicon)"]
        DiarEngine["Diarisation : pyannote (community-1) & Registre d'empreintes vocales"]
        LLMProfile["Profilage : Résumé + Détection de genre LLM (MLX · Mistral · OpenAI · Anthropic · OpenRouter · Groq)"]
        LLMReport["Auto-CR : pipeline riche par strates (MLX · Mistral · OpenAI · Anthropic · OpenRouter · Groq)"]
    end

    subgraph Storage ["Source de Vérité & Indexation"]
        MDStore["Fichiers Markdown (output/Projet/*.md)"]
        TrashDir["Corbeille de secours (.trash/)"]
        SQLiteDB[("Base SQLite : FTS5 + sqlite-vec")]
    end

    UI -->|HTTP REST & SSE Stream| API
    API --> Core
    AudioPrep --> STTEngine
    STTEngine --> DiarEngine
    DiarEngine --> LLMProfile
    LLMProfile --> LLMReport
    LLMReport --> MDStore
    MDStore -.->|"library.reindex()"| SQLiteDB
    MDStore --> TrashDir
```

- `core/` contient le pipeline et la logique métier indépendants de l'UI.
- `api/` expose les jobs, la progression SSE et les ressources de la bibliothèque.
- `ui/` contient l'application React et son proxy de développement.
- `output/` contient les données personnelles et n'est jamais versionné.
- `index.db` est dérivé de `output/` et peut être reconstruit.

La conception détaillée est dans [SPEC.md](../SPEC.md). Le journal des décisions
mesurées est interne et n'est pas publié.

---

[Retour au README](../README.fr.md)
