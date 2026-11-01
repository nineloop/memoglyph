"""Le moteur MLX contre un VRAI `mlx_lm.server` et un vrai modèle.

Marqué `ml` : exclu de `make check` (la CI n'a ni Metal ni poids). À lancer avec
`make test-ml`, qui déclare les prérequis de ce module dans `preflight()` —
paquet `mlx_lm` et présence du modèle dans le cache Hugging Face.

Aucun import de niveau module au-delà de `pytest` : un import raté au niveau
module INTERROMPT la collecte pytest entière, et les marqueurs ne protègent pas
— `-m "not ml"` désélectionne APRÈS la collecte. `importorskip` vit donc DANS
les fonctions.

Ces tests ne disent rien du texte produit : ils vérifient qu'une génération a
RÉELLEMENT eu lieu, ce que `/health` ne sait pas dire.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.ml, pytest.mark.slow]


def _port_libre() -> int:
    """Un port éphémère : le noyau en choisit un libre, on le rend aussitôt.

    Même procédé que `tests/unit/test_llm_mlx.py`. La fenêtre entre la fermeture
    de cette prise et le `bind` de l'enfant est une course connue et étroite ;
    son échec est franc — l'enfant meurt au démarrage et `_demarrer` le dit —
    là où le port de production échouait, lui, en cassant le produit.
    """
    import socket

    with socket.socket() as prise:
        prise.bind(("127.0.0.1", 0))
        return prise.getsockname()[1]


@pytest.fixture(scope="module")
def moteur():
    """Un serveur démarré une seule fois : c'est l'étape coûteuse (~5,6 Go).

    Sur un port ÉPHÉMÈRE, comme l'exige le témoin de `tests/conftest.py`.

    Ce module lançait son serveur sur le PORT DE PRODUCTION, présenté comme
    délibéré : cela « vérifiait que le port épinglé est bien celui de la
    configuration ». L'argument ne tenait pas. Le lien entre le moteur et la
    configuration s'observe sans démarrer quoi que ce soit, et le test unitaire
    le fige déjà en SUBSTITUANT la configuration — donc plus fortement que ne le
    faisait ici une comparaison à `config.MLX_PORT`. On payait un risque sur le
    mode local de l'utilisateur pour une propriété qu'on n'obtenait pas.

    Le prix était mesuré — trois orphelins en douze heures, dont un mode local
    resté mort onze heures — et il se lisait dans le résultat même de ce module :
    six tests verts, six erreurs de démontage, le premier laissant le port pris
    aux cinq suivants. Un fichier qui se déclarait vert en cassant le produit,
    exactement ce que le témoin existe pour attraper.

    Le `finally` est la garantie d'arrêt, et le témoin qui le suit VÉRIFIE
    qu'elle a tenu : `unload()` rend `True` sur la foi d'un `terminate()`, sans
    jamais regarder si le port s'est libéré. Aucun des deux ne survit à un
    `SIGKILL` — limite connue, que le port éphémère rend enfin inoffensive :
    l'orphelin qu'on laisserait alors n'occupe plus rien dont le produit ait
    besoin.
    """
    pytest.importorskip("mlx_lm", reason="paquet mlx-lm absent")
    from core import llm_mlx

    instance = llm_mlx.MlxEngine(port=_port_libre())
    try:
        yield instance
    finally:
        # Rend la mémoire même si un test a échoué : sur une machine à RAM
        # unifiée, un serveur oublié empêche la transcription suivante.
        instance.unload()
        import socket

        try:
            with socket.create_connection(("127.0.0.1", instance.port), timeout=0.5):
                encore = True
        except OSError:
            encore = False
        assert not encore, (
            f"un serveur survit sur le port {instance.port} après la fixture : "
            "la garantie d'arrêt n'a pas tenu"
        )


def test_le_moteur_est_disponible_apres_un_vrai_chargement(moteur) -> None:
    """`is_available()` démarre le serveur et prouve la vivacité par un jeton.

    Si ceci échoue alors que `curl /health` répond 200, c'est exactement le cas
    zombie que ce moteur existe pour refuser : le thread de chargement est mort
    et le serveur ne générera jamais rien.
    """
    assert moteur.is_available() is True


def test_une_generation_reelle_rend_du_texte(moteur) -> None:
    reponse = moteur.chat(
        "Tu réponds en un seul mot, sans ponctuation.",
        "Réponds exactement : OK",
    )

    assert reponse.strip(), "le serveur a répondu sans générer un seul jeton"


def test_le_serveur_ecoute_bien_le_port_DEMANDE(moteur) -> None:
    """L'enfant se lie au port qu'on lui donne, pas au 8080 par défaut.

    Vérifié sur le processus RÉEL : une collision sur 8080 donnerait un moteur
    silencieusement faux, et c'est la panne que l'épinglage évite. La boucle est
    fermée ici — l'`argv` annonce un port, et c'est là qu'un listener répond.

    Ce que ce test ne dit PAS, depuis que le serveur prend un port éphémère :
    que ce port soit celui de la CONFIGURATION. C'est une propriété du moteur,
    qui s'observe sans démarrer quoi que ce soit, et
    `tests/unit/test_llm_mlx.py::test_le_port_par_defaut_vient_de_la_CONFIGURATION`
    la fige en substituant la configuration — donc plus fortement que ne le
    ferait ici une égalité avec `config.MLX_PORT`.
    """
    import socket

    argv = moteur.commande_serveur()

    assert moteur.port != 8080, "sur le défaut de mlx_lm.server, ce test ne prouve rien"
    assert argv[argv.index("--port") + 1] == str(moteur.port), argv
    with socket.create_connection((moteur.host, moteur.port), timeout=2):
        pass


def test_le_modele_configure_ne_REFLECHIT_pas(moteur) -> None:
    """Le fait sur lequel repose tout le choix de modèle — vérifié, pas supposé.

    Un modèle à raisonnement met sa sortie dans un champ hors norme OpenAI et
    laisse `content` nul. La sonde de vivacité y lit « aucune génération », donc
    « serveur zombie », et TUE un serveur parfaitement sain : c'est la panne
    mesurée sur Qwen3.5-9B-4bit, et la raison du changement de modèle.

    Ce test interroge le serveur DIRECTEMENT plutôt que par le moteur : le
    moteur, lui, refuse déjà ce cas. Ce qu'on veut savoir ici est ce que le
    modèle configuré produit RÉELLEMENT, pas ce que le moteur en fait.
    """
    import httpx

    from core import config

    moteur.is_available()  # garantit un serveur démarré
    reponse = httpx.post(
        f"http://{moteur.host}:{moteur.port}/v1/chat/completions",
        json={
            "model": config.MLX_LLM_MODEL,
            "messages": [{"role": "user", "content": "Dis bonjour."}],
            "max_tokens": 32, "temperature": 0.0,
        },
        timeout=config.MLX_SONDE_TIMEOUT,
    )
    message = reponse.json()["choices"][0]["message"]

    for champ in ("reasoning", "reasoning_content", "thinking"):
        assert not message.get(champ), (
            f"le modèle {config.MLX_LLM_MODEL} réfléchit : champ {champ!r} présent. "
            f"La sonde de vivacité conclura à tort à un serveur zombie."
        )
    assert message.get("content"), "aucun contenu textuel : le contrat est rompu"


def test_chat_json_rend_une_TABLE_contre_un_vrai_modele(moteur) -> None:
    """Le chemin qu'emprunte `core/report.py`, de bout en bout.

    Le serveur mlx ignore `response_format` (mesuré) : le JSON n'est donc pas
    garanti par le décodage mais DEMANDÉ puis VÉRIFIÉ. Ce test est la seule
    preuve que cet arrangement tient face à un vrai modèle — un bouchon qui rend
    du JSON parfait ne prouverait que sa propre complaisance.

    Rien n'est affirmé sur le CONTENU : le schéma n'étant pas contraignant ici,
    exiger des clés précises testerait l'obéissance d'un 4B, pas le moteur.
    """
    donnees = moteur.chat_json(
        "Tu extrais les décisions d'une réunion.",
        "Réunion budget. Alice propose de reporter la ligne 4. Bob accepte.",
        schema={
            "type": "object",
            "properties": {"decisions": {"type": "array", "items": {"type": "string"}}},
            "required": ["decisions"],
        },
        max_tokens=512,
    )

    assert isinstance(donnees, dict), type(donnees)


def test_le_dechargement_rend_la_memoire(moteur) -> None:
    """`unload()` tue l'enfant, donc rend les ~5,6 Go.

    Placé en dernier du module : il détruit le serveur que les autres partagent.
    La fixture le rappellera, et un second appel doit rendre `False` sans lever.
    """
    assert moteur.unload() is True
    assert moteur.unload() is False
