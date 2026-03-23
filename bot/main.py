import logging
import os
from typing import Iterable

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.db import Base, SessionLocal, engine, ensure_database_schema
from app.history import request_history
from app.indexer import index_path
from app.rag import answer_question

load_dotenv()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)


def _should_auto_index() -> bool:
    return os.getenv("AUTO_INDEX_ON_STARTUP", "true").lower() in {"1", "true", "yes", "on"}


def _allowed_chat_ids() -> set[int]:
    raw = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "")
    values = {item.strip() for item in raw.split(",") if item.strip()}
    return {int(value) for value in values}


def _split_message(text: str, limit: int = 3800) -> Iterable[str]:
    start = 0
    while start < len(text):
        yield text[start : start + limit]
        start += limit


def _dump_model(item):
    return item.model_dump() if hasattr(item, "model_dump") else item.dict()


async def _is_allowed(update: Update) -> bool:
    allowed_ids = _allowed_chat_ids()
    if not allowed_ids:
        return True
    chat = update.effective_chat
    return bool(chat and chat.id in allowed_ids)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_allowed(update):
        return
    await update.message.reply_text(
        "Привет. Я бот учебного офиса. Пришлите вопрос, и я отвечу по загруженной базе документов."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_allowed(update):
        return
    if not update.message or not update.message.text:
        return

    question = update.message.text.strip()
    if not question:
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    with SessionLocal() as session:
        response = answer_question(session, question, top_k=8)

    request_history.add(
        {
            "asked_at": update.message.date.isoformat(),
            "question": question,
            "answer_preview": response.answer[:300],
            "sources": [_dump_model(source) for source in response.sources],
        }
    )

    text = response.answer
    if response.sources:
        source_lines = ["", "Источники:"]
        for source in response.sources[:5]:
            source_lines.append(
                f"- {source.title}, фрагмент #{source.chunk_index}"
            )
        text = "\n".join([text, *source_lines])

    for chunk in _split_message(text):
        await update.message.reply_text(chunk)


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN in environment before starting the bot.")

    Base.metadata.create_all(bind=engine)
    ensure_database_schema()
    if _should_auto_index():
        data_dir = os.getenv("DATA_DIR", "./docs")
        logger.info("Auto indexing from %s before bot start", data_dir)
        index_path(data_dir)

    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Starting Telegram bot")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
