"""Constantes et réglages centralisés de Memoglyph."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


# Préfixe des réglages de Memoglyph dans l'environnement. Les clés et modèles cloud
# sont lus sans préfixe, sous le nom de leur fournisseur.
ENV_PREFIX = "MEMOGLYPH_"


def getenv_compat(suffix: str, default: str = "",
                  environ: Mapping[str, str] | None = None) -> str:
    """Lit `MEMOGLYPH_<suffix>` ; `default` si la variable n'est pas posée.

    SEUL point de lecture des variables préfixées : un test refuse toute lecture
    directe ailleurs. `environ` permet aux appelants qui raisonnent sur un
    environnement fourni (`run_ml_tests.py`) de l'utiliser. Une valeur vide est
    une valeur posée.
    """
    source = os.environ if environ is None else environ
    return source.get(ENV_PREFIX + suffix, default)


# --- Racine projet (parent de core/) ---
ROOT = Path(__file__).resolve().parent.parent


def _fichier_env() -> Path:
    """Le `.env` lu : celui que nomme `MEMOGLYPH_ENV_FILE` (une autre racine, les tests),
    sinon `ROOT/.env`."""
    return Path(getenv_compat("ENV_FILE") or ROOT / ".env")


def _deja_exportee(cle: str) -> bool:
    """Une variable est-elle déjà posée, avec une valeur, dans l'environnement ?"""
    return bool(os.getenv(cle))


def _poser_env(chemin: Path) -> bool:
    """Pose les variables d'un `.env` dans l'environnement, sans écraser une variable
    exportée ; rend False sans fichier (best-effort, sans dépendance dure)."""
    if not chemin.exists():
        return False
    for line in chemin.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if value and not _deja_exportee(key):
            os.environ[key] = value
    return True


# Le `.env` AVANT toute lecture de réglage : les constantes de ce module sont lues à
# l'import, et `load_env()`, appelé ensuite par `api/main.py`, arrivait trop tard pour
# les réglages préfixés (`MEMOGLYPH_MISTRAL_STT_TRANCHE_S`, `MEMOGLYPH_MLX_PORT`,
# `MEMOGLYPH_ECL_MLX_MODEL`…) : posés dans `.env`, ils étaient ignorés.
_poser_env(_fichier_env())

