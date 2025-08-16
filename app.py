import re
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

app = FastAPI(title="Codolio Scraper API")

# CORS so your React app can call this directly (tighten origins later)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

async def scrape_codolio(username: str):
    url = f"https://codolio.com/profile/{username}/problemSolving"

    async with async_playwright() as p:
        # --no-sandbox is harmless on Windows and useful on some hosts
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context()
        page = await context.new_page()

        try:
            # Wait for network to settle, then a key element to ensure UI rendered
            await page.goto(url, wait_until="networkidle")
            await page.wait_for_selector("text=Total Questions", timeout=25000)
        except PWTimeout:
            await browser.close()
            raise HTTPException(status_code=504, detail="Timed out loading Codolio page")

        data = {"basicStats": {}, "problemsSolved": {}, "contestRankings": {}}

        # Helpers: find number near a label inside its nearest card/container
        async def get_number_after_text(label: str):
            loc = page.get_by_text(label, exact=False).first
            if await loc.count() == 0:
                return None
            js = r"""
            (el, label) => {
              const root = el.closest('[class*="MuiCard"]') || el.parentElement || el;
              const t = (root.innerText || "").replace(/\u00A0/g, ' ').trim();
              // Prefer number right after label occurrence
              const idx = t.toLowerCase().indexOf(label.toLowerCase());
              if (idx >= 0) {
                const after = t.slice(idx);
                const m2 = after.match(/(\d+(?:,\d+)*)/);
                if (m2) return m2[1];
              }
              // Fallback: first number in the container
              const m = t.match(/(\d+(?:,\d+)*)/);
              return m ? m[1] : null;
            }
            """
            return await loc.evaluate(js, label)

        async def find_number_by_regex(pattern: str):
            html = await page.content()
            m = re.search(pattern, html, flags=re.IGNORECASE)
            return m.group(1) if m else None

        # ---------- Basic Stats ----------
        data["basicStats"]["total_questions"]   = await get_number_after_text("Total Questions")
        data["basicStats"]["total_active_days"] = await get_number_after_text("Total Active Days")
        # looks like "230 submissions"
        data["basicStats"]["total_submissions"] = await find_number_by_regex(r">\s*(\d+)\s*submissions\b")
        data["basicStats"]["max_streak"]        = await get_number_after_text("Max.Streak")
        data["basicStats"]["current_streak"]    = await get_number_after_text("Current.Streak")
        data["basicStats"]["total_contests"]    = await get_number_after_text("Total Contests")
        data["basicStats"]["awards"]            = await get_number_after_text("Awards")

        # ---------- Problems Solved Breakdown ----------
        labels = [
            ("Fundamentals", "fundamentals"),
            ("DSA", "dsa"),
            ("Easy", "easy"),
            ("Medium", "medium"),
            ("Hard", "hard"),
            ("Competitive Programming", "competitive_programming"),
            ("Codechef", "codechef"),
            ("Codeforces", "codeforces"),
            ("HackerRank", "hackerrank"),
        ]
        for label, key in labels:
            val = await get_number_after_text(label)
            data["problemsSolved"][key] = val or "0"

        # ---------- Contest Rankings ----------
        async def extract_rating(site_label: str):
            loc = page.get_by_text(site_label, exact=False).first
            if await loc.count() == 0:
                return None
            js = r"""
            (el) => {
              const root = el.closest('[class*="MuiCard"]') || el.parentElement || el;
              const t = (root.innerText || "").replace(/\u00A0/g, ' ');
              // ratings typically 3-5 digits, prefer the largest first
              const matches = t.match(/\b(\d{3,5})\b/g) || [];
              // choose the last (often the "current" shown prominently)
              return matches.length ? matches[matches.length - 1] : null;
            }
            """
            return await loc.evaluate(js)

        lc = await extract_rating("LeetCode")
        if lc:
            data["contestRankings"]["leetcode"] = {"rating": lc}

        cc = await extract_rating("CodeChef")
        if cc:
            data["contestRankings"]["codechef"] = {"rating": cc}

        await browser.close()
        return data

@app.get("/")
async def root():
    return {"ok": True, "try": "/codolio/SambhavSurthi"}

@app.get("/codolio/{username}")
async def codolio(username: str):
    return await scrape_codolio(username)
