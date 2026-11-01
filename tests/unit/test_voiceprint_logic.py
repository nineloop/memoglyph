

# --- L'annexe illisible ne doit jamais être écrasée ----------------------

def test_save_embeddings_refuse_d_ecrire_sur_une_annexe_illisible(tmp_path):
    """Lecture ratée -> `{}` -> réécriture avec la SEULE nouvelle entrée : les
    empreintes de tous les autres documents étaient détruites en silence."""
    import pytest

    from core import voiceprint
    from core.config import VOICE_EMBED_DIM

    annexe = tmp_path / ".voiceprints.json"
    annexe.write_text("{ceci n'est pas du JSON", encoding="utf-8")
    avant = annexe.read_text(encoding="utf-8")

    vec = [1.0] + [0.0] * (VOICE_EMBED_DIM - 1)
    with pytest.raises(voiceprint.SidecarIllisible):
        voiceprint.save_embeddings("nouveau", {"Speaker 1": vec}, root=tmp_path)

    assert annexe.read_text(encoding="utf-8") == avant, (
        "l'annexe ne doit pas être réécrite à partir d'une lecture ratée"
    )


def test_drop_embeddings_leve_au_lieu_d_etre_un_no_op_muet(tmp_path):
    """Sans exception, la purge concluait au succès en laissant les empreintes."""
    import pytest

    from core import voiceprint

    annexe = tmp_path / ".voiceprints.json"
    annexe.write_text("tronque", encoding="utf-8")

    with pytest.raises(voiceprint.SidecarIllisible):
        voiceprint.drop_embeddings("peu-importe", root=tmp_path)


def test_une_annexe_absente_reste_un_etat_normal(tmp_path):
    """« Absente » n'est pas « illisible » : lire doit valoir {} sans lever."""
    from core import voiceprint

    assert voiceprint.load_embeddings("rien", root=tmp_path) == {}
    voiceprint.drop_embeddings("rien", root=tmp_path)  # ne doit pas lever
