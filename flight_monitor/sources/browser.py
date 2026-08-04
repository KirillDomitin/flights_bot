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
    const stopsM = text.match(/в пути[\s\S]{0,40}?(прям|без пересад|(\d+)\s*пересад)/i);
    if (stopsM) { stops = stopsM[2] ? parseInt(stopsM[2], 10) : 0; }
    return {
      found: !!price,
      price: price,
      iata: sm ? sm[1] : null,
      name: logo ? logo.alt : null,
      stops: stops,
    };
  }
  const out = {};
  for (const label of labels) {
    const r = extract(label);
    if (r && r.found) out[label] = r;
  }
  return out;
}"""


_CHEAPEST_DIRECT = "Самый дешёвый прямой"
_CHEAPEST = "Самый дешёвый"
_PROBE_LABELS = [_CHEAPEST_DIRECT, _CHEAPEST]


def _select(found: dict, direct_only: bool) -> Optional[dict]:
    """Выбрать нужную карточку из найденных (по режиму).

    direct_only: сначала «Самый дешёвый прямой»; если его нет (частый случай на
    внутренних линиях, где дешёвый и так прямой) — «Самый дешёвый», но только если
    он прямой (stops == 0). Иначе прямого предложения не нашли (None).
    Иначе (можно с пересадками): «Самый дешёвый», иначе «Самый дешёвый прямой».
    """
    cheap_direct = found.get(_CHEAPEST_DIRECT)
    cheapest = found.get(_CHEAPEST)
    if direct_only:
        if cheap_direct:
            return cheap_direct
        if cheapest and cheapest.get("stops") == 0:
            return cheapest
        return None
    return cheapest or cheap_direct


def build_search_url(origin: str, destination: str, depart_date: str, passengers: int = 1) -> str:
    """Собрать ссылку поиска Aviasales вида MOW2209PEK1: origin+DDMM+dest+пассажиры.
    Последняя цифра — число взрослых; цена в выдаче будет за всех пассажиров."""
    dt = datetime.strptime(depart_date, "%Y-%m-%d")
    return f"https://www.aviasales.ru/search/{origin}{dt.strftime('%d%m')}{destination}{max(1, passengers)}"


# Экстрактор ВСЕХ карточек рейсов (не только «Самый дешёвый»): нужен, чтобы
# выбрать самый дешёвый рейс РОВНО с N пересадками. Якорь — логотип авиакомпании
# (img al_square) в каждой карточке; поднимаемся до карточки с ₽ и «в пути».
# Цену чистим через \D (убираем любые пробелы-разделители), чтобы не зависеть от
# конкретных unicode-пробелов.
_EXTRACT_LIST_JS = r"""() => {
  const out = [];
  const seen = new Set();
  for (const logo of document.querySelectorAll('img[src*="al_square"]')) {
    let el = logo.parentElement;
    while (el && el.parentElement &&
           !(/₽/.test(el.innerText) && /в пути/.test(el.innerText))) {
      el = el.parentElement;
    }
    if (!el) continue;
    const text = el.innerText;
    const priceM = text.match(/([\d   ⁠\s]+)₽/);
    if (!priceM) continue;
    const price = parseInt(priceM[1].replace(/\D/g, ''), 10);
    if (!price) continue;
    let stops = null;
    const sm = text.match(/в пути[\s\S]{0,40}?(прям|без пересад|(\d+)\s*пересад)/i);
    if (sm) stops = sm[2] ? parseInt(sm[2], 10) : 0;
    const am = (logo.src || '').match(/al_square\/([A-Z0-9]{2})/);
    const key = price + ':' + stops + ':' + (am ? am[1] : '');
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({price: price, stops: stops, iata: am ? am[1] : null, name: logo.alt || null});
  }
  return out;
}"""


def _select_from_list(tickets: list, stops_wanted: int) -> Optional[dict]:
    """Из всех карточек выбрать самый дешёвый рейс РОВНО с stops_wanted пересадками."""
    matching = [t for t in tickets if t.get("stops") == stops_wanted and t.get("price")]
    if not matching:
        return None
    return min(matching, key=lambda t: t["price"])


def fetch_cheapest(
    origin: str,
    destination: str,
    depart_date: str,
    *,
    direct_only: bool = True,
    stops_wanted: int = 0,
    passengers: int = 1,
    timeout: int = 60,
    headless: bool = True,
) -> Optional[dict]:
    """Вернуть самый дешёвый рейс по маршруту на дату или None.

    direct_only=True  — карточка «Самый дешёвый прямой» (а если её нет на
                        внутренних линиях — «Самый дешёвый» при условии, что он
                        прямой);
    direct_only=False — сканируем ВСЕ карточки рейсов и берём самый дешёвый РОВНО
                        с stops_wanted пересадками (если таких в выдаче нет — None).

    passengers — число взрослых; уходит в ссылку поиска, цена будет за всех.

    Открывает страницу поиска в Chromium, опрашивает DOM до появления нужного
    рейса (или до таймаута). Ошибки браузера не крашат процесс — логируем и None.
    """
    # Импортируем внутри функции: playwright не нужен, если выбран API-режим.
    try:
        from playwright.sync_api import sync_playwright
        from playwright.sync_api import TimeoutError as PWTimeout
    except ImportError:
        logger.error("Playwright не установлен. Запустите: playwright install chromium")
        return None

    url = build_search_url(origin, destination, depart_date, passengers)
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
                if direct_only:
                    result = _poll_direct(page, deadline)
                elif stops_wanted and stops_wanted > 0:
                    result = _poll_exact_stops(page, deadline, stops_wanted)
                else:
                    # legacy «с пересадками» без числа — самый дешёвый с любым
                    result = _poll_any(page, deadline)
            finally:
                browser.close()
    except PWTimeout:
        logger.error("Таймаут загрузки Aviasales %s→%s", origin, destination)
        return None
    except Exception as exc:  # noqa: BLE001 — не роняем цикл мониторинга
        logger.error("Ошибка парсинга Aviasales %s→%s: %s", origin, destination, exc)
        return None

    if direct_only:
        mode = "прямой"
    elif stops_wanted and stops_wanted > 0:
        mode = f"ровно {stops_wanted} пересадок"
    else:
        mode = "с пересадками"
    if not result:
        logger.info(
            "Нет предложений (%s) %s→%s на %s (за %sс)",
            mode, origin, destination, depart_date, timeout,
        )
        return None

    stops = result.get("stops")
    if stops is None:
        stops = 0 if direct_only else stops_wanted
    record = {
        "origin": origin,
        "destination": destination,
        "depart_date": depart_date,
        "price": int(result["price"]),
        "airline": result.get("iata"),
        "flight_number": None,
        "stops": stops,
        "passengers": passengers,
        "link": url,
        "currency": "rub",
    }
    logger.info(
        "Aviasales: (%s) %s→%s: %s ₽ (%s, пересадок: %s, пассажиров: %d)",
        mode, origin, destination, record["price"],
        result.get("name") or "—", stops if stops is not None else "?", passengers,
    )
    return record


def _poll_direct(page, deadline: float) -> Optional[dict]:
    """Ждать карточку «Самый дешёвый прямой» (или прямой «Самый дешёвый»)."""
    while time.monotonic() < deadline:
        found = page.evaluate(_EXTRACT_JS, _PROBE_LABELS)
        sel = _select(found, direct_only=True)
        if sel and sel.get("price"):
            # Цена есть, но логотип авиакомпании подгружается позже — уточняем IATA.
            if not sel.get("iata"):
                page.wait_for_timeout(4000)
                found = page.evaluate(_EXTRACT_JS, _PROBE_LABELS)
                sel2 = _select(found, direct_only=True)
                if sel2 and sel2.get("price"):
                    sel = sel2
            return sel
        page.wait_for_timeout(2000)
    return None


def _poll_any(page, deadline: float) -> Optional[dict]:
    """Legacy-режим «с пересадками» без числа: карточка «Самый дешёвый» (любые
    пересадки), иначе «Самый дешёвый прямой»."""
    while time.monotonic() < deadline:
        found = page.evaluate(_EXTRACT_JS, _PROBE_LABELS)
        sel = _select(found, direct_only=False)
        if sel and sel.get("price"):
            if not sel.get("iata"):
                page.wait_for_timeout(4000)
                found = page.evaluate(_EXTRACT_JS, _PROBE_LABELS)
                sel2 = _select(found, direct_only=False)
                if sel2 and sel2.get("price"):
                    sel = sel2
            return sel
        page.wait_for_timeout(2000)
    return None


def _poll_exact_stops(page, deadline: float, stops_wanted: int) -> Optional[dict]:
    """Сканировать все карточки, вернуть самый дешёвый рейс ровно с N пересадками.

    Раз точной карточки у Aviasales нет, ждём появления результатов и подходящего
    рейса. Если результаты уже прогрузились, но рейса ровно с N пересадками нет —
    не ждём до самого таймаута, а выходим через grace-окно (могло не быть в выдаче).
    """
    results_seen_at: Optional[float] = None
    grace = 12  # сек на догрузку после появления первых результатов
    while time.monotonic() < deadline:
        tickets = page.evaluate(_EXTRACT_LIST_JS)
        if tickets:
            if results_seen_at is None:
                results_seen_at = time.monotonic()
            sel = _select_from_list(tickets, stops_wanted)
            if sel:
                return sel
            if time.monotonic() - results_seen_at > grace:
                return None
        page.wait_for_timeout(2000)
    return None
