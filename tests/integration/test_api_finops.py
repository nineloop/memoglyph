"""FinOps phase 2 — endpoints de LECTURE (résumé + détail), scoping et dégradation gracieuse.

Le catalogue s'initialise au démarrage (lifespan) ; les endpoints ne font que LIRE (jamais
d'écriture usage.db depuis l'API). Liaison CR↔exécution par ULID, transcript↔STT par document_id.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


class _U:
    def __init__(self, i, o):
        self.input_tokens = i
        self.output_tokens = o
        self.input_tokens_details = {"cached_tokens": 0}
        self.output_tokens_details = None


@pytest.fixture()
def finops_client(isolated_stores, monkeypatch, tmp_path):
    """Corpus + usage.db isolés, un transcript + un CR liés à leurs exécutions, app démarrée."""
    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(tmp_path / "usage.db"))
    from core import projects, writer_md, telemetry, usage_store, docid, library

    out = isolated_stores["output_dir"]
    projects.initialize(out, simulation=False)
    proj = projects.load(out)

    txid, meeting_id = docid.nouveau(), docid.nouveau()
    tx_fm = {"id": txid, "meeting_id": meeting_id, "project_id": proj.id, "title": "T",
             "date": "2026-08-09", "language": "fr", "engine": "whisper",
             "model": "mlx-community/whisper-large-v3-turbo"}
    tx_body = "# T\n\n## Transcript\n\n**Speaker 1** [00:00:00] bonjour\n"
    tx_path = writer_md.write_md(writer_md._yaml_frontmatter(tx_fm) + "\n" + tx_body,
                                 writer_md.unique_path("T", out))
    library.sync_file(tx_path)

    rid = docid.nouveau()
    rep_fm = {"id": rid, "type": "report", "source_document_id": txid, "source_transcript": tx_path.name,
              "meeting_id": meeting_id, "project_id": proj.id, "title": "R",
              "date": "2026-08-09", "model": "gpt-5.6-luna"}
    rep_path = writer_md.write_md(writer_md._yaml_frontmatter(rep_fm) + "\n# R\n\ncorps\n",
                                  writer_md.unique_path("T-cr", out))
    library.sync_file(rep_path)

    # STT execution (whisper) liée au transcript par document_id
    with telemetry.execution(kind="stt", provider="whisper",
                             model="mlx-community/whisper-large-v3-turbo",
                             document_id=txid, audio_seconds=90.0):
        pass
    # report execution (openai) liée au CR par produced_artifact_id=rid
    with telemetry.execution(kind="report", provider="openai", model="gpt-5.6-luna", document_id=txid):
        with telemetry.stage("extract"):
            telemetry.record_model_call(provider="openai", model="gpt-5.6-luna", latency_s=1.0,
                usage=_U(100, 100), usage_source="provider_reported")
        telemetry.set_produced_artifact(rid)

    # Catalogue + tarification explicites (le démarrage zéro-touche est couvert en phase 1D ;
    # ici on teste les endpoints de LECTURE, on veut donc un état tarifé déterministe).
    usage_store.initialize_catalogue()
    usage_store.reprice()
    from api import main as api_main
    client = TestClient(api_main.app)
    return {
        "client": client,
        "tx": tx_path.name, "tx_path": tx_path, "txid": txid,
        "rep": rep_path.name, "rep_path": rep_path, "rid": rid,
        "meeting_id": meeting_id, "proj": proj, "out": out,
        "usage_db": tmp_path / "usage.db",
    }


def test_report_finops_summary(finops_client):
    c, rep = finops_client["client"], finops_client["rep"]
    r = c.get(f"/api/transcript/{rep}/finops?view_scope=all")
    assert r.status_code == 200
    j = r.json()
    assert j["available"] and j["kind"] == "report"
    assert j["provider"] == "openai" and j["model"] == "gpt-5.6-luna"     # verbatim
    assert j["cost_basis"] == "cloud_derived" and j["cost"] is not None
    assert j["calls"] == 1 and j["tokens"] == 200
    # forensic detail (scoped to this document)
    d = c.get(f"/api/transcript/{rep}/finops/execution/{j['execution_id']}?view_scope=all").json()
    assert d["available"] and len(d["calls"]) == 1 and d["calls"][0]["stage"] == "extract"


def test_transcript_stt_finops_summary(finops_client):
    c, tx = finops_client["client"], finops_client["tx"]
    j = c.get(f"/api/transcript/{tx}/finops?view_scope=all").json()
    assert j["available"] and j["kind"] == "stt"
    assert j["cost_basis"] == "local_imputed" and j["audio_seconds"] == 90.0


def test_forensic_refuse_execution_hors_document(finops_client):
    """Une exécution NON liée au document scopé n'est PAS lisible via son endpoint forensique.

    L'exécution STT (liée au transcript) est demandée via le CR : ni son `document_id` (celui du
    transcript) ni son `produced_artifact_id` (vide) ne valent l'ULID du CR → 404. Pas de lecture
    d'exécution arbitraire par un document autorisé.
    """
    c, tx, rep = finops_client["client"], finops_client["tx"], finops_client["rep"]
    stt_exec = c.get(f"/api/transcript/{tx}/finops?view_scope=all").json()["execution_id"]
    x = c.get(f"/api/transcript/{rep}/finops/execution/{stt_exec}?view_scope=all")
    assert x.status_code == 404


def test_reponses_sans_texte_ni_secret(finops_client):
    c, rep = finops_client["client"], finops_client["rep"]
    rid_exec = c.get(f"/api/transcript/{rep}/finops?view_scope=all").json()["execution_id"]
    blob = c.get(f"/api/transcript/{rep}/finops/execution/{rid_exec}?view_scope=all").text
    for forbidden in ("prompt", "transcript", "content", "body", "secret", "sk-", "_details"):
        assert forbidden not in blob


def test_usage_db_absente_degrade_gracieusement(finops_client, monkeypatch, tmp_path):
    """usage.db indisponible -> {available:false}, jamais une 500, jamais 0 fabriqué."""
    c, rep = finops_client["client"], finops_client["rep"]
    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(tmp_path / "gone.db"))
    r = c.get(f"/api/transcript/{rep}/finops?view_scope=all")
    assert r.status_code == 200 and r.json() == {"available": False}


def test_endpoints_n_ecrivent_pas_usage_db(finops_client):
    """Les lectures FinOps ne modifient JAMAIS usage.db (mtime inchangée)."""
    import time
    c, rep, db = finops_client["client"], finops_client["rep"], finops_client["usage_db"]
    before = db.stat().st_mtime_ns
    time.sleep(0.01)
    for _ in range(3):
        c.get(f"/api/transcript/{rep}/finops?view_scope=all")
    assert db.stat().st_mtime_ns == before


# --- Liaison HISTORIQUE : stable par ID, jamais par nom de fichier / mtime / index -----------

def test_liaison_cr_survit_au_renommage_et_au_mtime(finops_client):
    """C — Renommer le fichier du CR (et toucher son mtime) ne casse pas CR→exécution.

    La clé de liaison est l'ULID du front-matter, jamais le nom de fichier. Après renommage,
    l'accès par le NOUVEAU nom résout la MÊME exécution.
    """
    import os
    from core import library
    c, rep_path = finops_client["client"], finops_client["rep_path"]
    before = c.get(f"/api/transcript/{rep_path.name}/finops?view_scope=all").json()
    assert before["available"] and before["cost_basis"] == "cloud_derived"

    new_path = rep_path.with_name("cr-renomme-manuellement.md")
    rep_path.rename(new_path)
    os.utime(new_path, (10_000, 10_000))     # mtime arbitraire, très ancien
    library.reindex()                         # l'index dérivé suit le fichier

    after = c.get(f"/api/transcript/{new_path.name}/finops?view_scope=all").json()
    assert after["available"]
    assert after["execution_id"] == before["execution_id"]     # MÊME exécution
    assert after["cost"] == before["cost"] and after["cost_basis"] == "cloud_derived"


def test_liaison_survit_a_la_reconstruction_de_l_index(finops_client):
    """D — Détruire puis reconstruire index.db ne change pas l'exécution résolue."""
    from core import library
    c, rep, tx = finops_client["client"], finops_client["rep"], finops_client["tx"]
    rep_before = c.get(f"/api/transcript/{rep}/finops?view_scope=all").json()["execution_id"]
    tx_before = c.get(f"/api/transcript/{tx}/finops?view_scope=all").json()["execution_id"]

    library.DB_PATH.unlink(missing_ok=True)   # l'index est DÉRIVÉ et reconstructible
    library.reindex()

    assert c.get(f"/api/transcript/{rep}/finops?view_scope=all").json()["execution_id"] == rep_before
    assert c.get(f"/api/transcript/{tx}/finops?view_scope=all").json()["execution_id"] == tx_before


