from playwright.async_api import async_playwright
from students import STUDENTS

LOGIN_URL = "https://online.udvash-unmesh.com/Account/Login"

# Most students are on programId=68. A couple of students are enrolled under a
# different course (different programId) but sit the same EAP exams, same table
# layout — only the program/course differs. Set student["program_id"] in
# students.py to override this per student; anyone without it falls back here.
DEFAULT_PROGRAM_ID = "68"
SESSION_ID         = "66"


def _report_url(program_id):
    return f"https://online.udvash-unmesh.com/Performance/Report?programId={program_id}&sessionId={SESSION_ID}&t=0&d=0"

# Subject letters as they appear inside EAP Daily exam names, e.g. "P-01", "C-02", "M-01"
VALID_DAILY_SUBJECTS = ["p", "c", "m"]

# Table column indices (confirmed from live HTML sample — same layout as the old report page,
# with one extra "Action" column at the end that we don't use):
#   0 Serial | 1 Date | 2 Exam Name | 3 Platform | 4 MCQ Marks | 5 Written Marks
#   6 Deduction | 7 Total Marks | 8 Highest Marks | 9 Branch Merit | 10 Central Merit | 11 Action

COL_EXAM_NAME     = 2
COL_MCQ_MARKS     = 4
COL_WRITTEN_MARKS = 5
COL_TOTAL_MARKS   = 7
COL_HIGHEST_MARKS = 8
COL_BRANCH_MERIT  = 9
COL_CENTRAL_MERIT = 10


async def _login_and_goto_report(page, student):
    await page.goto(LOGIN_URL, wait_until="domcontentloaded")
    await page.wait_for_selector("input[name='RegistrationNumber']", timeout=15000)
    await page.fill("input[name='RegistrationNumber']", student["reg"])
    await page.click("#btnSubmit")
    await page.wait_for_load_state("domcontentloaded")

    await page.wait_for_selector("input[name='Password']", timeout=15000)
    await page.fill("input[name='Password']", student["password"])
    await page.click("button[type='submit']")
    await page.wait_for_load_state("domcontentloaded")

    program_id = student.get("program_id", DEFAULT_PROGRAM_ID)
    await page.goto(_report_url(program_id), wait_until="domcontentloaded")

    try:
        await page.wait_for_selector("table tr td", timeout=20000)
    except Exception:
        return False

    await page.wait_for_timeout(2000)
    return True


async def _get_eap_rows(page):
    """Return cell-lists for every row whose Exam Name starts with 'EAP'."""
    rows = await page.query_selector_all("table tr")

    eap_rows = []
    for row in rows:
        cells = [await cell.inner_text() for cell in await row.query_selector_all("td, th")]
        cells = [c.strip() for c in cells]
        if len(cells) > COL_CENTRAL_MERIT and cells[COL_EXAM_NAME].strip().lower().startswith("eap"):
            eap_rows.append(cells)

    return eap_rows


def _format_result(nickname, exam_label, cells, show_cq, show_mcq, show_marks, show_branch, show_central, icon="📋"):
    mcq_marks     = cells[COL_MCQ_MARKS]
    written_marks = cells[COL_WRITTEN_MARKS]
    total_marks   = cells[COL_TOTAL_MARKS]
    highest       = cells[COL_HIGHEST_MARKS]
    branch_merit  = cells[COL_BRANCH_MERIT]
    central_merit = cells[COL_CENTRAL_MERIT]

    show_all = not any([show_cq, show_mcq, show_marks, show_branch, show_central])

    lines = [f"{icon} *{nickname.upper()} — {exam_label}*"]

    if show_all or show_mcq or show_marks:
        lines.append(f"MCQ Marks: {mcq_marks}")
    if show_all or show_cq or show_marks:
        lines.append(f"Written Marks: {written_marks}")
    if show_all:
        lines.append(f"Total Marks: {total_marks}")
        lines.append(f"Highest Marks: {highest}")
    if show_all or show_branch:
        lines.append(f"Branch Merit: {branch_merit}")
    if show_all or show_central:
        lines.append(f"Central Merit: {central_merit}")

    return "\n".join(lines)


async def fetch_daily(nickname, subject_code, index, part,
                       show_cq, show_mcq, show_marks, show_branch, show_central):
    nickname = nickname.lower()

    if nickname not in STUDENTS:
        return f"No student found with nickname '{nickname}'. Check the spelling."

    if subject_code not in VALID_DAILY_SUBJECTS:
        return f"Unknown subject '{subject_code}'. Valid subjects: {', '.join(VALID_DAILY_SUBJECTS)}"

    student = STUDENTS[nickname]

    subject_letter = subject_code.upper()
    index_padded   = index.zfill(2)
    part_padded    = part.zfill(2)

    # e.g. "P-01" and "Part-01" — both must appear in an exam name that also contains "Daily"
    frag_subject_index = f"{subject_letter}-{index_padded}".lower()
    frag_part           = f"part-{part_padded}".lower()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        page.set_default_timeout(60000)

        loaded = await _login_and_goto_report(page, student)
        if not loaded:
            await browser.close()
            return "Results table did not load in time. Try again."

        eap_rows = await _get_eap_rows(page)
        await browser.close()

    matched = None
    for cells in eap_rows:
        exam_name = cells[COL_EXAM_NAME].lower()
        if "daily" in exam_name and frag_subject_index in exam_name and frag_part in exam_name:
            matched = cells
            break

    if not matched:
        label = f"{subject_letter}-{index_padded} Part-{part_padded}"
        return f"No daily result found for {label}. Check the subject, index, and part number."

    exam_label = matched[COL_EXAM_NAME]
    return _format_result(nickname, exam_label, matched, show_cq, show_mcq, show_marks, show_branch, show_central, icon="📋")


