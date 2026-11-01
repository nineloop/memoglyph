"""ECL, phase 1 : extraction ancrée par réunion et stockage borné.

Corpus synthétique, écrit ici et non tiré d'une vraie réunion :

- « Tarifs » a, b, c : le prix de vente décidé, modifié, puis la décision annulée ;
- « Planning » : une contradiction entre deux réunions sur la date de livraison ;
- « Atelier » en deux versions, même `meeting_id` ;
- « Rachat » : une réunion ultra-confidentielle ;
- « Interne » : un autre projet du même propriétaire, pour le retrait de partage.

Le modèle est simulé : il propose les faits que chaque réunion contient, plus,
quand on le lui demande, des faits inventés que le contrôle doit tous refuser.
"""
from __future__ import annotations

import json
import os
import re
import socket
import threading
import time

import pytest

from api import auth, cli, partages
from api import portee as portee_api
from core import docid, ecl, library, projects, purge

pytestmark = [pytest.mark.integration]


@pytest.fixture(autouse=True)
def _faits_actives(monkeypatch):
    """Ces tests supposent les faits de la réunion activés (`MEMOGLYPH_FAITS=1`)."""
    from core import config as _config

    monkeypatch.setattr(_config, "FAITS", True)

MOT_DE_PASSE = "un mot de passe solide"


def _transcript(identite, reunion, projet, titre, date, repliques, sensibilite="standard"):
    lignes = "\n".join(f"**{qui}** [{ts}] {texte}" for qui, ts, texte in repliques)
    return (f"---\nid: {identite}\nmeeting_id: {reunion}\nproject_id: {projet}\n"
            f"title: {titre}\ndate: '{date}'\nlanguage: fr\nsensitivity: {sensibilite}\n"
            f"diarization: true\n---\n\n## Transcript\n\n{lignes}\n")


# Les faits que le faux modèle propose, par phrase repère de la réunion.
PROPOSES = {
    "fixe le prix de vente à 25 euros": [
        {"type": "decision", "horodatage": "00:00:12",
         "citation": "on fixe le prix de vente à 25 euros",
         "enonce": "Le prix de vente est fixé à 25 euros."},
        {"type": "action", "horodatage": "00:00:40",
         "citation": "Bruno prépare la grille tarifaire pour jeudi",
         "enonce": "Bruno prépare la grille tarifaire.", "porteur": "Bruno"},
    ],
    "passe le prix de vente à 30 euros": [
        {"type": "decision", "horodatage": "00:01:05",
         "citation": "on passe le prix de vente à 30 euros",
         "enonce": "Le prix de vente passe à 30 euros."},
    ],
    "on annule la hausse du prix": [
        {"type": "decision", "horodatage": "00:00:20",
         "citation": "on annule la hausse du prix finalement",
         "enonce": "La hausse du prix est annulée."},
    ],
    "livraison est prévue le 12 mars": [
        {"type": "chiffre", "horodatage": "00:00:08",
         "citation": "la livraison est prévue le 12 mars",
         "enonce": "Livraison prévue le 12 mars."},
    ],
    "livraison ne sera pas avant le 30 avril": [
        {"type": "chiffre", "horodatage": "00:00:09",
         "citation": "la livraison ne sera pas avant le 30 avril",
         "enonce": "Livraison pas avant le 30 avril."},
    ],
    "le moule reste un problème ouvert": [
        {"type": "probleme", "horodatage": "00:00:15",
         "citation": "le moule reste un problème ouvert",
         "enonce": "Le moule reste un problème ouvert."},
    ],
    "on rachète la société Tapir": [
        {"type": "decision", "horodatage": "00:00:05",
         "citation": "on rachète la société Tapir",
         "enonce": "Rachat de la société Tapir."},
    ],
    "on garde le kiwi en interne": [
        {"type": "decision", "horodatage": "00:00:03",
         "citation": "on garde le kiwi en interne",
         "enonce": "Le kiwi reste en interne."},
    ],
}

# Ce qu'un modèle invente : chacun doit être refusé, pour le motif indiqué.
INVENTES = [
    ({"type": "decision", "horodatage": "00:00:12",
      "citation": "on fixe le prix de vente à 2500 euros",
      "enonce": "Prix fixé à 2500 euros."}, "citation_absente"),
    ({"type": "chiffre", "horodatage": "00:00:12",
      "citation": "on fixe le prix de vente à 25 euros",
      "enonce": "Le coût de production visé est de 12,50 euros."}, "contenance"),
    ({"type": "decision", "horodatage": "00:00:12",
      "citation": "on fixe le prix de vente à 25 euros",
      "enonce": "Le prix est validé par Carole."}, "contenance"),
    ({"type": "decision", "horodatage": "00:05:00",
      "citation": "on fixe le prix de vente à 25 euros",
      "enonce": "Le prix de vente est fixé à 25 euros."}, "citation_absente"),
    ({"type": "decision", "horodatage": "00:00:12", "citation": "25 euros",
      "enonce": "25 euros."}, "citation_trop_courte"),
    ({"type": "rumeur", "horodatage": "00:00:12",
      "citation": "on fixe le prix de vente à 25 euros", "enonce": "Une rumeur."}, "type_inconnu"),
    ({"type": "decision", "horodatage": "plus tard",
      "citation": "on fixe le prix de vente à 25 euros", "enonce": "Prix."}, "horodatage_illisible"),
    ({"type": "decision", "citation": "", "enonce": ""}, "incomplet"),
]


class FauxModele:
    def __init__(self, inventer: bool = False, panne: bool = False):
        self.inventer, self.panne, self.appels = inventer, panne, []

    def __call__(self, system, user, **options):
        self.appels.append(options)
        if self.panne:
            raise RuntimeError("le modèle ne répond pas")
        faits = [f for repere, liste in PROPOSES.items() if repere in user for f in liste]
        if self.inventer and "fixe le prix de vente à 25 euros" in user:
            faits += [dict(f) for f, _motif in INVENTES]
        return {"faits": faits}


def _port_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def corpus(isolated_stores, tmp_path, monkeypatch):
    sortie = isolated_stores["output_dir"]
    from core import semantic
    from core.config import EMBED_DIM
    vecteur = [1.0] + [0.0] * (EMBED_DIM - 1)
    monkeypatch.setattr(semantic.embed, "embed_documents", lambda textes, **_k: [vecteur] * len(textes))
    monkeypatch.setattr(semantic.embed, "embed_query", lambda t, **_k: vecteur)
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(auth, "CODE_PATH", tmp_path / ".memoglyph-installation")
    for compte in ("alice", "bruno"):
        auth.creer_compte(compte, MOT_DE_PASSE)
    # Des ports libres pour cette racine : `ecl-extraire` relève si l'application
    # tourne, et la vraie pile de la machine, si elle tourne, n'y est pour rien.
    (sortie.parent / ".memoglyph-ports").write_text(
        f"UI_PORT={_port_libre()}\nAPI_PORT={_port_libre()}\n", encoding="utf-8")
    projects.ensure_inbox(sortie)
    d_clients, clients = projects.create_owned(sortie, name="Clients", owner="alice")
    d_interne, interne = projects.create_owned(sortie, name="Interne", owner="alice")
    noms: dict[str, str] = {}
    reunions: dict[str, str] = {}

    def ecrire(cle, dossier, projet, date, repliques, sensibilite="standard", reunion=None):
        identite = docid.nouveau()
        reunions[cle] = reunion or docid.nouveau()
        (dossier / f"{cle}.md").write_text(
            _transcript(identite, reunions[cle], projet.id, cle, date, repliques, sensibilite),
            encoding="utf-8")
        noms[cle] = f"{cle}.md"

    ecrire("tarifs-a", d_clients, clients, "2026-03-01", [
        ("Alice", "00:00:02", "bonjour à tous, on commence par les tarifs"),
        ("Alice", "00:00:10", "après discussion on fixe le prix de vente à 25 euros"),
        ("Bruno", "00:00:38", "d'accord, Bruno prépare la grille tarifaire pour jeudi"),
    ])
    ecrire("tarifs-b", d_clients, clients, "2026-03-08", [
        ("Alice", "00:01:00", "les coûts ont monté, on passe le prix de vente à 30 euros"),
    ])
    ecrire("tarifs-c", d_clients, clients, "2026-03-15", [
        ("Bruno", "00:00:18", "les clients ont protesté"),
        ("Alice", "00:00:19", "alors on annule la hausse du prix finalement"),
    ])
    ecrire("planning-a", d_clients, clients, "2026-03-01", [
        ("Alice", "00:00:08", "la livraison est prévue le 12 mars"),
    ])
    ecrire("planning-b", d_clients, clients, "2026-03-08", [
        ("Bruno", "00:00:09", "la livraison ne sera pas avant le 30 avril"),
    ])
    ecrire("atelier-v1", d_clients, clients, "2026-03-02", [
        ("Alice", "00:00:15", "le moule reste un problème ouvert"),
    ])
    ecrire("atelier-v2", d_clients, clients, "2026-03-02", [
        ("Alice", "00:00:15", "le moule reste un problème ouvert"),
    ], reunion=reunions["atelier-v1"])
    ecrire("rachat", d_clients, clients, "2026-03-03", [
        ("Alice", "00:00:05", "c'est décidé, on rachète la société Tapir"),
    ], sensibilite="ultra_confidential")
    ecrire("interne", d_interne, interne, "2026-03-04", [
        ("Alice", "00:00:03", "on garde le kiwi en interne"),
    ])
    library.reindex()
    return {"sortie": sortie, "clients": clients, "interne": interne,
            "noms": noms, "reunions": reunions}


