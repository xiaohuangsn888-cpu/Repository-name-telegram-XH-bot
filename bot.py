import asyncio
import html
import json
import logging
import os
import threading
from datetime import datetime, timedelta
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
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


# =========================================================
# 日志设置
# =========================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# TELEGRAM 与 RENDER 设置
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
PORT = int(os.getenv("PORT", "10000"))

RENDER_EXTERNAL_URL = os.getenv(
    "RENDER_EXTERNAL_URL",
    "",
).strip().rstrip("/")

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

WEBHOOK_URL = (
    f"{BASE_URL}/{WEBHOOK_PATH}"
    if BASE_URL
    else ""
)


# =========================================================
# GOOGLE SHEETS 设置
# =========================================================

SPREADSHEET_ID = (
    "1Z05WB8AOts_pjDC7D7bg28qKd6hEbeRG23AvP0RMFaI"
)

WORKSHEET_NAME = "Trang tính1"

# 只允许这个群组使用
ALLOWED_GROUP_ID = -1004440715006

# 只有这个 Telegram User ID 可以使用“检查”
ADMIN_USER_ID = 6096917665

# 菲律宾时间
TIME_ZONE = ZoneInfo("Asia/Manila")

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SHEET_LOCK = threading.RLock()
WORKSHEET_CACHE = None


# =========================================================
# 中文按钮
# =========================================================

BUTTON_CHECKIN = "上班"
BUTTON_CHECKOUT = "下班"
BUTTON_TOILET = "去厕所"
BUTTON_MEAL = "吃饭"
BUTTON_RETURN = "返回"
BUTTON_CHECK = "检查"


# 兼容旧按钮
BUTTON_ALIASES = {
    "上班/checkin": BUTTON_CHECKIN,
    "下班/checkout": BUTTON_CHECKOUT,
    "WC": BUTTON_TOILET,
    "吃饭/break": BUTTON_MEAL,
    "回/back": BUTTON_RETURN,
    "检查/check": BUTTON_CHECK,
}


# =========================================================
# 时间限制
# =========================================================

ACTIVITY_CONFIG = {
    BUTTON_TOILET: {
        "name": "去厕所",
        "return_action": "去厕所返回",
        "limit_seconds": 10 * 60,
        "limit_minutes": 10,
    },
    BUTTON_MEAL: {
        "name": "吃饭",
        "return_action": "吃饭返回",
        "limit_seconds": 30 * 60,
        "limit_minutes": 30,
    },
}


# 当前正在吃饭或去厕所的人员
# 格式：(群组 ID, 用户 ID)
ACTIVE_SESSIONS: dict[tuple[int, int], dict] = {}


# =========================================================
# 创建键盘
# =========================================================

def create_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [
            KeyboardButton(BUTTON_CHECKIN),
            KeyboardButton(BUTTON_CHECKOUT),
        ],
        [
            KeyboardButton(BUTTON_TOILET),
            KeyboardButton(BUTTON_MEAL),
        ],
        [
            KeyboardButton(BUTTON_RETURN),
            KeyboardButton(BUTTON_CHECK),
        ],
    ]

    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
        input_field_placeholder="请选择功能……",
    )


# =========================================================
# GOOGLE 凭据文件
# =========================================================

def find_google_credentials_file() -> Path:
    candidate_paths = []

    custom_path = os.getenv(
        "GOOGLE_CREDENTIALS_FILE",
        "",
    ).strip()

    if custom_path:
        candidate_paths.append(
            Path(custom_path)
        )

    candidate_paths.extend(
        [
            Path(
                "/etc/secrets/"
                "google-credentials.json"
            ),
            Path(
                "/opt/render/project/src/"
                "google-credentials.json"
            ),
            Path("google-credentials.json"),
        ]
    )

    for file_path in candidate_paths:
        if file_path.is_file():
            logger.info(
                "已找到 Google 凭据文件：%s",
                file_path,
            )
            return file_path

    # 自动搜索 /etc/secrets 中的 JSON
    secret_directory = Path("/etc/secrets")

    if secret_directory.is_dir():
        json_files = sorted(
            secret_directory.glob("*.json")
        )

        if json_files:
            logger.info(
                "自动找到 Google JSON 文件：%s",
                json_files[0],
            )
            return json_files[0]

    checked_paths = ", ".join(
        str(path)
        for path in candidate_paths
    )

    raise FileNotFoundError(
        "找不到 Google 凭据 JSON 文件。"
        f"已检查：{checked_paths}"
    )


