#!/usr/bin/env bash
#
# Arrête la stack de dev Memoglyph : API, Vite, et le serveur du LLM local que
# l'API a lancé.
#   ./stop.sh            # refuse si un traitement est en cours
#   ./stop.sh --forcer   # arrête quand même, le traitement est perdu
#
# Seuls les processus qui ÉCOUTENT sur nos ports sont visés (`-sTCP:LISTEN`).
# `lsof -ti:PORT` seul rend aussi les CLIENTS connectés : un onglet ouvert sur
# l'interface garde une connexion avec Vite, et le processus réseau du
# navigateur recevait `kill -9`.
#
set -euo pipefail
cd "$(dirname "$0")"

usage () {
  cat <<'EOF'
Usage : ./stop.sh [--forcer]

  sans option  refuse d'arrêter tant qu'un traitement est en cours
               (transcription, faits, voix, archives) et le nomme
  --forcer     arrête quand même : le traitement en cours est perdu
EOF
}

FORCER=0
case "${1:-}" in
  "") ;;
  --forcer) FORCER=1 ;;
  -h|--help) usage; exit 0 ;;
  *) echo "❌ Option inconnue : $1"; usage; exit 2 ;;
esac
if [ "$#" -gt 1 ]; then
  echo "❌ Une seule option est acceptée."
  usage
  exit 2
fi

UI_PORT="${MEMOGLYPH_UI_PORT:-8770}"
API_PORT="${MEMOGLYPH_API_PORT:-8771}"
if [ -f .memoglyph-ports ]; then
  # shellcheck disable=SC1091
  source .memoglyph-ports
fi

stop_one () {  # nom, pidfile, port
  local name="$1" pidf="$2" port="$3" stopped=false
  if [ -f "$pidf" ]; then
    local pid; pid="$(cat "$pidf" 2>/dev/null || true)"
    if [ -n "${pid:-}" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      for _ in $(seq 1 8); do kill -0 "$pid" 2>/dev/null && sleep 1 || { stopped=true; break; }; done
      $stopped || kill -9 "$pid" 2>/dev/null || true
    fi
  fi
  if [ -n "${port:-}" ] && lsof -ti:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    lsof -ti:"$port" -sTCP:LISTEN | xargs kill 2>/dev/null || true
    sleep 1; lsof -ti:"$port" -sTCP:LISTEN | xargs kill -9 2>/dev/null || true
  fi
  if [ -n "${port:-}" ] && lsof -ti:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "❌ $name écoute encore sur le port $port — PID conservé dans $pidf."
    return 1
  fi
  rm -f "$pidf"
  echo "🛑 $name arrêté."
}

# Le serveur `mlx_lm.server` du LLM local est un enfant de l'API, qui ne
# l'arrête pas en s'éteignant : il survivait à `./stop.sh`, gardait plusieurs Go
# et le port 8772. On ne l'arrête que si sa PROPRIÉTÉ est prouvée, par la même
# règle que `core/llm_mlx.py` : le témoin `.cache/mlx-server-<port>.owner`
# porte son PID et son heure de démarrage, et les deux doivent concorder. Un
# PID recyclé n'est jamais touché.
# Vivant = présent et pas zombie. Un processus tué reste « zombie » tant que son
# parent ne l'a pas récupéré, et `kill -0` le dit encore vivant.
vivant () {
  local etat; etat="$(ps -o stat= -p "$1" 2>/dev/null | tr -d '[:space:]' || true)"
  [ -n "$etat" ] && [ "${etat#Z}" = "$etat" ]
}

stop_mlx () {
  local port="${MEMOGLYPH_MLX_PORT:-8772}"
  local temoin=".cache/mlx-server-${port}.owner" pid attendu actuel
  if [ ! -f "$temoin" ]; then
    if lsof -ti:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
      echo "ℹ️  Un serveur écoute sur le port $port sans témoin de propriété : laissé tel quel."
      echo "   lsof -nP -iTCP:$port -sTCP:LISTEN   (puis kill <PID> si c'est bien le LLM de Memoglyph)"
    fi
    return 0
  fi
  pid="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["pid"])' "$temoin" 2>/dev/null || true)"
  attendu="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["demarrage"] or "")' "$temoin" 2>/dev/null || true)"
  if [ -z "$pid" ]; then
    echo "ℹ️  Témoin du serveur LLM illisible ($temoin) : rien n'est arrêté."
    return 0
  fi
  actuel="$(ps -o lstart= -p "$pid" 2>/dev/null | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' || true)"
  if [ -z "$actuel" ]; then
    rm -f "$temoin"   # le serveur est déjà mort : témoin périmé
    return 0
  fi
  if [ -z "$attendu" ] || [ "$actuel" != "$attendu" ]; then
    echo "ℹ️  Le PID $pid du témoin LLM ne correspond plus au serveur lancé par Memoglyph : laissé tel quel."
    return 0
  fi
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 10); do vivant "$pid" && sleep 1 || break; done
  if vivant "$pid"; then kill -9 "$pid" 2>/dev/null || true; sleep 1; fi
  if vivant "$pid"; then
    echo "❌ Serveur du LLM local (PID $pid, port $port) toujours en vie."
    return 1
  fi
  rm -f "$temoin"
  echo "🛑 Serveur du LLM local arrêté (port $port)."
}