async def fetch_weekly(nickname, serial,
                        show_cq, show_mcq, show_marks, show_branch, show_central):
    """
    NOTE: No real 'EAP Weekly ...' exam has appeared yet (course just started), so this
    matching logic is a best-effort guess mirroring the Daily naming style. Once a real
    weekly exam shows up, run 'nickname eap list' to see its exact name and adjust the
    matching fragments below (frag_candidates) to fit the real format.
    """
    nickname = nickname.lower()

    if nickname not in STUDENTS:
        return f"No student found with nickname '{nickname}'. Check the spelling."

    student = STUDENTS[nickname]
    serial_padded = str(serial).zfill(2)

    # Reasonable guesses for how the serial might appear, e.g. "Exam-01" or "-01"
    frag_candidates = [
        f"exam-{serial_padded}".lower(),
        f"exam {serial_padded}".lower(),
        f"-{serial_padded}".lower(),
    ]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        page.set_default_timeout(60000)

        loaded = await _login_and_goto_report(page, student)
        if not loaded:
            await browser.close()
            return "Results table did not load in time. Try again."

        eap_rows = await _get_eap_rows(page)
        await browser.close()

    matched = None
    for cells in eap_rows:
        exam_name = cells[COL_EXAM_NAME].lower()
        if "weekly" in exam_name and any(frag in exam_name for frag in frag_candidates):
            matched = cells
            break

    if not matched:
        return (
            f"No weekly result found for exam {serial_padded}. "
            "Note: weekly exam-name format hasn't been confirmed yet — "
            "try `nickname eap list` to see raw EAP exam names and check the match."
        )

    exam_label = matched[COL_EXAM_NAME]
    return _format_result(nickname, exam_label, matched, show_cq, show_mcq, show_marks, show_branch, show_central, icon="📆")


async def fetch_total(nickname):
    """
    Pulls the 'Course Merit Calculation' table at the bottom of the report page.
    Columns: 0 Serial | 1 Course Name | 2 Total MCQ Marks | 3 Total Written Marks
             | 4 Total Obtained Marks | 5 Total Deduction | 6 Highest Marks
             | 7 Course Branch Merit | 8 Course Central Merit
    """
    nickname = nickname.lower()

    if nickname not in STUDENTS:
        return f"No student found with nickname '{nickname}'. Check the spelling."

    student = STUDENTS[nickname]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        page.set_default_timeout(60000)

        loaded = await _login_and_goto_report(page, student)
        if not loaded:
            await browser.close()
            return "Results table did not load in time. Try again."

        tables = await page.query_selector_all("table")

        merit_table = None
        for table in tables:
            text = await table.inner_text()
            if "Course Name" in text and "Course Branch Merit" in text:
                merit_table = table
                break

        if not merit_table:
            await browser.close()
            return "Could not find the course merit table."

        rows = await merit_table.query_selector_all("tr")

        data_rows = []
        for row in rows:
            cells = [await cell.inner_text() for cell in await row.query_selector_all("td, th")]
            cells = [c.strip() for c in cells]
            if len(cells) >= 9 and cells[0].isdigit():
                data_rows.append(cells)

        await browser.close()

    if not data_rows:
        return "No course merit data available yet."

    data_row = data_rows[0]

    course_name    = data_row[1]
    mcq_marks      = data_row[2]
    written_marks  = data_row[3]
    obtained_marks = data_row[4]
    deduction      = data_row[5]
    highest_marks  = data_row[6]
    branch_merit   = data_row[7]
    central_merit  = data_row[8]

    lines = [
        f"📊 *{nickname.upper()} — Course Merit*",
        f"Course: {course_name}",
        f"Total MCQ Marks: {mcq_marks}",
        f"Total Written Marks: {written_marks}",
        f"Total Obtained Marks: {obtained_marks}",
        f"Deduction: {deduction}",
        f"Highest Marks: {highest_marks}",
        f"Branch Merit: {branch_merit}",
        f"Central Merit: {central_merit}",
    ]

    return "\n".join(lines)


async def fetch_eap_list(nickname):
    """Debug helper: dump every raw EAP-prefixed exam name + date currently on the page."""
    nickname = nickname.lower()

    if nickname not in STUDENTS:
        return f"No student found with nickname '{nickname}'. Check the spelling."

    student = STUDENTS[nickname]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        page.set_default_timeout(60000)

        loaded = await _login_and_goto_report(page, student)
        if not loaded:
            await browser.close()
            return "Results table did not load in time. Try again."

        eap_rows = await _get_eap_rows(page)
        await browser.close()

    if not eap_rows:
        return "No EAP exams found on the report page yet."

    lines = [f"🔎 *{nickname.upper()} — EAP exams found:*"]
    for cells in eap_rows:
        date = cells[1]
        name = cells[COL_EXAM_NAME]
        lines.append(f"`{date}` — {name}")

    return "\n".join(lines)
