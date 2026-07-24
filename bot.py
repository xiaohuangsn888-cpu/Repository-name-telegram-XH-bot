import asyncio
import html
import json
import logging
import os
import threading
from datetime import datetime, time, timedelta
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
# TELEGRAM / RENDER 设置
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
# 系统设置
# =========================================================

SPREADSHEET_ID = (
    "1Z05WB8AOts_pjDC7D7bg28qKd6hEbeRG23AvP0RMFaI"
)

WORKSHEET_NAME = "Trang tính1"

ALLOWED_GROUP_ID = -1004302603671
ADMIN_USER_ID = 6096917665

TIME_ZONE = ZoneInfo("Asia/Manila")

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SHEET_LOCK = threading.RLock()
WORKSHEET_CACHE = None


# =========================================================
# 班次设置
# =========================================================

DAY_SHIFT_START = time(8, 0)
DAY_SHIFT_END = time(20, 0)

NIGHT_SHIFT_START = time(20, 0)
NIGHT_SHIFT_END = time(8, 0)

# Thời gian bắt đầu được phép nhận diện ca
DAY_CHECKIN_START = time(7, 0)
NIGHT_CHECKIN_START = time(19, 0)


# =========================================================
# 按钮设置
# =========================================================

BUTTON_CHECKIN = "上班"
BUTTON_CHECKOUT = "下班"
BUTTON_TOILET = "去厕所"
BUTTON_MEAL = "吃饭"
BUTTON_RETURN = "返回"
BUTTON_CHECK = "检查"

BUTTON_ALIASES = {
    "上班/checkin": BUTTON_CHECKIN,
    "下班/checkout": BUTTON_CHECKOUT,
    "WC": BUTTON_TOILET,
    "吃饭/break": BUTTON_MEAL,
    "回/back": BUTTON_RETURN,
    "检查/check": BUTTON_CHECK,
}


# =========================================================
# 吃饭和厕所时间规定
# =========================================================

ACTIVITY_CONFIG = {
    BUTTON_TOILET: {
        "name": "去厕所",
        "return_action": "去厕所返回",
        "alert_action": "去厕所超时提醒",
        "limit_minutes": 10,
        "limit_seconds": 10 * 60,
    },
    BUTTON_MEAL: {
        "name": "吃饭",
        "return_action": "吃饭返回",
        "alert_action": "吃饭超时提醒",
        "limit_minutes": 30,
        "limit_seconds": 30 * 60,
    },
}


# =========================================================
# 当前状态
# =========================================================

# 已经上班的人员
ACTIVE_WORK_SESSIONS: dict[
    tuple[int, int],
    dict,
] = {}

# 正在吃饭或去厕所的人员
ACTIVE_ACTIVITY_SESSIONS: dict[
    tuple[int, int],
    dict,
] = {}


# Restore active state from Google Sheets after Render/bot restarts.
# A normal shift is 12 hours; 18 hours allows a reasonable checkout grace period.
MAX_WORK_SESSION_HOURS = 18
MAX_ACTIVITY_SESSION_HOURS = 18


# =========================================================
# 中文键盘
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
# GOOGLE 凭据
# =========================================================

