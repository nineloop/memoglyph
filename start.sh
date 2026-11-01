#!/usr/bin/env bash
#
# Démarre la stack de dev Memoglyph : backend FastAPI + frontend Vite.
#
# Ports : plage dédiée peu commune, pilotée par UN bouton (base) + auto-décalage.
#   ./start.sh                        # UI=8770, API=8771 (ou 1re paire libre : 8780/81, …)
#   ./start.sh --lan                  # rend l'UI accessible sur le réseau local
#   MEMOGLYPH_PORT_BASE=9200 ./start.sh
#   MEMOGLYPH_UI_PORT=8770 MEMOGLYPH_API_PORT=8771 ./start.sh   # forcer explicitement
#   MEMOGLYPH_ATTENTE_S=1800 ./start.sh   # attente maximale du démarrage (900 s par défaut)
#
set -euo pipefail
cd "$(dirname "$0")"

usage () {
  cat <<'EOF'
Usage : ./start.sh [--lan]

  sans option  UI disponible uniquement sur cette machine (127.0.0.1)
  --lan        UI disponible sur le réseau local (0.0.0.0)

L'API FastAPI reste locale dans les deux cas ; Vite proxifie /api vers elle.
Le mode LAN expose l'écran de connexion au réseau, en HTTP : réseau de confiance uniquement.
EOF
}

UI_HOST="${MEMOGLYPH_UI_HOST:-127.0.0.1}"
case "${1:-}" in
  "") ;;
  --lan) UI_HOST="0.0.0.0" ;;
  -h|--help) usage; exit 0 ;;
  *) echo "❌ Option inconnue : $1"; usage; exit 2 ;;
esac
if [ "$#" -gt 1 ]; then
  echo "❌ Une seule option est acceptée."
  usage
  exit 2
fi

