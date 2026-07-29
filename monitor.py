"""Точка входа-обёртка (shim): делегирует в flight_monitor.cli.main().

Оставлена для совместимости — команды `python monitor.py ...`, Docker (CMD) и
run_bot.bat продолжают работать без изменений. Вся логика — в пакете
flight_monitor/ (см. cli, config, core, sources, repository, bot).
"""
from flight_monitor.cli import main

if __name__ == "__main__":
    main()
