import os
from urllib.parse import urlparse, parse_qs

from playwright.async_api import async_playwright

from students import STUDENTS
from scraper import (
    _login_and_goto_report,
    _get_eap_rows,
    COL_EXAM_NAME,
    VALID_DAILY_SUBJECTS,
)

BASE_URL = "https://online.udvash-unmesh.com"

# PDFs are cached here, tagged by nickname + examId, so repeat queries for the
# same exam skip the login/scrape/render cycle entirely.
PDF_CACHE_DIR = os.path.join(os.path.dirname(__file__), "pdf_cache")
os.makedirs(PDF_CACHE_DIR, exist_ok=True)

# If the Analysis Report page still shows text like this, the answer key /
# solution isn't published yet. We still render and send the PDF, but we
# don't write it to the permanent cache — otherwise a future query would
# keep serving a stale, incomplete copy forever.
PENDING_PHRASES = ["will be published", "will be available"]


def _cache_path(nickname, exam_id):
    safe_id = str(exam_id).replace("/", "_")
    return os.path.join(PDF_CACHE_DIR, f"{nickname}_{safe_id}.pdf")


def _extract_exam_id(action_href):
    """Pull examId out of the Action column's 'View Result' link, e.g.
    /Exam/Result?courseId=2993&routineId=157888&examId=127494&studentProgramId=5786498
    """
    if not action_href:
        return None
    parsed = urlparse(action_href)
    params = parse_qs(parsed.query)
    return params.get("examId", [None])[0]


async def _find_matching_row(page, predicate):
    eap_rows = await _get_eap_rows(page)
    for row in eap_rows:
        if predicate(row["cells"]):
            return row
    return None


async def _render_report_pdf(nickname, student, predicate, fallback_key, not_found_label):
    """
    Single browser session: login -> find the row -> follow its Action link ->
    wait for the page (math/images) to finish -> render PDF -> cache if final.
    Returns (path_to_pdf, status) where status is one of:
      "cached" | "generated" | "pending" | None (see message in path slot on error)
    On error, returns (None, error_message).
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1600, "height": 1000})
        page.set_default_timeout(60000)

        loaded = await _login_and_goto_report(page, student)
        if not loaded:
            await browser.close()
            return None, "Results table did not load in time. Try again."

        row = await _find_matching_row(page, predicate)
        if not row:
            await browser.close()
            return None, f"No result found for {not_found_label}. Check the details and try again."

        action_href = row.get("action_href")
        if not action_href:
            await browser.close()
            return None, "This exam doesn't have a detailed report link yet (it may not be graded)."

        exam_id = _extract_exam_id(action_href) or fallback_key
        cache_path = _cache_path(nickname, exam_id)

        if os.path.exists(cache_path):
            await browser.close()
            return cache_path, "cached"

        full_url = action_href if action_href.startswith("http") else BASE_URL + action_href
        await page.goto(full_url, wait_until="domcontentloaded")

        # Let MathJax/KaTeX finish typesetting and diagram images finish loading
        # before snapshotting. Best-effort: if the network never fully idles
        # (trackers, polling, etc.), fall through and render anyway.
        try:
            await page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass

        try:
            body_text = (await page.inner_text("body")).lower()
        except Exception:
            body_text = ""
        is_pending = any(phrase in body_text for phrase in PENDING_PHRASES)

        await page.emulate_media(media="screen")

        # The portal's left nav sidebar is position:fixed/sticky, so Playwright's
        # print-to-PDF repeats it over every page, overlaying report content.
        # Rather than targeting the sidebar's specific CSS classes (fragile if
        # the markup changes), hide every fixed/sticky-positioned element
        # generically right before snapshotting.
        await page.evaluate("""
            document.querySelectorAll('*').forEach(el => {
                const pos = window.getComputedStyle(el).position;
                if (pos === 'fixed' || pos === 'sticky') {
                    el.style.setProperty('display', 'none', 'important');
                }
            });
        """)

        tmp_path = cache_path + ".tmp"
        await page.pdf(path=tmp_path, format="A4", print_background=True)

        await browser.close()

    if is_pending:
        # Don't promote to the permanent cache path — leave the .tmp file where
        # it is and hand it back as-is. It'll simply be regenerated next time.
        return tmp_path, "pending"

    os.replace(tmp_path, cache_path)
    return cache_path, "generated"


async def get_daily_result_pdf(nickname, subject_code, index, part):
    nickname = nickname.lower()

    if nickname not in STUDENTS:
        return None, f"No student found with nickname '{nickname}'. Check the spelling."

    if subject_code not in VALID_DAILY_SUBJECTS:
        return None, f"Unknown subject '{subject_code}'. Valid subjects: {', '.join(VALID_DAILY_SUBJECTS)}"

    student = STUDENTS[nickname]

    subject_letter = subject_code.upper()
    index_padded   = index.zfill(2)
    part_padded    = part.zfill(2)

    frag_subject_index = f"{subject_letter}-{index_padded}".lower()
    frag_part           = f"part-{part_padded}".lower()
    label               = f"{subject_letter}-{index_padded} Part-{part_padded}"

    def predicate(cells):
        exam_name = cells[COL_EXAM_NAME].lower()
        return "daily" in exam_name and frag_subject_index in exam_name and frag_part in exam_name

    fallback_key = f"daily-{subject_letter}{index_padded}-p{part_padded}"

    return await _render_report_pdf(nickname, student, predicate, fallback_key, label)


async def get_daily_offline_result_pdf(nickname, subject_code, index):
    nickname = nickname.lower()

    if nickname not in STUDENTS:
        return None, f"No student found with nickname '{nickname}'. Check the spelling."

    if subject_code not in VALID_DAILY_SUBJECTS:
        return None, f"Unknown subject '{subject_code}'. Valid subjects: {', '.join(VALID_DAILY_SUBJECTS)}"

    student = STUDENTS[nickname]

    subject_letter = subject_code.upper()
    index_padded   = index.zfill(2)

    frag_subject_index = f"{subject_letter}-{index_padded}".lower()
    label = f"{subject_letter}-{index_padded} (offline)"

    def predicate(cells):
        exam_name = cells[COL_EXAM_NAME].lower()
        return "daily" in exam_name and frag_subject_index in exam_name and "part" not in exam_name

    fallback_key = f"daily-offline-{subject_letter}{index_padded}"

    return await _render_report_pdf(nickname, student, predicate, fallback_key, label)


async def get_weekly_result_pdf(nickname, serial):
    nickname = nickname.lower()

    if nickname not in STUDENTS:
        return None, f"No student found with nickname '{nickname}'. Check the spelling."

    student = STUDENTS[nickname]
    serial_padded = str(serial).zfill(2)
    label = f"weekly exam {serial_padded}"

    # Same best-effort matching fragments as scraper.fetch_weekly — unconfirmed
    # until a real weekly exam appears. Adjust alongside that function if needed.
    frag_candidates = [
        f"exam-{serial_padded}".lower(),
        f"exam {serial_padded}".lower(),
        f"-{serial_padded}".lower(),
    ]

    def predicate(cells):
        exam_name = cells[COL_EXAM_NAME].lower()
        return "weekly" in exam_name and any(frag in exam_name for frag in frag_candidates)

    fallback_key = f"weekly-{serial_padded}"

    return await _render_report_pdf(nickname, student, predicate, fallback_key, label)
