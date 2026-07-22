import asyncio
import logging
import os
from datetime import datetime
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
# LOG
# =========================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# RENDER VÀ TELEGRAM
# =========================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", "10000"))

RENDER_EXTERNAL_HOSTNAME = os.environ.get(
    "RENDER_EXTERNAL_HOSTNAME"
)

WEBHOOK_PATH = "telegram-webhook"

# Giờ Philippines
TIME_ZONE = ZoneInfo("Asia/Manila")


# =========================================================
# GOOGLE SHEETS
# =========================================================

GOOGLE_CREDENTIALS_FILE = (
    "/etc/secrets/google-credentials.json"
)

SPREADSHEET_ID = (
    "1Z05WB8AOts_pjDC7D7bg28qKd6hEbeRG23AvP0RMFaI"
)

WORKSHEET_NAME = "Trang tính1"

# Chỉ nhóm này được ghi vào bảng
ALLOWED_GROUP_ID = -1004440715006

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def create_worksheet():
    """Kết nối Google Sheets và trả về trang cần ghi."""

    if not os.path.exists(GOOGLE_CREDENTIALS_FILE):
        raise FileNotFoundError(
            "Không tìm thấy file google-credentials.json "
            "trong Render Secret Files."
        )

    credentials = Credentials.from_service_account_file(
        GOOGLE_CREDENTIALS_FILE,
        scopes=SCOPES,
    )

    client = gspread.authorize(credentials)

    spreadsheet = client.open_by_key(
        SPREADSHEET_ID
    )

    return spreadsheet.worksheet(
        WORKSHEET_NAME
    )


# Kết nối một lần khi bot khởi động
worksheet = None


# =========================================================
# NÚT
# =========================================================

BUTTON_CHECKIN = "上班/checkin"
BUTTON_CHECKOUT = "下班/checkout"
BUTTON_WC = "WC"
BUTTON_BREAK = "吃饭/break"
BUTTON_BACK = "回/back"
BUTTON_CHECK = "检查/check"


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
        keyboard=keyboard,
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
        input_field_placeholder="Vui lòng chọn chức năng...",
    )


# =========================================================
# GHI GOOGLE SHEETS
# =========================================================

def append_to_sheet(row: list[str]) -> None:
    """Ghi một dòng mới vào Google Sheets."""

    if worksheet is None:
        raise RuntimeError(
            "Google Sheets chưa được kết nối."
        )

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
    """
    Chạy thao tác Google Sheets ở luồng riêng,
    tránh làm bot bị đứng khi Google phản hồi chậm.
    """

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
        append_to_sheet,
        row,
    )


# =========================================================
# /start VÀ /menu
# =========================================================

async def show_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message

    if message is None:
        return

    await message.reply_text(
        (
            "Hệ thống quản lý ca làm việc sẵn sàng. "
            "Vui lòng chọn chức năng:"
        ),
        reply_markup=create_keyboard(),
    )


# =========================================================
# /id
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
            f"👥 Tên nhóm: "
            f"{chat.title or 'Tin nhắn riêng'}\n"
            f"🆔 Group ID: {chat.id}"
        ),
        reply_markup=create_keyboard(),
    )


# =========================================================
# XỬ LÝ NÚT
# =========================================================

async def handle_keyboard_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if message is None or user is None or chat is None:
        return

    button_text = (
        message.text.strip()
        if message.text
        else ""
    )

    valid_buttons = {
        BUTTON_CHECKIN,
        BUTTON_CHECKOUT,
        BUTTON_WC,
        BUTTON_BREAK,
        BUTTON_BACK,
        BUTTON_CHECK,
    }

    if button_text not in valid_buttons:
        return

    # Chỉ cho nhóm đã cấu hình ghi dữ liệu
    if chat.id != ALLOWED_GROUP_ID:
        await message.reply_text(
            "⚠️ Nhóm này chưa được kết nối Google Sheets.",
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

    # Nút kiểm tra không ghi thêm một dòng thao tác
    if button_text == BUTTON_CHECK:
        await message.reply_text(
            (
                "📋 Hệ thống đang hoạt động.\n\n"
                f"👤 Người kiểm tra: {user.full_name}\n"
                f"🕐 Thời gian: {date_text} {time_text}\n"
                f"🆔 Group ID: {chat.id}"
            ),
            reply_markup=create_keyboard(),
        )
        return

    action_names = {
        BUTTON_CHECKIN: "上班/checkin",
        BUTTON_CHECKOUT: "下班/checkout",
        BUTTON_WC: "WC",
        BUTTON_BREAK: "吃饭/break",
        BUTTON_BACK: "回/back",
    }

    action = action_names[button_text]
    status = "Đã ghi nhận"

    try:
        await save_record(
            date_text=date_text,
            time_text=time_text,
            action=action,
            status=status,
            user_id=user.id,
            telegram_name=user.full_name,
            username=username,
        )

    except Exception:
        logger.exception(
            "Không thể ghi dữ liệu vào Google Sheets."
        )

        await message.reply_text(
            (
                "❌ Không thể ghi dữ liệu vào Google Sheets.\n"
                "Vui lòng báo quản trị viên kiểm tra Render Logs."
            ),
            reply_markup=create_keyboard(),
        )
        return

    await message.reply_text(
        (
            "✅ ĐÃ GHI NHẬN\n\n"
            f"👤 {user.full_name}\n"
            f"🔘 {action}\n"
            f"📅 {date_text}\n"
            f"🕐 {time_text}"
        ),
        reply_markup=create_keyboard(),
    )


# =========================================================
# KHỞI TẠO
# =========================================================

async def post_init(
    application: Application,
) -> None:
    global worksheet

    commands = [
        BotCommand("start", "Khởi động bot"),
        BotCommand("menu", "Hiển thị bàn phím"),
        BotCommand("id", "Xem Group ID"),
    ]

    await application.bot.set_my_commands(
        commands
    )

    # Kết nối Google Sheets ở luồng riêng
    worksheet = await asyncio.to_thread(
        create_worksheet
    )

    logger.info(
        "Đã kết nối Google Sheets: %s / %s",
        SPREADSHEET_ID,
        WORKSHEET_NAME,
    )


async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    logger.exception(
        "Bot gặp lỗi.",
        exc_info=context.error,
    )


# =========================================================
# CHẠY BOT
# =========================================================

def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError(
            "Không tìm thấy BOT_TOKEN."
        )

    if not RENDER_EXTERNAL_HOSTNAME:
        raise RuntimeError(
            "Không tìm thấy RENDER_EXTERNAL_HOSTNAME."
        )

    webhook_url = (
        f"https://{RENDER_EXTERNAL_HOSTNAME}/"
        f"{WEBHOOK_PATH}"
    )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(
        CommandHandler("start", show_menu)
    )

    application.add_handler(
        CommandHandler("menu", show_menu)
    )

    application.add_handler(
        CommandHandler("id", show_chat_id)
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_keyboard_button,
        )
    )

    application.add_error_handler(
        error_handler
    )

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=WEBHOOK_PATH,
        webhook_url=webhook_url,
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
