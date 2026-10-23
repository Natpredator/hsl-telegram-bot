# HSL Telegram Bot

A Telegram bot for Helsinki public transport (HSL), built on the Digitransit API.

## Features

- **Route number + location** (e.g. `550`, `9`, `M1`) — finds the nearest stop in each direction, with walking distance and upcoming departures.
- **Place name or address** — builds up to 3 route options, selectable via inline buttons.
- **"Хочу поесть" / "где магазин одежды"** (category search) — up to 5 nearby venues by category (food, clothing stores), each with a route.
- **"Где ближайший Мак" / "хочу в New Yorker"** (brand search) — up to 5 nearest locations of a given chain, each with a route.
- **"Сколько осталось?"** — time to arrival and current leg of the active route.
- `/history` — last 10 destinations, reusable with one tap.
- Location and active route persist across restarts in `data.json`.

## Requirements

- Python 3.11+
- A Telegram bot token ([@BotFather](https://t.me/BotFather))
- A [Digitransit API](https://digitransit.fi/en/developers/apis/) subscription key

## Setup

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in TG_TOKEN, ROUTING_KEY, GEOCODE_KEY
```

### Environment variables

| Variable       | Description                                                              |
|----------------|---------------------------------------------------------------------------|
| `TG_TOKEN`     | Telegram bot token.                                                      |
| `ROUTING_KEY`  | Digitransit Routing API key.                                             |
| `GEOCODE_KEY`  | Digitransit Geocoding API key. Falls back to `ROUTING_KEY` if unset.     |

## Running

```bash
set -a && . ./.env && set +a
./venv/bin/python bot.py
```

## Deployment (systemd)

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

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now hslbot
```

## License

MIT