def test_liaison_stt_par_document_id_survit_au_renommage(finops_client):
    """E — Le transcript résout son exécution STT par document_id, pas par nom de fichier."""
    from core import library
    c, tx_path = finops_client["client"], finops_client["tx_path"]
    before = c.get(f"/api/transcript/{tx_path.name}/finops?view_scope=all").json()
    assert before["kind"] == "stt" and before["audio_seconds"] == 90.0

    new_path = tx_path.with_name("transcript-renomme.md")
    tx_path.rename(new_path)
    library.reindex()

    after = c.get(f"/api/transcript/{new_path.name}/finops?view_scope=all").json()
    assert after["available"] and after["kind"] == "stt"
    assert after["execution_id"] == before["execution_id"]
    assert after["audio_seconds"] == 90.0


def test_deux_versions_de_cr_resolvent_independamment(finops_client, monkeypatch):
    """A+B — Une version PLUS RÉCENTE ne modifie pas l'exécution montrée pour l'ancienne.

    Deux CR (deux ULID) dans la MÊME réunion ; chacun résout SA propre exécution avec SES
    propres jetons, jamais « la plus récente ».
    """
    from core import writer_md, telemetry, usage_store, docid, library
    c = finops_client["client"]
    out, txid, meeting_id, proj = (finops_client["out"], finops_client["txid"],
                                   finops_client["meeting_id"], finops_client["proj"])

    old = c.get(f"/api/transcript/{finops_client['rep']}/finops?view_scope=all").json()
    assert old["tokens"] == 200 and old["provider"] == "openai"

    # Version 2 : nouvel ULID, jetons DIFFÉRENTS, exécution propre.
    rid2 = docid.nouveau()
    fm2 = {"id": rid2, "type": "report", "source_document_id": txid, "meeting_id": meeting_id,
           "project_id": proj.id, "title": "R v2", "date": "2026-08-09", "model": "gpt-5.6-luna"}
    p2 = writer_md.write_md(writer_md._yaml_frontmatter(fm2) + "\n# R v2\n\ncorps v2\n",
                            writer_md.unique_path("T-cr-v2", out))
    library.sync_file(p2)
    with telemetry.execution(kind="report", provider="openai", model="gpt-5.6-luna", document_id=txid):
        with telemetry.stage("extract"):
            telemetry.record_model_call(provider="openai", model="gpt-5.6-luna", latency_s=1.0,
                usage=_U(999, 111), usage_source="provider_reported")
        telemetry.set_produced_artifact(rid2)
    usage_store.reprice()

    new = c.get(f"/api/transcript/{p2.name}/finops?view_scope=all").json()
    still_old = c.get(f"/api/transcript/{finops_client['rep']}/finops?view_scope=all").json()

    assert new["tokens"] == 1110 and new["execution_id"] != old["execution_id"]
    # L'ANCIENNE version n'a pas bougé — ni exécution, ni jetons.
    assert still_old["execution_id"] == old["execution_id"] and still_old["tokens"] == 200


