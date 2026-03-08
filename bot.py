import asyncio
import logging
import os
from dataclasses import dataclass

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, KeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
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
    kb = ReplyKeyboardBuilder()
    kb.button(text="🎲 Бросить кубик Telegram")
    kb.button(text="✍️ Бросить свой кубик")
    kb.adjust(1)
    return kb.as_markup(resize_keyboard=True)


def build_router(db: Database) -> Router:
    router = Router()

    @router.message(CommandStart())
    async def cmd_start(message: Message, state: FSMContext):
        await state.clear()
        user = message.from_user
        await db.upsert_user(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
        )
        rules_text = (
            "👋 Добро пожаловать!\n\n"
            "📜 *Правила*:\n"
            "1. Нажмите «Согласиться», чтобы продолжить.\n"
            "2. В главном меню доступны 2 действия:\n"
            "   • «Бросить кубик Telegram» — бот отправит анимированный кубик.\n"
            "   • «Бросить свой кубик» — вы вводите число от 1 до 6.\n"
            "3. Если на кубике Telegram *или* на вашем кубике выпадает число *4*, бот пропускает вас дальше."
        )
        await message.answer(rules_text, parse_mode="Markdown", reply_markup=rules_keyboard())

    @router.callback_query(F.data == "rules:agree")
    async def agree_rules(callback: CallbackQuery):
        if callback.from_user is None:
            return
        await db.set_rules_agreement(callback.from_user.id, True)
        await callback.message.answer(
            "✅ Вы согласились с правилами. Главное меню открыто.",
            reply_markup=main_menu_keyboard(),
        )
        await callback.answer("Сохранено")

    @router.callback_query(F.data == "rules:decline")
    async def decline_rules(callback: CallbackQuery):
        if callback.from_user is None:
            return
        await db.set_rules_agreement(callback.from_user.id, False)
        await callback.message.answer(
            "❌ Вы отказались от правил. Для повторного показа нажмите /start."
        )
        await callback.answer("Отказ сохранен")

    @router.message(F.text == "🎲 Бросить кубик Telegram")
    async def roll_telegram_dice(message: Message):
        user = message.from_user
        if user is None:
            return

        is_agreed = await db.has_agreed(user.id)
        if not is_agreed:
            await message.answer("Сначала примите правила через /start.")
            return

        dice_message = await message.answer_dice(emoji="🎲")
        dice_value = dice_message.dice.value if dice_message.dice else None

        if dice_value is None:
            await message.answer("Не удалось получить значение кубика. Попробуйте еще раз.")
            return

        await db.save_telegram_roll(user.id, dice_value)

        if dice_value == 4:
            await db.set_passed(user.id, True)
            await message.answer("🎉 Выпало 4! Вы проходите дальше.")
        else:
            await message.answer(f"Выпало {dice_value}. Нужно 4, чтобы пройти дальше.")

    @router.message(F.text == "✍️ Бросить свой кубик")
    async def ask_user_roll(message: Message, state: FSMContext):
        user = message.from_user
        if user is None:
            return

        is_agreed = await db.has_agreed(user.id)
        if not is_agreed:
            await message.answer("Сначала примите правила через /start.")
            return

        await state.set_state(UserRollState.waiting_for_roll)
        await message.answer("Введите число от 1 до 6:")

    @router.message(UserRollState.waiting_for_roll)
    async def get_user_roll(message: Message, state: FSMContext):
        user = message.from_user
        if user is None:
            return

        text = (message.text or "").strip()
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
