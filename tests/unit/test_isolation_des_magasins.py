"""La suite de tests n'écrit jamais dans les magasins réels.

Les magasins vivent à la racine du dépôt (`output/`, `index.db`, `usage.db`) :
ceux de la personne qui lance les tests. `tests/conftest.py` les redirige
avant tout import de `core`, et un témoin compare la vraie racine avant et
après chaque test.

Deux familles de tests ici :

- chaque chemin de magasin, tel que le code le calcule VRAIMENT, est sous la
  racine isolée. Retirer une redirection du `conftest` fait échouer le test
  du magasin concerné, en le nommant ;
- le témoin voit une création, une modification et une suppression. Il est
  éprouvé sur une racine jetable : l'éprouver sur la vraie racine reviendrait
  à écrire dans les données qu'il protège.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.conftest import (RACINE_ISOLEE, RACINE_REELLE, chemins_non_rediriges, ecarts,
                            releve_complet, releve_rapide)

pytestmark = pytest.mark.unit


def _chemins_des_magasins() -> dict[str, Path]:
    from core import (config, dedup, library, semantic, sqlite_db, usage_store,
                      voice_registry, voiceprint, writer_md)
    from core.experimental import pipeline as experimental
    from api import auth

    return {
        "config.OUTPUT_DIR": config.OUTPUT_DIR,
        "config.AUDIO_DIR": config.AUDIO_DIR,
        "library.OUTPUT_DIR": library.OUTPUT_DIR,
        "writer_md.OUTPUT_DIR": writer_md.OUTPUT_DIR,
        "writer_md.unique_path (défaut)": writer_md.unique_path.__defaults__[0],
        "voiceprint.OUTPUT_DIR": voiceprint.OUTPUT_DIR,
        "experimental.EXPERIMENTAL_DIR": experimental.EXPERIMENTAL_DIR,
        "sqlite_db.DB_PATH": sqlite_db.DB_PATH,
        "sqlite_db.ROOT (bases héritées)": sqlite_db.ROOT,
        "library.DB_PATH": library.DB_PATH,
        "dedup.DB_PATH": dedup.DB_PATH,
        "semantic.DB_PATH": semantic.DB_PATH,
        "voiceprint.DB_PATH": voiceprint.DB_PATH,
        "voiceprint.SIDECAR": voiceprint.SIDECAR,
        "voice_registry (registre des voix)": voice_registry._chemin(),
        "usage_store.usage_db_path()": usage_store.usage_db_path(),
        "usage_store (défaut sans variable)": usage_store._DEFAULT_USAGE_DB,
        "auth.DB_PATH (comptes et sessions)": auth.DB_PATH,
        "auth.CODE_PATH (code d'installation)": auth.CODE_PATH,
    }


def test_chaque_magasin_pointe_vers_la_racine_isolee() -> None:
    chemins = _chemins_des_magasins()
    # Ancre : le relevé couvre bien les trois magasins et leurs annexes.
    assert len(chemins) >= 19
    hors_isolation = {nom: str(p) for nom, p in chemins.items()
                      if not Path(p).resolve().is_relative_to(RACINE_ISOLEE.resolve())}
    assert not hors_isolation, f"magasins non redirigés : {hors_isolation}"


def test_le_controle_apres_chaque_test_voit_une_redirection_defaite(monkeypatch) -> None:
    from core import config

    assert chemins_non_rediriges() == []
    monkeypatch.setattr(config, "OUTPUT_DIR", RACINE_REELLE / "output")
    assert chemins_non_rediriges() == ["config.OUTPUT_DIR"]


def test_la_racine_isolee_n_est_pas_dans_le_depot() -> None:
    assert not RACINE_ISOLEE.resolve().is_relative_to(RACINE_REELLE.resolve())
    assert RACINE_ISOLEE.is_dir()


def test_usage_db_reste_isolee_sans_la_variable(monkeypatch) -> None:
    """Un test qui efface la variable retombe sur le défaut, lui aussi redirigé."""
    from core import config, usage_store

    monkeypatch.delenv(config.ENV_PREFIX + "USAGE_DB", raising=False)
    assert usage_store.usage_db_path().is_relative_to(RACINE_ISOLEE)


def _racine_jetable(tmp_path: Path) -> Path:
    (tmp_path / "output" / "Projet").mkdir(parents=True)
    (tmp_path / "output" / ".voices.json").write_text("{}", encoding="utf-8")
    (tmp_path / "output" / "Projet" / "doc.md").write_text("x", encoding="utf-8")
    (tmp_path / "index.db").write_bytes(b"")
    return tmp_path


def test_le_temoin_voit_une_modification_du_registre(tmp_path) -> None:
    racine = _racine_jetable(tmp_path)
    avant = releve_rapide(racine)
    (racine / "output" / ".voices.json").write_text('{"voix": 1}', encoding="utf-8")
    assert ecarts(avant, releve_rapide(racine)) == ["output/.voices.json"]


def test_le_temoin_voit_la_creation_d_un_magasin(tmp_path) -> None:
    racine = _racine_jetable(tmp_path)
    avant = releve_rapide(racine)
    (racine / "usage.db").write_bytes(b"")
    assert ecarts(avant, releve_rapide(racine)) == ["usage.db"]


def test_le_temoin_voit_un_journal_sqlite(tmp_path) -> None:
    """Une écriture SQLite en WAL peut ne toucher que `-wal` et `-shm`."""
    racine = _racine_jetable(tmp_path)
    avant = releve_rapide(racine)
    (racine / "index.db-wal").write_bytes(b"x")
    assert ecarts(avant, releve_rapide(racine)) == ["index.db-wal"]


def test_le_releve_complet_voit_un_document_en_profondeur(tmp_path) -> None:
    racine = _racine_jetable(tmp_path)
    avant = releve_complet(racine)
    doc = racine / "output" / "Projet" / "doc.md"
    os.utime(doc, ns=(1, 1))
    assert ecarts(avant, releve_complet(racine)) == ["output/Projet/doc.md"]
    doc.unlink()
    assert "output/Projet/doc.md" in ecarts(avant, releve_complet(racine))


def test_deux_releves_identiques_ne_signalent_rien(tmp_path) -> None:
    racine = _racine_jetable(tmp_path)
    releve = releve_complet(racine)
    # Ancre : le relevé voit les fichiers posés.
    assert "output/.voices.json" in releve and "output/Projet/doc.md" in releve
    assert ecarts(releve, releve_complet(racine)) == []


def test_le_garde_de_l_output_partage_voit_un_fichier_laisse() -> None:
    """Audit externe, 4b : le garde de fin de test doit voir ce qu'un test laisse.

    Le fichier est retiré avant la fin du test, sinon le garde ferait échouer
    celui-ci : c'est la détection qu'on éprouve, pas l'échec.
    """
    from tests.conftest import _contenu_de_l_output_partage

    avant = _contenu_de_l_output_partage()
    cible = RACINE_ISOLEE / "output" / "sous-dossier" / ".oublie.json"
    cible.parent.mkdir(parents=True, exist_ok=True)
    cible.write_text("{}", encoding="utf-8")
    try:
        assert _contenu_de_l_output_partage() - avant == {"sous-dossier/.oublie.json"}
    finally:
        cible.unlink()
        cible.parent.rmdir()
    assert _contenu_de_l_output_partage() == avant
