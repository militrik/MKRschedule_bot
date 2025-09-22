import asyncio
import os
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeDefault,
)
from dotenv import load_dotenv

from config import Config
from db import init_engine, create_all
import models  # для create_all
from handlers import onboarding, commands, errors
from scheduler import BotScheduler


async def _setup_bot_commands(bot: Bot):
    """
    Якщо DISABLE_SLASH_MENU=1 — очищаємо офіційне "/"-меню.
    Інакше — встановлюємо наш список команд з описами.
    """
    if os.getenv("DISABLE_SLASH_MENU", "0") == "1":
        # Видалити команди у приватних чатах (з і без мови) і у дефолтному скопі
        try:
            await bot.delete_my_commands(scope=BotCommandScopeAllPrivateChats(), language_code="uk")
        except Exception:
            pass
        try:
            await bot.delete_my_commands(scope=BotCommandScopeAllPrivateChats())
        except Exception:
            pass
        try:
            await bot.delete_my_commands(scope=BotCommandScopeDefault(), language_code="uk")
        except Exception:
            pass
        try:
            await bot.delete_my_commands(scope=BotCommandScopeDefault())
        except Exception:
            pass
        return

    # Інакше — ставимо наші команди
    cmds = [
        BotCommand(command="start", description="⚙️ Налаштування (роль, група/викладач, нагадування)"),
        BotCommand(command="next", description="⏭ Найближча пара"),
        BotCommand(command="today", description="📅 Пари на сьогодні"),
        BotCommand(command="tomorrow", description="📆 Пари на завтра"),
        BotCommand(command="week", description="🗓 Пари на тиждень"),
        BotCommand(command="help", description="ℹ️ Довідка"),
    ]
    await bot.set_my_commands(cmds, scope=BotCommandScopeAllPrivateChats(), language_code="uk")
    #await bot.set_my_commands(cmds, scope=BotCommandScopeAllPrivateChats())


async def main():
    load_dotenv()
    cfg = Config.load()
    if not cfg.bot_token:
        raise RuntimeError("BOT_TOKEN не задано")

    init_engine(cfg.database_url)
    await create_all(models)

    bot = Bot(token=cfg.bot_token, default=DefaultBotProperties(parse_mode=None, link_preview_is_disabled=True))
    await bot.delete_webhook(drop_pending_updates=True)
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(onboarding.router)
    dp.include_router(commands.router)
    dp.include_router(errors.router)

    # Налаштувати (або очистити) офіційне "/"-меню
    await _setup_bot_commands(bot)

    bs = BotScheduler(bot)
    bs.start()

    print("Bot started.")
    try:
        await dp.start_polling(bot)
    except (asyncio.CancelledError, KeyboardInterrupt):
        # Нормальне завершення: Ctrl+C, SIGTERM або скасування тасків поллінга
        logging.info("Shutdown requested, stopping gracefully...")
    finally:
        # Акуратно закриваємо HTTP-сесію бота
        try:
            await bot.session.close()
        except Exception:
            pass
        # Якщо у вашого планувальника є метод зупинки — зупиняємо
        try:
            if hasattr(bs, "stop"):
                bs.stop()
            elif hasattr(bs, "shutdown"):
                bs.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