def _compte(identifiant: str) -> auth.Utilisateur:
    return auth.Utilisateur(id=identifiant, identifiant=identifiant, admin=False)


def _tout_extraire(corpus) -> None:
    for nom in corpus["noms"].values():
        ecl.extraire(nom)


def _enonces(faits) -> list[str]:
    return sorted(f["enonce"] for f in faits)


@pytest.fixture
def modele(monkeypatch):
    faux = FauxModele()
    monkeypatch.setattr(ecl, "_modele", faux)
    return faux


# ---------- Règle 1 : provenance jusqu'à la source ----------

def test_chaque_fait_garde_sa_source_et_sa_reformulation_marquee(corpus, modele) -> None:
    bilan = ecl.extraire(corpus["noms"]["tarifs-a"])
    assert (bilan["gardes"], bilan["refuses"]) == (2, 0)

    contenu = json.loads((corpus["sortie"] / ".ecl" / f"{bilan['document_id']}.json")
                         .read_text(encoding="utf-8"))
    corps = (corpus["sortie"] / "Clients" / "tarifs-a.md").read_text(encoding="utf-8")
    assert contenu["meeting_id"] == corpus["reunions"]["tarifs-a"]
    for fait in contenu["faits"]:
        assert fait["document_id"] == bilan["document_id"]
        assert fait["meeting_id"] == corpus["reunions"]["tarifs-a"]
        assert fait["enonce_statut"] == "reformulé"
        assert fait["citation"] != fait["enonce"]
        ligne = next(ln for ln in corps.splitlines() if f"[{fait['horodatage']}]" in ln)
        assert fait["citation"] in ligne                          # mot pour mot
        assert ligne.startswith(f"**{fait['locuteur']}**")
    decision, action = sorted(contenu["faits"], key=lambda f: f["type"], reverse=True)
    # L'horodatage annoncé (00:00:12) cède à celui de la réplique (00:00:10).
    assert (decision["type"], decision["horodatage"]) == ("decision", "00:00:10")
    assert (action["type"], action["porteur"], action["locuteur"]) == ("action", "Bruno", "Bruno")


def test_aucun_fait_invente_n_est_garde(corpus, monkeypatch) -> None:
    monkeypatch.setattr(ecl, "_modele", FauxModele(inventer=True))
    bilan = ecl.extraire(corpus["noms"]["tarifs-a"])

    attendus: dict[str, int] = {}
    for _fait, motif in INVENTES:
        attendus[motif] = attendus.get(motif, 0) + 1
    assert bilan["gardes"] == 2
    assert bilan["refuses"] == len(INVENTES)
    assert {m: n for m, n in bilan["refus"].items() if n} == attendus
    gardes = ecl.faits(None)
    assert _enonces(gardes) == ["Bruno prépare la grille tarifaire.",
                                "Le prix de vente est fixé à 25 euros."]
    fichier = ecl.lire_fichier(bilan["document_id"])           # refusé, jamais gardé
    assert _enonces(fichier["faits"]) == _enonces(gardes)
    assert fichier["refus"] == bilan["refus"]


def test_une_citation_a_cheval_sur_deux_repliques_prend_la_premiere(corpus, monkeypatch) -> None:
    monkeypatch.setattr(ecl, "_modele", lambda *a, **k: {"faits": [
        {"type": "decision", "horodatage": "00:00:05",
         "citation": "on commence par les tarifs après discussion on fixe",
         "enonce": "On commence par les tarifs."}]})
    ecl.extraire(corpus["noms"]["tarifs-a"])
    assert [(f["horodatage"], f["locuteur"]) for f in ecl.faits(None)] == [("00:00:02", "Alice")]


def test_un_porteur_non_nomme_dans_la_citation_n_est_pas_devine(corpus, monkeypatch) -> None:
    def modele(system, user, **_k):
        return {"faits": [{"type": "action", "horodatage": "00:00:38",
                           "citation": "Bruno prépare la grille tarifaire pour jeudi",
                           "enonce": "Bruno prépare la grille tarifaire.", "porteur": "Carole"},
                          {"type": "decision", "horodatage": "00:00:10",
                           "citation": "on fixe le prix de vente à 25 euros",
                           "enonce": "Le prix de vente est fixé à 25 euros.", "porteur": "Alice"}]}
    monkeypatch.setattr(ecl, "_modele", modele)
    ecl.extraire(corpus["noms"]["tarifs-a"])
    assert [(f["type"], f["porteur"]) for f in ecl.faits(None)] == [
        ("decision", None), ("action", None)]


def test_l_abstention_est_permise_et_ecrite(corpus, monkeypatch) -> None:
    monkeypatch.setattr(ecl, "_modele", lambda *a, **k: {"faits": []})
    bilan = ecl.extraire(corpus["noms"]["tarifs-a"])
    assert (bilan["gardes"], bilan["refuses"]) == (0, 0)
    assert ecl.lire_fichier(bilan["document_id"])["faits"] == []
    assert corpus["noms"]["tarifs-a"] not in ecl.a_extraire()[0]


def test_extraction_en_panne_rien_n_est_ecrit_ni_efface(corpus, modele, monkeypatch) -> None:
    bilan = ecl.extraire(corpus["noms"]["tarifs-a"])
    avant = ecl.lire_fichier(bilan["document_id"])
    monkeypatch.setattr(ecl, "_modele", FauxModele(panne=True))
    with pytest.raises(ecl.ExtractionEnPanne):
        ecl.extraire(corpus["noms"]["tarifs-a"])
    assert ecl.lire_fichier(bilan["document_id"]) == avant
    with pytest.raises(ecl.ExtractionEnPanne):
        ecl.extraire(corpus["noms"]["tarifs-b"])
    assert corpus["noms"]["tarifs-b"] in ecl.a_extraire()[0]


def test_deux_versions_d_une_reunion_gardent_leur_reunion_commune(corpus, modele) -> None:
    v1 = ecl.extraire(corpus["noms"]["atelier-v1"])
    v2 = ecl.extraire(corpus["noms"]["atelier-v2"])
    faits = ecl.faits(None, meeting_id=corpus["reunions"]["atelier-v1"])
    assert len(faits) == 2
    assert {f["document_id"] for f in faits} == {v1["document_id"], v2["document_id"]}
    assert {f["meeting_id"] for f in faits} == {corpus["reunions"]["atelier-v1"]}


def test_decision_modifiee_puis_annulee_et_contradiction_coexistent(corpus, modele) -> None:
    _tout_extraire(corpus)
    decisions = [f for f in ecl.faits(None, type_="decision")
                 if f["document"].startswith("tarifs")]
    assert [(f["date"], f["enonce"]) for f in decisions] == [
        ("2026-03-01", "Le prix de vente est fixé à 25 euros."),
        ("2026-03-08", "Le prix de vente passe à 30 euros."),
        ("2026-03-15", "La hausse du prix est annulée."),
    ]
    chiffres = [f["enonce"] for f in ecl.faits(None, type_="chiffre")]
    assert chiffres == ["Livraison prévue le 12 mars.", "Livraison pas avant le 30 avril."]


# ---------- Règle 3 : le cycle de vie suit la source ----------

def test_archive_masque_restauration_rend_purge_efface(corpus, modele) -> None:
    nom = corpus["noms"]["tarifs-a"]
    bilan = ecl.extraire(nom)
    fichier = corpus["sortie"] / ".ecl" / f"{bilan['document_id']}.json"
    assert len(ecl.faits(None)) == 2

    assert purge.archive(nom) == {"archived": True, "errors": []}
    assert ecl.faits(None) == []
    assert fichier.exists()                                       # gardé pour la restauration
    assert ecl.reindexer()["faits"] == 0                          # pas de retour au réindex

    archive = next(i["name"] for i in library.trash_items())
    assert purge.restore_detailed(archive)["errors"] == []
    assert _enonces(ecl.faits(None)) == ["Bruno prépare la grille tarifaire.",
                                         "Le prix de vente est fixé à 25 euros."]

    avant = purge.traces(nom)
    assert (avant["ecl_faits"], avant["ecl_fichier"]) == (2, True)
    resultat = purge.purge_transcript(nom)
    assert resultat["clean"], resultat["errors"]
    assert (resultat["after"]["ecl_faits"], resultat["after"]["ecl_fichier"]) == (0, False)
    assert not fichier.exists() and ecl.faits(None) == []


def test_purge_depuis_la_corbeille_efface_les_faits(corpus, modele) -> None:
    nom = corpus["noms"]["tarifs-b"]
    bilan = ecl.extraire(nom)
    purge.archive(nom)
    archive = next(i["name"] for i in library.trash_items())
    resultat = purge.purge_archived(archive)
    assert not resultat["errors"], resultat
    assert ecl.traces(bilan["document_id"]) == (0, False)


def test_les_tables_se_reconstruisent_depuis_les_fichiers(corpus, modele) -> None:
    _tout_extraire(corpus)
    avant = ecl.faits(None)
    conn = library._conn()
    conn.execute("DELETE FROM ecl_faits")
    conn.commit()
    conn.close()
    assert ecl.faits(None) == []
    stats = ecl.reindexer()
    assert (stats["documents"], stats["faits"], stats["illisibles"]) == (len(corpus["noms"]), len(avant), [])
    assert ecl.faits(None) == avant


