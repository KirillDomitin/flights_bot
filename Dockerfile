# Лёгкая база: python-slim (Docker Hub, быстро) + доустановка ТОЛЬКО Chromium.
# Официальный образ mcr.microsoft.com/playwright тянет Chromium+Firefox+WebKit
# (~1.8 ГБ) и отдаётся с mcr крайне медленно — нам же нужен один браузер.
FROM python:3.12-slim

WORKDIR /app

# Зависимости отдельным слоем — кешируется, пока requirements.txt не меняется
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Chromium + системные библиотеки для него. `--with-deps` сам делает apt-get
# update и ставит нужные пакеты; браузер кладётся в кэш Playwright внутри образа.
RUN playwright install --with-deps chromium

# Код приложения
COPY . .

# БД SQLite держим в примонтированном томе (см. docker-compose*.yml), а не в образе.
# Таймзону обеспечивает pip-пакет tzdata (zoneinfo), OS-tzdata в slim не нужен.
ENV MONITOR_DB_PATH=/app/data/prices.db \
    PRICE_SOURCE=browser \
    MONITOR_HEADLESS=true \
    TZ=Europe/Moscow \
    PYTHONUNBUFFERED=1

# Рекомендуемый режим: /check + /chart + автопроверки в одном процессе
CMD ["python", "monitor.py", "--bot"]
