"""Les README sont générés : `readme/readme.yaml` + une rubrique par langue.

Deux familles de tests.
- Sur de petits dépôts fabriqués (`tmp_path`) : le générateur et chacune de ses règles, avec le cas qui
  DOIT échouer à côté du cas qui passe. Un contrôle qu'on n'a jamais vu échouer ne prouve rien.
- Sur les vrais README : à jour, rubriques appariées, images approuvées, liens et ancres résolus DANS L'ARBRE
  EXPORTÉ (ce que le public clone), version du CHANGELOG, et, quand le gabarit porte `regles_strictes: true`,
  ton, coupe, longueur et attribution.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
import yaml

from tests.unit.test_noms_exportes import exportes
from tools import readme

pytestmark = pytest.mark.unit

RACINE = Path(__file__).resolve().parents[2]
MANIFESTE = RACINE / "tools" / "images_approuvees.json"
COMMENTAIRE = {"en": "<!-- Generated file: edit readme/, then run make readme -->",
               "fr": "<!-- Fichier généré : modifier readme/, puis make readme -->"}


# --------------------------------------------------------------------------- petits dépôts fabriqués

def depot(tmp_path: Path, rubriques: dict[str, tuple[str, str]] | None = None, *, strictes: bool = False,
          captures: dict | None = None, version: str = "v1.0.0") -> Path:
    """Un dépôt minimal : `rubriques` = {id: (texte anglais, texte français)}. Les fichiers se nomment par l'id."""
    rubriques = rubriques if rubriques is not None else {
        "un": ("<p align=\"center\">Logo</p>\n\n# Name\n\nOne.\n", "<p align=\"center\">Logo</p>\n\n# Nom\n\nUn.\n"),
        "deux": ("## Second\n\nTwo.\n", "## Deuxième\n\nDeux.\n"),
    }
    for lg in ("en", "fr"):
        (tmp_path / "readme" / lg).mkdir(parents=True, exist_ok=True)
    liste = []
    for i, (rid, (en, fr)) in enumerate(rubriques.items(), 1):
        liste.append({"id": rid, "fichier": {"en": f"{i:02d}-{rid}.md", "fr": f"{i:02d}-{rid}.md"}})
        (tmp_path / "readme" / "en" / f"{i:02d}-{rid}.md").write_text(en, encoding="utf-8")
        (tmp_path / "readme" / "fr" / f"{i:02d}-{rid}.md").write_text(fr, encoding="utf-8")
    gabarit = {"version": version, "regles_strictes": strictes, "langues": ["en", "fr"], "rubriques": liste,
               "captures": captures or {}, "liens": {}, "attribution": {}}
    (tmp_path / "readme" / "readme.yaml").write_text(yaml.safe_dump(gabarit, allow_unicode=True), encoding="utf-8")
    return tmp_path


def instantane(racine: Path) -> dict[str, bytes]:
    return {str(p.relative_to(racine)): p.read_bytes() for p in racine.rglob("*") if p.is_file()}


# --------------------------------------------------------------------------- le générateur

def test_les_deux_readme_sont_produits_avec_l_entete_de_fichier_genere_avant_le_logo(tmp_path) -> None:
    racine = depot(tmp_path)

    sorties = readme.tout_assembler(racine)

    assert set(sorties) == {"README.md", "README.fr.md"}
    for nom, lg in (("README.md", "en"), ("README.fr.md", "fr")):
        premiere, _, suite = sorties[nom].partition("\n")
        assert premiere == COMMENTAIRE[lg]
        assert suite.startswith("<p align=\"center\">"), "l'en-tête précède le logo"
        assert sorties[nom].endswith(".\n") and not sorties[nom].endswith("\n\n")


def test_la_sortie_est_deterministe_a_l_octet_pres(tmp_path) -> None:
    racine = depot(tmp_path)

    premiere = readme.tout_assembler(racine)
    readme.generer(racine)
    ecrit = {n: (racine / n).read_bytes() for n in premiere}
    readme.generer(racine)

    assert ecrit == {n: t.encode() for n, t in premiere.items()}
    assert {n: (racine / n).read_bytes() for n in premiere} == ecrit
    assert b"\r" not in b"".join(ecrit.values())


