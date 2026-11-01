"""« No telemetry » : seuls des modules nommés, chacun pour une raison dite, ouvrent le réseau.

La promesse du README : l'usage est noté en local (`core/telemetry.py`, `usage.db`) ;
rien ne part ailleurs qu'aux fournisseurs choisis et, au premier usage, aux
téléchargements de modèles depuis Hugging Face.

Ce test la garde vraie sur le code de Memoglyph, sans réseau ni modèle :

- les imports (lus par `ast`, imbriqués compris) de `core/`, `api/`, `tools/` et
  `scripts/` ne tirent un client réseau que dans les modules de `AUTORISES` (les
  fichiers exclus de l'export, `tests/hors_export.py`, ont leur propre liste) ;
- chaque entrée de la liste importe encore un client : elle reste fermée et juste ;
- le puits de télémétrie et le magasin d'usage n'en importent aucun ;
- `ui/src` ne contient aucune URL absolue hors de l'API locale.

Ce qu'il ne voit pas : un import dynamique (`importlib`), et le réseau qu'ouvrent
elles-mêmes les bibliothèques de modèles (pyannote, sentence-transformers, mlx-whisper,
mlx-lm), qui téléchargent leurs poids depuis Hugging Face au premier usage.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests.hors_export import exclus_de_l_export

RACINE = Path(__file__).resolve().parents[2]
DOSSIERS_PYTHON = ("core", "api", "tools", "scripts")

CLIENTS_RESEAU = (
    "httpx", "requests", "aiohttp", "urllib", "urllib3", "http.client", "socket",
    "websockets", "huggingface_hub", "openai", "mistralai", "anthropic",
)
# Découpe et assemblage d'URL, sans aucune entrée-sortie.
PAS_UN_CLIENT = frozenset({"urllib.parse"})

# La liste fermée : le module, et pourquoi il importe un client réseau.
AUTORISES = {
    # Moteurs des fournisseurs, appelés seulement pour le fournisseur choisi.
    "core/llm_anthropic.py": "moteur Anthropic : l'API Messages, par httpx",
    "core/llm_client_openai.py": "client OpenAI partagé par les moteurs openai, mlx et compatible",
    "core/llm_compatible.py": "moteur « Serveur compatible OpenAI » : l'hôte de COMPATIBLE_BASE_URL",
    "core/llm_mistral.py": "moteur Mistral (Chat API)",
    "core/llm_mlx.py": "mlx_lm.server local : sonde de port (socket) et client OpenAI vers lui",
    "core/llm_openai.py": "moteur OpenAI (Responses API)",
    "core/stt_mistral.py": "transcription Voxtral, chez Mistral",
    # Modèles : lecture du cache local de Hugging Face, jamais un envoi.
    "core/ecl.py": "try_to_load_from_cache : le modèle des faits est-il déjà dans le cache local",
    "core/pipeline.py": "try_to_load_from_cache : téléchargement ou simple chargement du modèle de CR",
    "core/stt_whisper.py": "try_to_load_from_cache : annoncer le téléchargement de Whisper au premier usage",
    # Outils.
    "tools/sauvegarder.py": "socket : connect_ex sur 127.0.0.1, refuser de tourner si Memoglyph tourne",
    "tools/corpus_demo.py": "urllib.request : télécharger à la demande le corpus public AMI de démonstration",
}

# Le puits d'usage et son magasin : tout reste sur la machine.
USAGE_LOCAL = ("core/telemetry.py", "core/usage_store.py", "core/usage_cost.py")

URL_ABSOLUE = re.compile(r"https?://[^\s'\"`)<>]+")
# L'API locale, et les espaces de noms XML des SVG, qui ne se chargent jamais.
URL_PERMISE = re.compile(r"https?://(localhost|127\.0\.0\.1|\[::1\])([:/]|$)|http://www\.w3\.org/")


def _client(nom: str) -> bool:
    if any(nom == n or nom.startswith(n + ".") for n in PAS_UN_CLIENT):
        return False
    return any(nom == c or nom.startswith(c + ".") for c in CLIENTS_RESEAU)


def imports_reseau(source: str) -> set[str]:
    """Les clients réseau qu'importe ce source, à toute profondeur (fonctions comprises)."""
    trouves: set[str] = set()
    for noeud in ast.walk(ast.parse(source)):
        if isinstance(noeud, ast.Import):
            trouves |= {a.name for a in noeud.names if _client(a.name)}
        elif isinstance(noeud, ast.ImportFrom) and noeud.module and noeud.level == 0:
            for a in noeud.names:
                complet = f"{noeud.module}.{a.name}"
                if not _client(complet):
                    continue
                # `from http import client`, `from urllib import request` : le sous-module.
                parent = noeud.module not in ("http", "urllib") and _client(noeud.module)
                trouves.add(noeud.module if parent else complet)
    return trouves


