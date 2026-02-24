import os
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

DB_CONFIG = {
    "host": os.environ.get("DB_IP", "tmnf-db"),
    "port": int(os.environ.get("DB_PORT", "5432")),
    "user": os.environ.get("DB_USER", "trakman"),
    "password": os.environ.get("DB_PASSWORD", "trakman"),
    "database": os.environ.get("DB_NAME", "trakman"),
}

pool: asyncpg.Pool | None = None
featured_map_ids: set[int] = set()


async def get_pool() -> asyncpg.Pool:
    global pool
    if pool is None or pool._closed:
        pool = await asyncpg.create_pool(**DB_CONFIG, min_size=1, max_size=5)
    return pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await get_pool()
    except Exception:
        pass  # DB may not be ready yet; pool created lazily on first request
    yield
    if pool and not pool._closed:
        await pool.close()


app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
security = HTTPBasic()

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")


def verify_admin(credentials: HTTPBasicCredentials = Depends(security)):
    if not ADMIN_PASSWORD:
        raise HTTPException(status_code=503, detail="ADMIN_PASSWORD not configured")
    if not secrets.compare_digest(credentials.password.encode(), ADMIN_PASSWORD.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials


def strip_tm_formatting(text: str) -> str:
    """Strip TrackMania formatting codes ($xxx colors, $o/$i/$s/$z etc)."""
    return re.sub(r"\$([0-9a-fA-F]{3}|[lh]\[.*?\]|[lh]|.)", "", str(text))


def format_time(ms: int | None) -> str:
    """Format milliseconds as M:SS.mmm."""
    if ms is None:
        return "-"
    minutes = ms // 60000
    seconds = (ms % 60000) // 1000
    millis = ms % 1000
    return f"{minutes}:{seconds:02d}.{millis:03d}"


@app.get("/healthz", response_class=PlainTextResponse)
async def healthz():
    return "ok"


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, _=Depends(verify_admin)):
    p = await get_pool()
    async with p.acquire() as conn:
        maps = await conn.fetch("SELECT id, name FROM maps ORDER BY name")
    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "maps": [
                {"id": m["id"], "name": strip_tm_formatting(m["name"])}
                for m in maps
            ],
            "featured": featured_map_ids,
        },
    )


@app.post("/admin")
async def admin_save(request: Request, _=Depends(verify_admin)):
    form = await request.form()
    featured_map_ids.clear()
    for key, val in form.multi_items():
        if key == "maps":
            featured_map_ids.add(int(val))
    return RedirectResponse("/admin", status_code=303)


@app.get("/api/leaderboard")
async def api_leaderboard():
    if not featured_map_ids:
        return {"maps": []}

    p = await get_pool()
    async with p.acquire() as conn:
        maps = await conn.fetch(
            "SELECT id, name, author_time FROM maps WHERE id = ANY($1) ORDER BY name",
            list(featured_map_ids),
        )

        result = []
        for m in maps:
            records = await conn.fetch(
                """
                SELECT p.nickname, MIN(r.time) AS best_time
                FROM records r
                JOIN players p ON p.id = r.player_id
                WHERE r.map_id = $1
                GROUP BY p.id, p.nickname
                ORDER BY best_time
                """,
                m["id"],
            )
            result.append({
                "map_id": m["id"],
                "name": strip_tm_formatting(m["name"]),
                "author_time": format_time(m["author_time"]),
                "records": [
                    {
                        "rank": i + 1,
                        "player": strip_tm_formatting(r["nickname"]),
                        "time": format_time(r["best_time"]),
                        "time_ms": r["best_time"],
                    }
                    for i, r in enumerate(records)
                ],
            })

        # Toxic stats
        time_wasted = await conn.fetch(
            "SELECT nickname, time_played FROM players WHERE time_played > 0 ORDER BY time_played DESC LIMIT 10"
        )
        chat_spammers = await conn.fetch(
            """
            SELECT p.nickname, COUNT(*) AS messages
            FROM chat c
            JOIN players p ON p.id = c.player_id
            GROUP BY p.id, p.nickname
            ORDER BY messages DESC
            LIMIT 10
            """
        )
        map_haters = await conn.fetch(
            """
            SELECT p.nickname, ROUND(AVG(v.vote)::numeric, 1) AS avg_vote, COUNT(*) AS votes_cast
            FROM votes v
            JOIN players p ON p.id = v.player_id
            GROUP BY p.id, p.nickname
            HAVING COUNT(*) >= 2
            ORDER BY avg_vote ASC
            LIMIT 10
            """
        )

    return {
        "maps": result,
        "stats": {
            "time_wasted": [
                {
                    "player": strip_tm_formatting(r["nickname"]),
                    "hours": round(r["time_played"] / 3600, 1),
                }
                for r in time_wasted
            ],
            "chat_spammers": [
                {
                    "player": strip_tm_formatting(r["nickname"]),
                    "messages": r["messages"],
                }
                for r in chat_spammers
            ],
            "map_haters": [
                {
                    "player": strip_tm_formatting(r["nickname"]),
                    "avg_vote": float(r["avg_vote"]),
                    "votes_cast": r["votes_cast"],
                }
                for r in map_haters
            ],
        },
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")
