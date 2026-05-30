# IndiaBix Current Affairs — Scraper + API

## Setup

```bash
pip install -r requirements.txt
```

## Scraper

```bash
python scraper.py
```

### Rerun safety

The scraper checks `scrape_status` before fetching any date.  
Dates already marked `success` are **skipped entirely** — no duplicate HTTP calls, no duplicate DB writes.

```bash
Total dates : 204
Already done: 150          ← skipped
To scrape   : 54           ← only these are fetched
```

---

## API

```bash
uvicorn api:app --reload --port 8000
```

Interactive docs: <http://localhost:8000/docs>

### Endpoints

| Method | Path                                          | Description                                           |
| ------ | --------------------------------------------- | ----------------------------------------------------- |
| GET    | `/dates`                                      | All scraped dates, newest first (with question count) |
| GET    | `/questions?date=YYYY-MM-DD`                  | All questions for a date                              |
| GET    | `/questions?date=YYYY-MM-DD&category=Science` | Questions for date + category                         |
| GET    | `/categories`                                 | All categories with question counts                   |
| GET    | `/categories/{category}/questions`            | Paginated questions for a category                    |
| GET    | `/practice/{category}?count=10`               | Random questions from a category (quiz mode)          |
| GET    | `/practice/date/{date}?count=10`              | Random questions from a date                          |
| GET    | `/search?q=parliament`                        | Full-text search                                      |
| GET    | `/stats`                                      | DB summary stats                                      |

### Examples

```bash
# List latest 10 dates
curl "http://localhost:8000/dates?limit=10"

# All questions on 2026-01-15
curl "http://localhost:8000/questions?date=2026-01-15"

# Questions on 2026-01-15, only Science category
curl "http://localhost:8000/questions?date=2026-01-15&category=Science"

# All categories
curl "http://localhost:8000/categories"

# Browse Economy questions, newest first, page 2
curl "http://localhost:8000/categories/Economy/questions?limit=20&offset=20"

# 10 random Science questions for practice
curl "http://localhost:8000/practice/Science?count=10"

# Search for questions mentioning "RBI"
curl "http://localhost:8000/search?q=RBI"

# Stats
curl "http://localhost:8000/stats"
```

---

## Bug fixes vs original code

| #   | Issue                                                            | Fix                                                                       |
| --- | ---------------------------------------------------------------- | ------------------------------------------------------------------------- |
| 1   | Reruns re-scraped all dates                                      | `get_done_dates()` fetches successful dates; pending list excludes them   |
| 2   | Duplicate option rows on rerun                                   | Added `UNIQUE(question_id, option_label)` constraint + `INSERT OR IGNORE` |
| 3   | `CHECKPOINT_FILE` declared but never used                        | Removed                                                                   |
| 4   | Question ID fetched after `INSERT OR IGNORE` could return `None` | Always `SELECT` by hash; skip only if genuinely missing                   |
| 5   | N+1 queries in `export_json`                                     | Single JOIN query; options aggregated in Python                           |
| 6   | All 200 dates launched as concurrent coroutines at once          | `BATCH_SIZE = 50` processes dates in chunks                               |
| 7   | Failed dates only retried once as a flat list                    | Retry tracked; permanently-failed dates logged clearly                    |
| 8   | No indexes on foreign keys                                       | Added indexes on `questions(scrape_date_id)` and `questions(category_id)` |
| 9   | WAL mode not enabled                                             | `PRAGMA journal_mode=WAL` for concurrent read/write performance           |