def test_la_lecture_est_bornee_par_le_compte_et_ses_partages(corpus, modele) -> None:
    _tout_extraire(corpus)
    alice, bruno = _compte("alice"), _compte("bruno")
    tout_alice = ecl.faits(portee_api.lecture(alice, "all"))
    assert "Rachat de la société Tapir." in _enonces(tout_alice)  # le propriétaire la voit
    assert "Le kiwi reste en interne." in _enonces(tout_alice)
    assert ecl.faits(portee_api.lecture(bruno, "all")) == []
    assert ecl.faits(portee_api.lecture(bruno, "shared")) == []

    partage = partages.partager("alice", "bruno", "projet", corpus["clients"].id)
    vus = _enonces(ecl.faits(portee_api.lecture(bruno, "shared")))
    assert "Le prix de vente est fixé à 25 euros." in vus
    assert "Rachat de la société Tapir." not in vus               # ultra : jamais en partage
    assert "Le kiwi reste en interne." not in vus                 # autre projet, non partagé
    assert ecl.faits(portee_api.lecture(bruno, "all")) == []      # « all » = ses projets

    assert partages.retirer("alice", partage["id"])
    assert ecl.faits(portee_api.lecture(bruno, "shared")) == []   # dès la lecture suivante


def test_une_reunion_partagee_seule_n_ouvre_que_ses_faits(corpus, modele) -> None:
    _tout_extraire(corpus)
    bruno = _compte("bruno")
    partages.partager("alice", "bruno", "reunion", corpus["reunions"]["tarifs-b"])
    assert _enonces(ecl.faits(portee_api.lecture(bruno, "shared"))) == [
        "Le prix de vente passe à 30 euros."]
    partages.partager("alice", "bruno", "reunion", corpus["reunions"]["rachat"])
    assert "Rachat de la société Tapir." not in _enonces(
        ecl.faits(portee_api.lecture(bruno, "shared")))


# ---------- Déclenchement ----------

def test_un_compte_rendu_produit_ne_lance_plus_l_extraction(corpus, modele, monkeypatch) -> None:
    """Découplage ECL / CR : le CR ne lance plus l'extraction des faits ; elle se fait à
    la demande (onglet « Faits », route POST de la réunion) ou par `ecl-extraire`."""
    from core import report
    from core.experimental import app_report

    sortie = {"title": "Tarifs", "language": "fr", "units": [], "final": {},
              "struct": {}, "rendered": {"sections": []},
              "diag": {"provider": "mlx", "totals": {}, "stages": {
                  "stage1_extract": {"failed_chunks": []}}}}
    monkeypatch.setattr(app_report._pipeline, "run_stages", lambda *a, **k: sortie)
    monkeypatch.setattr(app_report, "_assemble_app_report", lambda *a, **k: "---\ntype: report\n---\n\nCR\n")
    monkeypatch.setattr(report, "classify_genre", lambda *a, **k: "projet")
    monkeypatch.setattr(app_report.semantic, "index_name", lambda name: 0)

    resultat = app_report.make_local_report(corpus["noms"]["tarifs-a"], provider="mlx", seed=7)
    assert resultat["warnings"] is None
    assert modele.appels == [] and ecl.faits(None) == []
    assert corpus["noms"]["tarifs-a"] in ecl.a_extraire()[0]


# ---------- Extraction partielle : k tranches sur n en échec, 0 < k < n ----------

class _ModeleEnPartie(FauxModele):
    """Échoue sur la tranche qui contient `repere`, répond comme `FauxModele` ailleurs."""

    def __init__(self, repere: str):
        super().__init__()
        self.repere = repere

    def __call__(self, system, user, **options):
        if self.repere in user:
            self.appels.append(options)
            raise RuntimeError("tranche en panne")
        return super().__call__(system, user, **options)


PARTIELLE = {"tranches_en_echec": 1, "tranches": 3}


@pytest.fixture
def par_replique(monkeypatch):
    # Une tranche par réplique : « tarifs-a » en fait trois.
    monkeypatch.setattr(ecl, "BORNE_TRANCHE", 1)


def test_une_extraction_partielle_sans_courante_est_ecrite_marquee(corpus, monkeypatch, par_replique) -> None:
    monkeypatch.setattr(ecl, "_modele", _ModeleEnPartie("Bruno prépare la grille"))
    bilan = ecl.extraire(corpus["noms"]["tarifs-a"])
    assert bilan["partielle"] == PARTIELLE and bilan["remplace"] is True
    assert ecl.lire_fichier(bilan["document_id"])["partielle"] == PARTIELLE
    assert len(ecl.faits(None)) == 2                     # les faits des tranches abouties
    assert ecl.versions(bilan["document_id"]) == []


def test_une_extraction_partielle_ne_remplace_pas_la_courante(corpus, monkeypatch, par_replique) -> None:
    monkeypatch.setattr(ecl, "_modele", FauxModele())
    document = ecl.extraire(corpus["noms"]["tarifs-a"])["document_id"]
    avant, faits_avant = ecl.lire_fichier(document), ecl.faits(None)
    assert "partielle" not in avant

    monkeypatch.setattr(ecl, "_modele", _ModeleEnPartie("on fixe le prix de vente"))
    bilan = ecl.extraire(corpus["noms"]["tarifs-a"])
    assert bilan["partielle"] == PARTIELLE and bilan["remplace"] is False
    assert ecl.lire_fichier(document) == avant and ecl.faits(None) == faits_avant
    # Elle va dans l'historique, marquée.
    (version,) = ecl.versions(document)
    assert json.loads(version.read_text(encoding="utf-8"))["partielle"] == PARTIELLE


def test_la_relance_complete_reprend_une_extraction_partielle(corpus, monkeypatch, par_replique) -> None:
    monkeypatch.setattr(ecl, "_modele", _ModeleEnPartie("Bruno prépare la grille"))
    document = ecl.extraire(corpus["noms"]["tarifs-a"])["document_id"]
    monkeypatch.setattr(ecl, "_modele", FauxModele())
    bilan = ecl.extraire(corpus["noms"]["tarifs-a"])
    assert bilan["partielle"] is None and bilan["remplace"] is True
    assert "partielle" not in ecl.lire_fichier(document)
    (version,) = ecl.versions(document)                   # la partielle reste dans l'historique
    assert json.loads(version.read_text(encoding="utf-8"))["partielle"] == PARTIELLE


def test_l_ecran_des_faits_porte_la_marque_partielle(corpus, monkeypatch, par_replique) -> None:
    monkeypatch.setattr(ecl, "_modele", _ModeleEnPartie("Bruno prépare la grille"))
    ecl.extraire(corpus["noms"]["tarifs-a"])
    corps = _connecte("alice").get(f"/api/meetings/{corpus['reunions']['tarifs-a']}/ecl",
                                   params={"view_scope": "all"}).json()
    assert corps["faits"] and all(f["partielle"] == PARTIELLE for f in corps["faits"])
    monkeypatch.setattr(ecl, "_modele", FauxModele())
    ecl.extraire(corpus["noms"]["tarifs-a"])
    corps = _connecte("alice").get(f"/api/meetings/{corpus['reunions']['tarifs-a']}/ecl",
                                   params={"view_scope": "all"}).json()
    assert corps["faits"] and all(f["partielle"] is None for f in corps["faits"])


def test_la_commande_ecl_extraire_simule_puis_applique(corpus, modele, monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli.llm, "is_available", lambda *a, **k: True)
    assert cli.main(["ecl-extraire"]) == 0
    simulation = capsys.readouterr().out
    assert "Simulation : rien n'est écrit." in simulation
    assert f"Transcripts sans faits ECL : {len(corpus['noms'])}" in simulation
    assert not (corpus["sortie"] / ".ecl").exists()

    assert cli.main(["ecl-extraire", "--appliquer"]) == 0
    applique = capsys.readouterr().out
    total = re.search(r"Total : (\d+) fait\(s\) gardé\(s\), (\d+) refusé\(s\), 0 document", applique)
    assert total and int(total.group(1)) == len(ecl.faits(None)) == 10
    assert cli.main(["ecl-extraire"]) == 0
    assert "Transcripts sans faits ECL : 0" in capsys.readouterr().out


# ---------- Journal et serveur ----------

