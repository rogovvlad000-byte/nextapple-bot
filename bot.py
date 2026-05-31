import os
import re
import asyncio
import logging
import httpx
from datetime import datetime

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
OBSIDIAN_API_KEY = os.environ["OBSIDIAN_API_KEY"]
OBSIDIAN_URL = os.environ.get("OBSIDIAN_URL", "http://127.0.0.1:27123")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def normalize(text):
    text = text.replace("\u2014", "-").replace("\u2013", "-").replace("\u2212", "-")
    text = re.sub(r'\s*-\s*', ' - ', text)
    return text


def parse_report(raw_text):
    data = {}
    text = normalize(raw_text)
    text_lower = text.lower()

    finance_patterns = {
        "план": [
            r"план - ([\d\s]+)₽",
            r"план[^\d]*([\d][\d\s]*)",
        ],
        "выручка": [
            r"общая\s*(?:сумма|выручка) - ([\d\s]+)",
            r"\d+\)\s*общая\s*(?:сумма|выручка)[^\d]*([\d\s]+)",
        ],
        "наличные": [
            r"\d+\)\s*наличные - ([\d\s]+)",
            r"наличные - ([\d\s]+)",
        ],
        "переводы": [
            r"\d+\)\s*переводы? - ([\d\s]+)",
            r"переводы? - ([\d\s]+)",
        ],
        "qr": [
            r"\d+\)\s*qr[\s-]*код - ([\d\s]+)",
            r"qr[\s-]*код - ([\d\s]+)",
        ],
        "рассрочка": [
            r"\d+\)\s*рассрочка - ([\d\s]+)",
            r"рассрочка - ([\d\s]+)",
        ],
        "счет": [
            r"\d+\)\s*оплата\s*по\s*счет[уу] - ([\d\s]+)",
            r"оплата\s*по\s*счет[уу][^\d]*([\d\s]+)",
        ],
        "терминал": [
            r"\d+\)\s*терминал - ([\d\s]+)",
            r"терминал - ([\d\s]+)",
        ],
        "сдача": [
            r"сдача - ([\d\s]+)",
        ],
        "наличных_в_магазине": [
            r"наличных\s*в\s*магазине - ([\d\s]+)",
        ],
    }

    for key, patterns in finance_patterns.items():
        for pattern in patterns:
            match = re.search(pattern, text_lower)
            if match:
                value = match.group(1).replace(" ", "").replace("₽", "").strip()
                try:
                    data[key] = int(value)
                    break
                except ValueError:
                    pass

    stat_patterns = {
        "продаж":      [r"[•\*]\s*продаж[:\s]*(\d+)", r"продаж[:\s]*(\d+)"],
        "броней":      [r"[•\*]\s*броней[:\s]*(\d+)", r"броней[:\s]*(\d+)"],
        "предзаказов": [r"[•\*]\s*предзаказов[:\s]*(\d+)", r"предзаказов[:\s]*(\d+)"],
    }
    for key, patterns in stat_patterns.items():
        for pattern in patterns:
            match = re.search(pattern, text_lower)
            if match:
                try:
                    data[key] = int(match.group(1))
                    break
                except ValueError:
                    pass

    out_match = re.search(
        r"закончил(?:ись|ся)\s*товары?[:\.\s]*(.*?)(?=\n\n|\d+[\.\)]|\Z)",
        raw_text, re.DOTALL | re.IGNORECASE
    )
    if out_match:
        data["закончились"] = out_match.group(1).strip()

    asked_match = re.search(
        r"спросили?\s*сегодня[:\s]*(.*?)(?=\n\n|\d+[\.\)]|📊|\Z)",
        raw_text, re.DOTALL | re.IGNORECASE
    )
    if asked_match:
        data["спрашивали"] = asked_match.group(1).strip()

    comment_match = re.search(
        r"(?:^|\n)2[\.\)]\s*(.*?)(?=\n\n|\n\d+[\.\)]|спросили?|📊|\Z)",
        raw_text, re.DOTALL | re.IGNORECASE
    )
    if comment_match:
        data["комментарий"] = comment_match.group(1).strip()

    if "выручка" not in data:
        return None

    return data


def fmt(val):
    if isinstance(val, int):
        return f"{val:,}".replace(",", " ") + " ₽"
    return "—"


