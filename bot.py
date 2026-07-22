import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

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
# CẤU HÌNH RENDER
# =========================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")

PORT = int(
    os.environ.get("PORT", "10000")
)

RENDER_EXTERNAL_HOSTNAME = os.environ.get(
    "RENDER_EXTERNAL_HOSTNAME"
)

WEBHOOK_PATH = "telegram-webhook"

# Giờ Philippines
TIME_ZONE = ZoneInfo("Asia/Manila")


# =========================================================
# TÊN CÁC NÚT
# =========================================================

BUTTON_CHECKIN = "上班/checkin"
BUTTON_CHECKOUT = "下班/checkout"
BUTTON_WC = "WC"
BUTTON_BREAK = "吃饭/break"
BUTTON_BACK = "回/back"
BUTTON_CHECK = "检查/check"


# =========================================================
# TẠO BÀN PHÍM GIỐNG ẢNH MẪU
# =========================================================

def create_keyboard() -> ReplyKeyboardMarkup:
    """
    Tạo bàn phím gồm 3 hàng, mỗi hàng 2 nút.

    resize_keyboard=True:
        Telegram tự điều chỉnh kích thước nút.

    one_time_keyboard=False:
        Không tự động ẩn bàn phím sau khi bấm.

    is_persistent=True:
        Yêu cầu Telegram luôn hiển thị bàn phím.
    """

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
# LỆNH /start VÀ /menu
# =========================================================

async def show_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Hiển thị bàn phím chức năng."""

    message = update.effective_message

    if message is None:
        return

    await message.reply_text(
        text=(
            "Hệ thống quản lý ca làm việc sẵn sàng. "
            "Vui lòng chọn chức năng:"
        ),
        reply_markup=create_keyboard(),
    )


# =========================================================
# LỆNH /id
# =========================================================

async def show_chat_id(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Hiển thị ID của nhóm Telegram."""

    message = update.effective_message
    chat = update.effective_chat

    if message is None or chat is None:
        return

    chat_name = chat.title or "Tin nhắn riêng"

    await message.reply_text(
        text=(
            f"👥 Tên nhóm: {chat_name}\n"
            f"🆔 Group ID: {chat.id}"
        ),
        reply_markup=create_keyboard(),
    )


# =========================================================
# XỬ LÝ CÁC NÚT
# =========================================================

async def handle_keyboard_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Xử lý nội dung khi người dùng bấm nút."""

    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if message is None or user is None or chat is None:
        return

    button_text = message.text.strip() if message.text else ""

    valid_buttons = {
        BUTTON_CHECKIN,
        BUTTON_CHECKOUT,
        BUTTON_WC,
        BUTTON_BREAK,
        BUTTON_BACK,
        BUTTON_CHECK,
    }

    # Không xử lý những tin nhắn bình thường ngoài các nút
    if button_text not in valid_buttons:
        return

    current_time = datetime.now(
        TIME_ZONE
    ).strftime("%Y-%m-%d %H:%M:%S")

    username = (
        f"@{user.username}"
        if user.username
        else "Không có username"
    )

    group_name = chat.title or "Tin nhắn riêng"
    group_id = chat.id

    # -----------------------------------------------------
    # NÚT KIỂM TRA
    # -----------------------------------------------------

    if button_text == BUTTON_CHECK:
        await message.reply_text(
            text=(
                "📋 THÔNG TIN KIỂM TRA\n\n"
                f"👤 Người kiểm tra: {user.full_name}\n"
                f"🔗 Username: {username}\n"
                f"👥 Nhóm: {group_name}\n"
                f"🆔 Group ID: {group_id}\n"
                f"🕐 Thời gian: {current_time}\n\n"
                "Google Sheets sẽ được kết nối "
                "ở bước tiếp theo."
            ),
            reply_markup=create_keyboard(),
        )
        return

    # -----------------------------------------------------
    # TÊN HÀNH ĐỘNG
    # -----------------------------------------------------

    action_names = {
        BUTTON_CHECKIN: "✅ Lên ca",
        BUTTON_CHECKOUT: "🏁 Xuống ca",
        BUTTON_WC: "🚻 Đi WC",
        BUTTON_BREAK: "🍚 Đi ăn",
        BUTTON_BACK: "↩️ Quay lại",
    }

    action_name = action_names.get(
        button_text,
        button_text,
    )

    await message.reply_text(
        text=(
            "✅ ĐÃ GHI NHẬN THAO TÁC\n\n"
            f"👤 Người thao tác: {user.full_name}\n"
            f"🔗 Username: {username}\n"
            f"🔘 Hành động: {action_name}\n"
            f"🕐 Thời gian: {current_time}\n"
            f"👥 Nhóm: {group_name}\n"
            f"🆔 Group ID: {group_id}"
        ),
        reply_markup=create_keyboard(),
    )


# =========================================================
# CÀI DANH SÁCH LỆNH
# =========================================================

async def post_init(
    application: Application,
) -> None:
    """Cài đặt các lệnh Telegram."""

    commands = [
        BotCommand(
            command="start",
            description="Khởi động bot",
        ),
        BotCommand(
            command="menu",
            description="Hiển thị bàn phím",
        ),
        BotCommand(
            command="id",
            description="Xem Group ID",
        ),
    ]

    await application.bot.set_my_commands(
        commands
    )

    logger.info(
        "Đã cài đặt danh sách lệnh."
    )


# =========================================================
# XỬ LÝ LỖI
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Ghi lỗi trong Render Logs."""

    logger.exception(
        "Bot gặp lỗi.",
        exc_info=context.error,
    )


# =========================================================
# KHỞI ĐỘNG BOT
# =========================================================

def main() -> None:
    """Khởi động bot bằng webhook trên Render."""

    if not BOT_TOKEN:
        raise RuntimeError(
            "Không tìm thấy BOT_TOKEN trong Render Environment."
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

    # Lệnh /start
    application.add_handler(
        CommandHandler(
            "start",
            show_menu,
        )
    )

    # Lệnh /menu
    application.add_handler(
        CommandHandler(
            "menu",
            show_menu,
        )
    )

    # Lệnh /id
    application.add_handler(
        CommandHandler(
            "id",
            show_chat_id,
        )
    )

    # Xử lý nội dung từ các nút Reply Keyboard
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_keyboard_button,
        )
    )

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "Bot đang khởi động."
    )

    logger.info(
        "Webhook URL: %s",
        webhook_url,
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
