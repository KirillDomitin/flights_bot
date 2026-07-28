# ✈️ Flight Price Monitor

Бот следит за ценами на авиабилеты и присылает уведомления в Telegram-чат.
Берёт **самый дешёвый прямой рейс** с актуальными ценами (парсит Aviasales вживую).

## Отслеживаемые перелёты

| Маршрут | Дата |
|---------|------|
| ✈️ Москва → Пекин (MOW→PEK) | 22 сентября 2026 |
| ✈️ Шанхай → Москва (SHA→MOW) | 30 сентября 2026 |

## Что умеет бот

1. **Автопроверка дважды в день** — в 09:00 и 21:00 (МСК) проверяет цены и, если
   цена снизилась, шлёт уведомление в группу.
2. **Команда `/check`** — по запросу проверяет цены прямо сейчас и публикует их в группу.
3. **История цен** — все проверки пишутся в SQLite (`prices.db`).

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

> `@ИмяБота` обязателен из-за privacy mode. Чтобы работало короткое `/check`,
> отключи privacy у бота в @BotFather (`/setprivacy` → Disable) и заново добавь
> бота в группу.

**Разница:** автопроверка (09:00/21:00) пишет, только когда цена **упала**;
`/check` показывает текущую цену **всегда**.

## Команды CLI

| Команда | Назначение |
|---------|-----------|
| `python monitor.py --bot` | Бот: `/check` + автопроверки 09:00/21:00 (**основной режим**) |
| `python monitor.py` | Только планировщик (09:00/21:00), без бота |
| `python monitor.py --now` | Разовая проверка прямо сейчас |
| `python monitor.py --history` | Показать историю цен из БД |
| `python get_chat_id.py` | Узнать chat_id для `TELEGRAM_CHAT_ID` |

## Конфигурация (.env)

```
TELEGRAM_BOT_TOKEN=...         # токен бота от @BotFather
TELEGRAM_CHAT_ID=...           # id чата/группы (python get_chat_id.py)
PRICE_SOURCE=browser           # browser (парсинг Aviasales) | api (кэш Travelpayouts)
MONITOR_HEADLESS=true          # false — показать окно браузера (отладка)
TRAVELPAYOUTS_TOKEN=...        # нужен только при PRICE_SOURCE=api
```

## Автозапуск при старте Windows

В комплекте лаунчер `run_bot.bat` — запускает бота из venv **без окна** (pythonw),
логи пишет в `monitor.log`.

**Настроено:** ярлык `FlightMonitorBot.lnk` в папке «Автозагрузка», поэтому бот
стартует при каждом входе в Windows (прав администратора не требует).

```powershell
# Папка автозагрузки (там лежит ярлык)
explorer shell:startup

# Запустить бота сейчас, не дожидаясь перезагрузки
Start-Process 'G:\flight-monitor\run_bot.bat'

# Проверить, что бот работает
Get-Process pythonw -ErrorAction SilentlyContinue

# Остановить бота
Get-Process pythonw -ErrorAction SilentlyContinue | Stop-Process -Force

# Отключить автозапуск — удалить ярлык
Remove-Item "$([Environment]::GetFolderPath('Startup'))\FlightMonitorBot.lnk"
```

> ⚠️ **Только один экземпляр.** Telegram разрешает боту один поток `getUpdates`.
> Если включён автозапуск — **не запускай** одновременно `python monitor.py --bot`
> вручную, иначе получишь ошибку `Conflict: terminated by other getUpdates request`.

**Альтернатива с админ-правами** — задача в Планировщике (запуск даже до входа):
```powershell
schtasks /create /tn "FlightMonitorBot" /tr "G:\flight-monitor\run_bot.bat" /sc onlogon /rl highest /f
```

## Тесты

```bash
python -m unittest -v
```

## Структура

| Файл | Назначение |
|------|-----------|
| `monitor.py` | Точка входа: бот, планировщик, CLI, выбор источника цен |
| `browser_client.py` | Парсинг Aviasales через Playwright (основной источник) |
| `api_client.py` | Travelpayouts Data API (запасной источник) |
| `notifier.py` | Telegram-уведомления и форматирование |
| `storage.py` | Хранение истории в SQLite |
| `get_chat_id.py` | Утилита определения chat_id |
| `test_monitor.py` | Мок-тесты логики сравнения цен |

## Ограничения

- Парсинг медленнее API: ~20–40 сек на маршрут (запуск браузера + рендер).
- Хрупкость: если Aviasales изменит вёрстку сайта, парсер может сломаться —
  тогда переключись на `PRICE_SOURCE=api` (нужен `TRAVELPAYOUTS_TOKEN`) или поправь
  селекторы в `browser_client.py`.
- Бот работает, только пока запущен процесс (или настроен автозапуск).
