"""Le CR sortait avec DEUX titres de niveau 1 consécutifs.

Le gabarit écrit « # Compte-rendu — <titre> » et le modèle produit le sien.
On rétrograde ceux du modèle plutôt que de les supprimer : son intitulé est
souvent plus informatif, et la hiérarchie des titres reste valide.
"""

import pytest

from core.report import demote_headings, spoken_markdown

pytestmark = pytest.mark.unit


def test_h1_du_modele_devient_h2():
    assert demote_headings("# Compte-rendu du match") == "## Compte-rendu du match"


def test_les_autres_niveaux_sont_intacts():
    md = "## Résumé\n### Détail\nTexte normal."
    assert demote_headings(md) == md


def test_un_diese_sans_espace_n_est_pas_un_titre():
    assert demote_headings("#hashtag") == "#hashtag"


def test_les_blocs_de_code_sont_preserves():
    """Dans un bloc de code, « # » est un commentaire, pas un titre."""
    md = "# Titre\n\n```bash\n# ceci est un commentaire\n```\n\n# Autre"
    out = demote_headings(md)
    assert "# ceci est un commentaire" in out
    assert "## ceci" not in out
    assert out.startswith("## Titre")
    assert out.endswith("## Autre")


def test_un_seul_h1_subsiste_dans_le_document_final():
    cr = "# Titre du modèle\n\n## Section\n\n# Autre titre"
    document = f"# Compte-rendu — exemple\n\n{demote_headings(cr)}"
    h1 = [line for line in document.splitlines() if line.startswith("# ")]
    assert h1 == ["# Compte-rendu — exemple"]


# --- Détection du profil : elle décide de la FORCE DE LA PREUVE ---
# Sans profil, on juge sur du texte brut. Le classifieur se voit alors retirer
# les gabarits qui réclament décisions et engagements : moins de preuve, moins
# d'options risquées.

from core.report import profile_excerpt  # noqa: E402


def test_profil_reconnu_quand_la_section_existe():
    body = (
        "# titre\n\n## 🗣️ Profil de la conversation\n\n"
        "**Type :** interview\n\nRésumé de la conversation.\n\n"
        "## Transcript\n\n**Speaker 1** [00:00:01] Bonjour.\n"
    )
    excerpt, profiled = profile_excerpt(body)
    assert profiled is True
    assert "interview" in excerpt
    assert "Bonjour" not in excerpt  # le transcript n'est pas le profil


def test_ancien_format_sans_profil_ni_section_transcript():
    """Régression : ces .md n'ont AUCUN titre de section.

    L'heuristique « tout ce qui précède le transcript » y avalait le transcript
    entier et annonçait `profiled=True` — une preuve forte qui n'existait pas,
    d'où un vlog classé « réunion de projet » et sommé de livrer des décisions.
    """
    body = "# titre\n\nPremière phrase du transcript.\nDeuxième phrase.\n"
    excerpt, profiled = profile_excerpt(body)
    assert profiled is False
    assert "Première phrase" in excerpt


def test_transcript_non_profile_avec_section():
    body = "# titre\n\n## Transcript\n\n**Speaker 1** [00:00:01] Bonjour à tous.\n"
    excerpt, profiled = profile_excerpt(body)
    assert profiled is False
    assert "Bonjour" in excerpt


def test_liste_restreinte_sans_profil():
    """Les genres à engagements ne sont pas proposés sur preuve faible."""
    from core import templates
    complet = templates.genre_list(commitments=True)
    restreint = templates.genre_list(commitments=False)
    for g in templates.COMMITMENT_GENRES:
        assert g in complet
        assert g not in restreint


def test_la_source_du_cr_exclut_le_profil_llm_et_garde_les_horodatages():
    body = (
        "# titre\n\n## 🗣️ Conversation profile\n\n"
        "Affirmation inventée par le profil.\n\n"
        "## Transcript\n\n"
        "**Speaker 1** [00:00:01] Parole réellement prononcée.\n"
    )

    source = spoken_markdown(body)

    assert "Affirmation inventée" not in source
    assert "**Speaker 1** [00:00:01] Parole réellement prononcée." in source


def test_la_source_des_anciens_transcripts_reste_intacte():
    body = "# titre\n\n**Speaker 1** [00:00:01] Ancien format.\n"

    assert spoken_markdown(body) == body.strip()


def test_split_into_chunks_court_renvoie_une_seule_tranche():
    from core.report import split_into_chunks
    body = "\n".join(f"**Speaker 1** [{i:02d}:00] Phrase {i}" for i in range(50))
    chunks = split_into_chunks(body, max_lines_per_chunk=120)
    assert len(chunks) == 1
    assert chunks[0] == body


def test_split_into_chunks_long_decoupe_aux_frontieres_de_locuteurs():
    from core.report import split_into_chunks
    lines = [f"**Speaker {(i % 2) + 1}** [{i:02d}:00] Tour de parole numéro {i}" for i in range(250)]
    body = "\n".join(lines)
    chunks = split_into_chunks(body, max_lines_per_chunk=100)
    assert len(chunks) == 3
    assert "**Speaker 1** [00:00]" in chunks[0]
    assert len(chunks[0].splitlines()) >= 100


def test_map_summarize_chunks_ignoree_si_tranche_unique():
    from core.report import map_summarize_chunks
    contexte, perdues = map_summarize_chunks(["un seul chunk"], "fr", num_ctx=2048)
    assert contexte == ""
    assert perdues == []


# ---------- Motif nº3 : la phase MAP ne perd plus de tranche en silence ----------
#
# Le test ci-dessus vérifie que la fonction NE FAIT RIEN sur une tranche
# unique — indéfectible par construction, et c'est tout ce que le corps de
# boucle avait comme couverture. `core/report.py` lignes 321-364 n'étaient
# jamais exécutées. Neuf tranches sur dix perdues sur des 429 donnaient un
# compte rendu tronqué, sans un avertissement, avec
# `citation_integrity_verified` intact.

def test_une_tranche_qui_echoue_est_DECLAREE_et_non_avalee(monkeypatch):
    """Le prédicat de silence était corrélé au défaut : plus il y avait
    d'échecs, moins on en savait."""
    from core import report as report_mod

    appels = {"n": 0}

    def chat_capricieux(*a, **k):
        appels["n"] += 1
        if appels["n"] == 2:
            raise RuntimeError("429 rate limit")
        return f"résumé {appels['n']}"

    monkeypatch.setattr(report_mod.llm, "chat", chat_capricieux)

    contexte, perdues = report_mod.map_summarize_chunks(
        ["a", "b", "c"], "fr", num_ctx=2048)

    assert len(perdues) == 1, perdues
    assert "429" in perdues[0], perdues
    assert "tranche 2/3" in perdues[0], perdues
    # Les tranches saines survivent : une perte ne perd pas tout.
    assert "résumé 1" in contexte and "résumé 3" in contexte


def test_toutes_les_tranches_saines_ne_declarent_aucune_perte(monkeypatch):
    """Garde sur l'instrument : si `perdues` était toujours rempli, le test
    précédent serait vert sans rien prouver."""
    from core import report as report_mod

    monkeypatch.setattr(report_mod.llm, "chat", lambda *a, **k: "résumé")
    contexte, perdues = report_mod.map_summarize_chunks(
        ["a", "b", "c"], "fr", num_ctx=2048)
    assert perdues == []
    assert contexte.count("résumé") == 3