def create_google_credentials() -> Credentials:
    """
    支持两种方法：

    1. Render Secret File
    2. GOOGLE_CREDENTIALS_JSON 环境变量
    """

    credentials_json = os.getenv(
        "GOOGLE_CREDENTIALS_JSON",
        "",
    ).strip()

    if credentials_json:
        try:
            credentials_data = json.loads(
                credentials_json
            )
        except json.JSONDecodeError as error:
            raise ValueError(
                "GOOGLE_CREDENTIALS_JSON "
                "不是有效的 JSON。"
            ) from error

        return Credentials.from_service_account_info(
            credentials_data,
            scopes=GOOGLE_SCOPES,
        )

    credentials_file = (
        find_google_credentials_file()
    )

    return Credentials.from_service_account_file(
        str(credentials_file),
        scopes=GOOGLE_SCOPES,
    )


# =========================================================
# GOOGLE SHEETS 连接
# =========================================================

def get_worksheet():
    global WORKSHEET_CACHE

    with SHEET_LOCK:
        if WORKSHEET_CACHE is not None:
            return WORKSHEET_CACHE

        credentials = (
            create_google_credentials()
        )

        client = gspread.authorize(
            credentials
        )

        spreadsheet = client.open_by_key(
            SPREADSHEET_ID
        )

        WORKSHEET_CACHE = (
            spreadsheet.worksheet(
                WORKSHEET_NAME
            )
        )

        return WORKSHEET_CACHE


def append_sheet_row(
    row: list[str],
) -> None:
    global WORKSHEET_CACHE

    try:
        with SHEET_LOCK:
            worksheet = get_worksheet()

            worksheet.append_row(
                row,
                value_input_option="USER_ENTERED",
            )

    except Exception:
        with SHEET_LOCK:
            WORKSHEET_CACHE = None

        raise


def read_all_sheet_rows() -> list[list[str]]:
    global WORKSHEET_CACHE

    try:
        with SHEET_LOCK:
            worksheet = get_worksheet()
            return worksheet.get_all_values()

    except Exception:
        with SHEET_LOCK:
            WORKSHEET_CACHE = None

        raise


async def save_record(
    action: str,
    status: str,
    user_id: int,
    full_name: str,
    username: str,
    record_time: datetime | None = None,
) -> None:
    now = record_time or datetime.now(
        TIME_ZONE
    )

    username_text = (
        f"@{username}"
        if username
        else ""
    )

    row = [
        now.strftime("%Y-%m-%d"),
        now.strftime("%H:%M:%S"),
        action,
        status,
        str(user_id),
        full_name,
        username_text,
    ]

    await asyncio.to_thread(
        append_sheet_row,
        row,
    )


# =========================================================
# 工具函数
# =========================================================

def format_duration(
    total_seconds: float,
) -> str:
    seconds = max(
        0,
        int(total_seconds),
    )

    minutes = seconds // 60
    remaining_seconds = seconds % 60

    if minutes == 0:
        return f"{remaining_seconds}秒"

    if remaining_seconds == 0:
        return f"{minutes}分钟"

    return (
        f"{minutes}分钟"
        f"{remaining_seconds}秒"
    )


def create_user_mention(
    user_id: int,
    full_name: str,
) -> str:
    safe_name = html.escape(
        full_name or str(user_id)
    )

    return (
        f'<a href="tg://user?id={user_id}">'
        f"{safe_name}</a>"
    )


