# Local ou en ligne

Ce qui reste sur le Mac par défaut, ce qui peut partir vers un fournisseur en ligne, et ce qui ne part jamais sans autorisation. Les citations et leurs limites sont dans [limites.md](limites.md) ; les réglages dans [configuration.md](configuration.md).

## Le réglage de départ : tout en local

Le pipeline par défaut n'utilise que des modèles locaux : Whisper et un modèle de langage MLX (`Qwen3-4B-Instruct`) servi sur le Mac. La diarisation (pyannote) est facultative et demande une installation à part (voir [installation.md](installation.md)). Par défaut, la recherche sémantique utilise un modèle d'embeddings local ; `MEMOGLYPH_EMBED_BACKEND=ollama` conserve l'ancien comportement (un serveur Ollama).

## Les fournisseurs en ligne, facultatifs

Cinq fournisseurs en ligne sont facultatifs pour les comptes rendus, l'Assistant et les faits de la réunion : **Mistral**, **OpenAI**, **Anthropic** (Claude), puis **OpenRouter** et **Groq** (décrits plus bas). Mistral peut aussi transcrire. Il faut les choisir explicitement ; ils envoient alors de l'audio et/ou du texte à leur API.

- **Mistral** : les comptes rendus et l'Assistant utilisent `ministral-14b-2512`, que le palier par défaut de Mistral sert (`mistral-small` y a un quota nul). Voxtral transcrit une réunion de plus de 50 minutes en tranches qui se recouvrent, envoyées au rythme de la limite audio du palier par défaut (3 600 secondes d'audio par minute) ; les horodatages sont recalés sur la réunion entière et les locuteurs raccordés d'une tranche à l'autre.
- **Anthropic** : les comptes rendus et l'Assistant utilisent Claude Haiku 5.5, les faits de la réunion Claude Sonnet 5.5.

## OpenRouter et Groq : deux services en ligne, sous leur nom

**OpenRouter** et **Groq** sont deux services en ligne que l'on peut ajouter. Chacun apparaît sous son nom, partout où les fournisseurs prêts apparaissent (régénérer un compte rendu, transcrire, relancer les faits) ; ils ne s'affichent jamais comme un serveur local ou comme un cloud privé, et plusieurs peuvent être prêts à la fois. Un fournisseur n'est proposé que lorsque sa clé et son modèle sont renseignés dans `.env` (`OPENROUTER_API_KEY` et `MEMOGLYPH_OPENROUTER_MODEL`, `GROQ_API_KEY` et `MEMOGLYPH_GROQ_MODEL`, voir [configuration.md](configuration.md)). Après avoir ajouté ou changé une clé dans `.env`, redémarrer l'instance. Aucun modèle n'est imposé par le code : c'est vous qui choisissez, et l'adresse de chaque service est fixe. Chaque requête envoyée à OpenRouter demande en plus un effort de raisonnement faible (`reasoning.effort`, un paramètre que le service documente), pour qu'un modèle de raisonnement laisse de la place au texte visible ; ce que ce paramètre change pour un modèle donné n'est pas garanti. Groq ne reçoit rien de plus, et aucun en-tête d'attribution n'est ajouté.

Ils suivent la règle des fournisseurs en ligne : un document ultra-confidentiel n'y part pas sans l'autorisation de son propriétaire, et la règle est relue avant chaque appel.

