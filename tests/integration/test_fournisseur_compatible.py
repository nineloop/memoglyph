"""Le fournisseur « serveur compatible OpenAI », contre un FAUX serveur HTTP local.

Le faux serveur est lancé par chaque test sur 127.0.0.1, port libre, et fermé à la
fin : aucun Ollama, aucun téléchargement, aucun appel sortant. Il note chaque requête
reçue (chemin, en-têtes, corps) et rend une complétion au format OpenAI.
"""
from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from api import main as api_main
from core import config, docid, ecl, library, llm, llm_compatible, pricing_official, projects, semantic
from core import sensitivity, usage_cost
from core.config import EMBED_DIM
from core.experimental import profiles
from tests.conftest import COMPTE_DE_TEST

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _faits_actives(monkeypatch):
    """Ces tests supposent les faits de la réunion activés (`MEMOGLYPH_FAITS=1`)."""
    from core import config as _config

    monkeypatch.setattr(_config, "FAITS", True)

CLE = "cle-factice-compatible-XYZ"
MODELE = "qwen-test:8b"


class _Gestionnaire(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 — nom imposé par http.server
        corps = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        self.server.recus.append({"chemin": self.path, "entetes": dict(self.headers), "corps": corps})
        if self.server.attente:
            time.sleep(self.server.attente)
        reponse = json.dumps({
            "id": "c1", "object": "chat.completion", "created": 0, "model": corps.get("model"),
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": self.server.contenu}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        }).encode()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(reponse)))
            self.end_headers()
            self.wfile.write(reponse)
        except (BrokenPipeError, ConnectionResetError):
            pass   # le client a abandonné (délai dépassé) : rien à rendre

    def log_message(self, *_a) -> None:
        pass


@pytest.fixture
def serveur():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Gestionnaire)
    srv.daemon_threads = True
    srv.recus, srv.contenu, srv.attente = [], "réponse du serveur", 0.0
    fil = threading.Thread(target=srv.serve_forever, daemon=True)
    fil.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    fil.join(timeout=5)


@pytest.fixture
def configure(serveur, monkeypatch):
    monkeypatch.setattr(config, "COMPATIBLE_BASE_URL", f"http://127.0.0.1:{serveur.server_address[1]}/v1")
    monkeypatch.setattr(config, "COMPATIBLE_MODEL", MODELE)
    monkeypatch.setattr(config, "COMPATIBLE_API_KEY", CLE)
    return serveur


def _port_ferme() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------- Chat, JSON, client partagé ----------

def test_chat_part_vers_le_serveur_avec_sa_cle_et_sans_en_tete_d_organisation(configure, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_ORG_ID", "org-ne-doit-pas-partir")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "proj-ne-doit-pas-partir")
    assert llm.chat("système", "question", provider="compatible") == "réponse du serveur"
    [recu] = configure.recus
    assert recu["chemin"] == "/v1/chat/completions" and recu["corps"]["model"] == MODELE
    entetes = {k.lower(): v for k, v in recu["entetes"].items()}
    assert entetes["authorization"] == f"Bearer {CLE}"
    assert "openai-organization" not in entetes and "openai-project" not in entetes
    assert llm.modele_servi("compatible") == MODELE


def test_sans_cle_le_client_part_avec_une_cle_inerte(configure, monkeypatch) -> None:
    monkeypatch.setattr(config, "COMPATIBLE_API_KEY", "")
    assert llm.chat("s", "u", provider="compatible") == "réponse du serveur"
    assert configure.recus[0]["entetes"]["Authorization"] == "Bearer sans-cle"


def test_chat_json_rend_une_table_meme_dans_une_enveloppe_markdown(configure) -> None:
    configure.contenu = '```json\n{"faits": [], "ok": 1}\n```'
    schema = {"type": "object", "required": ["faits"]}
    assert llm.chat_json("système", "texte", provider="compatible", schema=schema, seed=7,
                         max_tokens=50) == {"faits": [], "ok": 1}
    corps = configure.recus[0]["corps"]
    assert corps["seed"] == 7 and corps["max_tokens"] == 50
    assert "réponds EXCLUSIVEMENT en JSON" in corps["messages"][0]["content"]
    assert '"required": ["faits"]' in corps["messages"][0]["content"]
    assert "response_format" not in corps, "le schéma passe par la directive"


def test_chat_json_illisible_est_une_sortie_inexploitable(configure) -> None:
    configure.contenu = "pas du json"
    with pytest.raises(llm.LLMMalformedOutputError):
        llm.chat_json("s", "u", provider="compatible", retries=1)
    assert len(configure.recus) == 2, "un essai, puis une reprise"


# ---------- Pannes lisibles ----------

def test_serveur_injoignable_message_clair_sans_la_cle(monkeypatch) -> None:
    monkeypatch.setattr(config, "COMPATIBLE_BASE_URL", f"http://utilisateur:{CLE}@127.0.0.1:{_port_ferme()}/v1")
    monkeypatch.setattr(config, "COMPATIBLE_MODEL", MODELE)
    monkeypatch.setattr(config, "COMPATIBLE_API_KEY", CLE)
    with pytest.raises(llm.LLMError) as panne:
        llm.chat("s", "u", provider="compatible")
    assert "injoignable" in str(panne.value) and "127.0.0.1" in str(panne.value)
    assert CLE not in str(panne.value), "ni la clé ni les identifiants de l'URL"


def test_delai_depasse_message_clair(configure, monkeypatch) -> None:
    configure.attente = 2.0
    monkeypatch.setattr(llm_compatible, "DELAI_S", 0.3)
    debut = time.monotonic()
    with pytest.raises(llm.LLMError) as panne:
        llm.chat("s", "u", provider="compatible")
    assert "délai dépassé" in str(panne.value) and time.monotonic() - debut < 1.8


def _vecteur(_texte: str) -> list[float]:
    v = [0.0] * EMBED_DIM
    v[0] = 1.0
    return v


def test_l_assistant_rend_503_avec_le_message_quand_le_serveur_est_injoignable(isolated_stores, monkeypatch,
                                                                                caplog) -> None:
    monkeypatch.setattr(semantic.embed, "embed_documents", lambda textes, **_k: [_vecteur(t) for t in textes])
    monkeypatch.setattr(semantic.embed, "embed_query", lambda t, **_k: _vecteur(t))
    monkeypatch.setattr(config, "COMPATIBLE_BASE_URL", f"http://127.0.0.1:{_port_ferme()}/v1")
    monkeypatch.setattr(config, "COMPATIBLE_MODEL", MODELE)
    monkeypatch.setattr(config, "COMPATIBLE_API_KEY", CLE)
    inbox = projects.create(isolated_stores["output_dir"], name=projects.INBOX_NAME, kind="inbox",
                            owner=COMPTE_DE_TEST)
    (isolated_stores["output_dir"] / "point.md").write_text(
        f"---\nid: {docid.nouveau()}\nmeeting_id: {docid.nouveau()}\nproject_id: {inbox.id}\ntitle: Point\n"
        "language: fr\nsensitivity: standard\n---\n\n## Transcript\n\n**Alice** [00:00:01] Le budget avance.\n",
        encoding="utf-8")
    library.reindex()
    semantic.reindex_all()
    with caplog.at_level("DEBUG"):
        reponse = TestClient(api_main.app).post("/api/ask", json={
            "question": "Le budget ?", "llm_provider": "compatible",
            "scope": {"mode": "project", "project_id": inbox.id}})
    assert reponse.status_code == 503
    assert "injoignable" in reponse.json()["detail"]
    assert CLE not in reponse.text and CLE not in caplog.text


# ---------- Proposé seulement s'il est configuré ----------

def test_la_configuration_ne_le_propose_qu_avec_une_url_et_un_modele(configure, monkeypatch) -> None:
    client = TestClient(api_main.app)
    corps = client.get("/api/config").json()
    assert "compatible" in corps["llm_providers"] and corps["llm_ready"]["compatible"] is True
    assert "compatible" in corps["local_providers"] and corps["private_cloud_providers"] == []
    assert corps["llm_models"]["compatible"] == MODELE
    assert CLE not in json.dumps(corps), "la clé n'est jamais renvoyée à l'écran"
    for champ in ("COMPATIBLE_BASE_URL", "COMPATIBLE_MODEL"):
        valeur = getattr(config, champ)
        monkeypatch.setattr(config, champ, "")
        corps = client.get("/api/config").json()
        assert "compatible" not in corps["llm_providers"] and corps["llm_ready"]["compatible"] is False, champ
        monkeypatch.setattr(config, champ, valeur)
    monkeypatch.setattr(config, "COMPATIBLE_BASE_URL", "http://10.20.30.40:8000/v1")
    corps = client.get("/api/config").json()
    assert "compatible" not in corps["local_providers"] and corps["private_cloud_providers"] == ["compatible"]


def test_la_relance_des_faits_refuse_un_serveur_compatible_non_configure(isolated_stores, monkeypatch) -> None:
    monkeypatch.setattr(config, "COMPATIBLE_BASE_URL", "")
    inbox = projects.create(isolated_stores["output_dir"], name=projects.INBOX_NAME, kind="inbox",
                            owner=COMPTE_DE_TEST)
    reunion = docid.nouveau()
    (isolated_stores["output_dir"] / "point.md").write_text(
        f"---\nid: {docid.nouveau()}\nmeeting_id: {reunion}\nproject_id: {inbox.id}\ntitle: Point\n"
        "language: fr\nsensitivity: standard\n---\n\n## Transcript\n\n[00:00:01] Le budget avance.\n",
        encoding="utf-8")
    library.reindex()
    reponse = TestClient(api_main.app).post(f"/api/meetings/{reunion}/ecl", params={"view_scope": inbox.id},
                                            json={"provider": "compatible"})
    assert reponse.status_code == 400


# ---------- Sensibilité : l'hôte décide ----------

def test_un_ultra_confidentiel_part_vers_un_serveur_compatible_local(configure, monkeypatch) -> None:
    monkeypatch.setattr(sensitivity, "_resolveur", sensitivity._jamais)
    assert llm.est_local("compatible")
    ultra = sensitivity.Envoi(sensibilite="ultra_confidential", proprietaire="alice")
    assert llm.chat("s", "TEXTE", provider="compatible", envoi=ultra) == "réponse du serveur"
    assert len(configure.recus) == 1


@pytest.mark.parametrize("url", ["http://10.20.30.40:11434/v1", "https://ollama.example.org/v1",
                                 "http://localhost.example.org:1234/v1"])
def test_un_ultra_confidentiel_ne_part_pas_vers_un_serveur_compatible_distant(configure, monkeypatch, url) -> None:
    monkeypatch.setattr(sensitivity, "_resolveur", sensitivity._jamais)
    monkeypatch.setattr(config, "COMPATIBLE_BASE_URL", url)
    assert not llm.est_local("compatible") and llm.auto_heberge("compatible")
    with pytest.raises(sensitivity.EnvoiRefuse):
        llm.chat("s", "TEXTE", provider="compatible",
                 envoi=sensitivity.Envoi(sensibilite="ultra_confidential", proprietaire="alice"))
    # Avec l'autorisation du propriétaire, la règle le laisse partir (comme tout cloud) ;
    # aucun hôte distant n'est joint ici : seule la règle est en jeu.
    monkeypatch.setattr(sensitivity, "_resolveur", lambda proprietaire: proprietaire == "alice")
    assert sensitivity.controler(sensitivity.Envoi(sensibilite="ultra_confidential", proprietaire="alice"),
                                 local=llm.est_local("compatible")) is True
    assert configure.recus == []


# ---------- Précision, profil, coût ----------

def test_aucune_precision_profil_prudent_et_cout_non_tarife(configure, monkeypatch) -> None:
    assert not hasattr(ecl, "taux") and not hasattr(ecl, "TAUX")
    monkeypatch.setattr(config, "COMPATIBLE_BASE_URL", "http://10.20.30.40:8000/v1")
    assert profiles.resolve("compatible") is profiles.CONSERVATIVE, "prudent, même hors de la machine"
    assert profiles.resolve("openai") is profiles.LARGE_CONTEXT
    catalogue = [*pricing_official.OFFICIAL_RATES, *pricing_official.RATES_TO_CONFIRM,
                 *pricing_official.LOCAL_ASSUMPTIONS]
    assert not [e for e in catalogue if e.get("provider") == "compatible"], "aucun tarif inventé"
    assert usage_cost.token_cost(in_tok=7, out_tok=3, cached_tok=0, snap=None) is None, "non tarifé, jamais 0"


def test_le_moteur_ne_gouverne_aucun_serveur() -> None:
    moteur = llm.moteur("compatible")
    assert not hasattr(moteur, "unload") and llm.unload("compatible") is False
    assert not hasattr(moteur, "_assurer_serveur") and not hasattr(moteur, "_demarrer")
