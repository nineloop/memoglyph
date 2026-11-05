"""Les décisions HUMAINES sur les voix doivent survivre à toute reconstruction.

L'index vocal est déclaré dérivé et reconstructible — mais reconstruire perdait
les noms et les fusions, qui n'existaient que dans la base. Ces tests verrouillent
la propriété inverse : vider l'index ne coûte aucune décision humaine.
"""

from __future__ import annotations

import json

import pytest

from core import docid, voice_registry as reg


@pytest.fixture
def racine(tmp_path):
    """Un registre isolé — jamais celui du corpus réel."""
    return tmp_path


def test_un_registre_absent_est_un_etat_normal(racine) -> None:
    """« Absent » n'est pas « illisible » : lire doit rendre un registre vide."""
    etat = reg.lire(racine)
    assert etat["schema_version"] == reg.SCHEMA_VERSION
    assert etat["voix"] == {} and etat["fusions"] == []


def test_on_n_ecrit_jamais_par_dessus_une_lecture_ratee(racine) -> None:
    """Écraser un registre illisible effacerait des décisions humaines — la
    seule donnée du projet qui ne soit PAS reconstructible."""
    (racine / ".voices.json").write_text("{ceci n'est pas du JSON", encoding="utf-8")
    avant = (racine / ".voices.json").read_text(encoding="utf-8")

    # `nouvelle_voix` n'écrit plus : c'est `nommer` qui déclenche l'écriture.
    with pytest.raises(reg.RegistreIllisible):
        reg.nommer(reg.nouvelle_voix(racine), "Ari", root=racine)

    assert (racine / ".voices.json").read_text(encoding="utf-8") == avant


def test_l_identite_d_une_voix_est_stable_et_hors_base(racine) -> None:
    """C'était un INTEGER AUTOINCREMENT, donc né en base : après reconstruction
    « voix 3 » ne désignait plus la même personne."""
    uid = reg.nouvelle_voix(racine)
    assert docid.est_valide(uid), f"identité non stable : {uid!r}"
    # L'entrée n'est persistée qu'au premier contenu réel — voir
    # `test_forger_une_identite_n_ecrit_rien` pour la raison.
    reg.enregistrer_membres(uid, [("DOC1", "Speaker 1")], root=racine)
    assert uid in json.loads((racine / ".voices.json").read_text(encoding="utf-8"))["voix"]


def test_un_nom_manuel_survit_a_la_reconstruction(racine) -> None:
    """Le cas qui motive tout ce module."""
    uid = reg.nouvelle_voix(racine)
    reg.nommer(uid, "Ari", root=racine)

    # Reconstruction : la base dérivée disparaît, le fichier autoritatif reste.
    assert reg.etiquette(uid, racine) == ("Ari", "manual")


def test_une_deduction_LLM_n_ecrase_jamais_un_nom_humain(racine) -> None:
    """Invariant du projet : une inférence LLM n'est jamais autoritative."""
    uid = reg.nouvelle_voix(racine)
    reg.nommer(uid, "Ari", source="manual", root=racine)
    reg.nommer(uid, "Intervieweur", source="auto", root=racine)

    assert reg.etiquette(uid, racine) == ("Ari", "manual")


def test_un_nom_auto_est_bien_enregistre_quand_rien_ne_le_precede(racine) -> None:
    """Le garde-fou ne doit pas empêcher le cas légitime."""
    uid = reg.nouvelle_voix(racine)
    reg.nommer(uid, "Intervieweur", source="auto", root=racine)
    assert reg.etiquette(uid, racine) == ("Intervieweur", "auto")


def test_une_provenance_inconnue_est_refusee(racine) -> None:
    uid = reg.nouvelle_voix(racine)
    with pytest.raises(ValueError, match="Provenance"):
        reg.nommer(uid, "X", source="devine", root=racine)


def test_une_fusion_est_journalisee_pas_seulement_appliquee(racine) -> None:
    """Seule la conséquence était appliquée en base ; la décision n'existait
    nulle part. Après reconstruction, deux voix déclarées identiques
    redevenaient distinctes, sans trace."""
    a, b = reg.nouvelle_voix(racine), reg.nouvelle_voix(racine)
    reg.nommer(b, "Ari", root=racine)
    reg.fusionner(a, b, root=racine)

    assert reg.cible_effective(a, racine) == b
    assert reg.etiquette(a, racine) == ("Ari", "manual"), (
        "interroger la voix absorbée doit rendre le nom de la survivante"
    )
    assert len(reg.lire(racine)["fusions"]) == 1


