"""Le moteur LLM local MLX : ses PROPRIÉTÉS, pas celles de `mlx_lm`.

Aucun test de ce fichier n'exige le paquet `mlx-lm`, et c'est délibéré : ce
qu'on contrôle ici est le comportement du MOTEUR face à un serveur qui se
comporte mal. Le serveur est donc bouchonné par un VRAI serveur HTTP local, sur
un port éphémère.

Bouchonner par un vrai serveur et non par `monkeypatch` sur le client HTTP a
une conséquence qui compte : le moteur doit réellement ouvrir une connexion TCP
sur le port qu'on lui a configuré. Un moteur qui parlerait à 8080 ne trouverait
personne, et ces tests tomberaient — ce qu'un `monkeypatch.setattr(httpx,
"post", …)` n'aurait jamais su voir.

Ce choix a rendu un second service, non prévu : le moteur est passé de `httpx`
écrit à la main au SDK `openai`, et ces tests ont traversé la migration SANS
retouche de leur logique. Ils décrivent ce qu'un serveur fait subir au moteur,
pas la bibliothèque par laquelle le moteur lui parle. Des tests écrits contre
`httpx.post` auraient tous été à réécrire — et auraient dû être crus sur
parole, puisque plus rien n'aurait attesté de l'ancien comportement.

## Le cas MESURÉ que l'on reproduit

Avec un modèle introuvable : le chargement de `mlx_lm.server` est éager dans un
thread de travail, ce thread meurt sur l'exception, le serveur HTTP continue de
servir, `GET /health` répond `200 {"status":"ok"}` indéfiniment, et
`POST /v1/chat/completions` ne rend AUCUNE réponse HTTP. Serveur définitivement
zombie, et vert.

Le bouchon `mode="zombie"` est exactement cela.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from core import config, llm, llm_base, llm_mlx

pytestmark = pytest.mark.unit


# ---------- Le bouchon : un vrai serveur HTTP qui ment comme le vrai ----------

class _Poignee(BaseHTTPRequestHandler):
    # HTTP/1.0 : une connexion par requête, pas de keep-alive à démêler.
    protocol_version = "HTTP/1.0"

    def log_message(self, *args) -> None:  # noqa: D102 — silence la sortie stderr
        pass

    def _rendre(self, code: int, charge: dict) -> None:
        corps = json.dumps(charge).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def do_GET(self) -> None:  # noqa: N802 — nom imposé par http.server
        self.server.chemins_vus.append(("GET", self.path))
        if self.path == "/health":
            # TOUJOURS vert, quoi qu'il arrive au modèle. C'est le comportement
            # RÉEL de `mlx_lm.server` : `handle_health_check` écrit
            # `200 {"status": "ok"}` sans rien consulter de l'état du modèle.
            self._rendre(200, {"status": "ok"})
            return
        if self.path.startswith("/v1/models"):
            if self.server.mode == "fige":
                # Le jam TOTAL mesuré : le processus tient le port mais
                # ne sert PLUS RIEN — `/v1/models`, pourtant sans GPU, ne répond
                # pas non plus. C'est ce que `_sonde_http_vivante` doit lire en
                # ROUGE, et ce qu'aucun serveur vivant, même saturé, ne produit.
                time.sleep(self.server.attente_zombie)
                return
            # Route sans GPU, servie sur son propre thread par `mlx_lm.server` :
            # un serveur VIVANT y répond toujours, même en pleine génération. La
            # sonde de vivacité ne vérifie que cela — jamais la justesse du modèle.
            self._rendre(200, {
                "object": "list",
                "data": [{"id": "mlx-community/modele-de-test", "object": "model"}],
            })
            return
        self._rendre(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        taille = int(self.headers.get("Content-Length") or 0)
        requete = json.loads(self.rfile.read(taille) or b"{}")
        self.server.chemins_vus.append(("POST", self.path))
        self.server.requetes.append(requete)
        self.server.entetes.append({k: v for k, v in self.headers.items()})

        if self.path != "/v1/chat/completions":
            # Un serveur ÉTRANGER sur notre port n'a pas forcément cette route.
            self._rendre(404, {"error": "not found"})
            return

        if self.server.mode in ("zombie", "fige"):
            # SILENCE : la connexion reste ouverte, rien n'arrive, le CLIENT
            # finit par renoncer (`httpx.TimeoutException`).
            #
            # Ce mode s'appelait « zombie » et servait à prouver qu'un zombie est
            # tué. Il ne prouvait pas cela : dormir produit un délai dépassé, et
            # un délai dépassé est aussi ce que rend une génération saine mais
            # longue. L'instrument confondait exactement ce que le moteur
            # confondait — il ne pouvait donc pas le détecter. Le mode
            # « raccroche » ci-dessous est le vrai cas zombie.
            #
            # `fige` partage ce silence sur la génération, mais s'en distingue
            # par `do_GET` : chez `zombie` le PROCESSUS répond encore
            # (`/v1/models` vert, thread de génération mort ou occupé) ; chez
            # `fige` le processus entier est bloqué (`/v1/models` muet). C'est
            # cette différence que la sonde de vivacité exploite.
            time.sleep(self.server.attente_zombie)
            return

        if self.server.mode == "raccroche":
            # RACCROCHAGE : la connexion se ferme sans le moindre octet de
            # réponse. Côté client, une `APIConnectionError` NON-timeout.
            #
            # Ce mode a été introduit en croyant reproduire le zombie mlx_lm.
            # C'était FAUX, et mesuré faux depuis : contre un vrai serveur lancé
            # sur un modèle introuvable, la requête PEND et le client sort en
            # `APITimeoutError`. Le raccrochage n'est donc pas le zombie — c'est
            # un serveur qui meurt en cours de route, ou un tiers assis sur
            # notre port. Le mode reste, parce que ce cas-là existe et que le
            # moteur doit le tuer ; il ne prétend simplement plus être ce qu'il
            # n'est pas. Le vrai zombie se teste au DÉMARRAGE.
            self.close_connection = True
            self.connection.close()
            return

        modele = requete.get("model")
        if self.server.mode == "autre_modele":
            modele = "quelqu-un-d-autre/modele-inconnu"

        message = {"role": "assistant", "content": "OK"}
        raison = "stop"
        if self.server.mode == "sans_generation":
            message["content"] = ""
        elif self.server.mode == "json_invalide":
            # Bien formé comme texte, illisible comme JSON. C'est le cas que la
            # reprise interne du moteur est censée rattraper — et qu'elle ne
            # rattrape plus quand une graine fixe rend la sortie identique.
            message["content"] = '{"sections": [{"question_id": 1,'
        elif self.server.mode == "tronquee":
            # Le serveur s'est arrêté à `max_tokens`. Le contenu est INCOMPLET,
            # et il est du JSON coupé net — le cas mesuré à 120 jetons.
            message["content"] = '{"titre": "Réunion budg'
            raison = "length"
        elif self.server.mode == "pense":
            # Un modèle à RAISONNEMENT : `content` est nul, la sortie est dans un
            # champ hors norme OpenAI. Mesuré sur Qwen3.5-9B-4bit, et c'est ce
            # qui faisait conclure « serveur zombie » sur un serveur sain.
            message["content"] = None
            message["reasoning"] = "Thinking Process: l'utilisateur demande…"

        self._rendre(200, {
            "id": "cmpl-bouchon",
            "object": "chat.completion",
            "model": modele,
            "choices": [{
                "index": 0,
                "message": message,
                "finish_reason": raison,
            }],
        })


class _Bouchon:
    """Serveur HTTP jetable sur un port éphémère."""

    def __init__(self, mode: str = "sain", *, attente_zombie: float = 3.0) -> None:
        self.serveur = ThreadingHTTPServer(("127.0.0.1", 0), _Poignee)
        self.serveur.daemon_threads = True
        self.serveur.mode = mode
        self.serveur.attente_zombie = attente_zombie
        self.serveur.requetes = []
        self.serveur.chemins_vus = []
        self.serveur.entetes = []
        self.port = self.serveur.server_address[1]
        threading.Thread(target=self.serveur.serve_forever, daemon=True).start()

    @property
    def requetes(self) -> list[dict]:
        return self.serveur.requetes

    @property
    def chemins_vus(self) -> list[tuple[str, str]]:
        return self.serveur.chemins_vus

    @property
    def entetes(self) -> list[dict]:
        return self.serveur.entetes

    def fermer(self) -> None:
        self.serveur.shutdown()
        self.serveur.server_close()


@pytest.fixture
def bouchon():
    """Fabrique de bouchons, tous fermés en fin de test."""
    ouverts: list[_Bouchon] = []

    def fabriquer(mode: str = "sain", **kwargs) -> _Bouchon:
        b = _Bouchon(mode, **kwargs)
        ouverts.append(b)
        return b

    yield fabriquer
    for b in ouverts:
        b.fermer()


def _moteur(bouchon_: _Bouchon, **kwargs) -> llm_mlx.MlxEngine:
    """Un moteur branché sur le bouchon, avec des délais courts.

    Les délais sont COURTS et explicites : le défaut de la sonde est de 60 s, et
    un test qui attendrait ça serait retiré de la suite avant la fin de la
    semaine.
    """
    parametres = dict(
        model="mlx-community/modele-de-test",
        host="127.0.0.1",
        port=bouchon_.port,
        delai_sonde=0.4,
        delai_generation=0.4,
        delai_demarrage=1.0,
    )
    parametres.update(kwargs)
    moteur = llm_mlx.MlxEngine(**parametres)
    # Le bouchon simule NOTRE serveur déjà en marche : c'est exactement l'état
    # laissé par un `_demarrer()` réussi. Sans cette déclaration, le moteur le
    # refuserait — par la règle « ownership uniquement » (voir
    # `_orphelin_nous_appartient`), il ne parle plus à un serveur
    # dont il ne peut pas prouver la propriété, et un bouchon de test n'est pas
    # une exception à cette règle : c'est un serveur qu'il n'a pas lancé.
    moteur._processus = _EnfantVivant()
    return moteur


class _EnfantVivant:
    """Un enfant qui ne meurt pas — `poll()` rend None, comme `subprocess`."""

    pid = -1

    def poll(self):
        return None


class _EnfantArretable:
    """Un enfant qu'on peut ARRÊTER : `_arreter` doit pouvoir le terminer.

    `_EnfantVivant` n'a que `poll()` ; dès qu'un test veut vérifier que le moteur
    a bien tué SON propre serveur (chemin du jam en cours de vie), il faut aussi
    `terminate`/`wait`, comme un vrai `subprocess.Popen`.
    """

    pid = -1

    def __init__(self) -> None:
        self.arrete = False

    def poll(self):
        return None

    def terminate(self) -> None:
        self.arrete = True

    def wait(self, timeout=None) -> int:
        return 0

    def kill(self) -> None:
        self.arrete = True


# ---------- 1. `/health` n'est JAMAIS la preuve ----------

def test_le_moteur_est_disponible_quand_le_serveur_GENERE_vraiment(bouchon) -> None:
    """ANCRE DE NON-VACUITÉ, et elle est indispensable ici.

    Tous les contrôles qui suivent affirment `is_available() is False`. Un
    moteur qui rendrait TOUJOURS faux — parce qu'il ne sait pas parler à son
    serveur, parce qu'il vise le mauvais port, parce qu'une exception l'avale —
    les rendrait tous verts d'un coup. Sans ce test, la section entière ne
    prouverait rien.
    """
    b = bouchon("sain")

    assert _moteur(b).is_available() is True
    assert b.requetes, "aucune requête n'est partie : le moteur n'a rien sondé"


def test_is_available_rend_FAUX_sur_un_serveur_ZOMBIE(bouchon) -> None:
    """LE cas mesuré : `/health` vert, complétion sans aucune réponse HTTP.

    C'est la raison d'être de tout ce lot. Un moteur qui interrogerait `/health`
    conclurait « disponible » sur ce serveur — indéfiniment, sans jamais
    produire un jeton.
    """
    b = bouchon("zombie")

    assert _moteur(b).is_available() is False


def test_la_sonde_n_interroge_JAMAIS_health(bouchon) -> None:
    """La propriété du lot, vérifiée sur le trafic RÉEL, pas sur le résultat.

    `is_available()` rendrait aussi `False` sur le zombie s'il consultait
    `/health` PUIS générait. Ce serait déjà correct — mais un `/health` dans le
    chemin de disponibilité est une invitation permanente à « optimiser » en
    s'arrêtant au premier signal vert. Le bouchon consigne chaque chemin
    demandé : on exige qu'aucun ne soit `/health`.
    """
    b = bouchon("sain")

    _moteur(b).is_available()

    assert ("GET", "/health") not in b.chemins_vus, b.chemins_vus
    assert ("POST", "/v1/chat/completions") in b.chemins_vus, b.chemins_vus


def test_la_sonde_demande_une_GENERATION_bornee(bouchon) -> None:
    """« Vraie génération » veut dire un ou deux jetons, pas un prompt complet."""
    b = bouchon("sain")

    _moteur(b).is_available()

    (requete,) = b.requetes
    assert requete["max_tokens"] <= 2, requete
    assert requete["stream"] is False
    assert requete["model"] == "mlx-community/modele-de-test"


def test_is_available_rend_FAUX_si_le_serveur_repond_sans_generer(bouchon) -> None:
    """200, JSON bien formé, `content` vide : répondre n'est pas générer."""
    b = bouchon("sans_generation")

    assert _moteur(b).is_available() is False


