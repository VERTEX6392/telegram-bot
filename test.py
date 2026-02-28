import asyncio
from playwright.async_api import async_playwright

async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)  # headless=False means we SEE the browser open
        page = await browser.new_page()
        print("Browser opened, trying to load Google...")
        await page.goto("https://www.google.com", timeout=15000)
        print("Success! Google loaded.")
        await browser.close()

asyncio.run(test())