# --- Modèles ---
# Whisper = moteur STT par défaut (détection de langue fiable)
WHISPER_MODEL = "mlx-community/whisper-large-v3-turbo"
# Mistral API Voxtral Mini STT & Chat
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
# Version ÉPINGLÉE, et non l'alias `voxtral-mini-latest`.
#
# L'alias rendait la provenance inutilisable : un transcript déclarait
# `voxtral-mini-latest`, et deux documents produits à six mois d'écart portaient
# la même chaîne sans venir du même modèle. L'API n'aide pas — son champ `model`
# ÉCHOIE ce qu'on lui envoie, donc demander l'alias fait enregistrer l'alias.
#
# Et l'écart entre versions n'est pas cosmétique. La fiche de
# `Voxtral-Mini-3B-2507` annonce huit langues — anglais, espagnol, français,
# portugais, hindi, allemand, néerlandais, italien — SANS l'arabe. La
# documentation de « Voxtral Mini Transcribe V2 » en annonce treize, AVEC.
#
# `voxtral-mini-2602` est la seule version mini non temps-réel exposée par
# `GET /v1/models` à l'épinglage, donc celle que l'alias servait. VÉRIFIÉ sur un
# extrait arabe de 120 s : l'alias et la version épinglée rendent 13 segments et
# 1 205 caractères, identiques.
#
# Ce que ça coûte, et il faut le dire : une montée de version devient un geste
# manuel. C'est le prix d'un transcript rejouable dans six mois.
MISTRAL_STT_MODEL = os.getenv("MISTRAL_STT_MODEL", "voxtral-mini-2602")
# Modèle de chat par défaut : `ministral-14b-2512`, version épinglée.
#
# Au palier par défaut de Mistral (offre gratuite comprise), `mistral-small`
# répond 429 avec un plafond NUL (`x-ratelimit-limit-req-minute=0`) : le
# profilage et le compte-rendu en cloud échouaient quelle que soit la longueur
# de la réunion. `ministral-14b-2512` est servi au même palier (30 requêtes et
# 937 500 jetons par minute relevés). Toujours réglable par l'environnement.
MISTRAL_CHAT_MODEL_DEFAUT = "ministral-14b-2512"
MISTRAL_CHAT_MODEL = os.getenv("MISTRAL_CHAT_MODEL", MISTRAL_CHAT_MODEL_DEFAUT)
# OpenAI (3e fournisseur de CR, chemin cloud). Modèle ÉPINGLÉ pour la même raison
# que Voxtral : le champ `model` d'un document autoritatif doit nommer une version,
# pas un alias. `gpt-5.6-luna` = modèle de base unique pour toutes les étapes du CR
# (extraction/classif/structure/rendu). Structured Outputs garantit un JSON valide
# côté moteur ; STT OpenAI et routage multi-modèle sont hors périmètre.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_CHAT_MODEL = getenv_compat("OPENAI_CHAT_MODEL", "gpt-5.6-luna"
)
# Anthropic (4e fournisseur, chemin cloud). Identifiant de modèle tel que publié, sans
# alias flottant : `claude-haiku-5-5` par défaut, `claude-sonnet-5-5` au choix.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
# Serveur compatible OpenAI (Ollama, LM Studio, vLLM…) : l'URL de son API (`…/v1`
# compris), le modèle à demander, et une clé facultative. Sans URL ni modèle, le
# fournisseur n'est pas proposé. Relus par `load_env`, comme les clés.
COMPATIBLE_BASE_URL = os.getenv("COMPATIBLE_BASE_URL", "")
COMPATIBLE_MODEL = os.getenv("COMPATIBLE_MODEL", "")
COMPATIBLE_API_KEY = os.getenv("COMPATIBLE_API_KEY", "")
ANTHROPIC_CHAT_MODEL = getenv_compat("ANTHROPIC_CHAT_MODEL", "claude-haiku-5-5")
# Modèle des faits de la réunion (ECL) quand ils passent par Anthropic : Sonnet, plus
# précis que Haiku pour cette extraction. Le compte rendu et l'Assistant gardent
# ANTHROPIC_CHAT_MODEL.
ECL_ANTHROPIC_MODEL = getenv_compat("ECL_ANTHROPIC_MODEL", "claude-sonnet-5-5")
# Modèle natif de pyannote.audio 4.x (auto-suffisant, gated une seule page)
DIARIZATION_MODEL = "pyannote/speaker-diarization-community-1"

# --- Audio ---
SAMPLE_RATE = 16_000  # Hz, mono — ce que consomme Whisper

# --- Transcription progressive (DÉSACTIVÉE par défaut) ---
# Longueur des tranches transcrites l'une après l'autre — affichage du texte au
# fil de l'eau. 0 = une seule passe, ce qui est désormais le défaut.
STT_CHUNK_SEC = float(getenv_compat("STT_CHUNK_SEC", "0"))
# Fenêtre de recherche du meilleur point de coupe (creux d'énergie) autour de la
# frontière visée, pour éviter de couper au milieu d'un mot.
STT_CUT_SEARCH_SEC = 2.0

# --- Chemins ---
OUTPUT_DIR = ROOT / "output"
CACHE_DIR = ROOT / ".cache"

# --- Conservation de l'audio ---
# On archive une copie audio COMPRESSÉE à côté du corpus (le WAV 16k reste le
# format de travail, transitoire : ~115 Mo/h, trop lourd à conserver).
# Sert à re-traiter plus tard (backfill des empreintes, meilleurs modèles) et à
# une future lecture synchronisée. AAC/m4a : ~21 Mo/h et lisible dans tous les
# navigateurs (Safari compris) — MEMOGLYPH_AUDIO_CODEC=libopus pour de l'Opus.
KEEP_AUDIO = getenv_compat("KEEP_AUDIO", "1") not in ("0", "false", "no")
AUDIO_DIR = OUTPUT_DIR / ".audio"
AUDIO_CODEC = getenv_compat("AUDIO_CODEC", "aac")
AUDIO_BITRATE = getenv_compat("AUDIO_BITRATE", "48k")
AUDIO_EXT = {"aac": ".m4a", "libopus": ".opus", "libmp3lame": ".mp3"}.get(AUDIO_CODEC, ".m4a")
# --- Archive FIDÈLE (retraitement) ---
KEEP_FAITHFUL = getenv_compat("KEEP_FAITHFUL", "1") not in ("0", "false", "no")
FAITHFUL_DIRNAME = "faithful"
FAITHFUL_EXT = ".flac"

