import asyncio
import logging
import os
from contextlib import suppress
from dataclasses import dataclass

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

from db import Database


class GameState(StatesGroup):
    waiting_tg_request = State()
    waiting_tg_roll = State()
    waiting_user_request = State()
    waiting_user_roll = State()


@dataclass
class Settings:
    bot_token: str
    db_path: str


def load_settings() -> Settings:
    load_dotenv()
    token = os.getenv("BOT_TOKEN", "")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set. Add it to .env or environment variables.")
    return Settings(bot_token=token, db_path=os.getenv("DB_PATH", "bot.db"))


def rules_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Согласиться", callback_data="rules:agree")
    kb.button(text="❌ Отказаться", callback_data="rules:decline")
    kb.adjust(1)
    return kb.as_markup()


def main_menu_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="🎲 Выбрать Telegram-кубик", callback_data="menu:choose_tg")
    kb.button(text="✍️ Выбрать свой кубик", callback_data="menu:choose_user")
    kb.adjust(1)
    return kb.as_markup()


def tg_roll_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="🎲 Бросить кубик", callback_data="game:tg_roll")
    return kb.as_markup()


def mode_title(mode: str) -> str:
    return "Telegram-кубик" if mode == "telegram" else "Свой кубик"


async def safe_delete_message(message: Message | None) -> None:
    if message is None:
        return
    with suppress(TelegramBadRequest):
        await message.delete()


async def wait_for_dice_animation() -> None:
    await asyncio.sleep(4)


