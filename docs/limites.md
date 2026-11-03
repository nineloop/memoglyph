# Limites connues

Ce que Memoglyph ne fait pas, ou pas encore, ou peut mal faire.

- Les citations sont des repères pour vérifier la transcription et l'audio, pas des preuves. Le contrôle confirme qu'un extrait figure tel quel dans la transcription au timestamp annoncé ; mais la transcription peut se tromper (un mot ou un chiffre mal entendu, une voix attribuée à la mauvaise personne), et un modèle peut tirer d'un extrait ce qu'il ne dit pas. Cliquez sur la citation, écoutez l'audio, et vérifiez ce qui compte avant de vous y fier. Le profil LLM aide au choix du gabarit mais n'est jamais remis au générateur comme parole source. Une relecture humaine reste nécessaire pour les inférences importantes. Les anciens comptes-rendus ne sont pas modifiés ni déclarés vérifiés rétroactivement.
- Un montant à décimales peut perdre sa virgule à la transcription : Whisper écrit « 1250 » pour « douze cinquante ». Le texte ne permet pas de le voir, puisque le chiffre est bien dans la citation : écoutez la citation d'un montant avant de vous y fier (mesure dans [docs/ecl.md](ecl.md), « La transcription et les chiffres »).
- Une boucle de répétition de Whisper est re-décodée une fois ; si elle persiste, elle est repliée avec sa marque et signalée en réserve dans la réunion.
- La stratégie **Map-Reduce** découpe automatiquement les longs enregistrements par tranches de 15 000 caractères, assurant une couverture complète même sur les transcripts étendus.
- Une réunion avec beaucoup de voix peut être sur-segmentée si aucune borne de locuteurs n'est renseignée.
- Deux anciens transcripts n'ont plus leur média original et ne peuvent donc pas recevoir rétroactivement une archive FLAC fidèle.

---

[Retour au README](../README.fr.md)
