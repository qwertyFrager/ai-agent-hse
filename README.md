# Учебный офис RAG

Минимальный RAG-агент на `FastAPI + Postgres`, который индексирует локальные документы из папки `docs`, отвечает через API, веб-дашборд и Telegram-бота.

Сейчас проект подготовлен под ваши 6 документов из `./docs`. Индексация идемпотентная: неизменившиеся файлы повторно не переразбираются.

## Что есть

- Индексация `pdf`, `docx`, `dotx`, `xlsx`, `xls`, `csv`
- Хранение только метаданных и чанков в Postgres
- Поиск через PostgreSQL Full-Text Search (`tsvector`, `plainto_tsquery`, `ts_rank_cd`)
- `POST /ask`, `POST /index`, `GET /health`
- Встроенный веб-дашборд на `/`
- Telegram-бот через `python -m bot.main`
- Опциональная генерация ответа через OpenAI-compatible API

## Быстрый старт

1. Создайте локальный `.env`:
   ```bash
   copy .env.example .env
   ```
2. Если хотите запускать весь стек одной командой через Docker Compose:
   ```bash
   docker compose up --build
   ```
   Что поднимется:
   - `postgres` - база данных
   - `api` - FastAPI + встроенный веб-дашборд на `http://localhost:8000/`
   - `bot` - Telegram-бот
   Все Python-зависимости из `requirements.txt` устанавливаются внутри Docker-образа на этапе сборки.
3. Если нужен локальный запуск без Docker, поднимите только Postgres:
   ```bash
   docker compose up -d postgres
   ```
4. Установите зависимости:
   ```bash
   pip install -r requirements.txt
   ```
5. Запустите API:
   ```bash
   uvicorn app.main:app --reload
   ```

По умолчанию приложение берет документы из `DATA_DIR=./docs` и на старте автоматически запускает индексацию, если `AUTO_INDEX_ON_STARTUP=true`.

## Индексация

Через CLI:

```bash
python -m scripts.index --path ./docs
```

Через API:

```bash
curl -X POST http://localhost:8000/index ^
  -H "Content-Type: application/json" ^
  -d "{\"path\":\"./docs\"}"
```

## API

Проверка:

```bash
curl http://localhost:8000/health
```

Запрос к агенту:

```bash
curl -X POST http://localhost:8000/ask ^
  -H "Content-Type: application/json" ^
  -d "{\"question\":\"Когда проходит сессия 3 модуля?\",\"top_k\":5}"
```

Пример ответа:

```json
{
  "answer": "Найденные источники и релевантные фрагменты: ...",
  "sources": [
    {
      "title": "СЕССИЯ 3 модуль (c 25.03.2026 по 28.03.2026) с изм. 20.03.26",
      "file_path": "E:\\MyProjects\\Python\\ai-agent-hse\\docs\\...",
      "chunk_index": 0,
      "snippet": "..."
    }
  ]
}
```

## Веб-дашборд

После запуска API откройте:

```text
http://localhost:8000/
```

Что есть на странице:

- форма вопроса к агенту
- список найденных источников
- последние запросы за текущий запуск приложения
- карточки со статистикой по документам и чанкам
- кнопка переиндексации `docs`

История запросов в дашборде хранится в памяти процесса и очищается после перезапуска приложения. Это сделано намеренно, чтобы не добавлять новые таблицы сверх `docs` и `chunks`.

Отдельный контейнер для фронтенда не нужен: статические файлы дашборда уже обслуживаются сервисом `api`.

## Telegram-бот

Добавьте токен в `.env`:

```env
TELEGRAM_BOT_TOKEN=your_token
```

Опционально можно ограничить доступ конкретными чатами:

```env
TELEGRAM_ALLOWED_CHAT_IDS=123456789,987654321
```

Запуск:

```bash
python -m bot.main
```

Или вместе со всем стеком:

```bash
docker compose up --build
```

Бот использует ту же логику поиска и ответа, что и HTTP API. При `AUTO_INDEX_ON_STARTUP=true` он тоже автоматически проверяет и индексирует `DATA_DIR` перед стартом polling.

## LLM

Если переменные не заданы, агент возвращает найденные источники и фрагменты без генерации:

```env
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
```

Если задать OpenAI-compatible настройки, агент будет пытаться синтезировать ответ только на основе найденных фрагментов:

```env
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=...
LLM_MODEL=gpt-4o-mini
```

## Переменные окружения

- `DATABASE_URL` - строка подключения к Postgres
- `DATA_DIR` - каталог с документами, по умолчанию `./docs`
- `LOG_LEVEL` - уровень логирования
- `AUTO_INDEX_ON_STARTUP` - автозапуск индексации на старте
- `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` - настройки LLM
- `TELEGRAM_BOT_TOKEN` - токен Telegram-бота
- `TELEGRAM_ALLOWED_CHAT_IDS` - белый список chat id через запятую

## Структура проекта

- `app/main.py` - API, дашборд и служебные HTTP-эндпоинты
- `app/indexer.py` - разбор файлов и загрузка в Postgres
- `app/rag.py` - общая логика ответа для API и Telegram
- `app/search.py` - FTS и fallback-поиск по `LIKE`
- `app/history.py` - in-memory история запросов для дашборда
- `app/static/` - фронтенд дашборда
- `bot/main.py` - Telegram-бот
- `scripts/index.py` - CLI-индексация

## Проверено локально

В текущем рабочем каталоге проиндексированы все 6 файлов из `./docs`. После индексации в базе:

- `docs_count = 6`
- `chunks_count = 184`

## Что дальше

Если хотите подключить Telegram полностью, пришлите токен бота. После этого можно будет сразу проверить обмен сообщениями на живом экземпляре.
