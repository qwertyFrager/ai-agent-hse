import logging
import os
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Iterable
from uuid import uuid4

from dotenv import load_dotenv
from sqlalchemy import select
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.db import Base, SessionLocal, engine, ensure_database_schema
from app.history import request_history
from app.indexer import index_path, normalize_stored_doc_paths
from app.models import Doc
from app.rag import answer_question

load_dotenv()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

BUTTON_ASK = "Задать вопрос"
BUTTON_DOCS = "Документы"
BUTTON_TOPICS = "Частые темы"
BUTTON_HELP = "Как пользоваться"
BUTTON_MENU = "В меню"

CALLBACK_MENU = "menu"
CALLBACK_TOPIC_PREFIX = "topic:"
CALLBACK_DOCS_PREFIX = "docs:"
CALLBACK_SOURCES_PREFIX = "sources:"

DOCS_PAGE_SIZE = 5
MAX_STORED_SOURCE_SETS = 20

TOPIC_QUERIES = OrderedDict(
    [
        ("transfer", ("Перевод", "Каков порядок перевода студентов?")),
        ("session", ("Сессия", "Когда проходит сессия 3 модуля?")),
        ("vkr", ("ВКР", "Какие правила действуют для ВКР?")),
        ("schedule", ("Расписание", "Какое расписание занятий на текущую неделю?")),
        ("sport", ("Физкультура", "Каков порядок проведения занятий по физической культуре?")),
        ("charter", ("Устав", "Какие общие правила и нормы закреплены в уставе?")),
    ]
)


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


def _main_menu_markup() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [BUTTON_ASK, BUTTON_DOCS],
            [BUTTON_TOPICS, BUTTON_HELP],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие или просто напишите вопрос",
    )


def _answer_actions_markup(source_key: str | None) -> InlineKeyboardMarkup:
    buttons = []
    if source_key:
        buttons.append(
            [InlineKeyboardButton("Показать источники", callback_data=f"{CALLBACK_SOURCES_PREFIX}{source_key}")]
        )
    buttons.append([InlineKeyboardButton(BUTTON_MENU, callback_data=CALLBACK_MENU)])
    return InlineKeyboardMarkup(buttons)


def _topics_markup() -> InlineKeyboardMarkup:
    topic_buttons = []
    items = list(TOPIC_QUERIES.items())
    for index in range(0, len(items), 2):
        row = []
        for key, (label, _) in items[index : index + 2]:
            row.append(InlineKeyboardButton(label, callback_data=f"{CALLBACK_TOPIC_PREFIX}{key}"))
        topic_buttons.append(row)
    topic_buttons.append([InlineKeyboardButton(BUTTON_MENU, callback_data=CALLBACK_MENU)])
    return InlineKeyboardMarkup(topic_buttons)


def _documents_markup(page: int, has_more: bool) -> InlineKeyboardMarkup:
    buttons = []
    if has_more:
        buttons.append(
            [InlineKeyboardButton("Показать еще", callback_data=f"{CALLBACK_DOCS_PREFIX}{page + 1}")]
        )
    if page > 0:
        buttons.append([InlineKeyboardButton("Сначала", callback_data=f"{CALLBACK_DOCS_PREFIX}0")])
    buttons.append([InlineKeyboardButton(BUTTON_MENU, callback_data=CALLBACK_MENU)])
    return InlineKeyboardMarkup(buttons)


def _format_help_text() -> str:
    return (
        "Я отвечаю на вопросы по документам учебного офиса.\n\n"
        "Как пользоваться:\n"
        "1. Нажмите «Задать вопрос» или просто отправьте сообщение.\n"
        "2. Формулируйте вопрос конкретно: например, про перевод, сессию или ВКР.\n"
        "3. Если нужны подтверждения, откройте источники по кнопке после ответа.\n\n"
        "Примеры вопросов:\n"
        "- Когда проходит сессия 3 модуля?\n"
        "- Какие правила перевода студентов?\n"
        "- Что сказано в документах про ВКР?"
    )


def _fetch_documents(page: int) -> tuple[list[Doc], bool]:
    safe_page = max(page, 0)
    offset = safe_page * DOCS_PAGE_SIZE
    with SessionLocal() as session:
        rows = (
            session.execute(
                select(Doc)
                .where(Doc.status == "active")
                .order_by(Doc.title)
                .offset(offset)
                .limit(DOCS_PAGE_SIZE + 1)
            )
            .scalars()
            .all()
        )
    has_more = len(rows) > DOCS_PAGE_SIZE
    return rows[:DOCS_PAGE_SIZE], has_more


def _format_documents_text(page: int, docs: list[Doc]) -> str:
    if not docs:
        if page == 0:
            return "В базе пока нет документов."
        return "Больше документов не найдено."

    lines = [f"Документы, страница {page + 1}:"]
    start_number = page * DOCS_PAGE_SIZE + 1
    for index, doc in enumerate(docs, start=start_number):
        lines.append(f"{index}. {doc.title}")
    return "\n".join(lines)


def _remember_sources(context: ContextTypes.DEFAULT_TYPE, sources) -> str | None:
    if not sources:
        return None

    store = context.user_data.setdefault("source_sets", OrderedDict())
    source_key = uuid4().hex[:12]
    store[source_key] = [_dump_model(source) for source in sources]
    while len(store) > MAX_STORED_SOURCE_SETS:
        store.popitem(last=False)
    return source_key


