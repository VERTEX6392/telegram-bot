import re
import threading
import asyncio
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
from dotenv import load_dotenv
from scraper import fetch_result, fetch_total
from routine_handler import get_upcoming_all, get_upcoming_subject

load_dotenv()

VALID_SUBJECTS = ["bangla", "eng", "chem", "bio", "phys", "hmath", "ict"]
NO_PAPER_SUBJECTS = ["ict"]

MY_TELEGRAM_ID = 1607298724

DISABLED_STUDENTS = set()

GROUP_CHAT_ID = -1003803230318
SIGNAL_SECRET = os.getenv("SIGNAL_SECRET")
SIGNAL_PORT   = int(os.getenv("SIGNAL_PORT", 5000))

FIXED_REPLIES = {
    "ovrar ki kora uchit?": "porashuna kora",
    "shiropa onek cute": "hard agree",
    "sayaner ki kora uchit?": "dhumay haat mara",
    "ankaner ki kora uchit?":"ankan ke? chinina to!",
    "shirshar ki kora uchit?": "ekta proper reality check khawa",
    "shiropar ki kora uchit?": "Weight loss.",
    "tomar ki kora uchit?": "tomader number dekhe hasha",
    "gali de": "bainchod kuttachoda besshamagi nodirput halarbhai khankirpola lewrachoda gushkirpola dhemnamagi chutmarani madarchod aluchoda potolchoda ut-khankir-dim condomchoda dinosaurchoda",
    "jore gali de":"BAINCHOD  KUTTACHODA  BESSHAMAGI  NODIRPUT  HALARBHAI  KHANKIRPOLA  LEWRACHODA  GUSHKIRPOLA  DHEMNAMAGI  CHUTMARANI  MADARCHOD  ALUCHODA  POTOLCHODA  UT-KHANKIR-DIM  CONDOMCHODA  DINOSAURCHODA",
    "love you": "*blushes cutely*",
    "goodnight": "Goodnight soldier. Stay strong rest well.",
    "tumi ki shohomot?": "100% shohomot",
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

def parse_message(text):
    text = text.strip().lower()

    if text.startswith("/ubot"):
        text = text[len("/ubot"):].strip()
    if text.startswith("@"):
        text = text.split(" ", 1)[-1].strip() if " " in text else ""

    parts = text.split()
    if len(parts) < 1:
        return None

    nickname = parts[0]
    exam_part = parts[1] if len(parts) > 1 else None
    flags = parts[2:]

    if nickname == "upcoming":
        if exam_part is None:
            return {"upcoming": True}
        match_with_paper = re.match(r'^([a-z]+)-(\d+)$', exam_part)
        match_no_paper   = re.match(r'^([a-z]+)$', exam_part)
        if match_with_paper:
            return {
                "upcoming":     True,
                "subject_code": match_with_paper.group(1),
                "paper_no":     match_with_paper.group(2),
            }
        elif match_no_paper and match_no_paper.group(1) in NO_PAPER_SUBJECTS:
            return {
                "upcoming":     True,
                "subject_code": match_no_paper.group(1),
                "paper_no":     None,
            }
        else:
            return {"error": "Invalid subject format.\nExample: `/ubot upcoming phys-1` or `/ubot upcoming ict`"}

    if exam_part is None:
        return None

    if exam_part == "off":
        return {"switch": "off", "nickname": nickname}
    if exam_part == "on":
        return {"switch": "on", "nickname": nickname}

    if exam_part == "total":
        return {"total": True, "nickname": nickname}

    match_no_paper   = re.match(r'^([a-z]+)-(\d+)$', exam_part)
    match_with_paper = re.match(r'^([a-z]+)-(\d+)-(\d+)$', exam_part)

    if match_with_paper:
        subject_code = match_with_paper.group(1)
        paper_no     = match_with_paper.group(2)
        exam_serial  = match_with_paper.group(3)
    elif match_no_paper and match_no_paper.group(1) in NO_PAPER_SUBJECTS:
        subject_code = match_no_paper.group(1)
        paper_no     = "1"
        exam_serial  = match_no_paper.group(2)
    else:
        return None

    if subject_code not in VALID_SUBJECTS:
        return {"error": f"Unknown subject code '{subject_code}'.\nValid codes are: {', '.join(VALID_SUBJECTS)}"}

    show_cq      = "-cq"     in flags
    show_mcq     = "-mcq"    in flags
    show_marks   = "-marks"  in flags
    show_branch  = "-branch" in flags
    show_central = "-merit"  in flags or "-central" in flags

    return {
        "nickname":     nickname,
        "subject_code": subject_code,
        "paper_no":     paper_no,
        "exam_serial":  exam_serial,
        "show_cq":      show_cq,
        "show_mcq":     show_mcq,
        "show_marks":   show_marks,
        "show_branch":  show_branch,
        "show_central": show_central,
    }


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
            "Use: `/ubot nickname subject-paper-exam`\n"
            "Example: `/ubot ovra chem-1-01`\n"
            "For ICT: `/ubot ovra ict-01`\n"
            "For course total: `/ubot ovra total`\n"
            "For upcoming exams: `/ubot upcoming` or `/ubot upcoming chem-1`",
            parse_mode="Markdown"
        )
        return

    if "error" in parsed:
        await update.message.reply_text(parsed["error"])
        return

    if parsed.get("upcoming"):
        if "subject_code" in parsed:
            result = get_upcoming_subject(parsed["subject_code"], parsed.get("paper_no"))
        else:
            result = get_upcoming_all()
        await update.message.reply_text(result, parse_mode="Markdown")
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

    await update.message.reply_text("Fetching result, please wait...")

    if parsed.get("total"):
        result = await fetch_total(parsed["nickname"])
        await update.message.reply_text(result, parse_mode="Markdown")
        return

    result = await fetch_result(
        nickname     = parsed["nickname"],
        subject_code = parsed["subject_code"],
        paper_no     = parsed["paper_no"],
        exam_serial  = parsed["exam_serial"],
        show_cq      = parsed["show_cq"],
        show_mcq     = parsed["show_mcq"],
        show_marks   = parsed["show_marks"],
        show_branch  = parsed["show_branch"],
        show_central = parsed["show_central"],
    )

    await update.message.reply_text(result, parse_mode="Markdown")


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
        "👋 *Result Bot — Udvash*\n\n"
        "Send a message in this format:\n"
        "`/ubot nickname subject-paper-exam`\n\n"
        "*Examples:*\n"
        "`/ubot ovra chem-1-01` — all stats\n"
        "`/ubot ovra eng-2-01 -mcq -merit` — MCQ marks + central merit only\n"
        "`/ubot ovra phys-1-01 -cq -branch` — Written marks + branch merit\n"
        "`/ubot ovra ict-01` — ICT has no paper number\n"
        "`/ubot ovra total` — full course merit summary\n\n"
        "*Flags (optional):*\n"
        "`-cq` — Written/CQ marks\n"
        "`-mcq` — MCQ marks\n"
        "`-marks` — both MCQ and Written marks\n"
        "`-branch` — branch merit\n"
        "`-merit` — central merit\n\n"
        "*Subject codes:*\n"
        "`bangla` `eng` `chem` `bio` `phys` `hmath` `ict`\n\n"
        "*Upcoming exams:*\n"
        "`/ubot upcoming` — next upcoming exam\n"
        "`/ubot upcoming chem-1` — next upcoming Chemistry 1st paper\n"
        "`/ubot upcoming ict` — next upcoming ICT exam\n\n"
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
