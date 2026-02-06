import asyncio
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from background_tasks import expire_reserved_slots
from config import BOT_TOKEN
from handlers import start, registration, sales, admin

async def main():
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        )
    )


    dp = Dispatcher()

    # handlers
    dp.include_router(start.router)
    dp.include_router(registration.router)
    dp.include_router(sales.router)
    dp.include_router(admin.router)
    asyncio.create_task(expire_reserved_slots(bot))
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
