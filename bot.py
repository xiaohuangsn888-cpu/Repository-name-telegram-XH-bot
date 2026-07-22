import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import gspread
from google.oauth2.service_account import Credentials
from telegram import (
    BotCommand,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


# =========================================================
# CẤU HÌNH LOG
# =========================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# TELEGRAM VÀ RENDER
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
PORT = int(os.getenv("PORT", "10000"))

RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
RENDER_EXTERNAL_HOSTNAME = os.getenv(
    "RENDER_EXTERNAL_HOSTNAME",
    "",
).strip()

WEBHOOK_PATH = "telegram-webhook"

if RENDER_EXTERNAL_URL:
    BASE_URL = RENDER_EXTERNAL_URL
elif RENDER_EXTERNAL_HOSTNAME:
    BASE_URL = f"https://{RENDER_EXTERNAL_HOSTNAME}"
else:
    BASE_URL = ""

WEBHOOK_URL = f"{BASE_URL}/{WEBHOOK_PATH}" if BASE_URL else ""


# =========================================================
# GOOGLE SHEETS
# =========================================================

GOOGLE_CREDENTIALS_FILE = Path(
    "/etc/secrets/google-credentials.json"
)

SPREADSHEET_ID = (
    "1Z05WB8AOts_pjDC7D7bg28qKd6hEbeRG23AvP0RMFaI"
)

WORKSHEET_NAME = "Trang tính1"

ALLOWED_GROUP_ID = -1004440715006

TIME_ZONE = ZoneInfo("Asia/Manila")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


# =========================================================
# NÚT TELEGRAM
# =========================================================

BUTTON_CHECKIN = "上班/checkin"
BUTTON_CHECKOUT = "下班/checkout"
BUTTON_WC = "WC"
BUTTON_BREAK = "吃饭/break"
BUTTON_BACK = "回/back"
BUTTON_CHECK = "检查/check"

VALID_BUTTONS = {
    BUTTON_CHECKIN,
    BUTTON_CHECKOUT,
    BUTTON_WC,
    BUTTON_BREAK,
    BUTTON_BACK,
    BUTTON_CHECK,
}


def create_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [
            KeyboardButton(BUTTON_CHECKIN),
            KeyboardButton(BUTTON_CHECKOUT),
        ],
        [
            KeyboardButton(BUTTON_WC),
            KeyboardButton(BUTTON_BREAK),
        ],
        [
            KeyboardButton(BUTTON_BACK),
            KeyboardButton(BUTTON_CHECK),
        ],
    ]

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
        input_field_placeholder="Vui lòng chọn chức năng...",
    )


# =========================================================
# KẾT NỐI GOOGLE SHEETS
# =========================================================

def get_worksheet():
    """
    Mỗi lần cần ghi dữ liệu, bot sẽ kiểm tra và kết nối lại.
    Cách này giúp bot không bị tắt khi Google Sheets gặp lỗi.
    """

    if not GOOGLE_CREDENTIALS_FILE.exists():
        raise FileNotFoundError(
            "Không tìm thấy Secret File tại: "
            "/etc/secrets/google-credentials.json"
        )

    credentials = Credentials.from_service_account_file(
        str(GOOGLE_CREDENTIALS_FILE),
        scopes=SCOPES,
    )

    client = gspread.authorize(credentials)

    spreadsheet = client.open_by_key(
        SPREADSHEET_ID
    )

    worksheet = spreadsheet.worksheet(
        WORKSHEET_NAME
    )

    return worksheet


def write_row_to_sheet(row: list[str]) -> None:
    worksheet = get_worksheet()

    worksheet.append_row(
        row,
        value_input_option="USER_ENTERED",
    )


async def save_record(
    date_text: str,
    time_text: str,
    action: str,
    status: str,
    user_id: int,
    telegram_name: str,
    username: str,
) -> None:
    row = [
        date_text,
        time_text,
        action,
        status,
        str(user_id),
        telegram_name,
        username,
    ]

    await asyncio.to_thread(
        write_row_to_sheet,
        row,
    )


# =========================================================
# LỆNH /start VÀ /menu
# =========================================================

async def show_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message

    if message is None:
        return

    await message.reply_text(
        "Hệ thống đã sẵn sàng. Vui lòng chọn chức năng:",
        reply_markup=create_keyboard(),
    )


# =========================================================
# LỆNH /id
# =========================================================

