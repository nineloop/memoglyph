# Les quatre statuts de génération — référence

Toute génération de compte-rendu se termine par **un** de ces quatre statuts,
remonté par `make_report()["generation"]` et consigné dans le journal de génération.

| statut | ce qu'il dit | génération supplémentaire ? |
|---|---|---|
| `PASS_DIRECT` | le modèle a honoré le contrat du premier coup | non |
| `PASS_REPAIRED` | une faute de balance a été corrigée, sans redemander | non |
| `PASS_RETRY` | il a fallu redemander au modèle | **oui** |
| `FAIL` | rien n'a abouti après toutes les tentatives | oui, en pure perte |

## Les trois lectures, et pourquoi elles ne se confondent jamais

```
fiabilité produit         = PASS_DIRECT + PASS_REPAIRED + PASS_RETRY
fiabilité NATIVE          = PASS_DIRECT seulement
sans génération suppl.    = PASS_DIRECT + PASS_REPAIRED     ← la lecture de COÛT
```

Règle, et elle porte une garde réelle : **si la fiabilité
produit monte pendant que la native baisse, le filet masque une dégradation du
modèle.** Un seul taux de réussite rendrait cette dérive invisible.

Le résultat voulu : la fiabilité native ne bouge pas pendant que la fiabilité
produit monte. Le filet rattrape sans masquer.

## Ce qui n'est PAS un statut

**Le déballage de transport.** Une clôture Markdown résiduelle retirée par
`llm_base.retirer_enveloppe_markdown` est consignée dans `envelope`, jamais
dans `repair`. Une sortie déballée puis valide compte `PASS_DIRECT`.

C'est délibéré : le modèle n'a commis aucune faute de STRUCTURE. Compter
autrement ferait paraître le filet plus utile qu'il n'est, et gonflerait
artificiellement le taux de réparation.

Mistral encadre souvent son JSON de ```` ```json ````, sans régularité — parfois
oui, parfois non, sur le même transcript. Ce n'est donc pas un cas marginal.

## Le contenu du journal

```json
{
  "status": "PASS_REPAIRED",
  "attempt": 2,
  "generation_seconds": 41.3,
  "attempts": [
    {"attempt": 1, "seconds": 22.7, "outcome": "unparsable"},
    {"attempt": 2, "seconds": 18.6, "outcome": "parsed"}
  ],
  "envelope": null,
  "repair": {
    "applied": true,
    "operation": "delete_closing_bracket",
    "position": 2728,
    "original_error": "Expecting property name enclosed in double quotes @2731",
    "original_sha": "e879c9f59966f8de",
    "repaired_sha": "889eb527ff46cdbd",
    "refused": null
  },
  "repairs_attempted": [ … une entrée par tentative, refus compris … ]
}
```

`attempts` chronomètre **l'appel de génération seul** — ni le profilage, ni le
Map-Reduce, ni l'écriture, ni l'indexation. La latence bout en bout de
l'endpoint est une autre mesure, et les confondre ferait attribuer au modèle
un temps qui ne lui appartient pas.

`outcome` vaut `parsed`, `unparsable` ou `contract_refused` — cette dernière
distinguant un document parfaitement lisible mais hors contrat d'une sortie
qu'on n'a pas su parser. Les deux appellent des corrections différentes.

## Les deux fournisseurs ne se comportent pas pareil

| | MLX local | Mistral cloud |
|---|---|---|
| graine | `mx.random.seed` appliqué | `random_seed`, effort du service |
| reproductibilité | **stricte**, octet pour octet | **aucune** |
| reprise interne du moteur | supprimée si graine posée | conservée |
| critère de certification | identité des empreintes | stabilité fonctionnelle |

Côté Mistral, à `random_seed` identique, un même média peut rendre un statut
différent d'un passage à l'autre.

D'où l'asymétrie des boucles internes, qui n'est pas une dette à uniformiser :

- côté MLX, rejouer rend la **même sortie** : une reprise ne fait que rejeter des
  jetons et du temps. La boucle est donc supprimée dès qu'une graine est posée.
- côté Mistral, redemander récupère réellement. La boucle reste.

Son coût est réel et documenté : une sortie réparable coûte **2 appels
facturés au lieu de 1**, une sortie irrécupérable **6 au lieu de 3**. Le filet
épargne la reprise MÉTIER côté cloud, jamais la reprise MOTEUR — le gain y est
moindre qu'en local.

## Où c'est produit, où c'est lu

- produit par `core/report._generer_sous_contrat`, remonté par `make_report`
- statuts définis dans `core/reparation_json.py`
- lu dans le journal de génération de chaque compte-rendu
