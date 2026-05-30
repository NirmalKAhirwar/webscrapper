"""
IndiaBix Current Affairs — REST API (no pagination limits)
Run:
    uvicorn api:app --reload --port 8000
"""
from __future__ import annotations
import random
from typing import Optional
import aiosqlite
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

DB_NAME = "indiabix.db"

app = FastAPI(title="IndiaBix Current Affairs API", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# serve the HTML UI at /
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", include_in_schema=False)
async def root():
    return FileResponse("static/index.html")

# ── Models ────────────────────────────────────────────────────────────────────
class Question(BaseModel):
    id: int
    question_no: str
    question: str
    options: dict[str, str]
    correct_answer: str
    correct_answer_text: str
    explanation: str
    category: str
    date: str

class DateSummary(BaseModel):
    date: str
    question_count: int

class CategorySummary(BaseModel):
    category: str
    question_count: int

class Stats(BaseModel):
    total_questions: int
    total_dates: int
    total_categories: int
    date_range_start: str
    date_range_end: str

# ── DB helpers ────────────────────────────────────────────────────────────────
async def query(sql: str, params: tuple = ()) -> list[dict]:
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(sql, params)
        rows = await cur.fetchall()
    return [dict(r) for r in rows]

async def build_questions(rows: list[dict]) -> list[Question]:
    q_map: dict[int, Question] = {}
    order: list[int] = []
    for r in rows:
        qid = r["id"]
        if qid not in q_map:
            q_map[qid] = Question(
                id=qid, question_no=r["question_no"], question=r["question"],
                options={}, correct_answer=r["correct_answer"],
                correct_answer_text=r["correct_answer_text"],
                explanation=r["explanation"], category=r["category"] or "",
                date=r["date"],
            )
            order.append(qid)
        if r["option_label"]:
            q_map[qid].options[r["option_label"]] = r["option_text"]
    return [q_map[qid] for qid in order]

BASE_Q_SELECT = """
    SELECT q.id, q.question_no, q.question,
           q.correct_answer, q.correct_answer_text, q.explanation,
           c.name AS category, sd.date,
           o.option_label, o.option_text
    FROM questions q
    JOIN scrape_dates sd ON q.scrape_date_id = sd.id
    LEFT JOIN categories c ON c.id = q.category_id
    LEFT JOIN options o ON o.question_id = q.id
"""

# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/dates", response_model=list[DateSummary], tags=["Browse"])
async def list_dates():
    """All scraped dates, newest first, with question count."""
    return await query("""
        SELECT sd.date, COUNT(q.id) AS question_count
        FROM scrape_dates sd
        LEFT JOIN questions q ON q.scrape_date_id = sd.id
        GROUP BY sd.date ORDER BY sd.date DESC
    """)

@app.get("/months", tags=["Browse"])
async def list_months():
    """Distinct year-months available (YYYY-MM), newest first."""
    rows = await query("""
        SELECT DISTINCT strftime('%Y-%m', date) AS month
        FROM scrape_dates ORDER BY month DESC
    """)
    return [r["month"] for r in rows]

@app.get("/questions", response_model=list[Question], tags=["Browse"])
async def get_questions(
    date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    month: Optional[str] = Query(None, description="YYYY-MM — returns all days in month"),
    category: Optional[str] = Query(None),
):
    """All questions — filter by date, month, and/or category. No limit."""
    where, params = [], []
    if date:
        where.append("sd.date = ?"); params.append(date)
    if month:
        where.append("strftime('%Y-%m', sd.date) = ?"); params.append(month)
    if category:
        where.append("c.name = ?"); params.append(category)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    rows = await query(f"{BASE_Q_SELECT} {clause} ORDER BY sd.date DESC, q.id, o.option_label", tuple(params))
    if not rows:
        raise HTTPException(status_code=404, detail="No questions found for the given filters")
    return await build_questions(rows)

@app.get("/categories", response_model=list[CategorySummary], tags=["Browse"])
async def list_categories():
    return await query("""
        SELECT c.name AS category, COUNT(q.id) AS question_count
        FROM categories c
        LEFT JOIN questions q ON q.category_id = c.id
        GROUP BY c.name ORDER BY question_count DESC
    """)

@app.get("/categories/{category}/questions", response_model=list[Question], tags=["Browse"])
async def get_questions_by_category(
    category: str,
    month: Optional[str] = Query(None, description="YYYY-MM filter"),
    newest_first: bool = Query(True),
):
    """All questions in a category. Optional month filter. No limit."""
    order = "DESC" if newest_first else "ASC"
    where = ["c.name = ?"]
    params: list = [category]
    if month:
        where.append("strftime('%Y-%m', sd.date) = ?"); params.append(month)
    clause = "WHERE " + " AND ".join(where)
    rows = await query(
        f"{BASE_Q_SELECT} {clause} ORDER BY sd.date {order}, q.id, o.option_label",
        tuple(params)
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"No questions for category '{category}'")
    return await build_questions(rows)

@app.get("/practice/{category}", response_model=list[Question], tags=["Practice"])
async def practice_by_category(
    category: str,
    count: int = Query(10, ge=1, le=200),
    month: Optional[str] = Query(None),
):
    """Random questions from a category for quiz practice."""
    where = ["c.name = ?"]
    params: list = [category]
    if month:
        where.append("strftime('%Y-%m', sd.date) = ?"); params.append(month)
    clause = "WHERE " + " AND ".join(where)
    id_rows = await query(f"SELECT q.id FROM questions q JOIN categories c ON c.id=q.category_id JOIN scrape_dates sd ON sd.id=q.scrape_date_id {clause}", tuple(params))
    if not id_rows:
        raise HTTPException(status_code=404, detail=f"Category '{category}' not found")
    sampled = random.sample([r["id"] for r in id_rows], min(count, len(id_rows)))
    ph = ",".join("?" * len(sampled))
    rows = await query(f"{BASE_Q_SELECT} WHERE q.id IN ({ph}) ORDER BY q.id, o.option_label", tuple(sampled))
    return await build_questions(rows)

@app.get("/practice/date/{date}", response_model=list[Question], tags=["Practice"])
async def practice_by_date(
    date: str,
    count: int = Query(10, ge=1, le=200),
    category: Optional[str] = Query(None),
):
    where = ["sd.date = ?"]
    params: list = [date]
    if category:
        where.append("c.name = ?"); params.append(category)
    clause = "WHERE " + " AND ".join(where)
    id_rows = await query(
        f"SELECT q.id FROM questions q JOIN scrape_dates sd ON sd.id=q.scrape_date_id LEFT JOIN categories c ON c.id=q.category_id {clause}",
        tuple(params)
    )
    if not id_rows:
        raise HTTPException(status_code=404, detail="No questions found")
    sampled = random.sample([r["id"] for r in id_rows], min(count, len(id_rows)))
    ph = ",".join("?" * len(sampled))
    rows = await query(f"{BASE_Q_SELECT} WHERE q.id IN ({ph}) ORDER BY q.id, o.option_label", tuple(sampled))
    return await build_questions(rows)

@app.get("/search", response_model=list[Question], tags=["Browse"])
async def search_questions(q: str = Query(..., min_length=3)):
    """Full-text search. No result limit."""
    rows = await query(
        f"{BASE_Q_SELECT} WHERE q.question LIKE ? OR q.explanation LIKE ? ORDER BY sd.date DESC, q.id, o.option_label",
        (f"%{q}%", f"%{q}%")
    )
    if not rows:
        raise HTTPException(status_code=404, detail="No questions matched")
    return await build_questions(rows)

@app.get("/stats", response_model=Stats, tags=["Info"])
async def get_stats():
    rows = await query("""
        SELECT (SELECT COUNT(*) FROM questions) AS total_questions,
               (SELECT COUNT(*) FROM scrape_dates) AS total_dates,
               (SELECT COUNT(*) FROM categories) AS total_categories,
               (SELECT MIN(date) FROM scrape_dates) AS date_range_start,
               (SELECT MAX(date) FROM scrape_dates) AS date_range_end
    """)
    r = rows[0]
    return Stats(total_questions=r["total_questions"], total_dates=r["total_dates"],
                 total_categories=r["total_categories"],
                 date_range_start=r["date_range_start"] or "",
                 date_range_end=r["date_range_end"] or "")