# ---------- 2. Le modèle servi doit être celui attendu ----------

def test_is_available_rend_FAUX_quand_le_modele_servi_n_est_PAS_celui_attendu(
    bouchon,
) -> None:
    """Un serveur étranger assis sur notre port annonce SON modèle.

    C'est le second verrou du port épinglé : la collision, si elle survient
    malgré tout, ne doit pas produire un moteur silencieusement faux.
    """
    b = bouchon("autre_modele")

    assert _moteur(b).is_available() is False


def test_un_modele_different_est_REFUSE_au_lieu_d_etre_servi(bouchon) -> None:
    """Le serveur tient UN modèle. Servir l'autre en silence serait un repli silencieux."""
    b = bouchon("sain")
    moteur = _moteur(b)

    with pytest.raises(llm.LLMError) as capture:
        moteur.chat("s", "u", model="un/autre-modele")

    assert "un/autre-modele" in str(capture.value)
    assert b.requetes == [], "la requête est partie malgré le refus"


# ---------- 3. « Aucune réponse » a DEUX causes, et une seule justifie de tuer ----

def test_une_absence_de_reponse_HTTP_leve_une_LLMError(bouchon) -> None:
    """Et c'est bien `LLMError` : `api/main.py` en fait un 503.

    Une `httpx.ReadTimeout` nue sortirait en 500 brut — indiscernable d'un bug
    de notre code — et aucun des cinq `except` de l'API ne l'attraperait.
    """
    b = bouchon("zombie")

    with pytest.raises(llm.LLMError) as capture:
        _moteur(b).chat("système", "utilisateur")

    message = str(capture.value)
    assert "n'a rien rendu" in message, message
    assert str(b.port) in message, message