def test_le_journal_du_banc_garde_chaque_proposition_hors_des_faits(corpus, monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(ecl, "_modele", FauxModele(inventer=True))
    monkeypatch.setattr(cli.llm, "is_available", lambda *a, **k: True)
    monkeypatch.setattr(cli.llm, "unload", lambda *a, **k: False)
    journal = tmp_path / "journal.jsonl"
    assert cli.main(["ecl-extraire", "--appliquer", "--journal", str(journal)]) == 0
    entrees = [json.loads(ligne) for ligne in journal.read_text(encoding="utf-8").splitlines()]
    tarifs = [e for e in entrees if e["document"] == "tarifs-a.md"]
    assert sum(1 for e in tarifs if e.get("garde")) == 2
    assert sorted(e["motif"] for e in tarifs if e.get("motif")) == sorted(m for _f, m in INVENTES)
    gardes = {f["id"] for f in ecl.faits(None)}
    assert {e["garde"] for e in entrees if e.get("garde")} == gardes   # refus : jamais des faits
    assert not list((corpus["sortie"] / ".ecl").glob("*.jsonl"))

    dedans = corpus["sortie"] / "journal.jsonl"
    assert cli.main(["ecl-extraire", "--appliquer", "--journal", str(dedans)]) == 1
    assert not dedans.exists()


def test_la_commande_rend_le_serveur_qu_elle_a_demarre(corpus, monkeypatch) -> None:
    rendus = []
    monkeypatch.setattr(cli.llm, "is_available", lambda *a, **k: True)
    monkeypatch.setattr(cli.llm, "unload", lambda *a, **k: rendus.append(True) or True)
    monkeypatch.setattr(ecl, "_modele", FauxModele(panne=True))
    assert cli.main(["ecl-extraire", "--appliquer"]) == 1           # documents en panne
    assert rendus == [True]
    monkeypatch.setattr(ecl, "_modele", FauxModele())
    assert cli.main(["ecl-extraire", "--appliquer"]) == 0
    assert rendus == [True, True]


def test_la_commande_n_extrait_qu_en_local(corpus, monkeypatch) -> None:
    """`ecl-extraire` parcourt toute la bibliothèque, ultra-confidentiel compris : elle
    n'appelle jamais qu'un fournisseur local, faute de quoi la règle « jamais » tomberait."""
    import inspect
    defaut = inspect.signature(ecl.extraire).parameters["provider"].default
    appels: list[str] = []
    monkeypatch.setattr(cli.llm, "is_available", lambda *a, **k: True)
    monkeypatch.setattr(cli.llm, "unload", lambda *a, **k: True)
    monkeypatch.setattr(ecl, "extraire", lambda nom, **k: appels.append(k.get("provider", defaut))
                        or {"gardes": 0, "refuses": 0, "refus": {}, "appels_en_echec": 0})

    assert cli.main(["ecl-extraire", "--appliquer"]) == 0

    assert appels, "ancre : la commande a bien extrait"
    assert all(cli.llm.est_local(p) for p in appels), appels



# ---------- 2c-bis : citation sur une à trois répliques ----------

def _un_fait(monkeypatch, **fait) -> dict:
    monkeypatch.setattr(ecl, "_modele", lambda *a, **k: {"faits": [fait]})
    return fait


def test_une_citation_sur_trois_repliques_est_ancree_sur_la_premiere(corpus, monkeypatch) -> None:
    _un_fait(monkeypatch, type="decision", horodatage="00:00:03",
             citation="on commence par les tarifs après discussion on fixe le prix de vente "
                      "à 25 euros d'accord Bruno prépare",
             enonce="Le prix de vente est fixé à 25 euros.")
    assert ecl.extraire(corpus["noms"]["tarifs-a"])["gardes"] == 1
    assert [(f["horodatage"], f["locuteur"]) for f in ecl.faits(None)] == [("00:00:02", "Alice")]


def test_une_citation_sur_quatre_repliques_est_refusee(corpus, monkeypatch) -> None:
    (corpus["sortie"] / "Clients" / "quatre.md").write_text(
        _transcript(docid.nouveau(), docid.nouveau(), corpus["clients"].id, "quatre", "2026-03-01", [
            ("A", "00:00:01", "un deux"), ("B", "00:00:02", "trois quatre"),
            ("A", "00:00:03", "cinq six"), ("B", "00:00:04", "sept huit")]), encoding="utf-8")
    library.reindex()
    _un_fait(monkeypatch, type="decision", horodatage="00:00:01",
             citation="un deux trois quatre cinq six sept huit", enonce="On compte.")
    assert ecl.extraire("quatre.md")["refus"]["citation_absente"] == 1
    _un_fait(monkeypatch, type="decision", horodatage="00:00:01",
             citation="un deux trois quatre cinq six", enonce="On compte.")
    assert ecl.extraire("quatre.md")["gardes"] == 1


def test_un_locuteur_cite_peut_etre_nomme_dans_l_enonce_pas_un_autre(corpus, monkeypatch) -> None:
    (corpus["sortie"] / "Clients" / "voix.md").write_text(
        _transcript(docid.nouveau(), docid.nouveau(), corpus["clients"].id, "voix", "2026-03-01", [
            ("Speaker 1", "00:00:05", "do we keep the yellow case for the remote"),
            ("Speaker 2", "00:00:07", "yes we keep the yellow case"),
            ("Speaker 3", "00:00:40", "the budget is 12 euros")]), encoding="utf-8")
    library.reindex()
    citation = "do we keep the yellow case for the remote yes we keep the yellow case"
    _un_fait(monkeypatch, type="decision", horodatage="00:00:05", citation=citation,
             enonce="Speaker 2 confirms the yellow case.")
    assert ecl.extraire("voix.md")["gardes"] == 1
    _un_fait(monkeypatch, type="decision", horodatage="00:00:05", citation=citation,
             enonce="Speaker 3 confirms the yellow case.")
    assert ecl.extraire("voix.md")["refus"]["contenance"] == 1
    _un_fait(monkeypatch, type="decision", horodatage="00:00:05", citation=citation,
             enonce="The yellow case costs 12 euros.")                # chiffre d'une autre réplique
    assert ecl.extraire("voix.md")["refus"]["contenance"] == 1


def test_un_engagement_a_la_premiere_personne_revient_a_sa_voix(corpus, monkeypatch) -> None:
    (corpus["sortie"] / "Clients" / "engagement.md").write_text(
        _transcript(docid.nouveau(), docid.nouveau(), corpus["clients"].id, "engagement", "2026-03-01", [
            ("Speaker 1", "00:00:05", "who sends the minutes to the team"),
            ("Speaker 2", "00:00:07", "I'll send the minutes tonight"),
            ("Speaker 1", "00:00:20", "you prepare the budget for monday")]), encoding="utf-8")
    library.reindex()
    monkeypatch.setattr(ecl, "_modele", lambda *a, **k: {"faits": [
        {"type": "action", "horodatage": "00:00:05",
         "citation": "who sends the minutes to the team I'll send the minutes tonight",
         "enonce": "The minutes are sent tonight."},
        {"type": "action", "horodatage": "00:00:20", "citation": "you prepare the budget for monday",
         "enonce": "The budget is prepared for monday."},
        {"type": "decision", "horodatage": "00:00:07", "citation": "I'll send the minutes tonight",
         "enonce": "The minutes go out tonight."}]})
    ecl.extraire("engagement.md")
    assert sorted((f["type"], f["porteur"] or "") for f in ecl.faits(None)
                  if f["document"] == "engagement.md") == [
        ("action", ""), ("action", "Speaker 2"), ("decision", "")]


# ---------- 2c-quater : tolérance au format, sans rien accepter de plus ----------

def test_le_decor_recopie_est_retire_avant_le_controle(corpus, monkeypatch) -> None:
    monkeypatch.setattr(ecl, "_modele", lambda *a, **k: {"faits": [
        {"type": "Décision", "horodatage": "00:00:10",
         "citation": "**Alice** [00:00:10] après discussion **on fixe le prix de vente à 25 euros**",
         "enonce": "Le prix de vente est fixé à **25 euros**."},
        {"type": "action", "horodatage": "00:00:38",
         "citation": "**Bruno**: d'accord, Bruno prépare la grille tarifaire pour jeudi",
         "enonce": "Bruno prépare la grille tarifaire.", "porteur": "Bruno"},
        {"type": "problème", "horodatage": "00:00:02",
         "citation": "[00:00:02] bonjour à tous, on commence par les tarifs",
         "enonce": "Les tarifs restent à traiter."}]})
    bilan = ecl.extraire(corpus["noms"]["tarifs-a"])
    assert (bilan["gardes"], bilan["refuses"]) == (3, 0)
    faits = {f["type"]: f for f in ecl.faits(None)}
    assert faits["decision"]["citation"] == "après discussion on fixe le prix de vente à 25 euros"
    assert faits["decision"]["enonce"] == "Le prix de vente est fixé à 25 euros."
    assert faits["action"]["citation"].startswith("d'accord, Bruno")
    assert set(faits) == {"decision", "action", "probleme"}


def test_le_decor_retire_ne_fait_rien_accepter_de_plus(corpus, monkeypatch) -> None:
    monkeypatch.setattr(ecl, "_modele", lambda *a, **k: {"faits": [
        {"type": "décision", "horodatage": "00:00:10",                  # mots inventés sous le gras
         "citation": "**Alice** [00:00:10] **on fixe le prix de vente à 30 euros**",
         "enonce": "Prix fixé à 30 euros."},
        {"type": "décision", "horodatage": "00:00:10",                  # chiffre inventé, en gras
         "citation": "**Alice** [00:00:10] on fixe le prix de vente à 25 euros",
         "enonce": "Prix fixé à **12** euros."},
        {"type": "rumeur", "horodatage": "00:00:10",
         "citation": "on fixe le prix de vente à 25 euros", "enonce": "Rumeur."}]})
    bilan = ecl.extraire(corpus["noms"]["tarifs-a"])
    assert bilan["gardes"] == 0
    assert {m: n for m, n in bilan["refus"].items() if n} == {
        "citation_absente": 1, "contenance": 1, "type_inconnu": 1}


def test_un_faux_modele_qui_invente_avec_du_decor_ne_fait_rien_garder(corpus, monkeypatch) -> None:
    decores = [dict(f, citation=f"**Alice** [00:00:12] **{f.get('citation', '')}**",
                    type=str(f.get("type", "")).capitalize()) for f, _m in INVENTES]
    monkeypatch.setattr(ecl, "_modele", lambda *a, **k: {"faits": decores})
    assert ecl.extraire(corpus["noms"]["tarifs-a"])["gardes"] == 0


# ---------- Lecture par l'API : les faits d'une réunion, pour l'écran ----------

def _connecte(identifiant: str):
    from fastapi.testclient import TestClient

    from api import main as api_main

    client = TestClient(api_main.app)
    assert client.post("/api/auth/connexion", json={
        "identifiant": identifiant, "mot_de_passe": MOT_DE_PASSE}).status_code == 200
    return client


def test_la_route_rend_les_faits_d_une_reunion_avec_leur_mode(corpus, modele) -> None:
    from core import llm

    _tout_extraire(corpus)
    alice = _connecte("alice")
    reunion = corpus["reunions"]["tarifs-a"]
    corps = alice.get(f"/api/meetings/{reunion}/ecl", params={"view_scope": "all"}).json()
    attendus = ecl.faits(portee_api.lecture(_compte("alice"), "all"), meeting_id=reunion)
    assert corps["meeting_id"] == reunion and attendus
    assert [f["id"] for f in corps["faits"]] == [f["id"] for f in attendus]   # la lecture de core, telle quelle
    fait = corps["faits"][0]
    assert {"type", "citation", "enonce", "horodatage", "porteur", "locuteur", "document"} <= set(fait)
    assert fait["enonce_statut"] == "reformulé" and fait["modele"] == llm.modele_servi()
    # Le modèle du CR (repli local) : « à vérifier », et aucun taux de précision.
    assert (fait["mode"], fait["a_verifier"]) == ("local", True) and "taux" not in fait


def test_le_mode_vient_du_fournisseur_qui_sert_le_modele() -> None:
    from api import main as api_main
    from core import llm

    assert api_main._mode_du_modele(llm.modele_servi("mlx")) == "local"
    assert api_main._mode_du_modele(llm.modele_servi("openai")) == "cloud"
    assert api_main._mode_du_modele(llm.modele_servi("anthropic")) == "cloud"
    assert api_main._mode_du_modele("modele-inconnu") is None and api_main._mode_du_modele(None) is None


def test_la_route_est_bornee_au_compte_et_a_ses_partages(corpus, modele) -> None:
    _tout_extraire(corpus)
    reunion = corpus["reunions"]["tarifs-b"]
    bruno = _connecte("bruno")
    assert bruno.get(f"/api/meetings/{reunion}/ecl", params={"view_scope": "all"}).status_code == 404
    assert bruno.get(f"/api/meetings/{reunion}/ecl", params={"view_scope": "shared"}).status_code == 404
    partage = partages.partager("alice", "bruno", "reunion", reunion)
    vus = bruno.get(f"/api/meetings/{reunion}/ecl", params={"view_scope": "shared"}).json()["faits"]
    assert vus and all(f["document"] == corpus["noms"]["tarifs-b"] for f in vus)
    assert partages.retirer("alice", partage["id"])
    assert bruno.get(f"/api/meetings/{reunion}/ecl", params={"view_scope": "shared"}).status_code == 404
    partages.partager("alice", "bruno", "reunion", corpus["reunions"]["rachat"])     # ultra-confidentiel
    reponse = bruno.get(f"/api/meetings/{corpus['reunions']['rachat']}/ecl", params={"view_scope": "shared"})
    assert reponse.status_code == 404 or reponse.json()["faits"] == []
    assert _connecte("alice").get("/api/meetings/reunion-inconnue/ecl", params={"view_scope": "all"}).status_code == 404


def test_une_reunion_archivee_ou_purgee_n_a_plus_aucun_fait(corpus, modele) -> None:
    _tout_extraire(corpus)
    alice = _connecte("alice")
    for cle, geste in (("planning-a", "archive"), ("planning-b", "purge")):
        reunion, nom = corpus["reunions"][cle], corpus["noms"][cle]
        assert alice.get(f"/api/meetings/{reunion}/ecl", params={"view_scope": "all"}).json()["faits"]
        if geste == "archive":
            purge.archive(nom)
        else:
            purge.purge_transcript(nom)
        reponse = alice.get(f"/api/meetings/{reunion}/ecl", params={"view_scope": "all"})
        assert reponse.status_code == 404 or reponse.json()["faits"] == []


# ---------- 2d-a : deux modes, local et cloud ----------

class _ModeleQuiRetient(FauxModele):
    def __call__(self, system, user, **options):
        rendu = super().__call__(system, user)
        self.appels[-1] = {"system": system, "user": user, **options}     # un appel, tout retenu
        return rendu


def test_la_consigne_et_le_plafond_suivent_le_mode(corpus, monkeypatch) -> None:
    from core import ecl_consignes

    modele = _ModeleQuiRetient()
    monkeypatch.setattr(ecl, "_modele", modele)
    local = ecl.extraire(corpus["noms"]["tarifs-a"], provider="mlx")
    nuage = ecl.extraire(corpus["noms"]["tarifs-b"], provider="anthropic")
    assert (local["mode"], nuage["mode"]) == ("local", "cloud")
    appel_local, appel_nuage = modele.appels[0], modele.appels[-1]
    assert appel_local["system"] == ecl_consignes.GABARIT and appel_nuage["system"] == ecl_consignes.DEFINITIONS
    assert (appel_local["max_tokens"], appel_nuage["max_tokens"]) == (4000, 16000)
    assert appel_local["user"].startswith("Langue de la réunion, et des énoncés : français")


def test_une_consigne_imposee_par_le_banc_l_emporte(corpus, monkeypatch) -> None:
    modele = _ModeleQuiRetient()
    monkeypatch.setattr(ecl, "_modele", modele)
    monkeypatch.setattr(ecl, "_SYSTEME", "consigne imposée")
    ecl.extraire(corpus["noms"]["tarifs-a"], provider="anthropic")
    assert {a["system"] for a in modele.appels} == {"consigne imposée"}


def test_mode_fournisseur_et_affichage_sont_notes_puis_relus_apres_reindexer(corpus, modele) -> None:
    from core import llm

    local = ecl.extraire(corpus["noms"]["tarifs-a"], provider="mlx")
    ecl.extraire(corpus["noms"]["tarifs-b"], provider="openai")
    contenu = ecl.lire_fichier(local["document_id"])
    assert (contenu["mode"], contenu["fournisseur"], contenu["avertissement"]) == ("local", "mlx", None)
    assert contenu["modele"] == llm.modele_servi("mlx")
    assert all(f["mode"] == "local" and f["fournisseur"] == "mlx" and f["affiche"] for f in contenu["faits"])
    ecl.reindexer()
    tous = ecl.faits(portee_api.lecture(_compte("alice"), "all"))
    par_doc = {f["document"]: f for f in tous}
    assert (par_doc["tarifs-a.md"]["mode"], par_doc["tarifs-a.md"]["fournisseur"]) == ("local", "mlx")
    assert (par_doc["tarifs-b.md"]["mode"], par_doc["tarifs-b.md"]["fournisseur"]) == ("cloud", "openai")
    assert par_doc["tarifs-b.md"]["affiche"] is None                   # affichage C : en local seulement


def test_une_citation_courte_s_etend_a_la_replique_suivante() -> None:
    liste = ecl.repliques("**A** [00:00:01] on garde le rouge\n**B** [00:00:04] oui, c'est d'accord pour tout le monde\n")
    courte = ecl.affichage("on garde le rouge", liste[:1], liste)
    assert courte == "on garde le rouge oui, c'est d'accord pour tout le monde"
    longue = "une citation assez longue pour se suffire à elle-même sans aide"
    assert ecl.affichage(longue, liste[:1], liste) == longue
    assert ecl.affichage("on garde le rouge", liste[1:], liste) == "on garde le rouge"   # pas de suivante


def test_sans_granite_l_ecl_le_signale_et_retombe_sur_le_modele_du_cr(corpus, modele, monkeypatch) -> None:
    from core import config

    monkeypatch.setattr(config, "ECL_MLX_MODEL", "ibm-granite/granite-4.2-8b-q4-mlx")
    monkeypatch.setattr(ecl, "_modele_present", lambda m: False)
    bilan = ecl.extraire(corpus["noms"]["tarifs-a"], provider="mlx")
    assert "absent" in bilan["avertissement"] and bilan["gardes"] >= 1
    assert all("moteur" not in options for options in modele.appels)       # le moteur du CR a servi
    monkeypatch.setattr(ecl, "_modele_present", lambda m: True)
    moteur, avertissement = ecl.moteur_local()
    assert avertissement is None and moteur.modele_servi() == "ibm-granite/granite-4.2-8b-q4-mlx"
    assert moteur._gabarit == {"enable_thinking": False}                     # réflexion coupée


def test_l_ecl_locale_passe_par_son_moteur_et_le_rend(corpus, monkeypatch) -> None:
    from core import config, llm

    class FauxMoteur:
        rendu = 0

        def modele_servi(self, model=None):
            return "granite-faux"

        def unload(self):
            FauxMoteur.rendu += 1
            return True

    modele = _ModeleQuiRetient()
    monkeypatch.setattr(ecl, "_modele", modele)
    monkeypatch.setattr(ecl, "moteur_local", lambda: (FauxMoteur(), None))
    monkeypatch.setattr(llm, "unload", lambda *a, **k: True)
    bilan = ecl.extraire(corpus["noms"]["tarifs-a"], provider="mlx")
    assert all(isinstance(a["moteur"], FauxMoteur) for a in modele.appels)
    assert ecl.lire_fichier(bilan["document_id"])["modele"] == "granite-faux" and FauxMoteur.rendu == 1
    assert config.MLX_LLM_MODEL != "granite-faux"                            # le CR garde le sien


def test_la_route_lit_le_mode_note_sans_taux_du_modele(corpus, modele, monkeypatch) -> None:
    ecl.extraire(corpus["noms"]["tarifs-a"], provider="anthropic")
    corps = _connecte("alice").get(f"/api/meetings/{corpus['reunions']['tarifs-a']}/ecl",
                                   params={"view_scope": "all"}).json()
    fait = corps["faits"][0]
    assert (fait["mode"], fait["fournisseur"], fait["a_verifier"]) == ("cloud", "anthropic", False)
    assert "taux" not in fait
    assert fait["modele"] == "claude-sonnet-5-5"


def test_aucune_table_de_taux_dans_core_ni_dans_la_route(corpus, modele) -> None:
    """Aucun taux de précision dans `core`, ni vers l'écran."""
    assert not hasattr(ecl, "TAUX") and not hasattr(ecl, "taux")
    assert not hasattr(ecl, "TAUX_LOCAL")                     # plus de taux d'emprunt pour le mode local
    ecl.extraire(corpus["noms"]["tarifs-a"], provider="mlx")
    corps = _connecte("alice").get(f"/api/meetings/{corpus['reunions']['tarifs-a']}/ecl",
                                   params={"view_scope": "all"}).json()
    assert {f["mode"] for f in corps["faits"]} == {"local"}
    assert not [f for f in corps["faits"] if "taux" in f]


def test_le_modele_est_rendu_sans_taux_pour_tout_fournisseur(corpus, modele, monkeypatch) -> None:
    from core import config

    def modeles_lus(cle: str) -> set:
        corps = _connecte("alice").get(f"/api/meetings/{corpus['reunions'][cle]}/ecl",
                                       params={"view_scope": "all"}).json()
        assert corps["faits"]
        assert not [f for f in corps["faits"] if "taux" in f], "aucun taux de précision"
        return {f["modele"] for f in corps["faits"]}

    ecl.extraire(corpus["noms"]["tarifs-a"], provider="anthropic")
    assert modeles_lus("tarifs-a") == {"claude-sonnet-5-5"}
    monkeypatch.setattr(config, "ECL_ANTHROPIC_MODEL", "claude-haiku-5-5")
    ecl.extraire(corpus["noms"]["tarifs-a"], provider="anthropic")
    assert modeles_lus("tarifs-a") == {"claude-haiku-5-5"}
    for fournisseur in ("openai", "mistral"):
        ecl.extraire(corpus["noms"]["tarifs-b"], provider=fournisseur)
        assert modeles_lus("tarifs-b")


def test_granite_ne_porte_aucun_taux(corpus, monkeypatch) -> None:
    from core import llm

    class Granite:
        def modele_servi(self, model=None):
            return "ibm-granite/granite-4.2-8b-q4-mlx"

        def unload(self):
            return True

    monkeypatch.setattr(ecl, "_modele", FauxModele())
    monkeypatch.setattr(ecl, "moteur_local", lambda: (Granite(), None))
    monkeypatch.setattr(llm, "unload", lambda *a, **k: True)
    ecl.extraire(corpus["noms"]["tarifs-a"], provider="mlx")
    corps = _connecte("alice").get(f"/api/meetings/{corpus['reunions']['tarifs-a']}/ecl",
                                   params={"view_scope": "all"}).json()
    assert {(f["mode"], f["a_verifier"], f["modele"]) for f in corps["faits"]} == {
        ("local", True, "ibm-granite/granite-4.2-8b-q4-mlx")}
    assert not [f for f in corps["faits"] if "taux" in f]


def test_les_faits_en_anthropic_passent_par_sonnet_jusqu_a_la_requete(corpus, monkeypatch) -> None:
    """Sonnet pour l'ECL, Haiku pour le compte rendu : le modèle part dans la requête,
    la télémétrie (FinOps) le note, et le fichier de faits aussi."""
    import httpx

    from core import config, llm, llm_anthropic, telemetry

    corps_envoyes, appels = [], []

    def repondre(requete):
        corps_envoyes.append(json.loads(requete.content))
        return httpx.Response(200, json={"stop_reason": "end_turn", "usage": {"input_tokens": 10, "output_tokens": 5},
                                         "content": [{"type": "text", "text": '{"faits": []}'}]})

    monkeypatch.setattr(llm_anthropic, "_transport", httpx.MockTransport(repondre))
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "cle-factice")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cle-factice")
    monkeypatch.setattr(telemetry, "record_model_call", lambda **k: appels.append(k))
    # Le chemin de `ecl._modele` sans moteur local, que la suite remplace par défaut.
    monkeypatch.setattr(ecl, "_modele", lambda system, user, **options: llm.chat_json(system, user, **options))
    bilan = ecl.extraire(corpus["noms"]["tarifs-a"], provider="anthropic")
    assert corps_envoyes and {c["model"] for c in corps_envoyes} == {"claude-sonnet-5-5"}
    assert {(a["provider"], a["model"]) for a in appels} == {("anthropic", "claude-sonnet-5-5")}
    assert ecl.lire_fichier(bilan["document_id"])["modele"] == "claude-sonnet-5-5"
    assert llm.modele_servi("anthropic") == "claude-haiku-5-5"            # le CR et l'Assistant gardent Haiku


