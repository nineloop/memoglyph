"""Le pipeline de bout en bout, sur une réunion PUBLIQUE à réponse connue.

Les autres tests `ml` vérifient des propriétés génériques : horodatages
monotones, absence de boucle, granularité. Celui-ci confronte le pipeline réel
(Whisper, pyannote, profilage par le LLM local) à une référence EXTÉRIEURE :
la réunion IS1009a de l'AMI Meeting Corpus (CC BY 4.0, `docs/corpus-demo.md`),
dont l'archive d'annotations donne, pour chacun des quatre participants, chaque
mot prononcé avec ses horodatages, et le rôle tenu dans le scénario.

Trois familles d'assertions, toutes ancrées dans cette référence :

- le TEXTE : taux d'erreur sur les mots contre la transcription manuelle ;
- les LOCUTEURS : chaque locuteur diarisé est rattaché, par chevauchement
  temporel, à un participant réel, et deux locuteurs ne tombent pas sur le
  même ;
- les RÔLES : le rôle que le profilage attribue à un locuteur doit être celui
  du participant réel sous ce locuteur. Une inversion des rôles, défaut déjà
  rencontré sur un entretien à deux voix, fait échouer le test, car c'est le
  signal et non le LLM qui dit qui parle.

Les seuils, posés dans le code ci-dessous, gardent une marge sous ce que le
pipeline rend sur ce même extrait.

Marqué `ml` et `slow` : il charge Whisper, pyannote et le LLM local, et prend
quelques minutes. Ignoré, jamais en échec, si le corpus ou les services
manquent (`python tools/corpus_demo.py` récupère le corpus).
"""

from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = [pytest.mark.ml, pytest.mark.slow]

REUNION = "IS1009a"
MEDIA = "IS1009a.Mix-Headset.wav"
ANNOTATIONS = "ami_public_manual_1.6.2.zip"
# L'extrait de la bibliothèque de démo : il saute la minute de silence initiale,
# où Whisper invente des « Thank you. », et couvre le reste de la réunion.
DEBUT_S, DUREE_S = 61.0, 778.0

# Rôles du scénario AMI (`corpusResources/meetings.xml`), par canal de participant.
ROLES_AMI = {"A": "PM", "B": "UI", "C": "ID", "D": "ME"}
# Ce que le profilage écrit pour chacun, en anglais ou en français.
MOTIFS_ROLES = {
    "PM": re.compile(r"project manager|chef(?:fe)? de projet|\bPM\b", re.I),
    "UI": re.compile(r"user interface|interface utilisateur|\bUI\b", re.I),
    "ID": re.compile(r"industrial|industriel", re.I),
    "ME": re.compile(r"marketing", re.I),
}

SEUIL_WER = 0.32
SEUIL_PURETE = 0.60
# Un locuteur diarisé qui parle moins que ça n'est pas jugé : sur quelques
# secondes, la pureté ne mesure que le bruit.
MINIMUM_S = 15.0
# Fin d'un segment du transcript : le début du suivant, borné.
SEGMENT_MAX_S = 20.0

# Hésitations : la transcription manuelle les note, Whisper les omet souvent.
# Les compter mesurerait un choix de style, pas une erreur.
HESITATIONS = frozenset({
    "uh", "um", "mm", "hmm", "mm-hmm", "uh-huh", "ah", "oh", "er", "erm",
    "huh", "hm", "mhm",
})


@dataclass(frozen=True)
class Mot:
    debut: float
    fin: float
    participant: str
    texte: str


@dataclass(frozen=True)
class Segment:
    debut: float
    fin: float
    locuteur: str
    texte: str


# ---------- Référence AMI ----------

def mots_de_reference(archive: Path, reunion: str, debut: float, duree: float) -> list[Mot]:
    """Mots de la transcription manuelle dans la fenêtre, horodatés depuis `debut`."""
    mots: list[Mot] = []
    with zipfile.ZipFile(archive) as z:
        for participant in ROLES_AMI:
            racine = ET.fromstring(z.read(f"words/{reunion}.{participant}.words.xml"))
            for w in racine:
                if not w.tag.endswith("w") or w.get("punc") == "true":
                    continue
                s, e = w.get("starttime"), w.get("endtime")
                if s is None or e is None:
                    continue
                s, e = float(s), float(e)
                if debut <= s < debut + duree:
                    mots.append(Mot(s - debut, e - debut, participant, w.text or ""))
    return sorted(mots, key=lambda m: m.debut)


# ---------- Transcript produit ----------

