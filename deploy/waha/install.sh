#!/usr/bin/env bash
# Установка WAHA — бесплатной рассылки в WhatsApp — одной командой.
#
#   cd /путь/к/english-center-crm
#   sudo bash deploy/waha/install.sh
#
# Что делает: ставит Docker (если его нет), кладёт WAHA в /opt/waha, сам придумывает ключи,
# запускает WAHA и сам вписывает адрес и ключ в CRM. Вам останется отсканировать QR-код
# на странице «Рассылки». Повторный запуск ничего не ломает: ключи остаются те же,
# а WAHA обновляется до свежей версии.
set -Eeuo pipefail

CRM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WAHA_DIR=/opt/waha

step() { printf '\n\033[1;34m▶ %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✔ %s\033[0m\n' "$*"; }
warn() { printf '  \033[33m! %s\033[0m\n' "$*"; }
fail() { printf '\n\033[1;31m✖ %s\033[0m\n\n' "$*" >&2; exit 1; }
trap 'fail "Что-то пошло не так (строка $LINENO). Пришлите этот вывод целиком — разберёмся."' ERR

random_key() {
    if command -v openssl >/dev/null 2>&1; then openssl rand -hex 32
    else head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; fi
}

[ "$(id -u)" -eq 0 ] || fail "Нужны права администратора. Запустите так: sudo bash deploy/waha/install.sh"
[ -f "$CRM_DIR/manage.py" ] || fail "Не нашёл CRM рядом со скриптом ($CRM_DIR). Запускайте из папки CRM."
command -v curl >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y -qq curl; }

# ---------- 1. Docker ----------
step "1/4 Docker"
if ! command -v docker >/dev/null 2>&1; then
    echo "  Docker не найден — ставлю (пара минут)…"
    curl -fsSL https://get.docker.com | sh >/dev/null
fi
systemctl enable --now docker >/dev/null 2>&1 || true
if docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE=(docker-compose)
else
    apt-get update -qq && apt-get install -y -qq docker-compose-plugin >/dev/null
    COMPOSE=(docker compose)
fi
ok "$(docker --version)"

# ---------- 2. Папка и ключи ----------
step "2/4 Настройки WAHA в $WAHA_DIR"
mkdir -p "$WAHA_DIR/sessions" "$WAHA_DIR/media"
cp "$CRM_DIR/deploy/waha/docker-compose.yml" "$WAHA_DIR/docker-compose.yml"

if [ -f "$WAHA_DIR/.env" ]; then
    ok "Ключи уже есть — оставляю прежние"
else
    # Порт: 3000, а если он занят чем-то другим — первый свободный до 3010
    PORT=3000
    while timeout 1 bash -c "exec 3<>/dev/tcp/127.0.0.1/$PORT" 2>/dev/null; do
        PORT=$((PORT + 1))
        [ "$PORT" -le 3010 ] || fail "Порты 3000–3010 заняты. Освободите один из них и запустите скрипт снова."
    done
    # Сервер на ARM: если обычный образ не под ARM — берём сборку :arm
    IMAGE=devlikeapro/waha:latest
    case "$(uname -m)" in
        aarch64|arm64)
            docker pull -q "$IMAGE" >/dev/null 2>&1 || true
            [ "$(docker image inspect -f '{{.Architecture}}' "$IMAGE" 2>/dev/null)" = "arm64" ] || IMAGE=devlikeapro/waha:arm
            ;;
    esac
    PANEL_PASSWORD="$(random_key | cut -c1-24)"
    umask 077
    cat > "$WAHA_DIR/.env" <<ENV
# Создано deploy/waha/install.sh. Тут ключи — никому не пересылайте этот файл.
WAHA_IMAGE=$IMAGE
WAHA_PORT=$PORT

# Ключ, по которому CRM отправляет сообщения (скрипт сам вписал его в CRM)
WAHA_API_KEY=$(random_key)

# Вход во встроенную панель WAHA (обычно не нужна — всё делается в CRM)
WAHA_DASHBOARD_USERNAME=admin
WAHA_DASHBOARD_PASSWORD=$PANEL_PASSWORD
WHATSAPP_SWAGGER_USERNAME=admin
WHATSAPP_SWAGGER_PASSWORD=$PANEL_PASSWORD

