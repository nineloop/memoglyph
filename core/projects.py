"""Identité persistante des projets Memoglyph.

Un dossier est un emplacement modifiable, pas une identité. Chaque projet porte
donc un manifeste `.memoglyph-project.yaml` avec un ULID. Le manifeste vit dans
`output/` avec les Markdown autoritaires ; SQLite ne fait que l'indexer plus
tard.

La migration de l'existant est en simulation par défaut. Un manifeste invalide
n'est jamais remplacé silencieusement : il faut d'abord comprendre le conflit.

Version 2 du manifeste : le champ `owner` porte l'identifiant du compte
propriétaire. Un manifeste en version 1 reste lu, sans propriétaire :
c'est un projet d'avant les comptes, visible des seuls administrateurs jusqu'à
son attribution (`memoglyph attribuer-bibliotheque`).
"""

from __future__ import annotations

import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Collection, Optional, Union

import yaml

from . import docid

MANIFEST_NAME = ".memoglyph-project.yaml"
MANIFEST_VERSION = 2
_VERSIONS_LUES = (1, 2)
INBOX_NAME = "Non classé"
_KINDS = {"inbox", "regular"}
_LOCK = threading.RLock()


class ProjectManifestError(ValueError):
    """Manifeste absent, illisible ou contradictoire."""


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    created_at: str
    kind: str
    version: int = MANIFEST_VERSION
    owner: Optional[str] = None

    def payload(self) -> dict:
        # `version` en tête : un lecteur sait immédiatement quel contrat lire.
        raw = asdict(self)
        return {"version": raw.pop("version"), **raw}


def meme_compte(a: Optional[str], b: Optional[str]) -> bool:
    """Deux identifiants de compte désignent-ils le même compte ?

    Les identifiants sont uniques sans égard à la casse (`auth`), et aucun
    identifiant ne vaut « sans propriétaire ».
    """
    return a is not None and b is not None and a.casefold() == b.casefold()


def manifest_path(directory: Path) -> Path:
    return Path(directory) / MANIFEST_NAME


