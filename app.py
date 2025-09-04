import re
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from pydantic import BaseModel

app = FastAPI(title="Codolio Scraper API", version="2.0.0")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class UsernameRequest(BaseModel):
    username: str


async def scrape_codolio(username: str):
    url = f"https://codolio.com/profile/{username}/problemSolving"

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--no-first-run",
                "--no-zygote",
                "--single-process"
            ]
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/115 Safari/537.36"
        )
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=90000)
            await page.wait_for_selector("text=Total Questions", timeout=90000)
            await page.wait_for_timeout(5000)  # let JS render
        except PWTimeout:
            await browser.close()
            raise HTTPException(status_code=504, detail=f"Timeout loading profile for {username}")
        except Exception as e:
            await browser.close()
            raise HTTPException(status_code=500, detail=f"Failed to load profile: {str(e)}")

        data = {
            "basicStats": {},
            "problemsSolved": {},
            "contestRankings": {},
            "heatmap": [],
            "dsaTopics": {}
        }

        try:
            # Helper: extract number after label
            async def extract_stat(label: str):
                try:
                    locator = page.get_by_text(label, exact=False).first
                    if await locator.count() == 0:
                        return None
                    return await locator.evaluate(
                        "(el) => el.nextElementSibling ? el.nextElementSibling.innerText.trim() : null"
                    )
                except:
                    return None

            # ---------- Basic Stats ----------
            data["basicStats"]["total_questions"] = await extract_stat("Total Questions") or "0"
            data["basicStats"]["total_active_days"] = await extract_stat("Total Active Days") or "0"

            # Submissions
            try:
                submissions_el = page.get_by_text(re.compile(r"\d+\s+submissions", re.I)).first
                if await submissions_el.count() > 0:
                    txt = await submissions_el.inner_text()
                    data["basicStats"]["total_submissions"] = txt.replace("submissions", "").strip()
                else:
                    data["basicStats"]["total_submissions"] = "0"
            except:
                data["basicStats"]["total_submissions"] = "0"

            data["basicStats"]["max_streak"] = await extract_stat("Max.Streak") or "0"
            data["basicStats"]["current_streak"] = await extract_stat("Current.Streak") or "0"
            data["basicStats"]["total_contests"] = await extract_stat("Total Contests") or "0"
            data["basicStats"]["awards"] = await extract_stat("Awards") or "0"

            # ---------- Problems Solved ----------
            problem_labels = [
                "Fundamentals", "DSA", "Easy", "Medium", "Hard",
                "Competitive Programming", "Codechef", "Codeforces", "HackerRank"
            ]
            for label in problem_labels:
                val = await extract_stat(label)
                key = label.lower().replace(" ", "_")
                data["problemsSolved"][key] = val or "0"

            # ---------- Contest Rankings ----------
            contest_sites = ["LeetCode", "CodeChef", "Codeforces", "HackerRank"]
            for site in contest_sites:
                try:
                    loc = page.get_by_text(site, exact=False).first
                    if await loc.count() > 0:
                        rating = await loc.evaluate("""
                            (el) => {
                                const parent = el.closest('[class*="MuiCard"]') || el.parentElement;
                                if (!parent) return "0";
                                const span = parent.querySelector("span");
                                if (!span) return "0";
                                const match = span.innerText.match(/\\d+/);
                                return match ? match[0] : "0";
                            }
                        """)
                        data["contestRankings"][site.lower()] = {"rating": rating or "0"}
                    else:
                        data["contestRankings"][site.lower()] = {"rating": "0"}
                except:
                    data["contestRankings"][site.lower()] = {"rating": "0"}

            # ---------- Heatmap ----------
            try:
                data["heatmap"] = await page.eval_on_selector_all(
                    "svg.react-calendar-heatmap rect",
                    """(rects) => rects.map(r => {
                        const tooltip = r.getAttribute("data-tooltip-content") || "";
                        const match = tooltip.match(/(\\d+)\\s+submissions\\s+on\\s+(\\d{2}\\/\\d{2}\\/\\d{4})/);
                        if (match) {
                            return {
                                date: match[2],
                                submissions: parseInt(match[1], 10),
                                colorClass: r.getAttribute("class") || "",
                                styleColor: r.style.fill || r.style.backgroundColor || ""
                            };
                        }
                        return null;
                    }).filter(x => x !== null)"""
                )
            except:
                data["heatmap"] = []

            # ---------- DSA Topics ----------
            try:
                topic_els = await page.query_selector_all(".dsa-topic-item")
                for t in topic_els:
                    try:
                        name = await t.query_selector_eval(".topic-name", "el => el.innerText.trim()")
                        solved = await t.query_selector_eval(".topic-solved", "el => el.innerText.trim()")
                        data["dsaTopics"][name] = solved
                    except:
                        continue
            except:
                data["dsaTopics"] = {}

        except Exception as e:
            await browser.close()
            raise HTTPException(status_code=500, detail=f"Error extracting data: {str(e)}")

        await browser.close()
        return data


# ------------------ API Routes ------------------
@app.get("/")
async def root():
    return {"message": "Codolio Scraper API", "status": "active"}


# ✅ Health endpoint that supports both GET and HEAD
@app.api_route("/health", methods=["GET", "HEAD"])
async def health_check(request: Request):
    return {"status": "healthy"}


@app.get("/codolio/{username}")
async def get_profile(username: str):
    if not username.strip():
        raise HTTPException(status_code=400, detail="Username required")
    return {"success": True, "username": username, "data": await scrape_codolio(username.strip())}


@app.post("/codolio")
async def post_profile(request: UsernameRequest):
    if not request.username.strip():
        raise HTTPException(status_code=400, detail="Username required")
    return {"success": True, "username": request.username, "data": await scrape_codolio(request.username.strip())}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