def test_generer_n_ecrit_que_les_deux_readme_et_ne_supprime_rien(tmp_path) -> None:
    racine = depot(tmp_path)
    (racine / "autre.md").write_text("intact\n", encoding="utf-8")
    avant = instantane(racine)

    readme.generer(racine)

    apres = instantane(racine)
    assert set(apres) - set(avant) == {"README.md", "README.fr.md"}
    assert all(apres[c] == avant[c] for c in avant), "aucun fichier existant modifié ni supprimé"


def test_une_modification_d_une_rubrique_ne_change_que_le_readme_de_sa_langue(tmp_path) -> None:
    racine = depot(tmp_path)
    readme.generer(racine)
    avant = {n: (racine / n).read_bytes() for n in ("README.md", "README.fr.md")}

    (racine / "readme" / "fr" / "02-deux.md").write_text("## Deuxième\n\nDeux, modifiée.\n", encoding="utf-8")
    readme.generer(racine)

    assert (racine / "README.md").read_bytes() == avant["README.md"]
    assert (racine / "README.fr.md").read_bytes() != avant["README.fr.md"]


def test_les_rubriques_sont_assemblees_dans_l_ordre_du_gabarit_et_les_captures_et_liens_resolus(tmp_path) -> None:
    captures = {"vue": {"corpus_ami": False, "chemin": {"en": "a/en.png", "fr": "a/fr.png"},
                        "alt": {"en": "Alt", "fr": "Texte"}, "legende": {"en": "Caption", "fr": "Légende"}}}
    racine = depot(tmp_path, {"un": ("# A\n\n{{capture:vue}}\n", "# A\n\n{{capture:vue}}\n"),
                              "deux": ("## B\n\nText.\n", "## B\n\nTexte.\n")}, captures=captures)

    sortie = readme.tout_assembler(racine)

    assert "![Alt](a/en.png)\n*Caption*" in sortie["README.md"]
    assert "![Texte](a/fr.png)\n*Légende*" in sortie["README.fr.md"]
    assert sortie["README.md"].index("# A") < sortie["README.md"].index("## B")


def test_la_version_du_gabarit_s_affiche_et_l_attribution_se_place_sous_la_capture_du_corpus(tmp_path) -> None:
    captures = {"vue": {"corpus_ami": True, "chemin": {"en": "a/en.png", "fr": "a/fr.png"},
                        "alt": {"en": "Alt", "fr": "Texte"}, "legende": {"en": "Caption", "fr": "Légende"}}}
    racine = depot(tmp_path, {"un": ("# A\n\n{{capture:vue}}\n\n{{attribution}}\n\nAfter.\n", "# A\n\n{{capture:vue}}\n\n{{attribution}}\n\nAprès.\n"),
                              "deux": ("## B\n\nVersion {{version}}.\n", "## B\n\nVersion {{version}}.\n")},
                  captures=captures, version="v7.8.9")
    brut = yaml.safe_load((racine / "readme" / "readme.yaml").read_text(encoding="utf-8"))
    brut["attribution"] = {"en": "<sub>AMI credit.</sub>", "fr": "<sub>Crédit AMI.</sub>"}
    (racine / "readme" / "readme.yaml").write_text(yaml.safe_dump(brut, allow_unicode=True), encoding="utf-8")

    sortie = readme.tout_assembler(racine)

    assert "Version v7.8.9." in sortie["README.md"] and "Version v7.8.9." in sortie["README.fr.md"]
    en = sortie["README.md"]
    assert en.index("*Caption*") < en.index("<sub>AMI credit.</sub>") < en.index("After."), "l'attribution suit la capture"
    assert en.count("AMI credit.") == 1 and sortie["README.fr.md"].count("Crédit AMI.") == 1, "une seule fois, pas en fin de fichier en plus"