# Conserver EN PLUS le fichier d'origine (sans perte, mais volumineux).
KEEP_UPLOAD = getenv_compat("KEEP_UPLOAD", "0") not in ("0", "false", "no")

# --- Transport vers les moteurs STT distants ---
# On envoyait le WAV 16 kHz brut : 115 Mo par heure d'audio. L'AAC 64 kbps mono
# divise le volume envoyé par près de quatre en ne changeant presque rien à la
# transcription ; 32 kbps divise davantage mais s'en écarte plus. 64 kbps garde
# donc une vraie marge. Mettre 0 pour envoyer le WAV tel quel.
MISTRAL_UPLOAD_KBPS = int(
    getenv_compat("MISTRAL_UPLOAD_KBPS", "64")
)

# --- Transcription Voxtral découpée ---
# Le palier par défaut de Mistral limite l'audio à 3 600 secondes par minute,
# tous modèles Voxtral confondus : une requête de plus d'une heure d'audio
# dépasse à elle seule la limite et ne passe jamais, quel que soit le nombre
# d'essais. Au-delà de `MISTRAL_STT_TRANCHE_S`, l'audio part donc en tranches
# successives d'au plus cette durée, recouvrement compris. 0 = jamais découper.
# La valeur doit rester sous la limite audio de la minute, sinon chaque tranche
# serait refusée.
MISTRAL_STT_TRANCHE_S = float(getenv_compat("MISTRAL_STT_TRANCHE_S", "3000"))
# Recouvrement entre deux tranches : il évite de couper une phrase net, et c'est
# le temps de parole commun qui y est mesuré qui raccorde les locuteurs d'une
# tranche à l'autre. Il se paie : chaque tranche transcrit ces secondes deux fois.
MISTRAL_STT_RECOUVREMENT_S = float(getenv_compat("MISTRAL_STT_RECOUVREMENT_S", "30"))
# Limite audio du palier, en secondes d'audio par minute glissante. Le palier
# par défaut publie 3 600 (en-tête `x-ratelimit-limit-audio-seconds-minute`) ;
# un palier supérieur peut la relever ici.
MISTRAL_AUDIO_S_PAR_MINUTE = float(getenv_compat("MISTRAL_AUDIO_S_PAR_MINUTE", "3600"))

# --- Formats acceptés ---
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".aac", ".wma", ".opus"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv"}
SUPPORTED_EXTS = AUDIO_EXTS | VIDEO_EXTS

# --- LLM Ollama (hors produit, hors `llm.MOTEURS`) ---
# Seuls les outils d'audit `core/report_audit*` l'appellent encore. Aucun modèle
# par défaut : un appel à Ollama exige `MEMOGLYPH_LLM_MODEL`, et `llm_ollama`
# le dit au lieu d'interroger le serveur avec un nom vide.
LLM_MODEL = getenv_compat("LLM_MODEL", "")

def _normalize_ollama_host(raw: str) -> str:
    """Accepte les DEUX conventions de `OLLAMA_HOST`."""
    host = (raw or "").strip().rstrip("/")
    if not host:
        return "http://127.0.0.1:11434"
    if "://" not in host:
        host = f"http://{host}"
    scheme, sep, rest = host.partition("://")
    for joker, boucle_locale in (("0.0.0.0", "127.0.0.1"), ("[::]", "[::1]")):
        if rest.startswith(joker):
            rest = boucle_locale + rest[len(joker):]
            break
    return f"{scheme}{sep}{rest}"


