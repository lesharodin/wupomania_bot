from aiogram import Router
from aiogram.types import Message

router = Router()

@router.message()
async def debug_chat_id(message: Message):
    await message.answer(
        f"Chat ID: <code>{message.chat.id}</code>",
        parse_mode="HTML"
    )
