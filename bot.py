import logging
import os
import re
from pathlib import Path
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


LOGGER = logging.getLogger("goalbet_bot")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID_VALUE = os.getenv("TELEGRAM_ADMIN_ID", "").strip()
try:
    ADMIN_ID: Optional[int] = int(ADMIN_ID_VALUE) if ADMIN_ID_VALUE else None
except ValueError:
    ADMIN_ID = None

EXPRESS_FILE = Path(__file__).parent / "data" / "express.txt"
BUTTON_LABEL = "🎯 ПОКАЗАТЬ ЭКСПРЕСС"
SHOW_CALLBACK = "show_express"
EXPRESS_HEADER = "🎯 GOALBET — ТЕКУЩИЙ ЭКСПРЕСС"
EMPTY_EXPRESS_TEXT = "Экспресс пока не опубликован."
MAX_POPUP_UTF16_UNITS = 200
MAX_TELEGRAM_TEXT_UTF16_UNITS = 4096
MAX_TELEGRAM_CAPTION_UTF16_UNITS = 1024
HIDDEN_EXPRESS_TEXT = "🎯 GOALBET — Нажмите кнопку, чтобы показать экспресс."


class SecretRedactionFilter(logging.Filter):
    """Keep the Telegram bot token out of log messages and tracebacks."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not BOT_TOKEN:
            return True

        message = record.getMessage()
        if BOT_TOKEN in message:
            record.msg = message.replace(BOT_TOKEN, "[REDACTED]")
            record.args = ()

        if record.exc_info:
            exception_text = logging.Formatter().formatException(record.exc_info)
            if BOT_TOKEN in exception_text:
                record.exc_info = None
                record.exc_text = "[redacted exception text containing a credential]"

        if record.exc_text and BOT_TOKEN in record.exc_text:
            record.exc_text = record.exc_text.replace(BOT_TOKEN, "[REDACTED]")
        return True


def get_express_text() -> str:
    """Read the latest picks, returning an empty string if none are set."""
    try:
        return EXPRESS_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def save_express_text(text: str) -> None:
    """Save the picks atomically so an interrupted write cannot corrupt them."""
    EXPRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = EXPRESS_FILE.with_suffix(".tmp")
    temporary_file.write_text(text.strip() + "\n", encoding="utf-8")
    temporary_file.replace(EXPRESS_FILE)


def format_popup_text(picks: str) -> str:
    if not picks:
        return f"{EXPRESS_HEADER}\n\n{EMPTY_EXPRESS_TEXT}"
    return f"{EXPRESS_HEADER}\n\n{picks}"


def utf16_code_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def popup_exceeds_limit(picks: str) -> bool:
    return utf16_code_units(format_popup_text(picks)) > MAX_POPUP_UTF16_UNITS


def parse_publish_command(message_text: str) -> tuple[Optional[str], str]:
    match = re.match(
        r"^\s*/publish(?:@\w+)?\s+(\S+)(.*)$",
        message_text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if match is None:
        return None, ""

    post_text = match.group(2)
    if post_text.startswith("\r\n"):
        post_text = post_text[2:]
    elif post_text.startswith("\n"):
        post_text = post_text[1:]
    else:
        post_text = post_text.lstrip(" \t")

    return match.group(1), post_text


def format_express_message() -> str:
    return format_popup_text(get_express_text())


def is_admin(update: Update) -> bool:
    user = update.effective_user
    return user is not None and ADMIN_ID is not None and user.id == ADMIN_ID


def admin_setup_message() -> str:
    return (
        "Редактирование пока не включено. Отправьте /myid, чтобы узнать свой "
        "Telegram ID, затем задайте его как TELEGRAM_ADMIN_ID."
    )


def show_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(BUTTON_LABEL, callback_data=SHOW_CALLBACK)]]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return

    await message.reply_text(
        "Добро пожаловать в GOALBET Express.\n"
        "Нажмите кнопку, чтобы получить актуальный экспресс.",
        reply_markup=show_keyboard(),
    )


async def show_express(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer(text=format_express_message(), show_alert=True)


async def express(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is not None:
        await message.reply_text(format_express_message())


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    if ADMIN_ID is None:
        status = "Администратор ещё не настроен."
    elif user.id == ADMIN_ID:
        status = "У вас есть доступ администратора."
    else:
        status = "Администратор настроен."
    await message.reply_text(f"Ваш Telegram ID: {user.id}\n{status}")


async def set_express(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    if not is_admin(update):
        await message.reply_text(
            admin_setup_message() if ADMIN_ID is None else "Нет доступа."
        )
        return

    match = re.match(
        r"^/setexpress(?:@\w+)?(?:\s+|$)(.*)$",
        message.text or "",
        flags=re.DOTALL | re.IGNORECASE,
    )
    new_text = match.group(1).strip() if match else ""
    if new_text:
        if popup_exceeds_limit(new_text):
            await message.reply_text(
                "Экспресс не помещается во всплывающее окно: максимум 200 единиц "
                "UTF-16 вместе с заголовком. Сократите текст и отправьте команду снова."
            )
            return
        save_express_text(new_text)
        await message.reply_text("Экспресс обновлён. Кнопка уже показывает новый текст.")
        return

    context.user_data["awaiting_express"] = True
    await message.reply_text(
        "Отправьте следующим сообщением новый текст экспресса. "
        "Чтобы отменить, отправьте /cancel."
    )


async def save_pending_express(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not context.user_data.get("awaiting_express"):
        return

    message = update.effective_message
    if message is None or not is_admin(update):
        return

    new_text = (message.text or "").strip()
    if popup_exceeds_limit(new_text):
        await message.reply_text(
            "Экспресс не помещается во всплывающее окно: максимум 200 единиц "
            "UTF-16 вместе с заголовком. Отправьте более короткий вариант."
        )
        return
    if not new_text:
        await message.reply_text("Текст пустой. Отправьте экспресс или /cancel.")
        return

    save_express_text(new_text)
    context.user_data.pop("awaiting_express", None)
    await message.reply_text("Экспресс обновлён. Кнопка уже показывает новый текст.")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    if context.user_data.pop("awaiting_express", None):
        await message.reply_text("Обновление отменено.")
    else:
        await message.reply_text("Нет ожидающего обновления.")


async def clear_express(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    if not is_admin(update):
        await message.reply_text(
            admin_setup_message() if ADMIN_ID is None else "Нет доступа."
        )
        return

    save_express_text("")
    await message.reply_text("Текущий экспресс очищен.")


async def publish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    if not is_admin(update):
        await message.reply_text(
            admin_setup_message() if ADMIN_ID is None else "Нет доступа."
        )
        return

    parsed_target, post_text = parse_publish_command(message.text or "")
    target = parsed_target or os.getenv("TELEGRAM_CHANNEL_ID", "").strip()
    if not target:
        await message.reply_text(
            "Укажите публичный канал: /publish @channel_username. "
            "Для приватного канала используйте его числовой ID."
        )
        return

    post_text = post_text if post_text.strip() else HIDDEN_EXPRESS_TEXT
    if utf16_code_units(post_text) > MAX_TELEGRAM_TEXT_UTF16_UNITS:
        await message.reply_text(
            "Текст поста превышает лимит Telegram — 4096 UTF-16 единиц. "
            "Сократите текст; пост не опубликован."
        )
        return

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(BUTTON_LABEL, callback_data=SHOW_CALLBACK)]]
    )
    try:
        await context.bot.send_message(
            chat_id=target,
            text=post_text,
            reply_markup=keyboard,
        )
    except TelegramError as exc:
        LOGGER.warning("Channel publishing failed (%s)", type(exc).__name__)
        await message.reply_text(
            "Не удалось опубликовать пост. Проверьте точный @username или ID канала "
            "и убедитесь, что бот добавлен администратором с правом публикации."
        )
        return

    await message.reply_text(f"Пост опубликован в {target}.")


async def publish_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or not message.photo:
        return

    target, caption = parse_publish_command(message.caption or "")
    if target is None:
        return
    if not is_admin(update):
        await message.reply_text(
            admin_setup_message() if ADMIN_ID is None else "Нет доступа."
        )
        return

    if utf16_code_units(caption) > MAX_TELEGRAM_CAPTION_UTF16_UNITS:
        await message.reply_text(
            "Подпись к фото превышает лимит Telegram — 1024 UTF-16 единицы. "
            "Сократите текст; фото не опубликовано."
        )
        return

    try:
        await context.bot.send_photo(
            chat_id=target,
            photo=message.photo[-1].file_id,
            caption=caption if caption.strip() else None,
            reply_markup=show_keyboard(),
            show_caption_above_media=True,
        )
    except TelegramError as exc:
        LOGGER.warning("Channel photo publishing failed (%s)", type(exc).__name__)
        await message.reply_text(
            "Не удалось опубликовать фото. Проверьте точный @username или ID канала "
            "и убедитесь, что бот добавлен администратором с правом публикации."
        )
        return

    await message.reply_text(f"Фото опубликовано в {target}.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return

    text = (
        "/start — открыть меню\n"
        "/express — показать текущий экспресс\n"
        "/myid — узнать свой Telegram ID\n"
        "/help — список команд"
    )
    if is_admin(update):
        text += (
            "\n\nКоманды администратора:\n"
            "/setexpress — заменить текст (командой или следующим сообщением)\n"
            "/clear — очистить текущий экспресс\n"
            "/publish @channel_username — опубликовать кнопку в канале"
        )
    await message.reply_text(text)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    if error is not None:
        LOGGER.error("Update handler failed (%s)", type(error).__name__)


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing. Add it in Replit Secrets before starting the bot."
        )
    if ADMIN_ID_VALUE and ADMIN_ID is None:
        raise RuntimeError("TELEGRAM_ADMIN_ID must be a numeric Telegram user ID.")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(SecretRedactionFilter())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("express", express))
    app.add_handler(CommandHandler("myid", my_id))
    app.add_handler(CommandHandler("setexpress", set_express))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("clear", clear_express))
    app.add_handler(CommandHandler("publish", publish))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(show_express, pattern=f"^{SHOW_CALLBACK}$"))
    app.add_handler(MessageHandler(filters.PHOTO, publish_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, save_pending_express))
    app.add_error_handler(error_handler)

    LOGGER.info("Starting GOALBET_ExpressBot polling")
    try:
        app.run_polling(drop_pending_updates=True)
    except Exception as exc:
        LOGGER.error("Bot stopped (%s); check the secret and Telegram connectivity.", type(exc).__name__)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
