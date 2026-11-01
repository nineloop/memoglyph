"""Consignes de l'ECL, une par mode : « gabarit » en local, « définitions » en cloud.

En local, le gabarit de réunion (déroulé, statut « arrêté ») est celui qui réussit le
mieux à Granite 4.2 8B ; en cloud, les définitions avec exemples positifs et négatifs.
Leurs exemples sont génériques : aucun ne vient d'une réunion réelle. `COMMUNE` est la
consigne d'origine, gardée pour comparer les consignes.
"""

COMMUNE = """Tu relèves les faits d'une réunion à partir d'un extrait de son transcript.
Chaque ligne est une réplique : « **Locuteur** [HH:MM:SS] texte » ou « [HH:MM:SS] texte ».

Types de faits :
- "decision" : ce que le groupe arrête ; une idée, une proposition, une hypothèse ou
  une question n'est pas une décision ;
- "action" : une tâche confiée à une personne ou à un rôle, ou un engagement explicite
  (« je vais… ») ; une suggestion n'est pas une action ;
- "probleme" : un point explicitement laissé sans réponse ou à régler ;
- "chiffre" : un chiffre, un montant ou une échéance fixés ou annoncés.

Pour chaque fait :
- "citation" : les mots EXACTS d'une à trois répliques consécutives, recopiés sans rien
  changer, hésitations comprises, au moins trois mots ; cite la proposition et la réponse
  quand un fait se construit sur deux tours ;
- "horodatage" : l'horodatage HH:MM:SS de la première réplique citée ;
- "enonce" : le fait en une phrase courte, écrite dans la langue de la réunion ; chaque chiffre
  et chaque nom propre de l'énoncé doit figurer, écrit pareil, dans la citation ; un
  locuteur cité se désigne par son libellé (« Speaker 2 ») ;
- "porteur" (actions seulement) : la personne ou le rôle à qui l'action est confiée,
  tel qu'il est nommé dans la citation ; null s'il n'y est pas nommé.

N'invente rien. Mieux vaut s'abstenir que proposer un fait douteux. S'il n'y a aucun
fait, réponds {"faits": []}.

Réponds en JSON seul : {"faits": [{"type": ..., "horodatage": ..., "citation": ...,
"enonce": ..., "porteur": ...}]}"""

# Définitions avec exemples positifs et négatifs.
DEFINITIONS = """Tu relèves les faits d'une réunion à partir d'un extrait de son transcript.
Chaque ligne est une réplique : « **Locuteur** [HH:MM:SS] texte » ou « [HH:MM:SS] texte ».

Un fait est rare : la plupart des répliques n'en contiennent aucun. Quatre types, définis
par ce qu'ils sont et ce qu'ils ne sont pas :

- "decision" : un choix que le groupe arrête, ou une contrainte imposée qu'il retient.
  Oui : « OK, so we go with supplier B. » ; « Management wants it shipped in red only. »
  Non : « Maybe we could use supplier B? » (une idée) ; « I think red would look nice. »
  (un avis) ; « Supplier B is cheaper. » (un constat d'exposé).
- "action" : une tâche confiée à quelqu'un, ou un engagement explicite à faire.
  Oui : « Mark, can you send the quotes by Friday? » ; « I'll update the schedule. »
  Non : « We should think about the schedule. » (une suggestion) ; « Next slide, please. »
- "probleme" : une question ou un point laissé ouvert, que personne ne règle sur le moment.
  Oui : « We still don't know who pays for shipping. »
  Non : une question à laquelle la réplique suivante répond.
- "chiffre" : un chiffre, un montant ou une échéance fixés ou annoncés comme tels.
  Oui : « The budget is 40,000 euros. » ; « Launch is on March 3rd. »
  Non : « Maybe around fifty? » (une estimation en l'air).

Pour chaque fait :
- "citation" : les mots EXACTS d'une à trois répliques consécutives, recopiés sans rien
  changer, hésitations comprises, sans le libellé ni l'horodatage, au moins trois mots ;
- "horodatage" : l'horodatage HH:MM:SS de la première réplique citée ;
- "enonce" : le fait en une phrase courte, dans la langue de la réunion ; chaque chiffre et
  chaque nom propre de l'énoncé doit figurer, écrit pareil, dans la citation ; n'affirme
  rien que la citation ne dise ;
- "porteur" (actions seulement) : la personne ou le rôle à qui l'action est confiée, tel
  qu'il est nommé dans la citation ; null s'il n'y est pas nommé.

Mieux vaut s'abstenir que proposer un fait douteux. S'il n'y a aucun fait, réponds
{"faits": []}.

Réponds en JSON seul, un seul objet : {"faits": [{"type": ..., "horodatage": ...,
"citation": ..., "enonce": ..., "porteur": ...}]}
"""

# Gabarit de réunion : genre, déroulé (exposé, discussion, conclusion), statut « arrêté ».
GABARIT = """Tu relèves les faits d'une réunion de travail à partir d'un extrait de son transcript.
Chaque ligne est une réplique : « **Locuteur** [HH:MM:SS] texte » ou « [HH:MM:SS] texte ».

Une réunion de ce genre suit un déroulé :
1. l'exposé : quelqu'un présente une étude, des résultats, des idées. Ce qui s'y dit
   n'engage pas le groupe : ce n'est pas un fait, sauf une contrainte imposée de l'extérieur
   (direction, client, budget) annoncée comme telle ;
2. la discussion : on propose, on objecte, on demande. Une proposition reste une
   proposition tant que personne ne la retient ;
3. la conclusion, souvent un récapitulatif ou un tour de table final : on arrête des choix,
   on répartit des tâches, on note ce qui reste ouvert. C'est là que sont la plupart des
   faits.

Pour chaque candidat, demande-toi d'abord s'il est « arrêté » (le groupe le retient, ou une
contrainte imposée) ou seulement « proposé ». Ne rends que ce qui est arrêté, ou une tâche
confiée, ou un point explicitement laissé ouvert, ou un chiffre fixé.

Types : "decision" (choix arrêté ou contrainte imposée retenue), "action" (tâche confiée ou
engagement explicite), "probleme" (point laissé ouvert, que personne ne règle sur le
moment), "chiffre" (chiffre, montant ou échéance fixés ou annoncés comme tels).

Pour chaque fait :
- "statut" : "arrêté" ;
- "citation" : les mots EXACTS d'une à trois répliques consécutives, recopiés sans rien
  changer, sans le libellé ni l'horodatage, au moins trois mots ; pour un choix arrêté en
  deux tours (« on retient X ? » « oui »), cite les deux ;
- "horodatage" : l'horodatage HH:MM:SS de la première réplique citée ;
- "enonce" : le fait en une phrase courte, dans la langue de la réunion ; chaque chiffre et
  chaque nom propre doit figurer, écrit pareil, dans la citation ; n'affirme rien que la
  citation ne dise ;
- "porteur" (actions seulement) : la personne ou le rôle à qui la tâche est confiée, tel
  qu'il est nommé dans la citation ; null sinon.

Un extrait d'exposé ne contient souvent aucun fait : réponds alors {"faits": []}.

Réponds en JSON seul, un seul objet : {"faits": [{"type": ..., "statut": ...,
"horodatage": ..., "citation": ..., "enonce": ..., "porteur": ...}]}
"""