def test_la_meme_absence_de_reponse_traverse_la_FACADE_en_LLMError(
    monkeypatch, bouchon
) -> None:
    """La façade ne doit pas retransformer la panne en autre chose.

    `llm._normalise` remplace le message des exceptions INATTENDUES. Une panne
    déjà à la frontière doit passer telle quelle, sinon le 503 perd son seul
    diagnostic utile.
    """
    b = bouchon("zombie")
    monkeypatch.setitem(llm.MOTEURS, "mlx", _moteur(b))

    with pytest.raises(llm.LLMError) as capture:
        llm.chat("système", "utilisateur", provider="mlx")

    assert "n'a rien rendu" in str(capture.value)


def test_un_serveur_qui_RACCROCHE_est_tue_plutot_que_reessaye(
    monkeypatch, bouchon
) -> None:
    """« Redémarrer plutôt que réessayer sur un zombie », vérifié.

    Réessayer contre un serveur dont le thread de chargement est mort ne rend
    jamais rien : il faut un processus neuf. `_assurer_serveur` ne le rattrapera
    pas — le processus vit et le port est ouvert. C'est donc ICI qu'il faut
    tuer.
    """
    b = bouchon("raccroche")
    moteur = _moteur(b)
    arrets: list[str] = []
    monkeypatch.setattr(
        moteur, "_arreter", lambda: arrets.append("arrêt") or True)

    with pytest.raises(llm.LLMError) as capture:
        moteur.chat("s", "u")

    assert "raccroché" in str(capture.value), capture.value
    assert arrets == ["arrêt"], "le serveur qui a raccroché n'a pas été arrêté"


def test_le_SDK_ne_REESSAIE_pas_dans_notre_dos(bouchon) -> None:
    """`max_retries` vaut 2 par défaut chez `openai` — MESURÉ sur 2.49.0.

    Laissé tel quel, le SDK réessaierait en silence contre le serveur même que
    ce module tue exprès pour ne PAS réessayer. Combiné à la boucle de
    `chat_json(retries=2)`, cela ferait 9 requêtes là où le code en annonce 3,
    et un délai de 900 s en ferait 2700.

    Compté sur le bouchon plutôt que lu sur le client : ce qui compte est le
    nombre de requêtes REÇUES, pas la valeur d'un attribut.
    """
    b = bouchon("raccroche")

    with pytest.raises(llm.LLMError):
        _moteur(b).chat("système", "utilisateur")

    assert len(b.requetes) == 1, (
        f"{len(b.requetes)} requêtes pour un seul appel — le SDK réessaie")


def test_un_DELAI_DEPASSE_ne_tue_PAS_le_serveur(monkeypatch, bouchon) -> None:
    """La moitié du lot, et la plus importante : ne pas détruire à tort.

    Le moteur attrapait `httpx.TransportError` d'un seul tenant et tuait dans
    tous les cas. Or `TimeoutException` en hérite, et signifie « NOUS avons
    renoncé » — ce que rend aussi une génération saine mais plus longue que
    `MLX_TIMEOUT`. Le moteur tuait donc des serveurs en plein travail, au milieu
    d'un compte-rendu.

    Rien dans l'exception ne sépare les deux hypothèses. On échoue, et on laisse
    vivre : si le serveur est réellement zombie, il RACCROCHERA au prochain
    appel, et le test ci-dessus prouve qu'il sera alors arrêté.
    """
    b = bouchon("zombie")
    moteur = _moteur(b)
    arrets: list[str] = []
    monkeypatch.setattr(
        moteur, "_arreter", lambda: arrets.append("arrêt") or True)

    with pytest.raises(llm.LLMError):
        moteur.chat("s", "u")

    assert arrets == [], "un délai dépassé a détruit un serveur peut-être sain"


def test_un_statut_HTTP_non_200_est_une_panne_NOMMEE(monkeypatch, bouchon) -> None:
    """Un serveur étranger, sans la route de complétion, rend 404.

    Ce n'est ni un zombie ni un succès : il faut le DIRE. Sans ce chemin, un
    `r.json()` sur un corps d'erreur produirait une `KeyError` — un 500 brut là
    où l'API doit rendre 503.
    """
    b = bouchon("sain")
    moteur = _moteur(b)
    # On déplace la cible sur une route absente : c'est ce que verrait le moteur
    # face à un serveur qui n'est pas `mlx_lm.server`.
    monkeypatch.setattr(
        moteur, "_url", lambda chemin: f"http://127.0.0.1:{b.port}/route/absente")

    with pytest.raises(llm.LLMError) as capture:
        moteur.chat("s", "u")

    assert "404" in str(capture.value), capture.value


# ---------- 3bis. Le jam du PROCESSUS ENTIER, distinct du zombie vert ----------
#
# Mesuré : un serveur tient le port en LISTEN mais ne sert PLUS RIEN,
# `/v1/models` compris. Ce n'est pas le zombie « thread de chargement mort »
# (qui reste vert) : c'est le processus entier figé. `_sonde_http_vivante` le lit
# à son ROUGE, et c'est le seul état où l'on tue sans preuve de propriété.

def test_sonde_http_vivante_rend_VRAI_quand_le_processus_repond(
    monkeypatch, bouchon
) -> None:
    """`/v1/models` répond : le processus est VIVANT, même si la génération pend.

    Le mode `zombie` fait exactement cela — POST muet, `/v1/models` vert. La
    sonde ne juge que le processus, jamais la génération.
    """
    monkeypatch.setattr(llm_mlx, "_SONDE_HTTP_DELAI", 0.3)
    monkeypatch.setattr(llm_mlx, "_SONDE_HTTP_TENTATIVES", 2)
    b = bouchon("zombie")

    assert _moteur(b)._sonde_http_vivante() is True


def test_sonde_http_vivante_rend_FAUX_quand_le_processus_est_FIGE(
    monkeypatch, bouchon
) -> None:
    """`/v1/models` muet : le processus entier est bloqué. Le seul ROUGE qu'on tue."""
    monkeypatch.setattr(llm_mlx, "_SONDE_HTTP_DELAI", 0.3)
    monkeypatch.setattr(llm_mlx, "_SONDE_HTTP_TENTATIVES", 2)
    b = bouchon("fige", attente_zombie=5.0)

    assert _moteur(b)._sonde_http_vivante() is False


def test_sonde_http_vivante_TOLERE_un_rate_transitoire(monkeypatch, bouchon) -> None:
    """Un `False` déclenche un arrêt forcé : on ne l'inflige qu'après N échecs.

    Le premier GET est saboté, les suivants passent : la sonde doit rendre VRAI,
    sinon un `scan_cache_dir()` momentanément lent ferait tuer un serveur sain.
    """
    monkeypatch.setattr(llm_mlx, "_SONDE_HTTP_DELAI", 0.3)
    monkeypatch.setattr(llm_mlx, "_SONDE_HTTP_TENTATIVES", 3)
    b = bouchon("sain")
    moteur = _moteur(b)

    vrai_get = httpx.Client.get
    etat = {"premier": True}

    def get_capricieux(self, url, *a, **k):
        if etat["premier"]:
            etat["premier"] = False
            raise httpx.ConnectError("raté transitoire")
        return vrai_get(self, url, *a, **k)

    monkeypatch.setattr(httpx.Client, "get", get_capricieux)

    assert moteur._sonde_http_vivante() is True


