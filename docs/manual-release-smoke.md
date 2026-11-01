# Smoke manuel de release — checklist vivante

**But :** vérifier, à la main dans l'interface réelle, qu'un utilisateur peut
aller de zéro à un résultat **avant chaque release**. Cette checklist complète
les tests automatisés ; elle couvre ce qu'ils ne voient pas : l'**expérience UI**
et le **démarrage à froid** (là où vit le défaut le plus grave).

> **Vivant :** cocher les cases pour la release en cours, consigner le passage
> dans le *Journal des passages* en bas, puis remettre les cases à blanc (ou
> dupliquer le bloc) pour la release suivante. Un scénario qui échoue **bloque
> la release** ou est explicitement accepté avec une note.

## Couverture automatisée (à lancer AUSSI)

| Porte | Ce qu'elle prouve | Commande |
|---|---|---|
| `make test-ml` | pipeline réel (vrais médias/modèles) — porte manuelle, hors CI | `make test-ml` |

Ces tests passent par le code et l'**endpoint**, pas par l'UI. Le smoke manuel
ci-dessous teste l'**interface** et le **premier usage**, invisibles pour eux.

## Préparer un environnement FROID (indispensable pour le scénario 0)

Le seul moyen de reproduire l'expérience d'un inconnu est de partir sans modèles
en cache et sans corpus. Sans écraser le corpus réel :

```bash
# 1. Cache Hugging Face vide (ATTENTION : ce cache peut être partagé avec d'autres
#    projets ; sauvegarder/déplacer plutôt que supprimer)
mv ~/.cache/huggingface ~/.cache/huggingface.bak   # à restaurer après

# 2. Racine de données jetable (ne jamais toucher l'output/ réel)
#    Copier core/ + api/ dans un dossier temporaire et l'utiliser comme racine.

# 3. Aucune clé cloud, aucun HF_TOKEN dans le .env de test — pour voir les
#    états désactivés tels qu'un inconnu les rencontre.
```

Restaurer le cache HF après le passage : `mv ~/.cache/huggingface.bak ~/.cache/huggingface`.

---

## Scénarios

Légende : **✅** attendu · **❌** signal d'échec · *Auto* = aussi couvert par un test automatisé.

### 0. Démarrage à FROID — premier téléchargement de modèles
*Précondition : cache HF vide (voir ci-dessus).*
- [ ] Lancer `./start.sh`, ouvrir l'URL affichée.
- [ ] Importer un média court, garder Compte-rendu coché, lancer.
- **✅** Une étape/état **explicite** dit qu'un modèle se **télécharge/charge**
  (texte et/ou progression), pas seulement une barre à 11 %.
- **❌** Barre figée vers 11 %, « Transcribing with Whisper… » sans autre signal
  pendant des minutes (indiscernable d'un blocage).
- *Non automatisé — manuel obligatoire.*

### 1. Créer un projet
- [ ] Trouver et utiliser l'affordance de création de projet.
- **✅** Le projet apparaît et devient la portée active.
- **❌** Aucune affordance visible de création.

### 2. Importer un média
- [ ] Déposer un audio/vidéo dans **Transcrire**.
- **✅** Les blocs 01/02/03 apparaissent, « Lancer » est actif ; les options
  Local/Cloud/Locuteurs/Profilage/CR sont compréhensibles.
- **❌** Rejet silencieux d'un fichier valide ; option cochable qui mène à une
  impasse. *Auto (import).*

### 3. Attendre la transcription
- [ ] Suivre la timeline à 6 étapes.
- **✅** Les étapes avancent jusqu'à « done » ; « Voir la réunion » apparaît.
- **❌** Blocage apparent sans retour d'étape ; **le transcript survit** même si
  une étape d'enrichissement échoue. *Auto (API).*

### 4. Générer un compte-rendu
- [ ] Déclencher le CR (ou le laisser en fin de pipeline).
- **✅** Le CR s'affiche avec citations et réserves visibles.
- **❌** HTTP 503 / erreur brute ; « modèle introuvable » sur un simple
  téléchargement lent. *Auto (API).*

### 5. Rechercher une citation
- [ ] Chercher un terme connu du transcript (mode intelligent, puis exact).
- **✅** Résultats groupés par réunion, clic ouvre au bon timestamp.
- **❌** Résultat vide inexpliqué (sémantique en panne muette) ;
  erreur opaque. *Auto (API).*

### 6. Poser une question RAG
- [ ] Poser une question (portée projet), puis élargir à tous les projets.
- **✅** Réponse avec sources ; la note de portée correspond au fournisseur réel.
- **❌** « modèle local » affiché en mode cloud ; question soumise
  puis échec brut sans LLM. *Auto (API).*

### 7. Supprimer (archiver)
- [ ] Archiver un transcript ou un CR, confirmer.
- **✅** L'élément part dans la **Corbeille** (item de nav propre).
- **❌** La confirmation renvoie vers « Maintenance ». *Auto (API).*

### 8. Restaurer
- [ ] Restaurer depuis la Corbeille.
- **✅** L'élément revient en Bibliothèque, réindexé, sans doublon.
- **❌** Perte / doublon. *Auto (API).*

### 9. Chemins d'erreur volontaires
- [ ] Cloud sans clé ; deux tâches lourdes en même temps (429) ; LLM non prêt.
- **✅** Messages **actionnables** disant quoi faire (pas de « HTTP 503 : … »
  brut, pas de conseil mort).
- **❌** Erreur HTTP brute affichée telle quelle ; conseil pointant
  vers Ollama / un champ Maintenance inexistant.
- *Non automatisé — manuel obligatoire.*

---

## Journal des passages

| Date | Version / commit | Environnement | Résultat | Notes |
|---|---|---|---|---|
| _à remplir_ | | froid / chaud | ✅ / ❌ / accepté | |

> Renseigner une ligne à chaque passage. Un ❌ non accepté bloque la release.
