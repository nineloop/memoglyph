# Maintenance, corbeille et portée des projets

Archives historiques, corbeille et suppression, portée des projets et des voix.

## Maintenance des archives historiques

L'écran **Maintenance** distingue les archives fidèles déjà présentes, les
originaux encore exploitables et les documents devenus impossibles à compléter.
Le CLI simule toujours par défaut :

```bash
.venv/bin/python -m core.faithful_backfill
.venv/bin/python -m core.faithful_backfill --apply
```

Le mode `--apply` n'utilise jamais l'archive d'écoute comme original, vérifie le
PCM 16 kHz mono et publie chaque FLAC atomiquement sans écrasement.

### Corbeille et suppression

Archiver retire le Markdown actif de la bibliothèque, de FTS5, des vecteurs
`sqlite-vec`, du RAG et du registre des voix. Le Markdown, les archives audio et
les empreintes restent dans la Corbeille pour permettre une restauration réelle.
Lorsqu'un transcript possède des comptes-rendus liés par leur identifiant stable,
la confirmation les liste et les archive avec leur source.

La suppression définitive d'un élément de la Corbeille retire aussi ses
empreintes et ses archives audio, puis vérifie qu'aucune des huit traces connues
ne subsiste. Une trace partagée avec une autre copie récupérable est conservée.
Si un magasin résiste, le Markdown et son manifeste restent visibles : l'erreur
est annoncée et l'opération peut être relancée.

### Portée projet — et la contrepartie sur les voix

Un projet **borne l'accès** aux documents, plus seulement l'affichage : lire,
télécharger, écouter, générer un compte-rendu, déplacer ou supprimer exige de
dire depuis quelle portée (`view_scope`), sans valeur par défaut. La portée d'un
document est celle de son **dossier** ; `view_scope=all` reste le seul
élargissement, explicite, et les liens partagés `?projet=all` fonctionnent.

**Les voix sont communes à tous les projets d'un compte, et à lui seul.** Une
voix nommée dans un projet est reconnue et nommée dans les autres projets du
même compte : reconnaître la même personne d'une réunion à l'autre suppose un
espace de voix commun, et cloisonner le registre par projet obligerait à
réapprendre chaque interlocuteur dans chaque projet. Entre deux comptes, en
revanche, rien ne passe : la même personne enregistrée par deux comptes fait
deux voix, et un nom donné par l'un n'est jamais montré ni suggéré à l'autre.
Détail en §7 et §8 du SPEC.

---

[Retour au README](../README.fr.md)
