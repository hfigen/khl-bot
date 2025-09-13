import os, time, difflib, logging, requests
from bs4 import BeautifulSoup
from typing import List, Dict, Tuple
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

# === настройки окружения ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
PUBLIC_URL = os.getenv("PUBLIC_URL")   # например: https://your-app.koyeb.app
SECRET = os.getenv("TELEGRAM_SECRET", "change_me")
PORT = int(os.getenv("PORT", "8080"))  # Koyeb сам подставит PORT

SOURCE_URL = "https://allhockey.ru/stat/khl/2026/312/player"  # КХЛ 2025/26 (регулярка)
HEADERS = {"User-Agent": "Mozilla/5.0"}
_CACHE = {"ts": 0.0, "rows": []}
CACHE_TTL = 60  # секунд

def _rus_pos(letter: str) -> str:
    return {"H": "Нападающий", "З": "Защитник", "В": "Вратарь"}.get((letter or "").strip(), (letter or "").strip())

def fetch_table_rows() -> List[Dict]:
    now = time.time()
    if _CACHE["rows"] and now - _CACHE["ts"] < CACHE_TTL:
        return _CACHE["rows"]

    resp = requests.get(SOURCE_URL, headers=HEADERS, timeout=20)
    resp.encoding = "utf-8"
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    target = None
    for tbl in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in tbl.find_all("th")]
        if "Игрок" in headers and "Команда" in headers:
            target = tbl
            break
    if target is None:
        tables = soup.find_all("table")
        target = tables[0] if tables else None
        if target is None:
            return []

    rows = []
    for tr in target.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 10:
            continue
        a_player = tds[1].find("a"); a_team = tds[2].find("a")
        player_name = (a_player.get_text(strip=True) if a_player else tds[1].get_text(strip=True))
        team_name = (a_team.get_text(strip=True) if a_team else tds[2].get_text(strip=True))
        team_abbr = tds[3].get_text(strip=True)
        pos = tds[4].get_text(strip=True)
        pts = tds[5].get_text(strip=True)
        goals = tds[6].get_text(strip=True)
        assists = tds[7].get_text(strip=True)
        games = tds[8].get_text(strip=True)
        plus_minus = tds[9].get_text(strip=True)
        pim = (tds[10].get_text(strip=True) if len(tds) > 10 else "")
        fo_w = (tds[11].get_text(strip=True) if len(tds) > 11 else "")
        fo_pct = (tds[12].get_text(strip=True) if len(tds) > 12 else "")
        toi = (tds[13].get_text(strip=True) if len(tds) > 13 else "")

        player_url = a_player["href"] if (a_player and a_player.has_attr("href")) else ""
        if player_url.startswith("/"):
            player_url = "https://allhockey.ru" + player_url

        rows.append({
            "name": player_name,
            "team": team_name,
            "team_abbr": team_abbr,
            "pos": pos,
            "pts": pts,
            "g": goals,
            "a": assists,
            "gp": games,
            "plus_minus": plus_minus,
            "pim": pim,
            "fo_w": fo_w,
            "fo_pct": fo_pct,
            "toi": toi,
            "url": player_url
        })
    _CACHE["rows"] = rows
    _CACHE["ts"] = now
    return rows

def find_best_matches(query: str, rows: List[Dict], limit: int = 3) -> List[Tuple[float, Dict]]:
    names = [r["name"] for r in rows]
    candidates = difflib.get_close_matches(query, names, n=limit, cutoff=0.6)
    scored = []
    for r in rows:
        if r["name"] in candidates:
            score = difflib.SequenceMatcher(None, query.lower(), r["name"].lower()).ratio()
            scored.append((score, r))
    for r in rows:
        if query.lower() in r["name"].lower() and r not in [x[1] for x in scored]:
            scored.append((0.99, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    unique, seen = [], set()
    for sc, rr in scored:
        if rr["name"] not in seen:
            seen.add(rr["name"])
            unique.append((sc, rr))
    return unique[:limit]

def format_player_card(r: Dict) -> str:
    pos = _rus_pos(r["pos"])
    parts = [
        f"<b>{r['name']}</b>",
        f"Команда: {r['team']} ({r['team_abbr']})",
        f"Апмлуа: {pos}",
        f"И: {r['gp']}  Ш: {r['g']}  А: {r['a']}  О: {r['pts']}  +/-: {r['plus_minus']}",
    ]
    extra = []
    if r["pim"]:
        extra.append(f"Штр: {r['pim']}")
    if r["fo_w"]:
        extra.append(f"БВ: {r['fo_w']}")
    if r["fo_pct"]:
        extra.append(f"%БВ: {r['fo_pct']}")
    if r["toi"]:
        extra.append(f"Ср.время: {r['toi']}")
    if extra:
        parts.append(" | ".join(extra))
    if r["url"]:
        parts.append(f'<a href="{r["url"]}">Профиль на Allhockey</a>')
    return "\n".join(parts)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Напиши ФИО игрока КХЛ — пришлю статистику сезона 2025/26 (регулярка) и команду."
    )

async def handle_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = (update.message.text or "").strip()
    if len(q) < 2:
        await update.message.reply_text("Введи ФИО, напр.: «Стефан Да Коста».")
        return
    try:
        rows = fetch_table_rows()
        if not rows:
            await update.message.reply_text("Не удалось получить таблицу. Попробуй позже.")
            return
        matches = find_best_matches(q, rows, limit=3)
        if not matches:
            await update.message.reply_text("Не нашёл игрока в текущем сезоне.")
            return
        texts = [format_player_card(r) for _, r in matches]
        await update.message.reply_text("\n\n".join(texts), parse_mode=ParseMode.HTML, disable_web_page_preview=False)
    except Exception as e:
        logging.exception("Error")
        await update.message.reply_text(f"Ошибка: {e}")

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_name))
    # webhook-режим ( для Koyeb/бесплатных хостингов)
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="tg",
        webhook_url=f"{PUBLIC_URL}/tg" if PUBLIC_URL else None,
        secret_token=SECRET
    )

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
