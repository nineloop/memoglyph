"""La charte tient-elle ses ratios, sur les fonds RÉELLEMENT employés ?

Un commentaire de jeton n'est pas une preuve : il vieillit sans bruit dès que
la valeur change. Ce témoin relit `ui/src/index.css`, déroule les chaînes de
`var()` et mesure. Il rougit si une paire texte/fond passe sous son seuil.

## Pourquoi calculer sur NOS fonds, et pas recopier une charte

Une charte publie ses ratios contre ses propres surfaces. Le survol de ligne de
cette interface n'y existe pas à la même valeur : une paire qui passe sur le fond
de la charte peut échouer ici. Un ratio n'est vrai que contre les fonds qui
existent vraiment.

## Portée assumée

Ce témoin lit les JETONS, pas le rendu. Il ne voit donc pas un composant qui
poserait une couleur en dur, ni une opacité qui abaisserait un contraste à
l'écran. Il garantit que le vocabulaire de la charte est sain ; il ne garantit
pas que chaque écran l'emploie bien.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

CSS = (Path(__file__).resolve().parents[2] / "ui" / "src" / "index.css").read_text(
    encoding="utf-8"
)
ANCRE_SOMBRE = "@media (prefers-color-scheme: dark)"

# Les fonds réels : page, panneau, panneau secondaire, survol de ligne.
FONDS = ("--bg", "--panel", "--panel-2", "--panel-3")
# 4,5 = texte courant (WCAG 1.4.3) ; 3,0 = objet graphique ou bordure de
# contrôle (WCAG 1.4.11).
TEXTES = (
    ("--text", 4.5), ("--text-h", 4.5), ("--muted", 4.5), ("--off", 4.5),
    ("--accent", 4.5), ("--llm", 4.5), ("--ok", 4.5), ("--warn", 4.5),
    ("--err", 4.5), ("--border-strong", 3.0),
    # Alias hérités : les composants les consomment encore (`.markdown-view`
    # prend `--ink-800`). Un alias figé sur une couleur du mode clair rend le
    # texte illisible sur les fonds sombres.
    ("--ink-950", 4.5), ("--ink-800", 4.5), ("--ink-600", 4.5),
)
# Un statut doit aussi tenir sur SA propre teinte, pas seulement sur la page.
SUR_TEINTE = (("--ok", "--ok-bg"), ("--warn", "--warn-bg"),
              ("--err", "--err-bg"), ("--llm", "--llm-bg"))
# Le label d'un bouton plein, sur chaque fond plein. En sombre il est ENCRE :
# garder du blanc y donnerait 2,33 sur l'accent.
BOUTONS_PLEINS = ("--accent", "--llm", "--err", "--ok")


def _bloc(depuis: str, jusqu_a: str | None = None) -> str:
    i = CSS.index(depuis)
    return CSS[i:CSS.index(jusqu_a, i)] if jusqu_a else CSS[i:]


def _jetons(source: str) -> dict[str, str]:
    return dict(re.findall(r"^\s*(--[a-z0-9-]+)\s*:\s*([^;]+);", source, re.M))


CLAIR = _jetons(_bloc(":root {", ANCRE_SOMBRE))
SOMBRE = {**CLAIR, **_jetons(_bloc(ANCRE_SOMBRE))}


def _resoudre(nom: str, table: dict[str, str], profondeur: int = 0) -> str | None:
    """Déroule `var(--a)` jusqu'à une couleur littérale, ou None."""
    if profondeur > 12:
        return None
    brut = table.get(nom, "").strip()
    ref = re.fullmatch(r"var\((--[a-z0-9-]+)\)", brut)
    if ref:
        return _resoudre(ref.group(1), table, profondeur + 1)
    return brut if re.fullmatch(r"#[0-9a-fA-F]{6}", brut) else None


def _luminance(couleur: str) -> float:
    h = couleur.lstrip("#")
    canaux = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    canaux = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
              for c in canaux]
    return 0.2126 * canaux[0] + 0.7152 * canaux[1] + 0.0722 * canaux[2]


