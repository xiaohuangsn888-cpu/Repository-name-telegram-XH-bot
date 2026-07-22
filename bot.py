import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

# =========================================================
# CẤU HÌNH GHI LOG
# =========================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# BIẾN MÔI TRƯỜNG TRÊN RENDER
# =========================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")

# Render tự tạo PORT cho Web Service
PORT = int(os.environ.get("PORT", "10000"))

# Render tự tạo địa chỉ dạng:
# https://ten-dich-vu.onrender.com
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")

# Đường dẫn Telegram gửi dữ liệu về
WEBHOOK_PATH = "telegram-webhook"

# Múi giờ Philippines
TIME_ZONE = ZoneInfo("Asia/Manila")


# =========================================================
# DANH SÁCH HÀNH ĐỘNG
# =========================================================

ACTIONS = {
    "meal_start": "🍚 Đi ăn",
    "wc_start": "🚻 Đi WC",
    "return": "↩️ Đã quay lại",
    "check": "📋 Kiểm tra",
}


# =========================================================
# TẠO MENU NÚT
# =========================================================

def create_menu() -> InlineKeyboardMarkup:
    """Tạo bảng nút hiển thị dưới tin nhắn."""

    keyboard = [
        [
            InlineKeyboardButton(
                text="🍚 Đi ăn",
                callback_data="meal_start",
            ),
            InlineKeyboardButton(
                text="🚻 Đi WC",
                callback_data="wc_start",
            ),
        ],
        [
            InlineKeyboardButton(
                text="↩️ Quay lại",
                callback_data="return",
            ),
            InlineKeyboardButton(
                text="📋 Kiểm tra",
                callback_data="check",
            ),
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


# =========================================================
# LỆNH /start VÀ /menu
# =========================================================

async def show_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Hiển thị menu khi người dùng gửi /start hoặc /menu."""

    message = update.effective_message

    if message is None:
        return

    await message.reply_text(
        text=(
            "📌 Vui lòng chọn thao tác bên dưới:\n\n"
            "🍚 Đi ăn\n"
            "🚻 Đi WC\n"
            "↩️ Quay lại\n"
            "📋 Kiểm tra"
        ),
        reply_markup=create_menu(),
    )


# =========================================================
# LỆNH /id
# =========================================================

async def show_chat_id(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Hiển thị Group ID.
    Sau này dùng ID này để phân biệt nhóm nào ghi vào bảng nào.
    """

    message = update.effective_message
    chat = update.effective_chat

    if message is None or chat is None:
        return

    group_name = chat.title or "Tin nhắn riêng"

    await message.reply_text(
        text=(
            f"👥 Tên nhóm: {group_name}\n"
            f"🆔 Group ID: {chat.id}"
        )
    )


# =========================================================
# XỬ LÝ KHI NGƯỜI DÙNG NHẤN NÚT
# =========================================================

async def handle_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Xử lý thao tác nhấn nút."""

    query = update.callback_query

    if query is None:
        return

    # Dừng biểu tượng tải trên nút
    await query.answer()

    user = query.from_user
    chat = update.effective_chat

    action_code = query.data
    action_name = ACTIONS.get(
        action_code,
        "⚠️ Thao tác không xác định",
    )

    current_time = datetime.now(TIME_ZONE).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    username = (
        f"@{user.username}"
        if user.username
        else "Không có username"
    )

    if chat is not None:
        group_name = chat.title or "Tin nhắn riêng"
        group_id = chat.id
    else:
        group_name = "Không xác định"
        group_id = "Không xác định"

    # Nút kiểm tra
    if action_code == "check":
        result = (
            "📋 THÔNG TIN KIỂM TRA\n\n"
            f"👤 Người kiểm tra: {user.full_name}\n"
            f"🔗 Username: {username}\n"
            f"👥 Nhóm: {group_name}\n"
            f"🆔 Group ID: {group_id}\n"
            f"🕐 Thời gian: {current_time}\n\n"
            "ℹ️ Chức năng kiểm tra dữ liệu Google Sheets "
            "sẽ được kết nối ở bước tiếp theo."
        )
    else:
        result = (
            "✅ ĐÃ GHI NHẬN THAO TÁC\n\n"
            f"👤 Người thao tác: {user.full_name}\n"
            f"🔗 Username: {username}\n"
            f"🔘 Hành động: {action_name}\n"
            f"🕐 Thời gian: {current_time}\n"
            f"👥 Nhóm: {group_name}\n"
            f"🆔 Group ID: {group_id}"
        )

    if query.message is not None:
        await query.message.reply_text(
            text=result,
            reply_markup=create_menu(),
        )


# =========================================================
# CÀI ĐẶT DANH SÁCH LỆNH TELEGRAM
# =========================================================

async def post_init(application: Application) -> None:
    """Cài danh sách lệnh hiển thị trong menu Telegram."""

    commands = [
        BotCommand(
            command="start",
            description="Khởi động bot",
        ),
        BotCommand(
            command="menu",
            description="Hiển thị các nút",
        ),
        BotCommand(
            command="id",
            description="Xem Group ID",
        ),
    ]

    await application.bot.set_my_commands(commands)

    logger.info("Đã cài đặt danh sách lệnh Telegram.")


# =========================================================
# XỬ LÝ LỖI
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Ghi lỗi để kiểm tra trong Render Logs."""

    logger.exception(
        "Bot gặp lỗi khi xử lý dữ liệu.",
        exc_info=context.error,
    )


# =========================================================
# KHỞI ĐỘNG BOT
# =========================================================

def main() -> None:
    """Khởi động bot bằng webhook trên Render."""

    if not BOT_TOKEN:
        raise RuntimeError(
            "Không tìm thấy BOT_TOKEN. "
            "Hãy thêm BOT_TOKEN trong Environment Variables của Render."
        )

    if not RENDER_EXTERNAL_URL:
        raise RuntimeError(
            "Không tìm thấy RENDER_EXTERNAL_URL. "
            "Bot phải được tạo dưới dạng Render Web Service."
        )

    external_url = RENDER_EXTERNAL_URL.rstrip("/")

    webhook_url = (
        f"{external_url}/{WEBHOOK_PATH}"
    )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(
        CommandHandler(
            command="start",
            callback=show_menu,
        )
    )

    application.add_handler(
        CommandHandler(
            command="menu",
            callback=show_menu,
        )
    )

    application.add_handler(
        CommandHandler(
            command="id",
            callback=show_chat_id,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            callback=handle_button,
        )
    )

    application.add_error_handler(error_handler)

    logger.info("Bot đang khởi động...")
    logger.info("Webhook URL: %s", webhook_url)
    logger.info("Port: %s", PORT)

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
