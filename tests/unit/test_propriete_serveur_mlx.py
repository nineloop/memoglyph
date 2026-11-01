"""Memoglyph n'arrête un serveur que s'il PROUVE l'avoir lancé.

## Le défaut, mesuré trois fois en douze heures

Un traitement local meurt en cours ; le `mlx_lm.server` qu'il a lancé survit
avec `PPID 1` et garde le port épinglé. Le traitement suivant le trouve, le
croit sien, échoue — et le message affirmait « Serveur arrêté ; le prochain
appel en démarrera un neuf » alors que rien n'était arrêté. Le mode local est
resté mort **11 heures** sur une machine de développement sans que rien ne
le dise.

Aucun `finally` ne couvre ce cas : un `SIGKILL` ne l'exécute pas. Seule une
preuve écrite AVANT le drame survit à la mort du parent.

## La règle (`core/llm_mlx.py`, `_orphelin_nous_appartient`)

> « Ownership uniquement. »

Servir le même modèle sur le même port ne suffit PAS : un utilisateur peut
lancer cette combinaison exacte à la main, et la tuer sous ses pieds serait pire
que la panne qu'on répare.

## Pourquoi le PID seul ne prouve rien

Il se recycle. Sur une machine allumée depuis des jours, le PID d'hier
appartient à quelqu'un d'autre aujourd'hui — et ce quelqu'un serait tué par un
contrôle qui ne regarde que le numéro. On exige donc PID **et heure de
démarrage**, et le test du PID RECYCLÉ ci-dessous est le plus important du
fichier : c'est le seul qui distingue une vraie preuve d'une coïncidence.
"""

from __future__ import annotations

import json

import pytest

from core import llm_mlx

pytestmark = pytest.mark.unit


@pytest.fixture
def moteur(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_mlx.config, "CACHE_DIR", tmp_path)
    return llm_mlx.MlxEngine(port=8799, model="mlx-community/test")


def _ecrire_temoin(moteur, **champs) -> None:
    base = {"pid": 4242, "demarrage": "Mon Jul 29 10:00:00 2026",
            "port": moteur.port, "model": moteur.model}
    base.update(champs)
    moteur._temoin_propriete.write_text(json.dumps(base), encoding="utf-8")


def test_sans_temoin_la_propriete_est_INCONNUE(moteur):
    assert moteur._orphelin_nous_appartient() is None


def test_un_temoin_ILLISIBLE_ne_prouve_rien(moteur):
    moteur._temoin_propriete.write_text("ceci n'est pas du JSON", encoding="utf-8")
    assert moteur._orphelin_nous_appartient() is None


def test_un_PID_MORT_ne_prouve_rien(moteur, monkeypatch):
    _ecrire_temoin(moteur)
    monkeypatch.setattr(type(moteur), "_demarrage_du_processus",
                        staticmethod(lambda pid: None))
    assert moteur._orphelin_nous_appartient() is None


def test_un_PID_RECYCLE_ne_prouve_rien(moteur, monkeypatch):
    """LE test qui compte.

    Même numéro, autre processus : le système rend une heure de démarrage
    différente. Un contrôle qui ne regarderait que le PID tuerait ici le travail
    d'un tiers — précisément ce que « ownership uniquement » écarte.
    """
    _ecrire_temoin(moteur, demarrage="Mon Jul 29 10:00:00 2026")
    monkeypatch.setattr(
        type(moteur), "_demarrage_du_processus",
        staticmethod(lambda pid: "Mon Jul 29 14:30:00 2026"),  # AUTRE processus
    )
    assert moteur._orphelin_nous_appartient() is None


def test_un_temoin_d_un_AUTRE_PORT_ne_prouve_rien(moteur, monkeypatch):
    _ecrire_temoin(moteur, port=9999)
    monkeypatch.setattr(type(moteur), "_demarrage_du_processus",
                        staticmethod(lambda pid: "Mon Jul 29 10:00:00 2026"))
    assert moteur._orphelin_nous_appartient() is None


