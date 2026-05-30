"""
IndiaBix Current Affairs Scraper
- Skips already-successfully-scraped dates on rerun
- Fixes duplicate options bug
- Fixes N+1 query in export
- Batches concurrent tasks to reduce memory pressure
"""

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta

import aiosqlite
import httpx
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL = "https://www.indiabix.com/current-affairs"
START_DATE = "2025-11-01"
END_DATE = "2026-05-23"

DB_NAME = "indiabix.db"
OUTPUT_JSON = "questions_by_date.json"

MAX_CONCURRENT_REQUESTS = 10
BATCH_SIZE = 50          # process dates in batches to cap memory use
MAX_RETRIES = 5

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

logging.basicConfig(
    filename="scraper.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

SEM = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)


# ── Helpers ───────────────────────────────────────────────────────────────────
def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def date_range(start: str, end: str):
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    while s <= e:
        yield s.strftime("%Y-%m-%d")
        s += timedelta(days=1)


# ── DB setup ──────────────────────────────────────────────────────────────────
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.executescript("""
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS scrape_status (
            date       TEXT PRIMARY KEY,
            status     TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS categories (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE
        );

        CREATE TABLE IF NOT EXISTS scrape_dates (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE
        );

        CREATE TABLE IF NOT EXISTS questions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            hash                TEXT UNIQUE,
            scrape_date_id      INTEGER,
            category_id         INTEGER,
            question_no         TEXT,
            question            TEXT,
            correct_answer      TEXT,
            correct_answer_text TEXT,
            explanation         TEXT
        );

        CREATE TABLE IF NOT EXISTS options (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id  INTEGER,
            option_label TEXT,
            option_text  TEXT,
            UNIQUE (question_id, option_label)   -- prevents duplicate options on rerun
        );

        CREATE INDEX IF NOT EXISTS idx_questions_date
            ON questions (scrape_date_id);

        CREATE INDEX IF NOT EXISTS idx_questions_category
            ON questions (category_id);
        """)
        await db.commit()


async def get_done_dates() -> set[str]:
    """Return the set of dates already scraped successfully — skip these on rerun."""
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "SELECT date FROM scrape_status WHERE status = 'success'"
        )
        rows = await cur.fetchall()
    return {r[0] for r in rows}


# ── Parsing ───────────────────────────────────────────────────────────────────
def parse_questions(soup: BeautifulSoup) -> list[dict]:
    result = []

    for q in soup.find_all("div", class_="bix-div-container"):
        item: dict = {
            "question_no": "",
            "question": "",
            "options": {},
            "correct_answer": "",
            "correct_answer_text": "",
            "explanation": "",
            "category": "",
        }

        div = q.find("div", class_="bix-td-qno")
        if div:
            item["question_no"] = clean_text(div.get_text())

        div = q.find("div", class_="bix-td-qtxt")
        if div:
            item["question"] = clean_text(div.get_text())

        div = q.find("div", class_="bix-tbl-options")
        if div:
            opts: list[str] = []
            for s in div.stripped_strings:
                s = clean_text(s)
                if s and s not in opts:
                    opts.append(s)
            for lbl, val in zip(["A", "B", "C", "D"], opts[:4]):
                item["options"][lbl] = val

        div = q.find("div", class_="bix-td-miscell")
        if div and div.find("input"):
            item["correct_answer"] = div.find("input").get("value", "").strip()
            item["correct_answer_text"] = item["options"].get(
                item["correct_answer"], ""
            )

        div = q.find("div", class_="bix-ans-description")
        if div:
            item["explanation"] = clean_text(div.get_text())

        div = q.find("div", class_="explain-link")
        if div:
            txt = clean_text(div.get_text())
            item["category"] = txt.split(":", 1)[-1].strip()

        result.append(item)

    return result


