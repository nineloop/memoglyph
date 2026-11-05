# Gabarits de compte-rendu — ce que le modèle reçoit et ce qu'il doit rendre

Ce document décrit la génération d'un compte-rendu, de bout en bout, pour
quelqu'un qui ne connaît pas le code. Il répond à quatre questions : comment
Memoglyph choisit un gabarit, ce que le modèle reçoit, ce qu'il doit produire,
et comment le résultat est validé puis rendu.

Les valeurs citées ici sont relevées dans le code ; les fichiers sources sont nommés à chaque fois.

---

## 1. Comment un gabarit est choisi

Le choix se fait en quatre temps, et le code garde le dernier mot.

**Le type du profil décide en premier.** Quand le document a un bloc de profil,
`type_du_profil` ([`core/report.py`](../core/report.py)) lit la ligne « Type »
que le profilage y a écrite. Seul le bloc de profil est lu : une ligne
« Type : » prononcée dans le transcript ne décide de rien. Si ce type est
**exactement** un alias de `projet` (`templates.est_type_reunion` : « workshop »,
« team meeting », « réunion de travail », « atelier »…), le genre est `projet`
**sans appel au modèle**. L'égalité est exacte : « sports meeting » n'est pas
un type de réunion.

**Sinon, le modèle propose.** `classify_genre` envoie au LLM un extrait de
1 400 caractères au plus — le bloc de profil, ou à défaut le début du texte
prononcé (`profile_excerpt`), jamais le transcript entier — avec **une ligne de définition
par genre** (`templates.genre_definitions`, dans la langue du document,
`analyse_sportive` en dernier) et la consigne de répondre par un seul mot.
Choisir dans un menu est bien plus fiable, pour un modèle, que d'inventer un
libellé, et des clés nues ne disent pas à un petit modèle ce qu'est « projet »
ou « interview ». Un document sans profil ne reçoit ni `projet` ni `steerco` dans
la liste.

**Le code vérifie.** `templates.normalize`
([`core/templates.py`](../core/templates.py)) ramène la réponse à un genre
connu : correspondance exacte, puis alias, puis recherche d'un libellé de genre
à l'intérieur d'une phrase — les plus longs d'abord, pour que « comité de
pilotage » l'emporte sur « comité ». **Tout écart retombe sur `generique`**, le
gabarit le plus sobre.

**Deux barrières protègent les genres étroits.**

- `projet` et `steerco` réclament décisions, responsables et échéances. Un
  document **sans profil** n'apporte qu'une preuve trop faible pour les engager :
  dans ce cas le code force `generique`, quelle que soit la réponse du modèle.
- `analyse_sportive` n'est gardé que si l'extrait contient **au moins deux mots
  distincts** du domaine du sport (`templates.domaine_present`,
  `MINIMUM_MOTS_DOMAINE = 2`). Les mots de toute réunion (« team », « score »,
  « game », « goal », « player ») n'y figurent pas. Sinon : `generique`.

Enfin, si le classifieur échoue — panne, délai — le compte-rendu reste possible
et retombe sur `generique`. La classification ne peut pas faire échouer un
document. Seul un refus de la règle d'envoi (document ultra-confidentiel, voir
[local-et-en-ligne.md](local-et-en-ligne.md)) n'est pas absorbé.

**Ce qui est gardé.** `classify_genre` peut remplir un dictionnaire de diagnostic :
la **source** du genre (`profil`, `modele`, `garde`, `repli`, `vide`) et la
réponse **brute** du modèle, bornée à 200 caractères. Ces deux valeurs restent
dans le diagnostic de l'exécution et dans son résultat ; elles ne sont jamais
écrites dans le document, car un modèle peut y avoir répété du transcript. La
version du classifieur (`GENRE_CLASSIFIER_VERSION`, actuellement 2) est inscrite
dans la provenance du compte-rendu (`core/provenance.py`).

---

## 2. Les dix gabarits