def test_une_capture_du_corpus_sans_attribution_est_refusee(tmp_path) -> None:
    captures = {"vue": {"corpus_ami": True, "chemin": {"en": "a/en.png", "fr": "a/fr.png"},
                        "alt": {"en": "Alt", "fr": "Texte"}, "legende": {"en": "Caption", "fr": "Légende"}}}
    racine = depot(tmp_path, {"un": ("# A\n\n{{capture:vue}}\n", "# A\n\n{{capture:vue}}\n")}, captures=captures)
    with pytest.raises(readme.ErreurGabarit, match="attribution"):
        readme.tout_assembler(racine)
    # Un emplacement d'attribution sans texte d'attribution dans le gabarit est refusé aussi.
    sans_texte = depot(tmp_path / "b", {"un": ("# A\n\n{{attribution}}\n", "# A\n\n{{attribution}}\n")})
    with pytest.raises(readme.ErreurGabarit, match="attribution"):
        readme.tout_assembler(sans_texte)


@pytest.mark.parametrize("sabotage, attendu", [
    ("rubrique_fr_absente", "manquant"),
    ("rubrique_orpheline", "orphelin"),
    ("rubrique_vide", "vide"),
    ("capture_inconnue", "inconnu du gabarit"),
    ("identifiant_double", "en double"),
])
def test_un_gabarit_ou_une_rubrique_invalide_est_refuse_en_listant_les_erreurs(tmp_path, sabotage, attendu) -> None:
    racine = depot(tmp_path)
    if sabotage == "rubrique_fr_absente":
        (racine / "readme" / "fr" / "02-deux.md").unlink()
    elif sabotage == "rubrique_orpheline":
        (racine / "readme" / "en" / "99-reste.md").write_text("## Reste\n\nTexte.\n", encoding="utf-8")
    elif sabotage == "rubrique_vide":
        (racine / "readme" / "en" / "02-deux.md").write_text("\n  \n", encoding="utf-8")
    elif sabotage == "capture_inconnue":
        (racine / "readme" / "en" / "02-deux.md").write_text("## B\n\n{{capture:nulle-part}}\n", encoding="utf-8")
    else:
        brut = yaml.safe_load((racine / "readme" / "readme.yaml").read_text(encoding="utf-8"))
        brut["rubriques"][1]["id"] = brut["rubriques"][0]["id"]
        (racine / "readme" / "readme.yaml").write_text(yaml.safe_dump(brut), encoding="utf-8")

    with pytest.raises(readme.ErreurGabarit) as e:
        readme.tout_assembler(racine)

    assert attendu in str(e.value) and e.value.erreurs, "les erreurs sont listées, la génération refusée"
    assert not (racine / "README.md").exists()


def test_la_fraicheur_se_verifie_sans_rien_ecrire(tmp_path) -> None:
    racine = depot(tmp_path)
    assert readme.verifier(racine) == ["README.md : absent", "README.fr.md : absent"], "ancre : l'écart est vu"
    readme.generer(racine)
    assert readme.verifier(racine) == [], "à jour : aucun écart"

    (racine / "README.fr.md").write_text("modifié à la main\n", encoding="utf-8")
    avant = instantane(racine)
    ecarts = readme.verifier(racine)

    assert len(ecarts) == 1 and ecarts[0].startswith("README.fr.md : diffère à la ligne 1")
    assert instantane(racine) == avant, "verifier n'écrit rien"


def test_codes_de_sortie_de_la_ligne_de_commande(tmp_path) -> None:
    racine = depot(tmp_path)
    assert readme.main(["generer", "--racine", str(racine)]) == 0
    assert readme.main(["verifier", "--racine", str(racine)]) == 0
    (racine / "README.md").write_text("autre\n", encoding="utf-8")
    assert readme.main(["verifier", "--racine", str(racine)]) == 1
    (racine / "readme" / "fr" / "02-deux.md").unlink()
    assert readme.main(["verifier", "--racine", str(racine)]) == 3
    assert readme.main(["inconnue"]) == 2


# --------------------------------------------------------------------------- les règles d'écriture (fixtures)

def test_la_regle_de_coupe_refuse_un_paragraphe_sur_plusieurs_lignes_mais_pas_le_code_ni_les_tableaux() -> None:
    coupe = "Une phrase\nsur deux lignes.\n"
    tolere = ("Une seule ligne.\n\n```\ncode\nsur deux lignes\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n"
              "- puce\n  suite\n\n<p align=\"center\">\n  <img src=\"x.png\">\n</p>\n\n## Titre\n")

    assert len(readme.paragraphes_coupes(coupe)) == 1
    assert readme.paragraphes_coupes(tolere) == []
    assert len(readme.paragraphes_coupes("Une.\n\n" + coupe)) == 1, "ancre : le contrôle regarde plus loin que le début"


