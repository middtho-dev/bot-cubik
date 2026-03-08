import asyncio
import logging
import os
from dataclasses import dataclass
from contextlib import suppress

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

from db import Database


class UserRollState(StatesGroup):
    waiting_for_roll = State()


@dataclass
class Settings:
    bot_token: str
    db_path: str


def load_settings() -> Settings:
    load_dotenv()
    token = os.getenv("BOT_TOKEN", "")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set. Add it to .env or environment variables.")
    return Settings(
        bot_token=token,
        db_path=os.getenv("DB_PATH", "bot.db"),
    )


def rules_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Согласиться", callback_data="rules:agree")
    kb.button(text="❌ Отказаться", callback_data="rules:decline")
    kb.adjust(1)
    return kb.as_markup()


def main_menu_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="🎲 Бросить кубик Telegram", callback_data="menu:roll_tg")
    kb.button(text="✍️ Бросить свой кубик", callback_data="menu:roll_user")
    kb.adjust(1)
    return kb.as_markup()


def cancel_roll_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="↩️ Назад в меню", callback_data="menu:open")
    return kb.as_markup()


async def safe_delete_message(message: Message | None) -> None:
    if message is None:
        return
    with suppress(TelegramBadRequest):
        await message.delete()


async def open_or_update_main_menu(
    callback: CallbackQuery | None,
    message: Message,
    db: Database,
    text: str = "Главное меню:",
) -> None:
    user = message.from_user if callback is None else callback.from_user
    if user is None:
        return

    last_menu_id = await db.get_last_menu_message_id(user.id)
    chat_id = message.chat.id

    if callback and callback.message:
        with suppress(TelegramBadRequest):
            await callback.message.edit_text(text, reply_markup=main_menu_keyboard())
        await db.set_last_menu_message_id(user.id, callback.message.message_id)
        await callback.answer()
        return

    if last_menu_id:
        with suppress(TelegramBadRequest):
            await message.bot.edit_message_text(
                chat_id=chat_id,
                message_id=last_menu_id,
                text=text,
                reply_markup=main_menu_keyboard(),
            )
            return

    sent = await message.answer(text, reply_markup=main_menu_keyboard())
    await db.set_last_menu_message_id(user.id, sent.message_id)


def build_router(db: Database) -> Router:
    router = Router()

    @router.message(CommandStart())
    async def cmd_start(message: Message, state: FSMContext):
        await state.clear()
        await safe_delete_message(message)

        user = message.from_user
        if user is None:
            return

        await db.upsert_user(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
        )

        status = await db.get_rules_status(user.id)
        if status == "agreed":
            await open_or_update_main_menu(None, message, db, "✅ Вы уже согласились с правилами. Главное меню открыто.")
            return

        rules_text = (
            "👋 Добро пожаловать!\n\n"
            "📜 Правила:\n"
            "1. Нажмите «Согласиться», чтобы продолжить.\n"
            "2. В главном меню доступны 2 действия:\n"
            "   • «Бросить кубик Telegram» — бот отправит анимированный кубик.\n"
            "   • «Бросить свой кубик» — вы вводите число от 1 до 6.\n"
            "3. Если на кубике Telegram или на вашем кубике выпадает число 4, бот пропускает вас дальше."
        )

        msg = await message.answer(rules_text, reply_markup=rules_keyboard())
        await db.set_last_rules_message_id(user.id, msg.message_id)

    @router.callback_query(F.data == "rules:agree")
    async def agree_rules(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        user = callback.from_user
        if user is None or callback.message is None:
            return

        status = await db.get_rules_status(user.id)
        if status == "agreed":
            await callback.answer("Выбор уже зафиксирован", show_alert=False)
            await open_or_update_main_menu(callback, callback.message, db, "✅ Вы уже согласились с правилами. Главное меню открыто.")
            return

        await db.set_rules_agreement(user.id, True)
        with suppress(TelegramBadRequest):
            await callback.message.edit_text("✅ Вы согласились с правилами. Главное меню открыто.")

        await open_or_update_main_menu(None, callback.message, db)
        await callback.answer("Сохранено")

    @router.callback_query(F.data == "rules:decline")
    async def decline_rules(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        user = callback.from_user
        if user is None or callback.message is None:
            return

        status = await db.get_rules_status(user.id)
        if status == "agreed":
            await callback.answer("После согласия изменить выбор нельзя", show_alert=True)
            return

        await db.set_rules_agreement(user.id, False)
        with suppress(TelegramBadRequest):
            await callback.message.edit_text("❌ Вы отказались от правил. Для повторного показа нажмите /start.")
        await callback.answer("Отказ сохранен")

    @router.callback_query(F.data == "menu:open")
    async def menu_open(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        if callback.message is None:
            return
        await open_or_update_main_menu(callback, callback.message, db)

    @router.callback_query(F.data == "menu:roll_tg")
    async def roll_telegram_dice(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        user = callback.from_user
        if user is None or callback.message is None:
            return

        is_agreed = await db.has_agreed(user.id)
        if not is_agreed:
            await callback.answer("Сначала примите правила через /start.", show_alert=True)
            return

        await callback.answer()
        dice_message = await callback.message.answer_dice(emoji="🎲")
        dice_value = dice_message.dice.value if dice_message.dice else None

        if dice_value is None:
            await callback.message.answer("Не удалось получить значение кубика. Попробуйте еще раз.")
            return

        await db.save_telegram_roll(user.id, dice_value)

        if dice_value == 4:
            await db.set_passed(user.id, True)
            result = "🎉 Выпало 4! Вы проходите дальше."
        else:
            result = f"Выпало {dice_value}. Нужно 4, чтобы пройти дальше."

        await callback.message.answer(result)
        await open_or_update_main_menu(None, callback.message, db)

    @router.callback_query(F.data == "menu:roll_user")
    async def ask_user_roll(callback: CallbackQuery, state: FSMContext):
        user = callback.from_user
        if user is None or callback.message is None:
            return

        is_agreed = await db.has_agreed(user.id)
        if not is_agreed:
            await callback.answer("Сначала примите правила через /start.", show_alert=True)
            return

        await state.set_state(UserRollState.waiting_for_roll)
        with suppress(TelegramBadRequest):
            await callback.message.edit_text(
                "Введите число от 1 до 6:",
                reply_markup=cancel_roll_keyboard(),
            )
        await callback.answer()

    @router.message(UserRollState.waiting_for_roll)
    async def get_user_roll(message: Message, state: FSMContext):
        user = message.from_user
        if user is None:
            return

        text = (message.text or "").strip()
        await safe_delete_message(message)

        if not text.isdigit():
            await message.answer("Нужно ввести число от 1 до 6.")
            return

        value = int(text)
        if value < 1 or value > 6:
            await message.answer("Число должно быть от 1 до 6.")
            return

        await db.save_user_roll(user.id, value)
        await state.clear()

        if value == 4:
            await db.set_passed(user.id, True)
            await message.answer("🎉 Ваш кубик: 4! Вы проходите дальше.")
        else:
            await message.answer(f"Ваш кубик: {value}. Нужно 4, чтобы пройти дальше.")

        await open_or_update_main_menu(None, message, db)

    return router


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = load_settings()

    db = Database(settings.db_path)
    await db.connect()
    await db.init()

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()
    dp.include_router(build_router(db))

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