def test_PID_VIVANT_et_MEME_demarrage_prouvent_la_propriete(moteur, monkeypatch):
    _ecrire_temoin(moteur, pid=4242, demarrage="Mon Jul 29 10:00:00 2026")
    monkeypatch.setattr(type(moteur), "_demarrage_du_processus",
                        staticmethod(lambda pid: "Mon Jul 29 10:00:00 2026"))
    assert moteur._orphelin_nous_appartient() == 4242


def test_un_serveur_NON_PROUVE_est_refuse_sans_etre_tue(moteur, monkeypatch):
    """La branche « propriété inconnue » : on refuse, on ne tue pas.

    Et le refus doit être ACTIONNABLE — port nommé, sortie proposée. Un refus
    qui n'indique rien laisse l'utilisateur relancer indéfiniment.
    """
    tue = {"appele": False}
    monkeypatch.setattr(type(moteur), "_port_ouvert", lambda self: True)
    monkeypatch.setattr(type(moteur), "_demarrage_du_processus",
                        staticmethod(lambda pid: None))
    monkeypatch.setattr(
        type(moteur), "_demarrer",
        lambda self: tue.__setitem__("appele", True))

    with pytest.raises(llm_mlx.MlxLLMError) as capture:
        moteur._assurer_serveur()

    message = str(capture.value)
    assert str(moteur.port) in message, message
    assert "n'a pas lancé" in message, message
    assert not tue["appele"], "on a démarré par-dessus un serveur d'autrui"


def test_le_refus_ne_PRETEND_PAS_avoir_arrete_quoi_que_ce_soit(moteur, monkeypatch):
    """Le défaut d'origine, interdit nommément.

    L'ancien message disait « Serveur arrêté ; le prochain appel en démarrera un
    neuf ». Les deux moitiés étaient fausses. Promettre une réparation qui n'a
    pas lieu est pire que d'avouer la panne : l'utilisateur relance au lieu de
    chercher.
    """
    monkeypatch.setattr(type(moteur), "_port_ouvert", lambda self: True)
    monkeypatch.setattr(type(moteur), "_demarrage_du_processus",
                        staticmethod(lambda pid: None))

    with pytest.raises(llm_mlx.MlxLLMError) as capture:
        moteur._assurer_serveur()

    message = str(capture.value).lower()
    assert "serveur arrêté" not in message, message
    assert "démarrera un neuf" not in message, message


def test_un_orphelin_PROUVE_est_recupere_puis_remplace(moteur, monkeypatch):
    """La réparation automatique, dans le seul cas où elle est légitime."""
    _ecrire_temoin(moteur, pid=4242, demarrage="H")
    tues: list[int] = []
    demarre = {"appele": False}
    ouvert = {"valeur": True}

    monkeypatch.setattr(type(moteur), "_port_ouvert", lambda self: ouvert["valeur"])
    monkeypatch.setattr(type(moteur), "_demarrage_du_processus",
                        staticmethod(lambda pid: "H"))
    monkeypatch.setattr(llm_mlx.os, "kill", lambda pid, sig: tues.append(pid))
    monkeypatch.setattr(
        type(moteur), "_demarrer",
        lambda self: demarre.__setitem__("appele", True))

    moteur._assurer_serveur()

    assert tues and tues[0] == 4242, tues
    assert demarre["appele"], "aucun serveur neuf après la récupération"
    assert not moteur._temoin_propriete.exists(), "le témoin périmé a survécu"


def test_notre_PROPRE_serveur_en_marche_n_est_ni_tue_ni_relance(moteur, monkeypatch):
    """Le cas nominal ne doit rien coûter.

    Une récupération déclenchée sur un serveur sain redémarrerait 5,6 Go à
    chaque appel — la réparation deviendrait la panne.
    """
    tues: list[int] = []
    demarre = {"appele": False}

    class _Vivant:
        pid = 4242

        def poll(self):
            return None

    moteur._processus = _Vivant()
    monkeypatch.setattr(type(moteur), "_port_ouvert", lambda self: True)
    monkeypatch.setattr(llm_mlx.os, "kill", lambda pid, sig: tues.append(pid))
    monkeypatch.setattr(
        type(moteur), "_demarrer",
        lambda self: demarre.__setitem__("appele", True))

    moteur._assurer_serveur()

    assert tues == []
    assert not demarre["appele"]


