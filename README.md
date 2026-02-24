# TMNF Leaderboard

The essential infrastructure for 20+ people losing friendships over a free-to-play racing game from 2006.

Live leaderboard for TrackMania Nations Forever LAN parties. Throw it on a big screen, pick your maps, and watch the room explode every time someone shaves 30 milliseconds off their best time.

- **Live leaderboard** — polls every 5 seconds so nobody can set a record in secret
- **Fanfare on new records** — a glorious synthesized brass fanfare blasts through the speakers when someone takes #1, complete with a full-screen golden overlay so the entire room knows what just happened
- **Admin page** — pick which maps to feature on the board, protected by basic auth so the crowd doesn't vote for the novelty maps
- **Per-map rankings** — every player's best time, ranked, for each featured map

## Dev guide

### Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- PostgreSQL (or a dump from the Trakman database)

### Local setup

```bash
# Install dependencies
uv sync

# Import database (if you have a dump)
createdb trakman
psql -d trakman < trakman-dump.sql

# Configure
cp .env.example .env
# Edit .env with your DB connection details

# Run
uv run uvicorn app:app --port 8080 --reload
```

Open http://localhost:8080 for the leaderboard, http://localhost:8080/admin to pick maps.

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_IP` | `tmnf-db` | PostgreSQL host |
| `DB_PORT` | `5432` | PostgreSQL port |
| `DB_USER` | `trakman` | Database user |
| `DB_PASSWORD` | `trakman` | Database password |
| `DB_NAME` | `trakman` | Database name |
| `ADMIN_PASSWORD` | *(required)* | Password for `/admin` (any username works) |

### Docker

```bash
docker build -t tmnf-leaderboard .
docker run -p 8080:8080 \
  -e DB_IP=host.docker.internal \
  -e ADMIN_PASSWORD=changeme \
  tmnf-leaderboard
```

### Releasing

Tag and push — GitHub Actions builds and pushes to `registry.0x01.dk/tmnf-leaderboard`. Flux image automation picks up the new tag and deploys it.

```bash
git tag v0.x.x
git push origin main --tags
```