def test_le_gabarit_strict_refuse_une_rubrique_coupee(tmp_path) -> None:
    racine = depot(tmp_path, {"un": ("# A\n\nUne ligne\ncoupée.\n", "# A\n\nUne ligne.\n")}, strictes=True)
    with pytest.raises(readme.ErreurGabarit, match="coupé"):
        readme.tout_assembler(racine)
    souple = depot(tmp_path / "souple", {"un": ("# A\n\nUne ligne\ncoupée.\n", "# A\n\nUne ligne.\n")})
    assert readme.tout_assembler(souple), "hors mode strict (portage fidèle), la coupe n'est pas imposée"


@pytest.mark.parametrize("langue, texte, motif", [
    ("fr", "Vous pouvez tester l'outil.", "adresse"),
    ("fr", "On enregistre la réunion.", "adresse"),
    ("fr", "Ouvrez la bibliothèque.", "impératif"),
    ("fr", "Le meilleur outil du monde.", "superlatif"),
    ("en", "You can try the tool.", "adresse"),
    ("en", "We record the meeting.", "adresse"),
    ("en", "Open the library.", "impératif"),
    ("en", "The best tool in the world.", "superlatif"),
    ("fr", " ".join(["mot"] * 40) + ".", "phrase de plus de"),
])
def test_le_plancher_de_ton_refuse_l_adresse_l_imperatif_le_superlatif_et_la_phrase_trop_longue(
        langue, texte, motif) -> None:
    fautes = readme.fautes_d_ecriture(texte, langue)
    assert fautes and motif in fautes[0], fautes


@pytest.mark.parametrize("langue, texte", [
    ("fr", "Memoglyph transcrit les réunions en local. Les citations renvoient à la seconde exacte."),
    ("en", "Memoglyph runs on the Mac. The reports cite the second where each sentence was said."),
    ("fr", "`vous` en code reste permis.\n\n```\nOuvrez le fichier\n```\n\n| Vous | On |\n|---|---|\n"),
    ("fr", "## Vous et on dans un titre\n\n<p>On dans du HTML</p>\n\nLe texte reste factuel.\n\n*Légende : vous pouvez voir l'écran.*\n"),
    ("en", "The guide is [Open source](pages/open.md) and the text stays factual."),
])
def test_le_plancher_de_ton_laisse_passer_la_prose_correcte_et_ce_qu_il_n_inspecte_pas(langue, texte) -> None:
    assert readme.fautes_d_ecriture(texte, langue) == []
    assert readme.phrases_de_prose(texte) or "```" in texte or "|" in texte, "ancre : il y a bien de quoi lire"


def test_un_imperatif_de_tete_est_vu_meme_dans_une_liste_ou_apres_un_point() -> None:
    assert readme.fautes_d_ecriture("Une phrase correcte. Lancez la commande.", "fr")
    assert readme.fautes_d_ecriture("- Install the tool\n- Run it", "en")
    assert readme.fautes_d_ecriture("- L'outil est installé\n- Le service démarre", "fr") == []


@pytest.mark.parametrize("langue, texte", [
    ("en", "Apache-2.0: see LICENSE."),
    ("en", "The code is open; read the notice."),
    ("fr", "Apache-2.0 : voir LICENSE."),
    ("fr", "Le code est ouvert ; lisez la notice."),
])
def test_un_imperatif_apres_deux_points_ou_point_virgule_est_vu(langue, texte) -> None:
    fautes = readme.fautes_d_ecriture(texte, langue)
    assert len(fautes) == 1 and "impératif" in fautes[0], fautes
    assert readme.fautes_d_ecriture("Apache-2.0: the license and the notice.", "en") == [], "ancre : un deux-points ordinaire passe"


