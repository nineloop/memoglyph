# Développement et tests

Commandes de développement et ce que les tests sur vrais modèles exigent de la machine.

Les captures d'écran et les démonstrations s'appuient sur un corpus public et libre
de droits, l'AMI Meeting Corpus : voir [docs/corpus-demo.md](corpus-demo.md).

```bash
make hooks       # installe le hook pre-push local
make check       # lock uv, Ruff, tests Python et UI, lint et build
make test-cov    # couverture core + API
make test-ml     # vrais médias et modèles ; scénarios indisponibles ignorés
make test-ml-strict
```

## Ce que les tests ML exigent de la machine

Ils ne tournent PAS en intégration continue, et pas par oubli : le profil que
la CI installe n'a ni `torch` ni `mlx-whisper`. Cette porte est donc manuelle,
et c'est la seule vérification du pipeline réel qui existe.

| Module | Ce qu'il exige |
|---|---|
| `tests/ml/test_stt_real.py` | un audio de référence (voir ci-dessous) |
| `tests/ml/test_ami_reponse_connue.py` | le corpus de démo AMI (`python tools/corpus_demo.py`), `HF_TOKEN`, `mlx_whisper`, le LLM local |
| `tests/unit/test_embed.py` | le modèle d'embedding local |

**L'audio de référence** vient de l'une de ces deux sources, dans cet ordre :

1. la variable `MEMOGLYPH_ML_AUDIO`, pointant un fichier existant ;
2. le premier fichier audio de `tests/fixtures/audio/`.

Ce dossier est **gitignoré** : l'audio est personnel, il ne part pas sur le
distant. Un extrait de 30 secondes suffit — les tests vérifient des propriétés
(horodatages monotones, absence de boucle de répétition, granularité des
segments), pas un contenu.

```bash
ffmpeg -i "chemin/vers/votre-enregistrement.m4a" -t 30 -c copy \
    tests/fixtures/audio/extrait-30s.m4a
```

Sans cet extrait, sept tests de propriété STT se SAUTENT — et un test qui se
saute ne rapporte rien.

Les tests ML s'exécutent dans des processus isolés afin qu'un crash natif
Metal/MLX n'emporte pas le superviseur. Ils vérifient des propriétés —
horodatages monotones, absence de boucles, granularité — plutôt qu'un texte exact.

Les fichiers `requirements*.txt` restent fournis pour compatibilité `pip`, mais
`pyproject.toml` et `uv.lock` constituent le chemin d'installation de référence.

---

[Retour au README](../README.fr.md)