| gabarit | genre / type de contenu | sections | questions | source de vérité |
|---|---|---|---|---|
| `generique` | repli, et tout contenu non reconnu | **4** | 4 | `core/templates.py` |
| `analyse_sportive` | plateau ou débat sportif | 5 | 5 | idem |
| `entretien_embauche` | entretien de recrutement | 5 | 5 | idem |
| `keynote` | conférence, intervention plénière | 5 | 5 | idem |
| `town_hall` | réunion collective, assemblée | 5 | 5 | idem |
| `webinar` | présentation en ligne avec questions | 5 | 5 | idem |
| `interview` | entretien journalistique, podcast | **6** | 6 | idem |
| `internal_briefing` | briefing opérationnel interne | 6 | 6 | idem |
| `projet` | réunion de projet — **genre à engagement** | 6 | 6 | idem |
| `steerco` | comité de pilotage — **genre à engagement** | 6 | 6 | idem |

**Le nombre de sections est exactement le nombre de questions.** Une question,
une section, dans l'ordre, numérotées de 1 à N par `question_id`. Le contrat
l'exige et le validateur le vérifie : une série incomplète est un refus.

**Plafond d'affirmations par section : 5.** C'est
`report_citations.BORNE_TEXTE_PAR_SECTION`, et c'est une valeur **du texte du
contrat uniquement** — le schéma JSON ne la porte pas. La distinction est
essentielle et documentée au §5.

**Plafond total du document :** `max(10, sections × 2)`, soit 10 pour les
gabarits à 4 et 5 sections, 12 pour ceux à 6. Il n'est pas demandé au modèle en
ces termes : il est appliqué **après coup** par la sélection (§6).

### Une formulation partagée par tous les gabarits

Chaque question qui peut légitimement rester sans réponse le dit explicitement :
« S'il n'y en a aucune, réponds *aucune* ». C'est délibéré — sur un plateau
télé, un gabarit qui réclame des décisions en fabrique.

Et deux gabarits vont plus loin. `projet` distingue une décision **adoptée**
d'une option discutée ; `steerco` distingue un arbitrage **rendu** d'un débat.
Ces majuscules sont dans le code, pas décoratives.

---

## 3. Le prompt final, couche par couche

Assemblé par `make_report` ([`core/report.py`](../core/report.py)). L'ordre
compte, et certaines couches apparaissent **deux fois**.

| # | couche | fonction | construite par | répétée | générée ou déterministe | influe sur la reproductibilité |
|---|---|---|---|---|---|---|
| 0 | **système** | cadre la tâche, interdit introduction et conclusion | `_SYSTEM`, constante | non | déterministe | non |
| 1 | **directive de langue** | ancre la langue de sortie, **écrite dans la langue cible** | `_report_directive` | **oui**, en tête et en pied | déterministe | non |
| 2 | **règle de sections** | « exactement N sections, `question_id` 1 à N » | `make_report` | **oui** | déterministe | non |
| 3 | **contrat de citations** | forme du JSON, règles de preuves, abstention | `report_citations.prompt_contract` | **oui** | déterministe | non |
| 4 | **questions du gabarit** | le fond du compte-rendu | `templates.questions` | **oui** | déterministe | non |
| 5 | **synthèses Map-Reduce** | résumé factuel par tranche de 120 lignes | `map_summarize_chunks` | non | **générée par le LLM** | **OUI — critique** |
| 6 | **transcript complet** | la source, avec locuteurs et horodatages | `spoken_markdown` | non | déterministe | non |

La couche 5 est la seule produite par un modèle, et donc la seule qui puisse
faire varier le prompt d'une exécution à l'autre. **C'est par elle que la
reproductibilité se perd si sa graine n'est pas fixée** : deux exécutions au
même commit peuvent produire des prompts différents, et des comptes-rendus qui
n'affirment pas le même nombre de choses.

### Deux points contre-intuitifs, vérifiés

**Le Map-Reduce ne raccourcit pas le prompt, il l'allonge.** Le transcript
entier est envoyé **en plus** des synthèses. Le découpage allège la lecture du
modèle, jamais la longueur de l'entrée.

**Le Map-Reduce tourne AVANT la classification.** Les synthèses sont donc
produites sans savoir de quel genre il s'agit. Ce n'est pas un défaut, mais ça
ne se lit pas dans le code sans le tracer.

**Le Map-Reduce ne tourne pas toujours.** En dessous de deux tranches — soit
120 lignes de transcript — `map_summarize_chunks` rend une chaîne vide sans
appeler le modèle. Un transcript court n'a donc aucune couche générée dans son
prompt, et est reproductible même sans graine en amont.

### Combien d'appels au modèle pour un compte-rendu

