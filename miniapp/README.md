# CSCA Telegram Mini App

Веб-приложение с основной функциональностью бота: тренировка по темам и подтемам, физика/химия, экзамены (jan/dec/mar/apr), статистика, подсказки и решения.

## Запуск

1. Установите зависимости:

```bash
pip install -r requirements.txt
```

2. Задайте переменные окружения (тот же `.env`, что и для бота):

```bash
set BOT_TOKEN=ваш_токен_бота
set BOT_DATA_DIR=C:\Users\Sveta\Desktop\CSCA\bot\data
set MINIAPP_URL=https://ваш-публичный-https-адрес
```

3. Запустите API и статику:

```bash
python run_miniapp.py
```

По умолчанию сервер слушает `http://0.0.0.0:8080`.

4. Для локальной разработки используйте туннель (ngrok, cloudflared и т.п.) — Telegram требует **HTTPS**:

```bash
ngrok http 8080
```

Скопируйте HTTPS-URL (например `https://abc123.ngrok-free.app`) в `MINIAPP_URL`.

5. Запустите бота с тем же `MINIAPP_URL`:

```bash
set MINIAPP_URL=https://abc123.ngrok-free.app
python testbot.py
```

В `/start` появится кнопка **«Открыть приложение»**, в меню чата — **«📱 CSCA App»**.

## Настройка в BotFather (опционально)

В [@BotFather](https://t.me/BotFather) → ваш бот → **Menu Button** → **Configure menu button** → Web App → укажите тот же URL, что в `MINIAPP_URL`.

## Что реализовано

- Авторизация через `Telegram.WebApp.initData`
- Общая SQLite-база с ботом (`answers`, `progress`, `users`)
- Математика: темы → подтемы → задачи
- Физика и химия
- Экзамены: январь, декабрь, март, апрель
- Картинки к условиям, подсказки, текстовые решения
- Статистика и «Продолжить» с последнего вопроса

## Пока только в боте

- LLM-чат и «Объяснить подробнее»
- Умный режим «вся математика»
- Mock-экзамены
- Оплата / invite / paywall
- Видео-разборы

## API

Все запросы (кроме статики) требуют заголовок `X-Telegram-Init-Data` с `tg.initData`.

- `GET /api/me` — профиль и прогресс
- `GET /api/menu`, `/api/topics`, `/api/question`, `POST /api/answer`
- `GET /api/exams`, `/api/exams/{key}/question`
- `GET /api/solution`, `/api/hint`, `/api/image`
- `GET /api/stats`
