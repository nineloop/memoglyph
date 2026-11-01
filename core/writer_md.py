"""Génération du fichier Markdown de sortie (front-matter YAML + corps)."""

from __future__ import annotations

import base64
import re
import struct
from pathlib import Path

try:  # même dépendance optionnelle que la lecture (core/library.py)
    import yaml
except ImportError:  # pragma: no cover - repli manuel ci-dessous
    yaml = None  # type: ignore[assignment]

from .config import OUTPUT_DIR
from .stt_base import Transcript


def encode_embedding(vec: list[float]) -> str:
    """Vecteur float -> chaîne base64 (float32 little-endian), compacte pour le YAML."""
    return base64.b64encode(struct.pack(f"<{len(vec)}f", *vec)).decode("ascii")


def decode_embedding(s: str) -> list[float]:
    """Inverse de `encode_embedding` : base64 -> liste de floats."""
    raw = base64.b64decode(s)
    return list(struct.unpack(f"<{len(raw) // 4}f", raw))


def slugify(name: str) -> str:
    """Nom de fichier sûr : minuscules, tirets, sans accents parasites."""
    name = name.strip().lower()
    name = re.sub(r"[^\w\s-]", "", name, flags=re.UNICODE)
    name = re.sub(r"[\s_]+", "-", name)
    name = re.sub(r"-+", "-", name).strip("-")
    return name or "transcript"


def fmt_timestamp(seconds: float) -> str:
    """Secondes -> [hh:mm:ss]."""
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def fmt_duration(seconds: float) -> str:
    """Durée totale hh:mm:ss (pour le front-matter)."""
    return fmt_timestamp(seconds)