OLLAMA_HOST = _normalize_ollama_host(os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"))
FACTCHECK_NUMBERS = getenv_compat("FACTCHECK_NUMBERS", "0") not in ("0", "false", "no")

LLM_NUM_CTX_MAX = 32_768  # plafond du contexte (num_ctx) pour les longs transcripts

# --- LLM local MLX (serveur `mlx_lm.server` piloté par core/llm_mlx.py) ---
# Un modèle SANS raisonnement, et volontairement plus petit que l'ancien
# modèle Ollama (qwen3.5:9b). Ce défaut a d'abord été le pendant exact du modèle
# Ollama — au nom du principe que deux moteurs annonçant la même chose doivent
# servir la même chose. Ce principe a cédé devant une mesure.
#
# MESURÉ sur Qwen3.5-9B-4bit : le modèle réfléchit TOUJOURS, `/no_think` n'y
# change rien, et sa réflexion sort dans un champ `reasoning` hors norme
# OpenAI, `content` restant `null`. À 256 jetons il n'avait pas fini de penser
# (`finish_reason=length`, `content` vide). La sonde de vivacité du moteur — une
# génération de deux jetons — n'y voyait donc AUCUN contenu et concluait à un
# serveur zombie : elle TUAIT des serveurs parfaitement sains.
#
# MESURÉ sur Qwen3-4B-Instruct-2507-4bit : les clés du message sont exactement
# `['content', 'role']`. Pas de `reasoning`, pas de `thinking`. La sonde à deux
# jetons rend du contenu, et une réponse française complète prend 1,3 s.
# La famille `Qwen3-*-Instruct-2507` ne fonctionne qu'en mode non-pensant, sans
# rien à désactiver (documentation Qwen, confirmée ici par la mesure).
#
# CE QUE CE CHOIX COÛTE, et qu'il faut dire : comparer une sortie MLX à une
# sortie Ollama ne compare plus deux moteurs, mais aussi 4B contre 9B. Toute
# conclusion sur la QUALITÉ tirée d'une telle comparaison serait fausse. Ce
# défaut sert à éprouver le PIPELINE ; la qualité se réglera en changeant cette
# seule variable, sans toucher au code.
MLX_LLM_MODEL = getenv_compat("MLX_LLM_MODEL", "mlx-community/Qwen3-4B-Instruct-2507-4bit",
)
MLX_HOST = getenv_compat("MLX_HOST", "127.0.0.1")
# Modèle local de l'ECL seule : Granite 4.2 8B, réflexion coupée, le modèle local le
# plus précis avec la consigne « gabarit ». Le compte rendu garde MLX_LLM_MODEL.
# Absent du cache, l'ECL le signale et retombe sur MLX_LLM_MODEL (`core/ecl.py`).
ECL_MLX_MODEL = getenv_compat("ECL_MLX_MODEL", "ibm-granite/granite-4.2-8b-q4-mlx")
# Les faits de la réunion (ECL) : expérimentaux, coupés par défaut ; `MEMOGLYPH_FAITS=1`
# les active. Coupés, ni onglet, ni routes, ni outil MCP `lire_faits`, ni `ecl-extraire`,
# ni réindex des faits au démarrage : aucun modèle des faits n'est chargé. Les fichiers
# `output/.ecl/` restent sur disque, et leur revérification sans modèle à la
# synchronisation aussi : des faits déjà extraits reviennent si l'option est réactivée.
FAITS = getenv_compat("FAITS", "0").strip().lower() in ("1", "true", "yes", "on")

# Plafond de génération du SERVEUR, passé en `--max-tokens`.
#
# MESURÉ : `mlx_lm.server` a un défaut CLI de **512 jetons**
# (`server.py` l. 1842-1846), et une requête qui omet `max_tokens` en hérite
# (l. 1169-1172). Or `LLMEngine.chat()` n'expose aucun moyen de le relever —
# le contrat n'a pas ce paramètre, et Ollama n'en a pas besoin.
#
# Sans ce réglage, tout compte-rendu dépassant 512 jetons était COUPÉ. Avant le
# contrôle de troncature il revenait amputé en silence ; depuis, il échoue avec
# un message qui conseille d'augmenter `max_tokens` — un paramètre que
# l'appelant n'a pas. Mesuré sur un vrai serveur : un compte-rendu de réunion
# ordinaire échouait.
#
# 16384 est la longueur de sortie recommandée par Qwen pour les variantes
# Instruct-2507 en mode non-pensant. C'est un PLAFOND, pas une réservation :
# rien n'est alloué d'avance, et `chat_json` peut toujours demander moins.
MLX_MAX_TOKENS = int(
    getenv_compat("MLX_MAX_TOKENS", "16384")
)

# Le cache de prompt de `mlx_lm.server` est borné en NOMBRE (défaut 10) et pas
# en TAILLE (aucun défaut). Sur nos prompts, cette borne-là ne veut rien dire.
#
# MESURÉ sur cinq comptes-rendus enchaînés sur des transcripts réels :
# prompts de 24 074 jetons, ~900 Mo de KV chacun, et le journal du serveur
# annonce « Prompt Cache: 10 sequences, 8.97 GB » — la moitié des 18 Go de la
# machine, pour un modèle qui n'en pèse que 2,1. La génération suivante meurt
# sur `[METAL] Insufficient Memory`.
#
# Le prix de cette panne est ce qui la rend grave : le thread de génération
# meurt SANS RÉPONDRE, la requête HTTP reste ouverte, et le client attend son
# `MLX_TIMEOUT` entier — trois fois, une par tentative du contrat. Quarante-cinq
# minutes d'attente pour un compte-rendu qui ne viendra pas, sur un serveur qui
# répond encore `/v1/models` en 136 ms et paraît donc parfaitement sain.
#
# 2 Gio laisse UN prompt de compte-rendu en cache, et c'est le cas qui compte :
# les REPRISES du contrat rejouent le même prompt à des températures
# différentes. Deux transcripts distincts, eux, ne partagent que l'en-tête du
# contrat — quelques centaines de jetons, sans intérêt à garder.
#
# ⚠️ Pas « deux prompts », comme le laisserait croire l'estimation de ~900 Mo
# par entrée tirée du journal du serveur. Le journal ne ment pas, mais sa
# moyenne mélange les tranches Map-Reduce (~0,5 Go) et le prompt du
# compte-rendu, bien plus lourd. CALCULÉ depuis le `config.json` du modèle
# (36 couches, 8 têtes KV, head_dim 128, fp16) :
#
#   24 000 jetons → 3,54 Go        12 330 jetons → 1,82 Go        3 400 → 0,50 Go
#
# Un seul prompt de transcript long dépasse donc cette borne à lui seul, et le
# cache l'évincera aussitôt. Ce n'est pas une régression — c'est la borne qui
# fait son travail — mais la marge est plus mince qu'on ne le croirait.
# Graine de génération du compte-rendu. FIXE par défaut : deux exécutions du
# même prompt rendent la même sortie, sur les deux fournisseurs.
#
# Le prix, et il faut le dire : un document qui échoue échouera à l'identique.
# C'est le choix assumé — sans graine, le même transcript a échoué sur TROIS
# modes distincts en trois tirages, et aucune comparaison entre modèles ne
# voulait plus rien dire. La reprise récupère en changeant le PROMPT, pas le
# hasard.
#
# `mlx_lm.server` la nomme `seed` et la VALIDE avant de l'appliquer ; l'API
# Mistral la nomme `random_seed`. La traduction vit dans chaque moteur.
REPORT_SEED = int(
    getenv_compat("REPORT_SEED", "42")
)

# Graine du PROFILAGE, la première étape LLM du pipeline. Elle existe pour une
# brèche qu'il faut dire en entier.
#
# `profiling.profile` était le SEUL appel LLM du tronc sans graine. Or son
# résultat est écrit dans le `.md`, et `report.classify_genre` le relit pour
# choisir le gabarit — donc le nombre de sections, donc tout le compte-rendu.
#
# Rejouer un transcript DÉJÀ profilé fige le bloc de profil sur disque : cela
# vérifie « transcript profilé → compte-rendu », jamais « audio → compte-rendu ».
# Sans graine ici, deux passages du même audio pouvaient diverger dès le profil.
#
# Elle est SÉPARÉE de `REPORT_SEED` plutôt que partagée : une mesure doit pouvoir
# figer une étape et laisser l'autre libre pour isoler d'où vient une variance.
# Même valeur par défaut, deux boutons.
#
# ⚠️ Ce que cette graine ne promet PAS. En local (`mlx_lm.server`), elle est
# appliquée par `mx.random.seed` et la sortie est identique octet pour octet —
# c'est mesuré. Côté Mistral, `random_seed` est un EFFORT du service, pas une
# garantie ; l'identité stricte reste à démontrer, et tant qu'elle ne l'est pas
# le critère cloud est la stabilité FONCTIONNELLE — même genre, même gabarit,
# même structure — et non l'empreinte.
PROFILE_SEED = int(
    getenv_compat("PROFILE_SEED", "42")
)

MLX_PROMPT_CACHE_BYTES = int(
    getenv_compat("MLX_PROMPT_CACHE_BYTES", str(2 * 1024**3),
    )
)

# Port ÉPINGLÉ, et dans la plage Memoglyph (interface 8770, API 8771).
#
# Le défaut de `mlx_lm.server` est 8080 — un port de développement banal, très
# souvent déjà pris. Une collision ne se voit pas : le serveur d'en face répond,
# nos requêtes partent chez lui, et le moteur devient silencieusement FAUX. La
# vérification du modèle servi dans `is_available()` est le second verrou ; ce
# port en est le premier.
#
# 8772 n'entre en conflit avec aucune paire de `start.sh`, qui balaie
# 8770/8771, 8780/8781, … par pas de dix.
MLX_PORT = int(getenv_compat("MLX_PORT", "8772"))

# Délai de la SONDE de disponibilité — une vraie génération d'un jeton.
# Généreux comparé aux 1,2-1,5 s mesurées à chaud, parce qu'il couvre aussi la
# toute première requête d'un serveur fraîchement démarré ; assez court pour que
# `is_available()` ne bloque pas une route de santé.
MLX_SONDE_TIMEOUT = float(
    getenv_compat("MLX_SONDE_TIMEOUT", "60")
)
# Délai d'une génération réelle : travail par lot, pas interactif.
MLX_TIMEOUT = float(getenv_compat("MLX_TIMEOUT", "900"))
# Attente de l'ouverture du port par le processus enfant. Le chargement des
# poids est ÉAGER dans un thread de travail : le port s'ouvre avant lui.
MLX_DEMARRAGE_TIMEOUT = float(
    getenv_compat("MLX_DEMARRAGE_TIMEOUT", "60")
)

# --- Embeddings (recherche sémantique) ---
EMBED_MODEL = getenv_compat("EMBED_MODEL", "nomic-embed-text")

# --- Moteur de vectorisation ---
# `local` (défaut) : modèle embarqué, aucune dépendance à Ollama ni au cloud —
# la recherche par le sens marche donc AUSSI en mode Mistral Cloud, où elle
# était muette. `ollama` conserve l'ancien comportement.
EMBED_BACKEND = getenv_compat("EMBED_BACKEND", "local")

# Règle : le défaut est `large`, qui retrouve mieux le bon passage que `base`
# pour une empreinte et une latence plus élevées. Trois choses à savoir sur ce choix :
#
# 1. L'écart de qualité est constant dans sa direction, mais pas établi
#    statistiquement : il demanderait plus de questions qu'une petite
#    bibliothèque n'en produit.
#
# 2. Le surcoût mémoire réel est de **+168 Mo**, pas les 1,2 Go que laissaient
#    croire `ps rss` et `resource.ru_maxrss` : ceux-ci comptent les pages du
#    fichier de poids, mappées et récupérables par le système. La grandeur qui
#    pèse est l'empreinte physique (`vmmap -summary`).
#
# 3. La dimension passe de 768 à 1024 : la table `vec0` fige la dimension dans
#    son DDL, donc **tout changement de modèle impose une reconstruction
#    complète de l'index sémantique**. `semantic_index_meta` le détecte et lève
#    `SemanticIndexIncompatible` plutôt que de comparer deux espaces vectoriels.
EMBED_LOCAL_MODEL = getenv_compat("EMBED_LOCAL_MODEL", "intfloat/multilingual-e5-large",
)


def _dimension_embeddings() -> int:
    """La dimension DOIT suivre le modèle : la table `vec0` la fige dans son
    schéma, et une valeur erronée ferait échouer chaque insertion."""
    explicite = getenv_compat("EMBED_DIM", "")
    if explicite:
        return int(explicite)
    if EMBED_BACKEND == "local":
        return {
            "intfloat/multilingual-e5-small": 384,
            "intfloat/multilingual-e5-base": 768,
            "intfloat/multilingual-e5-large": 1024,
            "BAAI/bge-m3": 1024,
        }.get(EMBED_LOCAL_MODEL, 768)
    return 768  # nomic-embed-text


EMBED_DIM = _dimension_embeddings()

# --- Signature vocale (empreinte de locuteur / voiceprint) ---
VOICE_EMBED_DIM = 256
VOICE_THRESHOLD = float(getenv_compat("VOICE_THRESHOLD", "0.5"))

# --- Garde-fous de ressources ---
MAX_UPLOAD_MB = int(getenv_compat("MAX_UPLOAD_MB", "2048"))
MAX_ACTIVE_JOBS = int(getenv_compat("MAX_ACTIVE_JOBS", "2"))
MAX_HEAVY_TASKS = int(getenv_compat("MAX_HEAVY_TASKS", "1"))
# Historique des traitements : 50 terminés PAR COMPTE, 200 au total (un compte très actif
# n'efface pas l'historique d'un autre).
MAX_JOB_HISTORY = int(getenv_compat("MAX_JOB_HISTORY", "50"))
MAX_JOB_HISTORY_TOTAL = int(getenv_compat("MAX_JOB_HISTORY_TOTAL", "200"))
# Attente du créneau lourd pour un traitement lancé comme job par l'API : 3 h (un compte rendu
# derrière une transcription longue ne doit pas échouer). Le pipeline en ligne de commande garde
# sa valeur d'avant (900 s).
ATTENTE_DE_FOND_API_S = 3 * 3600

# --- Rapport expérimental (HORS défaut, derrière un drapeau) ---
# Architecture « richer report » : extraction atomique
# bornée en tokens → validation déterministe → classification fermée + veto →
# structure bornée → rendu fidèle par unité → garde de contenance. RIEN de tout
# cela ne s'exécute tant que ce drapeau est à 0 : le pipeline par défaut
# (`core/report.make_report`) reste seul en vigueur, inchangé.
EXPERIMENTAL_REPORT = getenv_compat("EXPERIMENTAL_REPORT", "0"
) not in ("0", "false", "no")
# Borne d'entrée par tranche d'extraction. 2000 = plus grande borne sûre sur
# Qwen3-4B : aucune troncature, horodatages tous valides.
EXPERIMENTAL_CHUNK_TOKENS = int(
    getenv_compat("EXPERIMENTAL_CHUNK_TOKENS", "2000")
)
# Graine de reproductibilité pour TOUTES les étapes LLM du pipeline expérimental.
EXPERIMENTAL_SEED = int(
    getenv_compat("EXPERIMENTAL_SEED", "42")
)

# --- Secrets ---
HF_TOKEN = os.getenv("HF_TOKEN")  # requis uniquement pour la diarisation


def load_env() -> None:
    """Recharge le `.env` (`_poser_env`, déjà fait à l'import) et rafraîchit les clés.

    Une variable déjà exportée l'emporte. Les réglages préfixés sont lus à l'import,
    après la première pose : un `.env` modifié ensuite demande un redémarrage.
    """
    global HF_TOKEN, MISTRAL_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY
    global COMPATIBLE_BASE_URL, COMPATIBLE_MODEL, COMPATIBLE_API_KEY
    if not _poser_env(_fichier_env()):
        return
    HF_TOKEN = os.getenv("HF_TOKEN")
    MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
    # Rafraîchi comme MISTRAL_API_KEY : sans quoi `OpenAIEngine.pret_declare`
    # (qui lit `config.OPENAI_API_KEY`) annoncerait OpenAI indisponible même clé
    # posée, et l'UI ne proposerait jamais le fournisseur.
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    COMPATIBLE_BASE_URL = os.getenv("COMPATIBLE_BASE_URL", "")
    COMPATIBLE_MODEL = os.getenv("COMPATIBLE_MODEL", "")
    COMPATIBLE_API_KEY = os.getenv("COMPATIBLE_API_KEY", "")