# ---------- interface-b1 : relancer l'extraction d'une réunion ----------

def _jobs_sur_place(monkeypatch):
    from api import main as api_main

    lances = []
    monkeypatch.setattr(api_main.jobs, "run", lambda job, target: lances.append(target(lambda *_a: None)))
    return lances


def test_relancer_l_extraction_garde_la_version_precedente(corpus, modele, monkeypatch) -> None:
    lances = _jobs_sur_place(monkeypatch)
    _tout_extraire(corpus)
    reunion, nom = corpus["reunions"]["tarifs-a"], corpus["noms"]["tarifs-a"]
    document = ecl.lire_fichier(next(f["document_id"] for f in ecl.faits(
        portee_api.lecture(_compte("alice"), "all"), meeting_id=reunion)))["document_id"]
    reponse = _connecte("alice").post(f"/api/meetings/{reunion}/ecl", params={"view_scope": "all"},
                                      json={"provider": "mlx"})
    assert reponse.status_code == 200 and reponse.json()["job_id"] and lances[0]["gardes"] >= 1
    anciennes = ecl.versions(document)
    assert len(anciennes) == 1                                            # l'extraction d'avant, datée
    assert ecl.lire_fichier(document) and anciennes[0].read_text(encoding="utf-8")
    purge.purge_transcript(nom)
    assert ecl.versions(document) == []                                   # la purge n'en laisse rien


