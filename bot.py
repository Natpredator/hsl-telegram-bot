import os
import re
import json
import time
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from telegram import (Update, KeyboardButton, ReplyKeyboardMarkup,
                      InlineKeyboardButton, InlineKeyboardMarkup)
from telegram.ext import (ApplicationBuilder, CommandHandler, MessageHandler,
                          CallbackQueryHandler, ContextTypes, filters)

# ---------- Настройки ----------
TG_TOKEN = os.environ["TG_TOKEN"]
ROUTING_KEY = os.environ["ROUTING_KEY"]
GEOCODE_KEY = os.environ.get("GEOCODE_KEY", ROUTING_KEY)

ROUTING_URL = "https://api.digitransit.fi/routing/v2/hsl/gtfs/v1"
GEOCODE_URL = "https://api.digitransit.fi/geocoding/v1/search"
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")
TZ = ZoneInfo("Europe/Helsinki")

LOCATION_TTL = 10 * 60   # сколько секунд геопозиция считается свежей
SEARCH_RADIUS = 1500     # радиус поиска остановок, м
HISTORY_SIZE = 10        # сколько мест помнить
WALK_SPEED = 80          # м/мин

MODE_RU = {"BUS": "Автобус", "TRAM": "Трамвай", "SUBWAY": "Метро",
           "RAIL": "Поезд", "FERRY": "Паром", "WALK": "Пешком"}
MODE_ICON = {"BUS": "🚌", "TRAM": "🚋", "SUBWAY": "🚇", "RAIL": "🚆",
             "FERRY": "⛴", "WALK": "🚶"}

BUS_RE = re.compile(r"[A-Za-z]?\d{1,4}[A-Za-z]{0,2}|[A-Za-z]")
REMAIN_RE = re.compile(r"осталось|сколько ехать|когда приед", re.I)

BRAND_ALIASES = {
    "мак": "mcdonald", "макдональдс": "mcdonald", "макдоналдс": "mcdonald",
    "кфс": "kfc",
}

CATEGORIES = {
    "food": {
        "re": re.compile(r"поесть|покушать|голодн|ресторан\w*|\bкафе\b|\bеда\b", re.I),
        "label": "рестораны и кафе",
        "terms": ["ravintola", "kahvila", "pizzeria"],
    },
    "clothes": {
        "re": re.compile(r"одежд|одеться", re.I),
        "label": "магазины одежды",
        "terms": ["vaateliike", "vaatekauppa"],
    },
}

BRAND_TRIGGER_RE = re.compile(
    r"^(?:где\s+(?:есть\s+)?(?:ближайш\w*\s+)?|"
    r"хочу\s+(?:поехать\s+|попасть\s+)?(?:в|на)\s+|"
    r"как\s+(?:доехать|добраться|дойти)\s+(?:до|к)\s+)(.+)$", re.I)

POI_NAME_CLEAN_RE = re.compile(r"[^\w\s\-.'&]", re.UNICODE)

GEO_KB = ReplyKeyboardMarkup(
    [[KeyboardButton("📍 Отправить геопозицию", request_location=True)]],
    resize_keyboard=True)

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("hslbot")


# ---------- Хранение данных ----------
def load_data():
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


DATA = load_data()


def save_data():
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(DATA, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA_FILE)


def user(chat_id):
    return DATA.setdefault(str(chat_id), {"history": []})


def fresh_location(u):
    loc = u.get("location")
    if loc and time.time() - loc["ts"] < LOCATION_TTL:
        return loc
    return None


# ---------- Время ----------
def fmt(ts):
    return datetime.fromtimestamp(ts, TZ).strftime("%H:%M")


def mins(ts):
    return max(0, round((ts - time.time()) / 60))


# ---------- API ----------
def gql(query, variables=None):
    r = requests.post(ROUTING_URL, json={"query": query, "variables": variables or {}},
                      headers={"digitransit-subscription-key": ROUTING_KEY}, timeout=20)
    r.raise_for_status()
    body = r.json()
    if body.get("errors"):
        raise RuntimeError(body["errors"][0].get("message"))
    return body["data"]