def test_un_processus_FIGE_est_ARRETE_sur_timeout(monkeypatch, bouchon) -> None:
    """Timeout ET `/v1/models` muet = jam total : on retire le serveur.

    Le jam total rattrapé EN COURS DE VIE. Sans cela, chaque appel suivant
    repayait `MLX_TIMEOUT` sur un cadavre — jusqu'à 45 minutes d'attente.
    """
    monkeypatch.setattr(llm_mlx, "_SONDE_HTTP_DELAI", 0.3)
    monkeypatch.setattr(llm_mlx, "_SONDE_HTTP_TENTATIVES", 2)
    b = bouchon("fige", attente_zombie=5.0)
    moteur = _moteur(b)
    enfant = _EnfantArretable()
    moteur._processus = enfant

    with pytest.raises(llm.LLMError) as capture:
        moteur.chat("s", "u")

    message = str(capture.value)
    assert "FIGÉ" in message, message
    assert enfant.arrete, "le processus figé n'a pas été arrêté"


def test_un_processus_VIVANT_mais_lent_n_est_PAS_arrete_sur_timeout(
    monkeypatch, bouchon
) -> None:
    """Timeout mais `/v1/models` VERT = génération longue, ou thread mort : LAISSÉ EN VIE.

    L'invariant du lot, préservé : on ne détruit JAMAIS sur un timeout nu. La
    sonde n'assouplit la règle que dans un sens — le ROUGE — jamais l'autre.
    """
    monkeypatch.setattr(llm_mlx, "_SONDE_HTTP_DELAI", 0.3)
    monkeypatch.setattr(llm_mlx, "_SONDE_HTTP_TENTATIVES", 2)
    b = bouchon("zombie")  # POST muet, mais `/v1/models` répond vite
    moteur = _moteur(b)
    enfant = _EnfantArretable()
    moteur._processus = enfant

    with pytest.raises(llm.LLMError) as capture:
        moteur.chat("s", "u")

    assert "LAISSÉ EN VIE" in str(capture.value), capture.value
    assert not enfant.arrete, "un serveur peut-être sain a été détruit sur un timeout"


# ---------- 4. Le port est celui de la configuration, jamais 8080 ----------

def test_le_port_par_defaut_vient_de_la_CONFIGURATION(monkeypatch) -> None:
    """Résolu à l'appel, pas dans la signature.

    Une valeur par défaut dans la signature est liée à la DÉFINITION : changer
    la configuration ne la déplacerait jamais, et l'épinglage du port
    dépendrait d'un rechargement de module.
    """
    monkeypatch.setattr(config, "MLX_PORT", 8799)

    assert llm_mlx.MlxEngine().port == 8799


def test_la_configuration_epingle_un_port_de_la_plage_memoglyph() -> None:
    """8080 est le défaut de `mlx_lm.server`, et il est trop souvent pris.

    Une collision ne se voit pas : le serveur d'en face répond. C'est le premier
    des deux verrous — le second est la vérification du modèle servi.

    L'interface est sur 8770 et l'API sur 8771 ; `start.sh` balaie les paires
    8770/8771, 8780/8781, … par pas de dix, donc 8772 ne peut pas lui être pris.
    """
    assert config.MLX_PORT != 8080
    assert 8770 <= config.MLX_PORT <= 8779
    assert config.MLX_PORT not in (8770, 8771), "port de l'interface ou de l'API"


def test_le_processus_ENFANT_recoit_le_port_epingle() -> None:
    """Le port doit atteindre `mlx_lm.server`, pas seulement notre client HTTP.

    Sans `--port`, l'enfant écouterait 8080 et notre client parlerait dans le
    vide — ou pire, au serveur de quelqu'un d'autre.
    """
    argv = llm_mlx.MlxEngine(port=8772, model="org/m", host="127.0.0.1").commande_serveur()

    assert "--port" in argv
    assert argv[argv.index("--port") + 1] == "8772"
    assert "8080" not in argv, argv
    assert argv[argv.index("--model") + 1] == "org/m"


def test_le_client_HTTP_parle_bien_au_port_configure(bouchon) -> None:
    """Preuve par le trafic : le bouchon a REÇU la requête.

    Un contrôle sur `commande_serveur()` seul laisserait passer un client qui
    viserait un autre port que l'enfant.
    """
    b = bouchon("sain")

    _moteur(b).is_available()

    assert len(b.requetes) == 1


def test_le_plafond_de_512_JETONS_du_serveur_est_releve(monkeypatch) -> None:
    """Sans `--max-tokens`, un compte-rendu ordinaire est COUPÉ. Mesuré.

    `mlx_lm.server` a un défaut CLI de 512 jetons, dont hérite toute requête qui
    omet le champ. Or `LLMEngine.chat()` n'expose aucun `max_tokens` — le
    contrat ne l'a pas, et Ollama n'en a pas besoin. MESURÉ contre un vrai
    serveur : un compte-rendu de réunion échouait en `MlxLLMOutputLimitError`,
    en conseillant d'augmenter une valeur que l'appelant ne peut pas toucher.

    Le plafond est donc porté par le SERVEUR, une fois, à son lancement.
    """
    monkeypatch.setattr(config, "MLX_MAX_TOKENS", 16384)
    argv = llm_mlx.MlxEngine().commande_serveur()

    assert "--max-tokens" in argv, argv
    assert argv[argv.index("--max-tokens") + 1] == "16384", argv


def test_le_cache_de_prompt_est_borne_en_OCTETS_et_pas_seulement_en_nombre(
    monkeypatch,
) -> None:
    """`mlx_lm.server` borne son cache à dix ENTRÉES, et à aucune TAILLE.

    Sur nos prompts, cette borne-là est sans effet. MESURÉ sur cinq
    comptes-rendus enchaînés : prompts de 24 074 jetons, et le journal du
    serveur annonce « Prompt Cache: 10 sequences, 8.97 GB » — la moitié des
    18 Go de la machine, pour un modèle de 2,1 Go. La génération suivante meurt
    sur `[METAL] Insufficient Memory`.

    Ce qui rend la panne coûteuse : le thread de génération meurt SANS RÉPONDRE.
    La requête reste ouverte, le client attend `MLX_TIMEOUT` en entier, une fois
    par tentative du contrat — trois quarts d'heure pour rien. Et le serveur
    répond toujours `/v1/models` en 136 ms, donc rien ne le désigne.

    Le drapeau est porté par le SERVEUR à son lancement : aucune requête ne peut
    le rattraper après coup.
    """
    monkeypatch.setattr(config, "MLX_PROMPT_CACHE_BYTES", 2 * 1024**3)
    argv = llm_mlx.MlxEngine().commande_serveur()

    assert "--prompt-cache-bytes" in argv, argv
    assert argv[argv.index("--prompt-cache-bytes") + 1] == str(2 * 1024**3), argv


# ---------- 5. La supervision du processus enfant ----------