Pour un transcript de trois tranches : **3** synthèses + **1** classification +
**1** génération sous contrat = 5 minimum. La dernière peut être rejouée
jusqu'à trois fois, chacune pouvant elle-même être reprise une fois par le
moteur — soit **11 appels au pire**.

---

## 4. La structure de sortie attendue

```json
{"sections": [
  {"question_id": 1,
   "title": "Quels sujets ont été traités ?",
   "answer_status": "answered",
   "claims": [
     {"text": "L'équipe valide le lancement mardi.",
      "primary_evidence": {"timestamp": "00:03:21",
                           "quote": "On valide le lancement mardi."},
      "secondary_evidence": null}
   ]},
  {"question_id": 2,
   "title": "Quelles décisions ont été adoptées ?",
   "answer_status": "not_found",
   "claims": []}
]}
```

**`answer_status`** vaut `answered` ou `not_found`. Une question sans réponse
est une propriété **de la question**, pas une affirmation à propos du monde —
c'est pourquoi elle vit au niveau de la section et non dans une affirmation
artificielle du genre « aucune information trouvée ».

Les deux incohérences symétriques sont refusées : une section `not_found` qui
porte des affirmations, et une section `answered` qui n'en porte aucune.

Dans le compte rendu du pipeline riche, les rubriques Décisions, Actions et Questions
ouvertes sont tirées des unités de ce type (`core/experimental/app_report.py`,
`RUBRIQUES`) ; une rubrique vide est omise, le CR n'affiche pas de titre sans contenu.

---

## 5. Les règles de preuves

**Une preuve principale, obligatoire. Une preuve secondaire, facultative.**
Aucune troisième n'est possible — il n'y a pas de champ où l'écrire.

C'est une correction, et elle vaut d'être expliquée. Le contrat
utilisait auparavant un tableau ouvert `evidence: []`, borné par une consigne
en prose. MESURÉ en capturant une sortie tronquée : le modèle local y avait mis
**172 preuves pour 11 affirmations**, en recopiant le transcript tour par tour,
horodatage croissant. Il ne bouclait pas — il transcrivait.

Un tableau ouvert est précisément ce qu'un modèle remplit sans fin. Deux champs
nommés ne le peuvent pas : **la borne n'est plus une consigne, elle est la
forme de la sortie**.

### Ce qu'une preuve doit être

Un extrait recopié **mot pour mot** d'un seul tour de parole, portant
exactement l'horodatage annoncé. Jamais deux tours fusionnés, jamais de points
de suspension, jamais le libellé du locuteur ni les crochets d'horodatage.

La preuve secondaire doit apporter un élément **distinct**, pas reformuler la
première. Cette règle est demandée au modèle et **rien dans le code ne la
vérifie** — c'est une limite connue.

### Le contrôle, et ce qu'il ne prouve pas

`report_citations._evidence_valid` cherche la citation dans le transcript, à
l'horodatage annoncé, après normalisation de la casse, des accents et des
espaces — et rien d'autre. Il établit qu'un extrait **existe**. Il ne dit ni si
l'extrait soutient l'affirmation, ni si l'affirmation est vraie.

Une affirmation dont la citation n'est pas retrouvée n'est donc **pas fausse** :
elle est non vérifiée, souvent parce que le modèle a paraphrasé.

---

## 6. Le schéma JSON, et pourquoi il ne dit pas la même chose que le texte

`report_citations.output_schema` porte :

| contrainte | dans le schéma | dans le texte |
|---|---|---|
| nombre de sections | **oui** (`minItems` = `maxItems` = N) | oui |
| `question_id` dans la plage | **oui** | oui |
| `answer_status` parmi deux valeurs | **oui** (`enum`) | oui |
| clés exactes, rien de plus | **oui** (`additionalProperties: false`) | oui |
| preuve principale / secondaire | **oui** (deux champs, le second nullable) | oui |
| **max 5 affirmations par section** | **NON** | **oui** |
| max 10 affirmations au total | **NON** | oui |

**Cette asymétrie est délibérée, et elle est le cœur du réglage actuel.**

Les deux moteurs ne lisent pas la même chose. Mistral honore le schéma au
décodage ; `mlx_lm.server` l'accepte puis **l'ignore** — mesuré, et confirmé
par le mainteneur amont, qui a refusé la fonctionnalité. Le modèle local n'a
donc que le texte.

