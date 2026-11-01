"""Les garde-fous d'identité sont-ils réellement APPELÉS en production ?

Motif à traquer : **la règle est testée, le câblage jamais.**

`core/names.py` se déclare « pur (aucune I/O) : source unique de la règle ».
C'est cette pureté qui a permis de tester la règle vingt et une fois sans
jamais tester son appel — et les tests qui pourraient couvrir le chemin de
production le MONKEYPATCHENT.

Mesuré avant d'écrire ce fichier : débrancher `names.strip_speaker_bindings`
(`core/report.py:484`) ou `names.sanitize_roles` (`core/profiling.py:220`)
laissait **889 tests verts** et `ruff` satisfait — l'import étant couvert par un
`# noqa: F401`. Le compte rendu publiait alors « Speaker 10 (Léa
Marchand/Iris) » et le `.md` affichait « **Speaker 1 — guest (Lina)** ».

Ces deux garde-fous existent parce que le modèle RATTACHE des noms aux
étiquettes de diarisation — une inférence que la source ne porte pas. Publier
un nom propre inventé sur une réunion réelle est la faute la plus grave que ce
produit puisse commettre.

## Critère d'acceptation de ces tests

Chacun doit TOMBER quand on remplace l'appel par un no-op. C'est vérifié par
mutation, pas supposé : un test de câblage qui survit au débranchement ne teste
pas le câblage.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core import profiling
from core.stt_base import Segment, Transcript


def _front_matter(contenu: str) -> dict:
    import yaml
    return yaml.safe_load(contenu.split("---", 2)[1]) or {}


def _transcript_deux_locuteurs() -> Transcript:
    return Transcript(
        segments=[
            Segment(0.0, 30.0, "Bonjour, merci d'être venu aujourd'hui.", "Speaker 1"),
            Segment(30.0, 90.0, "Merci à vous, je suis ravi d'être là.", "Speaker 2"),
        ],
        full_text="Bonjour, merci d'être venu aujourd'hui. Merci à vous.",
        language="fr",
    )


# ---------- `profiling.profile` doit assainir les rôles ----------

# Seule la forme PARENTHÈSE est reconnue par `names.name_from_role` — vérifié :
#   'guest (Lina)'                 → 'Lina'
#   'candidat — Marc Petit'          → None
#   'interviewer (Claire Morel / RH)' → None
#
# Une première version testait les trois et échouait sur deux : c'était
# l'hypothèse qui était fausse, pas le garde. On teste ce que le garde fait, et
# la couverture de l'extracteur est un sujet distinct (signalé, pas mélangé).
@pytest.mark.parametrize("role_pollue,attendu", [
    ("guest (Lina)", "guest"),
    ("interviewer (Claire Morel)", "interviewer"),
    ("candidat (Marc Petit)", "candidat"),
])
def test_profile_retire_le_nom_propre_colle_au_role(
        monkeypatch, role_pollue: str, attendu: str) -> None:
    """Le LLM colle un nom au rôle ; `profile` doit le retirer AVANT publication.

    On exerce `profiling.profile` — la fonction APPELANTE — et non
    `names.sanitize_roles`, qui est déjà couverte par ailleurs. C'est toute la
    différence entre tester une règle et tester qu'elle s'applique.
    """
    monkeypatch.setattr(profiling.llm, "is_available", lambda **k: True)
    monkeypatch.setattr(profiling.llm, "chat_json", lambda *a, **k: {
        "summary": "Un entretien.",
        "speakers": {
            "Speaker 1": {"role": role_pollue},
            "Speaker 2": {"role": role_pollue},
        },
    })

    profil = profiling.profile(_transcript_deux_locuteurs(), diarized=True)

    roles = [
        (donnees or {}).get("role")
        for donnees in (profil.get("speakers") or {}).values()
    ]
    assert roles, f"aucun locuteur dans le profil : {profil}"
    for role in roles:
        assert role == attendu, (
            f"rôle publié « {role} » — le nom propre n'a pas été retiré. "
            f"`names.sanitize_roles` est-il encore appelé dans "
            f"`core/profiling.py` ?")


def test_profile_conserve_un_role_LEGITIME(monkeypatch) -> None:
    """Garde sur l'instrument : un assainisseur qui vide tout passerait le test
    précédent en ne publiant plus aucun rôle. Il doit couper, pas raser."""
    monkeypatch.setattr(profiling.llm, "is_available", lambda **k: True)
    monkeypatch.setattr(profiling.llm, "chat_json", lambda *a, **k: {
        "summary": "Un entretien.",
        "speakers": {
            "Speaker 1": {"role": "recruteuse"},
            "Speaker 2": {"role": "candidat"},
        },
    })

    profil = profiling.profile(_transcript_deux_locuteurs(), diarized=True)
    roles = {
        (donnees or {}).get("role")
        for donnees in (profil.get("speakers") or {}).values()
    }
    assert roles == {"recruteuse", "candidat"}, roles


# ---------- `report.make_report` doit retirer les rattachements ----------

@pytest.fixture
def corpus_avec_transcript(isolated_stores, monkeypatch):
    """Un transcript réel dans un corpus jetable, prêt pour `make_report`.

    L'indexation sémantique est neutralisée : elle charge e5-large et coûtait
    30 à 40 SECONDES par test — la suite rapide passait de 30 s à 122 s.

    Marquer ces tests `slow` aurait été la mauvaise réponse : la CI les exclut,
    ils seraient devenus des portes mortes le jour où l'on corrige les portes
    mortes. On retire le poids qui n'a rien à voir avec le câblage testé.
    L'indexation a ses propres tests.
    """
    from core import docid, library, projects, semantic

    monkeypatch.setattr(semantic, "index_name", lambda *a, **k: None)

    projects.ensure_inbox(isolated_stores["output_dir"])
    projet = projects.load(isolated_stores["output_dir"]).id
    nom = "entretien.md"
    (Path(isolated_stores["output_dir"]) / nom).write_text(
        f"---\nid: {docid.nouveau()}\nmeeting_id: {docid.nouveau()}\n"
        f"project_id: {projet}\ntitle: entretien\nlanguage: fr\n---\n\n"
        "**Speaker 1** [00:00:00] Bonjour, merci d'être venu aujourd'hui.\n\n"
        "**Speaker 2** [00:00:30] Merci à vous, je suis ravi d'être là.\n",
        encoding="utf-8")
    library.reindex()
    return nom


def _sans_llm(monkeypatch, report_mod) -> None:
    """Neutralise le VRAI chemin LLM. Trouvé en cherchant une lenteur.

    Avec `provider="ollama"` — le défaut — `make_report` passait par
    `llm_ollama` et non par le module générique `llm` : mes premiers bouchons
    portaient sur `report_mod.llm` et n'étaient donc jamais utilisés. Les tests
    appelaient le vrai modèle local, 20 à 30 SECONDES d'inférence chacun, et
    auraient échoué en CI, où aucun Ollama ne tourne.

    Une lenteur inexpliquée méritait d'être creusée plutôt que contournée en
    marquant les tests `slow` — ce qui les aurait exclus de la CI et rendus
    morts, le motif même qu'on corrige ici.

    Le double bouchon a disparu avec sa cause : `make_report` n'a plus qu'une
    porte. Si un contournement réapparaissait, ces tests redeviendraient lents
    — et `test_llm_interface.py` le dirait, lui, en refusant l'import direct.
    """
    from core import semantic

    monkeypatch.setattr(report_mod.llm, "is_available", lambda **k: True)
    monkeypatch.setattr(report_mod.llm, "chat_json", lambda *a, **k: {"sections": []})
    # `chat` MANQUAIT, et le défaut ne s'est vu qu'au changement de fournisseur.
    #
    # `classify_genre` passe par `llm.chat`, resté sans bouchon. Tant que le
    # défaut était `ollama`, l'appel échouait vite sur un démon absent et
    # personne ne le remarquait. Depuis que le défaut est `mlx`, le
    # même appel DÉMARRE un serveur de 2,1 Go sur le port de PRODUCTION — et
    # l'y laisse, ce qui casse le mode local de l'utilisateur en silence.
    #
    # Trouvé par le témoin de `conftest.py`, qui a nommé ces trois tests.
    monkeypatch.setattr(report_mod.llm, "chat", lambda *a, **k: "generique")
    # L'indexation charge e5-large ; elle a ses propres tests.
    monkeypatch.setattr(semantic, "index_name", lambda *a, **k: None)


def test_make_report_retire_les_noms_rattaches_aux_locuteurs(
        monkeypatch, corpus_avec_transcript) -> None:
    """Le modèle écrit « Speaker 10 (Léa Marchand/Iris) ». La source ne le dit
    pas — c'est une inférence, et elle ne doit pas être publiée.

    Ce test exerce `make_report`, pas `names.strip_speaker_bindings`.
    """
    from core import report as report_mod

    pollue = (
        "## Décisions\n\n"
        "- **Speaker 1 (Claire Morel)** valide le recrutement.\n"
        "- **Speaker 2 (Marc Petit/le candidat)** accepte l'offre.\n"
    )
    # On bouchonne le RENDU des citations, pas le garde.
    #
    # `cr_body` sort de `report_citations.render_structured`, dont le contrat
    # est strict (sections numérotées, preuves horodatées). Le reproduire ici
    # testerait le renderer, pas le câblage. En rendant directement un corps
    # pollué, on place le garde exactement là où il est en production : entre
    # ce que le modèle a produit et ce que l'utilisateur lira.
    _sans_llm(monkeypatch, report_mod)
    monkeypatch.setattr(report_mod.report_citations, "render_structured",
                        lambda *a, **k: pollue)

    resultat = report_mod.make_report(corpus_avec_transcript)
    contenu = resultat["content"]
    meta = _front_matter(contenu)

    # L'assertion PORTANTE est sur la métadonnée, pas sur l'absence des noms
    # dans le corps.
    #
    # Une première version se contentait de « Claire Morel n'apparaît pas ». Elle
    # passait — mais l'audit de citations réécrit le corps quand aucune
    # affirmation n'est étayée par la transcription, et le corps rendu ne
    # contenait plus RIEN de la sortie du modèle. Le test aurait été vert avec
    # le garde débranché : il constatait une absence produite par un autre
    # mécanisme.
    retires = meta.get("removed_speaker_bindings")
    assert retires, (
        "aucun rattachement déclaré retiré — `names.strip_speaker_bindings` "
        f"est-il encore appelé dans `core/report.py` ? méta : {sorted(meta)}")
    assert any("Claire Morel" in str(r) for r in retires), retires
    assert any("Marc Petit" in str(r) for r in retires), retires

    # Dans la PROSE, au-dessus du marqueur d'avis générés.
    #
    # Deux endroits republient légitimement les noms retirés : le front-matter
    # (`removed_speaker_bindings`) et l'avis `_bindings_notice`, sous
    # `GENERATED_NOTICE_MARKER`. C'est « rien ne disparaît en silence »
    # appliqué : le lecteur apprend ce qui a été écarté.
    #
    # Deux assertions antérieures — fichier entier, puis corps entier —
    # confondaient « affirmé par le compte rendu » et « consigné comme
    # retiré ». Ce qui compte est que le modèle ne puisse pas AFFIRMER
    # « Speaker 1 est Claire Morel » dans le texte que l'utilisateur lit.
    from core.report_citations import GENERATED_NOTICE_MARKER
    prose = contenu.split("---", 2)[2].split(GENERATED_NOTICE_MARKER)[0]
    for nom in ("Claire Morel", "Marc Petit"):
        assert nom not in prose, (
            f"« {nom} » AFFIRMÉ dans la prose du compte rendu : {prose[:300]}")


def test_make_report_DECLARE_ce_qu_il_a_retire(
        monkeypatch, corpus_avec_transcript) -> None:
    """« Rien ne disparaît en silence » — invariant du projet.

    Retirer un rattachement sans le dire produirait un compte rendu qui semble
    complet. Le front-matter doit porter `removed_speaker_bindings`.
    """
    from core import report as report_mod

    _sans_llm(monkeypatch, report_mod)
    monkeypatch.setattr(report_mod.report_citations, "render_structured",
                        lambda *a, **k: "- **Speaker 1 (Claire Morel)** valide.\n")

    # Le front-matter du COMPTE RENDU, pas celui du transcript : `make_report`
    # écrit un fichier distinct. Ma première version interrogeait le transcript
    # et n'y trouvait évidemment rien.
    resultat = report_mod.make_report(corpus_avec_transcript)
    meta = _front_matter(resultat["content"])
    retires = meta.get("removed_speaker_bindings")
    assert retires, (
        f"le retrait n'est pas déclaré dans le front-matter : {sorted(meta)}")
    assert any("Claire Morel" in str(r) for r in retires), retires


def test_un_compte_rendu_PROPRE_ne_declare_aucun_retrait(
        monkeypatch, corpus_avec_transcript) -> None:
    """Garde sur l'instrument : si `removed_speaker_bindings` était toujours
    rempli, le test précédent serait vert sans rien prouver."""
    from core import report as report_mod

    _sans_llm(monkeypatch, report_mod)
    monkeypatch.setattr(report_mod.report_citations, "render_structured",
                        lambda *a, **k: "- **Speaker 1** valide le recrutement.\n")

    resultat = report_mod.make_report(corpus_avec_transcript)
    meta = _front_matter(resultat["content"])
    assert not meta.get("removed_speaker_bindings"), meta.get(
        "removed_speaker_bindings")
