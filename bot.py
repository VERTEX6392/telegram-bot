import re
import threading
import asyncio
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
from dotenv import load_dotenv
from scraper import fetch_daily, fetch_daily_offline, fetch_weekly, fetch_eap_list, fetch_total
from pdf_creator import get_daily_result_pdf, get_daily_offline_result_pdf, get_weekly_result_pdf

load_dotenv()

VALID_DAILY_SUBJECTS = ["p", "c", "m"]

MY_TELEGRAM_ID = 1607298724

DISABLED_STUDENTS = set()

GROUP_CHAT_ID = -1003803230318
SIGNAL_SECRET = os.getenv("SIGNAL_SECRET")
SIGNAL_PORT   = int(os.getenv("SIGNAL_PORT", 5000))

FIXED_REPLIES = {
    "ovrar ki kora uchit?": "porashuna kora",
    "shiropa onek cute": "hard agree",
    "sayaner ki kora uchit?": "dhumay haat mara",
    "ankaner ki kora uchit?": "aro koyta magi dhora",
    "reshader ki kora uchit?": "aro handsome howa",
    "shirshar ki kora uchit?": "ekta proper reality check khawa",
    "shiropar ki kora uchit?": "Weight loss.",
    "tomar ki kora uchit?": "tomader number dekhe hasha",
    "gali de": "bainchod kuttachoda besshamagi nodirput halarbhai khankirpola lewrachoda gushkirpola dhemnamagi chutmarani madarchod aluchoda potolchoda ut-khankir-dim condomchoda dinosaurchoda",
    "jore gali de":"BAINCHOD  KUTTACHODA  BESSHAMAGI  NODIRPUT  HALARBHAI  KHANKIRPOLA  LEWRACHODA  GUSHKIRPOLA  DHEMNAMAGI  CHUTMARANI  MADARCHOD  ALUCHODA  POTOLCHODA  UT-KHANKIR-DIM  CONDOMCHODA  DINOSAURCHODA",
    "love you": "*blushes cutely*",
    "goodnight": "Goodnight soldier. Stay strong rest well.",
    "tumi ki shohomot?": "100% shohomot",
    "lb": "lb",
    "sieg": "heil",
    "porte bosho": "porte bhalo lage na",
    "gg": "wp",
}

# ── Shared state ──────────────────────────────────────────────────────────────

_telegram_app = None
_bot_loop     = None


# ── Signal HTTP server ────────────────────────────────────────────────────────

class SignalHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def do_POST(self):
        if self.path != "/signal":
            self._respond(404, "Not found")
            return

        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._respond(400, "Invalid JSON")
            return

        if data.get("secret") != SIGNAL_SECRET:
            self._respond(403, "Forbidden")
            return

        user_id = data.get("user_id", "").strip().lower()
        signal  = data.get("signal", "").strip().lower()

        if not user_id or signal not in ("on", "off"):
            self._respond(400, "Missing or invalid user_id / signal")
            return

        users_path = os.path.join(os.path.dirname(__file__), "users.json")
        try:
            with open(users_path) as f:
                users = json.load(f)
        except Exception as e:
            print(f"[Signal Server] Could not load users.json: {e}", flush=True)
            self._respond(500, "Could not load users.json")
            return

        nickname = users.get(user_id)
        if not nickname:
            self._respond(404, f"Unknown user_id: {user_id}")
            return

        # signal 'off' (website opened) -> disable results
        # signal 'on'  (website closed)  -> enable results
        if signal == 'off':
            DISABLED_STUDENTS.add(nickname)
            action = 'disabled'
        else:
            DISABLED_STUDENTS.discard(nickname)
            action = 'enabled'

        print(f'[Signal Server] Results {action} for {nickname}', flush=True)
        self._respond(200, f'OK: results {action} for {nickname}')

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _respond(self, code, text):
        self.send_response(code)
        self._cors_headers()
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(text.encode())


def run_signal_server():
    server = HTTPServer(("0.0.0.0", SIGNAL_PORT), SignalHandler)
    print(f"[Signal Server] Listening on port {SIGNAL_PORT}", flush=True)
    server.serve_forever()


# ── Telegram bot logic ────────────────────────────────────────────────────────