def _port_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _MoteurAvecEnfantFactice(llm_mlx.MlxEngine):
    """Moteur dont l'enfant est un script Python, pas `mlx_lm.server`.

    On substitue les DEUX points qui dépendent de l'environnement — la présence
    du paquet et la ligne de commande — et rien d'autre : la supervision
    exercée ici est exactement celle du code de production.
    """

    def __init__(self, script: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._script = script

    def _verifier_paquet(self) -> None:
        return None

    def commande_serveur(self) -> list[str]:
        import sys

        return [sys.executable, "-c", self._script]


def _attendre(predicat, limite: float = 5.0) -> bool:
    fin = time.monotonic() + limite
    while time.monotonic() < fin:
        if predicat():
            return True
        time.sleep(0.02)
    return False


def test_le_code_de_sortie_de_l_enfant_est_SURVEILLE(monkeypatch) -> None:
    """Un enfant mort au démarrage doit être NOMMÉ, avec son code.

    Sans cela, le symptôme observable serait « le port ne s'ouvre pas » — vrai,
    inutile, et indiscernable d'un démarrage lent.
    """
    monkeypatch.setattr(llm_mlx, "_journaliser", lambda message: None)
    moteur = _MoteurAvecEnfantFactice(
        "import sys; sys.exit(3)",
        port=_port_libre(), delai_demarrage=5.0,
    )

    with pytest.raises(llm.LLMError) as capture:
        moteur._assurer_serveur()

    assert "code 3" in str(capture.value), capture.value


def test_un_enfant_MORT_NE_ne_laisse_aucun_temoin_de_propriete(monkeypatch, tmp_path) -> None:
    """Une preuve de propriété périmée est retirée, pas laissée à traîner.

    Le témoin est écrit AVANT que l'enfant ait prouvé quoi que ce soit, et c'est
    délibéré : `_recuperer_orphelin` en dépend, car seule une preuve écrite
    avant la mort du parent survit à un `SIGKILL`. La contrepartie est qu'un
    enfant mort-né en laisse une, et cette sortie-là doit la retirer elle-même —
    les deux autres passent par `_arreter()`, qui le fait déjà.

    MESURÉ avant la correction : 59 fichiers `.owner` accumulés dans `.cache/`
    en deux jours, par PAIRES — les deux tests de supervision de ce fichier dont
    l'enfant sort avec un code non nul, une paire par exécution de la suite.

    Aucun ne faisait tuer personne : `_orphelin_nous_appartient` exige un PID
    vivant, et un PID mort est rejeté. Ce qui se dégradait n'était pas la
    correction mais la LISIBILITÉ — un dossier de preuves qui se remplit de
    preuves mortes est un instrument qu'on finit par ne plus consulter, et c'est
    tout ce que ce mécanisme avait à offrir.
    """
    monkeypatch.setattr(llm_mlx, "_journaliser", lambda message: None)
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)

    # Le témoin doit avoir ÉTÉ ÉCRIT pour que son absence finale prouve un
    # RETRAIT. Sans ce relevé, une version qui n'en écrirait jamais aucun
    # rendrait ce test vert — le contrôle décoratif que ce dépôt traque partout.
    ecrits: list[tuple] = []
    inscrire = llm_mlx.MlxEngine._inscrire_propriete

    def espion(self, pid: int) -> None:
        inscrire(self, pid)
        ecrits.append((self._temoin_propriete, self._temoin_propriete.exists()))

    monkeypatch.setattr(llm_mlx.MlxEngine, "_inscrire_propriete", espion)
    moteur = _MoteurAvecEnfantFactice(
        "import sys; sys.exit(3)",
        port=_port_libre(), delai_demarrage=5.0,
    )

    with pytest.raises(llm.LLMError):
        moteur._assurer_serveur()

    assert ecrits, "aucun témoin écrit : ce test ne prouverait rien"
    chemin, present_a_l_ecriture = ecrits[0]
    assert present_a_l_ecriture, "le témoin n'a jamais atteint le disque"
    assert not chemin.exists(), "le témoin d'un enfant mort-né survit dans le cache"
    assert list(tmp_path.iterdir()) == [], list(tmp_path.iterdir())


def test_le_stderr_de_l_enfant_atterrit_dans_le_JOURNAL(monkeypatch) -> None:
    """Le chargement échoue AVANT toute requête et n'est visible QUE là.

    Sans drainage, le tube se remplit, l'enfant finit par bloquer, et la seule
    trace côté Memoglyph serait une requête sans réponse — c'est-à-dire le
    zombie, avec sa cause perdue.
    """
    lignes: list[str] = []
    monkeypatch.setattr(llm_mlx, "_journaliser", lignes.append)
    moteur = _MoteurAvecEnfantFactice(
        "import sys; sys.stderr.write('ValueError: Model not found\\n'); "
        "sys.stderr.flush(); sys.exit(1)",
        port=_port_libre(), delai_demarrage=5.0,
    )

    with pytest.raises(llm.LLMError):
        moteur._assurer_serveur()

    assert _attendre(lambda: any("Model not found" in ligne for ligne in lignes)), lignes


def test_un_serveur_DEJA_la_n_est_pas_double(monkeypatch, bouchon) -> None:
    """Deux jobs de front ne doivent pas produire deux serveurs de 5,6 Go.

    Le moteur regarde si le port est PRIS — par une connexion TCP, pas par
    `/health` : la seule question ici est « dois-je lancer un processus », et
    elle ne demande rien au serveur.
    """
    b = bouchon("sain")
    moteur = _moteur(b)

    def interdit(*args, **kwargs):
        raise AssertionError("un second serveur a été lancé sur un port occupé")

    monkeypatch.setattr(llm_mlx.subprocess, "Popen", interdit)

    assert moteur.is_available() is True


_ZOMBIE_FIDELE = """
import http.server, sys, time

class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    def log_message(self, *a): pass
    def do_GET(self):
        # Vert quoi qu'il arrive, comme le vrai.
        self.send_response(200)
        self.send_header("Content-Length", "15")
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')
    def do_POST(self):
        # PEND. Ne raccroche pas, ne répond pas. C'est le comportement MESURÉ
        # d'un mlx_lm.server dont le thread de chargement est mort.
        time.sleep(3600)

http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
"""


def test_un_ZOMBIE_est_attrape_au_DEMARRAGE_et_tue(monkeypatch) -> None:
    """Le cas que le moteur existe pour refuser, contre un enfant FIDÈLE.

    MESURÉ sur un vrai `mlx_lm.server` lancé avec un modèle introuvable : le
    port s'ouvre, `/health` répond 200, et la complétion PEND — le client sort
    par un délai dépassé, jamais par un raccrochage.

    Deux conceptions se sont trompées là-dessus. La première tuait sur tout
    « pas de réponse », donc aussi sur une génération saine mais longue. La
    seconde ne tuait plus que sur un raccrochage — un signal que le zombie ne
    produit JAMAIS, si bien qu'aucun zombie n'était plus jamais remplacé.

    Ce qui tranche n'est pas l'exception mais le MOMENT : au démarrage on ne
    demande que deux jetons, et ne pas les rendre n'a aucune explication
    innocente.
    """
    port = _port_libre()

    class Moteur(_MoteurAvecEnfantFactice):
        def commande_serveur(self):
            import sys
            return [sys.executable, "-c", _ZOMBIE_FIDELE, str(port)]

    moteur = Moteur(
        "", model="mlx-community/modele-de-test", host="127.0.0.1", port=port,
        delai_sonde=1.5, delai_generation=1.5, delai_demarrage=8.0,
    )

    with pytest.raises(llm.LLMError) as capture:
        moteur.chat("système", "utilisateur")

    message = str(capture.value)
    assert "n'a pas su générer" in message, message
    # Et il est TUÉ : un cadavre laissé en place tiendrait le port et sa
    # mémoire, et le prochain appel s'y raccrocherait sans jamais le détecter.
    assert moteur._processus is None, "le zombie a survécu au démarrage"


