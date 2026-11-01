"""La règle d'envoi passe par la façade : chaque appel du cœur dit ce qu'il envoie.

`sensitivity.verifier_envoi` refuse l'ultra-confidentiel vers le cloud sans
l'autorisation de son propriétaire. Les routes la consultent ; la façade
`llm.chat`/`llm.chat_json` la réapplique juste avant le moteur, à partir de
`envoi=`. Un appel du cœur sans `envoi=` passerait sans contrôle : on lit donc le
CODE, au motif de `test_graine_du_tronc.py`.

Deux règles vérifiées sur l'AST de `core/` :

1. chaque appel `llm.chat(...)` ou `llm.chat_json(...)` porte le mot-clé `envoi` ;
2. tout appel direct à un moteur ou à un client cloud hors des modules de moteur
   (`core/llm_*.py`, `core/stt_*.py`, et la façade `core/llm.py`) est inscrit dans
   `APPELS_DIRECTS` avec sa raison. La liste se vérifie dans les deux sens : un
   appel nouveau doit y entrer, une entrée sans appel doit en sortir. Les fichiers
   exclus de l'export (`tests/hors_export.py`) ont leur propre liste.

Et une règle de comportement : appelée en cloud sur de l'ultra-confidentiel sans
autorisation, la façade lève SANS appeler le moteur.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core import llm, sensitivity, telemetry
from tests.hors_export import exclus_de_l_export

pytestmark = pytest.mark.unit

RACINE = Path(__file__).resolve().parents[2] / "core"

# Les fichiers qui appellent la façade aujourd'hui. Ancre de non-vacuité : un
# balayage qui ne trouverait plus aucun appel passerait la règle 1 sur du vide.
APPELANTS_DE_LA_FACADE = {"report.py", "profiling.py", "rag.py", "ecl.py",
                          "experimental/stages.py"}

# Les appels directs à un moteur ou un client, hors des modules de moteur, et
# pourquoi la façade n'est pas leur porte. (fichier, appel tel qu'écrit) → raison.
APPELS_DIRECTS: dict[tuple[str, str], str] = {
    ("ecl.py", "moteur.chat_json"):
        "moteur MLX propre à l'ECL (Granite), toujours LOCAL (`ecl.moteur_local`) : rien ne sort",
    ("pipeline.py", "engine.transcribe"):
        "moteur de transcription ; la règle d'envoi est appliquée juste avant "
        "(`sensitivity.autoriser_envoi`), comme dans la façade",
}

_METHODES_DE_MOTEUR = ("chat", "chat_json", "transcribe", "complete")
_CLIENTS = ("mistralai", "openai", "anthropic", "httpx", "requests", "urllib")


def _module_de_moteur(chemin: Path) -> bool:
    return chemin.name == "llm.py" or chemin.name.startswith(("llm_", "stt_"))


def _sources(*, exportes: bool = True) -> list[Path]:
    """Les modules de `core/` : ceux qui partent à l'export, ou les autres."""
    exclus = exclus_de_l_export(RACINE.parent)
    return sorted(p for p in RACINE.rglob("*.py") if "__pycache__" not in p.parts
                  and (p.relative_to(RACINE.parent).as_posix() not in exclus) is exportes)


def _relatif(chemin: Path) -> str:
    return chemin.relative_to(RACINE).as_posix()


def _appels_de_la_facade(arbre: ast.AST):
    for noeud in ast.walk(arbre):
        if (isinstance(noeud, ast.Call) and isinstance(noeud.func, ast.Attribute)
                and noeud.func.attr in ("chat", "chat_json")
                and isinstance(noeud.func.value, ast.Name) and noeud.func.value.id == "llm"):
            yield noeud


def _appels_directs(arbre: ast.AST):
    """(texte de l'appel, ligne) des appels de moteur et des imports de client cloud."""
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Call) and isinstance(noeud.func, ast.Attribute):
            if noeud.func.attr in _METHODES_DE_MOTEUR and not (
                    isinstance(noeud.func.value, ast.Name) and noeud.func.value.id == "llm"):
                yield ast.unparse(noeud.func), noeud.lineno
        elif isinstance(noeud, (ast.Import, ast.ImportFrom)):
            modules = ([a.name for a in noeud.names] if isinstance(noeud, ast.Import)
                       else [noeud.module or ""])
            for module in modules:
                if module.split(".")[0] in _CLIENTS:
                    yield f"import {module}", noeud.lineno


