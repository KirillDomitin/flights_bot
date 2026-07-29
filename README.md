# ✈️ Flight Price Monitor

Бот следит за ценами на авиабилеты и присылает уведомления в Telegram-чат.
Берёт **самый дешёвый прямой рейс** с актуальными ценами (парсит Aviasales вживую).

## Отслеживаемые перелёты

| Маршрут | Дата |
|---------|------|
| ✈️ Москва → Пекин (MOW→PEK) | 22 сентября 2026 |
| ✈️ Шанхай → Москва (SHA→MOW) | 30 сентября 2026 |

## Что умеет бот

1. **Автопроверка 4 раза в день** — в 03:00, 09:00, 15:00 и 21:00 (МСК) проверяет
   цены и, если цена снизилась, шлёт уведомление в группу.
2. **Команда `/check`** — по запросу проверяет цены прямо сейчас и публикует их в группу.
3. **Команда `/chart`** — присылает график изменения цены по накопленной истории.
4. **Команда `/menu`** — добавить/убрать отслеживаемый перелёт: выбор города
   (автоподсказка), даты (inline-календарь), типа рейса (прямой/с пересадками).
5. **История цен** — все проверки пишутся в SQLite (`prices.db`).

## Быстрый старт

```bash
# 1. Зависимости
pip install -r requirements.txt
playwright install chromium        # браузер для парсинга Aviasales (~150 МБ, разово)

# 2. Настройка: скопировать .env.example → .env и заполнить
#    TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID

# 3. Запуск (рекомендуемый режим: /check + автопроверки в одном процессе)
python monitor.py --bot
```

Оставь окно запущенным — пока оно работает, бот отвечает на `/check` и проверяет
цены по расписанию.

## Использование в Telegram

В группе напиши:
```
/check@ИмяБота
```
Бот пришлёт текущие цены по обоим маршрутам.

Для графика истории цен:
```
/chart@ИмяБота
```
Бот пришлёт картинкой график изменения цены по обоим маршрутам. График строится
по данным из `prices.db`, поэтому чем дольше работает мониторинг, тем нагляднее.

Управление маршрутами:
```
/menu
```
Кнопки: ➕ добавить перелёт (город → город → дата → тип рейса) или 📋 мои
перелёты (удалить). Маршруты хранятся в БД, мониторинг подхватывает их сам.

> `@ИмяБота` обязателен из-за privacy mode. Чтобы работали короткое `/check` и
> **ввод города текстом в мастере `/menu`**, отключи privacy у бота в @BotFather
> (`/setprivacy` → Disable) и заново добавь бота в группу. Иначе мастер веди в
> личке с ботом (кнопки работают в группе всегда, а вот текст — только без privacy).

**Разница:** автопроверка (4 раза в сутки) пишет, только когда цена **упала**;
`/check` показывает текущую цену **всегда**.

## Команды CLI

| Команда | Назначение |
|---------|-----------|
| `python monitor.py --bot` | Бот: `/check` + автопроверки 4×/сутки (**основной режим**) |
| `python monitor.py` | Только планировщик (4×/сутки), без бота |
| `python monitor.py --now` | Разовая проверка прямо сейчас |
| `python monitor.py --history` | Показать историю цен из БД |
| `python monitor.py --chart [файл]` | Построить график цен из БД в PNG (по умолч. `price_chart.png`) |
| `python tools/get_chat_id.py` | Узнать chat_id для `TELEGRAM_CHAT_ID` |

> Можно запускать и как пакет: `python -m flight_monitor --bot` (эквивалентно
> `python monitor.py --bot`; `monitor.py` — тонкий shim для совместимости).

## Конфигурация (.env)

```
TELEGRAM_BOT_TOKEN=...         # токен бота от @BotFather
TELEGRAM_CHAT_ID=...           # id чата/группы (python get_chat_id.py)
PRICE_SOURCE=browser           # browser (парсинг Aviasales) | api (кэш Travelpayouts)
MONITOR_HEADLESS=true          # false — показать окно браузера (отладка)
TRAVELPAYOUTS_TOKEN=...        # нужен только при PRICE_SOURCE=api
REDIS_URL=                     # кэш цен; в Docker задаётся автоматически, локально пусто = кэш выкл.
CACHE_TTL_SECONDS=900          # время жизни кэша (15 мин)
```