def test_le_paquet_absent_donne_une_erreur_NOMMEE(monkeypatch) -> None:
    """Et pas un « ModuleNotFoundError » du sous-processus, trois secondes plus tard."""
    monkeypatch.setattr(llm_mlx.importlib.util, "find_spec", lambda nom: None)
    moteur = llm_mlx.MlxEngine(port=_port_libre())

    with pytest.raises(llm.LLMError) as capture:
        moteur._assurer_serveur()

    assert "mlx-lm" in str(capture.value)


# ---------- 6. `unload()` ne lève jamais ----------

def test_unload_ne_leve_jamais_meme_sans_serveur() -> None:
    """Le contrat de la façade l'exige : l'appel arrive APRÈS l'écriture du
    transcript, et ne doit pas transformer un traitement réussi en échec."""
    assert llm_mlx.MlxEngine(port=_port_libre()).unload() is False


def test_unload_ne_leve_pas_quand_l_ARRET_du_processus_echoue(monkeypatch) -> None:
    """Un `terminate()` qui lève est absorbé, et rendu par la valeur.

    Écrit après avoir constaté que la garde de `core/llm.py` couvrirait déjà ce
    cas : elle le couvre pour la FAÇADE, pas pour un appelant qui tiendrait le
    moteur en main — et `unload()` est une méthode publique du contrat.
    """
    moteur = llm_mlx.MlxEngine(port=_port_libre())

    class _ProcessusRecalcitrant:
        returncode = None

        def terminate(self):
            raise OSError("le processus ne répond plus")

        def poll(self):
            return None

    monkeypatch.setattr(llm_mlx, "_journaliser", lambda message: None)
    moteur._processus = _ProcessusRecalcitrant()

    assert moteur.unload() is False


def test_unload_ARRETE_reellement_l_enfant(monkeypatch) -> None:
    """Ancre de non-vacuité des deux tests ci-dessus : `unload()` qui rendrait
    toujours `False` sans rien tuer les satisferait tous les deux, en laissant
    les 5,6 Go en place."""
    monkeypatch.setattr(llm_mlx, "_journaliser", lambda message: None)
    moteur = _MoteurAvecEnfantFactice(
        "import time; time.sleep(30)", port=_port_libre())

    class _ProcessusFactice:
        def __init__(self):
            self.tue = False
            self.returncode = 0

        def poll(self):
            return None

        def terminate(self):
            self.tue = True

        def wait(self, timeout=None):
            return 0

    processus = _ProcessusFactice()
    moteur._processus = processus

    assert moteur.unload() is True
    assert processus.tue is True


# ---------- 7. Le moteur est dans le registre, et son constructeur est inerte ----------

def test_le_moteur_mlx_est_un_fournisseur_du_registre() -> None:
    assert "mlx" in llm.FOURNISSEURS
    assert llm.moteur("mlx").name == "mlx"
    assert isinstance(llm.MOTEURS["mlx"], llm_mlx.MlxEngine)


def test_le_constructeur_ne_lance_NI_processus_NI_requete(monkeypatch) -> None:
    """L'instance est construite à l'import de `core/llm.py`.

    Un constructeur qui démarrerait le serveur ferait payer 5,6 Go et plusieurs
    secondes à chaque import de `core.llm` — donc à toute la suite de tests, à
    `run_ml_tests.py`, et aux scripts d'audit.
    """
    def interdit(*args, **kwargs):
        raise AssertionError("le constructeur a lancé un processus ou une requête")

    monkeypatch.setattr(llm_mlx.subprocess, "Popen", interdit)
    # Le client du SDK est visé à sa CONSTRUCTION, pas à son usage : `OpenAI()`
    # lit l'environnement et monte un pool de connexions. Le construire à
    # l'import de `core/llm.py` ferait payer ce coût à toute la suite de tests.
    monkeypatch.setattr(llm_mlx.openai, "OpenAI", interdit)

    moteur = llm_mlx.MlxEngine()

    assert moteur._processus is None
    assert moteur._client is None, "le client SDK a été construit trop tôt"


def test_chat_rend_le_texte_genere(bouchon) -> None:
    """Ancre : sans lui, un moteur qui lèverait toujours passerait la section 3."""
    b = bouchon("sain")

    assert _moteur(b).chat("système", "utilisateur") == "OK"


def test_chat_json_porte_le_SCHEMA_jusqu_au_modele(bouchon) -> None:
    """Le schéma ne peut pas contraindre le décodage ici : il doit donc au moins
    ATTEINDRE le modèle. Un moteur qui accepterait l'argument pour le jeter
    produirait un compte-rendu hors contrat sans le moindre signal."""
    b = bouchon("sain")
    b.serveur.mode = "sain"
    moteur = _moteur(b)
    schema = {"type": "object", "properties": {"sections": {"type": "array"}}}

    with pytest.raises(llm.LLMError):
        # Le bouchon rend « OK », pas du JSON : l'appel échoue après ses essais.
        moteur.chat_json("système", "utilisateur", schema=schema,
                         max_tokens=4096, retries=0)

    (requete,) = b.requetes
    directive = requete["messages"][0]["content"]
    assert "sections" in directive, directive
    assert requete["max_tokens"] == 4096


def test_le_schema_n_est_PAS_envoye_comme_response_format(bouchon) -> None:
    """MESURÉ : mlx-lm 0.31.3 accepte `response_format` et l'IGNORE.

    Un schéma exigeant deux champs inventés a rendu, trois fois de suite, une
    sortie qui n'en contenait aucun — identique à celle obtenue sans schéma.
    L'envoyer donnerait l'apparence d'une contrainte de décodage là où il n'y en
    a pas : le lecteur du code croirait la sortie garantie, et la vérification
    au retour paraîtrait redondante alors qu'elle est la SEULE garantie.
    """
    b = bouchon("sain")

    with pytest.raises(llm.LLMError):
        _moteur(b).chat_json(
            "système", "utilisateur",
            schema={"type": "object", "properties": {"x": {"type": "string"}}},
            retries=0,
        )

    (requete,) = b.requetes
    assert "response_format" not in requete, requete


# ---------- 7bis. Le trafic reste LOCAL, et ne porte rien de l'environnement ----

def test_un_PROXY_de_l_environnement_ne_DETOURNE_pas_le_transcript(
    monkeypatch, bouchon
) -> None:
    """La garde de confidentialité du moteur, de gravité bloquante.

    Le SDK `openai` monte par défaut un `httpx.Client` avec `trust_env=True`.
    `httpx` lit alors `urllib.request.getproxies()` et route même
    `http://127.0.0.1:…` vers le proxy trouvé — il n'exempte PAS la boucle
    locale. Le corps complet de la requête, directive système et TRANSCRIPT
    compris, part chez ce proxy, et l'appel RÉUSSIT : rien ne signale la fuite.

    Sur macOS, `getproxies()` lit en plus les Réglages Système : un VPN
    d'entreprise ou un Proxyman suffit, sans qu'aucune variable ne soit posée.

    Le montage reproduit la production : un serveur mlx SAIN tourne bien en
    local, et un proxy est configuré. On vérifie qui reçoit le transcript.
    """
    local = bouchon("sain")
    proxy = bouchon("sain")
    for variable in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(variable, f"http://127.0.0.1:{proxy.port}")

    _moteur(local).chat("directive système", "TRANSCRIPT CONFIDENTIEL DE RÉUNION")

    assert proxy.requetes == [], (
        "le transcript est parti chez le proxy de l'environnement")
    assert len(local.requetes) == 1, "le serveur local n'a pas reçu la requête"