async def open_or_update_main_menu(callback: CallbackQuery | None, message: Message, db: Database, text: str = "Главное меню:") -> None:
    user = callback.from_user if callback else message.from_user
    if user is None:
        return

    selected_mode = await db.get_selected_mode(user.id)
    passed = await db.has_passed(user.id)

    if selected_mode and not passed:
        locked_text = f"Вы уже выбрали режим: {mode_title(selected_mode)}. Выбор зафиксирован до попадания в игру."
        if callback:
            with suppress(TelegramBadRequest):
                await callback.message.edit_text(locked_text)
            await callback.answer()
        else:
            await message.answer(locked_text)
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

        await db.upsert_user(user.id, user.username, user.first_name)

        if await db.has_passed(user.id):
            await message.answer("🎮 Вы уже в игре.")
            return

        status = await db.get_rules_status(user.id)
        if status == "agreed":
            await open_or_update_main_menu(None, message, db, "✅ Вы уже согласились с правилами. Главное меню открыто.")
            return

        rules_text = (
            "👋 Добро пожаловать!\n\n"
            "📜 Правила:\n"
            "1. Нажмите «Согласиться», чтобы продолжить.\n"
            "2. Выберите один тип кубика (выбор фиксируется до попадания в игру).\n"
            "3. Бот просит ввести запрос перед каждым броском.\n"
            "4. Если выпало не 4 — бот попросит ввести другой запрос."
        )
        msg = await message.answer(rules_text, reply_markup=rules_keyboard())
        await db.set_last_rules_message_id(user.id, msg.message_id)

    @router.callback_query(F.data == "rules:agree")
    async def agree_rules(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        if callback.from_user is None or callback.message is None:
            return

        user_id = callback.from_user.id
        if await db.get_rules_status(user_id) == "agreed":
            await callback.answer("Выбор уже зафиксирован")
            return

        await db.set_rules_agreement(user_id, True)
        with suppress(TelegramBadRequest):
            await callback.message.edit_text("✅ Вы согласились с правилами. Главное меню открыто.")

        await open_or_update_main_menu(None, callback.message, db)
        await callback.answer("Сохранено")

    @router.callback_query(F.data == "rules:decline")
    async def decline_rules(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        if callback.from_user is None or callback.message is None:
            return

        user_id = callback.from_user.id
        if await db.get_rules_status(user_id) == "agreed":
            await callback.answer("После согласия изменить выбор нельзя", show_alert=True)
            return

        await db.set_rules_agreement(user_id, False)
        with suppress(TelegramBadRequest):
            await callback.message.edit_text("❌ Вы отказались от правил. Для повторного показа нажмите /start.")
        await callback.answer("Отказ сохранен")

    @router.callback_query(F.data == "menu:open")
    async def menu_open(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        if callback.message is None:
            return
        await open_or_update_main_menu(callback, callback.message, db)

    @router.callback_query(F.data.in_({"menu:choose_tg", "menu:choose_user"}))
    async def choose_mode(callback: CallbackQuery, state: FSMContext):
        if callback.from_user is None or callback.message is None:
            return

        user_id = callback.from_user.id
        if not await db.has_agreed(user_id):
            await callback.answer("Сначала примите правила через /start.", show_alert=True)
            return

        selected_mode = await db.get_selected_mode(user_id)
        requested_mode = "telegram" if callback.data == "menu:choose_tg" else "user"

        if selected_mode and selected_mode != requested_mode:
            await callback.answer(f"Вы уже выбрали режим: {mode_title(selected_mode)}", show_alert=True)
            return

        await db.set_selected_mode(user_id, requested_mode)
        await state.clear()

        if requested_mode == "telegram":
            await state.set_state(GameState.waiting_tg_request)
        else:
            await state.set_state(GameState.waiting_user_request)

        with suppress(TelegramBadRequest):
            await callback.message.edit_text(f"Режим: {mode_title(requested_mode)}.")

        await callback.message.answer("Введите ваш запрос:")
        await callback.answer("Режим выбран")

    @router.message(GameState.waiting_tg_request)
    async def handle_tg_request(message: Message, state: FSMContext):
        user = message.from_user
        if user is None:
            return

        request_text = (message.text or "").strip()
        await safe_delete_message(message)
        if not request_text:
            await message.answer("Введите текст запроса.")
            return

        await db.save_request(user.id, request_text)
        await state.set_state(GameState.waiting_tg_roll)
        await message.answer("Запрос принят. Нажмите кнопку, чтобы бросить кубик.", reply_markup=tg_roll_keyboard())

    @router.callback_query(F.data == "game:tg_roll")
    async def tg_roll(callback: CallbackQuery, state: FSMContext):
        if callback.from_user is None or callback.message is None:
            return

        user_id = callback.from_user.id
        current_state = await state.get_state()
        if current_state != GameState.waiting_tg_roll.state:
            await callback.answer("Сначала введите запрос.", show_alert=True)
            return

        request_text = await db.get_last_request(user_id)
        with suppress(TelegramBadRequest):
            await callback.message.edit_text(f"Ваш вопрос:\n{request_text or '—'}")

        await callback.answer()
        dice_message = await callback.message.answer_dice(emoji="🎲")
        await wait_for_dice_animation()

        dice_value = dice_message.dice.value if dice_message.dice else None
        if dice_value is None:
            await callback.message.answer("Не удалось получить значение кубика. Попробуйте еще раз.")
            return

        await db.save_telegram_roll(user_id, dice_value)

        if dice_value == 4:
            await db.set_passed(user_id, True)
            await state.clear()
            await callback.message.answer("🎉 Выпало 4! Вы попали в игру.")
            return

        await state.set_state(GameState.waiting_tg_request)
        await callback.message.answer("Попробуйте еще раз. Введите другой запрос:")

    @router.message(GameState.waiting_user_request)
    async def handle_user_request(message: Message, state: FSMContext):
        user = message.from_user
        if user is None:
            return

        request_text = (message.text or "").strip()
        await safe_delete_message(message)
        if not request_text:
            await message.answer("Введите текст запроса.")
            return

        await db.save_request(user.id, request_text)
        await state.set_state(GameState.waiting_user_roll)
        await message.answer("Теперь введите число с вашего кубика (1-6):")

    @router.message(GameState.waiting_user_roll)
    async def handle_user_roll(message: Message, state: FSMContext):
        user = message.from_user
        if user is None:
            return

        text = (message.text or "").strip()
        await safe_delete_message(message)

        if not text.isdigit() or not (1 <= int(text) <= 6):
            await message.answer("Введите число от 1 до 6.")
            return

        value = int(text)
        await db.save_user_roll(user.id, value)

        if value == 4:
            await db.set_passed(user.id, True)
            await state.clear()
            await message.answer("🎉 Выпало 4! Вы попали в игру.")
            return

        await state.set_state(GameState.waiting_user_request)
        await message.answer("Попробуйте еще раз. Введите другой запрос:")

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
