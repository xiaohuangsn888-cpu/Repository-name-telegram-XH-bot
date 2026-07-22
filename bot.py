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
# GHI NHẬT KÝ
# =========================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# CẤU HÌNH RENDER
# =========================================================

# Token được nhập trong Environment Variables của Render.
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# Render tự cấp cổng cho Web Service.
PORT = int(os.environ.get("PORT", "10000"))

# Render tự cấp hostname dạng:
# ten-dich-vu.onrender.com
RENDER_EXTERNAL_HOSTNAME = os.environ.get(
    "RENDER_EXTERNAL_HOSTNAME"
)

# Telegram sẽ gửi cập nhật vào đường dẫn này.
WEBHOOK_PATH = "telegram-webhook"

# Giờ Philippines.
TIME_ZONE = ZoneInfo("Asia/Manila")


# =========================================================
# TÊN CÁC HÀNH ĐỘNG
# =========================================================

ACTIONS = {
    "meal_start": "🍚 Đi ăn",
    "wc_start": "🚻 Đi WC",
    "return": "↩️ Đã quay lại",
    "check": "📋 Kiểm tra",
}


# =========================================================
# TẠO CÁC NÚT
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
    """Hiển thị các nút."""

    message = update.effective_message

    if message is None:
        return

    await message.reply_text(
        text="📌 Vui lòng chọn thao tác:",
        reply_markup=create_menu(),
    )


# =========================================================
# LỆNH /id
# =========================================================

async def show_chat_id(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Hiển thị ID nhóm Telegram."""

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
# XỬ LÝ NÚT
# =========================================================

async def handle_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Xử lý khi thành viên nhấn nút."""

    query = update.callback_query

    if query is None:
        return

    # Dừng biểu tượng tải trên nút Telegram.
    await query.answer()

    user = query.from_user
    chat = update.effective_chat

    action_code = query.data or ""
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

    if action_code == "check":
        result = (
            "📋 THÔNG TIN KIỂM TRA\n\n"
            f"👤 Người kiểm tra: {user.full_name}\n"
            f"🔗 Username: {username}\n"
            f"👥 Nhóm: {group_name}\n"
            f"🆔 Group ID: {group_id}\n"
            f"🕐 Thời gian: {current_time}\n\n"
            "ℹ️ Chức năng đọc Google Sheets "
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
# CÀI DANH SÁCH LỆNH
# =========================================================

async def post_init(application: Application) -> None:
    """Hiển thị các lệnh trong menu của Telegram."""

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

    logger.info("Đã cài danh sách lệnh Telegram.")


# =========================================================
# XỬ LÝ LỖI
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Hiển thị lỗi trong Render Logs."""

    logger.exception(
        "Bot gặp lỗi khi xử lý dữ liệu.",
        exc_info=context.error,
    )


# =========================================================
# KHỞI ĐỘNG BOT
# =========================================================

def main() -> None:
    """Khởi động bot bằng webhook."""

    if not BOT_TOKEN:
        raise RuntimeError(
            "Không tìm thấy BOT_TOKEN. "
            "Hãy thêm BOT_TOKEN trong Render Environment."
        )

    if not RENDER_EXTERNAL_HOSTNAME:
        raise RuntimeError(
            "Không tìm thấy RENDER_EXTERNAL_HOSTNAME. "
            "Hãy triển khai dưới dạng Render Web Service."
        )

    webhook_url = (
        f"https://{RENDER_EXTERNAL_HOSTNAME}/{WEBHOOK_PATH}"
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
        CallbackQueryHandler(
            handle_button,
        )
    )

    application.add_error_handler(error_handler)

    logger.info("Bot đang khởi động.")
    logger.info("Webhook: %s", webhook_url)
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