_SEGMENT = re.compile(r"^\*\*(Speaker \d+)\*\* \[(\d+):(\d{2}):(\d{2})\] (.*)$", re.M)
_ROLE = re.compile(r"^  (Speaker \d+): \{role: ([^,}]+)", re.M)


def segments(md: str) -> list[Segment]:
    """Segments attribués du transcript, la fin bornée par le début du suivant."""
    bruts = [(int(h) * 3600 + int(m) * 60 + int(s), loc, txt)
             for loc, h, m, s, txt in _SEGMENT.findall(md)]
    sortie = []
    for i, (debut, loc, txt) in enumerate(bruts):
        suivant = bruts[i + 1][0] if i + 1 < len(bruts) else debut + SEGMENT_MAX_S
        sortie.append(Segment(debut, min(suivant, debut + SEGMENT_MAX_S), loc, txt))
    return sortie


def roles_profiles(md: str) -> dict[str, str]:
    """{« Speaker N » : rôle écrit par le profilage}, depuis le front-matter."""
    return {loc: role.strip() for loc, role in _ROLE.findall(md)}


# ---------- Mesures ----------

def mots_normalises(texte: str) -> list[str]:
    texte = texte.lower().replace("-", " ")
    return [w for w in re.findall(r"[a-z0-9']+", texte)
            if w.strip("'") and w not in HESITATIONS]


def taux_erreur_mots(reference: list[str], hypothese: list[str]) -> float:
    """Distance d'édition sur les mots, rapportée à la longueur de la référence."""
    if not reference:
        raise ValueError("référence vide : le WER n'a pas de sens")
    precedente = list(range(len(hypothese) + 1))
    for i, r in enumerate(reference, 1):
        courante = [i] + [0] * len(hypothese)
        for j, h in enumerate(hypothese, 1):
            courante[j] = min(precedente[j] + 1, courante[j - 1] + 1,
                              precedente[j - 1] + (r != h))
        precedente = courante
    return precedente[-1] / len(reference)


def chevauchements(segs: list[Segment], mots: list[Mot]) -> dict[str, dict[str, float]]:
    """Secondes de parole partagées entre chaque locuteur diarisé et chaque participant."""
    table: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for seg in segs:
        for mot in mots:
            commun = min(seg.fin, mot.fin) - max(seg.debut, mot.debut)
            if commun > 0:
                table[seg.locuteur][mot.participant] += commun
    return {loc: dict(parts) for loc, parts in table.items()}


def rattachement(table: dict[str, dict[str, float]]) -> dict[str, tuple[str, float, float]]:
    """{locuteur : (participant dominant, pureté, secondes)} pour les locuteurs jugeables."""
    sortie = {}
    for loc, parts in table.items():
        total = sum(parts.values())
        if total < MINIMUM_S:
            continue
        dominant = max(parts, key=parts.get)
        sortie[loc] = (dominant, parts[dominant] / total, total)
    return sortie


# ---------- Exécution du pipeline, une fois pour le module ----------

@pytest.fixture(scope="module")
def corpus() -> Path:
    from tools import corpus_demo

    for nom in (MEDIA, ANNOTATIONS):
        if not (corpus_demo.DOSSIER / nom).is_file():
            pytest.skip(f"corpus de démo absent : {nom} — lancer `python tools/corpus_demo.py`")
    return corpus_demo.DOSSIER


@pytest.fixture(scope="module")
def reference(corpus) -> list[Mot]:
    mots = mots_de_reference(corpus / ANNOTATIONS, REUNION, DEBUT_S, DUREE_S)
    # Ancre : la fenêtre porte bien la réunion, ses quatre participants compris.
    assert len(mots) > 1000, len(mots)
    assert {m.participant for m in mots} == set(ROLES_AMI)
    return mots


@pytest.fixture(scope="module")
def transcript_md(corpus, tmp_path_factory) -> str:
    from core import config, llm

    config.load_env()
    if not config.HF_TOKEN:
        pytest.skip("HF_TOKEN absent — diarisation impossible")
    if not llm.is_available(provider=llm.FOURNISSEUR_DEFAUT):
        pytest.skip(f"LLM local ({llm.FOURNISSEUR_DEFAUT}) injoignable — profilage impossible")
    pytest.importorskip("mlx_whisper", reason="moteur Whisper absent")

    from core import pipeline, projects
    from tools import corpus_demo

    travail = tmp_path_factory.mktemp("ami")
    extrait = corpus_demo.extrait(MEDIA, DEBUT_S, DUREE_S, travail, dossier=corpus)
    # Dossier de sortie jetable, avec son manifeste : `pipeline.run()` refuse
    # d'écrire dans un dossier qui n'est pas un projet.
    sortie = travail / "output"
    sortie.mkdir()
    projects.ensure_inbox(sortie)
    out, texte = pipeline.run(extrait, engine_name="whisper", language=None,
                              timestamps=True, diarize_on=True, profile_on=True,
                              llm_provider=llm.FOURNISSEUR_DEFAUT, output_dir=sortie)
    assert sortie in out.parents, f"le test a écrit hors de son dossier jetable : {out}"
    return texte


