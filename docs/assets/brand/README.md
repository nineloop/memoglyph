# Marque Memoglyph

Le symbole Memoglyph, en deux variantes de même dessin (quatre tracés, viewBox 1254) :

| Fichier | Usage | Corps | Œil | Ailes |
|---|---|---|---|---|
| `memoglyph-symbole.svg` | sur fond clair | `#0A1417` encre | `#FFFFFF` | `#0A7178` pétrole, `#34C9D1` cyan |
| `memoglyph-symbole-sombre.svg` | sur fond sombre | `#EEF5F5` sable | `#0A1417` encre | `#0A7178` pétrole, `#34C9D1` cyan |

Le **logo horizontal** associe le symbole et le mot « Memoglyph » en Inter Bold
vectorisée (aucune police chargée), « glyph » en couleur, comme sur la planche de marque :

| Fichier | Usage | « Memo » | « glyph » |
|---|---|---|---|
| `memoglyph-logo-horizontal.svg` | sur fond clair, en tête des README | `#0A1417` encre | `#0A7178` pétrole |
| `memoglyph-logo-horizontal-sombre.svg` | sur fond sombre (`prefers-color-scheme`) | `#EEF5F5` sable | `#34C9D1` cyan |

`memoglyph-partage-github.png` (1280 × 640) est l'image de partage du dépôt : fond encre,
logo horizontal sombre et la phrase « Local meeting transcripts and reports, with verified
citations. ». Elle se règle à la main dans les paramètres du dépôt GitHub (*Social preview*).

Dans l'interface :

- `ui/public/brand/` porte les deux variantes ; l'en-tête affiche celle du thème effectif
  (`data-theme`, `ui/src/App.css`, `.brand-mark`).
- `ui/public/favicon.svg` est un seul fichier qui bascule corps et œil avec
  `prefers-color-scheme`.
- `ui/public/apple-touch-icon.png` (180 × 180) est rendu depuis la variante claire, sur fond sable.

Les SVG ne gardent que les tracés : sans métadonnées, leur rendu est identique au pixel près
aux fichiers sources.

## Licence

Le nom et le logo Memoglyph ne sont pas couverts par la licence Apache-2.0 du code
(voir la section 6 de la licence).

> The Memoglyph name and logo are not covered by the Apache-2.0 license of the code; see
> section 6 of the license.
