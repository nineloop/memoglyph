"""Extraction / normalisation audio via ffmpeg.

Produit un WAV 16 kHz mono depuis n'importe quelle entrée audio ou vidéo,
prêt à être consommé par le moteur STT.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from . import docid
from .config import CACHE_DIR, SAMPLE_RATE, SUPPORTED_EXTS


class MediaError(RuntimeError):
    """Erreur liée à l'extraction/normalisation média."""


def ensure_ffmpeg() -> None:
    """Vérifie que ffmpeg et ffprobe sont disponibles."""
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise MediaError(
                f"`{tool}` introuvable. Installe-le avec :  brew install ffmpeg"
            )


def validate_input(src: Path) -> Path:
    """Valide l'existence et le format du fichier source."""
    src = Path(src)
    if not src.exists():
        raise MediaError(f"Fichier introuvable : {src}")
    if src.suffix.lower() not in SUPPORTED_EXTS:
        exts = ", ".join(sorted(SUPPORTED_EXTS))
        raise MediaError(
            f"Format non supporté : '{src.suffix}'. Formats acceptés : {exts}"
        )
    return src


def probe_duration(src: Path) -> float:
    """Durée du média en secondes (via ffprobe). 0.0 si indéterminable."""
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(src),
            ],
            capture_output=True, text=True, check=True,
        )
        return float(out.stdout.strip())
    except (subprocess.CalledProcessError, ValueError):
        return 0.0