def format_note(data, author, raw_text):
    today = datetime.now().strftime("%d.%m.%Y")
    now = datetime.now().strftime("%H:%M")
    выручка = data.get("выручка", 0)
    план = data.get("план", 0)
    процент = round(выручка / план * 100) if план > 0 else 0
    статус = "✅" if процент >= 100 else "⚠️" if процент >= 80 else "🔴"

    lines = [
        f"# 📊 Отчёт за {today}",
        f"**Менеджер:** {author}",
        f"**Время:** {now}",
        "", "---", "",
    ]

    if data.get("закончились"):
        lines += ["## 📦 Закончились товары", "", data["закончились"], "", "---", ""]

    if data.get("комментарий"):
        lines += ["## 💬 Как прошёл день", "", data["комментарий"], "", "---", ""]

    if data.get("спрашивали"):
        lines += ["## ❓ Спрашивали, но не было", "", data["спрашивали"], "", "---", ""]

    if any(k in data for k in ["продаж", "броней", "предзаказов"]):
        lines += ["## 📈 Статистика", "", "| Показатель | Значение |", "|------------|----------|"]
        if "предзаказов" in data:
            lines.append(f"| Предзаказов | {data['предзаказов']} |")
        if "броней" in data:
            lines.append(f"| Броней | {data['броней']} |")
        if "продаж" in data:
            lines.append(f"| Продаж | {data['продаж']} |")
        lines += ["", "---", ""]

    lines += [
        "## 💰 Итоги по кассе",
        "",
        "| Метрика | Сумма |",
        "|---------|-------|",
        f"| Общая выручка | {fmt(выручка)} |",
        f"| План | {fmt(план)} |",
        f"| Выполнение | {процент}% {статус} |",
        f"| Наличные | {fmt(data.get('наличные'))} |",
        f"| Переводы | {fmt(data.get('переводы'))} |",
        f"| QR code | {fmt(data.get('qr'))} |",
        f"| Рассрочка | {fmt(data.get('рассрочка'))} |",
        f"| Терминал | {fmt(data.get('терминал'))} |",
        f"| Оплата по счёту | {fmt(data.get('счет'))} |",
        f"| Сдача | {fmt(data.get('сдача'))} |",
        f"| Наличных в магазине | {fmt(data.get('наличных_в_магазине'))} |",
        "", "---", "",
        "## 📝 Исходный отчёт",
        "",
        raw_text,
    ]

    return "\n".join(lines)


async def save_to_obsidian(content, filepath):
    url = f"{OBSIDIAN_URL}/vault/{filepath}"
   headers = {
    "Authorization": f"Bearer {OBSIDIAN_API_KEY}",
    "Content-Type": "text/markdown",
    "ngrok-skip-browser-warning": "true",
}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.put(url, content=content.encode("utf-8"), headers=headers)
            return r.status_code in (200, 201, 204)
    except Exception as e:
        logger.error(f"Obsidian error: {e}")
        return False


async def send_message(chat_id, text):
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(f"{TELEGRAM_API}/sendMessage", json={
            "chat_id": chat_id,
            "text": text,
        })


async def get_updates(offset=None):
    params = {"timeout": 30, "allowed_updates": ["message"]}
    if offset:
        params["offset"] = offset
    async with httpx.AsyncClient(timeout=40) as client:
        r = await client.get(f"{TELEGRAM_API}/getUpdates", params=params)
        return r.json()


async def process_update(update):
    message = update.get("message", {})
    text = message.get("text", "")
    chat_id = message.get("chat", {}).get("id")
    user = message.get("from", {})
    author = (
        f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
        or user.get("username", "Неизвестно")
    )

    if not text or not chat_id:
        return

    data = parse_report(text)
    if data is None:
        return

    note = format_note(data, author, text)
    today = datetime.now().strftime("%Y-%m-%d")
    safe_author = re.sub(r"[^\w\-]", "_", author)
    filepath = f"Отчёты/{today}_{safe_author}.md"

    success = await save_to_obsidian(note, filepath)

    выручка = data.get("выручка", 0)
    план = data.get("план", 0)
    процент = round(выручка / план * 100) if план > 0 else 0
    статус = "✅" if процент >= 100 else "⚠️" if процент >= 80 else "🔴"
    выручка_fmt = f"{выручка:,}".replace(",", " ")
    продаж = data.get("продаж", "—")

    obsidian_status = "📥 Сохранён в Obsidian" if success else "⚠️ Obsidian недоступен"

    reply = (
        f"📋 Отчёт принят | {obsidian_status}\n"
        f"👤 {author}\n"
        f"💰 Выручка: {выручка_fmt} ₽\n"
        f"📊 План: {процент}% {статус}\n"
        f"🛒 Продаж: {продаж}"
    )

    await send_message(chat_id, reply)


async def main():
    logger.info("✅ NextApple бот запущен...")
    offset = None
    while True:
        try:
            result = await get_updates(offset)
            updates = result.get("result", [])
            for update in updates:
                await process_update(update)
                offset = update["update_id"] + 1
        except Exception as e:
            logger.error(f"Polling error: {e}")
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
