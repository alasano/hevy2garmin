<p align="center">
  <img src="src/hevy2garmin/static/favicon.svg" width="80" height="80" alt="hevy2garmin logo">
</p>

<h1 align="center">hevy2garmin</h1>

<p align="center">
  <a href="https://github.com/drkostas/hevy2garmin/actions/workflows/ci.yml"><img src="https://github.com/drkostas/hevy2garmin/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/hevy2garmin/"><img src="https://img.shields.io/pypi/v/hevy2garmin" alt="PyPI"></a>
  <a href="https://pypi.org/project/hevy2garmin/"><img src="https://img.shields.io/pypi/pyversions/hevy2garmin" alt="Python"></a>
</p>

<p align="center">
  Sync your <a href="https://hevyapp.com">Hevy</a> gym workouts to <a href="https://connect.garmin.com">Garmin Connect</a> with correct exercise names, sets, reps, weights, calorie estimation, and optional heart rate overlay from your Garmin watch.
</p>

<p align="center">
  <img src="docs/screenshots/dashboard.png" alt="Dashboard" width="800">
</p>

> **Hevy Pro required.** The Hevy API is only available with a [Hevy Pro](https://hevyapp.com) subscription. Without it, hevy2garmin cannot access your workouts.

## Why?

Hevy is great for tracking gym workouts but doesn't sync to Garmin. This tool bridges the gap:

- **Maps 433+ Hevy exercises** to Garmin FIT SDK categories so bench press shows as bench press, not "Other"
- **Generates proper FIT files** with exercise structure, sets, reps, weights, and timing
- **Uploads to Garmin Connect** with the correct activity name and a detailed description
- **Estimates calories** using the Keytel formula (weight, age, VO2max, heart rate)
- **Overlays heart rate data** from your Garmin watch onto workout charts with per-exercise segments
- **Tracks synced workouts** so nothing gets duplicated

## Screenshots

| Workouts | Mappings |
|----------|----------|
| ![Workouts](docs/screenshots/workouts.png) | ![Mappings](docs/screenshots/mappings.png) |
| **HR Timeline** | **Calorie Breakdown** |
| ![HR Chart](docs/screenshots/hr-chart.png) | ![Calories](docs/screenshots/calories.png) |

## Requirements

- **[Hevy Pro](https://hevyapp.com) subscription** (required for API access)
- A [Garmin Connect](https://connect.garmin.com) account
- Python 3.10+

## Quick Start

Pick the option that fits you best:

### Web Dashboard (local install)

```bash
pip install hevy2garmin
hevy2garmin serve
```

> Not on PyPI yet? Install from source: `git clone https://github.com/drkostas/hevy2garmin.git && cd hevy2garmin && pip install .`

Open [localhost:8123](http://localhost:8123). The setup wizard walks you through connecting Hevy and Garmin.

Once you click **Sync Now**, your workouts appear in [Garmin Connect](https://connect.garmin.com/modern/activities) within a few seconds. Enable **auto-sync** on the dashboard to keep things synced on a schedule (30 min to 24 hours).

To keep the server running in the background:

```bash
nohup hevy2garmin serve > /dev/null 2>&1 &
```

<details>
<summary>systemd service file (Linux)</summary>

Save as `/etc/systemd/system/hevy2garmin.service`:

```ini
[Unit]
Description=hevy2garmin dashboard
After=network.target

[Service]
ExecStart=hevy2garmin serve
Restart=always
User=your-username
Environment=HEVY_API_KEY=your-key
Environment=GARMIN_EMAIL=your-email

[Install]
WantedBy=multi-user.target
```

Then `sudo systemctl enable --now hevy2garmin`.

</details>

### CLI

```bash
pip install hevy2garmin

# Interactive setup (Hevy API key + Garmin credentials)
hevy2garmin init

# Sync your 10 most recent workouts
hevy2garmin sync

# List recent workouts (checkmark = already synced)
hevy2garmin list

# Check sync status
hevy2garmin status

# Dry run (generate FIT files without uploading)
hevy2garmin sync --dry-run

# Sync last 5 workouts only
hevy2garmin sync -n 5
```

After syncing, check [Garmin Connect](https://connect.garmin.com/modern/activities) to see your workouts.

**Recurring sync without the dashboard:** set up a crontab after running `hevy2garmin init`:

```bash
# Sync every 2 hours (uses credentials saved by hevy2garmin init)
0 */2 * * * hevy2garmin sync
```

### Docker

```bash
git clone https://github.com/drkostas/hevy2garmin.git
cd hevy2garmin
docker build -t hevy2garmin .
```

Before running in Docker, you need Garmin auth tokens. Either:
- Run `pip install hevy2garmin && hevy2garmin init` locally (if you have Python), or
- Run `docker run -it -v ~/.garminconnect:/root/.garminconnect hevy2garmin init` to set up inside Docker interactively

**Web dashboard with auto-sync:**

```bash
docker run -d -p 8123:8123 --restart unless-stopped \
  -v ~/.hevy2garmin:/root/.hevy2garmin \
  -v ~/.garminconnect:/root/.garminconnect \
  -e HEVY_API_KEY=... \
  -e GARMIN_EMAIL=... \
  hevy2garmin serve
```

Open [localhost:8123](http://localhost:8123) and enable auto-sync on the dashboard.

**One-off sync:**

```bash
docker run --rm \
  -v ~/.hevy2garmin:/root/.hevy2garmin \
  -v ~/.garminconnect:/root/.garminconnect \
  -e HEVY_API_KEY=... \
  -e GARMIN_EMAIL=... \
  hevy2garmin sync
```

### Python API

```bash
pip install hevy2garmin
```

Before using the API, make sure credentials are available via `~/.hevy2garmin/config.json` (run `hevy2garmin init`), environment variables, or pass them directly.

```python
from hevy2garmin.sync import sync

# Uses config from ~/.hevy2garmin/config.json (or env vars)
result = sync()
print(f"Synced: {result['synced']}, Skipped: {result['skipped']}")

# Or pass credentials directly (no config file needed)
result = sync(hevy_api_key="...", garmin_email="...", garmin_password="...")
```

```python
# Just the exercise mapper
from hevy2garmin.mapper import lookup_exercise

cat, subcat, name = lookup_exercise("Bench Press (Barbell)")
# (0, 1, "Bench Press (Barbell)")

# Just FIT generation (see Hevy API docs for workout dict format:
# https://docs.hevy.com/#tag/workout/operation/workout)
from hevy2garmin.fit import generate_fit

result = generate_fit(hevy_workout_dict, hr_samples=None, output_path="workout.fit")
```

To use a Postgres backend, install with Postgres support:

```bash
pip install hevy2garmin[cloud]
```

This adds `psycopg2-binary` and enables automatic Postgres backend detection via `DATABASE_URL`.

## Getting Your Hevy API Key

> **Hevy Pro is required.** API access is not available on the free plan.

1. Go to [Hevy Settings](https://hevyapp.com/settings) > Developer (formerly Integrations & API)
2. Click **Generate API Key** and copy it
3. Paste it into `hevy2garmin init`, the web dashboard setup, or set as `HEVY_API_KEY` env var

If you don't see the Developer section, you need to upgrade to [Hevy Pro](https://hevyapp.com).

## Credentials

**Three ways to provide credentials** (in order of precedence):
1. CLI flags: `--hevy-api-key`, `--garmin-email`, `--garmin-password`
2. Environment variables: `HEVY_API_KEY`, `GARMIN_EMAIL`, `GARMIN_PASSWORD`
3. Config file: `~/.hevy2garmin/config.json` (created by `hevy2garmin init` or the web dashboard)

See [`.env.example`](.env.example) for all available env vars.

**Garmin authentication:** Only needs the password for initial login. After that, tokens are cached (in `~/.garminconnect`, or in Postgres when `DATABASE_URL` is set) and refresh automatically.

If Garmin answers 429, the dashboard records a local cooldown, shown on the setup page and the dashboard, and refuses further login attempts until it clears, because retrying resets Garmin's timer.

## Securing the dashboard

The dashboard has no login by default (fine for a private local install). **If you deploy it to a public URL, protect it** — otherwise anyone who finds the URL can see your data.

- **Set a password.** Set `H2G_PASSWORD` to require login on every page and API route. Without it the dashboard is open, so **always set a password before putting it on a public URL**.
- **Avoid plaintext (optional).** Run `hevy2garmin hash-password` to generate an argon2 hash and set it as `H2G_PASSWORD_HASH` instead of `H2G_PASSWORD`, so the plaintext password never lives in your environment.
- **Brute-force protection.** Failed logins are rate-limited per IP with exponential backoff and a global cap; repeated attempts get HTTP 429 with a cooldown.
- **Sessions.** Login sets a signed, `HttpOnly`, `SameSite=Strict` cookie (30-day TTL by default, configurable via `H2G_SESSION_TTL_DAYS`), marked `Secure` over HTTPS. Setting `H2G_SECRET` (recommended, especially with `H2G_PASSWORD_HASH`) signs sessions with a dedicated key, so rotating the password doesn't sign everyone out. **Sign out all devices** (Settings → Sessions &amp; Security) invalidates every active session at once.

See [`.env.example`](.env.example) for all the related variables.

## Self-hosting

Running it on your own machine — a spare box, a NAS, a Raspberry Pi — keeps your Hevy and Garmin data on hardware you control.

### Docker Compose (recommended)

```bash
git clone https://github.com/drkostas/hevy2garmin.git
cd hevy2garmin
cp .env.example .env      # set HEVY_API_KEY and H2G_PASSWORD
docker compose up -d --build
```

Open [localhost:8123](http://localhost:8123) and connect Garmin from the setup page.

The compose file uses named volumes for the sync database and the Garmin token store. Keep them: the token store is what saves you re-authenticating with Garmin (tokens last roughly a year), and the database is what stops already-synced workouts uploading twice.

It also binds the port to `127.0.0.1` rather than all interfaces, drops all capabilities, and sets `no-new-privileges`. The image runs as an unprivileged user (uid 999).

### Keeping credentials on your own machine

The Garmin login runs on this host from the setup page, so your Garmin password and MFA code never leave your network.

The resulting token store lives in `~/.garminconnect` by default (`garmin_token_dir` in `~/.hevy2garmin/config.json`). Back that directory up and you will not have to log in again after a rebuild — which is what the `garmin_auth` volume in the compose file is for.

Your Hevy API key stays local too.

### Behind a reverse proxy

Put the dashboard behind nginx, Caddy or Traefik on a subdomain, terminate TLS there, and keep the container bound to `127.0.0.1`. **Set `H2G_PASSWORD` before exposing it** — see [Securing the dashboard](#securing-the-dashboard).

Serving it at the **root of a subdomain** (`https://hevy.example.com/`) works with no extra configuration.

Serving it under a **sub-path** (`https://example.com/hevy2garmin/`) works too. It needs two things: the proxy tells the app which sub-path it is mounted at, and you set `H2G_TRUST_FORWARDED_PREFIX=true` so the app believes it.

```nginx
location /hevy2garmin/ {
    proxy_pass http://127.0.0.1:8123/;
    proxy_set_header Host              $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Prefix /hevy2garmin;
}
```

**The opt-in is not busywork.** Any client can send `X-Forwarded-Prefix`, and every URL on the page is built from it — the login form's `action`, the Garmin token POST, redirect targets. On an instance that is *not* behind a prefix-setting proxy, believing the header would let a caller re-point those at their own host. So the header is ignored unless you turn this on, and even then only a plain absolute path is accepted (no `//host`, no scheme, no quotes or angle brackets); anything else is treated as no prefix and the app serves from the root. **Your proxy must set the header itself rather than passing a client-supplied one through.**

Caddy equivalent (`header_up` replaces any incoming value, which is what you want):

```caddyfile
handle_path /hevy2garmin/* {
    reverse_proxy 127.0.0.1:8123 {
        header_up X-Forwarded-Prefix /hevy2garmin
    }
}
```

The app then emits every link, asset, form action, htmx call, redirect and JavaScript-built API URL under that prefix. The proxy does **not** need to rewrite response bodies. Without the header nothing changes, so a root install is unaffected.

### Keeping it in sync

Auto-sync runs on a timer inside the process, so a self-hosted instance can poll as often as you like — enable it and set the interval on the dashboard.

An external scheduler can instead call `GET /api/cron/sync` with `Authorization: Bearer $CRON_SECRET`; it syncs one workout per call.

### Syncing on a Hevy webhook instead of polling

Polling means a finished workout waits up to a full interval. Hevy can push instead: point a Hevy webhook subscription at `POST /api/cron/webhook`, authenticated with the same `CRON_SECRET` bearer token as the cron endpoint.

A webhook that synced immediately would be *worse* than polling for watch users, though: the paired Garmin activity has not arrived yet, the merge finds nothing, and the workout uploads as a plain FIT — leaving exactly the duplicate the merge exists to avoid. So the endpoint answers 200 straight away (Hevy times out in seconds) and stages the sync:

| Variable | Default | Meaning |
| --- | --- | --- |
| `WEBHOOK_DELAY_SECONDS` | `300` | Wait this long before the first attempt |
| `WEBHOOK_RETRY_INTERVAL_SECONDS` | `300` | Gap between attempts |
| `WEBHOOK_MAX_ATTEMPTS` | `24` | Attempts before giving up |

Every attempt is merge-only: the defaults look for the watch activity every five minutes for two hours, the same span as the sync grace period, and never upload a plain FIT, because a watch activity that arrives afterwards would be a duplicate of a workout already marked synced. A workout that has not merged by then is left to auto-sync, which decides between merge and upload once the grace period is over, so keep auto-sync enabled. A Garmin or Hevy error ends the staged sync for that workout and auto-sync picks it up. Retry state is in memory, so a restart drops it. The polling lasts delay plus (attempts minus one) times interval. If you make that shorter than your grace period, a watch activity that arrives late is still merged, by auto-sync after the grace period instead of within minutes.

`CRON_SECRET` must be set for the endpoint to work at all — with no secret configured it answers `503` rather than accepting unauthenticated calls, since it is internet-facing and is deliberately exempt from the dashboard password. At most `WEBHOOK_MAX_INFLIGHT` (4) staged syncs run at once; past that a request is acknowledged but not staged, because the ones already running plus auto-sync cover the work.

### Running as a non-root user

The image runs as uid 999. Named volumes (what the compose file uses) are handled automatically. If you use **bind mounts** instead — the `-v ~/.hevy2garmin:/root/.hevy2garmin` form shown in the Docker section — grant that user access once:

```bash
sudo chown -R 999:999 ~/.hevy2garmin ~/.garminconnect
```

The paths inside the container are unchanged, so nothing needs moving.

## Updating

### pip

```bash
pip install --upgrade hevy2garmin
```

### Docker

```bash
cd hevy2garmin
git pull origin main
docker build -t hevy2garmin .
```

### Git clone (local)

```bash
cd hevy2garmin
git pull origin main
pip install -e .
```

## Activity Description

When hevy2garmin syncs a workout, it adds a text description to the Garmin activity summarizing your session:

```
🏋️ Push Day
⏱️ 52 min
🔥 387 kcal
❤️ avg 118 bpm

• Bench Press (Barbell): 3 sets · 80.0kg × 8
• Incline Dumbbell Press: 3 sets · 28.0kg × 10
• Cable Fly: 3 sets · 15.0kg × 12

— synced by hevy2garmin
```

This is visible in the activity details on Garmin Connect and any connected apps (Strava, etc.). Cardio exercises show distance and duration instead of weight and reps.

## Enhance Watch Activities (opt-in)

By default, hevy2garmin creates a new Garmin activity from your Hevy workout using your watch's daily HR monitoring (~2 min sampling). This works without any behavior change.

If you start a **Strength Training** activity on your Garmin watch when you hit the gym, you can enable **Enhance Watch Activities** in the config (`"merge_mode": true`). hevy2garmin detects the matching watch activity and pushes your Hevy sets, reps and weights into it in place, so the original watch activity stays and nothing is uploaded or deleted. Benefits include:

- **1-second HR sampling** (vs ~2 min in continuous monitoring)
- **Training effect, EPOC, recovery time, and VO2max impact** stay, because the original watch activity is kept (Garmin does not compute these for an uploaded activity)
- **Correct Strava timestamps** (watch-synced activities use the real time, not upload time)
- **Single activity** on Garmin (no duplicate)

If no matching watch activity is found, hevy2garmin falls back to the default flow automatically. Matching requires a Strength Training activity that starts within 20 minutes of the Hevy workout and overlaps it by 70% of whichever of the two is shorter, so a workout you finished late in Hevy, or a watch you left running, still matches. The overlap must also last ten minutes (less for a workout shorter than that), so a recording started by accident and stopped after a minute or two is not taken for the workout.

### Non-strength watch activities (climbing, etc.)

By default, only watch activities recorded as **Strength Training** are eligible for enhancement. If you record something else on your watch at the same time as your Hevy workout — e.g. a **Climbing** session — it's matched as **Strength Training only**, so it won't be merged and a separate activity is created instead.

To also enhance e.g. climbing sessions, add the Garmin activity type(s) under **Settings → Enhance Watch Activities → Advanced → Additional Watch Activity Types**, using Garmin's internal type names (comma-separated), for example:

```
bouldering, indoor_climbing
```

or set `merge_activity_types` directly in `config.json`:

```json
"merge_activity_types": ["strength_training", "bouldering", "indoor_climbing"]
```

## How It Works

1. Pulls workouts from the Hevy API
2. Maps each exercise to a Garmin FIT SDK category and subcategory (433+ built-in mappings, plus any custom ones you add)
3. Generates a structured FIT file with timing, sets, reps, weights, and calories
4. Optionally fetches HR data from Garmin daily monitoring and overlays it on the workout
5. Authenticates with Garmin via [garmin-auth](https://pypi.org/project/garmin-auth/) (self-healing OAuth)
6. Uploads the FIT file, renames the activity, and sets the description
7. Tracks synced workouts in SQLite, or Postgres when `DATABASE_URL` is set, to avoid duplicates

## Exercise Mapping

433+ Hevy exercises are mapped to Garmin FIT SDK categories. If an exercise isn't mapped it falls back to "Unknown" (category 65534). The web dashboard shows unmapped exercises and lets you add custom mappings with a few clicks. You can also add them via CLI:

```bash
hevy2garmin map "My Custom Exercise" --category 28 --subcategory 0
```

## FAQ

**Is the sync one-way?**
Yes — Hevy → Garmin only. Anything you record directly on your watch stays on
Garmin; it does not flow back into Hevy. Keep logging your gym sessions in Hevy
(it's better for that) and this tool makes sure Garmin knows about them.

**Will my gym workouts get duplicated in Health Connect / on my phone (Android)?**
hevy2garmin uploads each Hevy workout to Garmin Connect once, as a single
Strength Training activity (or merges it into a watch-recorded one if you enable
[Enhance Watch Activities](#enhance-watch-activities-opt-in)). It does not write
to Health Connect directly — whatever Garmin Connect chooses to mirror into
Health Connect is Garmin's behavior. If you use Hevy for the gym and Garmin for
running, your runs are untouched; only your Hevy workouts are added.

**Do I need a Hevy Pro subscription?**
Yes. The Hevy API key requires an active Hevy Pro subscription, and the key stops
working once the subscription lapses. See [Getting Your Hevy API Key](#getting-your-hevy-api-key).

**Does it work with non-Garmin watches (Samsung, Amazfit/Zepp, etc.)?**
The tool reads from **Hevy** and writes to **Garmin Connect** — it's not tied to
a specific watch. The destination is always Garmin Connect, so you need a Garmin
account; the watch brand you wear at the gym doesn't matter. It runs in the
browser/cloud, not on the watch.

**My activity shows the wrong time on Strava.**
When Garmin pushes an API-uploaded activity to Strava, Strava sometimes uses the
upload time instead of the workout time. The FIT file and Garmin Connect have the
correct time — this is a Garmin→Strava quirk for non-watch uploads and isn't
something hevy2garmin can control.

**Does 2FA / MFA work on Garmin?**
Native 2FA support is in progress (tracked in
[#29 on garmin-auth](https://github.com/drkostas/garmin-auth/issues/29)). For now,
if your Garmin account has 2FA enabled, temporarily disable it, connect through
hevy2garmin, then re-enable it — the auth tokens persist for months afterward.

## Development

```bash
git clone https://github.com/drkostas/hevy2garmin.git
cd hevy2garmin
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -v
```

To test the Postgres backend locally:

```bash
pip install -e ".[dev,cloud]"
DATABASE_URL=postgresql://user:pass@localhost:5432/hevy2garmin pytest tests/ -v
```

## License

MIT