def test_aucun_en_tete_d_organisation_OpenAI_n_est_envoye(monkeypatch, bouchon) -> None:
    """`OPENAI_ORG_ID` et `OPENAI_PROJECT_ID` ne doivent pas fuir vers le local.

    MESURÉ SUR LE FIL : sans garde, le SDK pose `OpenAI-Organization` et
    `OpenAI-Project` depuis l'environnement sur CHAQUE requête, y compris vers
    notre serveur. Passer `organization=None` ne change rien — c'est justement
    ce `None` qui déclenche le repli (`openai/_client.py`). Et lire
    `client.default_headers` ne prouve rien : le sentinelle `Omit()` y reste
    visible et n'est retiré qu'à la construction de la requête.

    On compte donc ce que le SERVEUR a reçu, pas ce que le client déclare.
    """
    monkeypatch.setenv("OPENAI_ORG_ID", "org-a-ne-pas-divulguer")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "proj-a-ne-pas-divulguer")
    b = bouchon("sain")

    _moteur(b).chat("système", "utilisateur")

    (entetes,) = b.entetes
    fuites = {k: v for k, v in entetes.items()
              if "organization" in k.lower() or "project" in k.lower()}
    assert fuites == {}, f"en-têtes d'organisation envoyés au serveur local : {fuites}"


# ---------- 8. Une réponse tronquée est INCOMPLÈTE, et le dit ----------

def test_une_reponse_COUPEE_a_la_limite_de_jetons_est_refusee(bouchon) -> None:
    """Pendant du `done_reason == "length"` d'Ollama.

    Sans ce contrôle, un compte-rendu amputé en plein mot descendait dans le
    document final sans un signal.
    """
    b = bouchon("tronquee")

    with pytest.raises(llm_mlx.MlxLLMOutputLimitError) as capture:
        _moteur(b).chat("système", "utilisateur")

    assert "amputée" in str(capture.value), capture.value


def test_une_sortie_tronquee_est_CONSERVEE_sur_l_exception() -> None:
    """On la jetait, et il fallait REPRODUIRE la panne pour savoir ce qui
    s'était passé — sept minutes de génération à chaque fois.

    Elle n'est jamais rendue comme un contenu valide : elle voyage sur
    l'exception, à disposition du diagnostic et des mesures.
    """
    corps = {
        "choices": [{"finish_reason": "length",
                     "message": {"content": '{"sections": [{"question_id": 1'}}],
        "usage": {"completion_tokens": 6144},
    }

    with pytest.raises(llm_mlx.MlxLLMOutputLimitError) as capture:
        llm_mlx.MlxEngine._refuser_une_reponse_tronquee(corps)

    assert capture.value.contenu_tronque == '{"sections": [{"question_id": 1'
    assert capture.value.boucle is None


def test_une_BOUCLE_est_nommee_et_le_message_cesse_d_accuser_le_document() -> None:
    """MESURÉ : un compte-rendu légitime coûte 1 100 à 2 500 jetons, le
    contrat en autorise 3 300 au maximum, et trois modèles de trois éditeurs en
    brûlent 6 144 sans fermer leur JSON. Ils ne produisent pas un long document,
    ils dégénèrent.

    Le message disait « traite un extrait plus court ». C'est faux et coûteux :
    l'utilisateur raccourcirait son document pour rien. Quand la boucle est
    détectée, le message doit le DIRE.
    """
    motif = '{"timestamp": "00:00:04", "quote": "Bonjour."}, '
    corps = {
        "choices": [{"finish_reason": "length",
                     "message": {"content": '{"sections": [' + motif * 12}}],
        "usage": {"completion_tokens": 6144},
    }

    with pytest.raises(llm_mlx.MlxLLMOutputLimitError) as capture:
        llm_mlx.MlxEngine._refuser_une_reponse_tronquee(corps)

    periode, repetitions, _ = capture.value.boucle
    assert periode == len(motif)
    assert repetitions >= 3
    message = str(capture.value)
    assert "BOUCLÉ" in message
    assert "extrait plus court" not in message, (
        "raccourcir le document ne change rien à une boucle")
    assert "Le document n\'est pas en cause" in message


@pytest.mark.parametrize("texte", [
    "",
    "trop court",
    '{"sections": [{"question_id": 1, "title": "Décision", "claims": [',
])
def test_le_detecteur_de_boucle_ne_voit_pas_de_boucle_ou_il_n_y_en_a_pas(texte) -> None:
    """Un détecteur qui crie au loup ferait accuser le modèle à tort, et
    masquerait la vraie cause d'une troncature ordinaire."""
    assert llm_mlx._empreinte_de_boucle(texte) is None


def test_le_message_de_troncature_est_ACTIONNABLE_par_qui_le_recoit() -> None:
    """Ce message REMONTE : la route du compte-rendu le rend tel quel dans un
    503, à un utilisateur qui n'a aucun accès à `max_tokens` — ce paramètre
    n'est exposé nulle part dans l'interface.

    Il disait pourtant « Augmente `max_tokens` ». Le dépôt avait DÉJÀ relevé cet
    anti-motif pour le plafond de 512 jetons du serveur : « un message qui
    conseille d'augmenter une valeur que l'appelant ne peut pas toucher ». Il
    avait été réintroduit ici.

    Ce que l'utilisateur peut faire, lui, c'est changer de moteur ou raccourcir
    le document. C'est ce que le message doit dire.
    """
    corps = {"choices": [{"finish_reason": "length"}],
             "usage": {"completion_tokens": 6144}}

    with pytest.raises(llm_mlx.MlxLLMOutputLimitError) as capture:
        llm_mlx.MlxEngine._refuser_une_reponse_tronquee(corps)

    message = str(capture.value)
    assert "max_tokens" not in message, (
        "ne pas renvoyer l'utilisateur vers un paramètre qu'il n'a pas")
    assert "Mistral" in message, "l'issue praticable doit être nommée"
    # Le compte reste, mais pour le DIAGNOSTIC — pas comme une consigne.
    assert "6144" in message


def test_une_reponse_tronquee_n_est_PAS_reessayee(bouchon) -> None:
    """Le budget de jetons ne change pas d'un essai à l'autre.

    Réessayer trois fois un appel condamné par son `max_tokens` coûte trois
    générations, puis rend « JSON invalide » — un diagnostic FAUX, qui envoie
    chercher le défaut du côté du modèle au lieu du réglage.
    """
    b = bouchon("tronquee")

    with pytest.raises(llm_mlx.MlxLLMOutputLimitError):
        _moteur(b).chat_json("système", "utilisateur", max_tokens=8, retries=2)

    assert len(b.requetes) == 1, f"{len(b.requetes)} essais pour un budget figé"


def test_la_SONDE_survit_a_une_reponse_tronquee(bouchon) -> None:
    """Le verrou ne doit pas condamner ce qu'il protège.

    La sonde de `is_available()` demande DEUX jetons : sa réponse est donc
    toujours coupée à la limite. Mettre le contrôle de troncature dans
    `_contenu`, partagé avec elle, rendrait le moteur définitivement
    indisponible — le motif de l'instrument qui détruit sa propre mesure.
    """
    b = bouchon("tronquee")

    assert _moteur(b).is_available() is True


def test_la_limite_de_sortie_reste_un_marqueur_503() -> None:
    """`api/main.py` traduit `LLMError` en 503 ; une classe hors de cette
    parenté rendrait un 500 brut sur une simple réponse trop longue."""
    assert issubclass(llm_mlx.MlxLLMOutputLimitError, llm_base.LLMError)


# ---------- 9. Un modèle qui RÉFLÉCHIT est nommé, pas diagnostiqué à tort ----------

