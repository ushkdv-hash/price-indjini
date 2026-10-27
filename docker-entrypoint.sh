#!/bin/sh
# Точка входа контейнера: готовит /data, секретный ключ и запускает gunicorn.
set -e

DATA_DIR="${DATA_DIR:-/data}"
export DATA_DIR
mkdir -p "$DATA_DIR"

# SECRET_KEY не задан снаружи — генерируем один раз и храним в постоянном томе.
# Благодаря этому сессии не ломаются после пересборки и не нужен секрет в панели.
if [ -z "${SECRET_KEY:-}" ]; then
    SECRET_FILE="$DATA_DIR/.secret_key"
    if [ ! -s "$SECRET_FILE" ]; then
        umask 077
        python -c "import secrets; print(secrets.token_urlsafe(48))" > "$SECRET_FILE"
        echo "[auto-price] SECRET_KEY создан: $SECRET_FILE"
    fi
    SECRET_KEY="$(cat "$SECRET_FILE")"
    export SECRET_KEY
else
    echo "[auto-price] SECRET_KEY взят из переменной окружения"
fi

# Однократная инициализация: переносит справочники в /data и создаёт admin/admin,
# если пользователей ещё нет. Делается до gunicorn, чтобы 2 воркера не гонялись.
echo "[auto-price] Инициализация данных в $DATA_DIR..."
python -c "import wsgi"

PORT="${PORT:-80}"
WORKERS="${WEB_WORKERS:-2}"

echo "[auto-price] Запуск gunicorn: 0.0.0.0:$PORT, воркеров: $WORKERS"
exec gunicorn \
    -w "$WORKERS" \
    -b "0.0.0.0:$PORT" \
    --preload \
    --access-logfile - \
    --error-logfile - \
    --timeout 120 \
    wsgi:app
