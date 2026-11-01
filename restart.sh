#!/usr/bin/env bash
#
# Redémarre la stack de dev Memoglyph (stop + start).
#   ./restart.sh [--lan] [--forcer]
#
# `--forcer` va à `stop.sh` (arrêter malgré un traitement en cours) ; le reste
# va à `start.sh`.
#
set -euo pipefail
cd "$(dirname "$0")"

pour_stop=()
pour_start=()
for option in "$@"; do
  case "$option" in
    --forcer) pour_stop+=("$option") ;;
    *) pour_start+=("$option") ;;
  esac
done

# `${tableau[@]+…}` : sous `set -u`, le bash de macOS (3.2) refuse un tableau vide.
./stop.sh ${pour_stop[@]+"${pour_stop[@]}"}
exec ./start.sh ${pour_start[@]+"${pour_start[@]}"}