**Modèles gratuits.** Un modèle que le service marque lui-même comme gratuit (chez OpenRouter, un identifiant qui finit par `:free`, ou le routeur `openrouter/free`) reçoit, par défaut, les documents « public · normal » et « confidentiel », jamais un document ultra-confidentiel : celui-ci est refusé, même si son propriétaire a autorisé le cloud, avec un message qui le dit. La page de collecte de données d'OpenRouter, citée plus bas, indique que les réglages d'entraînement des fournisseurs sont séparés pour les modèles gratuits et pour les modèles payants. Chez Groq, aucun modèle n'est marqué gratuit par le service : la règle ne s'y applique pas. Cette règle est un choix de protection par défaut, réglable. **Quota quotidien.** Selon la documentation d'OpenRouter ([limites](https://openrouter.ai/docs/api_reference/limits), lue le 4 novembre 2026), les modèles gratuits sont limités à 20 requêtes par minute, et à 50 requêtes par jour pour un compte qui a acheté moins de 10 crédits (1000 par jour à partir de 10 crédits). Un compte rendu fait de nombreux appels au modèle (9 lors d'un essai réel avec un modèle gratuit) : un compte rendu complet peut donc épuiser le quota gratuit d'une journée, et le compte rendu échoue alors : l'écran affiche la phrase générale « le modèle de langage n'a pas répondu », et seul le détail technique nomme le quota atteint. Quand un quota est atteint pendant un compte rendu, une relance des faits ou le profilage d'une transcription, l'application attend le délai que le service annonce (60 secondes au plus, 20 secondes si le service n'en donne pas), puis réessaie, jusqu'à deux fois par appel. La pause est publiée dans l'état du traitement (« Quota du fournisseur : reprise dans N s ») ; seuls l'écran Transcrire et le panneau de maintenance affichent ce texte, les autres écrans (dont la régénération d'un compte rendu et la liste des traitements) montrent seulement « étape N sur M » : une régénération peut donc rester jusqu'à 5 minutes sur la même étape sans le montrer. L'attente totale d'un traitement est plafonnée à 5 minutes ; au-delà, les appels refusés échouent comme avant : une tranche non résumée est perdue (le document le dit), et le traitement n'échoue que si tout est refusé. Cela ne fait pas disparaître le quota : chaque réessai d'un appel refusé sans délai annoncé attend 20 secondes, soit jusqu'à 40 secondes par appel et 5 minutes en tout, avant que la tranche soit perdue. L'Assistant n'attend jamais. Un appel refusé laisse une ligne d'usage avec son code seul (par exemple 429 ou 404), jamais la réponse du service ; le compte d'appels de l'usage inclut désormais ces appels refusés. Un compte rendu mené à terme avec un modèle gratuit n'a pas encore été vérifié.

**Ce que disent les services de leurs données.** Les pages ci-dessous sont citées telles quelles, sans jugement ; la politique en vigueur est celle qu'elles publient.

- OpenRouter, [collecte de données](https://openrouter.ai/docs/guides/privacy/data-collection) et [journalisation par les fournisseurs](https://openrouter.ai/docs/guides/privacy/provider-logging) : ces pages indiquent qu'OpenRouter ne stocke pas les invites ni les réponses sauf activation explicite, que les métadonnées des requêtes sont stockées, et que les réglages d'entraînement sont séparés pour les modèles payants et gratuits. Les pages lues ne donnent pas le libellé exact de l'interrupteur du compte pour les modèles gratuits ; la politique de confidentialité complète est à [openrouter.ai/privacy](https://openrouter.ai/privacy).
- Groq, [Your Data in GroqCloud](https://console.groq.com/docs/your-data) : cette page indique que, par défaut, Groq ne conserve pas les données des requêtes d'inférence, avec des exceptions qu'elle liste, et qu'une option de rétention nulle peut être activée. La page lue ne précise pas ce qu'il en est de l'entraînement sur les données des clients ; la politique de confidentialité de Groq est à [groq.com/privacy-policy](https://groq.com/privacy-policy).

## Ce qui ne part pas : l'ultra-confidentiel

Un document étiqueté **ultra-confidentiel** ne part vers aucun fournisseur en ligne par défaut : la transcription, le compte rendu, les faits et les extraits de l'Assistant restent locaux. Chaque compte peut autoriser le cloud pour ses propres documents ultra-confidentiels dans **Confidentialité** (inactif par défaut, confirmé à l'activation, effectif aussitôt à la désactivation). Ce réglage n'ouvre rien pour les documents partagés ni pour un autre compte, et chaque envoi autorisé est consigné sans contenu (voir [SECURITY.md](../SECURITY.md)).

## Un serveur de modèle auto-hébergé

Un serveur de modèle hébergé par l'utilisateur (MLX, ou un serveur compatible OpenAI comme Ollama, LM Studio ou vLLM, réglé dans `.env`) ne compte comme local que sur l'adresse de bouclage de cette machine. Sur tout autre hôte, il s'affiche **Cloud privé** et suit les règles du cloud, ultra-confidentiel compris.

## Une sensibilité par réunion

Chaque réunion porte une seule sensibilité, relue avant chaque appel en ligne. **Confidentiel** est une étiquette pour les personnes : elle n'oriente rien d'elle-même, et c'est l'humain qui décide de ce qui part où.

---

[Retour au README](../README.fr.md)
