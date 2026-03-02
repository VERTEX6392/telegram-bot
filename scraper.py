from playwright.async_api import async_playwright
from students import STUDENTS

LOGIN_URL = "https://online.udvash-unmesh.com/Account/Login"
DATA_URL  = "https://online.udvash-unmesh.com/Performance/Report?programId=66&sessionId=66&t=0&d=0"

SUBJECT_MAP = {
    "bangla": "Bangla",
    "eng":    "English",
    "chem":   "Chemistry",
    "bio":    "Biology",
    "phys":   "Physics",
    "hmath":  "Higher Math",
    "ict":    "ICT",
}

PAPER_MAP = {
    "1": "1st",
    "2": "2nd",
    "3": "3rd",
    "4": "4th",
}

NO_PAPER_SUBJECTS = ["ict"]


async def fetch_result(nickname, subject_code, paper_no, exam_serial, show_cq, show_mcq, show_marks, show_branch, show_central):
    nickname = nickname.lower()

    if nickname not in STUDENTS:
        return f"No student found with nickname '{nickname}'. Check the spelling."

    student = STUDENTS[nickname]

    subject_full = SUBJECT_MAP.get(subject_code)
    if not subject_full:
        return f"Unknown subject code '{subject_code}'."

    exam_serial_formatted = f"Exam-{exam_serial.zfill(2)}"

    if subject_code in NO_PAPER_SUBJECTS:
        search_subject = subject_full.lower()
        paper_word = None
    else:
        paper_word = PAPER_MAP.get(paper_no, paper_no + "th")
        search_subject = f"{subject_full} {paper_word} Paper".lower()

    search_serial = exam_serial_formatted.lower()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        page.set_default_timeout(60000)

        await page.goto(LOGIN_URL, wait_until="domcontentloaded")
        await page.wait_for_selector("input[name='RegistrationNumber']", timeout=15000)
        await page.fill("input[name='RegistrationNumber']", student["reg"])
        await page.click("#btnSubmit")
        await page.wait_for_load_state("domcontentloaded")

        await page.wait_for_selector("input[name='Password']", timeout=15000)
        await page.fill("input[name='Password']", student["password"])
        await page.click("button[type='submit']")
        await page.wait_for_load_state("domcontentloaded")

        await page.goto(DATA_URL, wait_until="domcontentloaded")

        try:
            await page.wait_for_selector("table tr td", timeout=20000)
        except:
            await browser.close()
            return "Results table did not load in time. Try again."

        await page.wait_for_timeout(2000)

        rows = await page.query_selector_all("table tr")

        matched_cells = None
        for row in rows:
            cells = [await cell.inner_text() for cell in await row.query_selector_all("td, th")]
            cells = [c.strip() for c in cells]
            if len(cells) >= 10:
                exam_name = cells[2].lower()
                if search_subject in exam_name and search_serial in exam_name:
                    matched_cells = cells
                    break

        await browser.close()

    if not matched_cells:
        if subject_code in NO_PAPER_SUBJECTS:
            label = f"{subject_full} {exam_serial_formatted}"
        else:
            label = f"{subject_full} {paper_word} Paper {exam_serial_formatted}"
        return f"No result found for {label}. Check the subject, paper, and exam number."

    mcq_marks     = matched_cells[4]
    cq_marks      = matched_cells[5]
    total_marks   = matched_cells[7]
    highest       = matched_cells[8]
    branch_merit  = matched_cells[9]
    central_merit = matched_cells[10]

    if subject_code in NO_PAPER_SUBJECTS:
        exam_label = f"{subject_full} — {exam_serial_formatted}"
    else:
        exam_label = f"{subject_full} {paper_word} Paper — {exam_serial_formatted}"

    show_all = not any([show_cq, show_mcq, show_marks, show_branch, show_central])

    lines = [f"📋 *{nickname.upper()} — {exam_label}*"]

    if show_all or show_mcq or show_marks:
        lines.append(f"MCQ Marks: {mcq_marks}")
    if show_all or show_cq or show_marks:
        lines.append(f"Written/CQ Marks: {cq_marks}")
    if show_all:
        lines.append(f"Total Marks: {total_marks}")
        lines.append(f"Highest Marks: {highest}")
    if show_all or show_branch:
        lines.append(f"Branch Merit: {branch_merit}")
    if show_all or show_central:
        lines.append(f"Central Merit: {central_merit}")

    return "\n".join(lines)


async def fetch_total(nickname):
    nickname = nickname.lower()

    if nickname not in STUDENTS:
        return f"No student found with nickname '{nickname}'. Check the spelling."

    student = STUDENTS[nickname]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        page.set_default_timeout(60000)

        await page.goto(LOGIN_URL, wait_until="domcontentloaded")
        await page.wait_for_selector("input[name='RegistrationNumber']", timeout=15000)
        await page.fill("input[name='RegistrationNumber']", student["reg"])
        await page.click("#btnSubmit")
        await page.wait_for_load_state("domcontentloaded")

        await page.wait_for_selector("input[name='Password']", timeout=15000)
        await page.fill("input[name='Password']", student["password"])
        await page.click("button[type='submit']")
        await page.wait_for_load_state("domcontentloaded")

        await page.goto(DATA_URL, wait_until="domcontentloaded")

        try:
            await page.wait_for_selector("table tr td", timeout=20000)
        except:
            await browser.close()
            return "Results table did not load in time. Try again."

        await page.wait_for_timeout(2000)

        # Find the table that contains course merit data
        tables = await page.query_selector_all("table")

        merit_table = None
        for table in tables:
            html = await table.inner_text()
            if "Course Name" in html and "Course Branch Merit" in html:
                merit_table = table
                break

        if not merit_table:
            await browser.close()
            return "Could not find the course merit table."

        rows = await merit_table.query_selector_all("tr")

        data_row = None
        for row in rows:
            cells = [await cell.inner_text() for cell in await row.query_selector_all("td, th")]
            cells = [c.strip() for c in cells]
            if len(cells) >= 9 and cells[0].isdigit():
                data_row = cells
                break

        await browser.close()

    if not data_row:
        return "Could not find course merit data."

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