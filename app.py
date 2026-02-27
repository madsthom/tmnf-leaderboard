import asyncio
import os
import re
import secrets
import struct
import time
import xmlrpc.client
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

XMLRPC_HOST = os.environ.get("XMLRPC_HOST", "tmnf-server-xmlrpc")
XMLRPC_PORT = int(os.environ.get("XMLRPC_PORT", "5000"))
XMLRPC_USER = os.environ.get("XMLRPC_USER", "SuperAdmin")
XMLRPC_PASSWORD = os.environ.get("XMLRPC_PASSWORD", "tester123")

pool: asyncpg.Pool | None = None
featured_map_ids: set[int] = set()

# Server status cache
server_status: dict = {}
_last_map_uid: str = ""
_map_start_time: float = 0


async def gbx_call(method: str, *args) -> dict | None:
    """Connect to TMNF GBXRemote, authenticate, call a method, disconnect."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(XMLRPC_HOST, XMLRPC_PORT), timeout=3
        )
        # Handshake
        size_data = await asyncio.wait_for(reader.readexactly(4), timeout=3)
        size = struct.unpack("<I", size_data)[0]
        await reader.readexactly(size)  # "GBXRemote 2"

        handle = 0x80000001

        async def call(m, *a):
            nonlocal handle
            handle += 1
            xml = xmlrpc.client.dumps(a, m).encode("utf-8")
            header = struct.pack("<II", len(xml) + 4, handle)
            writer.write(header + xml)
            await writer.drain()

            # Read response (skip any callbacks)
            for _ in range(10):
                resp_size = struct.unpack(
                    "<I", await asyncio.wait_for(reader.readexactly(4), timeout=3)
                )[0]
                resp_data = await asyncio.wait_for(
                    reader.readexactly(resp_size), timeout=3
                )
                resp_handle = struct.unpack("<I", resp_data[:4])[0]
                if resp_handle >= 0x80000000:  # Response, not callback
                    result, _ = xmlrpc.client.loads(resp_data[4:].decode("utf-8"))
                    return result[0] if result else None
            return None

        await call("Authenticate", XMLRPC_USER, XMLRPC_PASSWORD)
        result = await call(method, *args)
        writer.close()
        return result
    except Exception:
        return None


async def poll_server_status():
    """Background task to poll server status every 5 seconds."""
    global server_status, _last_map_uid, _map_start_time
    while True:
        try:
            current = await gbx_call("GetCurrentChallengeInfo")
            next_map = await gbx_call("GetNextChallengeInfo")
            game_info = await gbx_call("GetCurrentGameInfo")

            if current and game_info:
                uid = current.get("UId", "")
                if uid != _last_map_uid:
                    _last_map_uid = uid
                    _map_start_time = time.time()

                ta_limit = game_info.get("TimeAttackLimit", 0) / 1000
                elapsed = time.time() - _map_start_time if _map_start_time else 0
                remaining = max(0, ta_limit - elapsed)

                server_status = {
                    "current_map": strip_tm_formatting(current.get("Name", "")),
                    "next_map": strip_tm_formatting(
                        next_map.get("Name", "")
                    ) if next_map else "",
                    "time_remaining": int(remaining),
                    "time_limit": int(ta_limit),
                }
            else:
                server_status = {}
        except Exception:
            server_status = {}
        await asyncio.sleep(5)


async def get_pool() -> asyncpg.Pool:
    global pool
    if pool is None or pool._closed:
        pool = await asyncpg.create_pool(**DB_CONFIG, min_size=1, max_size=5)
    return pool


DEFAULT_MAPS = os.environ.get("DEFAULT_MAPS", "avatar,eventual,observance")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        p = await get_pool()
        async with p.acquire() as conn:
            rows = await conn.fetch("SELECT id, name FROM maps")
            for row in rows:
                clean = strip_tm_formatting(row["name"]).lower()
                if any(k in clean for k in DEFAULT_MAPS.lower().split(",")):
                    featured_map_ids.add(row["id"])
    except Exception:
        pass
    task = asyncio.create_task(poll_server_status())
    yield
    task.cancel()
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
        },
        "server": server_status,
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")