async def show_chat_id(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    chat = update.effective_chat

    if message is None or chat is None:
        return

    await message.reply_text(
        (
            f"👥 Tên nhóm: {chat.title or 'Tin nhắn riêng'}\n"
            f"🆔 Group ID: {chat.id}"
        ),
        reply_markup=create_keyboard(),
    )


# =========================================================
# LỆNH /testsheet
# =========================================================

async def test_google_sheet(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message

    if message is None:
        return

    try:
        worksheet = await asyncio.to_thread(
            get_worksheet
        )

        await message.reply_text(
            (
                "✅ Kết nối Google Sheets thành công.\n"
                f"📄 Trang tính: {worksheet.title}"
            ),
            reply_markup=create_keyboard(),
        )

        logger.info(
            "Kiểm tra Google Sheets thành công: %s",
            worksheet.title,
        )

    except Exception as error:
        logger.exception(
            "Kiểm tra Google Sheets thất bại."
        )

        await message.reply_text(
            (
                "❌ Kết nối Google Sheets thất bại.\n"
                f"Lỗi: {type(error).__name__}\n"
                "Vui lòng kiểm tra Render Logs."
            ),
            reply_markup=create_keyboard(),
        )


# =========================================================
# XỬ LÝ CÁC NÚT
# =========================================================

async def handle_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if message is None or user is None or chat is None:
        return

    text = message.text.strip() if message.text else ""

    if text not in VALID_BUTTONS:
        return

    logger.info(
        "Nhận nút: %s | chat_id=%s | user_id=%s",
        text,
        chat.id,
        user.id,
    )

    if chat.id != ALLOWED_GROUP_ID:
        await message.reply_text(
            (
                "⚠️ Nhóm này chưa được cho phép.\n"
                f"Group ID hiện tại: {chat.id}"
            ),
            reply_markup=create_keyboard(),
        )
        return

    now = datetime.now(TIME_ZONE)

    date_text = now.strftime("%Y-%m-%d")
    time_text = now.strftime("%H:%M:%S")

    username = (
        f"@{user.username}"
        if user.username
        else ""
    )

    if text == BUTTON_CHECK:
        await message.reply_text(
            (
                "✅ BOT ĐANG HOẠT ĐỘNG\n\n"
                f"👤 {user.full_name}\n"
                f"📅 {date_text}\n"
                f"🕐 {time_text}\n"
                f"🆔 Group ID: {chat.id}"
            ),
            reply_markup=create_keyboard(),
        )
        return

    try:
        await save_record(
            date_text=date_text,
            time_text=time_text,
            action=text,
            status="Đã ghi nhận",
            user_id=user.id,
            telegram_name=user.full_name,
            username=username,
        )

        logger.info(
            "Đã ghi Google Sheets: %s | %s",
            user.full_name,
            text,
        )

    except Exception as error:
        logger.exception(
            "Không thể ghi dữ liệu vào Google Sheets."
        )

        await message.reply_text(
            (
                "❌ KHÔNG THỂ GHI GOOGLE SHEETS\n\n"
                f"Loại lỗi: {type(error).__name__}\n"
                "Vui lòng mở Render Logs để kiểm tra."
            ),
            reply_markup=create_keyboard(),
        )
        return

    await message.reply_text(
        (
            "✅ ĐÃ GHI NHẬN\n\n"
            f"👤 {user.full_name}\n"
            f"🔘 {text}\n"
            f"📅 {date_text}\n"
            f"🕐 {time_text}"
        ),
        reply_markup=create_keyboard(),
    )


# =========================================================
# KHỞI TẠO BOT
# =========================================================

async def post_init(
    application: Application,
) -> None:
    commands = [
        BotCommand("start", "Khởi động bot"),
        BotCommand("menu", "Hiển thị bàn phím"),
        BotCommand("id", "Xem Group ID"),
        BotCommand("testsheet", "Kiểm tra Google Sheets"),
    ]

    try:
        await application.bot.set_my_commands(
            commands
        )

        logger.info(
            "Đã cài danh sách lệnh Telegram."
        )

    except Exception:
        logger.exception(
            "Không thể cài danh sách lệnh Telegram."
        )

    # Chỉ kiểm tra file, không làm bot bị tắt
    if GOOGLE_CREDENTIALS_FILE.exists():
        logger.info(
            "Đã tìm thấy Secret File Google."
        )
    else:
        logger.error(
            "Không tìm thấy file: %s",
            GOOGLE_CREDENTIALS_FILE,
        )


async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    logger.exception(
        "Bot gặp lỗi chưa xử lý.",
        exc_info=context.error,
    )


# =========================================================
# CHẠY BOT
# =========================================================

def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN đang trống trong Render Environment."
        )

    if not WEBHOOK_URL:
        raise RuntimeError(
            "Không tìm thấy địa chỉ dịch vụ Render."
        )

    logger.info(
        "Khởi động bot trên port %s",
        PORT,
    )

    logger.info(
        "Webhook URL: %s",
        WEBHOOK_URL,
    )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            show_menu,
        )
    )

    application.add_handler(
        CommandHandler(
            "menu",
            show_menu,
        )
    )

    application.add_handler(
        CommandHandler(
            "id",
            show_chat_id,
        )
    )

    application.add_handler(
        CommandHandler(
            "testsheet",
            test_google_sheet,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_button,
        )
    )

    application.add_error_handler(
        error_handler
    )

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=WEBHOOK_PATH,
        webhook_url=WEBHOOK_URL,
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
        bootstrap_retries=3,
    )


if __name__ == "__main__":
    main()
