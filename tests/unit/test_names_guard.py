"""Garde-fous sur les noms propres déduits du profil LLM (core/names.py).

Le bug d'origine : le filtre n'existait qu'à l'étape « suggérer un nom de voix ».
Le rôle écrit dans le .md restait la sortie LLM brute, donc une célébrité
seulement CITÉE (« Lina », « Royer ») s'affichait dans le transcript lu par
l'utilisateur alors même que le registre de voix l'avait rejetée.
"""

from core import names


def test_name_extrait_de_la_parenthese():
    assert names.name_from_role("interviewee (Lina)") == "Lina"


def test_role_generique_ne_donne_aucun_nom():
    assert names.name_from_role("animateur") is None
    assert names.name_from_role(None) is None
    assert names.name_from_role("") is None


def test_strip_name_conserve_le_role_generique():
    """Le rôle est vérifiable, le nom ne l'est pas : on ne jette que le nom."""
    assert names.strip_name("interviewee (Lina)") == "interviewee"


def test_strip_name_rend_none_si_le_role_etait_juste_un_nom():
    assert names.strip_name("(Lina)") is None


def test_nom_unique_conserve():
    roles = {"Speaker 1": "host", "Speaker 2": "guest (Lina)"}
    assert names.reliable_names(roles) == {"Speaker 2": "Lina"}
    assert names.sanitize_roles(roles) == roles  # rien à retirer


def test_meme_nom_sur_deux_locuteurs_rejete():
    """Un nom porté par deux locuteurs est une devinette, pas une identification."""
    roles = {"Speaker 1": "guest (Lina)", "Speaker 2": "guest (Lina)"}
    assert names.reliable_names(roles) == {}
    assert names.sanitize_roles(roles) == {"Speaker 1": "guest", "Speaker 2": "guest"}


def test_plateau_double_aucun_nom_retenu():
    """Cas réel : plateau FR avec invités US en traduction simultanée.

    Les noms sont ceux des AUTEURS des propos, mais les voix diarisées sont
    celles des INTERPRÈTES. Retenir un nom ferait apprendre au registre global la
    voix de l'interprète sous le nom de l'acteur — erreur silencieuse qui
    contaminerait tous les autres fichiers doublés par la même personne.
    """
    roles = {
        "Speaker 1": "animateur / présentateur",
        "Speaker 6": "Hugo Lambert (acteur)",
        "Speaker 8": "Yanis Roche (acteur)",
        "Speaker 10": "Yanis Roche (acteur)",
        "Speaker 11": "Théo Vasseur (acteur)",
        **{f"Speaker {i}": "locuteur inconnu" for i in (2, 3, 4, 5, 7, 9, 12, 13, 14, 15)},
    }
    assert len(roles) == 15
    assert names.reliable_names(roles) == {}
    clean = names.sanitize_roles(roles)
    assert "Lambert" not in " ".join(v or "" for v in clean.values())
    assert clean["Speaker 1"] == "animateur / présentateur"  # rôle générique gardé


def test_sur_segmentation_rejette_tous_les_noms():
    """> 8 locuteurs : la diarisation ne correspond plus à des personnes."""
    roles = {f"Speaker {i}": f"guest (Nom{i})" for i in range(1, 11)}
    assert names.reliable_names(roles) == {}
    assert set(names.sanitize_roles(roles).values()) == {"guest"}


def test_a_la_limite_huit_locuteurs_les_noms_passent():
    roles = {f"Speaker {i}": f"guest (Nom{i})" for i in range(1, 9)}
    assert len(names.reliable_names(roles)) == 8


def test_sanitize_preserve_les_locuteurs_sans_role():
    roles = {"Speaker 1": None, "Speaker 2": "guest (Lina)"}
    assert names.sanitize_roles(roles) == roles
