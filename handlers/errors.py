from aiogram import Router
from aiogram.types import ErrorEvent

router = Router(name="errors")

@router.errors()
async def errors_handler(event: ErrorEvent):
    print("Error in update:", event.exception)