def test_un_modele_a_raisonnement_accuse_la_CONFIGURATION_pas_le_serveur(
    bouchon,
) -> None:
    """Le défaut qui a détruit des serveurs sains.

    MESURÉ sur Qwen3.5-9B-4bit : `content` est nul, la sortie part dans un champ
    `reasoning` hors norme OpenAI. Le moteur y lisait « aucune génération »,
    donc « serveur zombie », et tuait un serveur qui répondait parfaitement.

    Le modèle par défaut ne réfléchit pas, mais rien n'empêche d'en configurer
    un qui le fasse : le message doit alors envoyer vers la variable
    d'environnement, et non vers une panne inexistante.
    """
    b = bouchon("pense")

    with pytest.raises(llm.LLMError) as capture:
        _moteur(b).chat("système", "utilisateur")

    message = str(capture.value)
    assert "RÉFLÉCHIT" in message, message
    assert "MEMOGLYPH_MLX_LLM_MODEL" in message, message


def test_un_modele_a_raisonnement_ne_fait_pas_TUER_le_serveur(
    monkeypatch, bouchon
) -> None:
    """Le serveur va bien : c'est la configuration qui est en cause.

    C'est la moitié destructrice du défaut, et elle se teste séparément — un
    message juste accompagné d'un serveur détruit resterait le mauvais
    comportement.
    """
    b = bouchon("pense")
    moteur = _moteur(b)
    arrets: list[str] = []
    monkeypatch.setattr(moteur, "_arreter", lambda: arrets.append("arrêt") or True)

    with pytest.raises(llm.LLMError):
        moteur.chat("système", "utilisateur")

    assert arrets == [], "un serveur SAIN a été détruit pour un modèle mal choisi"


# ---------------------------------------------------------------------------
# Le relevé de diagnostic
# ---------------------------------------------------------------------------


def test_le_diagnostic_nomme_le_detournement_par_le_serveur() -> None:
    """`mlx_lm.server` déduit les capacités du modèle de son VOCABULAIRE et de
    son gabarit, jamais de son comportement : `<think>` et `<tool_call>`
    présents suffisent à installer des transitions d'état. Seul le texte en
    état normal alimente `content` ; le reste part dans `reasoning` ou
    `tool_calls`, et `finish_reason` vaut alors `stop`, jamais `length`.

    `content` est donc un préfixe coupé avec une fin déclarée normale — la
    panne que « réponse invalide » confondait avec un modèle trop petit.
    """
    corps = {
        "choices": [{
            "finish_reason": "tool_calls",
            "message": {"content": '{"sections": [', "tool_calls": [{"id": "x"}]},
        }],
        "usage": {"prompt_tokens": 5932, "completion_tokens": 812},
    }
    releve = llm_mlx._diagnostic_reponse(corps, '{"sections": [')

    assert "detournement=tool_calls" in releve
    assert "finish_reason='tool_calls'" in releve
    assert "prompt_tokens=5932" in releve
    assert "completion_tokens=812" in releve


def test_le_diagnostic_montre_la_FIN_et_pas_seulement_le_debut() -> None:
    """Un JSON coupé net se distingue d'un JSON complet noyé dans de la prose
    par sa DERNIÈRE ligne, jamais par sa première.

    Le message d'origine n'affichait que `dernier[:300]`, ce qui laissait croire
    que le modèle s'arrêtait toujours au même endroit — alors que c'était le
    point de coupe de l'affichage. Le relevé doit donc porter la fin.
    """
    contenu = "PRÉFIXE" + "x" * 4000 + "FIN_RECONNAISSABLE"
    releve = llm_mlx._diagnostic_reponse({}, contenu)

    assert "FIN_RECONNAISSABLE" in releve, "la fin de la réponse est invisible"
    assert f"len_contenu={len(contenu)}" in releve, "la longueur réelle manque"


def test_le_diagnostic_ne_leve_jamais_sur_un_corps_degenere() -> None:
    """Il est appelé sur le chemin d'ERREUR : s'il levait, il masquerait la
    panne qu'il est censé décrire, et la remplacerait par la sienne."""
    for corps in ({}, {"choices": []}, {"choices": [{}]},
                  {"choices": [{"message": None}], "usage": "pas une table"}):
        releve = llm_mlx._diagnostic_reponse(corps, "")
        assert "detournement=aucun" in releve


def test_la_GRAINE_atteint_vraiment_le_serveur(bouchon) -> None:
    """`seed` est RÉELLEMENT honoré, contrairement à `response_format`.

    `mlx_lm/server.py` le VALIDE (`self._validate("seed", int)`) puis l'applique
    (`mx.random.seed(args.seed)`). La distinction est tout le sujet de ce
    module : un paramètre accepté n'est pas un paramètre appliqué, et le dépôt
    s'est déjà fait prendre une fois par `response_format`.

    Ce témoin ne prouve pas que le serveur en fait quelque chose — il prouve
    qu'on le lui ENVOIE. La preuve d'application est dans son code source, citée
    ci-dessus.
    """
    b = bouchon("sain")

    _moteur(b)._completion("système", "utilisateur", max_tokens=8,
                           delai=5.0, seed=42)

    assert b.requetes[-1].get("seed") == 42, b.requetes[-1]


def test_sans_graine_aucun_champ_seed_n_est_envoye(bouchon) -> None:
    """Un `seed: null` explicite serait refusé par la validation du serveur —
    `_validate("seed", int, optional=True)` accepte l'ABSENCE, pas un nul."""
    b = bouchon("sain")

    _moteur(b)._completion("système", "utilisateur", max_tokens=8, delai=5.0)

    assert "seed" not in b.requetes[-1], b.requetes[-1]


def test_une_graine_FIXE_supprime_la_reprise_interne(bouchon) -> None:
    """Elle rejouerait une requête identique pour une sortie identique.

    Le serveur applique `mx.random.seed(seed)` avant de générer : à prompt,
    température et graine égaux, la sortie l'est aussi.

    Observé : quand la première génération rend un JSON invalide, la seconde
    porte la MÊME empreinte SHA-256 — une génération entière jetée pour rien.
    """
    b = bouchon("json_invalide")

    with pytest.raises(llm.LLMError):
        _moteur(b).chat_json("système", "utilisateur", retries=2, seed=42)

    assert len(b.requetes) == 1, (
        f"{len(b.requetes)} générations pour une sortie qui ne peut pas changer")


def test_SANS_graine_la_reprise_interne_garde_son_sens(bouchon) -> None:
    """Le hasard peut alors produire autre chose, et une virgule manquante ne
    doit pas coûter le cycle métier complet."""
    b = bouchon("json_invalide")

    with pytest.raises(llm.LLMError):
        _moteur(b).chat_json("système", "utilisateur", retries=2)

    assert len(b.requetes) == 3, f"{len(b.requetes)} essais au lieu de 3"


def test_un_gabarit_configure_part_avec_chaque_requete(bouchon) -> None:
    """Granite 4.2 réfléchit par défaut : le moteur de l'ECL coupe sa réflexion par
    `chat_template_kwargs`, sonde comprise ; sans gabarit, rien n'est ajouté."""
    b = bouchon("sain")

    _moteur(b, gabarit={"enable_thinking": False})._completion("système", "utilisateur", max_tokens=8, delai=5.0)
    assert b.requetes[-1]["chat_template_kwargs"] == {"enable_thinking": False}, b.requetes[-1]
    _moteur(b)._completion("système", "utilisateur", max_tokens=8, delai=5.0)
    assert "chat_template_kwargs" not in b.requetes[-1], b.requetes[-1]