**Кэш цен.** Повторный `/check` в течение 15 минут отдаётся из Redis мгновенно,
без повторного парсинга (~20–40 сек/маршрут). Плановые проверки (4×/сутки) всегда
берут свежую цену и перезаписывают кэш. Redis недоступен → бот просто делает
прямой запрос (кэш никогда не роняет работу).

## Запуск в Docker (рекомендуется)

Бот работает в контейнере: браузер Playwright и все зависимости уже в образе,
БД `prices.db` хранится в именованном томе и переживает пересборку. Рядом
поднимается контейнер `redis` — кэш цен (эфемерный, том не нужен).
`restart: unless-stopped` сам поднимает сервисы после перезагрузки и падений —
поэтому автозапуск через «Автозагрузку» Windows больше не нужен.

**Требуется:** установленный и запущенный Docker Desktop, заполненный `.env`.

```bash
# Собрать образ и запустить в фоне
docker compose up -d --build

# Логи (Ctrl+C — выйти из просмотра, контейнер продолжит работать)
docker compose logs -f

# Остановить
docker compose down

# Остановить и удалить историю цен (том с БД)
docker compose down -v
```

> ⚠️ **Только один экземпляр.** Telegram разрешает боту один поток `getUpdates`.
> Пока запущен контейнер — **не запускай** параллельно `python monitor.py --bot`
> вручную, иначе получишь `Conflict: terminated by other getUpdates request`.

Разовые команды внутри контейнера (например, построить график в файл):
```bash
docker compose run --rm bot python monitor.py --chart /app/data/price_chart.png
docker compose run --rm bot python monitor.py --history
```

### Локальный запуск без Docker

Лаунчер `run_bot.bat` по-прежнему запускает бота из venv (`.venv`) без окна:
```powershell
Start-Process 'G:\flight-monitor\run_bot.bat'                 # запустить
Get-Process pythonw -ErrorAction SilentlyContinue              # проверить
Get-Process pythonw -ErrorAction SilentlyContinue | Stop-Process -Force  # остановить
```

## Тесты

```bash
python -m unittest -v
```

## Структура

Проект собран в пакет `flight_monitor/` со слоями: **core** — логика,
**sources** — провайдеры цен, **repository** — хранилища, **bot** — Telegram.

```
monitor.py                      # тонкий shim → flight_monitor.cli.main()
flight_monitor/
├── cli.py                      # разбор аргументов, запуск режима
├── config.py                  # load_config, ROUTES, константы, логирование
├── notifier.py                # форматирование сообщений + отправка в Telegram
├── chart.py                   # график истории цен (matplotlib)
├── core/monitoring.py         # fetch_price (кэш), check_route, проверки, scheduler
├── sources/
│   ├── api.py                 # Travelpayouts Data API (запасной источник)
│   ├── browser.py             # парсинг Aviasales через Playwright (основной)
│   └── places.py              # автоподсказка городов (Travelpayouts places2)
├── repository/
│   ├── storage.py             # цены и маршруты в SQLite
│   └── cache.py               # кэш запросов (Redis/in-memory за интерфейсом Cache)
└── bot/
    ├── app.py                 # сборка/запуск бота (polling + JobQueue)
    ├── handlers.py            # /start /check /chart, плановая джоба
    └── menu.py                # мастер /menu (добавление/удаление маршрутов)
tests/test_monitoring.py        # мок-тесты логики и кэша
tools/get_chat_id.py            # утилита определения chat_id
```

## Ограничения

- Парсинг медленнее API: ~20–40 сек на маршрут (запуск браузера + рендер).
- Хрупкость: если Aviasales изменит вёрстку сайта, парсер может сломаться —
  тогда переключись на `PRICE_SOURCE=api` (нужен `TRAVELPAYOUTS_TOKEN`) или поправь
  селекторы в `browser_client.py`.
- Бот работает, только пока запущен процесс (или настроен автозапуск).
