import os
import re
import logging
import httpx
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

# ─── Настройки ────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
OBSIDIAN_API_KEY = os.environ["OBSIDIAN_API_KEY"]
# HTTP порт — включи в Obsidian: Settings → Local REST API → Enable HTTP server
OBSIDIAN_URL = os.environ.get("OBSIDIAN_URL", "http://127.0.0.1:27123")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Парсинг отчёта менеджера ──────────────────────────────────────────────────
def parse_report(text: str) -> dict | None:
    """
    Парсит ежедневный отчёт менеджера.
    Формат (из Задачи сотрудников.md):
        План - 500000
        Общая выручка - 320000
        Наличные - 150000
        Переводы - 100000
        QR code - 50000
        Рассрочка - 20000
        Оплата по счету - 0
        Терминал - 0
        Сдача - 3000
        Наличных в магазине - 147000
    """
    data = {}
    patterns = {
        "план":               r"план[^\d]*(\d[\d\s]*)",
        "выручка":            r"общая\s*выручка[^\d]*(\d[\d\s]*)",
        "наличные":           r"наличные[^\d]*(\d[\d\s]*)",
        "переводы":           r"переводы[^\d]*(\d[\d\s]*)",
        "qr":                 r"qr\s*code[^\d]*(\d[\d\s]*)",
        "рассрочка":          r"рассрочка[^\d]*(\d[\d\s]*)",
        "счет":               r"оплата\s*по\s*счет[уу][^\d]*(\d[\d\s]*)",
        "терминал":           r"терминал[^\d]*(\d[\d\s]*)",
        "сдача":              r"сдача[^\d]*(\d[\d\s]*)",
        "наличных_в_магазине":r"наличных\s*в\s*магазине[^\d]*(\d[\d\s]*)",
    }

    text_lower = text.lower()
    for key, pattern in patterns.items():
        match = re.search(pattern, text_lower)
        if match:
            value = match.group(1).replace(" ", "")
            try:
                data[key] = int(value)
            except ValueError:
                pass

    # Нужна хотя бы выручка — иначе не отчёт
    if "выручка" not in data:
        return None

    return data


# ─── Формат Markdown для Obsidian ─────────────────────────────────────────────
def format_note(data: dict, author: str, raw_text: str) -> str:
    today = datetime.now().strftime("%d.%m.%Y")
    now = datetime.now().strftime("%H:%M")

    выручка = data.get("выручка", 0)
    план = data.get("план", 0)
    процент = round(выручка / план * 100) if план > 0 else 0
    статус = "✅" if процент >= 100 else "⚠️" if процент >= 80 else "🔴"

    def fmt(val):
        return f"{val:,}".replace(",", " ") + " ₽" if isinstance(val, int) else "—"

    lines = [
        f"# 📊 Отчёт за {today}",
        f"**Менеджер:** {author}  ",
        f"**Время:** {now}",
        "",
        "---",
        "",
        "## 💰 Финансы",
        "",
        "| Метрика | Сумма |",
        "|---------|-------|",
        f"| Общая выручка | {fmt(выручка)} |",
        f"| План | {fmt(план)} |",
        f"| Выполнение | {процент}% {статус} |",
        f"| Наличные | {fmt(data.get('наличные', '—'))} |",
        f"| Переводы | {fmt(data.get('переводы', '—'))} |",
        f"| QR code | {fmt(data.get('qr', '—'))} |",
        f"| Рассрочка | {fmt(data.get('рассрочка', '—'))} |",
        f"| Терминал | {fmt(data.get('терминал', '—'))} |",
        f"| Оплата по счёту | {fmt(data.get('счет', '—'))} |",
        f"| Сдача | {fmt(data.get('сдача', '—'))} |",
        f"| Наличных в магазине | {fmt(data.get('наличных_в_магазине', '—'))} |",
        "",
        "---",
        "",
        "## 📝 Полный отчёт",
        "",
        raw_text,
    ]

    return "\n".join(lines)


# ─── Сохранение в Obsidian через Local REST API ────────────────────────────────
async def save_to_obsidian(note_content: str, filepath: str) -> bool:
    """
    PUT /vault/{path} — создаёт или перезаписывает файл.
    Документация: https://coddingtonbear.github.io/obsidian-local-rest-api/
    """
    url = f"{OBSIDIAN_URL}/vault/{filepath}"
    headers = {
        "Authorization": f"Bearer {OBSIDIAN_API_KEY}",
        "Content-Type": "text/markdown",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.put(
                url,
                content=note_content.encode("utf-8"),
                headers=headers
            )
            logger.info(f"Obsidian API response: {response.status_code}")
            return response.status_code in (200, 201, 204)
    except Exception as e:
        logger.error(f"Ошибка Obsidian API: {e}")
        return False


# ─── Обработчик сообщений ──────────────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return

    text = message.text
    author = message.from_user.full_name or message.from_user.username or "Неизвестно"

    data = parse_report(text)
    if data is None:
        return  # Не похоже на отчёт — игнорируем

    note = format_note(data, author, text)

    today = datetime.now().strftime("%Y-%m-%d")
    safe_author = re.sub(r"[^\w\-]", "_", author)
    filepath = f"%D0%9E%D1%82%D1%87%D1%91%D1%82%D1%8B/{today}_{safe_author}.md"

    success = await save_to_obsidian(note, filepath)

    выручка = data.get("выручка", 0)
    план = data.get("план", 0)
    процент = round(выручка / план * 100) if план > 0 else 0
    статус = "✅" if процент >= 100 else "⚠️" if процент >= 80 else "🔴"

    выручка_fmt = f"{выручка:,}".replace(",", " ")

    if success:
        await message.reply_text(
            f"📥 Отчёт сохранён в Obsidian\n"
            f"👤 {author}\n"
            f"💰 Выручка: {выручка_fmt} ₽\n"
            f"📊 План: {процент}% {статус}"
        )
    else:
        await message.reply_text(
            "⚠️ Отчёт получен, но не удалось сохранить в Obsidian.\n"
            "Проверь: Obsidian открыт? HTTP сервер включён? API ключ верный?"
        )


# ─── Запуск ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("✅ NextApple бот запущен...")
    app.run_polling()