def _quote(value: str) -> str:
    """Repli manuel quand PyYAML manque : guillemets doubles correctement échappés."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _yaml_frontmatter(meta: dict) -> str:
    """Front-matter YAML.

    Sérialisé par PyYAML — le MÊME analyseur que celui qui relit (`library.
    _split_frontmatter`). L'écriture était auparavant faite à la main : une valeur
    contenant un guillemet, par exemple un rôle LLM tel que `interviewé
    "principal"`, produisait du YAML invalide, et la relecture retombait
    silencieusement sur `{}` — langue, modèle, projet et statut de profilage
    perdus d'un coup. Écrire et lire avec le même outil rend la classe d'erreur
    impossible plutôt que rare.
    """
    clean = {k: v for k, v in meta.items() if v is not None}
    if yaml is not None:
        # Style choisi CLÉ PAR CLÉ, et c'est une correction.
        #
        # `default_flow_style=None` laisse PyYAML décider par collection : bloc
        # si elle contient une collection imbriquée, compact sinon. Le
        # front-matter d'un transcript contient `speakers`, donc il restait en
        # blocs — mais celui d'un COMPTE-RENDU est plat, et il est parti sur UNE
        # SEULE LIGNE le jour où le désarmement du vérificateur numérique lui a
        # retiré sa dernière liste. YAML valide, relu correctement par
        # `library`, mais illisible à l'œil et insaisissable par un `grep
        # "^language:"` — or le `.md` est la source de vérité, faite pour être
        # lue et outillée à la ligne.
        #
        # On force donc le BLOC à la racine (scalaire → `clé: valeur`) tout en
        # laissant les feuilles compactes (`Speaker 1: {talk_time: …}`), ce qui
        # était l'intention d'origine. Sur un fichier à 14 locuteurs : 20 lignes
        # ainsi, contre 47 en tout-bloc et 1 seule en tout-compact.
        #
        # PyYAML place lui-même les guillemets là où le piège sexagésimal YAML
        # 1.1 existe (`'12:30:00'`) et les omet quand c'est sûr (`00:13:41`) —
        # vérifié sur les deux styles.
        morceaux = []
        for cle, valeur in clean.items():
            morceaux.append(
                yaml.safe_dump(
                    {cle: valeur},
                    sort_keys=False,       # l'ordre porte du sens pour un lecteur humain
                    allow_unicode=True,    # pas d'échappement \uXXXX sur les accents
                    default_flow_style=None if isinstance(valeur, (dict, list)) else False,
                    width=10_000,          # pas de repli de ligne sur les titres longs
                ).rstrip("\n")
            )
        return "---\n" + "\n".join(morceaux) + "\n---"
    lines = ["---"]
    for key, value in clean.items():
        if isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif isinstance(value, (int, float)):
            lines.append(f"{key}: {value}")
        else:
            lines.append(f"{key}: {_quote(str(value))}")
    lines.append("---")
    return "\n".join(lines)


def _speakers_meta(profile: dict) -> dict:
    """`{label: {role, talk_time, share}}` — renvoyé en DONNÉES, pas en texte.

    Le bloc était auparavant rendu en chaîne puis recollé dans le front-matter
    par découpage de texte. Le produire comme dict laisse la sérialisation à
    PyYAML, donc échappée pour de bon.
    """
    speakers = profile.get("speakers") or {}
    out: dict[str, dict] = {}
    for label, info in speakers.items():
        compact = {k: info.get(k) for k in ("role", "talk_time", "share")}
        out[label] = {k: v for k, v in compact.items() if v is not None}
    return out


# Libellés de sections localisés (les VALEURS sont produites par le LLM dans la
# langue de la transcription ; ici on aligne les libellés structurels).
_LABELS = {
    "fr": {"colon": " :", "conv": "🗣️ Profil de la conversation", "type": "Type",
           "duration": "durée", "speakers": "locuteurs", "turns": "tours",
           "topics": "Thèmes", "speakers_sec": "👥 Profils des locuteurs",
           "tone": "Ton", "transcript": "Transcript"},
    "en": {"colon": ":", "conv": "🗣️ Conversation profile", "type": "Type",
           "duration": "duration", "speakers": "speakers", "turns": "turns",
           "topics": "Topics", "speakers_sec": "👥 Speaker profiles",
           "tone": "Tone", "transcript": "Transcript"},
}


def _labels(language: str | None) -> dict:
    return _LABELS.get((language or "fr").lower(), _LABELS["fr"])


def _render_profile_sections(profile: dict, diarized: bool, lang: str | None = None) -> list[str]:
    """Sections Markdown des profils (conversation + locuteurs)."""
    L = _labels(lang)
    c = L["colon"]
    out: list[str] = []
    conv = profile.get("conversation") or {}
    cstats = profile.get("conversation_stats") or {}

    out += [f"## {L['conv']}", ""]
    if conv.get("type"):
        out.append(f"**{L['type']}{c}** {conv['type']}")
    meta_bits = []
    if cstats.get("duration"):
        meta_bits.append(f"{L['duration']} {cstats['duration']}")
    if cstats.get("n_speakers"):
        meta_bits.append(f"{cstats['n_speakers']} {L['speakers']}")
    if cstats.get("n_turns"):
        meta_bits.append(f"{cstats['n_turns']} {L['turns']}")
    if meta_bits:
        out.append(f"*{' · '.join(meta_bits)}*")
    if conv.get("summary"):
        out += ["", conv["summary"]]
    topics = conv.get("topics") or []
    if topics:
        out += ["", f"**{L['topics']}{c}** " + " · ".join(topics)]
    out.append("")

    speakers = profile.get("speakers") or {}
    if diarized and speakers:
        out += [f"## {L['speakers_sec']}", ""]
        for label, info in speakers.items():
            head_bits = [b for b in (info.get("talk_time"), info.get("share")) if b]
            if info.get("turns"):
                head_bits.append(f"{info['turns']} {L['turns']}")
            # Sans rôle, on écrit « Speaker 1 » et non « Speaker 1 — — » : le
            # garde-fou de nommage retire les rôles dès 9 locuteurs, et le
            # gabarit laissait alors un tiret orphelin sur CHAQUE ligne.
            role = info.get("role")
            titre = f"{label} — {role}" if role else label
            suffix = f" · {' · '.join(head_bits)}" if head_bits else ""
            out.append(f"**{titre}**{suffix}")
            if info.get("tone"):
                out.append(f"- *{L['tone']}{c}* {info['tone']}")
            for kp in info.get("key_points") or []:
                out.append(f"- {kp}")
            out.append("")
    return out


def render_markdown(
    transcript: Transcript,
    meta: dict,
    *,
    timestamps: bool = True,
    diarized: bool = False,
    profile: dict | None = None,
) -> str:
    """Assemble le document Markdown complet."""
    title = meta.get("title", "transcript")
    # Un seul dict sérialisé d'un bloc : les locuteurs ne sont plus recollés dans
    # le front-matter par découpage de texte.
    # NB : les empreintes vocales ne sont PAS écrites ici (1,4 ko de base64 par
    # locuteur = pavé illisible) — elles vont dans output/.voiceprints.json.
    full_meta = dict(meta)
    speakers = _speakers_meta(profile) if profile else {}
    if speakers:
        full_meta["speakers"] = speakers
    front = _yaml_frontmatter(full_meta)

    lang = meta.get("language")
    body_lines: list[str] = [f"# {title}", ""]

    if profile:
        body_lines += _render_profile_sections(profile, diarized, lang)
        body_lines += [f"## {_labels(lang)['transcript']}", ""]

    if diarized:
        # Regroupe les phrases consécutives d'un même locuteur.
        for seg in transcript.segments:
            speaker = seg.speaker or "Speaker ?"
            ts = f"[{fmt_timestamp(seg.start)}] " if timestamps else ""
            body_lines.append(f"**{speaker}** {ts}{seg.text}")
            body_lines.append("")
    elif timestamps:
        for seg in transcript.segments:
            body_lines.append(f"[{fmt_timestamp(seg.start)}] {seg.text}")
            body_lines.append("")
    else:
        # Texte brut : phrases regroupées en paragraphes fluides.
        body_lines.append(" ".join(s.text for s in transcript.segments))

    return f"{front}\n\n" + "\n".join(body_lines).rstrip() + "\n"


def _taken_names() -> set[str]:
    """Noms de .md déjà pris, tous projets confondus — **corbeille comprise**.

    Un nom archivé reste réservé : sinon une nouvelle transcription réutiliserait
    sa clé, et les deux générations partageraient audio et empreintes (vider la
    corbeille effacerait alors les artefacts du nouveau transcript).
    """
    if not OUTPUT_DIR.exists():
        return set()
    taken = {
        p.name for p in OUTPUT_DIR.rglob("*.md")
        if ".trash" not in p.relative_to(OUTPUT_DIR).parts
    }
    from . import library  # local : évite un cycle à l'import du module
    return taken | library.trash_names()


def unique_path(base_name: str, output_dir: Path = OUTPUT_DIR) -> Path:
    """Chemin .md non-collision : ajoute -1, -2… si nécessaire.

    `output_dir` = dossier du projet cible. L'unicité est vérifiée sur **tous les
    projets** : le nom de fichier reste la clé du transcript (URLs/API stables).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    # Unicité globale (tous projets) ET dans le dossier cible — ce dernier peut
    # être hors de output/ (tests, export) : sans lui on écraserait un fichier.
    taken = _taken_names() | {p.name for p in output_dir.glob("*.md")}
    stem = slugify(base_name)
    candidate = f"{stem}.md"
    i = 1
    while candidate in taken:
        candidate = f"{stem}-{i}.md"
        i += 1
    return output_dir / candidate


def write_md(content: str, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    return out_path
