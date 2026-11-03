.PHONY: lock-check lint-python test test-python test-ui test-cov test-ml test-ml-strict check hooks companion readme

# Interpréteur : le venv du projet s'il existe, sinon python3. Sans ça, `make`
# invoquait `python` tout court — absent du PATH hors venv activé — et les tests
# risquaient de tourner sur un autre interpréteur que l'application.
PYTHON ?= $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)

lock-check:
	uv lock --check

# `LINT_EN_PLUS` : les chemins qu'un fichier `tools/*.mk` du dépôt de développement
# ajoute au lint, quand il en porte ; vide sinon.
LINT_EN_PLUS ?=

lint-python:
	# Tout le Python suivi est linté, scripts racine compris : un dossier hors
	# du lint est celui où un défaut passe sans que personne le voie.
	$(PYTHON) -m ruff check api core tests tools run_ml_tests.py $(LINT_EN_PLUS)

# Deux cibles SÉPARÉES, et c'est le point.
#
# `make` interrompt une recette à la première ligne en échec. Les deux commandes
# vivaient dans la même cible : un échec Python effaçait toute preuve côté
# interface. 113 tests UI, le typecheck et le build n'ont ainsi pas tourné une
# seule fois en CI sur dix-neuf push, dont huit touchant `ui/`. Le
# workflow les lance maintenant en deux étapes, la seconde en
# `if: always()`.
#
# `make -k` ne réglait PAS ce cas : `-k` poursuit les cibles INDÉPENDANTES, pas
# les lignes d'une même recette, et ne construit pas une cible dont un
# prérequis a échoué. Vérifié plutôt que supposé.
test-python:
	$(PYTHON) -m pytest -m "not slow and not ml"

test-ui:
	npm --prefix ui test

test: test-python test-ui

# Règle : plancher à 80 %. Couverture mesurée à la pose du plancher :
# 82 %.
#
# Sans `--cov-fail-under`, cette cible n'était qu'un AFFICHAGE : elle rendait 0
# quel que soit le chiffre. Un module de 1600 lignes devenu mort faisait tomber
# la couverture de 82 à 68 % — exit 0. Et personne ne l'appelait : ni la CI, ni
# le hook, ni aucun script. `pytest-cov` était installé en CI pour rien.
#
# Le plancher n'est pas une cible à atteindre, c'est une alarme de CHUTE. Deux
# points de marge : il ne bloque rien aujourd'hui et parle si ça décroche.
test-cov:
	$(PYTHON) -m pytest -m "not slow and not ml" --cov=core --cov=api \
		--cov-report=term-missing --cov-fail-under=80

# Tests ML : préflight sans import natif, puis un processus isolé par module.
# La cible optionnelle ignore explicitement les scénarios dont les médias ou
# services manquent. La cible stricte exige que TOUS soient exécutables.
test-ml:
	$(PYTHON) run_ml_tests.py

test-ml-strict:
	$(PYTHON) run_ml_tests.py --strict

# Les deux README (README.md en anglais, README.fr.md en français) sont GÉNÉRÉS : on modifie les
# rubriques de `readme/` ou le gabarit `readme/readme.yaml`, puis on lance cette cible. Elle ne
# fait pas partie de `check` ; un test vérifie que les README publiés sont à jour.
readme:
	$(PYTHON) tools/readme.py generer

# Portail local complet. La CI, elle, éclate ces étapes pour qu'un échec
# Python n'empêche pas de savoir où en est l'interface.
check: lock-check lint-python test
	npm --prefix ui run lint
	npm --prefix ui run build


