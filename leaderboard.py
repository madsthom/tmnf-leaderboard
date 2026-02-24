#!/usr/bin/env python3
import os
import re
from http.server import HTTPServer, BaseHTTPRequestHandler

import pg8000

DB_CONFIG = {
    "host": os.environ.get("DB_IP", "tmnf-db"),
    "port": int(os.environ.get("DB_PORT", "5432")),
    "user": os.environ.get("DB_USER", "trakman"),
    "password": os.environ.get("DB_PASSWORD", "trakman"),
    "database": os.environ.get("DB_NAME", "trakman"),
}


def strip_tm_formatting(text):
    """Strip TrackMania formatting codes ($xxx colors, $o/$i/$s/$z etc)."""
    return re.sub(r"\$([0-9a-fA-F]{3}|[lh]\[.*?\]|[lh]|.)", "", str(text))


def format_time(ms):
    """Format milliseconds as M:SS.mmm."""
    if ms is None:
        return "-"
    minutes = ms // 60000
    seconds = (ms % 60000) // 1000
    millis = ms % 1000
    return f"{minutes}:{seconds:02d}.{millis:03d}"


def query_db():
    try:
        conn = pg8000.connect(**DB_CONFIG)
    except Exception as e:
        return None, None, None, str(e)
    try:
        cur = conn.cursor()

        # Best record per map
        cur.execute("""
            SELECT m.name, p.nickname, r.time, m.author_time
            FROM records r
            JOIN maps m ON m.id = r.map_id
            JOIN players p ON p.id = r.player_id
            WHERE r.time = (
                SELECT MIN(r2.time) FROM records r2 WHERE r2.map_id = r.map_id
            )
            ORDER BY m.name
        """)
        map_records = cur.fetchall()

        # Top players by record count
        cur.execute("""
            SELECT p.nickname, COUNT(*) as records,
                   p.wins, p.time_played
            FROM records r
            JOIN players p ON p.id = r.player_id
            WHERE r.time = (
                SELECT MIN(r2.time) FROM records r2 WHERE r2.map_id = r.map_id
            )
            GROUP BY p.id, p.nickname, p.wins, p.time_played
            ORDER BY records DESC
            LIMIT 20
        """)
        top_players = cur.fetchall()

        # Recent records
        cur.execute("""
            SELECT p.nickname, m.name, r.time, r.date
            FROM records r
            JOIN maps m ON m.id = r.map_id
            JOIN players p ON p.id = r.player_id
            ORDER BY r.date DESC
            LIMIT 20
        """)
        recent = cur.fetchall()

        return map_records, top_players, recent, None
    finally:
        conn.close()


def build_html():
    map_records, top_players, recent, err = query_db()

    if err:
        return f"""<!DOCTYPE html>
<html><head><title>TMNF Leaderboard</title>
<style>{CSS}</style></head>
<body><div class="container">
<h1>TMNF Leaderboard</h1>
<p class="error">Database unavailable: {err}</p>
</div></body></html>"""

    map_rows = ""
    for name, nick, time, author_time in (map_records or []):
        name = strip_tm_formatting(name)
        nick = strip_tm_formatting(nick)
        map_rows += f"<tr><td>{name}</td><td>{nick}</td>"
        map_rows += f"<td>{format_time(time)}</td>"
        map_rows += f"<td>{format_time(author_time)}</td></tr>\n"

    player_rows = ""
    for nick, records, wins, time_played in (top_players or []):
        nick = strip_tm_formatting(nick)
        hours = (time_played or 0) // 3600
        player_rows += f"<tr><td>{nick}</td><td>{records}</td>"
        player_rows += f"<td>{wins}</td><td>{hours}h</td></tr>\n"

    recent_rows = ""
    for nick, name, time, date in (recent or []):
        nick = strip_tm_formatting(nick)
        name = strip_tm_formatting(name)
        date_str = date.strftime("%Y-%m-%d %H:%M") if date else "-"
        recent_rows += f"<tr><td>{nick}</td><td>{name}</td>"
        recent_rows += f"<td>{format_time(time)}</td>"
        recent_rows += f"<td>{date_str}</td></tr>\n"

    no_data = '<tr><td colspan="4" class="empty">No records yet</td></tr>'

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TMNF Leaderboard</title>
<style>{CSS}</style>
</head><body>
<div class="container">
<h1>LAN Party TMNF</h1>
<p class="subtitle">Leaderboard</p>

<h2>Map Records</h2>
<table>
<thead><tr><th>Map</th><th>Record Holder</th><th>Time</th><th>Author</th></tr></thead>
<tbody>{map_rows or no_data}</tbody>
</table>

<h2>Top Players</h2>
<table>
<thead><tr><th>Player</th><th>Records</th><th>Wins</th><th>Play Time</th></tr></thead>
<tbody>{player_rows or no_data}</tbody>
</table>

<h2>Recent Records</h2>
<table>
<thead><tr><th>Player</th><th>Map</th><th>Time</th><th>Date</th></tr></thead>
<tbody>{recent_rows or no_data}</tbody>
</table>

</div></body></html>"""


CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: #0f0f23; color: #ccc; font-family: 'Segoe UI', sans-serif; }
.container { max-width: 900px; margin: 0 auto; padding: 2rem 1rem; }
h1 { color: #00cc66; font-size: 2rem; text-align: center; }
.subtitle { text-align: center; color: #666; margin-bottom: 2rem; }
h2 { color: #0099ff; margin: 2rem 0 0.5rem; border-bottom: 1px solid #222; padding-bottom: 0.3rem; }
table { width: 100%; border-collapse: collapse; margin-bottom: 1rem; }
th { background: #1a1a2e; color: #00cc66; text-align: left; padding: 0.6rem; font-size: 0.85rem; }
td { padding: 0.5rem 0.6rem; border-bottom: 1px solid #1a1a2e; font-size: 0.9rem; }
tr:hover { background: #16213e; }
.empty { text-align: center; color: #555; padding: 1rem; }
.error { text-align: center; color: #ff4444; margin: 2rem 0; }
@media (max-width: 600px) { td, th { padding: 0.3rem; font-size: 0.8rem; } }
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
            return
        html = build_html()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

    def log_message(self, format, *args):
        pass  # suppress request logs


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 8080), Handler)
    print("Leaderboard listening on :8080")
    server.serve_forever()