def test_le_temoin_est_ECRIT_avec_le_demarrage_du_processus(moteur, monkeypatch):
    monkeypatch.setattr(type(moteur), "_demarrage_du_processus",
                        staticmethod(lambda pid: "Mon Jul 29 11:00:00 2026"))

    moteur._inscrire_propriete(1234)

    ecrit = json.loads(moteur._temoin_propriete.read_text(encoding="utf-8"))
    assert ecrit["pid"] == 1234
    assert ecrit["demarrage"] == "Mon Jul 29 11:00:00 2026"
    assert ecrit["port"] == moteur.port


def test_un_temoin_NON_ECRIVABLE_ne_casse_pas_le_demarrage(moteur, monkeypatch):
    """La dégradation choisie : sans témoin, on refusera au lieu de tuer.

    C'est le bon sens de l'échec — jamais l'inverse.
    """
    def refuse(*a, **k):
        raise PermissionError("disque en lecture seule")

    monkeypatch.setattr(llm_mlx.Path, "write_text", refuse)
    moteur._inscrire_propriete(1234)  # ne doit pas lever
    assert moteur._orphelin_nous_appartient() is None


# ---------- Le cadavre non prouvé nôtre : tué UNIQUEMENT s'il est figé ----------
#
# La brèche laissée par « ownership uniquement », mesurée : un serveur
# qu'on n'a pas lancé, dont le témoin de propriété a disparu (cache effacé,
# session tuée sans `finally`), tient le port sans plus rien servir. On a dû en
# tuer trois à la main. La règle est étendue au strict minimum : on ne retire
# qu'un CADAVRE — un serveur dont `/v1/models` lui-même est muet, état qu'aucun
# serveur vivant ne produit. Un serveur d'autrui qui répond reste intouchable.


def _ouverture(sequence):
    """Un `_port_ouvert` qui rend `sequence` puis `False` : coupe l'attente de 5 s."""
    file = list(sequence)
    return lambda self: file.pop(0) if file else False


def test_serveur_non_notre_mais_FIGE_est_recupere_puis_remplace(moteur, monkeypatch):
    """Le seul cas où l'on tue sans preuve de propriété : le cadavre qui bloque le port.

    Témoin absent (propriété INCONNUE), mais `/v1/models` muet prouve un jam du
    processus entier. On retire le cadavre et on repart à neuf.
    """
    tues: list[int] = []
    demarre = {"appele": False}

    monkeypatch.setattr(type(moteur), "_port_ouvert", _ouverture([True]))
    monkeypatch.setattr(type(moteur), "_sonde_http_vivante", lambda self: False)  # figé
    monkeypatch.setattr(type(moteur), "_pids_du_port", lambda self: [4242])
    monkeypatch.setattr(type(moteur), "_demarrage_du_processus",
                        staticmethod(lambda pid: None))  # mort dès le SIGTERM
    monkeypatch.setattr(llm_mlx.os, "kill", lambda pid, sig: tues.append(pid))
    monkeypatch.setattr(type(moteur), "_demarrer",
                        lambda self: demarre.__setitem__("appele", True))

    moteur._assurer_serveur()

    assert tues and tues[0] == 4242, tues
    assert demarre["appele"], "aucun serveur neuf après le retrait du cadavre"


