"""Показать все чаты, доступные боту (для заполнения TELEGRAM_CHAT_ID).

Порядок действий:
  1. Добавьте бота в нужный чат/группу.
  2. Напишите в этом чате /start@ИмяБота (для группы — обязательно с @упоминанием).
  3. Запустите:  python get_chat_id.py
  4. Скопируйте id нужного чата в .env → TELEGRAM_CHAT_ID
"""
from __future__ import annotations

import os
import sys

import httpx
from dotenv import load_dotenv

# Консоль Windows не всегда в UTF-8 — переключаем, чтобы не падать на кириллице.
for _stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(_stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")


def main() -> None:
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN не задан в .env")

    try:
        resp = httpx.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            timeout=20.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        raise SystemExit(f"Ошибка запроса к Telegram: {exc}")

    if not data.get("ok"):
        raise SystemExit(f"Telegram вернул ошибку: {data}")

    # Собираем уникальные чаты из всех типов апдейтов
    chats: dict[int, dict] = {}
    for update in data.get("result", []):
        msg = (
            update.get("message")
            or update.get("channel_post")
            or update.get("my_chat_member")
            or {}
        )
        chat = msg.get("chat")
        if chat and "id" in chat:
            chats[chat["id"]] = chat

    if not chats:
        print(
            "Чатов не найдено.\n"
            "Проверьте, что вы:\n"
            "  • добавили бота в группу;\n"
            "  • написали в ней /start@ИмяБота (с @упоминанием — из-за privacy mode);\n"
            "  • сделали это недавно (Telegram хранит апдейты ~24 часа).\n"
            "Затем запустите скрипт снова."
        )
        return

    print("Доступные чаты (значение для TELEGRAM_CHAT_ID — колонка id):\n")
    for chat_id, chat in chats.items():
        title = chat.get("title") or chat.get("first_name") or ""
        print(f"  id = {chat_id:<16}  type = {chat.get('type'):<10}  {title}")


if __name__ == "__main__":
    main()
