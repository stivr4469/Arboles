import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from src.core.config import settings
from src.bot.copilot import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

bot = Bot(
    token=settings.telegram_bot_token,
    parse_mode=ParseMode.HTML,
)
dp = Dispatcher(storage=MemoryStorage())
dp.include_router(router)


async def main() -> None:
    logger.info("Ad-Pilot bot starting...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