@pytest.mark.parametrize("langue, titre, fautif", [
    ("en", "## Start in five commands", True), ("en", "## Learn more", True), ("en", "## Develop and license", True),
    ("en", "## Five commands to start", False), ("en", "## Further reading", False), ("en", "## Development and license", False),
    ("fr", "## Découvrez le produit", True), ("fr", "## Démarrer en cinq commandes", False), ("fr", "## Aller plus loin", False),
    ("en", "### 1. Transcribe", False),
])
def test_un_titre_de_section_n_est_pas_un_imperatif(langue, titre, fautif) -> None:
    assert bool(readme.fautes_de_titre(titre + "\n\nTexte.\n", langue)) is fautif


def test_on_n_est_interdit_qu_en_francais() -> None:
    assert readme.fautes_d_ecriture("Everything runs on the Mac.", "en") == []
    assert readme.fautes_d_ecriture("Tout tourne sur le Mac, on le configure.", "fr")


def test_les_ancres_sont_calculees_comme_github() -> None:
    assert readme.ancre_github("Démarrer en cinq commandes") == "démarrer-en-cinq-commandes"
    assert readme.ancre_github("Portée projet — et la contrepartie") == "portée-projet--et-la-contrepartie"
    assert readme.ancre_github("`make check` : « tout »") == "make-check---tout-"
    assert readme.ancre_github("Recording a call (beta)") == "recording-a-call-beta"
    assert readme.ancre_github("Local first, cloud only if you choose it") == "local-first-cloud-only-if-you-choose-it"
    doc = "# Titre\n\n## Suite\n\n## Suite\n\n```\n## Pas un titre\n```\n\n## Suite\n"
    assert readme.ancres_du_texte(doc) == {"titre", "suite", "suite-1", "suite-2"}


def test_les_liens_casses_sont_vus_avec_chemin_et_ancre() -> None:
    arbre = {"README.md", "pages/guide.md", "docs/assets/a.png"}
    docs = {"pages/guide.md": "# Guide\n\n## Étape un\n"}
    texte = ("[ok](pages/guide.md#étape-un) [ok2](pages/guide.md) ![i](docs/assets/a.png) [web](https://x.test/y) "
             "[haut](#début) [dossier](docs/) <img src=\"docs/assets/a.png\">\n\n# Début\n")
    assert readme.liens_casses(texte, "README.md", arbre, docs.__getitem__) == []

    mauvais = readme.liens_casses("[a](pages/absent.md) [b](pages/guide.md#nulle) ![c](docs/assets/b.png) [d](#rien)",
                                  "README.md", arbre, docs.__getitem__)
    assert len(mauvais) == 4, mauvais
    assert len(readme.cibles_des_liens(texte)) >= 6, "ancre : toutes les formes de lien sont relevées"

    logo = '<picture><source media="(prefers-color-scheme: dark)" srcset="docs/assets/sombre.svg, docs/assets/x2.svg 2x">'
    assert len(readme.liens_casses(logo, "README.md", arbre, docs.__getitem__)) == 2, "le srcset est lu aussi"


def test_l_attribution_du_corpus_est_exigee_tant_qu_une_capture_est_affichee() -> None:
    avec = "![x](docs/assets/screenshots/library.png)\n"
    assert readme.attribution_manquante(avec) == list(readme.ATTRIBUTION), "ancre : rien ne l'écarte"
    complet = avec + "AMI Meeting Corpus, CC BY 4.0, Carletta 2007."
    assert readme.attribution_manquante(complet) == []
    assert readme.attribution_manquante("Aucune capture ici.") == []


def test_la_longueur_se_mesure_en_mots_a_dix_pour_cent_pres() -> None:
    assert readme.ecart_de_longueur(1000, 1100) == pytest.approx(0.10)
    assert readme.ecart_de_longueur(1000, 1101) > readme.ECART_MAX_MOTS
    assert readme.ecart_de_longueur(1200, 1000) > readme.ECART_MAX_MOTS
    assert readme.mesure("a b\nc\n") == (2, 3)


# --------------------------------------------------------------------------- les vrais README

@pytest.fixture(scope="module")
def gabarit() -> readme.Gabarit:
    return readme.charger(RACINE)


@pytest.fixture(scope="module")
def arbre() -> set[str]:
    fichiers = set(exportes())
    assert "core/config.py" in fichiers and len(fichiers) > 200, "ancre : l'arbre exporté est le vrai"
    return fichiers