def geocode(text, focus=None):
    params = {"text": text, "size": 1, "lang": "fi",
              "boundary.rect.min_lat": 59.9, "boundary.rect.max_lat": 60.6,
              "boundary.rect.min_lon": 24.2, "boundary.rect.max_lon": 25.6}
    if focus:
        params["focus.point.lat"], params["focus.point.lon"] = focus
    r = requests.get(GEOCODE_URL, params=params,
                     headers={"digitransit-subscription-key": GEOCODE_KEY}, timeout=20)
    r.raise_for_status()
    feats = r.json().get("features", [])
    if not feats:
        return None
    lon, lat = feats[0]["geometry"]["coordinates"]
    return {"name": feats[0]["properties"].get("label", text), "lat": lat, "lon": lon}


def geocode_many(text, focus, size=8, layers=None):
    params = {"text": text, "size": size, "lang": "fi",
              "boundary.rect.min_lat": 59.9, "boundary.rect.max_lat": 60.6,
              "boundary.rect.min_lon": 24.2, "boundary.rect.max_lon": 25.6,
              "focus.point.lat": focus[0], "focus.point.lon": focus[1]}
    if layers:
        params["layers"] = layers
    r = requests.get(GEOCODE_URL, params=params,
                     headers={"digitransit-subscription-key": GEOCODE_KEY}, timeout=20)
    r.raise_for_status()
    out = []
    for feat in r.json().get("features", []):
        props = feat["properties"]
        lon, lat = feat["geometry"]["coordinates"]
        out.append({"name": props.get("name") or props.get("label", text),
                    "lat": lat, "lon": lon,
                    "distance": float(props.get("distance", 0)) * 1000})
    return out


STOPS_Q = """
query($lat: Float!, $lon: Float!, $r: Int!) {
  stopsByRadius(lat: $lat, lon: $lon, radius: $r, first: 80) {
    edges { node { distance stop { gtfsId name code routes { shortName } } } }
  }
}"""

TIMES_Q = """
query($id: String!) {
  stop(id: $id) {
    stoptimesWithoutPatterns(numberOfDepartures: 40, timeRange: 10800, omitNonPickups: true) {
      serviceDay realtimeDeparture realtime headsign
      trip { routeShortName tripHeadsign }
    }
  }
}"""

PLAN_Q = """
query($from: InputCoordinates!, $to: InputCoordinates!, $n: Int!) {
  plan(from: $from, to: $to, numItineraries: $n) {
    itineraries {
      startTime endTime
      legs {
        mode startTime endTime distance
        from { name stop { code } }
        to { name }
        route { shortName }
        trip { tripHeadsign }
      }
    }
  }
}"""


def find_departures(route, lat, lon):
    """Ближайшая остановка с этим маршрутом для каждого направления + ближайшие отправления."""
    route = route.upper()
    edges = gql(STOPS_Q, {"lat": lat, "lon": lon, "r": SEARCH_RADIUS})["stopsByRadius"]["edges"]
    nodes = [e["node"] for e in edges
             if any((r["shortName"] or "").upper() == route for r in e["node"]["stop"]["routes"])]
    nodes.sort(key=lambda n: n["distance"])

    now = time.time()
    by_dir = {}
    for node in nodes[:6]:
        stop = node["stop"]
        times = gql(TIMES_Q, {"id": stop["gtfsId"]})["stop"]["stoptimesWithoutPatterns"]
        for st in times:
            if (st["trip"]["routeShortName"] or "").upper() != route:
                continue
            dep = st["serviceDay"] + st["realtimeDeparture"]
            if dep < now - 30:
                continue
            head = st["headsign"] or st["trip"]["tripHeadsign"] or "?"
            entry = by_dir.setdefault(head, {"headsign": head, "stop": stop,
                                             "distance": node["distance"], "deps": []})
            if entry["stop"]["gtfsId"] == stop["gtfsId"] and len(entry["deps"]) < 3:
                entry["deps"].append((dep, st["realtime"]))
        if len(by_dir) >= 2:
            break
    return list(by_dir.values())


