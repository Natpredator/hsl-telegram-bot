# HSL Telegram Bot

A Telegram bot for Helsinki public transport (HSL), built on the Digitransit API.

## Features

- **Route number + location** (e.g. `550`, `9`, `M1`) — finds the nearest stop in each direction, with walking distance and upcoming departures.
- **Place name or address** — builds up to 3 route options, selectable via inline buttons.
- **"Хочу поесть" / "где магазин одежды"** (category search) — up to 5 nearby venues by category (food, clothing stores), each with a route.
- **"Где ближайший Мак" / "хочу в New Yorker"** (brand search) — up to 5 nearest locations of a given chain, each with a route.
- **"Сколько осталось?"** — time to arrival and current leg of the active route.
- `/history` — last 10 destinations, reusable with one tap.
- **Favorites** — add a place via the "⭐ Add to favorites" button under any route, or just search the same place more than 5 times and it's added automatically. `/favorites` lists them for one-tap routing.
- **GPS-aware "time to leave" alerts** — if you share a Telegram *live location* (clip icon → Location → Share Live Location) while a route is active, the bot re-checks the real-time departure of your boarding vehicle and your actual walking distance every ~25s, and proactively messages you if it's time to leave, or if the vehicle is running late/early enough to change that. Without live location, it only answers "how much time is left" by schedule/realtime on request, same as before — bots can't request live location via a button, only a one-off point, so this has to be started manually by the user.
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
