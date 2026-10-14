# Бесплатная рассылка в WhatsApp через WAHA

[WAHA](https://github.com/devlikeapro/waha) — бесплатная программа с открытым кодом. Она работает
на сервере CRM, подключается к WhatsApp центра по QR-коду (как WhatsApp Web) и отправляет
сообщения от его имени. CRM шлёт ей запросы, и рассылка уходит сама — в личку и в групповые чаты.

Ставится один раз, минут за 10. Нужен доступ к серверу по SSH.

## 1. Docker

Если Docker на сервере ещё нет:

```bash
curl -fsSL https://get.docker.com | sh
```

## 2. Папка и настройки

```bash
sudo mkdir -p /opt/waha && cd /opt/waha
```

Создайте файл `/opt/waha/.env`. Ключи придумайте сами, например `openssl rand -hex 32`:

```env
# API-ключ: его же вписать в CRM («Рассылки» → подключение → «Свой сервер»)
WAHA_API_KEY=впишите-сюда-длинный-случайный-ключ

# Пароли от встроенной панели WAHA и документации API
WAHA_DASHBOARD_USERNAME=admin
WAHA_DASHBOARD_PASSWORD=впишите-пароль
WHATSAPP_SWAGGER_USERNAME=admin
WHATSAPP_SWAGGER_PASSWORD=впишите-пароль

# GOWS работает без браузера — экономит память сервера
WHATSAPP_DEFAULT_ENGINE=GOWS
WAHA_PRINT_QR=False
WAHA_LOG_FORMAT=JSON
```

Создайте файл `/opt/waha/docker-compose.yml`:

```yaml
services:
  waha:
    image: devlikeapro/waha:latest
    restart: always
    ports:
      # Только для самого сервера — снаружи WAHA не видна
      - '127.0.0.1:3000:3000'
    volumes:
      # Здесь хранится вход в WhatsApp — чтобы не сканировать QR после каждого перезапуска
      - ./sessions:/app/.sessions
      - ./media:/app/.media
    env_file: .env
```

## 3. Запуск

```bash
cd /opt/waha
docker compose up -d
```

Проверка (вместо `КЛЮЧ` — ваш `WAHA_API_KEY`):

```bash
curl -H "X-Api-Key: КЛЮЧ" http://localhost:3000/api/sessions
```

Ответ `[]` — всё работает.

## 4. Подключение в CRM

1. «Рассылки» → кнопка «Ручной режим» вверху справа → «Свой сервер».
2. Адрес: `http://localhost:3000`, API-ключ: `WAHA_API_KEY` из `.env`. «Подключить и проверить».
3. Появится QR-код. На телефоне с WhatsApp центра: **Настройки → Связанные устройства →
   Привязка устройства** — и отсканируйте его.
4. Кнопка вверху сменится на «Отправка автоматически». Ниже можно привязать группы CRM
   к групповым чатам WhatsApp.

Если CRM сама работает в Docker, `localhost` в адресе не подойдёт — укажите адрес хоста,
например `http://172.17.0.1:3000`, и поменяйте `127.0.0.1:3000:3000` на `172.17.0.1:3000:3000`.

## Обновление

```bash
cd /opt/waha
docker compose pull && docker compose up -d
```

## Чтобы WhatsApp не заблокировал номер

WAHA, как и Green API, — неофициальный способ: WhatsApp может ограничить номер, если примет
рассылку за спам. Поэтому:

- лучше завести для рассылок отдельный номер центра, а не основной;
- пусть родители сохранят этот номер в контакты, а лучше хоть раз напишут на него;
- CRM уже отправляет сообщения по одному с паузой 1,5–3 секунды — не убирайте её;
- не рассылайте одно и то же много раз подряд.