def test_une_chaine_de_fusions_est_suivie_jusqu_au_bout(racine) -> None:
    """A dans B, puis B dans C : demander A doit rendre C."""
    a, b, c = (reg.nouvelle_voix(racine) for _ in range(3))
    reg.nommer(c, "Ari", root=racine)
    reg.fusionner(a, b, root=racine)
    reg.fusionner(b, c, root=racine)

    assert reg.cible_effective(a, racine) == c
    assert reg.etiquette(a, racine) == ("Ari", "manual")


def test_une_fusion_circulaire_ne_boucle_pas(racine) -> None:
    """Pas théorique : une fusion en aller-retour ferait tourner la résolution
    indéfiniment."""
    a, b = reg.nouvelle_voix(racine), reg.nouvelle_voix(racine)
    reg.fusionner(a, b, root=racine)
    reg.fusionner(b, a, root=racine)

    assert reg.cible_effective(a, racine) in (a, b)  # s'arrête, quoi qu'il arrive


def test_une_voix_ne_se_fusionne_pas_avec_elle_meme(racine) -> None:
    a = reg.nouvelle_voix(racine)
    with pytest.raises(ValueError):
        reg.fusionner(a, a, root=racine)


# --- Espace vectoriel : deux extracteurs ne se mélangent jamais -------------

def _spec(modele="pyannote/embedding", dim=256, revision="v3.1"):
    return reg.specification_extracteur(
        modele=modele, dimension=dim, revision=revision,
    )


def test_un_registre_neuf_accepte_n_importe_quel_extracteur(racine) -> None:
    """Rien à contredire : il n'y a pas encore de vecteurs."""
    reg.verifier_extracteur(_spec(), racine)  # ne doit pas lever


def test_le_meme_extracteur_est_accepte(racine) -> None:
    reg.declarer_extracteur(_spec(), racine)
    reg.verifier_extracteur(_spec(), racine)


@pytest.mark.parametrize("changement", [
    {"modele": "Alibaba-NLP/CAM++"},
    {"dim": 512},
    {"revision": "v4.0"},
])
def test_un_extracteur_different_est_REFUSE(racine, changement) -> None:
    """Deux extracteurs produisent des vecteurs incomparables : les mélanger
    donnerait des distances dépourvues de sens, sans que rien ne le signale."""
    reg.declarer_extracteur(_spec(), racine)
    with pytest.raises(reg.ExtracteurIncompatible, match="reconstruction"):
        reg.verifier_extracteur(_spec(**changement), racine)


def test_le_refus_nomme_ce_qui_a_change(racine) -> None:
    """Un message qui dit seulement « incompatible » n'aide personne."""
    reg.declarer_extracteur(_spec(), racine)
    with pytest.raises(reg.ExtracteurIncompatible) as erreur:
        reg.verifier_extracteur(_spec(dim=512), racine)
    assert "dimension" in str(erreur.value) and "512" in str(erreur.value)


def test_changer_d_extracteur_CONSERVE_les_noms_et_les_fusions(racine) -> None:
    """Le point central du module : changer d'extracteur invalide les VECTEURS,
    jamais les décisions humaines."""
    a, b = reg.nouvelle_voix(racine), reg.nouvelle_voix(racine)
    reg.nommer(b, "Ari", root=racine)
    reg.fusionner(a, b, root=racine)
    reg.declarer_extracteur(_spec(), racine)

    reg.declarer_extracteur(_spec(modele="Alibaba-NLP/CAM++", dim=512), racine)

    assert reg.etiquette(a, racine) == ("Ari", "manual")
    assert reg.cible_effective(a, racine) == b
    assert reg.extracteur_enregistre(racine)["dimension"] == 512


def test_l_etat_resume_ce_qui_compte(racine) -> None:
    a, b = reg.nouvelle_voix(racine), reg.nouvelle_voix(racine)
    reg.nommer(a, "Ari", root=racine)
    reg.nommer(b, "Intervieweur", source="auto", root=racine)
    reg.declarer_extracteur(_spec(), racine)

    etat = reg.etat(racine)
    assert etat["voix"] == 2
    assert etat["voix_nommees"] == 2
    assert etat["nommees_manuellement"] == 1
    assert etat["extracteur"]["modele"] == "pyannote/embedding"


