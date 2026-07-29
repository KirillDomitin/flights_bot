"""Парсинг цен с Aviasales через headless-браузер (Playwright).

Aviasales — это SPA: цены подгружаются в браузере после рендера, поэтому
обычным HTTP-запросом их не получить. Открываем ссылку поиска в Chromium,
ждём появления нужной карточки и читаем её цену, авиакомпанию и число пересадок.

Карточки на странице:
  «Самый дешёвый прямой»  — берём при direct_only=True;
  «Самый дешёвый»         — самый дешёвый вообще (может быть с пересадками),
                            берём при direct_only=False.
IATA-код авиакомпании — из URL логотипа img.avs.io/.../al_square/XX.
Метод извлечения проверен на живых страницах MOW→PEK / SHA→MOW.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# JS перебирает переданные метки карточек, находит первую, поднимается до самой
# карточки (содержит ₽ и «в пути») и достаёт цену, авиакомпанию и число пересадок
# (из строки «… в пути / Прямой» или «… в пути / N пересадка»).
_EXTRACT_JS = r"""(labels) => {
  function extract(label) {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let labelNode = null;
    while (walker.nextNode()) {
      if (walker.currentNode.textContent.trim() === label) {
        labelNode = walker.currentNode; break;
      }
    }
    if (!labelNode) return null;
    let el = labelNode.parentElement;
    while (el && el.parentElement &&
           !(/₽/.test(el.innerText) && /в пути/.test(el.innerText))) {
      el = el.parentElement;
    }
    if (!el) return null;
    const text = el.innerText;
    const priceM = text.match(/([\d\s   ⁠]+)₽/);
    const price = priceM ? priceM[1].replace(/[\s   ⁠]/g, '') : null;
    const logo = el.querySelector('img[src*="al_square"]');
    const sm = logo && (logo.src || '').match(/al_square\/([A-Z0-9]{2})/);
    let stops = null;
    const stopsM = text.match(/в пути[\s\S]{0,40}?(Прямой|(\d+)\s*пересад)/);
    if (stopsM) { stops = /Прямой/.test(stopsM[1]) ? 0 : parseInt(stopsM[2], 10); }
    return {
      found: !!price,
      price: price,
      iata: sm ? sm[1] : null,
      name: logo ? logo.alt : null,
      stops: stops,
    };
  }
  for (const label of labels) {
    const r = extract(label);
    if (r && r.found) return r;
  }
  return { found: false };
}"""


def build_search_url(origin: str, destination: str, depart_date: str) -> str:
    """Собрать ссылку поиска Aviasales вида MOW2209PEK1 (origin+DDMM+dest+пассажиры)."""
    dt = datetime.strptime(depart_date, "%Y-%m-%d")
    return f"https://www.aviasales.ru/search/{origin}{dt.strftime('%d%m')}{destination}1"


def fetch_cheapest(
    origin: str,
    destination: str,
    depart_date: str,
    *,
    direct_only: bool = True,
    timeout: int = 60,
    headless: bool = True,
) -> Optional[dict]:
    """Вернуть самый дешёвый рейс по маршруту на дату или None.

    direct_only=True  — берём карточку «Самый дешёвый прямой» (stops=0);
    direct_only=False — карточку «Самый дешёвый» (может быть с пересадками);
                        если самый дешёвый оказался прямым, страница может
                        показать только «Самый дешёвый прямой» — используем её.

    Открывает страницу поиска в Chromium, опрашивает DOM до появления карточки
    (или до таймаута). Ошибки браузера не крашат процесс — логируем и вернём None.
    """
    # Импортируем внутри функции: playwright не нужен, если выбран API-режим.
    try:
        from playwright.sync_api import sync_playwright
        from playwright.sync_api import TimeoutError as PWTimeout
    except ImportError:
        logger.error("Playwright не установлен. Запустите: playwright install chromium")
        return None

    labels = (
        ["Самый дешёвый прямой"]
        if direct_only
        else ["Самый дешёвый", "Самый дешёвый прямой"]
    )
    url = build_search_url(origin, destination, depart_date)
    result: Optional[dict] = None

    try:
        with sync_playwright() as pw:
            # Без этого флага Aviasales определяет headless-браузер и не отдаёт
            # результаты поиска (форма грузится, а цены — нет).
            browser = pw.chromium.launch(
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                context = browser.new_context(
                    locale="ru-RU",
                    user_agent=_USER_AGENT,
                    viewport={"width": 1366, "height": 900},
                )
                # Прячем признак автоматизации (navigator.webdriver)
                context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
                )
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)

                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    data = page.evaluate(_EXTRACT_JS, labels)
                    if data.get("found") and data.get("price"):
                        # Цена уже есть, но логотип авиакомпании подгружается
                        # чуть позже — ждём и уточняем, чтобы получить IATA-код.
                        if not data.get("iata"):
                            page.wait_for_timeout(4000)
                            refined = page.evaluate(_EXTRACT_JS, labels)
                            if refined.get("price"):
                                data = refined
                        result = data
                        break
                    page.wait_for_timeout(2000)  # мс
            finally:
                browser.close()
    except PWTimeout:
        logger.error("Таймаут загрузки Aviasales %s→%s", origin, destination)
        return None
    except Exception as exc:  # noqa: BLE001 — не роняем цикл мониторинга
        logger.error("Ошибка парсинга Aviasales %s→%s: %s", origin, destination, exc)
        return None

    mode = "прямой" if direct_only else "с пересадками"
    if not result:
        logger.info(
            "Нет предложений (%s) %s→%s на %s (карточка не найдена за %sс)",
            mode, origin, destination, depart_date, timeout,
        )
        return None

    stops = result.get("stops")
    if stops is None:
        stops = 0 if direct_only else None
    record = {
        "origin": origin,
        "destination": destination,
        "depart_date": depart_date,
        "price": int(result["price"]),
        "airline": result.get("iata"),
        "flight_number": None,
        "stops": stops,
        "link": url,
        "currency": "rub",
    }
    logger.info(
        "Aviasales: самый дешёвый (%s) %s→%s: %s ₽ (%s, пересадок: %s)",
        mode, origin, destination, record["price"],
        result.get("name") or "—", stops if stops is not None else "?",
    )
    return record
