"""Le front-matter doit survivre à des valeurs hostiles.

Il était écrit à la main et relu par PyYAML. Une valeur contenant un guillemet —
un rôle produit par le LLM, par exemple `interviewé "principal"` — produisait du
YAML invalide, et `library._split_frontmatter` retombait silencieusement sur
`{}` : langue, modèle, projet et statut de profilage perdus d'un coup, sans
aucun signal. Écrire avec le même analyseur que celui qui relit rend cette classe
d'erreur impossible plutôt que rare.
"""

import pytest

from core import library, writer_md
from core.stt_base import Segment, Transcript

pytestmark = pytest.mark.unit

TR = Transcript(
    segments=[Segment(4, 8, "Bonjour.", speaker="Speaker 1")],
    full_text="Bonjour.", language="fr",
)
META = {"title": "t", "duration": "00:42:15", "language": "fr", "profiled": True}


def _roundtrip(role: str) -> dict:
    profile = {"speakers": {"Speaker 1": {
        "role": role, "talk_time": "12:30", "share": "30%"}}}
    md = writer_md.render_markdown(
        TR, META, timestamps=True, diarized=True, profile=profile)
    meta, _ = library._split_frontmatter(md)
    return meta


@pytest.mark.parametrize("role", [
    'interviewé "principal"',      # le cas qui cassait tout
    "animateur : plateau",
    "l'invité d'honneur",
    "rôle\nsur deux lignes",
    "expert {IA}",
    "expert [tech]",
    "expert #1",
    "- tiret initial",
    "* étoile initiale",
    "",
])
def test_role_hostile_ne_detruit_pas_les_metadonnees(role):
    meta = _roundtrip(role)
    assert meta.get("language") == "fr", "métadonnées perdues"
    assert meta.get("profiled") is True
    assert meta["speakers"]["Speaker 1"]["role"] == role


def test_talk_time_reste_une_chaine():
    """En YAML 1.1, `12:30` non quoté vaut 750 (sexagésimal), pas « 12:30 »."""
    meta = _roundtrip("animateur")
    assert meta["speakers"]["Speaker 1"]["talk_time"] == "12:30"


def test_duree_reste_une_chaine():
    meta = _roundtrip("animateur")
    assert meta["duration"] == "00:42:15"


def test_valeurs_nulles_omises():
    md = writer_md.render_markdown(
        TR, {"title": "t", "language": None, "profiled": False},
        timestamps=True, diarized=False, profile=None)
    meta, _ = library._split_frontmatter(md)
    assert "language" not in meta
    assert meta["profiled"] is False  # False n'est PAS None : il doit rester


def test_accents_non_echappes():
    """allow_unicode : un front-matter reste lisible par un humain."""
    md = writer_md.render_markdown(
        TR, {"title": "réunion équipe", "language": "fr"},
        timestamps=True, diarized=False, profile=None)
    assert "réunion équipe" in md
    assert "\\u" not in md


# --- Le front-matter reste EN BLOCS à la racine --------------------------------
# Régression réelle : `default_flow_style=None` laisse PyYAML choisir par
# collection. Le front-matter d'un CR est plat, et il est parti sur UNE SEULE
# LIGNE le jour où le désarmement du vérificateur numérique lui a retiré sa
# dernière liste. Valide, relu correctement — mais illisible et non greppable,
# alors que le `.md` est la source de vérité.

def _fm(meta):
    return writer_md._yaml_frontmatter(meta).splitlines()


def test_un_front_matter_PLAT_reste_en_blocs():
    lignes = _fm({"title": "x", "date": "2026-07-21", "language": "en"})
    assert lignes[0] == "---" and lignes[-1] == "---"
    assert any(line.startswith("language:") for line in lignes), "doit rester greppable"
    assert not any(line.startswith("{") for line in lignes), "jamais de style flow à la racine"


def test_chaque_cle_de_racine_est_sur_sa_propre_ligne():
    meta = {"title": "x", "date": "2026-07-21", "type": "report",
            "language": "en", "model": "qwen3.5:9b", "genre": "interview"}
    corps = [line for line in _fm(meta) if line != "---"]
    assert len(corps) == len(meta)


def test_les_dicts_FEUILLES_restent_compacts():
    """L'intention d'origine : un fichier à 14 locuteurs ne doit pas exploser en
    47 lignes de front-matter."""
    meta = {"n_speakers": 2, "speakers": {
        "Speaker 1": {"talk_time": "01:25", "share": "7%"},
        "Speaker 2": {"talk_time": "02:10", "share": "9%"}}}
    lignes = _fm(meta)
    assert any("{talk_time:" in line for line in lignes), "les feuilles restent inline"
    assert any(line.startswith("speakers:") for line in lignes)


def test_les_listes_restent_compactes():
    lignes = _fm({"removed_speaker_bindings": ["Speaker 3 (X)", "Speaker 9 (Y)"]})
    assert any(line.startswith("removed_speaker_bindings: [") for line in lignes)


@pytest.mark.parametrize("duree", ["00:13:41", "12:30:00", "23:59:59", "1:30"])
def test_le_piege_sexagesimal_reste_neutralise(duree):
    """YAML 1.1 lit « 12:30 » comme 750. PyYAML place les guillemets là où il
    faut — on vérifie le ROND-TRIP, pas la présence de guillemets."""
    import yaml as _y
    fm = "\n".join(line for line in _fm({"duration": duree}) if line != "---")
    assert _y.safe_load(fm)["duration"] == duree