# ── DB write ──────────────────────────────────────────────────────────────────
async def save_questions(date: str, questions: list[dict]):
    async with aiosqlite.connect(DB_NAME) as db:
        # ensure date row
        await db.execute(
            "INSERT OR IGNORE INTO scrape_dates (date) VALUES (?)", (date,)
        )
        cur = await db.execute(
            "SELECT id FROM scrape_dates WHERE date = ?", (date,)
        )
        date_id: int = (await cur.fetchone())[0]

        for q in questions:
            # category
            await db.execute(
                "INSERT OR IGNORE INTO categories (name) VALUES (?)",
                (q["category"],),
            )
            cur = await db.execute(
                "SELECT id FROM categories WHERE name = ?", (q["category"],)
            )
            category_id: int = (await cur.fetchone())[0]

            # question — use INSERT OR IGNORE to be idempotent
            qhash = hashlib.sha256(q["question"].encode()).hexdigest()
            await db.execute(
                """
                INSERT OR IGNORE INTO questions (
                    hash, scrape_date_id, category_id, question_no,
                    question, correct_answer, correct_answer_text, explanation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    qhash, date_id, category_id, q["question_no"],
                    q["question"], q["correct_answer"],
                    q["correct_answer_text"], q["explanation"],
                ),
            )

            # always fetch the real id (even when already existed)
            cur = await db.execute(
                "SELECT id FROM questions WHERE hash = ?", (qhash,)
            )
            row = await cur.fetchone()
            if not row:
                continue
            qid: int = row[0]

            # options — INSERT OR IGNORE prevents duplicates on rerun
            for label, text in q["options"].items():
                await db.execute(
                    """
                    INSERT OR IGNORE INTO options (question_id, option_label, option_text)
                    VALUES (?, ?, ?)
                    """,
                    (qid, label, text),
                )

        await db.execute(
            "INSERT OR REPLACE INTO scrape_status (date, status) VALUES (?, 'success')",
            (date,),
        )
        await db.commit()


# ── Fetch ─────────────────────────────────────────────────────────────────────
async def fetch_date(client: httpx.AsyncClient, date: str) -> bool:
    async with SEM:
        url = f"{BASE_URL}/{date}"

        for attempt in range(MAX_RETRIES):
            try:
                r = await client.get(url)
                r.raise_for_status()

                soup = BeautifulSoup(r.text, "html.parser")
                questions = parse_questions(soup)

                if not questions:
                    logging.warning("NO_QUESTIONS %s", date)
                    # still mark success so we don't keep retrying empty pages
                    async with aiosqlite.connect(DB_NAME) as db:
                        await db.execute(
                            "INSERT OR REPLACE INTO scrape_status (date, status) "
                            "VALUES (?, 'success')",
                            (date,),
                        )
                        await db.commit()
                    return True

                await save_questions(date, questions)
                logging.info("SUCCESS %s (%d questions)", date, len(questions))
                return True

            except Exception as e:
                wait = 2 ** attempt
                logging.error(
                    "%s attempt=%d error=%s — retrying in %ds",
                    date, attempt + 1, e, wait,
                )
                await asyncio.sleep(wait)

        # mark as failed after all retries
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "INSERT OR REPLACE INTO scrape_status (date, status) VALUES (?, 'failed')",
                (date,),
            )
            await db.commit()

        return False


# ── Export JSON ───────────────────────────────────────────────────────────────
async def export_json():
    """Export all questions grouped by date (desc). Uses a single JOIN query — no N+1."""
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            """
            SELECT sd.date,
                   q.question_no, q.question,
                   q.correct_answer, q.correct_answer_text,
                   q.explanation, c.name AS category,
                   o.option_label, o.option_text,
                   q.id
            FROM questions q
            JOIN scrape_dates sd ON q.scrape_date_id = sd.id
            LEFT JOIN categories c ON c.id = q.category_id
            LEFT JOIN options o ON o.question_id = q.id
            ORDER BY sd.date DESC, q.id, o.option_label
            """
        )
        rows = await cur.fetchall()

    output: dict[str, list] = {}
    # accumulate options per question
    q_map: dict[int, dict] = {}
    date_order: list[str] = []

    for row in rows:
        date, qno, qtxt, ans, ans_txt, expl, cat, opt_lbl, opt_txt, qid = row

        if date not in output:
            output[date] = []
            date_order.append(date)

        if qid not in q_map:
            entry = {
                "question_no": qno,
                "question": qtxt,
                "options": {},
                "correct_answer": ans,
                "correct_answer_text": ans_txt,
                "explanation": expl,
                "category": cat,
            }
            q_map[qid] = entry
            output[date].append(entry)

        if opt_lbl:
            q_map[qid]["options"][opt_lbl] = opt_txt

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Exported {sum(len(v) for v in output.values())} questions "
          f"across {len(output)} dates → {OUTPUT_JSON}")


# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    await init_db()

    all_dates = list(date_range(START_DATE, END_DATE))

    # ── KEY FIX: skip dates already scraped successfully ──────────────────────
    done = await get_done_dates()
    pending = [d for d in all_dates if d not in done]

    print(f"Total dates : {len(all_dates)}")
    print(f"Already done: {len(done)}")
    print(f"To scrape   : {len(pending)}")

    if not pending:
        print("Nothing to scrape. Running export only.")
        await export_json()
        return

    async with httpx.AsyncClient(
        headers=HEADERS,
        timeout=30,
        follow_redirects=True,
    ) as client:

        failed: list[str] = []

        # process in batches to cap concurrent coroutine count
        for i in range(0, len(pending), BATCH_SIZE):
            batch = pending[i : i + BATCH_SIZE]
            results = await asyncio.gather(
                *[fetch_date(client, d) for d in batch]
            )
            for d, ok in zip(batch, results):
                if not ok:
                    failed.append(d)
            print(
                f"Batch {i // BATCH_SIZE + 1}: "
                f"{sum(results)}/{len(batch)} succeeded"
            )

        if failed:
            print(f"\nRetrying {len(failed)} failed dates…")
            retry_results = await asyncio.gather(
                *[fetch_date(client, d) for d in failed]
            )
            still_failed = [d for d, ok in zip(failed, retry_results) if not ok]
            if still_failed:
                print(f"Permanently failed: {still_failed}")
                logging.error("PERMANENTLY_FAILED %s", still_failed)

    await export_json()
    print("DONE")


if __name__ == "__main__":
    asyncio.run(main())