# Cibles propres au dépôt de développement, quand il les porte (`tools/*.mk`).
-include $(wildcard tools/*.mk)

# Active les hooks git versionnés (.githooks/). À lancer une fois après un clone :
# `core.hooksPath` est une configuration LOCALE, elle ne se propage pas avec le dépôt.
hooks:
	git config core.hooksPath .githooks
	@echo "✅ hooks git actifs — pre-push lance 'make check' avant chaque push."

# Compagnon de capture (macOS) : un vrai paquet .app, pour que macOS attribue les
# autorisations du micro et du son du système au compagnon lui-même, pas au Terminal.
# Signature ad hoc par défaut ; `SIGN_IDENTITY` (une identité du trousseau) la remplace.
# Le paquet est assemblé sous compagnon/.build, puis copié dans `DEST`, /Applications par
# défaut. Si /Applications n'est pas accessible en écriture (compte sans droits
# d'administration), la cible le dit et se replie sur ~/Applications ; un `DEST` donné
# n'a pas de repli : il est pris tel quel. `OPEN_R` montre le paquet dans le Finder à la
# fin ; vide, rien ne s'ouvre (essais, CI).
DEST_DEFAUT ?= /Applications
DEST_REPLI ?= $(HOME)/Applications
DEST ?= $(DEST_DEFAUT)
OPEN_R ?= open -R
SIGN_IDENTITY ?= -
COMPAGNON_APP = Memoglyph compagnon.app
COMPAGNON_PAQUET = compagnon/.build/paquet/$(COMPAGNON_APP)
companion:
	cd compagnon && swift build -c release --product memoglyph-compagnon
	mkdir -p "$(COMPAGNON_PAQUET)/Contents/MacOS"
	cp compagnon/.build/release/memoglyph-compagnon "$(COMPAGNON_PAQUET)/Contents/MacOS/memoglyph-compagnon"
	cp compagnon/Sources/memoglyph-compagnon/Info.plist "$(COMPAGNON_PAQUET)/Contents/Info.plist"
	mkdir -p "$(COMPAGNON_PAQUET)/Contents/Resources" compagnon/.build/icone
	swift compagnon/Outils/generer-icone.swift ui/public/brand/memoglyph-symbole-sombre.svg compagnon/.build/icone/Memoglyph.iconset
	iconutil --convert icns compagnon/.build/icone/Memoglyph.iconset --output "$(COMPAGNON_PAQUET)/Contents/Resources/Memoglyph.icns"
	test -s "$(COMPAGNON_PAQUET)/Contents/Resources/Memoglyph.icns"
	codesign --sign "$(SIGN_IDENTITY)" --identifier org.memoglyph.compagnon "$(COMPAGNON_PAQUET)"
	codesign --verify --strict "$(COMPAGNON_PAQUET)"
	@dest="$(DEST)"; \
	if [ "$$dest" = "$(DEST_DEFAUT)" ] && [ ! -w "$$dest" ]; then \
		echo ""; \
		echo "$(DEST_DEFAUT) n'est pas accessible en écriture pour ce compte :"; \
		echo "installation dans $(DEST_REPLI) à la place."; \
		echo "(Pour $(DEST_DEFAUT) : un compte administrateur ; ailleurs : DEST=<dossier>.)"; \
		dest="$(DEST_REPLI)"; \
	fi; \
	mkdir -p "$$dest" && ditto "$(COMPAGNON_PAQUET)" "$$dest/$(COMPAGNON_APP)" || exit 1; \
	echo ""; \
	echo "Compagnon installé : $$dest/$(COMPAGNON_APP)"; \
	echo "$$dest/$(COMPAGNON_APP)" > compagnon/.build/paquet/installe.txt
	@echo "Au premier démarrage en Visio, macOS demande deux autorisations :"
	@echo "  - Micro : Réglages Système > Confidentialité et sécurité > Microphone ;"
	@echo "  - son des applications : Réglages Système > Confidentialité et sécurité >"
	@echo "    Enregistrement de l'écran et de l'audio du système (section audio du système)."
	@echo "Acceptez-les pour « Memoglyph compagnon », puis démarrez à nouveau."
	@if [ "$(SIGN_IDENTITY)" = "-" ]; then \
		echo "Signature ad hoc : une recompilation change l'empreinte du paquet, et macOS peut"; \
		echo "redemander ces autorisations. Avec SIGN_IDENTITY=\"<identité>\", elles restent."; \
	else \
		echo "Signé avec $(SIGN_IDENTITY) : les autorisations restent d'une recompilation à l'autre."; \
	fi
	@if [ -n "$(OPEN_R)" ]; then $(OPEN_R) "$$(cat compagnon/.build/paquet/installe.txt)"; fi
