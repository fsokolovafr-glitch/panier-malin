#!/bin/zsh
set -e

cd "${0:A:h}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Не найден Python 3. Установите его с https://www.python.org/downloads/"
  read "?Нажмите Enter, чтобы закрыть окно."
  exit 1
fi

if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 9))'; then
  echo "Нужен Python 3.9 или новее. Установите его с https://www.python.org/downloads/"
  read "?Нажмите Enter, чтобы закрыть окно."
  exit 1
fi

if [ ! -d .venv ]; then
  echo "Первый запуск: подготавливаю приложение..."
  python3 -m venv .venv
fi

.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
fi

echo "Panier Malin открывается по адресу http://127.0.0.1:8000"
(sleep 1 && open http://127.0.0.1:8000) &
export FLASK_SKIP_DOTENV=1
.venv/bin/python -m app