def plan(origin, dest, n=3):
    data = gql(PLAN_Q, {
        "from": {"lat": origin["lat"], "lon": origin["lon"], "address": "Старт"},
        "to": {"lat": dest["lat"], "lon": dest["lon"], "address": dest["name"]},
        "n": n})
    result = []
    for it in data["plan"]["itineraries"]:
        legs = []
        for l in it["legs"]:
            legs.append({
                "mode": l["mode"],
                "start": l["startTime"] // 1000, "end": l["endTime"] // 1000,
                "from": l["from"]["name"], "to": l["to"]["name"],
                "code": (l["from"].get("stop") or {}).get("code"),
                "route": (l.get("route") or {}).get("shortName"),
                "headsign": (l.get("trip") or {}).get("tripHeadsign"),
                "distance": int(l.get("distance") or 0)})
        result.append({"start": it["startTime"] // 1000, "end": it["endTime"] // 1000, "legs": legs})
    return result


def dedupe_pois(pois, min_sep=0.05):
    kept = []
    for p in sorted(pois, key=lambda p: p["distance"]):
        key = (p["name"], round(p["lat"], 4), round(p["lon"], 4))
        if any(k["_key"] == key for k in kept):
            continue
        p["_key"] = key
        kept.append(p)
    for p in kept:
        p.pop("_key", None)
    return kept


def clean_poi_text(text):
    return POI_NAME_CLEAN_RE.sub("", text).strip()[:60]


def pois_by_name(name, lat, lon, limit=5):
    sanitized = clean_poi_text(name)
    if not sanitized:
        return []
    term = BRAND_ALIASES.get(sanitized.lower(), sanitized)
    pois = geocode_many(term, (lat, lon), size=10, layers="venue")
    return dedupe_pois(pois)[:limit]


def pois_by_category(cat, lat, lon, limit=5):
    pois = []
    for term in cat["terms"]:
        pois.extend(geocode_many(term, (lat, lon), size=6, layers="venue"))
    return dedupe_pois(pois)[:limit]


def detect_category(text):
    for key, cat in CATEGORIES.items():
        if cat["re"].search(text):
            return key
    return None


def extract_brand(text):
    m = BRAND_TRIGGER_RE.match(text)
    if not m:
        return None
    return clean_poi_text(m.group(1)) or None


# ---------- Форматирование ----------
def format_departures(route, results):
    if not results:
        return (f"В радиусе {SEARCH_RADIUS} м не нашёл остановок, где ходит {route.upper()}, "
                f"или в ближайшие 3 часа рейсов нет.")
    lines = [f"🚌 {route.upper()}"]
    for r in results:
        stop = r["stop"]
        walk = max(1, round(r["distance"] / WALK_SPEED))
        deps = []
        for dep, realtime in r["deps"]:
            m = mins(dep)
            mark = "" if realtime else "~"
            late = " ⚠️ не успеешь" if m < walk else ""
            deps.append(f"{mark}{fmt(dep)} (через {m} мин){late}")
        lines.append(f"\n→ {r['headsign']}\n"
                     f"Остановка {stop['name']} ({stop['code']}), {r['distance']} м, ~{walk} мин пешком\n"
                     f"Отправления: " + ", ".join(deps))
    lines.append("\n~ = по расписанию, без данных в реальном времени")
    return "\n".join(lines)


def format_leg(l):
    icon = MODE_ICON.get(l["mode"], "•")
    if l["mode"] == "WALK":
        return f"{icon} {fmt(l['start'])} пешком {l['distance']} м до {l['to']}"
    name = MODE_RU.get(l["mode"], l["mode"])
    code = f" ({l['code']})" if l.get("code") else ""
    head = f" → {l['headsign']}" if l.get("headsign") else ""
    return (f"{icon} {fmt(l['start'])} {name} {l['route'] or ''}{head}: "
            f"{l['from']}{code} → {l['to']}, выход в {fmt(l['end'])}")


def format_itinerary(it, n):
    total = (it["end"] - it["start"]) // 60
    lines = [f"Вариант {n}: {fmt(it['start'])} → {fmt(it['end'])} ({total} мин)"]
    lines += ["  " + format_leg(l) for l in it["legs"]]
    return "\n".join(lines)


def format_poi_options(title, options):
    lines = [f"{title} — нашёл {len(options)} вариант(ов):"]
    for i, opt in enumerate(options):
        poi, it = opt["poi"], opt["it"]
        total = (it["end"] - it["start"]) // 60
        km = poi["distance"] / 1000
        lines.append(f"\n{i + 1}. {poi['name']}\n"
                     f"{km:.1f} км по прямой, в пути ~{total} мин, прибытие {fmt(it['end'])}")
    return "\n".join(lines)


def remaining_report(u):
    trip = u.get("active_trip")
    now = time.time()
    if not trip:
        return "Сейчас нет активного маршрута. Напиши, куда ехать, и выбери вариант."
    dest = trip["dest"]["name"]
    if now > trip["end"] + 120:
        u.pop("active_trip", None)
        save_data()
        return f"По плану ты уже приехал в {dest} (в {fmt(trip['end'])}). Маршрут закрыт."

    lines = [f"До {dest}: прибытие в {fmt(trip['end'])}, осталось {mins(trip['end'])} мин."]
    if now < trip["start"]:
        lines.append(f"Маршрут ещё не начался, выходи в {fmt(trip['start'])}.")
    else:
        cur = next((l for l in trip["legs"] if l["start"] <= now < l["end"]), None)
        nxt = next((l for l in trip["legs"] if l["start"] > now), None)
        if cur:
            lines.append("Сейчас: " + format_leg(cur))
        if nxt:
            lines.append("Дальше: " + format_leg(nxt))
    return "\n".join(lines)


# ---------- Действия ----------
async def send_departures(msg, route, loc):
    results = await asyncio.to_thread(find_departures, route, loc["lat"], loc["lon"])
    await msg.reply_text(format_departures(route, results))


async def send_plan(msg, u, dest, loc):
    if isinstance(dest, str):
        dest = await asyncio.to_thread(geocode, dest, (loc["lat"], loc["lon"]))
        if not dest:
            await msg.reply_text("Не нашёл такое место. Попробуй написать точнее (адрес, станция, район).")
            return
    its = await asyncio.to_thread(plan, loc, dest, 3)
    if not its:
        await msg.reply_text("Маршрутов не нашлось.")
        return
    u["pending_plans"] = {"dest": dest, "its": its}
    save_data()
    text = f"Маршруты до {dest['name']}:\n\n" + "\n\n".join(
        format_itinerary(it, i + 1) for i, it in enumerate(its))
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"Еду №{i + 1}", callback_data=f"pick:{i}")
                                for i in range(len(its))]])
    await msg.reply_text(text, reply_markup=kb)


