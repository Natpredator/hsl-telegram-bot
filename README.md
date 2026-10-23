# HSL Telegram Bot

Телеграм-бот для общественного транспорта Хельсинки (HSL) на базе Digitransit API.

## Возможности

- Номер маршрута (550, 9, M1) + геопозиция → ближайшая остановка в каждом направлении, расстояние и ближайшие отправления.
- Название места или адрес → до 3 вариантов маршрута с выбором кнопкой.
- «Хочу поесть» / «где магазин одежды» → до 5 ближайших заведений по категории с маршрутом до каждого.
- «Где ближайший Мак» / «хочу в New Yorker» → до 5 ближайших точек конкретной сети с маршрутом.
- «Сколько осталось?» → время до прибытия и текущий отрезок пути по выбранному маршруту.
- `/history` — последние 10 мест, можно построить маршрут снова.
- Геопозиция и активный маршрут сохраняются в `data.json` и переживают перезапуск.

## Настройка

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env   # заполнить TG_TOKEN, ROUTING_KEY, GEOCODE_KEY
```

Переменные окружения:

- `TG_TOKEN` — токен Telegram-бота.
- `ROUTING_KEY` — ключ Digitransit Routing API.
- `GEOCODE_KEY` — ключ Digitransit Geocoding API (если не задан, используется `ROUTING_KEY`).

## Запуск

```bash
set -a && . ./.env && set +a
./venv/bin/python bot.py
```

## Автозапуск через systemd

```ini
[Unit]
Description=HSL Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
User=<user>
WorkingDirectory=/path/to/hsl-telegram-bot
EnvironmentFile=/path/to/hsl-telegram-bot/.env
ExecStart=/path/to/hsl-telegram-bot/venv/bin/python /path/to/hsl-telegram-bot/bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```