def to_wav16k(src: Path, cache_dir: Path = CACHE_DIR) -> Path:
    """Convertit `src` (audio OU vidéo) en WAV 16 kHz mono.

    Retourne le chemin du WAV temporaire dans `cache_dir`. Le nom inclut un
    hash du chemin+taille source pour éviter les collisions.
    """
    src = validate_input(src)
    ensure_ffmpeg()
    cache_dir.mkdir(parents=True, exist_ok=True)

    stat = src.stat()
    digest = hashlib.sha1(f"{src.resolve()}:{stat.st_size}".encode()).hexdigest()[:12]
    out_path = cache_dir / f"{src.stem}-{digest}.wav"

    if out_path.exists():
        return out_path  # déjà extrait pour cette source

    cmd = [
        "ffmpeg",
        "-i", str(src),
        "-ac", "1",             # mono
        "-ar", str(SAMPLE_RATE),  # 16 kHz
        "-vn",                  # ignore la vidéo
        "-f", "wav",
        str(out_path),
        "-y",                   # écrase
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        # Dernières lignes de stderr = message ffmpeg pertinent
        tail = "\n".join(proc.stderr.strip().splitlines()[-4:])
        out_path.unlink(missing_ok=True)
        raise MediaError(
            f"Échec de l'extraction audio pour '{src.name}'.\n"
            f"(Fichier sans piste audio ? corrompu ?)\nffmpeg :\n{tail}"
        )
    return out_path


# ---------- Archive audio (conservée à côté du corpus) ----------
#
# Le WAV 16k est un format de TRAVAIL, transitoire (~115 Mo/h : trop lourd à
# garder). On conserve une copie compressée mono : re-traitement ultérieur
# (backfill des empreintes, meilleurs modèles) et future lecture synchronisée.


def _storage_keys(document_ref: str, root: Optional[Path]) -> list[str]:
    """Identité stable d'abord, ancien stem ensuite pour la transition."""
    legacy = Path(document_ref).stem
    if docid.est_valide(document_ref):
        return [document_ref]
    if root is not None:
        return [legacy]
    try:
        from . import library
        item = library.get_transcript(document_ref)
        identifiant = (item or {}).get("meta", {}).get("id")
        if not docid.est_valide(identifiant):
            for info in library._read_manifest().values():
                if info.get("name") == document_ref and docid.est_valide(info.get("document_id")):
                    identifiant = info["document_id"]
                    break
        if docid.est_valide(identifiant):
            return [identifiant, legacy]
    except Exception:  # noqa: BLE001 — store legacy ou non initialisé
        pass
    return [legacy]

def audio_path(transcript_name: str, root: Optional[Path] = None) -> Path:
    """Chemin de l'archive audio d'un transcript (clé = nom du .md).

    Cherche d'abord un fichier EXISTANT quelle que soit son extension : changer
    `MEMOGLYPH_AUDIO_CODEC` ne doit pas rendre invisibles les archives déjà là.
    """
    from .config import AUDIO_DIR, AUDIO_EXT

    d = (root / ".audio") if root is not None else AUDIO_DIR
    keys = _storage_keys(transcript_name, root)
    for key in keys:
        for ext in (AUDIO_EXT, ".m4a", ".opus", ".mp3", ".ogg", ".wav"):
            cand = d / (key + ext)
            if cand.exists():
                return cand
    return d / (keys[0] + AUDIO_EXT)


def faithful_path(transcript_name: str, root: Optional[Path] = None) -> Path:
    """Chemin de l'archive FIDÈLE (16 kHz mono sans perte), pour le retraitement.

    Sous-dossier dédié plutôt qu'une extension de plus dans `.audio/` : les deux
    fichiers ont des rôles distincts — l'un pour les oreilles, l'autre pour les
    modèles — et un dossier séparé rend l'inventaire et la purge évidents.
    """
    from .config import AUDIO_DIR, FAITHFUL_DIRNAME, FAITHFUL_EXT

    d = (root / ".audio") if root is not None else AUDIO_DIR
    directory = d / FAITHFUL_DIRNAME
    keys = _storage_keys(transcript_name, root)
    for key in keys:
        candidate = directory / (key + FAITHFUL_EXT)
        if candidate.exists():
            return candidate
    return directory / (keys[0] + FAITHFUL_EXT)


def pcm_sha256(src: Path) -> str:
    """Empreinte du PCM exact que STT et diarisation consomment.

    Le flux est lu par blocs : une heure en s16 mono représente environ 115 Mo,
    qu'il ne faut jamais charger intégralement en mémoire pour une vérification.
    """
    src = validate_input(src)
    ensure_ffmpeg()
    proc = subprocess.Popen(
        [
            "ffmpeg", "-v", "error", "-i", str(src), "-vn",
            "-ac", "1", "-ar", str(SAMPLE_RATE),
            "-c:a", "pcm_s16le", "-f", "s16le", "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdout is not None
    digest = hashlib.sha256()
    for chunk in iter(lambda: proc.stdout.read(1024 * 1024), b""):
        digest.update(chunk)
    proc.stdout.close()
    stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
    returncode = proc.wait()
    if returncode != 0:
        raise MediaError(f"Décodage PCM impossible pour '{src.name}' : {stderr.strip()}")
    return digest.hexdigest()


def create_faithful(
    src: Path,
    destination: Path,
    *,
    verify_pcm: bool = True,
    overwrite: bool = False,
) -> Path:
    """Crée un FLAC fidèle puis le publie atomiquement.

    En mode backfill, `overwrite=False` garantit qu'une archive apparue entre le
    plan et l'application n'est jamais remplacée. Le lien dur publie alors le
    temporaire en une seule opération qui échoue si la destination existe déjà.
    """
    src = validate_input(src)
    ensure_ffmpeg()
    destination = Path(destination)
    if destination.exists() and not overwrite:
        raise MediaError(f"Archive fidèle déjà présente : {destination.name}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    descriptor, raw_temp = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp.flac", dir=destination.parent,
    )
    os.close(descriptor)
    temp = Path(raw_temp)
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-i", str(src), "-vn",
                "-ac", "1", "-ar", str(SAMPLE_RATE),
                "-sample_fmt", "s16", "-c:a", "flac",
                str(temp), "-y",
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise MediaError(
                f"Encodage fidèle impossible pour '{src.name}' : {proc.stderr.strip()}"
            )
        if verify_pcm and pcm_sha256(src) != pcm_sha256(temp):
            raise MediaError(f"Archive fidèle non identique au PCM de '{src.name}'")

        with temp.open("rb") as stream:
            os.fsync(stream.fileno())
        temp.chmod(0o644)
        if overwrite:
            os.replace(temp, destination)
        else:
            try:
                os.link(temp, destination)
            except FileExistsError as exc:
                raise MediaError(
                    f"Archive fidèle apparue pendant le traitement : {destination.name}"
                ) from exc
            temp.unlink()

        directory_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return destination
    finally:
        temp.unlink(missing_ok=True)


def archive_faithful(
    src: Path, transcript_name: str, root: Optional[Path] = None
) -> Optional[Path]:
    """Archive SANS PERTE à la résolution que les modèles consomment.

    Pourquoi 16 kHz mono et pas le média entier : `to_wav16k` réduit tout à
    `-ac 1 -ar 16000` avant STT comme avant diarisation. Conserver davantage ne
    changerait aucun résultat, pour un volume bien supérieur. Conserver moins —
    c'est le cas de l'archive d'écoute — change les résultats : après un
    aller-retour en AAC 48 kbps, la diarisation perd des locuteurs.

    Best-effort : ne fait jamais échouer une transcription.
    """
    from .config import KEEP_FAITHFUL

    if not KEEP_FAITHFUL:
        return None
    dest = faithful_path(transcript_name, root)
    try:
        # La transcription reste best-effort et évite le coût d'une seconde
        # lecture complète. Le backfill, lui, appelle `create_faithful` avec la
        # vérification PCM obligatoire avant publication.
        return create_faithful(src, dest, verify_pcm=False, overwrite=True)
    except (MediaError, OSError):
        return None


def archive_audio(
    src: Path, transcript_name: str, root: Optional[Path] = None
) -> Optional[Path]:
    """Encode une copie audio compressée du média source. None si désactivé/échec.

    `root` : racine de sortie (le dossier du corpus). Permet à un appelant isolé
    (tests) de ne pas écrire dans les emplacements globaux.
    """
    from .config import AUDIO_BITRATE, AUDIO_CODEC, KEEP_AUDIO

    if not KEEP_AUDIO:
        return None
    ensure_ffmpeg()
    dest = audio_path(transcript_name, root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-i", str(src),
        "-vn",                       # pas de vidéo
        "-ac", "1",                  # mono : c'est de la parole
        "-c:a", AUDIO_CODEC,
        "-b:a", AUDIO_BITRATE,
        str(dest), "-y",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        dest.unlink(missing_ok=True)
        return None  # best-effort : ne jamais faire échouer une transcription
    return dest


def keep_original(src: Path, transcript_name: str, root: Optional[Path] = None) -> Optional[Path]:
    """Conserve le média SOURCE tel quel si MEMOGLYPH_KEEP_UPLOAD=1.

    L'archive compressée suffit dans la plupart des cas ; cette option est là
    pour qui veut du sans-perte (re-diariser depuis du compressé altère très
    légèrement les empreintes).
    """
    from .config import AUDIO_DIR, KEEP_UPLOAD

    if not KEEP_UPLOAD or not src.exists():
        return None
    d = (root / ".audio") if root is not None else AUDIO_DIR
    dest = d / "originals" / f"{_storage_keys(transcript_name, root)[0]}{src.suffix}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(src, dest)
    except Exception:  # noqa: BLE001 — best-effort, jamais bloquant
        return None
    return dest


def original_path(transcript_name: str, root: Optional[Path] = None) -> Optional[Path]:
    """Média source conservé pour ce transcript, s'il y en a un."""
    from .config import AUDIO_DIR

    d = ((root / ".audio") if root is not None else AUDIO_DIR) / "originals"
    if not d.exists():
        return None
    for key in _storage_keys(transcript_name, root):
        for p in d.glob(f"{key}.*"):
            return p
    return None


def drop_audio(transcript_name: str, root: Optional[Path] = None) -> None:
    """Supprime TOUTES les archives audio d'un transcript, et l'original conservé.

    On balaie chaque extension connue : n'en supprimer qu'une laisserait des
    traces si le codec a changé entre deux enregistrements.
    """
    from .config import FAITHFUL_DIRNAME
    # `audio_path` reste le point de personnalisation historique (tests et
    # installations ayant déplacé l'archive). Sa réponse fixe le dossier.
    resolved = audio_path(transcript_name, root)
    directory = resolved.parent
    for key in _storage_keys(transcript_name, root):
        for ext in (".m4a", ".opus", ".mp3", ".ogg", ".wav", ".flac"):
            (directory / f"{key}{ext}").unlink(missing_ok=True)
        (directory / FAITHFUL_DIRNAME / f"{key}.flac").unlink(missing_ok=True)
    resolved.unlink(missing_ok=True)
    orig = original_path(transcript_name, root)
    if orig is not None:
        orig.unlink(missing_ok=True)


# ---------- Découpe d'audio ----------
#
# Deux usages partagent la même primitive « couper entre deux instants » :
#
# 1. Borner la durée pour les ESSAIS. Réitérer un essai sur une réunion de
#    55 minutes coûte une heure à chaque passe ; sur un extrait de 10 minutes,
#    quelques minutes.
# 2. Le COLLAGE des passages saillants. Chaque affirmation d'un compte-rendu
#    porte l'horodatage de sa preuve (`[source 00:07:44]`) : on peut donc en
#    extraire un condensé audio.
#
# ATTENTION à l'usage 1 : découper AVANT transcription rend la diarisation
# locale à chaque tranche. « Speaker 1 » de la tranche 1 et de la tranche 3 ne
# sont plus la même personne par construction — c'est le clustering d'empreintes
# qui doit les relier. Pour éprouver JUSTEMENT cette capacité (l'identité des
# voix sur toute la réunion), transcrire la réunion ENTIÈRE. Découper est fait pour itérer vite sur
# ce qui ne dépend pas de l'identité globale des locuteurs.


def secondes_depuis_horodatage(valeur: str) -> float:
    """« 00:07:44 » ou « 7:44 » -> secondes. Lève sur une forme inattendue.

    Refuser plutôt que deviner : un horodatage mal lu produirait un extrait
    silencieusement décalé, et personne ne s'en apercevrait à l'écoute d'un
    collage de trente extraits.
    """
    morceaux = str(valeur).strip().strip("[]").split(":")
    if not 2 <= len(morceaux) <= 3 or not all(m.isdigit() for m in morceaux):
        raise ValueError(f"Horodatage illisible : {valeur!r}")
    nombres = [int(m) for m in morceaux]
    if len(nombres) == 2:
        nombres.insert(0, 0)
    heures, minutes, secondes = nombres
    if minutes > 59 or secondes > 59:
        raise ValueError(f"Horodatage hors bornes : {valeur!r}")
    return heures * 3600 + minutes * 60 + secondes


def extraire(
    src: Path, debut: float, fin: float, destination: Path,
    *, codec: str = "aac", debit: str = "128k",
) -> Path:
    """Extrait `[debut, fin[` de `src` vers `destination`.

    Ré-encode plutôt que de copier le flux : une copie sans ré-encodage se cale
    sur l'image-clé la plus proche, ce qui décale le début de plusieurs secondes.
    Sur un extrait de 8 secondes, ce décalage rate la citation.
    """
    src = validate_input(src)
    ensure_ffmpeg()
    if fin <= debut:
        raise ValueError(f"Intervalle vide ou inversé : [{debut}, {fin}[")
    destination.parent.mkdir(parents=True, exist_ok=True)
    resultat = subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{debut:.3f}", "-t", f"{fin - debut:.3f}",
         "-i", str(src), "-vn", "-c:a", codec, "-b:a", debit, str(destination)],
        capture_output=True, text=True,
    )
    if resultat.returncode != 0 or not destination.exists():
        raise RuntimeError(
            f"Extraction impossible ({debut:.1f}s→{fin:.1f}s) : "
            f"{resultat.stderr[-300:]}"
        )
    return destination


def decouper(
    src: Path, dossier: Path, *, duree_max: float = 600.0, recouvrement: float = 0.0,
) -> list[Path]:
    """Découpe `src` en tranches d'au plus `duree_max` secondes (10 min par défaut).

    `recouvrement` évite qu'une phrase soit coupée net entre deux tranches. Il a
    un COÛT : le total transcrit dépasse la durée réelle d'autant.

    Rappel : ne pas utiliser pour éprouver l'identité des voix sur toute une
    réunion — voir l'avertissement en tête de section.
    """
    from .config import AUDIO_EXT

    _valider_decoupe(duree_max, recouvrement)
    bornes = bornes_tranches(probe_duration(src), duree_max, recouvrement)
    return [
        extraire(src, debut, fin, dossier / f"{src.stem}.{index:03d}{AUDIO_EXT}")
        for index, (debut, fin) in enumerate(bornes, start=1)
    ]


def _valider_decoupe(duree_max: float, recouvrement: float) -> None:
    if duree_max <= 0:
        raise ValueError("La durée maximale doit être strictement positive")
    if recouvrement < 0 or recouvrement >= duree_max:
        raise ValueError("Le recouvrement doit être positif et plus court qu'une tranche")


def bornes_tranches(
    duree: float, duree_max: float, recouvrement: float = 0.0,
) -> list[tuple[float, float]]:
    """Bornes `(début, fin)` des tranches de `decouper`, sans toucher au fichier.

    La tranche `i` commence à `i × (duree_max − recouvrement)` : elle reprend les
    `recouvrement` dernières secondes de la précédente. Séparée de `decouper` pour
    que la transcription découpée connaisse le décalage de chaque tranche, et pour
    qu'on puisse la vérifier sans ffmpeg.
    """
    _valider_decoupe(duree_max, recouvrement)
    pas = duree_max - recouvrement
    bornes: list[tuple[float, float]] = []
    debut = 0.0
    while debut < duree:
        fin = min(debut + duree_max, duree)
        # Une tranche résiduelle d'une poignée de secondes n'apporte rien et
        # produit un fichier presque vide : on l'agrège à la précédente.
        if duree - fin < 1.0:
            fin = duree
        bornes.append((debut, fin))
        if fin >= duree:
            break
        debut += pas
    return bornes


def assembler(
    src: Path, intervalles: list[tuple[float, float]], destination: Path,
    *, silence: float = 0.35, codec: str = "aac", debit: str = "128k",
) -> Path:
    """Concatène plusieurs intervalles de `src` en un seul fichier.

    Sert à rassembler toute la parole d'un locuteur dispersée sur une réunion :
    quatre tours de trois secondes s'écoutent mal séparément, et c'est parfois
    tout ce dont on dispose pour décider si deux enregistrements portent la même
    voix.

    `silence` insère un blanc entre les morceaux. Sans lui, deux phrases prises
    à vingt minutes d'écart se collent en une seule et l'oreille entend une
    coupure là où il n'y en a pas — on juge alors le montage, pas la voix.
    """
    src = validate_input(src)
    ensure_ffmpeg()
    if not intervalles:
        raise ValueError("Aucun intervalle à assembler")
    for debut, fin in intervalles:
        if fin <= debut:
            raise ValueError(f"Intervalle vide ou inversé : [{debut}, {fin}[")
    destination.parent.mkdir(parents=True, exist_ok=True)

    filtres, etiquettes = [], []
    for i, (debut, fin) in enumerate(intervalles):
        filtres.append(
            f"[0:a]atrim=start={debut:.3f}:end={fin:.3f},asetpts=PTS-STARTPTS[p{i}]"
        )
        etiquettes.append(f"[p{i}]")
        if silence > 0 and i < len(intervalles) - 1:
            filtres.append(
                f"aevalsrc=0:d={silence:.3f}:s=16000:c=mono[b{i}]"
            )
            etiquettes.append(f"[b{i}]")
    filtres.append(
        "".join(etiquettes) + f"concat=n={len(etiquettes)}:v=0:a=1[out]"
    )
    resultat = subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-filter_complex", ";".join(filtres),
         "-map", "[out]", "-ac", "1", "-c:a", codec, "-b:a", debit, str(destination)],
        capture_output=True, text=True,
    )
    if resultat.returncode != 0 or not destination.exists():
        raise RuntimeError(
            f"Assemblage impossible ({len(intervalles)} morceaux) : "
            f"{resultat.stderr[-300:]}"
        )
    return destination


