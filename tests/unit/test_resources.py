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


# ---------- l'attente se voit ----------

def test_on_wait_n_est_appele_que_si_le_creneau_est_occupe():
    appels: list[str] = []
    with resources.heavy(wait=1, label="a", on_wait=lambda: appels.append("attend"),
                         on_acquired=lambda: appels.append("obtenu")):
        pass
    # Créneau libre : rien n'a attendu ; l'obtention est quand même signalée.
    assert appels == ["obtenu"]


def test_on_wait_precede_l_obtention_quand_le_creneau_est_pris():
    appels: list[str] = []
    liberer = threading.Event()
    pris = threading.Event()

    def occupant() -> None:
        with resources.heavy(label="occupant"):
            pris.set()
            liberer.wait(timeout=5)

    t = threading.Thread(target=occupant)
    t.start()
    assert pris.wait(timeout=2)

    def attendant() -> None:
        with resources.heavy(wait=5, label="b", on_wait=lambda: appels.append("attend"),
                             on_acquired=lambda: appels.append("obtenu")):
            appels.append("travail")

    a = threading.Thread(target=attendant)
    a.start()
    deadline = time.time() + 2
    while "attend" not in appels and time.time() < deadline:
        time.sleep(0.01)
    assert appels == ["attend"]            # il attend, et le dit AVANT d'obtenir
    liberer.set()
    a.join(timeout=5)
    t.join(timeout=5)
    assert appels == ["attend", "obtenu", "travail"]


def test_les_rappels_ne_sont_pas_appeles_en_cas_de_reentrance():
    appels: list[str] = []
    with resources.heavy(label="externe"):
        with resources.heavy(wait=1, label="interne", on_wait=lambda: appels.append("attend"),
                             on_acquired=lambda: appels.append("obtenu")):
            pass
    assert appels == []


def test_le_delai_depasse_leve_une_erreur_au_code_stable():
    pris = threading.Event()
    liberer = threading.Event()

    def occupant() -> None:
        with resources.heavy(label="occupant"):
            pris.set()
            liberer.wait(timeout=5)

    t = threading.Thread(target=occupant)
    t.start()
    assert pris.wait(timeout=2)
    attente: list[str] = []
    try:
        with pytest.raises(resources.ResourcesBusy) as exc:
            with resources.heavy(wait=0.2, label="traitement", on_wait=lambda: attente.append("a")):
                pass
        assert getattr(exc.value.args[0], "code", None) == "step.error.waitTooLong"
        assert attente == ["a"]
    finally:
        liberer.set()
        t.join(timeout=5)


def test_l_attente_de_fond_des_jobs_de_l_api_est_de_trois_heures():
    from core import config
    assert config.ATTENTE_DE_FOND_API_S == 10_800
    # Le pipeline en CLI garde sa valeur d'avant.
    import inspect
    from core import pipeline
    assert "wait=900" in inspect.getsource(pipeline)
