import re
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
from dotenv import load_dotenv
from scraper import fetch_result, fetch_total
import os

load_dotenv()

VALID_SUBJECTS = ["bangla", "eng", "chem", "bio", "phys", "hmath", "ict"]
NO_PAPER_SUBJECTS = ["ict"]

MY_TELEGRAM_ID = 1607298724

# Add as many trigger-reply pairs as you want here
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
    # "another trigger": "another reply",
}

# Tracks which students are disabled — resets on bot restart
DISABLED_STUDENTS = set()

def parse_message(text):
    text = text.strip().lower()

    if text.startswith("/ubot"):
        text = text[len("/ubot"):].strip()
    if text.startswith("@"):
        text = text.split(" ", 1)[-1].strip() if " " in text else ""

    parts = text.split()
    if len(parts) < 2:
        return None

    nickname = parts[0]
    exam_part = parts[1]
    flags = parts[2:]

    if exam_part == "total":
        return {"total": True, "nickname": nickname}
    if exam_part == "off":
        return {"switch": "off", "nickname": nickname}
    if exam_part == "on":
        return {"switch": "on", "nickname": nickname}

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

    # Extract the part after /ubot
    query = text.strip()
    if query.lower().startswith("/ubot"):
        query = query[len("/ubot"):].strip()
    # Handle /ubot@botusername format
    if query.startswith("@"):
        query = query.split(" ", 1)[-1].strip() if " " in query else ""

    # Check fixed replies — anyone can use them
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
            "For course total: `/ubot ovra total`",
            parse_mode="Markdown"
        )
        return

    if "error" in parsed:
        await update.message.reply_text(parsed["error"])
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
        "`bangla` `eng` `chem` `bio` `phys` `hmath` `ict`"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def add_student(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != 1607298724:
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

    # Check if already exists
    from students import STUDENTS
    if nickname in STUDENTS:
        await update.message.reply_text(f"Student *{nickname}* already exists. Edit `students.py` manually to update.", parse_mode="Markdown")
        return

    # Append to students.py permanently
    new_entry = f'    "{nickname}": {{\n        "reg": "{reg}",\n        "password": "{password}"\n    }},\n'

    students_path = os.path.join(os.path.dirname(__file__), "students.py")
    with open(students_path, "r") as f:
        content = f.read()

    # Insert before the closing } of the STUDENTS dict
    insertion_point = content.rfind("}")
    updated_content = content[:insertion_point] + new_entry + content[insertion_point:]

    with open(students_path, "w") as f:
        f.write(updated_content)

    # Also add to the live STUDENTS dict so it works immediately without restart
    from students import STUDENTS
    STUDENTS[nickname] = {"reg": reg, "password": password}

    await update.message.reply_text(f"Student *{nickname}* added successfully.", parse_mode="Markdown")

app = ApplicationBuilder().token(os.getenv("BOT_TOKEN")).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT, handle_message))
app.add_handler(CommandHandler("addstudent", add_student))
app.run_polling(allowed_updates=Update.ALL_TYPES)