def enveloppe(src: Path, *, pas: float = 0.02) -> tuple["object", float]:
    """Énergie par trame de `pas` secondes, en dBFS. Rend (trames, pas).

    Décoder le fichier une fois et interroger l'enveloppe ensuite coûte moins
    cher que de sonder chaque fenêtre candidate séparément : une réunion d'une
    heure tient en 180 000 trames.
    """
    import numpy as np

    src = validate_input(src)
    ensure_ffmpeg()
    taux = 16000
    resultat = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(src), "-f", "f32le",
         "-ac", "1", "-ar", str(taux), "-"],
        capture_output=True,
    )
    if resultat.returncode != 0:
        raise RuntimeError(f"Décodage impossible : {resultat.stderr[-300:]!r}")
    ech = np.frombuffer(resultat.stdout, dtype=np.float32)
    n = max(1, int(taux * pas))
    trames = ech[: len(ech) // n * n].reshape(-1, n)
    rms = np.sqrt((trames.astype(np.float64) ** 2).mean(axis=1))
    return 20.0 * np.log10(np.maximum(rms, 1e-10)), pas


def densite_vocale(
    env: tuple["object", float], debut: float, fin: float, *, seuil_db: float = -45.0,
) -> float:
    """Fraction de `[debut, fin[` où le signal dépasse `seuil_db`.

    Le transcript dit qu'on a parlé à cet instant ; il ne dit pas combien de
    temps. Un « Yes. » suivi de dix secondes de silence occupe le même
    intervalle qu'une phrase entière, et une fenêtre taillée dessus s'écoute
    vide. On mesure donc le signal plutôt que de faire confiance aux bornes.
    """
    trames, pas = env
    a = max(0, int(debut / pas))
    b = min(len(trames), int(fin / pas))
    if b <= a:
        return 0.0
    return float((trames[a:b] > seuil_db).mean())
