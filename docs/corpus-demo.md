# Corpus de démo : AMI Meeting Corpus

Les captures d'écran, les tests sur vrais modèles et les démonstrations de Memoglyph
s'appuient sur un corpus **public et libre de droits**, jamais sur des réunions
réelles d'utilisateurs.

## Ce qu'il contient

Trois réunions-scénarios du **AMI Meeting Corpus**, une par site
d'enregistrement, chacune à quatre participants, en anglais :

| Réunion | Site | Durée | Fichier |
|---|---|---|---|
| `ES2002a` | Université d'Édimbourg | ≈ 21 min | `ES2002a.Mix-Headset.wav` |
| `IS1009a` | IDIAP | ≈ 14 min | `IS1009a.Mix-Headset.wav` |
| `TS3003a` | TNO | ≈ 25 min | `TS3003a.Mix-Headset.wav` |

Chaque fichier est le mélange des micros-casques des participants (« headset
mix »), en WAV 16 kHz mono. L'archive `ami_public_manual_1.6.2.zip` fournit la
transcription de référence mot à mot de chaque locuteur (`words/<réunion>.<A-D>.words.xml`),
à laquelle le test à réponse connue (`make test-ml`) confronte la transcription et la
diarisation de Memoglyph.

## Récupération

Le corpus n'est **jamais versionné**. Il se télécharge dans `demo/ami/`, dossier
ignoré par git :

```bash
python tools/corpus_demo.py              # télécharge ce qui manque (≈ 139 Mo) et vérifie
python tools/corpus_demo.py --verifier   # vérifie sans rien télécharger
```

Chaque fichier est vérifié contre son empreinte SHA-256, déclarée dans
`tools/corpus_demo.py`. Un fichier présent mais différent est signalé, jamais
écrasé.

## Où il sert

- `tools/corpus_demo.extrait` découpe des extraits de deux minutes à l'exécution, pour
  les parcours de vérification.
- La bibliothèque de démo, `demo/bibliotheque/output/`, est produite sur ce corpus.

## Licence et attribution

Le AMI Meeting Corpus et ses annotations sont publiés sous licence
**Creative Commons Attribution 4.0 International (CC BY 4.0)**
(<https://creativecommons.org/licenses/by/4.0/>), comme l'indique le fichier
`LICENCE.txt` de l'archive d'annotations. Source :
<https://groups.inf.ed.ac.uk/ami/corpus/>.

Toute capture ou tout extrait tiré de ce corpus doit porter l'attribution
suivante :

> Données : AMI Meeting Corpus, University of Edinburgh et partenaires du
> projet AMI, sous licence CC BY 4.0. Référence : J. Carletta et al., « The AMI
> Meeting Corpus: A Pre-announcement », *Machine Learning for Multimodal
> Interaction* (MLMI), 2005.