# GOWS работает без браузера и почти не ест память
WHATSAPP_DEFAULT_ENGINE=GOWS
# Как CRM будет называться в «Связанных устройствах» на телефоне
WAHA_CLIENT_DEVICE_NAME=Enjoy-English-CRM
WAHA_PRINT_QR=False
WAHA_LOG_FORMAT=JSON
WAHA_LOG_LEVEL=info
ENV
    umask 022
    ok "Ключи созданы: $WAHA_DIR/.env"
fi

env_value() { { grep -E "^$1=" "$WAHA_DIR/.env" || true; } | tail -1 | cut -d= -f2-; }
KEY="$(env_value WAHA_API_KEY)"
PORT="$(env_value WAHA_PORT)"; PORT="${PORT:-3000}"
[ -n "$KEY" ] || fail "В $WAHA_DIR/.env нет WAHA_API_KEY. Удалите этот файл и запустите скрипт снова."
URL="http://127.0.0.1:$PORT"

# ---------- 3. Запуск ----------
step "3/4 Запускаю WAHA (в первый раз скачивается несколько сотен МБ — подождите)"
cd "$WAHA_DIR"
"${COMPOSE[@]}" pull -q
"${COMPOSE[@]}" up -d --remove-orphans >/dev/null
printf '  Жду, пока WAHA проснётся'
for _ in $(seq 1 90); do
    if curl -fsS -o /dev/null -H "X-Api-Key: $KEY" "$URL/api/sessions" 2>/dev/null; then READY=1; break; fi
    printf '.'; sleep 2
done
echo
[ "${READY:-}" = 1 ] || fail "WAHA не отвечает на $URL. Посмотрите, что пишет WAHA: sudo docker logs waha --tail 50"
ok "WAHA работает на $URL (снаружи сервера не видна)"

# ---------- 4. Подключение к CRM ----------
step "4/4 Подключаю CRM к WAHA"
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
    for candidate in "$CRM_DIR/venv/bin/python" "$CRM_DIR/.venv/bin/python" "$CRM_DIR/env/bin/python"; do
        if [ -x "$candidate" ]; then PY="$candidate"; break; fi
    done
fi
PY="${PY:-$(command -v python3)}"
# От имени владельца папки CRM — чтобы база не стала принадлежать root
OWNER="$(stat -c %U "$CRM_DIR/manage.py")"
if [ "$OWNER" = root ]; then AS=()
elif command -v sudo >/dev/null 2>&1; then AS=(sudo -u "$OWNER")
else AS=(runuser -u "$OWNER" --); fi
trap - ERR
if (cd "$CRM_DIR" && ${AS[@]+"${AS[@]}"} env WAHA_API_KEY="$KEY" "$PY" manage.py setup_whatsapp --url "$URL"); then
    CONNECTED=1
fi

echo
if [ "${CONNECTED:-}" = 1 ]; then
    printf '\033[1;32m══ Готово! Осталось отсканировать QR-код ══\033[0m\n'
    echo "  1. Откройте CRM → «Рассылки» → кнопка вверху справа (или сразу адрес /broadcast/?setup=1)."
    echo "  2. На телефоне с WhatsApp центра: Настройки → Связанные устройства → Привязка устройства."
    echo "  3. Наведите камеру на QR-код — через пару секунд CRM напишет «WhatsApp подключён»."
else
    warn "WAHA работает, но CRM не удалось настроить автоматически (не нашёл Python от CRM)."
    echo "  Впишите вручную: CRM → «Рассылки» → кнопка вверху справа → «Свой сервер»:"
    echo "    Адрес:    $URL"
    echo "    API-ключ: показать командой  sudo grep WAHA_API_KEY $WAHA_DIR/.env"
    echo "  Или подскажите скрипту Python от CRM:  sudo PYTHON=/путь/к/python bash deploy/waha/install.sh"
fi
echo
echo "  Полезное:"
echo "    проверить WhatsApp:   cd $CRM_DIR && $PY manage.py setup_whatsapp --status"
echo "    что пишет WAHA:       sudo docker logs waha --tail 50"
echo "    обновить WAHA:        запустите этот скрипт ещё раз"
