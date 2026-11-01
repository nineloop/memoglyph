"""Découpage borné en tokens (préservant les tours de parole) + petits utilitaires
déterministes : estimation de tokens, lecture des répliques du transcript (porte
unique du compte-rendu et de l'ECL), et lecture des thèmes du bloc de profil."""
from __future__ import annotations

import re
from collections import Counter
from typing import NamedTuple, Optional

# ---------- Lecture des répliques : la porte unique du CR et de l'ECL ----------
#
# `writer_md.render_markdown` écrit `**Speaker 1** [HH:MM:SS] texte`, `**Speaker ?**`
# (segment sans locuteur), `**<nom>**` avec diarisation, ou `[HH:MM:SS] texte` sans.
# Ne lire que `**Speaker N**` privait de CR tout transcript non diarisé : zéro ligne,
# zéro unité, un échec sans cause lisible. Un transcript importé d'ailleurs porte en
# plus l'horodatage court `[MM:SS]`, le locuteur hors gras (« Nom [HH:MM:SS] texte »)
# ou après l'horodatage (« [HH:MM:SS] Nom : texte ») : la même porte les lit.
#
# L'horodatage est normalisé en `HH:MM:SS` dès la lecture : citations du CR, garde,
# ancrage de l'ECL et unités ne voient jamais que cette forme.
_HORODATAGE = r"\[(?P<ts>\d{1,2}:\d{2}(?::\d{2})?)\]"
# Un nom de locuteur hors gras : un à quatre mots, sans chiffre. Un mot commence par
# une lettre et ne contient que lettres, apostrophes, points et traits d'union : ni
# « Salle 4 », ni une heure « 10:30 », ni une adresse « https://… ».
_MOT = r"[^\W\d_](?:[^\W\d_]|['’.-])*"
_NOM = rf"{_MOT}(?: {_MOT}){{0,3}}"
_EN_GRAS = re.compile(rf"^\*\*(?P<locuteur>[^*\n]+?)\*\*\s*{_HORODATAGE}\s*(?P<texte>.*)$")
_NU = re.compile(rf"^{_HORODATAGE}\s*(?P<texte>.*)$")
_NOM_AVANT = re.compile(rf"^(?P<locuteur>{_NOM})\s+{_HORODATAGE}\s*(?P<texte>.*)$")
_NOM_DEUX_POINTS = re.compile(rf"^(?P<nom>{_NOM})\s*:\s+(?P<reste>\S.*)$")
# « Nom : » ne devient un locuteur qu'à partir de ce nombre de répliques ouvertes.
REPETITIONS_LOCUTEUR = 2


class Replique(NamedTuple):
    """Une réplique lue : horodatage `HH:MM:SS`, locuteur (ou None), texte."""
    horodatage: str
    locuteur: Optional[str]
    texte: str

    def ligne(self) -> str:
        """La forme canonique donnée au modèle : `**Nom** [HH:MM:SS] texte` ou `[HH:MM:SS] texte`."""
        if self.locuteur:
            return f"**{self.locuteur}** [{self.horodatage}] {self.texte}"
        return f"[{self.horodatage}] {self.texte}"


def normaliser_horodatage(brut: str) -> Optional[str]:
    """`HH:MM:SS` d'un horodatage de transcript, ou None s'il est impossible.

    `MM:SS` compte des minutes : `75:10` donne `01:15:10`, jamais `00:15:10`.
    `H:MM:SS` prend son zéro. `HH:MM:SS` est rendu tel qu'écrit, comme il l'a
    toujours été. Des secondes (ou, sur trois parties, des minutes) de 60 et plus
    ne sont pas un horodatage.
    """
    parties = (brut or "").split(":")
    if len(parties) == 3 and all(len(p) == 2 and p.isdigit() for p in parties):
        return brut
    if not 2 <= len(parties) <= 3 or not all(p.isdigit() for p in parties):
        return None
    if len(parties) == 2:
        minutes, s = int(parties[0]), int(parties[1])
        h, m = divmod(minutes, 60)
    else:
        h, m, s = (int(p) for p in parties)
        if m >= 60:
            return None
    if s >= 60:
        return None
    return f"{h:02d}:{m:02d}:{s:02d}"


def _brute(ligne: str) -> Optional[tuple[str, Optional[str], str]]:
    """(horodatage normalisé, locuteur écrit avant l'horodatage, texte) ou None."""
    m = _EN_GRAS.match(ligne) or _NU.match(ligne) or _NOM_AVANT.match(ligne)
    if not m:
        return None
    horodatage = normaliser_horodatage(m.group("ts"))
    if horodatage is None:
        return None
    locuteur = (m.groupdict().get("locuteur") or "").strip() or None
    return horodatage, locuteur, m.group("texte").strip()