def load(directory: Path) -> Project:
    """Lit et valide un manifeste existant."""
    path = manifest_path(directory)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ProjectManifestError(f"Manifeste projet illisible : {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProjectManifestError(f"Manifeste projet invalide : {path}")
    if raw.get("version") not in _VERSIONS_LUES:
        raise ProjectManifestError(
            f"Version de manifeste non prise en charge dans {path}: {raw.get('version')!r}"
        )
    if not docid.est_valide(raw.get("id")):
        raise ProjectManifestError(f"Identifiant projet invalide dans {path}")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ProjectManifestError(f"Nom de projet invalide dans {path}")
    kind = raw.get("kind")
    if kind not in _KINDS:
        raise ProjectManifestError(f"Type de projet invalide dans {path}: {kind!r}")
    created_at = raw.get("created_at")
    if not created_at:
        raise ProjectManifestError(f"Date de création absente dans {path}")
    owner = raw.get("owner") if raw["version"] >= 2 else None
    if owner is not None and (not isinstance(owner, str) or not owner.strip()):
        raise ProjectManifestError(f"Propriétaire invalide dans {path}: {owner!r}")
    return Project(
        id=raw["id"], name=name.strip(), created_at=str(created_at),
        kind=kind, version=raw["version"], owner=owner,
    )


def _write(directory: Path, project: Project) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = manifest_path(directory)
    tmp = directory / f"{MANIFEST_NAME}.{os.getpid()}.{threading.get_ident()}.tmp"
    text = yaml.safe_dump(
        project.payload(), sort_keys=False, allow_unicode=True, default_flow_style=False,
    )
    try:
        with tmp.open("x", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _new(name: str, kind: str, owner: Optional[str] = None) -> Project:
    return Project(
        id=docid.nouveau(),
        name=name,
        created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        kind=kind,
        owner=owner,
    )


def create(
    directory: Path, *, name: str, kind: str = "regular", owner: Optional[str] = None,
) -> Project:
    """Crée un projet ou relit l'identité déjà présente dans ce dossier."""
    if kind not in _KINDS:
        raise ProjectManifestError(f"Type de projet invalide : {kind!r}")
    with _LOCK:
        path = manifest_path(directory)
        if path.exists():
            existing = load(directory)
            if (existing.name != name or existing.kind != kind
                    or (existing.owner or owner) and not meme_compte(existing.owner, owner)):
                raise ProjectManifestError(
                    f"Le dossier {directory} appartient déjà au projet {existing.name!r}"
                )
            return existing
        project = _new(name, kind, owner)
        _write(directory, project)
        return project


def create_owned(
    root: Path, *, name: str, owner: str, kind: str = "regular", dossier: Optional[str] = None,
) -> tuple[Path, Project]:
    """Crée le projet `name` de `owner` dans un dossier libre de `root`.

    Le dossier s'appelle `dossier`, par défaut `name`. Deux comptes peuvent
    nommer un projet de la même façon : le dossier prend alors le nom du
    propriétaire en suffixe, le nom affiché reste `name`. Un projet de même nom
    déjà détenu par `owner` est rendu tel quel. Le dossier d'un autre compte
    n'est jamais ni réutilisé ni nommé dans l'erreur.
    """
    root = Path(root)
    base = dossier or name
    with _LOCK:
        for directory in (root / base, root / f"{base} ({owner})"):
            if not manifest_path(directory).exists():
                if directory.exists() and any(directory.iterdir()):
                    continue
                return directory, create(directory, name=name, kind=kind, owner=owner)
            existing = load(directory)
            if existing.name == name and existing.kind == kind and meme_compte(existing.owner, owner):
                return directory, existing
    raise ProjectManifestError(f"Impossible de créer le projet {name!r} : nom indisponible")


def _avec_proprietaire(project: Project, owner: str) -> Project:
    """Le même projet, même identité, attribué à `owner`, en manifeste version 2."""
    return Project(
        id=project.id, name=project.name, created_at=project.created_at,
        kind=project.kind, version=MANIFEST_VERSION, owner=owner,
    )


def all_projects(root: Path) -> list[tuple[Path, Project]]:
    """Les projets dotés d'un manifeste, racine en premier."""
    return [
        (directory, load(directory))
        for directory, _name, _kind, _rel in _targets(Path(root))
        if manifest_path(directory).exists()
    ]


def find_personal_inbox(root: Path, owner: str) -> Optional[tuple[Path, Project]]:
    """Le projet « Non classé » de `owner` s'il existe, sinon None. N'écrit rien.

    Retrouvé par son manifeste (type `inbox`, propriétaire `owner`) et jamais
    par le nom de son dossier : un dossier se renomme, un manifeste non.
    """
    for directory, project in all_projects(Path(root)):
        if project.kind == "inbox" and meme_compte(project.owner, owner):
            return directory, project
    return None


def personal_inbox(root: Path, owner: str) -> tuple[Path, Project]:
    """Le projet « Non classé » de `owner`, créé s'il manque.

    Réservé aux écritures : création du compte, envoi, création de projet,
    déplacement. Une lecture passe par `find_personal_inbox`, qui ne crée rien.

    Sur une bibliothèque vierge, le « Non classé » de la racine, encore sans
    propriétaire, revient au premier compte qui en a besoin : l'administrateur
    d'une installation neuve n'en voit qu'un. Une bibliothèque qui contient
    déjà un Markdown, corbeille comprise, n'est jamais attribuée ainsi : c'est
    le rôle de `attribuer`, en simulation par défaut.
    """
    root = Path(root)
    with _LOCK:
        existant = find_personal_inbox(root, owner)
        if existant is not None:
            return existant
        # La racine reçoit d'abord son identité, comme au démarrage de l'API :
        # un dossier créé avant elle ferait passer la bibliothèque pour un
        # corpus ancien à migrer.
        racine = ensure_inbox(root)
        if racine.owner is None and not any(root.rglob("*.md")):
            attribue = _avec_proprietaire(racine, owner)
            _write(root, attribue)
            return root, attribue
        # Jamais un dossier nommé « Non classé » tout court : ce libellé désigne
        # déjà la racine dans les anciens filtres par nom.
        return create_owned(root, name=INBOX_NAME, owner=owner, kind="inbox",
                            dossier=f"{INBOX_NAME} ({owner})")


def ensure_inbox(root: Path) -> Project:
    """Garantit le projet système sur une installation neuve.

    Si le corpus contient déjà quoi que ce soit, l'absence de manifeste exige
    `initialize(..., simulation=True)` puis une migration explicite : générer un
    nouvel ID au démarrage rendrait une restauration ou une copie ambiguë.
    """
    root = Path(root)
    path = manifest_path(root)
    with _LOCK:
        if path.exists():
            project = load(root)
            if project.kind != "inbox" or project.name != INBOX_NAME:
                raise ProjectManifestError(
                    f"Le projet racine doit être {INBOX_NAME!r} de type 'inbox'"
                )
            return project
        if root.exists() and any(root.iterdir()):
            raise ProjectManifestError(
                "Corpus existant sans manifeste projet : migration explicite requise"
            )
        return create(root, name=INBOX_NAME, kind="inbox")


def resolve(root: Path, project_id: str) -> tuple[Path, Project]:
    """Résout une identité stable en dossier courant et manifeste.

    Le libellé et le chemin sont modifiables : aucun des deux ne doit servir de
    clé d'API. Une identité absente ou dupliquée est une incohérence visible,
    jamais une invitation à créer ou choisir un projet implicitement.
    """
    root = Path(root)
    matches: list[tuple[Path, Project]] = []
    for directory, _name, _kind, _rel in _targets(root):
        if not manifest_path(directory).exists():
            continue
        project = load(directory)
        if project.id == project_id:
            matches.append((directory, project))
    if not matches:
        raise ProjectManifestError(f"Projet inconnu : {project_id!r}")
    if len(matches) > 1:
        paths = ", ".join(str(path) for path, _ in matches)
        raise ProjectManifestError(
            f"Identifiant projet dupliqué {project_id!r} dans : {paths}"
        )
    return matches[0]


def _targets(root: Path) -> list[tuple[Path, str, str, str]]:
    """(dossier, nom, kind, chemin relatif affichable), racine en premier."""
    targets = [(root, INBOX_NAME, "inbox", ".")]
    if root.exists():
        for directory in sorted(root.iterdir(), key=lambda p: p.name.casefold()):
            if directory.is_dir() and not directory.name.startswith("."):
                targets.append((directory, directory.name, "regular", directory.name))
    return targets


def _described(project: Project, directory: str) -> dict:
    return {**project.payload(), "directory": directory}


def initialize(root: Path, *, simulation: bool = True) -> dict:
    """Attribue une identité aux projets existants, sans écriture par défaut."""
    root = Path(root)
    planned: list[dict] = []
    created: list[dict] = []
    existing: list[dict] = []
    with _LOCK:
        for directory, name, kind, rel in _targets(root):
            path = manifest_path(directory)
            if path.exists():
                project = load(directory)
                # Le « Non classé » d'un compte vit dans un sous-dossier.
                if project.kind != kind and not (project.kind == "inbox" and project.owner):
                    raise ProjectManifestError(
                        f"Le manifeste {path} déclare {project.kind!r}, attendu {kind!r}"
                    )
                existing.append(_described(project, rel))
                continue
            planned.append({"name": name, "kind": kind, "directory": rel})
            if not simulation:
                project = _new(name, kind)
                _write(directory, project)
                created.append(_described(project, rel))
    return {
        "simulation": simulation,
        "planned": planned,
        "created": created,
        "existing": existing,
    }


def attribuer(root: Path, owner: str, *, simulation: bool = True) -> dict:
    """Attribue à `owner` les projets sans propriétaire, sans écriture par défaut.

    Seul le manifeste est réécrit (version 2, champ `owner`) : ni l'identité du
    projet, ni ses Markdown ne changent. Un dossier sans manifeste n'est pas
    un projet : il est signalé, la migration `initialize` passe d'abord.
    """
    root = Path(root)
    sans_proprietaire: list[dict] = []
    sans_manifeste: list[str] = []
    with _LOCK:
        for directory, _name, _kind, rel in _targets(root):
            if not manifest_path(directory).exists():
                if directory != root or (root.exists() and any(root.iterdir())):
                    sans_manifeste.append(rel)
                continue
            project = load(directory)
            if project.owner is not None:
                continue
            sans_proprietaire.append(_described(project, rel))
            if not simulation:
                _write(directory, _avec_proprietaire(project, owner))
    return {
        "simulation": simulation,
        "owner": owner,
        "sans_proprietaire": sans_proprietaire,
        "sans_manifeste": sans_manifeste,
    }


@dataclass(frozen=True)
class PorteeLecture:
    """Ce qu'un invité lit (partage v1) : des documents un à un.

    `documents` : identités des documents partagés ; `sources` : les projets
    d'où ils viennent. Un document n'est lisible que si son identité est dans
    `documents` ET son dossier dans `sources` : un `.md` recopié ailleurs avec
    la même identité ne s'ouvre pas. `proprietaires` : tous les projets de ceux
    qui partagent, pour savoir quelles voix leur appartiennent entièrement (Q12).
    """
    documents: frozenset
    sources: frozenset
    proprietaires: frozenset = frozenset()


# Une portée, pour les modules qui filtrent par projet : `None` = toute la
# bibliothèque (maintenance d'administrateur, scripts) ; un identifiant = ce
# projet ; une collection = ces projets, vide comprise (alors : rien) ; une
# `PorteeLecture` = les documents partagés avec un invité.
Portee = Union[None, str, Collection[str], PorteeLecture]


def dans_la_portee(project_id: Optional[str], portee: Portee) -> bool:
    """Le projet est-il dans la portée ? Une `PorteeLecture` se juge document
    par document (`library.document_visible_in_project`), jamais par projet."""
    if portee is None:
        return True
    if isinstance(portee, PorteeLecture):
        return False
    if isinstance(portee, str):
        return project_id == portee
    return project_id in portee


def filtre_sql(colonne: str, portee: Portee) -> tuple[str, list]:
    """Condition SQL sur `colonne` pour `portee` ; `("", [])` sans filtre.

    Une collection vide rend une condition toujours fausse, jamais une absence
    de filtre : une portée vide ne voit rien. Une `PorteeLecture` filtre sur
    l'identité du document, colonne `id` de la même table que `colonne`.
    """
    if portee is None:
        return "", []
    if isinstance(portee, PorteeLecture):
        assert colonne.endswith("project_id"), colonne
        documents, sources = sorted(portee.documents), sorted(portee.sources)
        if not documents or not sources:
            return "0", []
        return (
            f"({colonne[:-len('project_id')]}id IN ({', '.join('?' * len(documents))}) "
            f"AND {colonne} IN ({', '.join('?' * len(sources))}))",
            documents + sources,
        )
    if isinstance(portee, str):
        return f"{colonne} = ?", [portee]
    valeurs = sorted(portee)
    if not valeurs:
        return "0", []
    return f"{colonne} IN ({', '.join('?' * len(valeurs))})", valeurs


_AUCUN = object()


def projets_du_meme_proprietaire(root: Path, project_id: Optional[str]) -> frozenset[str]:
    """Les projets qui ont le même propriétaire que `project_id`, lui compris.

    Les projets sans propriétaire forment un groupe à eux seuls. Un projet
    inconnu rend l'ensemble vide : ce qui ne se rattache à aucun compte ne se
    regroupe avec rien.
    """
    projets = all_projects(Path(root))
    proprietaire = next((p.owner for _d, p in projets if p.id == project_id), _AUCUN)
    if proprietaire is _AUCUN:
        return frozenset()
    return frozenset(
        p.id for _d, p in projets
        if (p.owner is None and proprietaire is None) or meme_compte(p.owner, proprietaire)
    )