def _format_sources_text(source_items: list[dict]) -> str:
    if not source_items:
        return "Источники для этого ответа не найдены."

    lines = ["Источники:"]
    for index, source in enumerate(source_items[:5], start=1):
        lines.append(f"{index}. {source['title']}, фрагмент #{source['chunk_index']}")
        lines.append(f"   {source['snippet']}")
    return "\n".join(lines)


async def _is_allowed(update: Update) -> bool:
    allowed_ids = _allowed_chat_ids()
    if not allowed_ids:
        return True
    chat = update.effective_chat
    return bool(chat and chat.id in allowed_ids)


async def _clear_inline_markup(query) -> None:
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except BadRequest:
        return


async def _show_menu(message) -> None:
    await message.reply_text(
        "Главное меню. Выберите действие или просто напишите вопрос.",
        reply_markup=_main_menu_markup(),
    )


async def _show_help(message) -> None:
    await message.reply_text(_format_help_text(), reply_markup=_main_menu_markup())


async def _show_topics(message) -> None:
    await message.reply_text(
        "Выберите частую тему. Я сразу отправлю соответствующий запрос в базу документов.",
        reply_markup=_topics_markup(),
    )


async def _show_documents(message, page: int = 0) -> None:
    docs, has_more = _fetch_documents(page)
    await message.reply_text(
        _format_documents_text(page, docs),
        reply_markup=_documents_markup(page, has_more),
    )


async def _send_answer(
    *,
    message,
    chat_id: int,
    question: str,
    context: ContextTypes.DEFAULT_TYPE,
    asked_at: str,
) -> None:
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    with SessionLocal() as session:
        response = answer_question(session, question, top_k=8)

    request_history.add(
        {
            "asked_at": asked_at,
            "question": question,
            "answer_preview": response.answer[:300],
            "sources": [_dump_model(source) for source in response.sources],
        }
    )

    source_key = _remember_sources(context, response.sources)
    action_markup = _answer_actions_markup(source_key)

    parts = list(_split_message(response.answer))
    for index, chunk in enumerate(parts):
        reply_markup = action_markup if index == len(parts) - 1 else None
        await message.reply_text(chunk, reply_markup=reply_markup)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_allowed(update):
        return
    await update.message.reply_text(
        "Привет. Я бот учебного офиса. Выберите действие в меню или сразу напишите вопрос по документам.",
        reply_markup=_main_menu_markup(),
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_allowed(update):
        return
    await _show_menu(update.message)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_allowed(update):
        return

    query = update.callback_query
    if not query or not query.message:
        return

    await query.answer()
    data = query.data or ""

    if data == CALLBACK_MENU:
        await _clear_inline_markup(query)
        await _show_menu(query.message)
        return

    if data.startswith(CALLBACK_TOPIC_PREFIX):
        topic_key = data.removeprefix(CALLBACK_TOPIC_PREFIX)
        topic = TOPIC_QUERIES.get(topic_key)
        if not topic:
            await query.message.reply_text("Не удалось определить тему. Вернитесь в меню и попробуйте снова.")
            return

        await _clear_inline_markup(query)
        label, question = topic
        await query.message.reply_text(
            f"Тема: {label}\nОбрабатываю готовый запрос: {question}",
            reply_markup=_main_menu_markup(),
        )
        await _send_answer(
            message=query.message,
            chat_id=query.message.chat.id,
            question=question,
            context=context,
            asked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        return

    if data.startswith(CALLBACK_DOCS_PREFIX):
        try:
            page = max(int(data.removeprefix(CALLBACK_DOCS_PREFIX)), 0)
        except ValueError:
            page = 0
        await _clear_inline_markup(query)
        await _show_documents(query.message, page=page)
        return

    if data.startswith(CALLBACK_SOURCES_PREFIX):
        source_key = data.removeprefix(CALLBACK_SOURCES_PREFIX)
        source_sets = context.user_data.get("source_sets", {})
        source_items = source_sets.get(source_key, [])
        await query.message.reply_text(
            _format_sources_text(source_items),
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(BUTTON_MENU, callback_data=CALLBACK_MENU)]]
            ),
        )
        return

    await query.message.reply_text("Команда не распознана. Вернитесь в меню.", reply_markup=_main_menu_markup())


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_allowed(update):
        return
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    if not text:
        return

    if text == BUTTON_ASK:
        await update.message.reply_text(
            "Напишите вопрос по документам учебного офиса. Можно сразу одним сообщением.",
            reply_markup=_main_menu_markup(),
        )
        return

    if text == BUTTON_DOCS:
        await _show_documents(update.message, page=0)
        return

    if text == BUTTON_TOPICS:
        await _show_topics(update.message)
        return

    if text == BUTTON_HELP:
        await _show_help(update.message)
        return

    if text == BUTTON_MENU:
        await _show_menu(update.message)
        return

    await _send_answer(
        message=update.message,
        chat_id=update.effective_chat.id,
        question=text,
        context=context,
        asked_at=update.message.date.isoformat(),
    )


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN in environment before starting the bot.")

    Base.metadata.create_all(bind=engine)
    ensure_database_schema()
    with SessionLocal() as session:
        normalize_stored_doc_paths(session)
    if _should_auto_index():
        data_dir = os.getenv("DATA_DIR", "./docs")
        logger.info("Auto indexing from %s before bot start", data_dir)
        index_path(data_dir)

    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", menu))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Starting Telegram bot")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
