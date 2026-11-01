"""FinOps phase 1D — le VRAI seam de démarrage app initialise le catalogue (zéro-touche).

Exerce `api.main._startup_finops` — la fonction que le `lifespan` FastAPI appelle au démarrage
normal (`uvicorn api.main:app`, via `./start.sh`). Prouve : ensemencement automatique sans
action manuelle, idempotence au redémarrage, et isolation totale des pannes.
"""
from __future__ import annotations

import inspect

import pytest

from core import usage_store, pricing_official


@pytest.fixture()
def usage_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(tmp_path / "usage.db"))
    return tmp_path / "usage.db"


@pytest.mark.integration
def test_lifespan_cable_le_seam_finops():
    """Le seam est bien branché dans le démarrage (lifespan appelle `_startup_finops`)."""
    from api import main
    assert hasattr(main, "_startup_finops")
    assert "_startup_finops" in inspect.getsource(main.lifespan)


@pytest.mark.integration
def test_demarrage_normal_ensemence_le_catalogue(usage_db):
    """Fresh install : pas de usage.db, un démarrage normal -> catalogue complet, en un exemplaire."""
    from api import main
    assert not usage_db.exists()
    main._startup_finops()                      # = ce que fait le lifespan au démarrage
    c = usage_store.connect(usage_db)
    n_pricing = c.execute("SELECT COUNT(*) FROM pricing_snapshot").fetchone()[0]
    n_alias = c.execute("SELECT COUNT(*) FROM model_alias").fetchone()[0]
    # officiels + locaux + alias, chacun présent
    assert n_pricing == len(pricing_official.OFFICIAL_RATES) + len(pricing_official.LOCAL_ASSUMPTIONS)
    assert n_alias == len(pricing_official.OFFICIAL_ALIASES)
    assert c.execute("SELECT COUNT(*) FROM pricing_snapshot WHERE kind='local_imputed'").fetchone()[0] == 2
    assert c.execute("SELECT COUNT(*) FROM pricing_snapshot WHERE rate_source='official_published'").fetchone()[0] == sum(
        1 for e in pricing_official.OFFICIAL_RATES if e["rate_source"] == "official_published")   # 5 : OpenAI, Mistral ×2, Anthropic ×2
    c.close()


@pytest.mark.integration
def test_redemarrage_normal_ne_duplique_pas(usage_db):
    """Redémarrages successifs : idempotent, aucun doublon."""
    from api import main
    main._startup_finops()
    main._startup_finops()
    main._startup_finops()
    c = usage_store.connect(usage_db)
    n_pricing = c.execute("SELECT COUNT(*) FROM pricing_snapshot").fetchone()[0]
    n_alias = c.execute("SELECT COUNT(*) FROM model_alias").fetchone()[0]
    c.close()
    assert n_pricing == len(pricing_official.OFFICIAL_RATES) + len(pricing_official.LOCAL_ASSUMPTIONS)
    assert n_alias == len(pricing_official.OFFICIAL_ALIASES)


@pytest.mark.integration
def test_panne_init_ne_casse_pas_le_demarrage(monkeypatch):
    """Une init qui explose ne doit PAS faire lever le seam de démarrage."""
    from api import main

    def _boom():
        raise RuntimeError("panne d'init simulée")

    monkeypatch.setattr(usage_store, "initialize_catalogue", _boom)
    main._startup_finops()                      # ne doit PAS lever
