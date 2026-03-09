#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="${VENV_DIR:-venv}"

if [[ ! -d "$VENV_DIR" ]]; then
  echo "[run.sh] Criando ambiente virtual em ./$VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"

if [[ ! -f ".env" ]]; then
  echo "[run.sh] Arquivo .env nao encontrado."
  echo "[run.sh] Crie com: cp .env.example .env"
  exit 1
fi

if [[ "${SKIP_INSTALL:-0}" != "1" ]]; then
  echo "[run.sh] Instalando/atualizando dependencias"
  pip install -r requirements.txt
fi

echo "[run.sh] Iniciando bot"
exec python -m src.main