def _ratio(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


MODES = (("clair", CLAIR), ("sombre", SOMBRE))


@pytest.mark.parametrize("mode,table", MODES, ids=[m for m, _ in MODES])
@pytest.mark.parametrize("jeton,seuil", TEXTES, ids=[j for j, _ in TEXTES])
def test_un_jeton_de_texte_tient_son_seuil_sur_chaque_fond(
        mode: str, table: dict, jeton: str, seuil: float) -> None:
    avant = _resoudre(jeton, table)
    assert avant, f"[{mode}] {jeton} ne se résout pas en couleur littérale"
    for nom_fond in FONDS:
        fond = _resoudre(nom_fond, table)
        assert fond, f"[{mode}] {nom_fond} ne se résout pas"
        r = _ratio(avant, fond)
        assert r >= seuil, (
            f"[{mode}] {jeton} ({avant}) sur {nom_fond} ({fond}) : "
            f"{r:.2f} < {seuil}")


@pytest.mark.parametrize("mode,table", MODES, ids=[m for m, _ in MODES])
@pytest.mark.parametrize("jeton,teinte", SUR_TEINTE, ids=[j for j, _ in SUR_TEINTE])
def test_un_statut_tient_sur_sa_propre_teinte(
        mode: str, table: dict, jeton: str, teinte: str) -> None:
    avant, fond = _resoudre(jeton, table), _resoudre(teinte, table)
    assert avant and fond, f"[{mode}] {jeton} ou {teinte} ne se résout pas"
    r = _ratio(avant, fond)
    assert r >= 4.5, f"[{mode}] {jeton} sur {teinte} : {r:.2f} < 4.5"


@pytest.mark.parametrize("mode,table", MODES, ids=[m for m, _ in MODES])
@pytest.mark.parametrize("fond", BOUTONS_PLEINS)
def test_le_label_d_un_bouton_plein_suit_le_mode(
        mode: str, table: dict, fond: str) -> None:
    """En sombre le label est ENCRE, pas blanc — et ce n'est pas un goût.

    Sur les fonds clairs du mode sombre, un label blanc rend 2,33 sur l'accent,
    2,54 sur le violet et 2,42 sur l'erreur. Les deux exigences — surface
    lisible sur la page ET label lisible sur la surface — tirent en sens
    opposé, et l'inversion est la seule sortie arithmétique.
    """
    surface, label = _resoudre(fond, table), _resoudre("--accent-text", table)
    assert surface and label, f"[{mode}] {fond} ou --accent-text ne se résout pas"
    r = _ratio(surface, label)
    assert r >= 4.5, (
        f"[{mode}] label {label} sur bouton {fond} ({surface}) : {r:.2f} < 4.5")


def test_aucune_variable_n_est_consommee_sans_etre_definie() -> None:
    """Une variable CSS absente ne lève RIEN : la déclaration devient invalide
    et la propriété retombe sur l'héritage. C'est ainsi que `--muted` a servi
    huit fois de couleur de texte, à 9,5 px, sans exister.
    """
    racine = Path(__file__).resolve().parents[2] / "ui" / "src"
    source = CSS + (racine / "App.css").read_text(encoding="utf-8")
    definies = set(re.findall(r"^\s*(--[a-z0-9-]+)\s*:", source, re.M))
    utilisees = set(re.findall(r"var\((--[a-z0-9-]+)", source))
    manquantes = sorted(utilisees - definies)
    assert not manquantes, f"consommées sans définition : {manquantes}"


THEME_CSS = (Path(__file__).resolve().parents[2] / "ui" / "src" / "design" / "theme.css")


def test_le_sombre_force_redefinit_les_memes_jetons_que_le_sombre_systeme() -> None:
    """Le mode sombre est écrit deux fois : `prefers-color-scheme` dans
    `index.css`, et `data-theme='dark'` (choix explicite) dans `theme.css`. Le
    témoin de contraste ne lit que le premier : les deux doivent rester
    identiques, sinon une correction faite d'un seul côté passerait inaperçue."""
    texte = THEME_CSS.read_text(encoding="utf-8")
    i = texte.index(":root[data-theme='dark'] {")
    force = _jetons(texte[i:texte.index("\n}", i)])
    systeme = _jetons(_bloc(ANCRE_SOMBRE))
    # Ancre : les deux blocs sont bien lus (47 jetons chacun aujourd'hui).
    assert len(systeme) >= 40 and len(force) >= 40, (len(systeme), len(force))
    ecarts = {k: (systeme.get(k), force.get(k)) for k in set(systeme) | set(force)
              if (systeme.get(k) or "").strip() != (force.get(k) or "").strip()}
    assert not ecarts, f"sombre système et sombre forcé divergent : {ecarts}"
