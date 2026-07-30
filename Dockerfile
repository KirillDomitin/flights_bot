# Официальный образ Playwright: Python + Chromium + системные зависимости
# уже внутри (тег совпадает с версией playwright из requirements.txt).
# Тяжёлый (~1.8 ГБ), но на сервере уже скачан и закэширован — пересборка при
# правках идёт по кэшу. Лёгкую базу (python-slim + chromium) отложили: mcr
# отдаётся очень медленно только при ПЕРВОМ пулле, а он уже позади.
FROM mcr.microsoft.com/playwright/python:v1.61.0-noble

WORKDIR /app

# Зависимости отдельным слоем — кешируется, пока requirements.txt не меняется
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код приложения
COPY . .

# БД SQLite держим в примонтированном томе (см. docker-compose*.yml), а не в образе
ENV MONITOR_DB_PATH=/app/data/prices.db \
    PRICE_SOURCE=browser \
    MONITOR_HEADLESS=true \
    TZ=Europe/Moscow \
    PYTHONUNBUFFERED=1

# Рекомендуемый режим: /check + /chart + автопроверки в одном процессе
CMD ["python", "monitor.py", "--bot"]