def _parse_flags(flags):
    return {
        "show_cq":      "-cq"     in flags,
        "show_mcq":     "-mcq"    in flags,
        "show_marks":   "-marks"  in flags,
        "show_branch":  "-branch" in flags,
        "show_central": "-merit"  in flags or "-central" in flags,
        "show_result":  "pdf"     in flags,
    }


def parse_message(text):
    text = text.strip().lower()

    if text.startswith("/ubot"):
        text = text[len("/ubot"):].strip()
    if text.startswith("@"):
        text = text.split(" ", 1)[-1].strip() if " " in text else ""

    parts = text.split()
    if len(parts) < 2:
        return None

    nickname   = parts[0]
    subcommand = parts[1]

    if subcommand == "off":
        return {"switch": "off", "nickname": nickname}
    if subcommand == "on":
        return {"switch": "on", "nickname": nickname}

    # /ubot nickname eap list
    if subcommand == "eap" and len(parts) >= 3 and parts[2] == "list":
        return {"list": True, "nickname": nickname}

    # /ubot nickname total
    if subcommand == "total":
        return {"total": True, "nickname": nickname}

    # /ubot nickname daily p1 1 [-flags]
    # /ubot nickname daily offline c-1 [-flags]
    if subcommand == "daily":
        if len(parts) < 4:
            return {"error": "Invalid format.\nExample: `/ubot ovra daily p1 1`"}

        if parts[2] == "offline":
            subj_index_token = parts[3]
            flags            = parts[4:]

            match = re.match(r'^([pcm])-?(\d+)$', subj_index_token)
            if not match:
                return {
                    "error": (
                        "Invalid daily offline format.\n"
                        "Use: `/ubot nickname daily offline <subject>-<index>`\n"
                        "Example: `/ubot ovra daily offline c-1`\n"
                        f"Valid subjects: {', '.join(VALID_DAILY_SUBJECTS)}"
                    )
                }

            return {
                "daily_offline": True,
                "nickname":      nickname,
                "subject_code":  match.group(1),
                "index":         match.group(2),
                **_parse_flags(flags),
            }

        subj_index_token = parts[2]
        part_token        = parts[3]
        flags             = parts[4:]

        match = re.match(r'^([pcm])(\d+)$', subj_index_token)
        if not match:
            return {
                "error": (
                    "Invalid daily format.\n"
                    "Use: `/ubot nickname daily <subject><index> <part>`\n"
                    "Example: `/ubot ovra daily p1 1`\n"
                    f"Valid subjects: {', '.join(VALID_DAILY_SUBJECTS)}"
                )
            }

        subject_code = match.group(1)
        index        = match.group(2)

        if not part_token.isdigit():
            return {"error": "Part number must be a number.\nExample: `/ubot ovra daily p1 1`"}

        return {
            "daily":        True,
            "nickname":     nickname,
            "subject_code": subject_code,
            "index":        index,
            "part":         part_token,
            **_parse_flags(flags),
        }

    # /ubot nickname weekly 1 [-flags]
    if subcommand == "weekly":
        if len(parts) < 3:
            return {"error": "Invalid format.\nExample: `/ubot ovra weekly 1`"}

        serial_token = parts[2]
        flags        = parts[3:]

        if not serial_token.isdigit():
            return {"error": "Weekly exam number must be a number.\nExample: `/ubot ovra weekly 1`"}

        return {
            "weekly":   True,
            "nickname": nickname,
            "serial":   serial_token,
            **_parse_flags(flags),
        }

    return None


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text
    if not text.lower().startswith("/ubot"):
        return

    query = text.strip()
    if query.lower().startswith("/ubot"):
        query = query[len("/ubot"):].strip()
    if query.startswith("@"):
        query = query.split(" ", 1)[-1].strip() if " " in query else ""

    if query.lower() in FIXED_REPLIES:
        await update.message.reply_text(FIXED_REPLIES[query.lower()])
        return

    parsed = parse_message(text)

    if parsed is None:
        await update.message.reply_text(
            "Invalid format.\n"
            "Use: `/ubot nickname daily <subject><index> <part>`\n"
            "Example: `/ubot ovra daily p1 1`\n"
            "Use: `/ubot nickname weekly <exam number>`\n"
            "Example: `/ubot ovra weekly 1`\n"
            "Debug: `/ubot ovra eap list`",
            parse_mode="Markdown"
        )
        return

    if "error" in parsed:
        await update.message.reply_text(parsed["error"], parse_mode="Markdown")
        return

    if parsed.get("switch"):
        nickname = parsed["nickname"]
        if parsed["switch"] == "off":
            DISABLED_STUDENTS.add(nickname)
            await update.message.reply_text(f"Results for *{nickname}* have been disabled.", parse_mode="Markdown")
        elif parsed["switch"] == "on":
            DISABLED_STUDENTS.discard(nickname)
            await update.message.reply_text(f"Results for *{nickname}* have been enabled.", parse_mode="Markdown")
        return

    if parsed["nickname"] in DISABLED_STUDENTS:
        await update.message.reply_text(f"Results for *{parsed['nickname']}* are currently disabled.", parse_mode="Markdown")
        return

    if parsed.get("list"):
        await update.message.reply_text("Fetching EAP exam list, please wait...")
        result = await fetch_eap_list(parsed["nickname"])
        await update.message.reply_text(result, parse_mode="Markdown")
        return

    if parsed.get("total"):
        await update.message.reply_text("Fetching course merit, please wait...")
        result = await fetch_total(parsed["nickname"])
        await update.message.reply_text(result, parse_mode="Markdown")
        return

    is_pdf_request = parsed.get("show_result", False)
    await update.message.reply_text(
        "Fetching result PDF, please wait..." if is_pdf_request else "Fetching result, please wait..."
    )

    if parsed.get("daily"):
        if parsed.get("show_result"):
            await _send_result_pdf(
                update,
                kind="daily",
                nickname=parsed["nickname"],
                subject_code=parsed["subject_code"],
                index=parsed["index"],
                part=parsed["part"],
            )
            return

        result = await fetch_daily(
            nickname     = parsed["nickname"],
            subject_code = parsed["subject_code"],
            index        = parsed["index"],
            part         = parsed["part"],
            show_cq      = parsed["show_cq"],
            show_mcq     = parsed["show_mcq"],
            show_marks   = parsed["show_marks"],
            show_branch  = parsed["show_branch"],
            show_central = parsed["show_central"],
        )
        await update.message.reply_text(result, parse_mode="Markdown")
        return

    if parsed.get("daily_offline"):
        if parsed.get("show_result"):
            await _send_result_pdf(
                update,
                kind="daily_offline",
                nickname=parsed["nickname"],
                subject_code=parsed["subject_code"],
                index=parsed["index"],
            )
            return

        result = await fetch_daily_offline(
            nickname     = parsed["nickname"],
            subject_code = parsed["subject_code"],
            index        = parsed["index"],
            show_cq      = parsed["show_cq"],
            show_mcq     = parsed["show_mcq"],
            show_marks   = parsed["show_marks"],
            show_branch  = parsed["show_branch"],
            show_central = parsed["show_central"],
        )
        await update.message.reply_text(result, parse_mode="Markdown")
        return

    if parsed.get("weekly"):
        if parsed.get("show_result"):
            await _send_result_pdf(
                update,
                kind="weekly",
                nickname=parsed["nickname"],
                serial=parsed["serial"],
            )
            return

        result = await fetch_weekly(
            nickname     = parsed["nickname"],
            serial       = parsed["serial"],
            show_cq      = parsed["show_cq"],
            show_mcq     = parsed["show_mcq"],
            show_marks   = parsed["show_marks"],
            show_branch  = parsed["show_branch"],
            show_central = parsed["show_central"],
        )
        await update.message.reply_text(result, parse_mode="Markdown")
        return


