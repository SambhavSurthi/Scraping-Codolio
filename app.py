import re
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from pydantic import BaseModel
import os

app = FastAPI(title="Codolio Scraper API", version="1.0.0")

# CORS dddddconfiguration
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
    """Scrape Codolio profile data for a given username"""
    url = f"https://codolio.com/profile/{username}/problemSolving"

    async with async_playwright() as p:
        # Launch browser with optimized settings for deployment
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
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = await context.new_page()

        try:
            # Navigate and wait for content
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_selector("text=Total Questions", timeout=20000)

            # Wait for dynamic content to load
            await page.wait_for_timeout(3000)

        except PWTimeout:
            await browser.close()
            raise HTTPException(status_code=504, detail=f"Timeout loading profile for {username}")
        except Exception as e:
            await browser.close()
            raise HTTPException(status_code=500, detail=f"Failed to load profile: {str(e)}")

        data = {"basicStats": {}, "problemsSolved": {}, "contestRankings": {}}

        try:
            # Helper function to extract numbers after text labels
            async def get_number_after_text(label: str):
                try:
                    loc = page.get_by_text(label, exact=False).first
                    if await loc.count() == 0:
                        return None

                    js_code = """
                    (el, label) => {
                      const root = el.closest('[class*="MuiCard"]') || el.parentElement || el;
                      const text = (root.innerText || "").replace(/\\u00A0/g, ' ').trim();
                      const idx = text.toLowerCase().indexOf(label.toLowerCase());
                      if (idx >= 0) {
                        const after = text.slice(idx);
                        const match = after.match(/(\\d+(?:,\\d+)*)/);
                        if (match) return match[1];
                      }
                      const fallback = text.match(/(\\d+(?:,\\d+)*)/);
                      return fallback ? fallback[1] : null;
                    }
                    """
                    return await loc.evaluate(js_code, label)
                except:
                    return None

            async def find_number_by_regex(pattern: str):
                try:
                    html = await page.content()
                    match = re.search(pattern, html, flags=re.IGNORECASE)
                    return match.group(1) if match else None
                except:
                    return None

            # Extract basic stats
            data["basicStats"]["total_questions"] = await get_number_after_text("Total Questions")
            data["basicStats"]["total_active_days"] = await get_number_after_text("Total Active Days")
            data["basicStats"]["total_submissions"] = await find_number_by_regex(r">\s*(\d+)\s*submissions\b")
            data["basicStats"]["max_streak"] = await get_number_after_text("Max.Streak")
            data["basicStats"]["current_streak"] = await get_number_after_text("Current.Streak")
            data["basicStats"]["total_contests"] = await get_number_after_text("Total Contests")
            data["basicStats"]["awards"] = await get_number_after_text("Awards")

            # Extract problems solved breakdown
            problem_categories = [
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

            for label, key in problem_categories:
                val = await get_number_after_text(label)
                data["problemsSolved"][key] = val or "0"

            # Extract contest rankings
            async def extract_rating(site_label: str):
                try:
                    loc = page.get_by_text(site_label, exact=False).first
                    if await loc.count() == 0:
                        return None

                    js_code = """
                    (el) => {
                      const root = el.closest('[class*="MuiCard"]') || el.parentElement || el;
                      const text = (root.innerText || "").replace(/\\u00A0/g, ' ');
                      const matches = text.match(/\\b(\\d{3,5})\\b/g) || [];
                      return matches.length ? matches[matches.length - 1] : null;
                    }
                    """
                    return await loc.evaluate(js_code)
                except:
                    return None

            leetcode_rating = await extract_rating("LeetCode")
            if leetcode_rating:
                data["contestRankings"]["leetcode"] = {"rating": leetcode_rating}

            codechef_rating = await extract_rating("CodeChef")
            if codechef_rating:
                data["contestRankings"]["codechef"] = {"rating": codechef_rating}

        except Exception as e:
            await browser.close()
            raise HTTPException(status_code=500, detail=f"Error extracting data: {str(e)}")

        await browser.close()
        return data


@app.get("/")
async def root():
    return {
        "message": "Codolio Scraper API",
        "version": "1.0.0",
        "status": "active",
        "endpoints": {
            "GET /health": "Health check",
            "GET /codolio/{username}": "Get profile data for username",
            "POST /codolio": "Get profile data via POST request"
        },
        "example": "/codolio/SambhavSurthi"
    }


@app.get("/health")
async def health_check():
    return {"status": "healthy", "message": "API is running"}


@app.get("/codolio/{username}")
async def get_codolio_profile(username: str):
    """Get Codolio profile data for a specific username"""
    if not username or len(username.strip()) == 0:
        raise HTTPException(status_code=400, detail="Username cannot be empty")

    try:
        data = await scrape_codolio(username.strip())
        return {
            "success": True,
            "username": username,
            "data": data
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/codolio")
async def post_codolio_profile(request: UsernameRequest):
    """Get Codolio profile data via POST request"""
    if not request.username or len(request.username.strip()) == 0:
        raise HTTPException(status_code=400, detail="Username cannot be empty")

    try:
        data = await scrape_codolio(request.username.strip())
        return {
            "success": True,
            "username": request.username,
            "data": data
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# For local development
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)