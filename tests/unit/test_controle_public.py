"""Le contrôle des termes interdits (`tools/controle_public.py`) : texte, noms, messages, images.

Ces tests emploient une liste d'essai INOFFENSIVE (« zebre », « cactus ») : le contrôle livré
dans le dépôt public ne contient aucun terme interdit lisible, et ses tests non plus. Les
essais avec les vrais termes vivent dans les tests privés du dépôt de développement.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from tools import controle_public as cp

pytestmark = pytest.mark.unit

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
CONFIG = cp.fabriquer(["zebre", "=cactus", "bleu nuit"])
NUM = "#"                              # construit en morceaux, comme DECISION
DECISION = "D" + "-123"                 # construit en morceaux : ce test ne se signale pas lui-même


def _git(depot: Path, *args: str, env: dict | None = None) -> str:
    import os
    return subprocess.run(["git", "-c", "user.name=essai", "-c", "user.email=essai@example.org", *args],
                          cwd=depot, capture_output=True, text=True, check=True,
                          env={**os.environ, **(env or {})}).stdout


def _depot(tmp_path: Path, fichiers: dict, message: str = "initial") -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    for chemin, contenu in fichiers.items():
        cible = tmp_path / chemin
        cible.parent.mkdir(parents=True, exist_ok=True)
        cible.write_bytes(contenu if isinstance(contenu, bytes) else contenu.encode("utf-8"))
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", message)
    return tmp_path


def _types(violations) -> set[str]:
    return {v.type for v in violations}


# --- texte -----------------------------------------------------------------------------------

def test_un_terme_dans_un_texte_est_signale_avec_son_numero_de_ligne():
    v = cp.controler_texte("première\nle zebre court\nfin\n", "doc.md", CONFIG)
    assert [(x.type, x.lieu) for x in v] == [("terme", "doc.md:2")]


@pytest.mark.parametrize("texte", ["ZÈBRE", "Zèbre", "zebre", "mon_zebre_x", "monZebreFoo", "un.zebre.court", "zébrezébre"])
def test_casse_accents_separateurs_et_mots_composes_sont_normalises(texte):
    assert cp.controler_texte(texte, "f", CONFIG)


@pytest.mark.parametrize("texte,attendu", [
    ("cactus", True), ("un cactus!", True), ("Cactus-Joe", True), ("(cactus)", True),
    ("cactuses", False), ("supercactus", False), ("cactus2", False),
])
def test_mot_entier_sans_faux_positif(texte, attendu):
    assert bool(cp.controler_texte(texte, "f", CONFIG)) is attendu


def test_un_terme_de_deux_mots_se_trouve_malgre_les_separateurs():
    assert cp.controler_texte("le Bleu-Nuit tombe", "f", CONFIG)
    assert cp.controler_texte("bleu   nuit", "f", CONFIG)
    assert not cp.controler_texte("bleu jour", "f", CONFIG)


def test_un_texte_sans_terme_passe():
    assert cp.controler_texte("rien à signaler ici\n", "f", CONFIG) == []


def test_la_sortie_ne_reaffiche_jamais_le_terme():
    for v in cp.controler_texte("un zebre et un cactus", "f", CONFIG):
        assert "zebre" not in str(v).lower() and "cactus" not in str(v).lower()


def test_les_motifs_en_clair_sont_appliques():
    config = cp.fabriquer([], motifs=[{"nom": "décision", "expression": r"\bD" + r"-\d{3}\b"}])
    assert _types(cp.controler_texte(f"voir {DECISION}", "f", config)) == {"décision"}


def test_exception_fermee_comptee_au_plus_juste():
    config = cp.fabriquer(["zebre"], exceptions=[{"terme": "zebre", "chemin": "a.txt", "occurrences": 1, "raison": "essai"}])
    assert cp.controler_texte("un zebre", "a.txt", config) == []
    assert cp.controler_texte("un zebre\nun autre zebre", "a.txt", config)      # la deuxième est en trop
    assert cp.controler_texte("un zebre", "b.txt", config)                        # autre fichier : pas d'exception


# --- noms ------------------------------------------------------------------------------------

def test_un_nom_de_fichier_de_tag_ou_de_branche_est_controle():
    assert cp.controler_nom("notes/zebre-notes.md", CONFIG)
    assert cp.controler_nom("v1.0.0-cactus", CONFIG)
    assert cp.controler_nom("notes/notes.md", CONFIG) == []


# --- arbre git, messages ---------------------------------------------------------------------

def test_un_commit_propre_passe(tmp_path):
    depot = _depot(tmp_path, {"a.txt": "propre\n", "notes/b.md": "rien\n"})
    assert cp.controler_arbre(depot, "HEAD", CONFIG, {}) == []


def test_un_terme_dans_un_fichier_un_chemin_ou_un_message_est_bloque(tmp_path):
    depot = _depot(tmp_path, {"a.txt": "un zebre\n", "zebre.txt": "ok\n"}, message="ajoute un cactus")
    types = _types(cp.controler_arbre(depot, "HEAD", CONFIG, {}))
    assert "terme" in types
    lieux = {v.lieu for v in cp.controler_arbre(depot, "HEAD", CONFIG, {})}
    assert "a.txt:1" in lieux and "zebre.txt" in lieux
    assert _types(cp.controler_messages(depot, "HEAD", CONFIG, limite=1)) == {"terme"}     # le message du commit


def test_les_messages_de_l_intervalle_sont_controles(tmp_path):
    depot = _depot(tmp_path, {"a.txt": "ok\n"})
    base = _git(depot, "rev-parse", "HEAD").strip()
    (depot / "a.txt").write_text("ok 2\n")
    _git(depot, "commit", "-q", "-am", "corrige le zebre")
    v = cp.controler_messages(depot, f"{base}..HEAD", CONFIG)
    assert v and v[0].type == "terme" and "zebre" not in str(v[0]).lower()
    assert cp.controler_messages(depot, f"{base}..{base}", CONFIG) == []


def test_le_controle_lit_le_commit_et_non_l_arbre_de_travail(tmp_path):
    depot = _depot(tmp_path, {"a.txt": "un zebre\n"})                    # commit fautif
    (depot / "a.txt").write_text("propre\n")                              # arbre de travail propre
    assert cp.controler_arbre(depot, "HEAD", CONFIG, {})                  # échec : le commit est fautif
    propre = tmp_path / "propre"
    propre.mkdir()
    depot2 = _depot(propre, {"a.txt": "propre\n"})                        # commit propre
    (depot2 / "a.txt").write_text("un zebre\n")                           # arbre de travail fautif, non commité
    assert cp.controler_arbre(depot2, "HEAD", CONFIG, {}) == []           # succès : seul le commit compte


# --- images et binaires ----------------------------------------------------------------------

def test_image_non_approuvee_echoue_approuvee_passe_modifiee_echoue(tmp_path):
    depot = _depot(tmp_path, {"img/logo.png": PNG})
    assert _types(cp.controler_arbre(depot, "HEAD", CONFIG, {})) == {"image"}
    approuvees = {hashlib.sha256(PNG).hexdigest(): "img/logo.png"}
    assert cp.controler_arbre(depot, "HEAD", CONFIG, approuvees) == []
    (depot / "img/logo.png").write_bytes(PNG + b"\x00")
    _git(depot, "commit", "-q", "-am", "retouche")
    assert _types(cp.controler_arbre(depot, "HEAD", CONFIG, approuvees)) == {"image"}


def test_binaire_non_prevu_est_refuse_police_acceptee(tmp_path):
    depot = _depot(tmp_path, {"data/mesure.bin": b"a\x00b", "ui/f.woff2": b"wOF2\x00\x01"})
    v = cp.controler_arbre(depot, "HEAD", CONFIG, {})
    assert [(x.type, x.lieu) for x in v] == [("binaire", "data/mesure.bin")]


# --- ligne de commande -----------------------------------------------------------------------

def _ecrire_config(tmp_path: Path) -> tuple[Path, Path]:
    empreintes = tmp_path / "empreintes.json"
    empreintes.write_text(json.dumps(CONFIG), encoding="utf-8")
    images = tmp_path / "images.json"
    images.write_text(json.dumps({"images": []}), encoding="utf-8")
    return empreintes, images


def test_cli_codes_de_sortie(tmp_path, capsys):
    (tmp_path / "d").mkdir()
    depot = _depot(tmp_path / "d", {"a.txt": "un zebre\n"})
    empreintes, images = _ecrire_config(tmp_path)
    base = ["--depot", str(depot), "--empreintes", str(empreintes), "--images", str(images)]
    assert cp.main(["arbre", "HEAD", *base]) == 1
    sortie = capsys.readouterr().out
    assert "a.txt:1" in sortie and "zebre" not in sortie.lower()


def test_cli_texte_depuis_un_fichier(tmp_path):
    empreintes, images = _ecrire_config(tmp_path)
    propre = tmp_path / "pr.txt"
    propre.write_text("notes de version\n")
    sale = tmp_path / "pr2.txt"
    sale.write_text("pour le cactus\n")
    base = ["--empreintes", str(empreintes), "--images", str(images)]
    assert cp.main(["texte", str(propre), *base]) == 0
    assert cp.main(["texte", str(sale), *base]) == 1


def test_cli_sans_fichier_d_empreintes_n_est_jamais_un_succes(tmp_path):
    assert cp.main(["texte", str(tmp_path / "x.txt"), "--empreintes", str(tmp_path / "absent.json")]) == 2


def test_cli_tout_enchaine_arbre_messages_texte_et_noms(tmp_path):
    empreintes, images = _ecrire_config(tmp_path)
    (tmp_path / "d").mkdir()
    depot = _depot(tmp_path / "d", {"a.txt": "propre\n"})
    base = ["--depot", str(depot), "--empreintes", str(empreintes), "--images", str(images)]
    assert cp.main(["tout", "--commit", "HEAD", *base]) == 0
    assert cp.main(["tout", "--commit", "HEAD", "--nom", "tag-zebre", *base]) == 1
    texte = tmp_path / "pr.txt"
    texte.write_text("le cactus\n")
    assert cp.main(["tout", "--commit", "HEAD", "--texte", str(texte), *base]) == 1


def test_les_references_numerotees_sont_refusees_dans_les_messages_et_les_textes_de_pr(tmp_path):
    config = cp.fabriquer([], motifs_messages=[{"nom": "référence", "expression": r"(?<![\w&])#\d+\b"}])
    assert _types(cp.controler_texte(f"corrige ({NUM}12)", "pr", config, messages=True)) == {"référence"}
    assert cp.controler_texte(f"corrige ({NUM}12)", "doc.md", config) == []          # dans un fichier : autre motif
    depot = _depot(tmp_path, {"a.txt": "ok\n"}, message=f"correctif ({NUM}34)")
    assert _types(cp.controler_messages(depot, "HEAD", config, limite=1)) == {"référence"}


@pytest.mark.parametrize("texte", ["BleuNuit", "bleunuit", "bleu_nuit", "MON-BLEU-NUIT-X", "monBleuNuitSuite"])
def test_un_terme_a_plusieurs_mots_se_trouve_aussi_colle_ou_en_casse_mixte(texte):
    violations = cp.controler_texte(texte, "f", CONFIG)
    assert violations and len(violations) == 1                       # rapporté une seule fois par ligne


def test_cli_tout_controle_les_messages_de_l_intervalle_base_commit(tmp_path):
    empreintes, images = _ecrire_config(tmp_path)
    (tmp_path / "d").mkdir()
    depot = _depot(tmp_path / "d", {"a.txt": "propre\n"})
    base = _git(depot, "rev-parse", "HEAD").strip()
    (depot / "a.txt").write_text("propre 2\n")
    _git(depot, "commit", "-q", "-am", "corrige le zebre")
    tete = _git(depot, "rev-parse", "HEAD").strip()
    options = ["--depot", str(depot), "--empreintes", str(empreintes), "--images", str(images)]
    assert cp.main(["tout", "--commit", tete, "--base", base, *options]) == 1       # le message est fautif
    assert cp.main(["tout", "--commit", base, "--base", base, *options]) == 0


# --- étiquettes annotées : l'auteur (tagger) est lu comme le reste -----------------------------

def _etiquette(depot: Path, nom: str, tagger: str | None, message: str = "Version essai") -> str:
    """Crée une étiquette annotée dont le « tagger » est donné (« Nom <adresse> »), sans toucher la configuration."""
    commit = _git(depot, "rev-parse", "HEAD").strip()
    ligne = f"tagger {tagger} 1700000000 +0000\n" if tagger else ""
    objet = _git_entree(depot, ["mktag"], f"object {commit}\ntype commit\ntag {nom}\n{ligne}\n{message}\n").strip()
    _git(depot, "update-ref", f"refs/tags/{nom}", objet)
    return objet


def _git_entree(depot: Path, args: list[str], entree: str) -> str:
    return subprocess.run(["git", *args], cwd=depot, input=entree, capture_output=True, text=True, check=True).stdout


def test_une_etiquette_a_l_identite_neutre_passe(tmp_path):
    depot = _depot(tmp_path, {"a.txt": "ok\n"})
    objet = _etiquette(depot, "v1", "Projet <noreply@memoglyph.invalid>")
    assert cp.controler_etiquette(depot, objet, CONFIG) == []


# Adresses assemblées à l'exécution : le fichier est exporté, il ne doit contenir aucune adresse en clair.
@pytest.mark.parametrize("tagger", ["Quelqu'un <quelqu.un" + "@fournisseur.net>", "Zebre Dupont <noreply@memoglyph.invalid>",
                                     "Un Nom <perso" + "@fournisseur.fr>"])
def test_une_adresse_personnelle_ou_un_terme_interdit_dans_le_tagger_est_bloque(tmp_path, tagger):
    depot = _depot(tmp_path, {"a.txt": "ok\n"})
    objet = _etiquette(depot, "v1", tagger)
    violations = cp.controler_etiquette(depot, objet, cp.fabriquer(
        ["zebre"], motifs=[{"nom": "adresse", "expression": r"[\w.+-]+@(?!(?:[\w-]+\.)*(?:example\.(?:com|org|net)|memoglyph\.invalid)\b)[\w-]+(?:\.[\w-]+)+"}]))
    assert violations
    assert all("zebre" not in str(v).lower() for v in violations)       # jamais le terme réaffiché


def test_le_message_d_une_etiquette_est_controle_aussi(tmp_path):
    depot = _depot(tmp_path, {"a.txt": "ok\n"})
    objet = _etiquette(depot, "v1", "Projet <noreply@memoglyph.invalid>", message="notes sur le zebre")
    assert cp.controler_etiquette(depot, objet, CONFIG)


def test_une_etiquette_legere_ou_un_commit_n_a_pas_de_tagger(tmp_path):
    depot = _depot(tmp_path, {"a.txt": "ok\n"})
    assert cp.controler_etiquette(depot, _git(depot, "rev-parse", "HEAD").strip(), CONFIG) == []


def test_cli_tout_controle_les_objets_d_etiquette_donnes(tmp_path):
    empreintes, images = _ecrire_config(tmp_path)
    (tmp_path / "d").mkdir()
    depot = _depot(tmp_path / "d", {"a.txt": "propre\n"})
    objet = _etiquette(depot, "v1", "Zebre <noreply@memoglyph.invalid>")
    options = ["--depot", str(depot), "--empreintes", str(empreintes), "--images", str(images)]
    assert cp.main(["tout", "--commit", "HEAD", "--etiquette", objet, *options]) == 1
    propre = _etiquette(depot, "v2", "Projet <noreply@memoglyph.invalid>")
    assert cp.main(["tout", "--commit", "HEAD", "--etiquette", propre, *options]) == 0
