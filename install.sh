#!/usr/bin/env bash
# install.sh — instalador de un comando para CTR Rosales QC (v1.0.0).
#
# Verifica prerequisitos, construye las imágenes FRESCAS (evita el problema de
# imagen desactualizada) y levanta backend + frontend con Docker Compose en
# modo determinista (vision-off + SUNAT). Pensado para un usuario que solo
# necesita "que funcione".
#
# Uso:
#   ./install.sh           # instala y arranca
#   ./install.sh --stop    # detiene la app
#   ./install.sh --logs    # muestra los logs en vivo

set -euo pipefail

COMPOSE_FILE="docker-compose.app.yml"
# Host-published backend port (default 8010, OFF :8000 to coexist with sibling
# host-port services). Must match CTR_BACKEND_PORT in docker-compose.app.yml.
CTR_BACKEND_PORT="${CTR_BACKEND_PORT:-8010}"
FRONTEND_URL="http://localhost:5173"
BACKEND_URL="http://localhost:${CTR_BACKEND_PORT}"
SUNAT_HOST="https://e-factura.sunat.gob.pe"

cd "$(dirname "$0")"

c_green() { printf '\033[0;32m%s\033[0m\n' "$1"; }
c_yellow() { printf '\033[1;33m%s\033[0m\n' "$1"; }
c_red() { printf '\033[0;31m%s\033[0m\n' "$1"; }
c_bold() { printf '\033[1m%s\033[0m\n' "$1"; }

compose() { docker compose -f "$COMPOSE_FILE" "$@"; }

# ── Subcomandos ──────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--stop" ]]; then
  c_bold "Deteniendo CTR Rosales QC…"
  compose down
  c_green "Detenido."
  exit 0
fi
if [[ "${1:-}" == "--logs" ]]; then
  compose logs -f
  exit 0
fi

c_bold "═══════════════════════════════════════════════════"
c_bold "  CTR Rosales QC — Instalador v1.0.0"
c_bold "═══════════════════════════════════════════════════"
echo

# ── 1. Prerequisitos ─────────────────────────────────────────────────────────
c_bold "1) Verificando prerequisitos…"
if ! command -v docker >/dev/null 2>&1; then
  c_red "  ✗ Docker no está instalado."
  echo "    Instalá Docker Desktop (Windows/Mac) o el paquete docker (Linux) y volvé a ejecutar."
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  c_red "  ✗ Docker Compose v2 no está disponible (se necesita 'docker compose')."
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  c_red "  ✗ El demonio de Docker no está corriendo. Iniciá Docker y reintentá."
  exit 1
fi
c_green "  ✓ Docker y Docker Compose disponibles."

# ── 2. Chequeos no bloqueantes ───────────────────────────────────────────────
c_bold "2) Chequeos del entorno…"
for port in 5173 "$CTR_BACKEND_PORT"; do
  if (command -v ss >/dev/null 2>&1 && ss -tln 2>/dev/null | grep -q ":$port ") ||
     (command -v lsof >/dev/null 2>&1 && lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1); then
    c_yellow "  ⚠ El puerto $port ya está en uso. Si la app no abre, liberalo o detené el proceso que lo ocupa."
  fi
done
if command -v curl >/dev/null 2>&1; then
  if curl -sS --max-time 8 -o /dev/null "$SUNAT_HOST" 2>/dev/null; then
    c_green "  ✓ SUNAT alcanzable (modo determinista usa SUNAT para material y fechas)."
  else
    c_yellow "  ⚠ No se pudo contactar a SUNAT ahora. La app igual arranca; las consultas se"
    c_yellow "    resolverán cuando haya conexión (los resultados quedan cacheados)."
  fi
fi

# ── 3. Build fresco ──────────────────────────────────────────────────────────
echo
c_bold "3) Construyendo imágenes (fresco)…"
c_yellow "    La primera vez puede tardar varios minutos."
compose build

# ── 4. Arranque ──────────────────────────────────────────────────────────────
echo
c_bold "4) Iniciando la aplicación…"
compose up -d

# ── 5. Espera de salud ───────────────────────────────────────────────────────
echo
c_bold "5) Esperando que el backend esté listo…"
for i in $(seq 1 30); do
  if curl -sS --max-time 4 -o /dev/null "$BACKEND_URL/api/v1/runs/" 2>/dev/null; then
    c_green "  ✓ Backend listo."
    break
  fi
  sleep 2
  if [[ $i -eq 30 ]]; then
    c_yellow "  ⚠ El backend tardó más de lo esperado. Revisá los logs con: ./install.sh --logs"
  fi
done

echo
c_green "═══════════════════════════════════════════════════"
c_green "  ✓ CTR Rosales QC está corriendo."
c_green "═══════════════════════════════════════════════════"
echo
c_bold  "  Abrí la aplicación en:  $FRONTEND_URL"
echo    "  API (backend):          $BACKEND_URL"
echo
echo    "  Detener:   ./install.sh --stop   (o: make app-down)"
echo    "  Logs:      ./install.sh --logs"
echo

# Abrir el navegador automáticamente si hay sesión gráfica.
if [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]] && command -v xdg-open >/dev/null 2>&1; then
  (xdg-open "$FRONTEND_URL" >/dev/null 2>&1 &) || true
fi
