"""Le client OpenAI d'un serveur joint par URL (MLX local, serveur compatible OpenAI).

Une seule construction pour les deux moteurs qui parlent le protocole OpenAI à un
serveur que l'utilisateur héberge : les trois réglages ci-dessous sont des gardes de
confidentialité et de délai, et une copie qui en oublierait un ne se verrait pas.

- `trust_env=False` : sans lui, `httpx` lit les proxys du système (variables
  d'environnement, et sur macOS les Réglages Système) et y envoie la requête entière,
  transcript compris, y compris vers la boucle locale ;
- en-têtes `OpenAI-Organization` et `OpenAI-Project` retirés par `Omit()` : sans quoi
  le SDK pose ceux de `OPENAI_ORG_ID` / `OPENAI_PROJECT_ID` sur une requête qui ne
  va pas chez OpenAI ;
- `max_retries=0` : le SDK réessaie deux fois par défaut, ce qui triple l'attente
  d'un délai dépassé et masque un serveur absent.
"""
from __future__ import annotations

import httpx
import openai


def client_sans_fuite(base_url: str, *, api_key: str) -> openai.OpenAI:
    """Un client vers `base_url`, sans proxy du système, sans en-tête d'organisation, sans reprise."""
    return openai.OpenAI(
        base_url=base_url, api_key=api_key,
        max_retries=0,
        http_client=httpx.Client(trust_env=False),
        default_headers={
            "OpenAI-Organization": openai.Omit(),
            "OpenAI-Project": openai.Omit(),
        },
    )