def test_relancer_est_reserve_au_proprietaire_et_aux_fournisseurs_proposes(corpus, modele, monkeypatch) -> None:
    _jobs_sur_place(monkeypatch)
    _tout_extraire(corpus)
    reunion = corpus["reunions"]["tarifs-b"]
    bruno = _connecte("bruno")
    assert bruno.post(f"/api/meetings/{reunion}/ecl", params={"view_scope": "all"},
                      json={"provider": "mlx"}).status_code == 404
    partages.partager("alice", "bruno", "reunion", reunion)
    assert bruno.post(f"/api/meetings/{reunion}/ecl", params={"view_scope": "shared"},
                      json={"provider": "mlx"}).status_code == 404          # un invité ne réécrit rien
    alice = _connecte("alice")
    from core import llm
    monkeypatch.setattr(llm, "HORS_INTERFACE", frozenset({"openai"}))
    assert alice.post(f"/api/meetings/{reunion}/ecl", params={"view_scope": "all"},
                      json={"provider": "openai"}).status_code == 400       # hors interface
    monkeypatch.setattr(llm, "HORS_INTERFACE", frozenset())
    assert alice.post(f"/api/meetings/{reunion}/ecl", params={"view_scope": "all"},
                      json={"provider": "inconnu"}).status_code == 400
    rachat = corpus["reunions"]["rachat"]
    assert alice.post(f"/api/meetings/{rachat}/ecl", params={"view_scope": "all"},
                      json={"provider": "openai"}).status_code == 403       # ultra-confidentiel : pas de cloud
    assert alice.post(f"/api/meetings/{rachat}/ecl", params={"view_scope": "all"},
                      json={"provider": "anthropic"}).status_code == 403
    assert alice.post(f"/api/meetings/{rachat}/ecl", params={"view_scope": "all"},
                      json={"provider": "mlx"}).status_code == 200          # en local, oui
    from api import main as api_main
    from api.jobs import JobManager
    monkeypatch.setattr(api_main, "jobs", JobManager())                    # créneau libéré
    _jobs_sur_place(monkeypatch)
    assert alice.post(f"/api/meetings/{reunion}/ecl", params={"view_scope": "all"},
                      json={"provider": "anthropic"}).status_code == 200    # proposé à l'écran


def test_une_relance_ouvre_une_execution_finops_au_modele_des_faits(corpus, modele, monkeypatch) -> None:
    # Hors d'une exécution, la télémétrie est un no-op : une relance en cloud (Sonnet)
    # échappait au suivi des coûts.
    from contextlib import contextmanager

    from api import main as api_main
    from api.jobs import JobManager
    from core import telemetry
    monkeypatch.setattr(api_main, "jobs", JobManager())                    # créneau libre
    _jobs_sur_place(monkeypatch)
    executions: list[dict] = []
    etapes: list[str] = []
    origine_execution, origine_etape = telemetry.execution, telemetry.stage

    @contextmanager
    def execution(kind, **champs):
        executions.append({"kind": kind, **champs})
        with origine_execution(kind, **champs):
            yield

    @contextmanager
    def stage(nom):
        etapes.append(nom)
        with origine_etape(nom):
            yield
    monkeypatch.setattr(telemetry, "execution", execution)
    monkeypatch.setattr(telemetry, "stage", stage)
    reunion = corpus["reunions"]["tarifs-a"]
    assert _connecte("alice").post(f"/api/meetings/{reunion}/ecl", params={"view_scope": "all"},
                                   json={"provider": "anthropic"}).status_code == 200
    (lancee,) = executions
    assert lancee["kind"] == "ecl" and lancee["provider"] == "anthropic"
    assert lancee["model"] == "claude-sonnet-5-5" and lancee["meeting_id"] == reunion
    assert "ecl" in etapes