if [ -z "${PYTHON:-}" ] && [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
fi
PYTHON="${PYTHON:-python3}"
BASE="${MEMOGLYPH_PORT_BASE:-8770}"
# L'API ne répond qu'après avoir réindexé documents, faits et voix (son
# `lifespan`). Sur une grande bibliothèque, ou après un changement de version
# de l'index, cela prend plusieurs minutes : un plafond court concluait à
# l'échec d'une API qui démarrait normalement. On attend donc tant que les
# processus vivent ; ce plafond ne sert que contre un démarrage bloqué.
ATTENTE_S="${MEMOGLYPH_ATTENTE_S:-900}"

preflight () {
  if ! "$PYTHON" -c 'import fastapi, uvicorn, sqlite_vec, httpx' 2>/dev/null; then
    echo "❌ Dépendances introuvables pour l'interpréteur choisi."
    echo "   Interpréteur : $("$PYTHON" -c 'import sys; print(sys.executable, sys.version.split()[0])' 2>/dev/null || echo "$PYTHON (illisible)")"
    if [ -x ".venv/bin/python" ]; then
      echo "   Le venv du projet existe pourtant : relance sans forcer PYTHON,"
      echo "   ou explicitement :  PYTHON=.venv/bin/python $0"
    else
      echo "   Aucun .venv dans le projet. Crée-le :"
      echo "     uv sync --locked"
      echo "   (sans uv :  python3.11 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt)"
    fi
    exit 1
  fi
  if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "❌ ffmpeg introuvable sur le PATH."
    echo "   La transcription échouerait APRÈS l'upload, pas maintenant."
    echo "   Installe-le :  brew install ffmpeg"
    exit 1
  fi
}
preflight

# Un port est pris s'il a un serveur qui ÉCOUTE : un client encore connecté
# (un onglet ouvert) ne compte pas. Même règle que `stop.sh`.
port_busy () { lsof -ti:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

# Une pile déjà lancée occupe ses ports : sans ce relevé, un second
# `./start.sh` les croyait pris par autre chose, décalait sur la paire
# suivante, réécrivait `.memoglyph-ports` et attendait des serveurs qui n'y
# étaient pas.
pile_en_marche () {
  local pidf
  for pidf in .memoglyph-api.pid .memoglyph-ui.pid; do
    if [ -f "$pidf" ] && kill -0 "$(cat "$pidf" 2>/dev/null)" 2>/dev/null; then return 0; fi
  done
  return 1
}

UI_ENV="${MEMOGLYPH_UI_PORT:-}"
API_ENV="${MEMOGLYPH_API_PORT:-}"

REPRISE=0
if [ -z "$UI_ENV" ] && [ -z "$API_ENV" ] && pile_en_marche && [ -f .memoglyph-ports ]; then
  # shellcheck disable=SC1091
  source .memoglyph-ports
  REPRISE=1
  echo "ℹ️  Une pile Memoglyph tourne déjà (UI=$UI_PORT, API=$API_PORT) : ses ports sont repris."
elif [ -z "$UI_ENV" ] && [ -z "$API_ENV" ]; then
  found=""
  for off in 0 10 20 30 40 50 60 70 80 90; do
    ui=$((BASE + off)); api=$((BASE + off + 1))
    if ! port_busy "$ui" && ! port_busy "$api"; then found="$ui $api"; break; fi
  done
  if [ -z "$found" ]; then
    echo "❌ Aucune paire de ports libre autour de $BASE. Essaie MEMOGLYPH_PORT_BASE=9200 ./start.sh"; exit 1
  fi
  UI_PORT="${found% *}"; API_PORT="${found#* }"
else
  UI_PORT="${UI_ENV:-$BASE}"; API_PORT="${API_ENV:-$((BASE + 1))}"
fi

# Une pile reprise garde son fichier de ports tel quel.
[ "$REPRISE" = 1 ] || printf 'UI_PORT=%s\nAPI_PORT=%s\n' "$UI_PORT" "$API_PORT" > .memoglyph-ports
# Les processus enfants lisent `MEMOGLYPH_*` : exporter ces variables leur
# transmet les ports et l'hôte retenus ici.
export MEMOGLYPH_UI_PORT="$UI_PORT" MEMOGLYPH_API_PORT="$API_PORT" MEMOGLYPH_UI_HOST="$UI_HOST"

start_bg () {  # nom, port, pidfile, logfile, commande...
  local name="$1" port="$2" pidf="$3" logf="$4"; shift 4
  if [ -f "$pidf" ] && kill -0 "$(cat "$pidf" 2>/dev/null)" 2>/dev/null; then
    echo "⚠️  $name tourne déjà (PID $(cat "$pidf"))"; return 0
  fi
  rm -f "$pidf"
  if port_busy "$port"; then echo "❌ Port $port occupé — $name non démarré"; return 1; fi
  nohup "$@" > "$logf" 2>&1 &
  echo $! > "$pidf"
  echo "  … $name (PID $!) sur le port $port"
}

echo "🚀 [React] Démarrage de la stack Memoglyph — frontend React (Vite) + backend FastAPI (UI=$UI_PORT, API=$API_PORT)…"
if [ "$UI_HOST" = "0.0.0.0" ]; then
  echo "⚠️  Mode LAN : toute personne sur ce réseau pouvant joindre ce Mac verra l'écran de connexion."
  echo "   La connexion est obligatoire, mais le trafic n'est pas chiffré (HTTP) sur le réseau."
fi

# Sortie non tamponnée : redirigé vers un fichier, Python écrit par blocs, et
# le journal retardait de plusieurs minutes sur ce qui se passait.
start_bg "API FastAPI" "$API_PORT" ".memoglyph-api.pid" "memoglyph-api.log" \
  env PYTHONUNBUFFERED=1 "$PYTHON" -m uvicorn api.main:app --port "$API_PORT" --log-level warning

if ! ( cd ui && start_bg "Front Vite" "$UI_PORT" "../.memoglyph-ui.pid" "../memoglyph-ui.log" npm run dev ); then
  echo "❌ Démarrage partiel : l'API tourne, pas l'interface. ./stop.sh pour tout arrêter."
  exit 1
fi

# Un serveur mort pendant son démarrage est un vrai échec : le dire tout de
# suite, avec la fin de son journal, plutôt qu'à l'issue du plafond.
mort_au_demarrage () {  # nom, pidfile, logfile
  local pid; pid="$(cat "$2" 2>/dev/null || true)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then return 1; fi
  echo; echo "❌ $1 s'est arrêté pendant son démarrage. Fin de $3 :"
  tail -n 20 "$3" 2>/dev/null | sed 's/^/   /'
  echo "   ./stop.sh pour arrêter ce qui reste."
  return 0
}

echo -n "⏳ Attente des serveurs"
debut=$SECONDS
annonce=0
until curl -s -o /dev/null "http://127.0.0.1:${API_PORT}/api/health" 2>/dev/null \
      && curl -s -o /dev/null "http://127.0.0.1:${UI_PORT}/" 2>/dev/null; do
  if mort_au_demarrage "L'API" .memoglyph-api.pid memoglyph-api.log \
     || mort_au_demarrage "L'interface" .memoglyph-ui.pid memoglyph-ui.log; then
    exit 1
  fi
  if [ "$annonce" = 0 ] && [ $((SECONDS - debut)) -ge 10 ]; then
    echo
    echo "   L'API reconstruit son index (documents, faits, voix) avant de répondre :"
    echo "   sur une grande bibliothèque, cela peut prendre plusieurs minutes."
    echo -n "⏳ Attente"
    annonce=1
  fi
  if [ $((SECONDS - debut)) -ge "$ATTENTE_S" ]; then
    echo; echo "❌ Délai dépassé (${ATTENTE_S} s, réglable par MEMOGLYPH_ATTENTE_S). Logs : memoglyph-api.log / memoglyph-ui.log"
    exit 1
  fi
  echo -n "."; sleep 1
done
echo; echo "✅ Memoglyph prêt en $((SECONDS - debut)) s — stack React + FastAPI :"
echo "   • App locale → http://127.0.0.1:${UI_PORT}"
if [ "$UI_HOST" = "0.0.0.0" ]; then
  LAN_IP=""
  if command -v ipconfig >/dev/null 2>&1; then
    for iface in en0 en1 en2; do
      LAN_IP="$(ipconfig getifaddr "$iface" 2>/dev/null || true)"
      [ -z "$LAN_IP" ] || break
    done
  fi
  if [ -n "$LAN_IP" ]; then
    echo "   • App réseau → http://${LAN_IP}:${UI_PORT}"
  else
    echo "   • App réseau → http://<adresse-IP-de-ce-Mac>:${UI_PORT}"
  fi
  # Nom Bonjour : plus stable que l'IP (qui change au bail DHCP). Vite
  # autorise les noms `.local` en mode LAN ; on affiche l'URL correspondante.
  LAN_NAME=""
  if command -v scutil >/dev/null 2>&1; then
    LAN_NAME="$(scutil --get LocalHostName 2>/dev/null || true)"
  fi
  if [ -n "$LAN_NAME" ]; then
    echo "   • App réseau → http://${LAN_NAME}.local:${UI_PORT}"
  fi
fi
echo "   • Arrêt  → ./stop.sh"
# Premier lancement : l'API a tiré un code à usage unique (api/auth.py),
# dans un fichier lisible par le seul propriétaire. Il n'est jamais
# journalisé ; c'est ici, dans le terminal, qu'il s'affiche.
if [ -f .memoglyph-installation ]; then
  echo
  echo "🔑 Premier lancement : aucun compte n'existe encore."
  echo "   Code d'installation, à saisir dans l'écran « Créer le compte administrateur » :"
  echo "      $(cat .memoglyph-installation)"
  echo "   Il ne sert qu'une fois. Mot de passe oublié plus tard : ./memoglyph reset-password <utilisateur>"
fi
exit 0
