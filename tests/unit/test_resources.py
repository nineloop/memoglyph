"""Arbitrage d'un seul travail lourd à la fois.

`MAX_ACTIVE_JOBS` ne bornait que la transcription et le backfill — les seuls
chemins passant par le gestionnaire de jobs. Le CR, le RAG et la réindexation
sémantique sont synchrones et y échappaient : trois modèles pouvaient être
résidents simultanément sur 18 Go de RAM unifiée.
"""

import threading
import time

import pytest

from core import backfill, diarization, faithful_backfill, resources

pytestmark = pytest.mark.unit


def test_un_seul_travail_a_la_fois():
    resultats: list[str] = []

    def worker(n: int) -> None:
        try:
            with resources.heavy(label="test"):
                time.sleep(0.2)
                resultats.append(f"{n}:ok")
        except resources.ResourcesBusy:
            resultats.append(f"{n}:refusé")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(r.endswith(":ok") for r in resultats) == 1
    assert sum(r.endswith(":refusé") for r in resultats) == 2


def test_le_jeton_est_rendu_apres_usage():
    with resources.heavy(label="a"):
        pass
    with resources.heavy(label="b"):  # ne doit pas lever
        pass


def test_le_jeton_est_rendu_meme_en_cas_d_erreur():
    """Sans ça, une exception dans un CR bloquerait toute l'application."""
    with pytest.raises(ValueError):
        with resources.heavy(label="a"):
            raise ValueError("boum")
    with resources.heavy(label="b"):
        pass


def test_reentrance_dans_le_meme_thread():
    """`pipeline.run` prend le jeton puis appelle `profiling`, qui interroge le LLM.

    Sans réentrance, ce second passage s'auto-bloquerait — la transcription
    resterait figée jusqu'au délai d'attente.
    """
    with resources.heavy(wait=1, label="externe"):
        with resources.heavy(label="imbriqué"):
            assert resources.busy()


def test_attente_bornee_puis_refus():
    pris = threading.Event()
    relache = threading.Event()

    def occupant() -> None:
        with resources.heavy(label="occupant"):
            pris.set()
            relache.wait(2)

    t = threading.Thread(target=occupant)
    t.start()
    pris.wait(1)
    debut = time.time()
    with pytest.raises(resources.ResourcesBusy):
        with resources.heavy(wait=0.3, label="tardif"):
            pass
    assert 0.25 <= time.time() - debut < 1.5, "l'attente doit être bornée"
    relache.set()
    t.join()


def test_message_de_refus_explicite():
    with resources.heavy(label="compte-rendu"):
        with pytest.raises(resources.ResourcesBusy) as exc:
            with resources.heavy(label="autre") if False else _autre_thread():
                pass
    assert "compte-rendu" in str(exc.value) or "occupé" in str(exc.value).lower()


def _autre_thread():
    """Prend le jeton depuis un AUTRE thread (la réentrance masquerait le refus)."""
    box: list = []

    class Ctx:
        def __enter__(self):
            def run():
                try:
                    with resources.heavy(label="compte-rendu"):
                        box.append(None)
                except resources.ResourcesBusy as e:
                    box.append(e)
            t = threading.Thread(target=run)
            t.start()
            t.join()
            if box and isinstance(box[0], Exception):
                raise box[0]
            return self

        def __exit__(self, *a):
            return False

    return Ctx()


def test_backfill_passe_par_le_verrou_global(monkeypatch, tmp_path):
    target = tmp_path / "ancien.md"
    target.write_text("---\n---\n", encoding="utf-8")
    monkeypatch.setattr(backfill, "eligible", lambda _project_id=None: [target])
    monkeypatch.setattr(diarization, "load_pipeline", lambda _token: object())
    monkeypatch.setattr(
        backfill, "backfill_one",
        lambda path, *, pipe=None: {"name": path.name, "status": "done"},
    )
    result: list[object] = []

    def contender() -> None:
        try:
            result.append(backfill.backfill_all(resource_wait=0))
        except Exception as exc:
            result.append(exc)

    with resources.heavy(label="occupant"):
        thread = threading.Thread(target=contender)
        thread.start()
        thread.join()

    assert len(result) == 1
    assert isinstance(result[0], resources.ResourcesBusy)


def test_backfill_fidele_passe_par_le_verrou_global(tmp_path):
    root = tmp_path / "output"
    root.mkdir()
    result: list[object] = []

    def contender() -> None:
        try:
            result.append(faithful_backfill.run(root=root, resource_wait=0))
        except Exception as exc:
            result.append(exc)

    with resources.heavy(label="occupant"):
        thread = threading.Thread(target=contender)
        thread.start()
        thread.join()

    assert len(result) == 1
    assert isinstance(result[0], resources.ResourcesBusy)