async def _send_result_pdf(update: Update, kind, nickname, **kwargs):
    """Fetches (or reuses a cached) Analysis Report PDF and sends it to the user."""
    if kind == "daily":
        pdf_path, status = await get_daily_result_pdf(
            nickname=nickname,
            subject_code=kwargs["subject_code"],
            index=kwargs["index"],
            part=kwargs["part"],
        )
    elif kind == "daily_offline":
        pdf_path, status = await get_daily_offline_result_pdf(
            nickname=nickname,
            subject_code=kwargs["subject_code"],
            index=kwargs["index"],
        )
    else:
        pdf_path, status = await get_weekly_result_pdf(
            nickname=nickname,
            serial=kwargs["serial"],
        )

    if pdf_path is None:
        # status holds the error message in this case
        await update.message.reply_text(status)
        return

    caption = f"📄 *{nickname.upper()} — Result PDF*"
    if status == "cached":
        caption += "\n_(served from cache)_"

    with open(pdf_path, "rb") as f:
        await update.message.reply_document(document=f, filename=os.path.basename(pdf_path), caption=caption, parse_mode="Markdown")


async def add_student(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != MY_TELEGRAM_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return

    if len(context.args) != 3:
        await update.message.reply_text(
            "Invalid format.\n"
            "Use: `/addstudent nickname registration password`\n"
            "Example: `/addstudent ovra 1739257 mypassword`",
            parse_mode="Markdown"
        )
        return

    nickname = context.args[0].lower()
    reg      = context.args[1]
    password = context.args[2]

    from students import STUDENTS
    if nickname in STUDENTS:
        await update.message.reply_text(f"Student *{nickname}* already exists. Edit `students.py` manually to update.", parse_mode="Markdown")
        return

    new_entry = f'    "{nickname}": {{\n        "reg": "{reg}",\n        "password": "{password}"\n    }},\n'

    students_path = os.path.join(os.path.dirname(__file__), "students.py")
    with open(students_path, "r") as f:
        content = f.read()

    insertion_point = content.rfind("}")
    updated_content = content[:insertion_point] + new_entry + content[insertion_point:]

    with open(students_path, "w") as f:
        f.write(updated_content)

    STUDENTS[nickname] = {"reg": reg, "password": password}

    await update.message.reply_text(f"Student *{nickname}* added successfully.", parse_mode="Markdown")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "👋 *EAP Result Bot — Udvash*\n\n"
        "*Daily exams:*\n"
        "`/ubot nickname daily <subject><index> <part>`\n"
        "Example: `/ubot ovra daily p1 1` — Physics daily exam 1, part 1\n"
        "Example: `/ubot ovra daily m2 2` — Higher Math daily exam 2, part 2\n"
        f"Valid subjects: {', '.join(VALID_DAILY_SUBJECTS)} (p = Physics, c = Chemistry, m = Higher Math)\n\n"
        "*Daily offline exams (MCQ and Written, no part number):*\n"
        "`/ubot nickname daily offline <subject>-<index>`\n"
        "Example: `/ubot ovra daily offline c-1`\n\n"
        "*Weekly exams:*\n"
        "`/ubot nickname weekly <exam number>`\n"
        "Example: `/ubot ovra weekly 1`\n\n"
        "*Flags (optional, add after the command):*\n"
        "`-cq` — Written marks\n"
        "`-mcq` — MCQ marks\n"
        "`-marks` — both MCQ and Written marks\n"
        "`-branch` — branch merit\n"
        "`-merit` — central merit\n"
        "`pdf` — get the full Analysis Report as a PDF instead of a text summary\n\n"
        "*Course merit:*\n"
        "`/ubot nickname total` — overall course merit summary\n\n"
        "*Debug:*\n"
        "`/ubot nickname eap list` — list all EAP exams found on the report page\n\n"
        "*Switching results on/off:*\n"
        "`/ubot ovra off` — disable results for ovra\n"
        "`/ubot ovra on` — enable results for ovra"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


# ── Entry point ───────────────────────────────────────────────────────────────

async def post_init(app):
    global _bot_loop, _telegram_app
    _telegram_app = app
    _bot_loop = asyncio.get_running_loop()
    print("[Bot] Telegram polling started", flush=True)


if __name__ == "__main__":
    signal_thread = threading.Thread(target=run_signal_server, daemon=True)
    signal_thread.start()

    app = ApplicationBuilder().token(os.getenv("BOT_TOKEN")).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addstudent", add_student))
    app.add_handler(MessageHandler(filters.TEXT, handle_message))

    app.run_polling(allowed_updates=Update.ALL_TYPES)
