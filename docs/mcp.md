# Serveur MCP en lecture seule

Memoglyph expose aux agents (Claude, Codex, Cursor… tout client du Model Context
Protocol) ce qu'il sait déjà : projets, réunions, recherche, comptes rendus avec leurs
citations horodatées, faits de la réunion (ECL). **En lecture seule**, en local, borné à un compte.

Code : [`api/mcp.py`](../api/mcp.py). Tests : `tests/integration/test_mcp.py`,
`tests/integration/test_cloud_par_compte.py` (réglage cloud du compte).

Le protocole est tenu par le SDK Python officiel du Model Context Protocol, le paquet
[`mcp`](https://pypi.org/project/mcp/), épinglé en `2.3.0` dans `pyproject.toml`,
`uv.lock` et les `requirements*.txt` : poignée de main, JSON-RPC, validation des
arguments et codes d'erreur sont les siens. `api/mcp.py` n'écrit que la logique propre
à Memoglyph (portée du compte, écart de l'ultra-confidentiel, contenu des outils).

## Lancement

Sur la machine qui héberge Memoglyph :

```bash
./memoglyph mcp <identifiant>
```

Le serveur parle sur l'entrée et la sortie standard (transport stdio du SDK, JSON-RPC
2.0, un message par ligne). Il sert **un** compte, nommé au lancement, et ne demande pas de mot
de passe : comme `./memoglyph reset-password`, il suppose l'accès à la machine. Ne le
lancez donc que pour un compte dont vous avez la charge.

Configuration d'un client (Claude Desktop, Claude Code et la plupart des clients MCP
acceptent cette forme) :

```json
{
  "mcpServers": {
    "memoglyph": {
      "command": "/chemin/vers/memoglyph/memoglyph",
      "args": ["mcp", "<identifiant>"]
    }
  }
}
```

Le script `memoglyph` se place lui-même dans son dossier : le client peut le lancer
d'où il veut.

### Contre une autre racine (démo, recette)

`./memoglyph mcp` sert la racine de son propre dossier. La racine des données est le
dossier parent de `core/` (`core.config.ROOT`) : une racine de démo qui porte sa copie de
`core/` et d'`api/` se sert en lançant le module depuis elle, prise comme dossier courant :

```bash
cd <racine-de-démo>
<dépôt>/.venv/bin/python -m api.cli mcp <identifiant>
```

`python -m` met le dossier courant en tête du chemin d'import : c'est le `core/` de la
démo qui est chargé, donc ses données qui sont servies, et non celles du dépôt.

## Outils

| Outil | Arguments | Rend |
|---|---|---|
| `lister_projets` | aucun | les projets du compte, et les partages qu'il a reçus |
| `lister_reunions` | `projet` (facultatif) | les réunions d'un projet, ou de tous ses projets et de ce qui lui est partagé, chacune avec sa sensibilité (`sensibilite`, celle de la réunion, que l'écran affiche) |
| `rechercher` | `requete`, `projet` et `limite` (1 à 50, facultatifs) | la recherche hybride (mots et sens) : réunion, document, horodatage, extrait |
| `lire_compte_rendu` | `reunion` | le compte rendu le plus récent de la réunion, avec ses citations `[source HH:MM:SS]` |
| `lire_faits` | `reunion` | les faits de la réunion, ECL (décisions, actions, problèmes, chiffres), chacun avec sa citation horodatée et `a_verifier`, une mise en garde ; aucune mesure de précision |

Chaque résultat est rendu deux fois : en texte JSON (`content`) et en objet
(`structuredContent`). Chaque outil s'annonce en lecture seule (`readOnlyHint`,
sans `destructiveHint` ni `openWorldHint`).

## Ce que le serveur garantit

- **La portée du compte, rien de plus.** Ses projets, et ce qui lui est partagé en
  lecture (`api/portee.py`). Une réunion d'un autre compte, ou retirée du partage, est
  « introuvable », exactement comme une réunion qui n'existe pas.
- **L'ultra-confidentiel seulement si son propriétaire l'autorise.** Un client MCP peut
  être un modèle cloud : par défaut, un document `ultra_confidential`, ou une réunion qui
  en contient un (Q16), n'est jamais rendu, et les listes disent seulement combien
  d'éléments ont été écartés. Si le compte servi autorise le cloud pour son
  ultra-confidentiel (panneau **Confidentialité**, `PUT /api/compte/cloud-ultra`), ses
  propres documents ultra-confidentiels sont rendus ; chaque rendu est consigné sur
  l'erreur standard (compte, `fournisseur=mcp`, identifiant du document, sans titre ni
  contenu). Le réglage est relu à chaque appel : désactivé, il referme dès l'appel
  suivant. Il n'ouvre jamais l'ultra-confidentiel d'un autre compte : ce qui est partagé
  avec le compte n'en contient pas, même devenu ultra-confidentiel après le partage
  (`tests/integration/test_cloud_par_compte.py`).
- **Aucune écriture.** Aucun outil n'appelle une fonction qui écrit : pas de session, pas
  de « Non classé » créé, pas d'index reconstruit, pas de modèle de génération. Un test
  compare le contenu de `output/` et des bases avant et après une série d'appels.
- **Une sortie propre.** La sortie standard ne porte que le protocole ; les journaux
  (chargement du modèle de recherche…) partent sur l'erreur standard. Le transport
  stdio du SDK détourne le descripteur 1 vers l'erreur standard pendant le service :
  même une écriture d'une bibliothèque en C manque le protocole. Un test de bout en
  bout le vérifie sur un sous-processus, avec le client stdio du SDK.

## Erreurs

Celles du SDK, sauf les deux dernières lignes, propres à Memoglyph.

| Cas | Réponse |
|---|---|
| JSON illisible, ou message qui n'est pas du JSON-RPC 2.0 | aucune réponse : la ligne est écartée (journal de débogage du SDK), la session continue |
| Requête avant la poignée de main (`initialize`) | erreur `-32602` |
| Méthode inconnue | erreur `-32601` |
| Outil inconnu | résultat `isError` : « Unknown tool: … » |
| Argument manquant ou invalide | résultat `isError` : « Error executing tool … », qui nomme l'argument en cause |
| Réunion ou projet hors portée, ou inexistant | résultat `isError` : « Réunion introuvable », « Projet introuvable » |
| Réunion ultra-confidentielle, sans le réglage du compte | résultat `isError`, sans rien de son contenu |

Un outil inconnu et un argument invalide sont des erreurs d'outil, et non du protocole :
l'agent les lit et peut corriger son appel.

## Limites

- Lecture seule en V1 : lancer une transcription ou renommer attend une V2.
- La première recherche charge le modèle d'encodage (`multilingual-e5-large`) : quelques
  secondes, une fois par lancement.
- Les titres rendus sont lisibles : le titre saisi, sinon le nom de la source.
- **À chaque montée de version du SDK** : `servir` lance le serveur par un attribut privé
  (`_lowlevel_server.run`), pour placer la redirection de la sortie standard après la
  prise du transport. Une nouvelle version peut le renommer ; le test de bout en bout
  (`test_bout_en_bout_par_le_client_stdio_du_sdk`) le dirait.
- Un serveur, un compte : pour un autre compte, un autre lancement.
- À la fin de l'entrée, le SDK annule les requêtes encore en cours : un client attend
  ses réponses avant de fermer (ce que font les clients MCP). Un fichier de requêtes
  envoyé d'un bloc par un tube peut perdre les dernières réponses.
