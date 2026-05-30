Request

{
  "content": "import os
import re
import json
import asyncio
import logging
import httpx
from datetime import datetime

TELEGRAM_TOKEN = os.environ[\"TELEGRAM_TOKEN\"]
OBSIDIAN_API_KEY = os.environ[\"OBSIDIAN_API_KEY\"]
OBSIDIAN_URL = os.environ.get(\"OBSIDIAN_URL\", \"http://127.0.0.1:27123\")
TELEGRAM_API = f\"https://api.telegram.org/bot{TELEGRAM_TOKEN}\"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def parse_report(text):
    data = {}
    patterns = {
        \"план\":                r\"план[^\\d]*(\\d[\\d\\s]*)\",
        \"выручка\":             r\"общая\\s*выручка[^\\d]*(\\d[\\d\\s]*)\",
        \"наличные\":            r\"наличные[^\\d]*(\\d[\\d\\s]*)\",
        \"переводы\":            r\"переводы[^\\d]*(\\d[\\d\\s]*)\",
        \"qr\":                  r\"qr\\s*code[^\\d]*(\\d[\\d\\s]*)\",
        \"рассрочка\":           r\"рассрочка[^\\d]*(\\d[\\d\\s]*)\",
        \"счет\":                r\"оплата\\s*по\\s*счет[уу][^\\d]*(\\d[\\d\\s]*)\",
        \"терминал\":            r\"терминал[^\\d]*(\\d[\\d\\s]*)\",
        \"сдача\":               r\"сдача[^\\d]*(\\d[\\d\\s]*)\",
        \"наличных_в_магазине\": r\"наличных\\s*в\\s*магазине[^\\d]*(\\d[\\d\\s]*)\",
    }
    text_lower = text.lower()
    for key, pattern in patterns.items():
        match = re.search(pattern, text_lower)
        if match:
            value = match.group(1).replace(\" \", \"\")
            try:
                data[key] = int(value)
            except ValueError:
                pass
    if \"выручка\" not in data:
        return None
    return data


def fmt(val):
    if isinstance(val, int):
        return f\"{val:,}\".replace(\",\", \" \") + \" ₽\"
    return \"—\"


def format_note(data, author, raw_text):
    today = datetime.now().strftime(\"%d.%m.%Y\")
    now = datetime.now().strftime(\"%H:%M\")
    выручка = data.get(\"выручка\", 0)
    план = data.get(\"план\", 0)
    процент = round(выручка / план * 100) if план > 0 else 0
    статус = \"✅\" if процент >= 100 else \"⚠️\" if процент >= 80 else \"🔴\"

    lines = [
        f\"# 📊 Отчёт за {today}\",
        f\"**Менеджер:** {author}\",
        f\"**Время:** {now}\",
        \"\",
        \"---\",
        \"\",
        \"## 💰 Финансы\",
        \"\",
        \"| Метрика | Сумма |\",
        \"|---------|-------|\",
        f\"| Общая выручка | {fmt(выручка)} |\",
        f\"| План | {fmt(план)} |\",
        f\"| Выполнение | {процент}% {статус} |\",
        f\"| Наличные | {fmt(data.get('наличные'))} |\",
        f\"| Переводы | {fmt(data.get('переводы'))} |\",
        f\"| QR code | {fmt(data.get('qr'))} |\",
        f\"| Рассрочка | {fmt(data.get('рассрочка'))} |\",
        f\"| Терминал | {fmt(data.get('терминал'))} |\",
        f\"| Оплата по счёту | {fmt(data.get('счет'))} |\",
        f\"| Сдача | {fmt(data.get('сдача'))} |\",
        f\"| Наличных в магазине | {fmt(data.get('наличных_в_магазине'))} |\",
        \"\",
        \"---\",
        \"\",
        \"## 📝 Полный отчёт\",
        \"\",
        raw_text,
    ]
    return \"\
\".join(lines)


async def save_to_obsidian(content, filepath):
    url = f\"{OBSIDIAN_URL}/vault/{filepath}\"
    headers = {
        \"Authorization\": f\"Bearer {OBSIDIAN_API_KEY}\",
        \"Content-Type\": \"text/markdown\",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.put(url, content=content.encode(\"utf-8\"), headers=headers)
            return r.status_code in (200, 201, 204)
    except Exception as e:
        logger.error(f\"Obsidian error: {e}\")
        return False


async def send_message(chat_id, text):
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(f\"{TELEGRAM_API}/sendMessage\", json={
            \"chat_id\": chat_id,
            \"text\": text,
        })


async def get_updates(offset=None):
    params = {\"timeout\": 30, \"allowed_updates\": [\"message\"]}
    if offset:
        params[\"offset\"] = offset
    async with httpx.AsyncClient(timeout=40) as client:
        r = await client.get(f\"{TELEGRAM_API}/getUpdates\", params=params)
        return r.json()


async def process_update(update):
    message = update.get(\"message\", {})
    text = message.get(\"text\", \"\")
    chat_id = message.get(\"chat\", {}).get(\"id\")
    user = message.get(\"from\", {})
    author = f\"{user.get('first_name', '')} {user.get('last_name', '')}\".strip() or user.get(\"username\", \"Неизвестно\")

    if not text or not chat_id:
        return

    data = parse_report(text)
    if data is None:
        return

    note = format_note(data, author, text)
    today = datetime.now().strftime(\"%Y-%m-%d\")
    safe_author = re.sub(r\"[^\\w\\-]\", \"_\", author)
    filepath = f\"Отчёты/{today}_{safe_author}.md\"

    success = await save_to_obsidian(note, filepath)

    выручка = data.get(\"выручка\", 0)
    план = data.get(\"план\", 0)
    процент = round(выручка / план * 100) if план > 0 else 0
    статус = \"✅\" if процент >= 100 else \"⚠️\" if процент >= 80 else \"🔴\"
    выручка_fmt = f\"{выручка:,}\".replace(\",\", \" \")

    if success:
        reply = f\"📥 Отчёт сохранён в Obsidian\
👤 {author}\
💰 Выручка: {выручка_fmt} ₽\
📊 План: {процент}% {статус}\"
    else:
        reply = f\"📋 Отчёт получен\
👤 {author}\
💰 Выручка: {выручка_fmt} ₽\
📊 План: {процент}% {статус}\
⚠️ Obsidian недоступен\"

    await send_message(chat_id, reply)


async def main():
    logger.info(\"✅ NextApple бот запущен (long polling)...\")
    offset = None
    while True:
        try:
            result = await get_updates(offset)
            updates = result.get(\"result\", [])
            for update in updates:
                await process_update(update)
                offset = update[\"update_id\"] + 1
        except Exception as e:
            logger.error(f\"Polling error: {e}\")
            await asyncio.sleep(5)


if __name__ == \"__main__\":
    asyncio.run(main())
",
  "path": "/Users/vladrogov/Documents/Obsidian Vault/ИИ агенты/nextapple-bot/bot.py"
}
Response

Successfully wrote to /Users/vladrogov/Documents/Obsidian Vault/ИИ агенты/nextapple-bot/bot.py