def test_chaque_appel_de_la_facade_dans_le_coeur_porte_envoi() -> None:
    appelants: set[str] = set()
    sans_envoi: list[str] = []
    for chemin in _sources():
        if _module_de_moteur(chemin):
            continue
        for noeud in _appels_de_la_facade(ast.parse(chemin.read_text(encoding="utf-8"))):
            appelants.add(_relatif(chemin))
            if "envoi" not in {mot.arg for mot in noeud.keywords}:
                sans_envoi.append(f"{_relatif(chemin)}:{noeud.lineno}")
    assert appelants == APPELANTS_DE_LA_FACADE, "liste des appelants à revoir"
    assert sans_envoi == [], (
        f"appel(s) à la façade sans `envoi=` : {sans_envoi}. Passe le document envoyé "
        f"(`library.envoi_du_document`), ou `envoi=None` en le justifiant si aucun "
        f"document n'est envoyé.")


def test_chaque_appel_direct_a_un_moteur_est_inscrit_avec_sa_raison() -> None:
    trouves: dict[tuple[str, str], list[int]] = {}
    for chemin in _sources():
        if _module_de_moteur(chemin):
            continue
        for appel, ligne in _appels_directs(ast.parse(chemin.read_text(encoding="utf-8"))):
            trouves.setdefault((_relatif(chemin), appel), []).append(ligne)
    assert len(trouves) >= len(APPELS_DIRECTS) > 0
    nouveaux = sorted(f"{f}: {a} (l. {trouves[(f, a)]})" for f, a in trouves.keys() - APPELS_DIRECTS.keys())
    perimes = sorted(f"{f}: {a}" for f, a in APPELS_DIRECTS.keys() - trouves.keys())
    assert nouveaux == [], (f"appel direct à un moteur ou client cloud hors façade : {nouveaux}. "
                            "Passe par `llm.chat`/`llm.chat_json` avec `envoi=`, ou inscris-le "
                            "dans `APPELS_DIRECTS` avec sa raison.")
    assert perimes == [], f"entrée(s) sans appel : retire-les de `APPELS_DIRECTS` : {perimes}"
    for cle, raison in APPELS_DIRECTS.items():
        assert len(raison) > 30, f"{cle} : la raison doit être une phrase"


# ---------- La façade elle-même ----------

class _FauxMoteur:
    """Un moteur cloud qui note chaque appel, sans rien envoyer."""

    def __init__(self, nom: str) -> None:
        self.name = nom
        self.appels: list[str] = []

    def chat(self, system, user, **_k):
        self.appels.append(user)
        return "réponse"

    def chat_json(self, system, user, **_k):
        self.appels.append(user)
        return {"ok": True}


@pytest.fixture
def moteurs(monkeypatch):
    faux = {nom: _FauxMoteur(nom) for nom in ("mistral", "openai", "anthropic", "mlx")}
    for nom, moteur in faux.items():
        monkeypatch.setitem(llm.MOTEURS, nom, moteur)
    monkeypatch.setattr(sensitivity, "_resolveur", sensitivity._jamais)
    return faux


ULTRA = sensitivity.Envoi(sensibilite="ultra_confidential", proprietaire="alice", document_id="D1")
CLOUD = ("mistral", "cloud", "openai", "anthropic")


@pytest.mark.parametrize("fournisseur", CLOUD)
@pytest.mark.parametrize("appel", ("chat", "chat_json"))
def test_en_cloud_sans_autorisation_la_facade_leve_sans_appeler_le_moteur(moteurs, fournisseur, appel):
    with pytest.raises(sensitivity.EnvoiRefuse) as refus:
        getattr(llm, appel)("système", "TEXTE-SECRET", provider=fournisseur, envoi=ULTRA)
    assert str(refus.value) == sensitivity.REFUS_CLOUD
    assert all(m.appels == [] for m in moteurs.values()), "le moteur a été appelé"


@pytest.mark.parametrize("fournisseur", CLOUD)
def test_l_autorisation_est_celle_du_proprietaire_et_relue_a_chaque_appel(moteurs, fournisseur):
    autorises = {"alice"}
    sensitivity.enregistrer_resolveur(lambda proprietaire: proprietaire in autorises)
    try:
        assert llm.chat("s", "TEXTE", provider=fournisseur, envoi=ULTRA) == "réponse"
        cible = moteurs[llm.moteur(fournisseur).name]
        assert cible.appels == ["TEXTE"]
        # Un autre propriétaire, sans réglage : refusé, même dans le même processus.
        bruno = sensitivity.Envoi(sensibilite="ultra_confidential", proprietaire="bruno")
        with pytest.raises(sensitivity.EnvoiRefuse):
            llm.chat_json("s", "AUTRE", provider=fournisseur, envoi=bruno)
        # Désactiver vaut aussitôt : l'appel suivant est refusé.
        autorises.clear()
        with pytest.raises(sensitivity.EnvoiRefuse):
            llm.chat("s", "APRES", provider=fournisseur, envoi=ULTRA)
        assert cible.appels == ["TEXTE"]
    finally:
        sensitivity.enregistrer_resolveur(None)