def lire_repliques(body: str) -> list[Replique]:
    """Les répliques horodatées de la section « ## Transcript », dans l'ordre.

    Lecture en deux temps, parce qu'un locuteur « Nom : » se juge sur le document
    entier. Règle : « [HH:MM:SS] Nom : texte » ne fait de « Nom » un locuteur que si
    le même nom ouvre au moins `REPETITIONS_LOCUTEUR` répliques sans locuteur du
    document, et s'il tient en quatre mots sans chiffre. Sinon le texte reste
    entier : « [00:01:02] Budget : 25 € », isolée, est une réplique sans locuteur
    dont le texte est « Budget : 25 € ». Un locuteur déjà écrit (en gras ou avant
    l'horodatage) n'est jamais remplacé.
    """
    section = body.split("## Transcript", 1)[-1]
    lues = [r for r in (_brute(ln.strip()) for ln in section.splitlines()) if r and r[2]]
    # Premier temps : les noms qui ouvrent des répliques sans locuteur.
    ouvertures = Counter(m.group("nom") for _ts, locuteur, texte in lues
                         if locuteur is None and (m := _NOM_DEUX_POINTS.match(texte)))
    recurrents = {nom for nom, n in ouvertures.items() if n >= REPETITIONS_LOCUTEUR}
    # Second temps : la lecture.
    repliques: list[Replique] = []
    for horodatage, locuteur, texte in lues:
        if locuteur is None:
            m = _NOM_DEUX_POINTS.match(texte)
            if m and m.group("nom") in recurrents:
                locuteur, texte = m.group("nom"), m.group("reste").strip()
        repliques.append(Replique(horodatage, locuteur, texte))
    return repliques


class TranscriptNonCitable(ValueError):
    """Transcript sans réplique horodatée : un compte-rendu n'y pourrait rien citer."""
# Ligne « **Thèmes :** a · b · c » du bloc de profil (FR ou EN).
THEMES_RE = re.compile(r"\*\*Th[eè]mes ?:\*\*\s*(.+)", re.IGNORECASE)
THEMES_RE_EN = re.compile(r"\*\*Topics ?:\*\*\s*(.+)", re.IGNORECASE)


def est_tokens(text: str) -> int:
    """Estimation déterministe, sans dépendance : ~3.6 caractères/token (FR).

    Sert UNIQUEMENT à borner la taille des tranches ; le contrat de sortie et la
    validation restent déterministes et exacts en aval.
    """
    return max(1, round(len(text) / 3.6))


def transcript_lines(body: str) -> tuple[list[str], set[str]]:
    """Les répliques en forme canonique (`Replique.ligne`) et leurs horodatages `HH:MM:SS`."""
    repliques = lire_repliques(body)
    return [r.ligne() for r in repliques], {r.horodatage for r in repliques}


def verifier_citable(body: str) -> None:
    """Refuse AVANT tout appel au modèle un transcript sans réplique horodatée.

    Chaque phrase d'un CR cite `[source HH:MM:SS]` : sans horodatage, il n'y a
    rien à citer, et lancer le CR finirait sur zéro unité après des minutes de
    calcul.
    """
    if not transcript_lines(body)[0]:
        raise TranscriptNonCitable(
            "Ce transcript n'a pas d'horodatage : un compte-rendu ne pourrait rien citer. "
            "Retranscrivez avec les horodatages activés.")


def profile_themes(body: str) -> list[str]:
    """Lit les thèmes déjà produits par le profil (bloc du transcript). Vide si absent."""
    m = THEMES_RE.search(body) or THEMES_RE_EN.search(body)
    if not m:
        return []
    raw = m.group(1).split("\n", 1)[0]
    return [t.strip() for t in raw.split("·") if t.strip()]


def token_chunks(lines: list[str], bound_tokens: int, overhead_tokens: int = 320) -> list[str]:
    """Regroupe les répliques en tranches dont l'entrée estimée reste ≤ bound_tokens.

    Coupe UNIQUEMENT aux frontières de réplique ; ne fusionne JAMAIS une queue si
    cela dépasserait la borne (la queue devient sa propre tranche).
    """
    budget = max(1, bound_tokens - overhead_tokens)
    chunks: list[str] = []
    cur: list[str] = []
    cur_tok = 0
    for ln in lines:
        t = est_tokens(ln + "\n")
        if cur and cur_tok + t > budget:
            chunks.append("\n".join(cur))
            cur, cur_tok = [], 0
        cur.append(ln)
        cur_tok += t
    if cur:
        chunks.append("\n".join(cur))
    return chunks