# --- Retrouver une voix après reconstruction --------------------------------

def test_les_membres_permettent_de_retrouver_une_voix(racine) -> None:
    """Le `voice_id` entier disparaît avec la base et le vecteur change avec
    l'extracteur — mais « Speaker 2 du document X » reste lui-même. C'est la
    seule clé qui survive."""
    uid = reg.nouvelle_voix(racine)
    reg.nommer(uid, "Ari", root=racine)
    reg.enregistrer_membres(uid, [("DOC1", "Speaker 1"), ("DOC2", "Speaker 3")], root=racine)

    assert reg.identite_pour([("DOC1", "Speaker 1"), ("DOC2", "Speaker 3")], racine) == uid


def test_un_recouvrement_partiel_suffit(racine) -> None:
    """Une reconstruction ne redonne pas forcément exactement les mêmes membres."""
    uid = reg.nouvelle_voix(racine)
    reg.enregistrer_membres(uid, [("DOC1", "Speaker 1"), ("DOC2", "Speaker 3")], root=racine)
    assert reg.identite_pour([("DOC1", "Speaker 1")], racine) == uid


def test_sans_recouvrement_aucune_identite_n_est_inventee(racine) -> None:
    """Mieux vaut une voix anonyme qu'un nom attribué à la mauvaise personne."""
    uid = reg.nouvelle_voix(racine)
    reg.nommer(uid, "Ari", root=racine)
    reg.enregistrer_membres(uid, [("DOC1", "Speaker 1")], root=racine)

    assert reg.identite_pour([("DOC9", "Speaker 9")], racine) is None


def test_la_voix_la_plus_recouvrante_l_emporte(racine) -> None:
    a, b = reg.nouvelle_voix(racine), reg.nouvelle_voix(racine)
    reg.enregistrer_membres(a, [("DOC1", "Speaker 1")], root=racine)
    reg.enregistrer_membres(b, [("DOC1", "Speaker 2"), ("DOC2", "Speaker 2")], root=racine)

    assert reg.identite_pour([("DOC1", "Speaker 2"), ("DOC2", "Speaker 2")], racine) == b


def test_retrouver_une_voix_fusionnee_rend_la_survivante(racine) -> None:
    """Interroger les membres de la voix absorbée doit mener à celle qui reste."""
    a, b = reg.nouvelle_voix(racine), reg.nouvelle_voix(racine)
    reg.enregistrer_membres(a, [("DOC1", "Speaker 1")], root=racine)
    reg.nommer(b, "Ari", root=racine)
    reg.fusionner(a, b, root=racine)

    assert reg.identite_pour([("DOC1", "Speaker 1")], racine) == b


def test_scenario_complet_reconstruction_apres_changement_d_extracteur(racine) -> None:
    """Le cas que tout ce module existe pour garantir, de bout en bout."""
    uid = reg.nouvelle_voix(racine)
    reg.nommer(uid, "Ari", root=racine)
    reg.enregistrer_membres(uid, [("DOC1", "Speaker 1"), ("DOC2", "Speaker 2")], root=racine)
    reg.declarer_extracteur(_spec(), racine)

    # On change d'extracteur : les vecteurs sont jetés, la base est reconstruite.
    with pytest.raises(reg.ExtracteurIncompatible):
        reg.verifier_extracteur(_spec(modele="Alibaba-NLP/CAM++", dim=512), racine)
    reg.declarer_extracteur(_spec(modele="Alibaba-NLP/CAM++", dim=512), racine)

    # Le nouveau clustering produit un groupe : il retrouve son identité et son nom.
    retrouve = reg.identite_pour([("DOC1", "Speaker 1"), ("DOC2", "Speaker 2")], racine)
    assert retrouve == uid
    assert reg.etiquette(retrouve, racine) == ("Ari", "manual")


# --- Adoption des voix créées avant l'identité stable -----------------------

