"""Identifiant stable : forme, unicité, ordre, et surtout NON-ÉCRASEMENT."""

import pytest

from core import docid

pytestmark = pytest.mark.unit


def test_la_forme_est_un_ulid_de_26_caracteres():
    i = docid.nouveau()
    assert len(i) == 26
    assert docid.est_valide(i)


def test_deux_appels_ne_collident_pas():
    assert len({docid.nouveau() for _ in range(2000)}) == 2000


def test_l_alphabet_exclut_les_caracteres_ambigus():
    """Crockford retire I, L, O et U : 1/I/L et 0/O sont confondus à la lecture."""
    tous = "".join(docid.nouveau() for _ in range(300))
    for interdit in "ILOU":
        assert interdit not in tous


def test_l_ordre_alphabetique_suit_l_ordre_de_creation():
    """C'est l'intérêt de l'ULID sur l'UUID : trier les ids trie par date."""
    a = docid.nouveau(horodatage_ms=1_700_000_000_000)
    b = docid.nouveau(horodatage_ms=1_700_000_001_000)
    assert a < b


def test_l_horodatage_est_relisible():
    ms = 1_753_000_000_000
    assert docid.horodatage_ms(docid.nouveau(horodatage_ms=ms)) == ms


@pytest.mark.parametrize("mauvais", [
    "", "trop-court", "0" * 25, "0" * 27, None, 42,
    "IIIIIIIIIIIIIIIIIIIIIIIIII",   # caractères exclus de l'alphabet
    "abcdefghijklmnopqrstuvwxyz",   # minuscules
])
def test_ce_qui_n_est_PAS_un_identifiant_est_refuse(mauvais):
    """`est_valide` garde le front-matter : un id déjà écrit ne doit JAMAIS être
    remplacé, sinon tous les rattachements qui le citent sont orphelins."""
    assert not docid.est_valide(mauvais)


# --- Migration des documents existants ---------------------------------------

def _md(tmp_path, nom, fm):
    import yaml
    p = tmp_path / nom
    p.write_text("---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True)
                 + "---\n\n# Titre\n\nDu corps qui ne doit pas bouger.\n", encoding="utf-8")
    return p


def test_la_simulation_n_ecrit_RIEN(tmp_path):
    """Une migration silencieuse sur des données personnelles n'est pas
    acceptable : le défaut est `simulation=True`."""
    from core import backfill_ids
    p = _md(tmp_path, "a.md", {"title": "a"})
    avant = p.read_text()
    r = backfill_ids.attribuer(tmp_path, simulation=True)
    assert r["attribues"] == 1
    assert p.read_text() == avant


def test_un_identifiant_EXISTANT_n_est_jamais_ecrase(tmp_path):
    """L'écraser orphelinerait tout ce qui le référence — c'est le pire défaut
    possible pour un identifiant."""
    from core import backfill_ids, docid
    ancien = docid.nouveau()
    p = _md(tmp_path, "a.md", {"id": ancien, "title": "a"})
    backfill_ids.attribuer(tmp_path, simulation=False)
    import yaml
    assert yaml.safe_load(p.read_text().split("---", 2)[1])["id"] == ancien


def test_le_CORPS_n_est_pas_touche(tmp_path):
    from core import backfill_ids
    p = _md(tmp_path, "a.md", {"title": "a"})
    corps_avant = p.read_text().split("---", 2)[2]
    backfill_ids.attribuer(tmp_path, simulation=False)
    assert p.read_text().split("---", 2)[2] == corps_avant


def test_les_autres_metadonnees_sont_conservees(tmp_path):
    from core import backfill_ids
    import yaml
    fm = {"title": "a", "language": "fr", "n_speakers": 3, "duration": "00:12:30"}
    p = _md(tmp_path, "a.md", fm)
    backfill_ids.attribuer(tmp_path, simulation=False)
    apres = yaml.safe_load(p.read_text().split("---", 2)[1])
    assert {k: v for k, v in apres.items() if k != "id"} == fm


def test_la_migration_est_IDEMPOTENTE(tmp_path):
    from core import backfill_ids
    _md(tmp_path, "a.md", {"title": "a"})
    backfill_ids.attribuer(tmp_path, simulation=False)
    assert backfill_ids.attribuer(tmp_path, simulation=False)["attribues"] == 0


def test_deux_documents_recoivent_des_identifiants_DIFFERENTS(tmp_path):
    from core import backfill_ids
    import yaml
    _md(tmp_path, "a.md", {"title": "a"})
    _md(tmp_path, "b.md", {"title": "b"})
    backfill_ids.attribuer(tmp_path, simulation=False)
    ids = [yaml.safe_load((tmp_path / n).read_text().split("---", 2)[1])["id"]
           for n in ("a.md", "b.md")]
    assert ids[0] != ids[1]
