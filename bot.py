import asyncio
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from background_tasks import expire_reserved_slots
from config import BOT_TOKEN, TELEGRAM_PROXY_URL
from handlers import start, registration, sales, admin
from handlers.payments_watcher import payments_watcher

async def main():
    session = (
        AiohttpSession(proxy=TELEGRAM_PROXY_URL)
        if TELEGRAM_PROXY_URL
        else None
    )

    bot = Bot(
        token=BOT_TOKEN,
        session=session,
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
    asyncio.create_task(payments_watcher(bot))  
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