# Les traitements ne vivent que dans la mémoire de l'API (`api/jobs.py`) : un
# arrêt les perd. L'API les recopie dans ce relevé tant qu'ils tournent. Un
# relevé dont le processus n'est plus l'API (mort, ou PID recyclé) est périmé :
# il ne bloque jamais l'arrêt.
# Sortie : 0 rien en cours, 3 traitement en cours.
verifier_traitements () {
  local releve=".memoglyph-traitements.json" api_pid=""
  [ -f "$releve" ] || return 0
  [ ! -f .memoglyph-api.pid ] || api_pid="$(cat .memoglyph-api.pid 2>/dev/null || true)"
  python3 - "$releve" "$api_pid" <<'PY'
import json, os, subprocess, sys
releve, api_pid = sys.argv[1], sys.argv[2]
NATURES = {"transcription": "transcription", "faits": "extraction des faits",
           "voix": "rattachement des voix", "archives": "archives fidèles"}
def perime(raison):
    print(f"ℹ️  Relevé des traitements périmé ({raison}) : retiré.")
    try:
        os.remove(releve)
    except OSError:
        pass
    sys.exit(0)
try:
    with open(releve, encoding="utf-8") as f:
        contenu = json.load(f)
    pid = int(contenu["pid"])
    traitements = list(contenu["traitements"])
except (OSError, ValueError, KeyError, TypeError):
    perime("illisible")
try:
    os.kill(pid, 0)
except ProcessLookupError:
    perime(f"l'API {pid} ne tourne plus")
except PermissionError:
    pass
commande = subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                          capture_output=True, text=True).stdout
if str(pid) != api_pid and "uvicorn api.main" not in commande:
    perime(f"le PID {pid} n'est plus l'API")
if not traitements:
    sys.exit(0)
n = len(traitements)
print(f"⚠️  {n} traitement{'s' if n > 1 else ''} en cours dans Memoglyph :")
for t in traitements:
    nature = NATURES.get(t.get("nature"), t.get("nature") or "traitement")
    avance = f"{round(100 * float(t.get('progression') or 0))} %"
    print(f"   • {nature} : {avance}, depuis {t.get('depuis') or '?'}")
print("   L'arrêter maintenant le perdrait : il faudrait le relancer.")
print("   Attendez sa fin, ou arrêtez quand même :  ./stop.sh --forcer")
sys.exit(3)
PY
}

# Un orphelin est un serveur lancé depuis CETTE racine : son dossier courant est
# la racine ou l'un de ses sous-dossiers, comme `ui/` pour Vite. La commande
# seule ne le prouve pas : `uvicorn api.main` est aussi celle de l'API d'une
# autre racine (démo, kit de prise en main), qui tournait et était tuée.
dossier_de () {
  lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1
}

sweep_orphans () {
  local base="${MEMOGLYPH_PORT_BASE:-8770}" here found=0 failed=0
  here="$(pwd -P)"
  for off in 0 10 20 30 40 50 60 70 80 90; do
    for p in $((base + off)) $((base + off + 1)); do
      for pid in $(lsof -ti:"$p" -sTCP:LISTEN 2>/dev/null || true); do
        local cmd dossier; cmd="$(ps -o command= -p "$pid" 2>/dev/null || true)"
        dossier="$(dossier_de "$pid")"
        case "$dossier" in
          "$here"|"$here"/*)
            kill "$pid" 2>/dev/null || true
            sleep 1
            ! vivant "$pid" || kill -9 "$pid" 2>/dev/null || true
            sleep 1
            if vivant "$pid"; then
              echo "❌ Impossible d'arrêter l'orpheline sur le port $p (PID $pid)"
              failed=$((failed + 1))
            else
              echo "🧹 orpheline nettoyée sur le port $p (PID $pid)"
              found=$((found + 1))
            fi
            ;;
          *)
            case "$cmd" in
              *"uvicorn api.main"*|*vite*)
                echo "ℹ️  Port $p : serveur Memoglyph d'une autre racine (${dossier:-dossier illisible}), laissé en place."
                ;;
            esac
            ;;
        esac
      done
    done
  done
  [ "$found" -eq 0 ] || echo "🧹 $found processus orphelin(s) retiré(s)."
  [ "$failed" -eq 0 ]
}

if [ "$FORCER" = 0 ]; then
  code=0
  verifier_traitements || code=$?
  if [ "$code" -ne 0 ]; then
    echo "❌ Arrêt refusé : rien n'a été arrêté."
    exit "$code"
  fi
elif [ -f .memoglyph-traitements.json ]; then
  echo "⚠️  --forcer : les traitements en cours sont perdus."
fi

echo "🛑 [React] Arrêt de la stack Memoglyph — frontend React (Vite, port ${UI_PORT}) + backend FastAPI (port ${API_PORT})…"
failed=0
stop_one "API FastAPI (backend)" ".memoglyph-api.pid" "${API_PORT:-}" || failed=1
stop_one "Front Vite (React)"     ".memoglyph-ui.pid"  "${UI_PORT:-}" || failed=1
stop_mlx || failed=1
sweep_orphans || failed=1
if [ "$failed" -ne 0 ]; then
  echo "❌ Arrêt incomplet — ports et fichiers PID conservés pour permettre un nouvel essai."
  exit 1
fi
rm -f .memoglyph-ports .memoglyph-traitements.json
echo "✅ Memoglyph arrêté — stack React + FastAPI."