Remettre `maxItems: 5` dans le schéma ramènerait par la porte du cloud un
nivellement de la matière qu'on a retiré : sans bride, la répartition réelle est
très inégale — `[4, 8, 6, 1, 3, 1]` chez Mistral. C'est le transcript qui décide
de la densité d'une section, pas une constante.

Et le plafond **total** n'est exprimable dans aucun schéma : JSON Schema n'a pas
d'arithmétique entre propriétés sœurs. Aucun moteur de décodage contraint ne
pourra jamais l'imposer. Il ne peut être qu'appliqué après coup.

---

## 7. Validation et sélection

`report_citations.render_structured` fait trois choses, dans cet ordre.

**Il valide.** Toute violation lève une `StructuredReportError` portant un
**code stable** — `TOO_MANY_EVIDENCE`, `MISSING_PRIMARY_EVIDENCE`,
`NOT_FOUND_WITH_CLAIMS`, `UNKNOWN_SECTION`… Ce code, et non le message,
alimente la reprise dirigée : un message peut porter un fragment de la sortie
générée, et en faire une consigne rendrait le prompt dépendant de ce que le
modèle vient d'écrire.

**Il sélectionne.** Le plafond total est appliqué en gardant **les affirmations
dont la citation a été vérifiée**, avec une garantie d'au moins une par section
répondue. Ce qui n'est pas vérifiable ne complète pas le document : remplir le
budget avec du non vérifié ne sauve pas une affirmation prouvée de plus, et fait
baisser la part vérifiable du document.

Conséquence assumée : **le compte-rendu est plus court quand le modèle a moins
bien cité.** Sa longueur cesse d'être constante et devient une information.

**Il rend le Markdown.** Corps éditorial d'un côté, annexe des sources de
l'autre, séparés par un marqueur. Une citation valide donne un badge
d'horodatage ; une citation invalide reste dans l'annexe et devient une réserve.
Ce qui a été écarté est **annoncé**, avec son critère.

---

## 8. La reprise, quand le contrat n'est pas tenu

Trois tentatives métier. La température **ne monte pas** et la graine **ne
change pas** — ce qui varie est l'**instruction** : chaque reprise porte en tête
une correction ciblée, choisie sur le code de refus.

Monter la température pour corriger une faute de cardinalité serait
contre-productif : on rendrait le modèle plus variable au moment précis où on
lui demande plus de discipline structurelle.

Une **troncature** n'est pas rejouée. Elle ne se répare pas en redemandant la
même chose, et c'est mesuré : trois tirages, trois troncatures, à 427, 417 et
461 secondes.

---

## 9. Limites connues

- **La preuve secondaire n'est jamais `null` en pratique.** Le champ facultatif
  est rempli systématiquement par le modèle local. La borne réelle est donc
  « deux preuves », pas « une, plus une si utile ».
- **La pertinence d'une citation n'est pas vérifiée** — seulement son existence.
- **Le taux d'affirmations fondées varie fortement** selon le transcript. La cause
  n'est pas établie.
- **Le prompt système est bilingue**, chaque phrase en français puis en anglais.
  Cela double sa longueur, et l'effet sur un petit modèle n'a pas été mesuré.
- **Le contrat et les questions apparaissent deux fois** dans le prompt. Ce
  choix visait la robustesse ; rien ne vérifie qu'il aide.

---

## 10. Où regarder dans le code

| ce que vous cherchez | fichier | fonction |
|---|---|---|
| les questions de chaque gabarit | `core/templates.py` | `_QUESTIONS`, `questions` |
| le choix du gabarit | `core/report.py` | `classify_genre` |
| la normalisation d'une réponse de classifieur | `core/templates.py` | `normalize` |
| l'assemblage du prompt | `core/report.py` | `make_report` |
| le découpage et les synthèses | `core/report.py` | `split_into_chunks`, `map_summarize_chunks` |
| la boucle de reprise | `core/report.py` | `_generer_sous_contrat`, `_rappel_correctif` |
| le contrat en texte | `core/report_citations.py` | `prompt_contract` |
| le schéma JSON | `core/report_citations.py` | `output_schema` |
| la validation et le rendu | `core/report_citations.py` | `render_structured` |
| la sélection des affirmations | `core/report_citations.py` | `_selectionner` |
| le contrôle d'une citation | `core/report_citations.py` | `_evidence_valid` |