def test_serveur_non_notre_VIVANT_est_refuse_sans_etre_tue(moteur, monkeypatch):
    """LA sûreté du lot : un serveur non-nôtre qui RÉPOND n'est jamais tué.

    Sain, saturé, ou zombie « thread mort » resté vert : `/v1/models` répond, la
    sonde rend VRAI, et le moteur retombe sur le refus « ownership uniquement ».
    C'est exactement le serveur qu'un utilisateur peut avoir lancé à la main.
    """
    tues: list[int] = []
    demarre = {"appele": False}

    monkeypatch.setattr(type(moteur), "_port_ouvert", lambda self: True)
    monkeypatch.setattr(type(moteur), "_sonde_http_vivante", lambda self: True)  # vivant
    monkeypatch.setattr(type(moteur), "_pids_du_port", lambda self: [4242])
    monkeypatch.setattr(llm_mlx.os, "kill", lambda pid, sig: tues.append(pid))
    monkeypatch.setattr(type(moteur), "_demarrer",
                        lambda self: demarre.__setitem__("appele", True))

    with pytest.raises(llm_mlx.MlxLLMError) as capture:
        moteur._assurer_serveur()

    assert "n'a pas lancé" in str(capture.value), capture.value
    assert tues == [], "un serveur vivant non-nôtre a été tué"
    assert not demarre["appele"], "on a démarré par-dessus un serveur d'autrui"


def test_un_serveur_FIGE_mais_INNOMMABLE_est_refuse(moteur, monkeypatch):
    """Figé mais introuvable par `lsof` : on ne tue pas ce qu'on ne peut pas nommer.

    Le refus prend alors le relais, comme pour tout port occupé sans issue.
    """
    demarre = {"appele": False}

    monkeypatch.setattr(type(moteur), "_port_ouvert", lambda self: True)
    monkeypatch.setattr(type(moteur), "_sonde_http_vivante", lambda self: False)  # figé
    monkeypatch.setattr(type(moteur), "_pids_du_port", lambda self: [])  # innommable
    monkeypatch.setattr(type(moteur), "_demarrer",
                        lambda self: demarre.__setitem__("appele", True))

    with pytest.raises(llm_mlx.MlxLLMError):
        moteur._assurer_serveur()

    assert not demarre["appele"]


def test_un_orphelin_PROUVE_court_circuite_la_sonde_de_jam(moteur, monkeypatch):
    """Un orphelin NÔTRE est tué qu'il soit figé ou non : la propriété prime.

    On vérifie AUSSI que `/v1/models` n'est pas sondé — la preuve de propriété se
    suffit, et l'ordre du `or` court-circuité épargne l'aller-retour inutile.
    """
    _ecrire_temoin(moteur, pid=4242, demarrage="H")
    tues: list[int] = []
    demarre = {"appele": False}
    sondes = {"n": 0}
    etat = {"vivant": True}

    def demarrage(pid):
        # "H" pour la preuve de propriété, puis None pour la boucle d'arrêt.
        if etat["vivant"]:
            etat["vivant"] = False
            return "H"
        return None

    monkeypatch.setattr(type(moteur), "_port_ouvert", _ouverture([True]))
    monkeypatch.setattr(type(moteur), "_demarrage_du_processus", staticmethod(demarrage))
    monkeypatch.setattr(type(moteur), "_sonde_http_vivante",
                        lambda self: sondes.__setitem__("n", sondes["n"] + 1) or True)
    monkeypatch.setattr(llm_mlx.os, "kill", lambda pid, sig: tues.append(pid))
    monkeypatch.setattr(type(moteur), "_demarrer",
                        lambda self: demarre.__setitem__("appele", True))

    moteur._assurer_serveur()

    assert tues == [4242], tues
    assert demarre["appele"]
    assert sondes["n"] == 0, "la sonde de jam a tourné alors que la propriété suffisait"
    assert not moteur._temoin_propriete.exists(), "le témoin périmé a survécu"


def test_pids_du_port_analyse_la_sortie_de_lsof(moteur, monkeypatch):
    class _Sortie:
        stdout = "4242\n5310\n"

    monkeypatch.setattr(llm_mlx.subprocess, "run", lambda *a, **k: _Sortie())
    assert moteur._pids_du_port() == [4242, 5310]


def test_pids_du_port_rend_une_liste_vide_si_lsof_echoue(moteur, monkeypatch):
    def boom(*a, **k):
        raise OSError("lsof absent")

    monkeypatch.setattr(llm_mlx.subprocess, "run", boom)
    assert moteur._pids_du_port() == []