def test_usage_db_corrompue_degrade_sans_casser_la_lecture(finops_client):
    """usage.db CORROMPUE -> {available:false} et la lecture du document reste intacte."""
    db = finops_client["usage_db"]
    c, rep = finops_client["client"], finops_client["rep"]
    db.write_bytes(b"ce ne sont pas des octets SQLite valides")   # corruption franche

    r = c.get(f"/api/transcript/{rep}/finops?view_scope=all")
    assert r.status_code == 200 and r.json() == {"available": False}
    # La lecture du CR lui-même n'est pas affectée par la panne FinOps.
    doc = c.get(f"/api/transcript/{rep}?view_scope=all")
    assert doc.status_code == 200 and doc.json()["name"] == rep


# --- Profilage : l'onglet Information d'un transcript montre aussi l'exécution `profile` -------

def _profiler(txid: str) -> None:
    from core import telemetry
    with telemetry.execution(kind="profile", provider="openai", model="gpt-5.6-luna",
                             document_id=txid):
        with telemetry.stage("profile"):
            telemetry.record_model_call(provider="openai", model="gpt-5.6-luna",
                                        latency_s=0.5, in_tok=1000, out_tok=200,
                                        usage_source="provider_reported")


def test_transcript_montre_son_profilage_au_meme_contrat(finops_client):
    from core import usage_store
    c, tx, rep = finops_client["client"], finops_client["tx"], finops_client["rep"]
    assert "profile" not in c.get(f"/api/transcript/{tx}/finops?view_scope=all").json()
    _profiler(finops_client["txid"])
    usage_store.reprice()

    j = c.get(f"/api/transcript/{tx}/finops?view_scope=all").json()

    # L'exécution STT reste en tête ; le profilage s'ajoute, avec les mêmes clés.
    assert j["available"] and j["kind"] == "stt"
    profil = j["profile"]
    assert set(profil) == set(j) - {"profile"}
    assert (profil["available"], profil["kind"], profil["provider"], profil["model"]) == (
        True, "profile", "openai", "gpt-5.6-luna")
    assert (profil["calls"], profil["in_tok"], profil["out_tok"], profil["tokens"]) == (1, 1000, 200, 1200)
    assert profil["cost_basis"] == "cloud_derived" and profil["cost"] > 0
    # Son détail forensique est lisible par le transcript, pas par le CR.
    d = c.get(f"/api/transcript/{tx}/finops/execution/{profil['execution_id']}?view_scope=all").json()
    assert d["available"] and [a["stage"] for a in d["calls"]] == ["profile"]
    assert c.get(f"/api/transcript/{rep}/finops/execution/{profil['execution_id']}"
                 "?view_scope=all").status_code == 404
    # Un CR ne porte pas le profilage de sa source.
    assert "profile" not in c.get(f"/api/transcript/{rep}/finops?view_scope=all").json()


def test_un_profilage_sans_stt_se_montre_quand_meme(finops_client):
    from core import docid, library, writer_md
    c, out, proj = finops_client["client"], finops_client["out"], finops_client["proj"]
    txid = docid.nouveau()
    fm = {"id": txid, "meeting_id": docid.nouveau(), "project_id": proj.id, "title": "Importé",
          "date": "2026-08-09", "language": "fr"}
    chemin = writer_md.write_md(writer_md._yaml_frontmatter(fm) + "\n## Transcript\n\n[00:00:01] a\n",
                                writer_md.unique_path("Importé", out))
    library.sync_file(chemin)
    assert c.get(f"/api/transcript/{chemin.name}/finops?view_scope=all").json() == {"available": False}
    _profiler(txid)

    j = c.get(f"/api/transcript/{chemin.name}/finops?view_scope=all").json()

    assert j["available"] is False
    assert (j["profile"]["kind"], j["profile"]["tokens"]) == ("profile", 1200)