# ---------- Faits périmés : la source a changé depuis l'extraction ----------
# Un transcript corrigé (25 → 35 euros) ne garde pas son ancien fait comme actuel :
# à la synchronisation et à `reindexer()`, chaque fait est revérifié par les règles
# déterministes de l'extraction, sans modèle. Un fait qui échoue est périmé, avec son
# motif : jamais servi comme actuel, gardé dans le fichier et dans l'historique.

PRIX = "Le prix de vente est fixé à 25 euros."
GRILLE = "Bruno prépare la grille tarifaire."


def _corriger(corpus, cle: str, avant: str, apres: str):
    """Réécrit le `.md` sur disque, comme une correction à la main ; `mtime` avancé."""
    chemin = corpus["sortie"] / ("Interne" if cle == "interne" else "Clients") / f"{cle}.md"
    texte = chemin.read_text(encoding="utf-8")
    assert texte.count(avant) == 1, "ancre : le passage corrigé est bien dans le transcript"
    chemin.write_text(texte.replace(avant, apres), encoding="utf-8")
    instant = chemin.stat().st_mtime + 10
    os.utime(chemin, (instant, instant))
    return chemin


def _perimes_du_fichier(document_id: str) -> list[tuple[str, str]]:
    contenu = ecl.lire_fichier(document_id)
    return sorted((f["enonce"], f["motif_peremption"]) for f in contenu["faits"]
                  if f.get("statut") == "perime")


def test_un_transcript_corrige_puis_synchronise_perime_l_ancien_fait(corpus, modele) -> None:
    from api import mcp

    nom, reunion = corpus["noms"]["tarifs-a"], corpus["reunions"]["tarifs-a"]
    document = ecl.extraire(nom)["document_id"]
    assert _enonces(ecl.faits(None)) == [GRILLE, PRIX]

    library.sync_file(_corriger(corpus, "tarifs-a", "à 25 euros", "à 35 euros"))

    assert _enonces(ecl.faits(None)) == [GRILLE]                        # jamais servi comme actuel
    assert _perimes_du_fichier(document) == [(PRIX, "citation_absente")]   # gardé, avec son motif
    contenu = ecl.lire_fichier(document)
    assert len(contenu["faits"]) == 2
    assert contenu["empreinte"] == ecl.empreinte(library.get_transcript(nom)["body"])
    (avant,) = ecl.versions(document)                                   # le fichier d'avant, dans l'historique
    assert [f.get("statut", "actuel") for f in json.loads(avant.read_text(encoding="utf-8"))["faits"]] == [
        "actuel", "actuel"]

    corps = _connecte("alice").get(f"/api/meetings/{reunion}/ecl", params={"view_scope": "all"}).json()
    assert [f["enonce"] for f in corps["faits"]] == [GRILLE]
    assert [(f["enonce"], f["statut"], f["motif_peremption"]) for f in corps["perimes"]] == [
        (PRIX, "perime", "citation_absente")]
    lu = mcp.Lecteur(_compte("alice")).lire_faits({"reunion": reunion})
    assert [f["enonce"] for f in lu["faits"]] == [GRILLE]               # le MCP ne sert que l'actuel


def test_apres_reindexation_complete_et_redemarrage_l_ancien_fait_est_perime(corpus, modele, monkeypatch,
                                                                            tmp_path) -> None:
    nom = corpus["noms"]["tarifs-a"]
    document = ecl.extraire(nom)["document_id"]
    _corriger(corpus, "tarifs-a", "à 25 euros", "à 35 euros")

    # Redémarrage sur un index reconstruit : une base neuve, puis l'ordre du démarrage.
    monkeypatch.setattr(library, "DB_PATH", tmp_path / "index-reconstruit.db")
    library.reindex()
    stats = ecl.reindexer()
    assert (stats["documents"], stats["reverifies"], stats["perimes"]) == (1, 1, 1)
    assert _enonces(ecl.faits(None)) == [GRILLE]
    assert _perimes_du_fichier(document) == [(PRIX, "citation_absente")]
    assert ecl.reindexer()["reverifies"] == 0                           # empreinte à jour : rien à refaire


def test_la_reindexation_de_l_interface_revérifie_aussi_les_faits(corpus, modele) -> None:
    ecl.extraire(corpus["noms"]["tarifs-a"])
    _corriger(corpus, "tarifs-a", "à 25 euros", "à 35 euros")
    auth.creer_compte("admin", MOT_DE_PASSE, admin=True)
    assert _connecte("admin").post("/api/library/reindex").status_code == 200
    assert _enonces(ecl.faits(None)) == [GRILLE]


def test_une_nouvelle_extraction_suit_la_source_corrigee(corpus, modele, monkeypatch, capsys) -> None:
    nom = corpus["noms"]["tarifs-a"]
    document = ecl.extraire(nom)["document_id"]
    library.sync_file(_corriger(corpus, "tarifs-a", "à 25 euros", "à 35 euros"))
    assert nom in ecl.a_extraire()[0]                                   # des faits périmés : à réextraire
    monkeypatch.setattr(cli.llm, "is_available", lambda *a, **k: True)
    monkeypatch.setattr(cli.llm, "unload", lambda *a, **k: False)
    assert cli.main(["ecl-extraire"]) == 0
    sortie = capsys.readouterr().out
    assert "Transcripts aux faits périmés : 1" in sortie and f"  - {nom}" in sortie

    monkeypatch.setitem(PROPOSES, "fixe le prix de vente à 35 euros", [
        {"type": "decision", "horodatage": "00:00:10", "citation": "on fixe le prix de vente à 35 euros",
         "enonce": "Le prix de vente est fixé à 35 euros."},
        PROPOSES["fixe le prix de vente à 25 euros"][1]])               # la grille, inchangée
    ecl.extraire(nom)
    assert _enonces(ecl.faits(None)) == [GRILLE, "Le prix de vente est fixé à 35 euros."]
    contenu = ecl.lire_fichier(document)
    assert [f.get("statut") for f in contenu["faits"]] == ["actuel", "actuel"]   # remplace le tout
    assert contenu["empreinte"] == ecl.empreinte(library.get_transcript(nom)["body"])
    assert nom not in ecl.a_extraire()[0]
    assert len(ecl.versions(document)) == 2


def test_une_metadonnee_changee_ne_perime_rien(corpus, modele) -> None:
    nom = corpus["noms"]["tarifs-a"]
    document = ecl.extraire(nom)["document_id"]
    fichier = ecl.chemin(document)
    avant = fichier.read_bytes()
    library.sync_file(_corriger(corpus, "tarifs-a", "title: tarifs-a", "title: Tarifs de mars"))
    assert library.get_transcript(nom)["meta"]["title"] == "Tarifs de mars"   # ancre : la correction a pris
    assert _enonces(ecl.faits(None)) == [GRILLE, PRIX]
    assert fichier.read_bytes() == avant and ecl.versions(document) == []     # rien réécrit


def test_un_ancien_fichier_sans_empreinte_est_revérifié(corpus, modele) -> None:
    nom = corpus["noms"]["tarifs-a"]
    document = ecl.extraire(nom)["document_id"]
    # Un fichier d'avant les empreintes, tiré d'une version plus ancienne du transcript :
    # sa citation de prix n'y figure plus mot pour mot.
    ancien = ecl.lire_fichier(document)
    ancien.pop("empreinte", None)
    for fait in ancien["faits"]:
        fait.pop("statut", None)
        if fait["type"] == "decision":
            fait["citation"] = "on fixe le prix de vente à 20 euros"
            fait["enonce"] = "Le prix de vente est fixé à 20 euros."
    ecl.chemin(document).write_text(json.dumps(ancien, ensure_ascii=False), encoding="utf-8")
    ecl.reindexer()
    assert _enonces(ecl.faits(None)) == [GRILLE]
    assert _perimes_du_fichier(document) == [("Le prix de vente est fixé à 20 euros.", "citation_absente")]
    assert ecl.lire_fichier(document)["empreinte"] == ecl.empreinte(library.get_transcript(nom)["body"])


def test_la_synchronisation_revérifie_aussi_un_fichier_sans_empreinte(corpus, modele) -> None:
    nom = corpus["noms"]["tarifs-a"]
    document = ecl.extraire(nom)["document_id"]
    ancien = ecl.lire_fichier(document)
    ancien.pop("empreinte", None)
    ecl.chemin(document).write_text(json.dumps(ancien, ensure_ascii=False), encoding="utf-8")
    library.sync_file(library.transcript_path(nom))
    assert ecl.lire_fichier(document)["empreinte"] == ecl.empreinte(library.get_transcript(nom)["body"])
    assert _enonces(ecl.faits(None)) == [GRILLE, PRIX]                  # rien d'échoué, rien de périmé


def _releve(racine) -> dict:
    """Chaque fichier sous la racine de données : taille, date et contenu."""
    import hashlib
    fichiers = sorted(p for p in racine.rglob("*") if p.is_file())
    return {str(p): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest()) for p in fichiers}