def test_adopter_identites_est_idempotent_et_consigne_les_membres(
    isolated_stores, monkeypatch,
) -> None:
    """Les voix créées avant la colonne `uid` — ou par une réindexation qui
    rattache sans créer — resteraient innommables durablement : un nom saisi
    dessus disparaîtrait à la reconstruction suivante."""
    from core.config import VOICE_EMBED_DIM
    from core import docid as d, voiceprint

    output = isolated_stores["output_dir"]
    vec = [1.0] + [0.0] * (VOICE_EMBED_DIM - 1)
    doc = d.nouveau()
    voiceprint.index_file(doc, {"Speaker 1": vec}, name=f"{doc}.md")
    # On simule une base antérieure : identité effacée.
    conn = voiceprint._conn()
    try:
        conn.execute("UPDATE voices SET uid = NULL")
        conn.commit()
    finally:
        conn.close()

    premier = voiceprint.adopter_identites()
    second = voiceprint.adopter_identites()

    assert premier["adoptees"] >= 1
    assert second["adoptees"] == 0, "une voix déjà identifiée ne doit pas être ré-adoptée"
    magasin = reg.lire(output)
    assert all(v.get("membres") for v in magasin["voix"].values()), (
        "les membres doivent être consignés : c'est par eux qu'une voix se retrouve"
    )


def test_un_nom_deja_en_base_rejoint_l_autoritatif_a_l_adoption(
    isolated_stores, monkeypatch,
) -> None:
    """Sinon la prochaine reconstruction l'efface — le défaut d'origine."""
    from core.config import VOICE_EMBED_DIM
    from core import docid as d, voiceprint

    output = isolated_stores["output_dir"]
    vec = [1.0] + [0.0] * (VOICE_EMBED_DIM - 1)
    doc = d.nouveau()
    voiceprint.index_file(doc, {"Speaker 1": vec}, name=f"{doc}.md")
    conn = voiceprint._conn()
    try:
        conn.execute("UPDATE voices SET uid = NULL, label = 'Ari', label_source = 'manual'")
        conn.commit()
    finally:
        conn.close()

    voiceprint.adopter_identites()

    magasin = reg.lire(output)
    assert any(v.get("label") == "Ari" for v in magasin["voix"].values())


# --- Une reconstruction complète ne perd plus les noms ----------------------

def _voix_de(doc: str, speaker: str = "Speaker 1"):
    """(id, uid, label) de la voix rattachée à ce locuteur."""
    from core import voiceprint

    conn = voiceprint._conn()
    try:
        return conn.execute(
            "SELECT v.id, v.uid, v.label FROM voices v "
            "JOIN voice_members m ON m.voice_id = v.id "
            "WHERE m.document_id = ? AND m.speaker_local = ?",
            (doc, speaker),
        ).fetchone()
    finally:
        conn.close()


def test_une_reconstruction_complete_rend_son_nom_a_la_voix(
    isolated_stores, monkeypatch,
) -> None:
    """LE scénario que cette suite existe pour garantir.

    Vider le registre puis le reconstruire redonnait des voix anonymes : les
    noms restaient dans le magasin sans que rien ne sache à qui les appliquer.
    """
    from core.config import VOICE_EMBED_DIM
    from core import docid as d, voiceprint

    vec = [1.0] + [0.0] * (VOICE_EMBED_DIM - 1)
    doc = d.nouveau()
    voiceprint.index_file(doc, {"Speaker 1": vec}, name=f"{doc}.md")
    voiceprint.rename(_voix_de(doc)["id"], "Ari")

    # Reconstruction TOTALE de l'index dérivé — le magasin, lui, reste.
    conn = voiceprint._conn()
    try:
        conn.execute("DELETE FROM voice_members")
        conn.execute("DELETE FROM voices")
        conn.commit()
    finally:
        conn.close()
    voiceprint.index_file(doc, {"Speaker 1": vec}, name=f"{doc}.md")

    apres = _voix_de(doc)
    assert apres["label"] == "Ari", (
        "la voix reconstruite doit retrouver son nom, pas repartir anonyme"
    )
    assert reg.etiquette(apres["uid"], isolated_stores["output_dir"])[1] == "manual"


def test_deux_groupes_distincts_ne_partagent_pas_une_identite(
    isolated_stores, monkeypatch,
) -> None:
    """Une identité déjà revendiquée n'est pas reprise : deux groupes ne peuvent
    pas être la même personne, et dupliquer un `uid` casserait l'appariement."""
    from core.config import VOICE_EMBED_DIM
    from core import docid as d, voiceprint

    proche = [1.0] + [0.0] * (VOICE_EMBED_DIM - 1)
    loin = [0.0] * (VOICE_EMBED_DIM - 1) + [1.0]
    doc = d.nouveau()
    voiceprint.index_file(doc, {"Speaker 1": proche, "Speaker 2": loin}, name=f"{doc}.md")

    a, b = _voix_de(doc, "Speaker 1"), _voix_de(doc, "Speaker 2")
    assert a["id"] != b["id"], "pré-condition : deux voix distinctes"
    assert a["uid"] != b["uid"], "deux voix ne doivent jamais partager une identité"