def _modules_python(*, exportes: bool = True) -> list[Path]:
    """Les modules des dossiers balayés : ceux qui partent à l'export, ou les autres."""
    exclus = exclus_de_l_export(RACINE)
    return sorted(p for d in DOSSIERS_PYTHON if (RACINE / d).is_dir()
                  for p in (RACINE / d).rglob("*.py")
                  if (p.relative_to(RACINE).as_posix() not in exclus) is exportes)


def _reseau_par_module(*, exportes: bool = True) -> dict[str, set[str]]:
    vus = {}
    for p in _modules_python(exportes=exportes):
        clients = imports_reseau(p.read_text(encoding="utf-8"))
        if clients:
            vus[p.relative_to(RACINE).as_posix()] = clients
    return vus


def test_les_dossiers_scannes_existent():
    for d in ("core", "api", "tools"):
        assert (RACINE / d).is_dir(), d
    assert len(_modules_python()) > 50


@pytest.mark.parametrize("source, attendu", [
    ("import httpx", {"httpx"}),
    ("import socket as s", {"socket"}),
    ("from http import client", {"http.client"}),
    ("import http.client", {"http.client"}),
    ("from urllib import request", {"urllib.request"}),
    ("import urllib.request", {"urllib.request"}),
    ("def f():\n    from huggingface_hub import hf_hub_download\n", {"huggingface_hub"}),
    ("from mistralai.client import Mistral", {"mistralai.client"}),
    ("import anthropic, openai", {"anthropic", "openai"}),
    ("from urllib.parse import urlparse", set()),
    ("from urllib import parse", set()),
    ("import json, sqlite3\nfrom . import httpx_local", set()),
    ("from http import HTTPStatus", set()),
])
def test_le_detecteur_voit_chaque_forme_d_import(source, attendu):
    assert imports_reseau(source) == attendu


def test_seuls_les_modules_de_la_liste_fermee_importent_un_client_reseau():
    vus = _reseau_par_module()
    # Ancre : le balayage voit au moins les moteurs de la liste.
    assert len(vus) >= len(AUTORISES)
    assert "core/llm_mistral.py" in vus
    hors_liste = {m: sorted(c) for m, c in vus.items() if m not in AUTORISES}
    assert hors_liste == {}, (
        "Client réseau hors de la liste fermée (README, « No telemetry ») : "
        f"{hors_liste}. Un nouvel accès réseau entre dans AUTORISES avec sa raison, "
        "ou ne se fait pas.")


def test_chaque_entree_de_la_liste_importe_encore_un_client():
    vus = _reseau_par_module()
    assert len(vus) >= len(AUTORISES)
    mortes = sorted(m for m in AUTORISES if m not in vus)
    assert mortes == [], f"Entrées sans client réseau, à retirer de la liste : {mortes}"
    assert all(raison.strip() for raison in AUTORISES.values())


def test_la_telemetrie_et_le_magasin_d_usage_n_importent_aucun_client():
    for module in USAGE_LOCAL:
        source = (RACINE / module).read_text(encoding="utf-8")
        assert imports_reseau(source) == set(), module
        assert module not in AUTORISES


def test_l_interface_n_appelle_aucune_url_absolue_hors_de_l_api_locale():
    src = RACINE / "ui" / "src"
    assert src.is_dir()
    trouvees, lus, vues = [], 0, 0
    for p in sorted(src.rglob("*")):
        if not p.is_file() or "node_modules" in p.parts:
            continue
        try:
            texte = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        lus += 1
        for url in URL_ABSOLUE.findall(texte):
            vues += 1
            if not URL_PERMISE.match(url):
                trouvees.append(f"{p.relative_to(RACINE).as_posix()}: {url}")
    # Ancres : des fichiers lus, et un détecteur qui voit une URL semée et la refuse.
    # L'interface ne porte plus aucune URL absolue (les logos SVG du gabarit, seuls à
    # en avoir une, sont retirés) : `vues` peut valoir 0, le semis prouve le balayage.
    assert lus >= 50
    seme = 'fetch("https://telemetrie.example.com/e")'
    assert URL_ABSOLUE.findall(seme) == ["https://telemetrie.example.com/e"]
    assert not URL_PERMISE.match(URL_ABSOLUE.findall(seme)[0])
    assert vues >= 0
    assert trouvees == [], trouvees