def test_lire_les_faits_n_ecrit_aucun_fichier_et_ne_relit_pas_les_transcripts(corpus, modele, monkeypatch) -> None:
    from api import mcp

    _tout_extraire(corpus)
    reunion = corpus["reunions"]["tarifs-a"]
    # Corrigé sans synchronisation : une lecture qui revérifierait le verrait.
    _corriger(corpus, "tarifs-a", "à 25 euros", "à 35 euros")
    alice = _connecte("alice")
    # La portée « all » de l'interface crée à la demande le « Non classé » du compte
    # (`portee.non_classe`) : un approvisionnement du compte, pas une lecture de faits.
    portee_api.non_classe(_compte("alice"))
    avant = _releve(corpus["sortie"])
    assert len(avant) > len(corpus["noms"]), "ancre : le relevé voit les transcripts et les faits"
    lignes_avant = library._conn().execute("SELECT * FROM ecl_faits ORDER BY id").fetchall()

    def interdit(*_a, **_k):
        raise AssertionError("la lecture des faits a relu un transcript")

    monkeypatch.setattr(ecl, "repliques", interdit)
    monkeypatch.setattr(ecl, "controler", interdit)
    monkeypatch.setattr(ecl, "ecrire", interdit)

    assert PRIX in _enonces(ecl.faits(None))
    corps = alice.get(f"/api/meetings/{reunion}/ecl", params={"view_scope": "all"})
    assert corps.status_code == 200 and PRIX in [f["enonce"] for f in corps.json()["faits"]]
    assert PRIX in [f["enonce"] for f in mcp.Lecteur(_compte("alice")).lire_faits({"reunion": reunion})["faits"]]

    assert _releve(corpus["sortie"]) == avant
    lignes_apres = library._conn().execute("SELECT * FROM ecl_faits ORDER BY id").fetchall()
    assert [tuple(r) for r in lignes_apres] == [tuple(r) for r in lignes_avant]


def test_portee_partage_et_archivage_valent_aussi_pour_les_faits_perimes(corpus, modele) -> None:
    _tout_extraire(corpus)
    library.sync_file(_corriger(corpus, "tarifs-a", "à 25 euros", "à 35 euros"))
    library.sync_file(_corriger(corpus, "rachat", "société Tapir", "société Castor"))
    alice, bruno = _compte("alice"), _compte("bruno")
    perimes_alice = _enonces(ecl.faits(portee_api.lecture(alice, "all"), statut="perime"))
    assert perimes_alice == [PRIX, "Rachat de la société Tapir."]
    assert ecl.faits(portee_api.lecture(bruno, "all"), statut="perime") == []

    partages.partager("alice", "bruno", "projet", corpus["clients"].id)
    vus = portee_api.lecture(bruno, "shared")
    assert _enonces(ecl.faits(vus, statut="perime")) == [PRIX]          # ultra : jamais en partage
    assert PRIX not in _enonces(ecl.faits(vus))

    nom = corpus["noms"]["tarifs-a"]
    assert purge.archive(nom) == {"archived": True, "errors": []}
    assert PRIX not in _enonces(ecl.faits(None, statut="perime"))       # archivé : plus rien
    archive = next(i["name"] for i in library.trash_items() if i["name"].startswith("tarifs-a"))
    assert purge.restore_detailed(archive)["errors"] == []
    assert PRIX in _enonces(ecl.faits(None, statut="perime"))           # restauré, toujours périmé
    assert PRIX not in _enonces(ecl.faits(None))


# ---------- Relance coordonnée : la réservation des travaux lourds ----------

class _Granite:
    """Le moteur local de l'ECL, simulé : son déchargement est noté."""

    def __init__(self, journal: list):
        self.journal = journal

    def modele_servi(self, model=None):
        return "ibm-granite/granite-4.2-8b-q4-mlx"

    def unload(self):
        self.journal.append("granite")
        return True


@pytest.fixture
def un_seul_creneau(monkeypatch):
    from core import resources
    monkeypatch.setattr(resources, "_SEM", threading.BoundedSemaphore(1))
    return resources


def test_reservation_occupee_aucun_appel_au_modele_avant_sa_liberation(corpus, modele, monkeypatch,
                                                                     un_seul_creneau) -> None:
    from core import llm

    dechargements: list = []
    monkeypatch.setattr(llm, "unload", lambda *a, **k: dechargements.append("cr") or True)
    monkeypatch.setattr(ecl, "moteur_local", lambda: (_Granite(dechargements), None))
    tenue, liberer = threading.Event(), threading.Event()

    def detenteur():
        with un_seul_creneau.heavy(wait=5, label="transcription"):
            tenue.set()
            liberer.wait(10)

    autre = threading.Thread(target=detenteur, daemon=True)
    autre.start()
    assert tenue.wait(5)
    bilan: dict = {}
    extraction = threading.Thread(target=lambda: bilan.update(ecl.extraire(corpus["noms"]["tarifs-a"])),
                                  daemon=True)
    extraction.start()
    extraction.join(0.5)
    assert extraction.is_alive(), "l'extraction attend la réservation"
    assert modele.appels == [] and dechargements == []                  # ni appel, ni déchargement
    liberer.set()
    autre.join(5)
    extraction.join(10)
    assert bilan["gardes"] == 2 and len(modele.appels) == 1
    assert dechargements == ["cr", "granite"]


def test_dans_le_pipeline_la_reservation_imbriquee_ne_bloque_pas(corpus, modele, un_seul_creneau) -> None:
    bilan: dict = {}

    def pipeline():
        with un_seul_creneau.heavy(wait=1, label="traitement"):        # comme `pipeline.run`
            bilan.update(ecl.extraire(corpus["noms"]["tarifs-a"]))

    fil = threading.Thread(target=pipeline, daemon=True)
    fil.start()
    fil.join(10)
    assert not fil.is_alive() and bilan["gardes"] == 2


def test_une_exception_pendant_l_extraction_libere_la_reservation(corpus, monkeypatch, un_seul_creneau) -> None:
    sous_reservation: list[bool] = []

    def en_panne(system, user, **options):
        sous_reservation.append(un_seul_creneau.busy())
        raise RuntimeError("le modèle ne répond pas")

    monkeypatch.setattr(ecl, "_modele", en_panne)
    with pytest.raises(ecl.ExtractionEnPanne):
        ecl.extraire(corpus["noms"]["tarifs-a"])
    assert sous_reservation == [True]                                   # l'appel était sous réservation
    assert not un_seul_creneau.busy()
    with un_seul_creneau.heavy(wait=0, label="suivant"):                # libérée tout de suite
        pass


def test_deux_relances_simultanees_donnent_un_fichier_coherent(corpus, monkeypatch, un_seul_creneau) -> None:
    from api import main as api_main
    from api.jobs import JobManager

    en_cours, maximum = [0], [0]
    verrou = threading.Lock()
    faux = FauxModele()

    def lent(system, user, **options):
        with verrou:
            en_cours[0] += 1
            maximum[0] = max(maximum[0], en_cours[0])
        time.sleep(0.3)
        try:
            return faux(system, user, **options)
        finally:
            with verrou:
                en_cours[0] -= 1

    monkeypatch.setattr(ecl, "_modele", lent)
    monkeypatch.setattr(api_main, "jobs", JobManager(max_active=2))
    reunion, nom = corpus["reunions"]["tarifs-a"], corpus["noms"]["tarifs-a"]
    alice = _connecte("alice")
    travaux = [alice.post(f"/api/meetings/{reunion}/ecl", params={"view_scope": "all"},
                          json={"provider": "mlx"}).json()["job_id"] for _ in range(2)]
    limite = time.monotonic() + 20
    while time.monotonic() < limite and any(api_main.jobs.get(j).status not in ("done", "error") for j in travaux):
        time.sleep(0.05)
    assert [api_main.jobs.get(j).status for j in travaux] == ["done", "done"], [
        api_main.jobs.get(j).error for j in travaux]
    assert maximum[0] == 1                                              # jamais deux extractions ensemble
    document = library.get_transcript(nom)["meta"]["id"]
    contenu = json.loads(ecl.chemin(document).read_text(encoding="utf-8"))   # lisible
    assert _enonces(contenu["faits"]) == [GRILLE, PRIX]
    assert len(ecl.versions(document)) == 1                             # la première, gardée
    assert not list(ecl.dossier().glob("*.tmp"))
    assert _enonces(ecl.faits(None)) == [GRILLE, PRIX]


def test_la_relance_attend_la_reservation_au_lieu_d_echouer(corpus, modele, monkeypatch, un_seul_creneau) -> None:
    from api import main as api_main
    from api.jobs import JobManager

    monkeypatch.setattr(api_main, "jobs", JobManager())
    tenue, liberer = threading.Event(), threading.Event()

    def detenteur():
        with un_seul_creneau.heavy(wait=5, label="transcription"):
            tenue.set()
            liberer.wait(10)

    threading.Thread(target=detenteur, daemon=True).start()
    assert tenue.wait(5)
    reunion = corpus["reunions"]["tarifs-a"]
    job = _connecte("alice").post(f"/api/meetings/{reunion}/ecl", params={"view_scope": "all"},
                                  json={"provider": "mlx"}).json()["job_id"]
    time.sleep(0.5)
    assert api_main.jobs.get(job).status not in ("done", "error") and modele.appels == []
    liberer.set()
    limite = time.monotonic() + 10
    while time.monotonic() < limite and api_main.jobs.get(job).status not in ("done", "error"):
        time.sleep(0.05)
    assert api_main.jobs.get(job).status == "done", api_main.jobs.get(job).error
    assert len(modele.appels) == 1


def test_ecl_extraire_refuse_pendant_que_l_application_tourne(corpus, modele, monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli.llm, "is_available", lambda *a, **k: True)
    monkeypatch.setattr(cli.llm, "unload", lambda *a, **k: False)
    racine = library.OUTPUT_DIR.parent
    (racine / ".memoglyph-api.pid").write_text(str(os.getpid()), encoding="utf-8")   # un processus vivant
    assert cli.main(["ecl-extraire", "--appliquer"]) == 1
    assert "tourne" in capsys.readouterr().err
    assert modele.appels == [] and not ecl.dossier().exists()           # rien d'extrait
    assert cli.main(["ecl-extraire", "--appliquer", "--forcer"]) == 0   # en connaissance de cause
    assert len(ecl.faits(None)) == 10