def test_un_seul_passage_refuse_arrete_tout_l_envoi(moteurs):
    sensitivity.enregistrer_resolveur(lambda proprietaire: proprietaire == "alice")
    try:
        bruno = sensitivity.Envoi(sensibilite="ultra_confidential", proprietaire="bruno")
        standard = sensitivity.Envoi(sensibilite="standard", proprietaire="carole")
        with pytest.raises(sensitivity.EnvoiRefuse):
            llm.chat("s", "TEXTE", provider="openai", envoi=[standard, ULTRA, bruno])
        assert moteurs["openai"].appels == []
        assert llm.chat("s", "TEXTE", provider="openai", envoi=[standard, ULTRA]) == "réponse"
    finally:
        sensitivity.enregistrer_resolveur(None)


def test_le_local_et_le_standard_ne_demandent_aucune_autorisation(moteurs):
    assert llm.chat("s", "TEXTE", provider="mlx", envoi=ULTRA) == "réponse"
    assert llm.chat_json("s", "TEXTE", provider="openai",
                         envoi=sensitivity.Envoi(sensibilite="confidential")) == {"ok": True}
    assert llm.chat("s", "TEXTE", provider="openai", envoi=None) == "réponse"
    assert moteurs["mlx"].appels == ["TEXTE"] and moteurs["openai"].appels == ["TEXTE", "TEXTE"]


def test_un_resolveur_en_panne_ou_un_proprietaire_absent_vaut_non(moteurs):
    def en_panne(_proprietaire):
        raise RuntimeError("base illisible")

    sensitivity.enregistrer_resolveur(en_panne)
    try:
        with pytest.raises(sensitivity.EnvoiRefuse):
            llm.chat("s", "TEXTE", provider="openai", envoi=ULTRA)
    finally:
        sensitivity.enregistrer_resolveur(None)
    sensitivity.enregistrer_resolveur(lambda _p: True)
    try:
        sans_proprietaire = sensitivity.Envoi(sensibilite="ultra_confidential", proprietaire=None)
        with pytest.raises(sensitivity.EnvoiRefuse):
            llm.chat("s", "TEXTE", provider="openai", envoi=sans_proprietaire)
        # Une valeur « vraie » qui n'est pas `True` n'autorise pas.
        sensitivity.enregistrer_resolveur(lambda _p: "oui")
        with pytest.raises(sensitivity.EnvoiRefuse):
            llm.chat("s", "TEXTE", provider="openai", envoi=ULTRA)
    finally:
        sensitivity.enregistrer_resolveur(None)
    assert moteurs["openai"].appels == []


def test_un_envoi_autorise_marque_l_execution_et_ecrit_une_ligne_sans_contenu(moteurs, caplog):
    sensitivity.enregistrer_resolveur(lambda _p: True)
    try:
        with caplog.at_level("INFO", logger="memoglyph.envoi"), \
                telemetry.execution(kind="assistant") as execution:
            llm.chat("s", "TEXTE-SECRET", provider="openai", envoi=ULTRA)
            llm.chat("s", "TEXTE-SECRET", provider="openai", envoi=ULTRA)
            assert execution.execution_row()["envoi_ultra_autorise"] == 1
    finally:
        sensitivity.enregistrer_resolveur(None)
    lignes = [r.getMessage() for r in caplog.records if r.name == "memoglyph.envoi"]
    assert len(lignes) == 1, "une ligne par document et par exécution, pas une par appel"
    assert "compte=alice" in lignes[0] and "fournisseur=openai" in lignes[0] and "D1" in lignes[0]
    assert "TEXTE-SECRET" not in lignes[0]


def test_sans_envoi_ultra_l_execution_n_est_pas_marquee(moteurs):
    with telemetry.execution(kind="assistant") as execution:
        llm.chat("s", "TEXTE", provider="openai", envoi=sensitivity.Envoi(sensibilite="standard"))
        llm.chat("s", "TEXTE", provider="mlx", envoi=ULTRA)
        assert execution.execution_row()["envoi_ultra_autorise"] == 0


def test_la_regle_unique_et_son_cas_non_autorise() -> None:
    sensitivity.verifier_envoi("ultra_confidential", local=False, autorise=True)
    sensitivity.verifier_envoi("ultra_confidential", local=True)
    sensitivity.verifier_envoi("confidential", local=False)
    with pytest.raises(sensitivity.EnvoiRefuse):
        sensitivity.verifier_envoi("ultra_confidential", local=False)   # autorisation oubliée : non
    assert sensitivity.cloud_refuse("ultra_confidential", local=False)
    assert not sensitivity.cloud_refuse("ultra_confidential", local=True)
    assert not issubclass(sensitivity.EnvoiRefuse, (OSError, llm.LLMError))
