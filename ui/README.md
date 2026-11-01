# Interface Memoglyph

Frontend React 19, TypeScript et Vite de Memoglyph. En développement, les appels
`/api/*` sont transmis au backend FastAPI par le proxy configuré dans
`vite.config.ts`.

Le lancement normal se fait depuis la racine afin de démarrer et arrêter les
deux processus ensemble :

```bash
./start.sh
./stop.sh               # refuse pendant un traitement ; --forcer arrête quand même
./restart.sh            # transmet --forcer à stop.sh, le reste à start.sh
```

Par défaut, Vite écoute explicitement sur `127.0.0.1`. Pour un accès temporaire
depuis le réseau local, utiliser `./start.sh --lan` à la racine ; l'API
reste locale et passe par le proxy `/api`. La connexion reste obligatoire,
comme en local ; le trafic n'est pas chiffré (HTTP) : réseau de confiance uniquement.

Pour travailler uniquement sur le frontend, avec une API déjà démarrée :

```bash
npm --prefix ui ci
MEMOGLYPH_UI_PORT=8770 MEMOGLYPH_API_PORT=8771 npm --prefix ui run dev
```

Contrôles disponibles :

```bash
npm --prefix ui test
npm --prefix ui run lint
npm --prefix ui run build
```

Les tests utilisent Vitest, Testing Library et jsdom. La validation complète du
dépôt reste `make check` depuis la racine.
