# Provenance des générations

D'où vient chaque génération : modèle, fournisseur, version.

Chaque nouveau transcript et chaque nouveau compte-rendu porte un bloc
`pipeline` dans son front-matter YAML. Il identifie la version de Memoglyph, la
révision Git et l'état du code, les réglages effectifs des modèles et un hash du
code pertinent. Le compte-rendu référence aussi le fingerprint du transcript
qui lui a servi de source.

Le bloc transcript inclut également le SHA-256 du flux PCM normalisé réellement
fourni aux modèles. Lorsqu'un document est retraité depuis une archive fidèle,
`derived_from_document_id` relie la nouvelle génération à l'ancienne sans
réutiliser son identité ni l'écraser.

Ce fingerprint décrit le contexte de génération ; il ne promet pas une sortie
bit à bit reproductible des modèles non déterministes. Les anciens documents ne
sont pas modifiés automatiquement : l'absence du bloc signifie
`legacy_unknown`. Aucun secret, hôte de service ou chemin local n'est consigné.

---

[Retour au README](../README.fr.md)