def find_google_credentials_file() -> Path:
    candidate_paths: list[Path] = []

    custom_path = os.getenv(
        "GOOGLE_CREDENTIALS_FILE",
        "",
    ).strip()

    if custom_path:
        candidate_paths.append(Path(custom_path))

    candidate_paths.extend(
        [
            Path("/etc/secrets/google-credentials.json"),
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

    raise FileNotFoundError(
        "找不到 Google 凭据 JSON 文件。"
    )


def create_google_credentials() -> Credentials:
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
                "GOOGLE_CREDENTIALS_JSON 不是有效的 JSON。"
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
# GOOGLE SHEETS
# =========================================================

def get_worksheet():
    global WORKSHEET_CACHE

    with SHEET_LOCK:
        if WORKSHEET_CACHE is not None:
            return WORKSHEET_CACHE

        credentials = create_google_credentials()
        client = gspread.authorize(credentials)

        spreadsheet = client.open_by_key(
            SPREADSHEET_ID
        )

        WORKSHEET_CACHE = spreadsheet.worksheet(
            WORKSHEET_NAME
        )

        return WORKSHEET_CACHE


def append_sheet_row(row: list[str]) -> None:
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
# 从 GOOGLE SHEETS 恢复进行中的状态
# =========================================================

def parse_sheet_datetime(date_text: str, time_text: str) -> datetime | None:
    """Parse a Sheet date/time using the bot timezone."""
    value = f"{date_text.strip()} {time_text.strip()}"

    for date_format in (
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
    ):
        try:
            return datetime.strptime(
                value,
                date_format,
            ).replace(tzinfo=TIME_ZONE)
        except ValueError:
            continue

    return None


def build_work_session_from_row(
    chat_id: int,
    user_id: int,
    full_name: str,
    username: str,
    started_at: datetime,
) -> dict:
    shift = get_shift_info(started_at)

    return {
        "chat_id": chat_id,
        "user_id": user_id,
        "full_name": full_name,
        "username": username.lstrip("@"),
        "started_at": started_at,
        "shift_name": shift["name"],
        "shift_schedule": shift["schedule"],
        "shift_start": shift["start"],
        "shift_end": shift["end"],
        "restored": True,
    }


def build_activity_session_from_row(
    chat_id: int,
    user_id: int,
    full_name: str,
    username: str,
    activity: str,
    started_at: datetime,
) -> dict | None:
    config = ACTIVITY_CONFIG.get(activity)

    if config is None:
        return None

    return {
        "session_id": (
            f"restored-{chat_id}-{user_id}-"
            f"{int(started_at.timestamp())}"
        ),
        "chat_id": chat_id,
        "user_id": user_id,
        "full_name": full_name,
        "username": username.lstrip("@"),
        "activity": config["name"],
        "return_action": config["return_action"],
        "alert_action": config["alert_action"],
        "started_at": started_at,
        "limit_minutes": config["limit_minutes"],
        "limit_seconds": config["limit_seconds"],
        "alerted": False,
        "job": None,
        "restored": True,
    }


def reconstruct_active_sessions(
    rows: list[list[str]],
    now: datetime,
) -> tuple[dict, dict]:
    """
    Reconstruct open work/activity sessions from Sheet history.

    The Sheet remains the durable source of truth. This prevents state loss
    when Render restarts, redeploys, sleeps, or changes worker processes.
    """
    states: dict[int, dict] = {}

    for original_row in rows[1:]:
        row = original_row + [""] * (7 - len(original_row))
        record_time = parse_sheet_datetime(row[0], row[1])

        if record_time is None:
            continue

        try:
            user_id = int(row[4].strip())
        except (TypeError, ValueError):
            continue

        action = row[2].strip()
        full_name = row[5].strip() or str(user_id)
        username = row[6].strip()
        state = states.setdefault(
            user_id,
            {"work": None, "activity": None},
        )

        if action == BUTTON_CHECKIN:
            state["work"] = build_work_session_from_row(
                ALLOWED_GROUP_ID,
                user_id,
                full_name,
                username,
                record_time,
            )
            state["activity"] = None

        elif action == BUTTON_CHECKOUT:
            state["work"] = None
            state["activity"] = None

        elif action in ACTIVITY_CONFIG:
            if state["work"] is not None:
                state["activity"] = build_activity_session_from_row(
                    ALLOWED_GROUP_ID,
                    user_id,
                    full_name,
                    username,
                    action,
                    record_time,
                )

        elif action in {
            config["return_action"]
            for config in ACTIVITY_CONFIG.values()
        }:
            state["activity"] = None

        elif action in {
            config["alert_action"]
            for config in ACTIVITY_CONFIG.values()
        }:
            if state["activity"] is not None:
                state["activity"]["alerted"] = True

    work_sessions: dict[tuple[int, int], dict] = {}
    activity_sessions: dict[tuple[int, int], dict] = {}

    for user_id, state in states.items():
        work_session = state["work"]

        if work_session is None:
            continue

        work_age = (
            now - work_session["started_at"]
        ).total_seconds()

        if not (
            0 <= work_age <= MAX_WORK_SESSION_HOURS * 3600
        ):
            continue

        session_key = create_session_key(
            ALLOWED_GROUP_ID,
            user_id,
        )
        work_sessions[session_key] = work_session

        activity_session = state["activity"]

        if activity_session is None:
            continue

        activity_age = (
            now - activity_session["started_at"]
        ).total_seconds()

        if (
            0 <= activity_age
            <= MAX_ACTIVITY_SESSION_HOURS * 3600
        ):
            activity_sessions[session_key] = activity_session

    return work_sessions, activity_sessions


async def restore_active_sessions_from_sheet(
    application: Application,
    replace_existing: bool = False,
) -> None:
    """Restore active sessions and their timeout jobs from Google Sheets."""
    rows = await asyncio.to_thread(read_all_sheet_rows)
    now = datetime.now(TIME_ZONE)

    work_sessions, activity_sessions = reconstruct_active_sessions(
        rows,
        now,
    )

    if replace_existing:
        ACTIVE_WORK_SESSIONS.clear()
        ACTIVE_ACTIVITY_SESSIONS.clear()

    for session_key, session in work_sessions.items():
        ACTIVE_WORK_SESSIONS.setdefault(
            session_key,
            session,
        )

    for session_key, session in activity_sessions.items():
        if session_key in ACTIVE_ACTIVITY_SESSIONS:
            continue

        ACTIVE_ACTIVITY_SESSIONS[session_key] = session

        if session.get("alerted"):
            continue

        try:
            schedule_timeout_job(
                application,
                session,
            )
        except Exception:
            logger.exception(
                "恢复活动超时任务失败：%s",
                session_key,
            )

    logger.info(
        "状态恢复完成：上班中 %s 人，活动中 %s 人。",
        len(ACTIVE_WORK_SESSIONS),
        len(ACTIVE_ACTIVITY_SESSIONS),
    )


async def recover_user_state_if_missing(
    application: Application,
    chat_id: int,
    user_id: int,
) -> None:
    """On-demand recovery if a required in-memory state is missing."""
    # Always re-read the durable Sheet here. The caller invokes this only
    # when the specific state it needs is absent. A work session may still
    # exist in memory while its meal/toilet session was lost.
    try:
        await restore_active_sessions_from_sheet(
            application,
            replace_existing=False,
        )
    except Exception:
        logger.exception(
            "按需恢复用户状态失败：chat_id=%s user_id=%s",
            chat_id,
            user_id,
        )


# =========================================================
# 工具函数
# =========================================================

def create_session_key(
    chat_id: int,
    user_id: int,
) -> tuple[int, int]:
    return chat_id, user_id


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


def format_duration(
    total_seconds: float,
) -> str:
    seconds = max(
        0,
        int(total_seconds),
    )

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    remaining_seconds = seconds % 60

    parts = []

    if hours:
        parts.append(f"{hours}小时")

    if minutes:
        parts.append(f"{minutes}分钟")

    if remaining_seconds or not parts:
        parts.append(
            f"{remaining_seconds}秒"
        )

    return "".join(parts)


def get_shift_info(
    moment: datetime,
) -> dict:
    current_time = moment.time()

    # 07:00–18:59:59: tính là ca ngày 08:00–20:00
    if (
        DAY_CHECKIN_START
        <= current_time
        < NIGHT_CHECKIN_START
    ):
        shift_date = moment.date()

        shift_start = datetime.combine(
            shift_date,
            DAY_SHIFT_START,
            tzinfo=TIME_ZONE,
        )

        shift_end = datetime.combine(
            shift_date,
            DAY_SHIFT_END,
            tzinfo=TIME_ZONE,
        )

        return {
            "name": "白班",
            "start": shift_start,
            "end": shift_end,
            "schedule": "08:00-20:00",
        }

    # 19:00–23:59:59: ca đêm bắt đầu 20:00 cùng ngày
    if current_time >= NIGHT_CHECKIN_START:
        shift_start_date = moment.date()

    # 00:00–06:59:59: ca đêm bắt đầu 20:00 ngày hôm trước
    else:
        shift_start_date = (
            moment.date()
            - timedelta(days=1)
        )

    shift_start = datetime.combine(
        shift_start_date,
        NIGHT_SHIFT_START,
        tzinfo=TIME_ZONE,
    )

    shift_end = datetime.combine(
        shift_start_date + timedelta(days=1),
        NIGHT_SHIFT_END,
        tzinfo=TIME_ZONE,
    )

    return {
        "name": "夜班",
        "start": shift_start,
        "end": shift_end,
        "schedule": "20:00-08:00",
    }


def activity_is_overtime(
    session: dict,
    now: datetime,
) -> bool:
    elapsed_seconds = (
        now - session["started_at"]
    ).total_seconds()

    return (
        elapsed_seconds
        > session["limit_seconds"]
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

    session = ACTIVE_ACTIVITY_SESSIONS.get(
        session_key
    )

    # Render 重启后，内存状态会消失；从 Google Sheets 自动恢复。
    if session is None:
        await recover_user_state_if_missing(
            context.application,
            chat.id,
            user.id,
        )
        session = ACTIVE_ACTIVITY_SESSIONS.get(
            session_key
        )

    if session is None:
        return

    if session.get("session_id") != session_id:
        return

    if session.get("alerted"):
        return

    session["alerted"] = True
    session["job"] = None

    now = datetime.now(TIME_ZONE)

    elapsed_seconds = (
        now - session["started_at"]
    ).total_seconds()

    overtime_seconds = max(
        1,
        elapsed_seconds
        - session["limit_seconds"],
    )

    mention = create_user_mention(
        session["user_id"],
        session["full_name"],
    )

    warning_text = (
        "⚠️ <b>超时未返回提醒</b>\n\n"
        f"👤 员工：{mention}\n"
        f"📌 事项："
        f"{html.escape(session['activity'])}\n"
        f"🕐 离开时间："
        f"{session['started_at'].strftime('%H:%M:%S')}\n"
        f"⏱ 规定时间："
        f"{session['limit_minutes']}分钟\n"
        f"⌛ 当前用时："
        f"{format_duration(elapsed_seconds)}\n"
        f"❗ 已经超时："
        f"{format_duration(overtime_seconds)}\n\n"
        "该员工已经超过规定时间，"
        "目前仍然没有返回，请尽快返回。"
    )

    try:
        await context.bot.send_message(
            chat_id=session["chat_id"],
            text=warning_text,
            parse_mode=ParseMode.HTML,
            reply_markup=create_keyboard(),
        )

        await save_record(
            action=session["alert_action"],
            status=(
                "已超过规定时间，"
                "目前尚未返回"
            ),
            user_id=session["user_id"],
            full_name=session["full_name"],
            username=session["username"],
            record_time=now,
        )

    except Exception:
        logger.exception(
            "发送超时提醒失败。"
        )


def schedule_timeout_job(
    application: Application,
    session: dict,
) -> None:
    if application.job_queue is None:
        raise RuntimeError(
            "JobQueue 未启用，请检查 requirements.txt。"
        )

    now = datetime.now(TIME_ZONE)
    elapsed_seconds = max(
        0,
        (now - session["started_at"]).total_seconds(),
    )

    # New sessions wait for the full limit. Restored overdue sessions warn
    # almost immediately instead of restarting the timer from zero.
    remaining_seconds = (
        session["limit_seconds"] - elapsed_seconds
    )
    warning_delay = max(1, remaining_seconds + 1)

    job = application.job_queue.run_once(
        callback=timeout_warning_job,
        when=warning_delay,

        data={
            "chat_id": session["chat_id"],
            "user_id": session["user_id"],
            "session_id": session["session_id"],
        },
        name=(
            f"activity-timeout-"
            f"{session['session_id']}"
        ),
        chat_id=session["chat_id"],
        user_id=session["user_id"],
    )

    session["job"] = job


# =========================================================
# 上班
# =========================================================

async def check_in(
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

    session_key = create_session_key(
        chat.id,
        user.id,
    )

    if session_key in ACTIVE_WORK_SESSIONS:
        old_session = ACTIVE_WORK_SESSIONS[
            session_key
        ]

        await message.reply_text(
            (
                "⚠️ 您已经上班打卡，"
                "不能重复打卡。\n\n"
                f"班次：{old_session['shift_name']}\n"
                f"上班时间："
                f"{old_session['started_at'].strftime('%H:%M:%S')}"
            ),
            reply_markup=create_keyboard(),
        )
        return

    now = datetime.now(TIME_ZONE)
    shift = get_shift_info(now)

    status = (
        f"{shift['name']}上班，"
        f"班次时间{shift['schedule']}"
    )

    try:
        await save_record(
            action=BUTTON_CHECKIN,
            status=status,
            user_id=user.id,
            full_name=user.full_name,
            username=user.username or "",
            record_time=now,
        )

    except Exception as error:
        logger.exception(
            "写入上班记录失败。"
        )

        await message.reply_text(
            (
                "❌ 上班打卡失败。\n"
                f"错误类型："
                f"{type(error).__name__}"
            ),
            reply_markup=create_keyboard(),
        )
        return

    ACTIVE_WORK_SESSIONS[
        session_key
    ] = {
        "chat_id": chat.id,
        "user_id": user.id,
        "full_name": user.full_name,
        "username": user.username or "",
        "started_at": now,
        "shift_name": shift["name"],
        "shift_schedule": shift["schedule"],
        "shift_start": shift["start"],
        "shift_end": shift["end"],
    }

    mention = create_user_mention(
        user.id,
        user.full_name,
    )

    await message.reply_text(
        (
            "🟢 <b>上班打卡成功</b>\n\n"
            f"👤 员工：{mention}\n"
            f"📋 班次：{shift['name']}\n"
            f"⏰ 班次时间：{shift['schedule']}\n"
            f"🕐 打卡时间："
            f"{now.strftime('%H:%M:%S')}"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=create_keyboard(),
    )


# =========================================================
# 下班
# =========================================================

async def check_out(
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

    session_key = create_session_key(
        chat.id,
        user.id,
    )

    work_session = ACTIVE_WORK_SESSIONS.get(
        session_key
    )

    if work_session is None:
        await message.reply_text(
            "⚠️ 您还没有上班打卡，不能下班打卡。",
            reply_markup=create_keyboard(),
        )
        return

    if session_key in ACTIVE_ACTIVITY_SESSIONS:
        await message.reply_text(
            (
                "⚠️ 您目前还有未结束的"
                "吃饭或厕所记录。\n"
                "请先点击“返回”，然后再下班。"
            ),
            reply_markup=create_keyboard(),
        )
        return

    now = datetime.now(TIME_ZONE)

    worked_seconds = (
        now - work_session["started_at"]
    ).total_seconds()

    status = (
        f"{work_session['shift_name']}下班，"
        f"本次工作"
        f"{format_duration(worked_seconds)}"
    )

    try:
        await save_record(
            action=BUTTON_CHECKOUT,
            status=status,
            user_id=user.id,
            full_name=user.full_name,
            username=user.username or "",
            record_time=now,
        )

    except Exception as error:
        logger.exception(
            "写入下班记录失败。"
        )

        await message.reply_text(
            (
                "❌ 下班打卡失败。\n"
                f"错误类型："
                f"{type(error).__name__}"
            ),
            reply_markup=create_keyboard(),
        )
        return

    ACTIVE_WORK_SESSIONS.pop(
        session_key,
        None,
    )

    await message.reply_text(
        (
            "🔴 <b>下班打卡成功</b>\n\n"
            f"👤 员工："
            f"{html.escape(user.full_name)}\n"
            f"📋 班次："
            f"{work_session['shift_name']}\n"
            f"🕐 上班："
            f"{work_session['started_at'].strftime('%H:%M:%S')}\n"
            f"🕐 下班："
            f"{now.strftime('%H:%M:%S')}\n"
            f"⌛ 工作时间："
            f"{format_duration(worked_seconds)}"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=create_keyboard(),
    )


# =========================================================
# 开始吃饭或上厕所
# =========================================================

async def start_activity(
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

    # Render 重启后，先从 Google Sheets 恢复状态，再判断是否上班。
    if session_key not in ACTIVE_WORK_SESSIONS:
        await recover_user_state_if_missing(
            context.application,
            chat.id,
            user.id,
        )

    # 必须先上班
    if session_key not in ACTIVE_WORK_SESSIONS:
        await message.reply_text(
            (
                "⛔ 您还没有上班打卡。\n\n"
                "必须先点击“上班”，"
                "才可以吃饭或去厕所。"
            ),
            reply_markup=create_keyboard(),
        )
        return

    old_activity = (
        ACTIVE_ACTIVITY_SESSIONS.get(
            session_key
        )
    )

    if old_activity is not None:
        now = datetime.now(TIME_ZONE)

        elapsed_seconds = (
            now
            - old_activity["started_at"]
        ).total_seconds()

        await message.reply_text(
            (
                "⚠️ 您还有未结束的记录。\n\n"
                f"当前事项："
                f"{old_activity['activity']}\n"
                f"开始时间："
                f"{old_activity['started_at'].strftime('%H:%M:%S')}\n"
                f"当前用时："
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
        "session_id": (
            f"{chat.id}-"
            f"{user.id}-"
            f"{int(now.timestamp())}"
        ),
        "chat_id": chat.id,
        "user_id": user.id,
        "full_name": user.full_name,
        "username": user.username or "",
        "activity": config["name"],
        "return_action": config[
            "return_action"
        ],
        "alert_action": config[
            "alert_action"
        ],
        "started_at": now,
        "limit_minutes": config[
            "limit_minutes"
        ],
        "limit_seconds": config[
            "limit_seconds"
        ],
        "alerted": False,
        "job": None,
    }

    try:
        await save_record(
            action=config["name"],
            status=(
                "进行中，规定时间"
                f"{config['limit_minutes']}分钟"
            ),
            user_id=user.id,
            full_name=user.full_name,
            username=user.username or "",
            record_time=now,
        )

    except Exception as error:
        logger.exception(
            "写入活动开始记录失败。"
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

    ACTIVE_ACTIVITY_SESSIONS[
        session_key
    ] = session

    try:
        schedule_timeout_job(
            context.application,
            session,
        )

    except Exception as error:
        ACTIVE_ACTIVITY_SESSIONS.pop(
            session_key,
            None,
        )

        logger.exception(
            "创建超时任务失败。"
        )

        await message.reply_text(
            (
                "❌ 无法启动计时器。\n"
                f"错误类型："
                f"{type(error).__name__}"
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
            f"🕐 离开时间："
            f"{now.strftime('%H:%M:%S')}\n"
            f"⏱ 规定时间："
            f"{config['limit_minutes']}分钟\n"
            f"🔔 最晚返回："
            f"{deadline.strftime('%H:%M:%S')}\n\n"
            "回来后必须点击“返回”。"
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

    session = ACTIVE_ACTIVITY_SESSIONS.get(
        session_key
    )

    if session is None:
        await message.reply_text(
            (
                "⚠️ 您目前没有进行中的"
                "吃饭或厕所记录。"
            ),
            reply_markup=create_keyboard(),
        )
        return

    now = datetime.now(TIME_ZONE)

    elapsed_seconds = (
        now - session["started_at"]
    ).total_seconds()

    if elapsed_seconds <= session[
        "limit_seconds"
    ]:
        sheet_status = (
            "准时返回，"
            f"用时{format_duration(elapsed_seconds)}"
        )

        title = "✅ 准时返回"
        result_text = "在规定时间内返回。"

    else:
        overtime_seconds = (
            elapsed_seconds
            - session["limit_seconds"]
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
                f"{type(error).__name__}"
            ),
            reply_markup=create_keyboard(),
        )
        return

    job = session.get("job")

    if job is not None:
        try:
            job.schedule_removal()
        except Exception:
            logger.exception(
                "取消超时任务失败。"
            )

    ACTIVE_ACTIVITY_SESSIONS.pop(
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
            f"🕐 离开时间："
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
# 管理员检查
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

    if user.id != ADMIN_USER_ID:
        await message.reply_text(
            "⛔ 您没有权限使用“检查”功能。",
            reply_markup=create_keyboard(),
        )
        return

    now = datetime.now(TIME_ZONE)
    today_text = now.strftime("%Y-%m-%d")

    violations = []

    try:
        rows = await asyncio.to_thread(
            read_all_sheet_rows
        )

    except Exception as error:
        await message.reply_text(
            (
                "❌ 无法读取 Google Sheets。\n"
                f"错误类型："
                f"{type(error).__name__}"
            ),
            reply_markup=create_keyboard(),
        )
        return

    # 已经返回的超时记录
    for original_row in rows[1:]:
        row = original_row + [""] * (
            7 - len(original_row)
        )

        if row[0].strip() != today_text:
            continue

        status = row[3].strip()

        if "超时返回" not in status:
            continue

        violations.append(
            {
                "time": row[1].strip(),
                "action": row[2].strip(),
                "status": status,
                "name": row[5].strip(),
                "username": row[6].strip(),
            }
        )

    # 当前仍未返回并且已经超时
    for session in (
        ACTIVE_ACTIVITY_SESSIONS.values()
    ):
        if session["chat_id"] != chat.id:
            continue

        if not activity_is_overtime(
            session,
            now,
        ):
            continue

        elapsed_seconds = (
            now - session["started_at"]
        ).total_seconds()

        overtime_seconds = (
            elapsed_seconds
            - session["limit_seconds"]
        )

        violations.append(
            {
                "time": session[
                    "started_at"
                ].strftime("%H:%M:%S"),
                "action": session["activity"],
                "status": (
                    "尚未返回，"
                    f"已超时"
                    f"{format_duration(overtime_seconds)}"
                ),
                "name": session["full_name"],
                "username": (
                    f"@{session['username']}"
                    if session["username"]
                    else ""
                ),
            }
        )

    if not violations:
        await message.reply_text(
            (
                "✅ <b>今日违规检查结果</b>\n\n"
                f"📅 日期：{today_text}\n"
                "今天暂时没有超时记录。"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=create_keyboard(),
        )
        return

    lines = [
        "⚠️ <b>今日违规记录</b>",
        "",
        f"📅 日期：{today_text}",
        f"📋 违规次数：{len(violations)}次",
        "",
    ]

    for index, record in enumerate(
        violations,
        start=1,
    ):
        lines.extend(
            [
                (
                    f"<b>{index}. "
                    f"{html.escape(record['name'])}</b>"
                ),
                f"🕐 {html.escape(record['time'])}",
                f"📌 {html.escape(record['action'])}",
                (
                    "结果："
                    f"{html.escape(record['status'])}"
                ),
                "",
            ]
        )

    await message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=create_keyboard(),
    )


# =========================================================
# 菜单和测试
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
            "☀️ 白班：08:00-20:00（07:00起可打卡）\n"
            "🌙 夜班：20:00-08:00（19:00起可打卡）\n\n"
            "必须先点击“上班”，"
            "才可以吃饭或去厕所。\n\n"
            "吃饭：30分钟\n"
            "去厕所：10分钟"
        ),
        reply_markup=create_keyboard(),
    )


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
                f"{type(error).__name__}"
            ),
            reply_markup=create_keyboard(),
        )


# =========================================================
# 按钮处理
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

    if text == BUTTON_CHECKIN:
        await check_in(update)

    elif text == BUTTON_CHECKOUT:
        await check_out(update)

    elif text in ACTIVITY_CONFIG:
        await start_activity(
            update,
            context,
            text,
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


# =========================================================
# 启动设置
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

        await restore_active_sessions_from_sheet(
            application,
            replace_existing=True,
        )

    except Exception:
        logger.exception(
            "Google Sheets 初始连接失败，"
            "机器人继续运行。"
        )


async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    logger.error(
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