async def send_poi_options(msg, u, pois, loc, title):
    if not pois:
        await msg.reply_text(f"Не нашёл вариантов «{title}» рядом.")
        return
    options = []
    for poi in pois:
        its = await asyncio.to_thread(plan, loc, poi, 1)
        if its:
            options.append({"poi": poi, "it": its[0]})
    if not options:
        await msg.reply_text("Нашёл места, но не получилось построить маршрут ни до одного.")
        return
    u["pending_pois"] = options
    save_data()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"Еду №{i + 1}", callback_data=f"poi:{i}")
                                for i in range(len(options))]])
    await msg.reply_text(format_poi_options(title, options), reply_markup=kb)


async def handle_poi_request(msg, u, spec, loc):
    if "category" in spec:
        cat = CATEGORIES[spec["category"]]
        pois = await asyncio.to_thread(pois_by_category, cat, loc["lat"], loc["lon"])
        await send_poi_options(msg, u, pois, loc, cat["label"].capitalize())
    else:
        name = spec["name"]
        pois = await asyncio.to_thread(pois_by_name, name, loc["lat"], loc["lon"])
        if not pois:
            await send_plan(msg, u, name, loc)
            return
        await send_poi_options(msg, u, pois, loc, f"«{name}» рядом")


async def ask_location(msg, what):
    await msg.reply_text(f"Скинь геопозицию, и я покажу {what}.", reply_markup=GEO_KB)


# ---------- Обработчики ----------
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Что я умею:\n"
        "• Напиши номер (550, 9, M1) и скинь геопозицию: покажу ближайшую остановку и когда он поедет.\n"
        "• Напиши место (Kamppi, Otaniemi, адрес): построю маршрут, выбери вариант кнопкой.\n"
        "• «Хочу поесть» или «где магазин одежды»: покажу до 5 ближайших вариантов с маршрутом.\n"
        "• «Где ближайший Мак» или «хочу в New Yorker»: найду ближайшие точки этой сети.\n"
        "• «Сколько осталось?»: сколько ехать по выбранному маршруту.\n"
        "• /history: последние места, /stop: закончить маршрут.",
        reply_markup=GEO_KB)


async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = user(update.effective_chat.id)
    hist = u.get("history", [])
    if not hist:
        await update.effective_message.reply_text("История пустая.")
        return
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(h["name"][:60], callback_data=f"hist:{i}")]
                               for i, h in enumerate(hist)])
    await update.effective_message.reply_text("Куда поедем снова?", reply_markup=kb)


async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = user(update.effective_chat.id)
    u.pop("active_trip", None)
    save_data()
    await update.effective_message.reply_text("Маршрут закончен.")