@pytest.fixture(scope="module", params=["README.md", "README.fr.md"])
def publie(request) -> tuple[str, str, str]:
    """(nom, langue, texte) des README versionnés."""
    langue = "en" if request.param == "README.md" else "fr"
    return request.param, langue, (RACINE / request.param).read_text(encoding="utf-8")


def test_chaque_rubrique_existe_dans_les_deux_langues_et_aucune_n_est_vide(gabarit) -> None:
    assert len(gabarit.rubriques) >= 1 and gabarit.langues == ["en", "fr"]
    for lg in gabarit.langues:
        textes = readme.textes_des_rubriques(gabarit, lg, RACINE)
        assert len(textes) == len(gabarit.rubriques)
        assert all(t.strip() for _r, t in textes)
    dossiers = {lg: sorted(p.name for p in (RACINE / "readme" / lg).iterdir()) for lg in gabarit.langues}
    assert len(dossiers["en"]) == len(dossiers["fr"]) == len(gabarit.rubriques), "ni orphelin ni rubrique seule"


def test_les_readme_publies_sont_a_jour_de_leurs_rubriques() -> None:
    assert readme.verifier(RACINE) == [], "lancer `make readme` puis recommiter"
    assert len(readme.tout_assembler(RACINE)) == 2, "ancre : deux README sont comparés"


def test_les_readme_portent_la_mention_de_fichier_genere_et_le_selecteur_de_langue(publie) -> None:
    nom, langue, texte = publie
    assert texte.splitlines()[0] == COMMENTAIRE[langue]
    haut = "\n".join(texte.splitlines()[:25])
    ligne = next((x for x in haut.splitlines() if "English" in x and "Français" in x), "")
    assert ligne, f"{nom} : sélecteur « English · Français » absent du haut du fichier"
    autre = "README.fr.md" if langue == "en" else "README.md"
    assert f"({autre})" in ligne, "le sélecteur renvoie vers l'autre langue"
    assert haut.index("</picture>") < haut.index(ligne), "le sélecteur est sous le logo"


def test_chaque_image_existe_dans_l_arbre_exporte_et_les_images_sont_approuvees(publie, arbre) -> None:
    nom, _langue, texte = publie
    images = readme.images_citees(texte)
    assert len(images) >= 3, f"ancre : {nom} cite des images"
    approuvees = {(i["chemin"], i["sha256"]) for i in json.loads(MANIFESTE.read_text(encoding="utf-8"))["images"]}
    assert len(approuvees) >= 6, "ancre : le manifeste est lu"
    for cible in images:
        assert cible in arbre, f"{nom} : {cible} n'est pas dans l'arbre exporté"
        if Path(cible).suffix.lower() in readme.IMAGES:
            empreinte = hashlib.sha256((RACINE / cible).read_bytes()).hexdigest()
            assert (cible, empreinte) in approuvees, f"{nom} : {cible} n'est pas approuvée par le manifeste"


def test_liens_relatifs_et_ancres_resolvent_dans_l_arbre_exporte(publie, arbre) -> None:
    nom, _langue, texte = publie
    assert len(readme.cibles_des_liens(texte)) >= 10, f"ancre : {nom} cite des liens"
    casses = readme.liens_casses(texte, nom, arbre, lambda c: (RACINE / c).read_text(encoding="utf-8"))
    assert casses == [], casses


def test_la_version_du_gabarit_est_celle_de_la_premiere_entree_du_changelog(gabarit) -> None:
    changelog = (RACINE / "CHANGELOG.md").read_text(encoding="utf-8")
    premiere = re.search(r"^## (v\d+\.\d+\.\d+)", changelog, re.MULTILINE)
    assert premiere, "ancre : le CHANGELOG a une entrée de version"
    assert gabarit.version == premiere.group(1)


def test_attribution_du_corpus_dans_chaque_readme_qui_affiche_une_capture(publie) -> None:
    nom, _langue, texte = publie
    assert readme.DOSSIER_CAPTURES in texte, f"ancre : {nom} affiche des captures"
    assert readme.attribution_manquante(texte) == [], f"{nom} : attribution du corpus incomplète"