def test_un_locuteur_inconnu_recoit_une_identite_neuve(
    isolated_stores, monkeypatch,
) -> None:
    """Pas de faux héritage : sans recouvrement, on ne réutilise rien."""
    from core.config import VOICE_EMBED_DIM
    from core import docid as d, voiceprint

    vec = [1.0] + [0.0] * (VOICE_EMBED_DIM - 1)
    doc = d.nouveau()
    voiceprint.index_file(doc, {"Speaker 1": vec}, name=f"{doc}.md")
    premier = _voix_de(doc)["uid"]

    autre = d.nouveau()
    loin = [0.0] * (VOICE_EMBED_DIM - 1) + [1.0]
    voiceprint.index_file(autre, {"Speaker 1": loin}, name=f"{autre}.md")

    assert _voix_de(autre)["uid"] != premier


# --- Pas de fantômes dans le fichier des décisions humaines ------------------

def test_forger_une_identite_n_ecrit_rien(racine) -> None:
    """Écrire à la création produisait des fantômes : la base supprime une voix
    dès qu'elle perd sa dernière empreinte, et une réindexation vide
    transitoirement chaque voix — l'entrée du magasin, elle, survivait."""
    uid = reg.nouvelle_voix(racine)
    assert docid.est_valide(uid)
    assert not (racine / ".voices.json").exists() or reg.lire(racine)["voix"] == {}


def test_l_entree_apparait_au_premier_contenu_reel(racine) -> None:
    for action in (
        lambda u: reg.nommer(u, "Ari", root=racine),
        lambda u: reg.enregistrer_membres(u, [("DOC1", "Speaker 1")], root=racine),
    ):
        uid = reg.nouvelle_voix(racine)
        assert uid not in reg.lire(racine)["voix"]
        action(uid)
        assert uid in reg.lire(racine)["voix"]


def test_elaguer_retire_les_entrees_sans_aucun_contenu(racine) -> None:
    vide = reg.nouvelle_voix(racine)
    reg.nommer(vide, "X", root=racine)
    reg.nommer(vide, "", root=racine)          # nom effacé : l'entrée subsiste
    fantome = reg.nouvelle_voix(racine)
    reg.enregistrer_membres(fantome, [], root=racine)  # entrée sans membre
    utile = reg.nouvelle_voix(racine)
    reg.enregistrer_membres(utile, [("DOC1", "Speaker 1")], root=racine)

    resultat = reg.elaguer(racine)

    restantes = reg.lire(racine)["voix"]
    assert utile in restantes, "une voix avec des membres n'est jamais retirée"
    assert resultat["retirees"] >= 1


def test_elaguer_epargne_toute_trace_de_decision_humaine(racine) -> None:
    """Conservateur par construction : un nom, même effacé depuis, ou une
    participation à une fusion suffit à protéger l'entrée."""
    nomme = reg.nouvelle_voix(racine)
    reg.nommer(nomme, "Ari", root=racine)
    a, b = reg.nouvelle_voix(racine), reg.nouvelle_voix(racine)
    # On leur donne une entrée AVANT la fusion : c'est le cas réel, une voix
    # fusionnée a forcément été rattachée à des locuteurs auparavant.
    reg.enregistrer_membres(a, [("DOC1", "Speaker 1")], root=racine)
    reg.enregistrer_membres(b, [("DOC2", "Speaker 1")], root=racine)
    reg.fusionner(a, b, root=racine)

    reg.elaguer(racine)

    restantes = reg.lire(racine)["voix"]
    assert nomme in restantes
    assert a in restantes and b in restantes, (
        "les voix impliquées dans une fusion portent une décision humaine"
    )


def test_elaguer_est_idempotent(racine) -> None:
    reg.nouvelle_voix(racine)
    utile = reg.nouvelle_voix(racine)
    reg.enregistrer_membres(utile, [("DOC1", "Speaker 1")], root=racine)
    reg.elaguer(racine)
    assert reg.elaguer(racine)["retirees"] == 0