async def on_location(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    u = user(msg.chat_id)
    loc = msg.location
    u["location"] = {"lat": loc.latitude, "lon": loc.longitude, "ts": time.time()}
    save_data()
    if update.edited_message:      # обновления трансляции геопозиции: просто запоминаем
        return
    if u.get("pending_route"):
        route = u.pop("pending_route")
        save_data()
        await send_departures(msg, route, u["location"])
    elif u.get("pending_poi"):
        spec = u.pop("pending_poi")
        save_data()
        await handle_poi_request(msg, u, spec, u["location"])
    elif u.get("pending_dest"):
        dest = u.pop("pending_dest")
        save_data()
        await send_plan(msg, u, dest, u["location"])
    else:
        await msg.reply_text("Геопозиция сохранена. Напиши номер транспорта или куда ехать.")


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = msg.text.strip()
    u = user(msg.chat_id)

    if REMAIN_RE.search(text):
        await msg.reply_text(remaining_report(u))
        return

    loc = fresh_location(u)
    if BUS_RE.fullmatch(text):
        if loc:
            await send_departures(msg, text, loc)
        else:
            u["pending_route"] = text
            u.pop("pending_dest", None)
            u.pop("pending_poi", None)
            save_data()
            await ask_location(msg, f"ближайшую остановку {text.upper()}")
        return

    category = detect_category(text)
    brand = None if category else extract_brand(text)

    if category or brand:
        spec = {"category": category} if category else {"name": brand}
        label = CATEGORIES[category]["label"] if category else brand
        if loc:
            await handle_poi_request(msg, u, spec, loc)
        else:
            u["pending_poi"] = spec
            u.pop("pending_route", None)
            u.pop("pending_dest", None)
            save_data()
            await ask_location(msg, f"варианты «{label}»")
        return

    if loc:
        await send_plan(msg, u, text, loc)
    else:
        u["pending_dest"] = text
        u.pop("pending_route", None)
        u.pop("pending_poi", None)
        save_data()
        await ask_location(msg, "маршрут")


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    u = user(q.message.chat_id)
    kind, _, arg = q.data.partition(":")

    if kind == "pick":
        pp = u.get("pending_plans")
        if not pp or int(arg) >= len(pp["its"]):
            await q.message.reply_text("Эти варианты устарели, построй маршрут заново.")
            return
        it = pp["its"][int(arg)]
        dest = pp["dest"]
        u["active_trip"] = {"dest": dest, **it}
        u["history"] = ([dest] + [h for h in u.get("history", []) if h["name"] != dest["name"]])[:HISTORY_SIZE]
        u.pop("pending_plans", None)
        save_data()
        await q.message.reply_text(
            f"Запомнил вариант {int(arg) + 1}. Прибытие в {fmt(it['end'])}.\n"
            f"Спроси «сколько осталось» в любой момент.")

    elif kind == "poi":
        options = u.get("pending_pois")
        if not options or int(arg) >= len(options):
            await q.message.reply_text("Эти варианты устарели, спроси заново.")
            return
        opt = options[int(arg)]
        poi, it = opt["poi"], opt["it"]
        dest = {"name": poi["name"], "lat": poi["lat"], "lon": poi["lon"]}
        u["active_trip"] = {"dest": dest, **it}
        u["history"] = ([dest] + [h for h in u.get("history", []) if h["name"] != dest["name"]])[:HISTORY_SIZE]
        u.pop("pending_pois", None)
        save_data()
        await q.message.reply_text(
            f"Запомнил: едешь в {dest['name']}. Прибытие в {fmt(it['end'])}.\n"
            f"Спроси «сколько осталось» в любой момент.")

    elif kind == "hist":
        hist = u.get("history", [])
        if int(arg) >= len(hist):
            return
        dest = hist[int(arg)]
        loc = fresh_location(u)
        if loc:
            await send_plan(q.message, u, dest, loc)
        else:
            u["pending_dest"] = dest
            save_data()
            await ask_location(q.message, f"маршрут до {dest['name']}")


async def on_error(update, ctx: ContextTypes.DEFAULT_TYPE):
    log.error("Ошибка: %s", ctx.error, exc_info=ctx.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("Что-то пошло не так (сеть или API). Попробуй ещё раз.")


def main():
    app = ApplicationBuilder().token(TG_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(MessageHandler(filters.LOCATION, on_location))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.UpdateType.MESSAGE, on_text))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_error_handler(on_error)
    log.info("Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