CINQ_COMMANDES = ["brew install uv ffmpeg", "uv sync --locked", "npm --prefix ui ci", "cp .env.example .env", "./start.sh"]


def test_les_cinq_commandes_et_l_adresse_sont_avant_la_ligne_80(publie) -> None:
    nom, _langue, texte = publie
    lignes = texte.splitlines()
    debut = next((i for i, x in enumerate(lignes) if x.strip() == "```bash"), None)
    assert debut is not None, f"ancre : {nom} contient un bloc de commandes"
    fin = lignes.index("```", debut + 1)
    assert [x.strip() for x in lignes[debut + 1:fin]] == CINQ_COMMANDES, f"{nom} : les cinq commandes, dans l'ordre"
    assert fin < 80, f"{nom} : les cinq commandes finissent à la ligne {fin + 1}"
    assert any("http://127.0.0.1:8770" in x for x in lignes[:80]), f"{nom} : l'adresse manque avant la ligne 80"


def test_les_titres_de_section_des_readme_ne_sont_pas_des_imperatifs(publie) -> None:
    nom, langue, texte = publie
    assert len(re.findall(r"^## ", texte, flags=re.MULTILINE)) >= 7, f"ancre : {nom} a ses titres de section"
    assert readme.fautes_de_titre(texte, langue) == []


def test_les_commandes_disent_ou_elles_se_lancent_et_nomment_homebrew(publie) -> None:
    nom, langue, texte = publie
    assert "Homebrew" in texte, f"{nom} : Homebrew n'est pas nommé"
    assert ("run from a clone of the repository" if langue == "en" else "se lancent depuis un clone du dépôt") in texte


def test_la_promesse_d_absence_de_telemetrie_reste_dite_dans_chaque_readme(publie) -> None:
    """`tests/unit/test_aucune_telemetrie.py` garde cette promesse vraie ; elle doit rester écrite."""
    _nom, langue, texte = publie
    assert ("no telemetry" if langue == "en" else "aucune télémétrie") in texte.lower()


def test_aucun_emplacement_du_gabarit_ne_reste_dans_un_readme(publie) -> None:
    nom, _langue, texte = publie
    assert "{{" not in texte and "}}" not in texte, f"{nom} : un emplacement {{{{…}}}} n'a pas été remplacé"
    assert len(texte.splitlines()) > 20, "ancre : le README a un contenu"


# Règles d'écriture : actives sur les vrais README quand le gabarit les déclare.

def test_regles_d_ecriture_des_readme_quand_elles_sont_declarees(gabarit, publie) -> None:
    nom, langue, texte = publie
    if not gabarit.regles_strictes:
        pytest.skip("portage fidèle : les règles d'écriture s'appliquent aux rubriques réécrites")
    assert readme.phrases_de_prose(texte), f"ancre : {nom} contient de la prose"
    assert readme.fautes_d_ecriture(texte, langue) == []
    assert readme.paragraphes_coupes("\n\n".join(t for _r, t in readme.textes_des_rubriques(gabarit, langue, RACINE))) == []


def test_chaque_rubrique_reecrite_commence_par_un_titre_sauf_l_ouverture(gabarit) -> None:
    if not gabarit.regles_strictes:
        pytest.skip("portage fidèle : une seule rubrique, l'ouverture")
    for lg in gabarit.langues:
        textes = readme.textes_des_rubriques(gabarit, lg, RACINE)
        assert len(textes) >= 8
        assert [r for r, t in textes[1:] if readme.titre_d_ouverture_manquant(t)] == []


def test_longueur_des_readme_quand_les_regles_sont_declarees(gabarit) -> None:
    if not gabarit.regles_strictes:
        pytest.skip("portage fidèle : la longueur actuelle (204 et 492 lignes) est celle de l'ancien README")
    mesures = {lg: readme.mesure((RACINE / readme.SORTIES[lg]).read_text(encoding="utf-8")) for lg in readme.SORTIES}
    for lg, (lignes, _mots) in mesures.items():
        assert readme.LIGNES_MIN <= lignes <= readme.LIGNES_MAX, (lg, lignes)
    assert readme.ecart_de_longueur(mesures["en"][1], mesures["fr"][1]) <= readme.ECART_MAX_MOTS, mesures