def create_session_key(
    chat_id: int,
    user_id: int,
) -> tuple[int, int]:
    return chat_id, user_id


def create_session_id(
    chat_id: int,
    user_id: int,
    started_at: datetime,
) -> str:
    return (
        f"{chat_id}-"
        f"{user_id}-"
        f"{int(started_at.timestamp())}"
    )


# =========================================================
# 超时提醒
# =========================================================

async def timeout_warning_job(
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    job = context.job

    if job is None:
        return

    data = job.data or {}

    chat_id = data.get("chat_id")
    user_id = data.get("user_id")
    session_id = data.get("session_id")

    if chat_id is None or user_id is None:
        return

    session_key = create_session_key(
        int(chat_id),
        int(user_id),
    )

    session = ACTIVE_SESSIONS.get(
        session_key
    )

    # 已经返回
    if session is None:
        return

    # 防止旧任务错误提醒
    if session.get("session_id") != session_id:
        return

    # 防止重复提醒
    if session.get("alerted"):
        return

    session["alerted"] = True
    session["job"] = None

    now = datetime.now(TIME_ZONE)

    elapsed_seconds = (
        now - session["started_at"]
    ).total_seconds()

    overtime_seconds = max(
        0,
        elapsed_seconds
        - session["limit_seconds"],
    )

    mention = create_user_mention(
        session["user_id"],
        session["full_name"],
    )

    warning_text = (
        "⚠️ <b>超时提醒</b>\n\n"
        f"👤 员工：{mention}\n"
        f"📌 事项："
        f"{html.escape(session['activity'])}\n"
        f"🕐 开始时间："
        f"{session['started_at'].strftime('%H:%M:%S')}\n"
        f"⏱ 规定时间："
        f"{session['limit_minutes']}分钟\n"
        f"⌛ 当前用时："
        f"{format_duration(elapsed_seconds)}\n"
        f"❗ 已超时："
        f"{format_duration(overtime_seconds)}\n\n"
        "请尽快返回并点击“返回”。"
    )

    try:
        await context.bot.send_message(
            chat_id=session["chat_id"],
            text=warning_text,
            parse_mode=ParseMode.HTML,
            reply_markup=create_keyboard(),
        )

        logger.warning(
            "员工超时：user_id=%s activity=%s",
            session["user_id"],
            session["activity"],
        )

    except Exception:
        logger.exception(
            "发送超时提醒失败。"
        )


def schedule_timeout_job(
    application: Application,
    session: dict,
    delay_seconds: float | None = None,
) -> None:
    if application.job_queue is None:
        raise RuntimeError(
            "JobQueue 未启用。"
            "请检查 requirements.txt。"
        )

    if delay_seconds is None:
        delay_seconds = (
            session["limit_seconds"] + 1
        )

    job = application.job_queue.run_once(
        callback=timeout_warning_job,
        when=max(1, delay_seconds),
        data={
            "chat_id": session["chat_id"],
            "user_id": session["user_id"],
            "session_id": session["session_id"],
        },
        name=(
            f"timeout-"
            f"{session['session_id']}"
        ),
        chat_id=session["chat_id"],
        user_id=session["user_id"],
    )

    session["job"] = job


# =========================================================
# 开始吃饭或去厕所
# =========================================================

async def start_timed_activity(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    activity: str,
) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if (
        message is None
        or user is None
        or chat is None
    ):
        return

    session_key = create_session_key(
        chat.id,
        user.id,
    )

    old_session = ACTIVE_SESSIONS.get(
        session_key
    )

    if old_session is not None:
        now = datetime.now(TIME_ZONE)

        elapsed_seconds = (
            now
            - old_session["started_at"]
        ).total_seconds()

        await message.reply_text(
            (
                "⚠️ 您还有未结束的记录。\n\n"
                f"📌 当前事项："
                f"{old_session['activity']}\n"
                f"🕐 开始时间："
                f"{old_session['started_at'].strftime('%H:%M:%S')}\n"
                f"⌛ 当前用时："
                f"{format_duration(elapsed_seconds)}\n\n"
                "请先点击“返回”。"
            ),
            reply_markup=create_keyboard(),
        )
        return

    config = ACTIVITY_CONFIG[
        activity
    ]

    now = datetime.now(TIME_ZONE)

    session = {
        "session_id": create_session_id(
            chat.id,
            user.id,
            now,
        ),
        "chat_id": chat.id,
        "user_id": user.id,
        "full_name": user.full_name,
        "username": user.username or "",
        "activity": config["name"],
        "return_action": config[
            "return_action"
        ],
        "started_at": now,
        "limit_seconds": config[
            "limit_seconds"
        ],
        "limit_minutes": config[
            "limit_minutes"
        ],
        "alerted": False,
        "job": None,
    }

    start_status = (
        "进行中，规定时间"
        f"{config['limit_minutes']}分钟"
    )

    try:
        await save_record(
            action=config["name"],
            status=start_status,
            user_id=user.id,
            full_name=user.full_name,
            username=user.username or "",
            record_time=now,
        )

    except Exception as error:
        logger.exception(
            "写入开始记录失败。"
        )

        await message.reply_text(
            (
                "❌ 无法写入 Google Sheets。\n"
                f"错误类型："
                f"{type(error).__name__}\n"
                "本次计时没有开始。"
            ),
            reply_markup=create_keyboard(),
        )
        return

    ACTIVE_SESSIONS[
        session_key
    ] = session

    try:
        schedule_timeout_job(
            context.application,
            session,
        )

    except Exception as error:
        ACTIVE_SESSIONS.pop(
            session_key,
            None,
        )

        logger.exception(
            "创建计时任务失败。"
        )

        await message.reply_text(
            (
                "❌ 无法启动计时任务。\n"
                f"错误类型："
                f"{type(error).__name__}\n"
                "请检查 requirements.txt。"
            ),
            reply_markup=create_keyboard(),
        )
        return

    deadline = now + timedelta(
        seconds=config["limit_seconds"]
    )

    mention = create_user_mention(
        user.id,
        user.full_name,
    )

    await message.reply_text(
        (
            "✅ <b>计时已经开始</b>\n\n"
            f"👤 员工：{mention}\n"
            f"📌 事项：{config['name']}\n"
            f"🕐 开始时间："
            f"{now.strftime('%H:%M:%S')}\n"
            f"⏱ 规定时间："
            f"{config['limit_minutes']}分钟\n"
            f"🔔 最晚返回："
            f"{deadline.strftime('%H:%M:%S')}\n\n"
            "回来后请点击“返回”。"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=create_keyboard(),
    )


# =========================================================
# 返回
# =========================================================

async def return_from_activity(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if (
        message is None
        or user is None
        or chat is None
    ):
        return

    session_key = create_session_key(
        chat.id,
        user.id,
    )

    session = ACTIVE_SESSIONS.get(
        session_key
    )

    if session is None:
        await message.reply_text(
            "⚠️ 您目前没有进行中的吃饭或厕所记录。",
            reply_markup=create_keyboard(),
        )
        return

    now = datetime.now(TIME_ZONE)

    elapsed_seconds = (
        now
        - session["started_at"]
    ).total_seconds()

    limit_seconds = session[
        "limit_seconds"
    ]

    if elapsed_seconds <= limit_seconds:
        sheet_status = (
            "准时返回，"
            f"用时{format_duration(elapsed_seconds)}"
        )

        title = "✅ 准时返回"

        result_text = (
            "在规定时间内返回。"
        )

    else:
        overtime_seconds = (
            elapsed_seconds
            - limit_seconds
        )

        sheet_status = (
            "超时返回，"
            f"超时{format_duration(overtime_seconds)}，"
            f"总用时{format_duration(elapsed_seconds)}"
        )

        title = "⚠️ 超时返回"

        result_text = (
            "已经超时"
            f"{format_duration(overtime_seconds)}。"
        )

    try:
        await save_record(
            action=session["return_action"],
            status=sheet_status,
            user_id=user.id,
            full_name=user.full_name,
            username=user.username or "",
            record_time=now,
        )

    except Exception as error:
        logger.exception(
            "写入返回记录失败。"
        )

        await message.reply_text(
            (
                "❌ 无法写入返回记录。\n"
                f"错误类型："
                f"{type(error).__name__}\n"
                "请稍后再次点击“返回”。"
            ),
            reply_markup=create_keyboard(),
        )
        return

    # 写入成功后取消超时任务
    job = session.get("job")

    if job is not None:
        try:
            job.schedule_removal()
        except Exception:
            logger.exception(
                "取消超时任务失败。"
            )

    ACTIVE_SESSIONS.pop(
        session_key,
        None,
    )

    mention = create_user_mention(
        user.id,
        user.full_name,
    )

    await message.reply_text(
        (
            f"<b>{title}</b>\n\n"
            f"👤 员工：{mention}\n"
            f"📌 事项："
            f"{html.escape(session['activity'])}\n"
            f"🕐 开始时间："
            f"{session['started_at'].strftime('%H:%M:%S')}\n"
            f"🕐 返回时间："
            f"{now.strftime('%H:%M:%S')}\n"
            f"⌛ 总用时："
            f"{format_duration(elapsed_seconds)}\n"
            f"📋 结果：{result_text}"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=create_keyboard(),
    )


# =========================================================
# 上班与下班
# =========================================================

async def record_work_action(
    update: Update,
    action: str,
) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if (
        message is None
        or user is None
        or chat is None
    ):
        return

    session_key = create_session_key(
        chat.id,
        user.id,
    )

    if session_key in ACTIVE_SESSIONS:
        await message.reply_text(
            (
                "⚠️ 您还有未结束的吃饭或厕所记录。\n"
                "请先点击“返回”。"
            ),
            reply_markup=create_keyboard(),
        )
        return

    now = datetime.now(TIME_ZONE)

    try:
        await save_record(
            action=action,
            status="已记录",
            user_id=user.id,
            full_name=user.full_name,
            username=user.username or "",
            record_time=now,
        )

    except Exception as error:
        logger.exception(
            "写入上下班记录失败。"
        )

        await message.reply_text(
            (
                "❌ 无法写入 Google Sheets。\n"
                f"错误类型："
                f"{type(error).__name__}"
            ),
            reply_markup=create_keyboard(),
        )
        return

    icon = (
        "🟢"
        if action == BUTTON_CHECKIN
        else "🔴"
    )

    await message.reply_text(
        (
            f"{icon} <b>{action}记录成功</b>\n\n"
            f"👤 员工："
            f"{html.escape(user.full_name)}\n"
            f"📅 日期："
            f"{now.strftime('%Y-%m-%d')}\n"
            f"🕐 时间："
            f"{now.strftime('%H:%M:%S')}"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=create_keyboard(),
    )


# =========================================================
# 管理员检查今日违规
# =========================================================

async def check_today_violations(
    update: Update,
) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if (
        message is None
        or user is None
        or chat is None
    ):
        return

    # 只有管理员可以查看
    if user.id != ADMIN_USER_ID:
        await message.reply_text(
            "⛔ 您没有权限使用“检查”功能。",
            reply_markup=create_keyboard(),
        )
        return

    now = datetime.now(TIME_ZONE)
    today_text = now.strftime("%Y-%m-%d")

    violations: dict[str, dict] = {}

    try:
        rows = await asyncio.to_thread(
            read_all_sheet_rows
        )

    except Exception as error:
        logger.exception(
            "读取违规记录失败。"
        )

        await message.reply_text(
            (
                "❌ 无法读取 Google Sheets。\n"
                f"错误类型："
                f"{type(error).__name__}"
            ),
            reply_markup=create_keyboard(),
        )
        return

    # 检查已经返回并记录为超时的人员
    for original_row in rows[1:]:
        row = original_row + [""] * (
            7 - len(original_row)
        )

        date_text = row[0].strip()
        time_text = row[1].strip()
        action = row[2].strip()
        status = row[3].strip()
        user_id_text = row[4].strip()
        full_name = row[5].strip()
        username = row[6].strip()

        if date_text != today_text:
            continue

        if "超时返回" not in status:
            continue

        if not user_id_text:
            user_id_text = (
                full_name or "未知用户"
            )

        if user_id_text not in violations:
            violations[user_id_text] = {
                "full_name": (
                    full_name
                    or user_id_text
                ),
                "username": username,
                "records": [],
            }

        violations[user_id_text][
            "records"
        ].append(
            {
                "time": time_text,
                "action": action,
                "status": status,
                "active": False,
            }
        )

    # 检查目前尚未返回并已超时的人员
    for session in ACTIVE_SESSIONS.values():
        if session["chat_id"] != chat.id:
            continue

        if (
            session["started_at"].strftime(
                "%Y-%m-%d"
            )
            != today_text
        ):
            continue

        elapsed_seconds = (
            now
            - session["started_at"]
        ).total_seconds()

        if (
            elapsed_seconds
            <= session["limit_seconds"]
        ):
            continue

        overtime_seconds = (
            elapsed_seconds
            - session["limit_seconds"]
        )

        user_id_text = str(
            session["user_id"]
        )

        if user_id_text not in violations:
            username = (
                f"@{session['username']}"
                if session["username"]
                else ""
            )

            violations[user_id_text] = {
                "full_name": session[
                    "full_name"
                ],
                "username": username,
                "records": [],
            }

        violations[user_id_text][
            "records"
        ].append(
            {
                "time": session[
                    "started_at"
                ].strftime("%H:%M:%S"),
                "action": session[
                    "activity"
                ],
                "status": (
                    "尚未返回，"
                    f"已超时"
                    f"{format_duration(overtime_seconds)}"
                ),
                "active": True,
            }
        )

    if not violations:
        await message.reply_text(
            (
                "✅ <b>今日违规检查结果</b>\n\n"
                f"📅 日期：{today_text}\n"
                "今天暂时没有人员超时。"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=create_keyboard(),
        )
        return

    total_people = len(violations)

    total_records = sum(
        len(item["records"])
        for item in violations.values()
    )

    lines = [
        "⚠️ <b>今日违规人员</b>",
        "",
        f"📅 日期：{today_text}",
        f"👥 违规人数：{total_people}人",
        f"📋 违规次数：{total_records}次",
        "",
    ]

    number = 1

    for violation in violations.values():
        safe_name = html.escape(
            violation["full_name"]
        )

        safe_username = html.escape(
            violation["username"]
        )

        lines.append(
            f"<b>{number}. {safe_name}</b>"
        )

        if safe_username:
            lines.append(
                f"账号：{safe_username}"
            )

        for record in violation["records"]:
            icon = (
                "🔴"
                if record["active"]
                else "🟠"
            )

            safe_action = html.escape(
                record["action"]
            )

            safe_status = html.escape(
                record["status"]
            )

            lines.append(
                (
                    f"{icon} {record['time']}｜"
                    f"{safe_action}\n"
                    f"结果：{safe_status}"
                )
            )

        lines.append("")
        number += 1

    await message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=create_keyboard(),
    )


# =========================================================
# /start 与 /menu
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
            "✅ 考勤系统已经启动。\n\n"
            "请选择下面的功能："
        ),
        reply_markup=create_keyboard(),
    )


# =========================================================
# /id
# =========================================================

async def show_group_id(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    chat = update.effective_chat

    if message is None or chat is None:
        return

    await message.reply_text(
        (
            f"👥 群组名称："
            f"{chat.title or '私人聊天'}\n"
            f"🆔 群组 ID：{chat.id}"
        ),
        reply_markup=create_keyboard(),
    )


# =========================================================
# /myid
# =========================================================

async def show_my_user_id(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    user = update.effective_user

    if message is None or user is None:
        return

    await message.reply_text(
        (
            "🆔 您的 Telegram User ID：\n"
            f"<code>{user.id}</code>"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=create_keyboard(),
    )


# =========================================================
# /testsheet
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
                "✅ Google Sheets 连接成功。\n"
                f"📄 工作表：{worksheet.title}"
            ),
            reply_markup=create_keyboard(),
        )

    except Exception as error:
        logger.exception(
            "Google Sheets 连接失败。"
        )

        await message.reply_text(
            (
                "❌ Google Sheets 连接失败。\n"
                f"错误类型："
                f"{type(error).__name__}\n"
                "请检查 Render 日志和 Secret File。"
            ),
            reply_markup=create_keyboard(),
        )


# =========================================================
# 处理按钮
# =========================================================

async def handle_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    chat = update.effective_chat

    if message is None or chat is None:
        return

    original_text = (
        message.text.strip()
        if message.text
        else ""
    )

    text = BUTTON_ALIASES.get(
        original_text,
        original_text,
    )

    valid_buttons = {
        BUTTON_CHECKIN,
        BUTTON_CHECKOUT,
        BUTTON_TOILET,
        BUTTON_MEAL,
        BUTTON_RETURN,
        BUTTON_CHECK,
    }

    if text not in valid_buttons:
        return

    if chat.id != ALLOWED_GROUP_ID:
        await message.reply_text(
            (
                "⚠️ 此群组尚未连接系统。\n"
                f"当前群组 ID：{chat.id}"
            ),
            reply_markup=create_keyboard(),
        )
        return

    if text == BUTTON_MEAL:
        await start_timed_activity(
            update,
            context,
            BUTTON_MEAL,
        )

    elif text == BUTTON_TOILET:
        await start_timed_activity(
            update,
            context,
            BUTTON_TOILET,
        )

    elif text == BUTTON_RETURN:
        await return_from_activity(
            update,
            context,
        )

    elif text == BUTTON_CHECK:
        await check_today_violations(
            update
        )

    elif text in {
        BUTTON_CHECKIN,
        BUTTON_CHECKOUT,
    }:
        await record_work_action(
            update,
            text,
        )


# =========================================================
# 启动后的设置
# =========================================================

async def post_init(
    application: Application,
) -> None:
    commands = [
        BotCommand(
            "start",
            "启动机器人",
        ),
        BotCommand(
            "menu",
            "显示功能按钮",
        ),
        BotCommand(
            "id",
            "查看群组 ID",
        ),
        BotCommand(
            "myid",
            "查看自己的 User ID",
        ),
        BotCommand(
            "testsheet",
            "测试 Google Sheets",
        ),
    ]

    try:
        await application.bot.set_my_commands(
            commands
        )

        logger.info(
            "Telegram 命令设置成功。"
        )

    except Exception:
        logger.exception(
            "Telegram 命令设置失败。"
        )

    try:
        worksheet = await asyncio.to_thread(
            get_worksheet
        )

        logger.info(
            "Google Sheets 连接成功：%s",
            worksheet.title,
        )

    except Exception:
        logger.exception(
            "Google Sheets 初始连接失败，"
            "机器人继续运行。"
        )


# =========================================================
# 全局错误处理
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    logger.exception(
        "机器人发生未处理错误。",
        exc_info=context.error,
    )


# =========================================================
# 启动机器人
# =========================================================

def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError(
            "Render 中的 BOT_TOKEN 为空。"
        )

    if not WEBHOOK_URL:
        raise RuntimeError(
            "找不到 Render 外部网址。"
        )

    logger.info(
        "机器人正在启动，端口：%s",
        PORT,
    )

    logger.info(
        "Webhook 地址：%s",
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
            show_group_id,
        )
    )

    application.add_handler(
        CommandHandler(
            "myid",
            show_my_user_id,
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
    )


if __name__ == "__main__":
    main()