@pytest.fixture(scope="module")
def segs(transcript_md) -> list[Segment]:
    s = segments(transcript_md)
    # Ancre : sans segments attribués, toutes les mesures ci-dessous seraient vides.
    assert len(s) > 50, len(s)
    return s


def _front_matter(md: str) -> dict:
    import yaml
    return yaml.safe_load(md.split("---", 2)[1]) or {}


# ---------- Le texte ----------

def test_langue_anglaise(transcript_md):
    assert _front_matter(transcript_md).get("language") == "en"


def test_le_texte_suit_la_transcription_manuelle(reference, segs):
    ref = [w for m in reference for w in mots_normalises(m.texte)]
    hyp = [w for s in segs for w in mots_normalises(s.texte)]
    wer = taux_erreur_mots(ref, hyp)
    assert wer <= SEUIL_WER, f"WER {wer:.3f} > {SEUIL_WER} ({len(ref)} mots de référence)"


# ---------- Les locuteurs ----------

def test_quatre_locuteurs(transcript_md):
    assert _front_matter(transcript_md).get("n_speakers") == len(ROLES_AMI)


def test_chaque_locuteur_est_un_participant_et_un_seul(reference, segs):
    rattaches = rattachement(chevauchements(segs, reference))
    # Ancre : les quatre participants parlent assez pour être jugés.
    assert len(rattaches) == len(ROLES_AMI), rattaches
    impurs = {loc: round(p, 2) for loc, (_, p, _) in rattaches.items() if p < SEUIL_PURETE}
    assert not impurs, f"locuteurs qui mélangent des participants : {impurs} — {rattaches}"
    participants = [part for part, _, _ in rattaches.values()]
    assert len(set(participants)) == len(participants), (
        f"deux locuteurs tombent sur le même participant : {rattaches}")


def test_le_chef_de_projet_parle_le_plus_et_le_signal_le_retrouve(reference, segs):
    """Ancré dans le signal seul : ni le texte ni le LLM n'interviennent."""
    temps = defaultdict(float)
    for m in reference:
        temps[m.participant] += m.fin - m.debut
    assert max(temps, key=temps.get) == "A", f"référence : {dict(temps)}"

    rattaches = rattachement(chevauchements(segs, reference))
    plus_bavard = max(rattaches, key=lambda loc: rattaches[loc][2])
    assert rattaches[plus_bavard][0] == "A", (
        f"le locuteur qui parle le plus ({plus_bavard}) n'est pas le chef de projet : "
        f"{rattaches}")


# ---------- Les rôles ----------

def _role_ami(role_profile: str) -> str | None:
    return next((code for code, motif in MOTIFS_ROLES.items() if motif.search(role_profile)), None)


def test_le_role_de_chef_de_projet_tombe_sur_le_bon_participant(transcript_md, reference, segs):
    """LE garde-fou contre l'inversion des rôles : rôle du LLM contre signal."""
    roles = roles_profiles(transcript_md)
    rattaches = rattachement(chevauchements(segs, reference))
    chefs = [loc for loc, role in roles.items() if _role_ami(role) == "PM"]
    assert len(chefs) == 1, f"un seul chef de projet attendu — rôles : {roles}"
    assert chefs[0] in rattaches, f"{chefs[0]} ne parle pas assez pour être jugé : {rattaches}"
    assert rattaches[chefs[0]][0] == "A", (
        f"le rôle de chef de projet est sur {chefs[0]}, qui est le participant "
        f"{rattaches[chefs[0]][0]} ({ROLES_AMI[rattaches[chefs[0]][0]]}) — rôles : {roles}")


def test_au_moins_trois_roles_sur_quatre_tombent_sur_le_bon_participant(
    transcript_md, reference, segs,
):
    roles = roles_profiles(transcript_md)
    rattaches = rattachement(chevauchements(segs, reference))
    # Ancre : le profilage a bien écrit un rôle par locuteur.
    assert len(roles) >= len(ROLES_AMI), roles
    justes = [loc for loc, role in roles.items()
              if loc in rattaches and _role_ami(role) == ROLES_AMI[rattaches[loc][0]]]
    assert len(justes) >= 3, (
        f"rôles justes : {justes} — rôles : {roles} — rattachements : {rattaches}")
