# Card Maze 🃏

Card Maze is a small app made with Python, Flask, SQLite, Bootstrap and other libraries, it allows the user to create a "deck" of 56 cards with micro tasks that grant points, once the points reach certain thresholds prizes are revealed. It was designed to be selfhosted, and all tasks and prizes can be configured inside the app, the decks can be exported and imported, the results from the runs produce pretty results ready to be shared and it uses seeds to generate the shuffle, so seeds can be shared and multiple users using the app and the same tasks can play the same seeded run.

The app was created to fullfill one crazy idea: one day I was struggling to get myself going for a very busy day when everything was important and urgent, in one small break I pulled a deck of cards and started to fiddle with them, thinking that I had so many thing that I could assign a task to each card, shuffle them and just pull one and start working on that... and then with the help of Claude I turned the idea into this pet project app.

---

## Tech Stack

![Python](https://img.shields.io/badge/Python-3.9+-3776AB?style=flat&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.x-000000?style=flat&logo=flask)
![SQLite](https://img.shields.io/badge/SQLite-embedded-003B57?style=flat&logo=sqlite)
![Bootstrap](https://img.shields.io/badge/Bootstrap-5-7952B3?style=flat&logo=bootstrap&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-ready-2496ED?style=flat&logo=docker&logoColor=white)
![OpenAI](https://img.shields.io/badge/OpenAI-optional-412991?style=flat&logo=openai)
![Ollama](https://img.shields.io/badge/Ollama-local_AI-black?style=flat)

---

## How It Works

The app loads a 56-card runtime deck (52 standard cards + 4 jokers) shuffled on every game start. Players draw cards one at a time, complete the task shown, and earn points per suit. When a suit's score crosses a configurable threshold, its prize page unlocks.

All content — card tasks, point values, prize definitions, and AI settings — is editable through the app's admin pages. No config file editing required after initial setup.

---

## Features

### Gameplay
- Draw-and-complete card flow with a 56-card shuffled deck
- Per-suit score tracking: ♥ Hearts · ♦ Diamonds · ♣ Clubs · ♠ Spades · Jokers
- Prize pages unlock when suit scores cross a configurable threshold
- Joker cards (worth 10 pts each) trigger a special starry background on draw
- **Daily shared seed** — one seed is generated per calendar day and shared across all users, so everyone plays the same shuffle
- Save session snapshots to SQLite and share a results link with others
- Top-3 Hall of Fame podium on the results page

### Configuration (no code changes needed)
- Edit card tasks and point values via `/modify`
- Edit app title and username via `/modify-app`
- Manage per-suit prizes (title, description, image upload) via `/modify-prizes`
- All settings persisted to SQLite config and `decks.xml`
- **Export Deck** (`/export-deck`) — download a `deck.xml` snapshot of all card tasks, prizes, joker tasks, and today's seed
- **Import Deck** (`/import-deck`) — upload a previously exported `deck.xml` to restore tasks, prizes, and seed; deck is reshuffled immediately on import

### AI Joker Tasks
- Generate fresh joker tasks on every deck start using **OpenAI** or a local **Ollama** instance
- Configure the AI provider live via `/ai` — no restart needed
- Avoid-repetition logic: previously generated tasks are passed back as exclusions so tasks stay fresh week to week
- Falls back to manual tasks silently if AI is unavailable

### UI & Auth
- Dark/light theme toggle persisted across sessions
- Fixed **bottom bar** shows live suit score badges, card counter, and current seed on the same line
- Suit icons in the mobile navbar are invisible until the first prize threshold is reached, then follow color/glow rules
- Share button (↗ share) appears in the bottom bar as soon as at least one card has been completed
- Login-protected — all routes require an authenticated session
- Bootstrap and Inter font served locally (no CDN dependency at runtime)

---

## Routes

| Route | Purpose |
| --- | --- |
| `/login` | Sign-in page — public |
| `/logout` | Signs the current user out |
| `/` | Main game screen |
| `/draw` | Draw the next card |
| `/complete` | Mark card complete and add points |
| `/reset` | Reset the session deck and scores |
| `/seed` | Reset deck with a specific seed |
| `/modify` | Edit card tasks, point values, Joker tasks / AI toggle |
| `/modify-app` | Edit app title and active username |
| `/modify-prizes` | Edit per-suit success text, prize titles, descriptions, images |
| `/ai` | Configure AI provider (OpenAI or Ollama) |
| `/save` | Save session snapshot to SQLite |
| `/results` | Review saved sessions and Hall of Fame |
| `/share/<id>` | Public share page for a saved session |
| `/export-deck` | Export current deck config as `deck.xml` |
| `/export-deck/download` | Download the generated XML file |
| `/import-deck` | Upload and apply a `deck.xml` export |
| `/import-deck/apply` | Validate and apply an uploaded XML |
| `/prize/<suit>` | Unlocked prize overview for a suit |
| `/prize/<suit>/<slot>` | Individual prize detail page |
| `/nuclear` | Full database reset (standalone page) |
| `/cookies-disclaimer` | Cookie policy page |

---

## Architecture Overview

### High-level layers

```
┌─────────────────────────────────────────────────────────────┐
│  Browser                                                     │
│  HTML / CSS / Vanilla JS  (Bootstrap 5 + Inter, local CDN)  │
│  Theme persistence via localStorage                         │
└────────────────────┬────────────────────────────────────────┘
                     │ HTTP — page loads, fetch() API calls
                     ▼
┌─────────────────────────────────────────────────────────────┐
│  Flask  (app.py — single file)                               │
│  ├── Auth middleware  (bcrypt via werkzeug.security)         │
│  ├── Route handlers   (draw, complete, reset, save, …)       │
│  ├── XML helpers      (load_base_cards, load_prizes, …)      │
│  ├── Session manager  (server-signed cookie)                 │
│  └── AI helper        (OpenAI / Ollama — optional)           │
└───────┬─────────────────────────┬───────────────────────────┘
        │                         │
        ▼                         ▼
┌───────────────┐      ┌──────────────────────┐
│  SQLite        │      │  XML files            │
│  results.db    │      │  decks.xml            │
│  ├── users     │      │  appvalues.xml         │
│  ├── results   │      └──────────────────────┘
│  ├── config    │
│  └── seeds     │
└───────────────┘
```

### Request / response cycle

```
Browser                          Flask (app.py)                   Storage
   │                                   │                              │
   │── GET /  ──────────────────────►  │ ensure_deck()                │
   │                                   │── read session['deck'] ────► │
   │                                   │── read scores, seed ───────► │
   │◄── render index.html ─────────── │                              │
   │   (card state restored from       │                              │
   │    session on page load)          │                              │
   │                                   │                              │
   │── GET /draw  (fetch) ──────────► │ pop card from session['deck']│
   │                                   │ save session['current_card'] │
   │◄── JSON { card data } ────────── │                              │
   │   (JS renders card + lock deck)   │                              │
   │                                   │                              │
   │── POST /complete (fetch) ──────► │ update session['scores']     │
   │                                   │ clear session['current_card']│
   │                                   │ append session['completed_cards']
   │◄── JSON { updated scores } ───── │                              │
   │   (JS updates badges + unlocks)   │                              │
   │                                   │                              │
   │── POST /save  (fetch) ─────────► │ write row to results.db ───► │
   │◄── JSON { share_id } ─────────── │                              │
```

### Flask session keys

The server-signed cookie holds all in-progress run state, so navigating between pages (e.g. to a prize page and back) never loses progress:

| Key | Type | Purpose |
| --- | --- | --- |
| `deck` | `list[str]` | Remaining card IDs in draw order |
| `current_card` | `dict \| None` | Card drawn but not yet completed |
| `scores` | `dict[str, int]` | Accumulated points per suit |
| `completed_cards` | `list[str]` | Card IDs completed this run |
| `seed` | `str` | Shuffle seed (daily shared or custom) |
| `logged_in` | `bool` | Auth gate for all non-public routes |
| `username` | `str` | Active player display name |

### Data layer

```
decks.xml ──────────────► load_base_cards()  ─► 52 card dicts
                                                   │
                                          init_deck() adds 4 jokers,
                                          shuffles with random.Random(seed),
                                          writes ordered IDs to session['deck']

appvalues.xml ──────────► load_prizes()     ─► prize thresholds, titles,
                           load_app_config()    descriptions, AI settings

results.db
  seeds    ──────────────► get_or_create_daily_seed()  (one per calendar day)
  results  ──────────────► /save → /results → /share/<id>
  users    ──────────────► bcrypt auth + total-score hall of fame
  config   ──────────────► prize and app settings mirror (written by /modify-*)
```

### AI Joker task generation

```
init_deck()
    │
    ├── AI enabled? ──No──► use manual joker_tasks from appvalues.xml
    │
    └──Yes──► call generate_joker_tasks(provider, existing_tasks)
                  │
                  ├── provider == "openai" ──► OpenAI Chat Completions API
                  └── provider == "ollama" ──► local Ollama HTTP API
                                               (http://localhost:11434)
              Returns 4 unique task strings, passed to session alongside
              the shuffled deck. Previously used tasks sent as exclusions
              to avoid repetition across runs.
```

### Front-end state machine (index.html)

The game UI is driven by a small set of JS state variables and helper functions — no framework, no build step:

```
State:  cardPending · deckExhausted · currentSuit/Pts/CardId · completedCards

Events:
  click #deck  ──► drawCard()   ──fetch /draw──►  renderCard() + renderInfo()
                                                   lockDeck() + showCompleteBtn()
                                                   cardPending = true

  click #complete-btn ──► completeCard() ──fetch /complete──► updateScores()
                                                               unlockDeck()
                                                               cardPending = false

  page load  ──► restorePendingCard()  (reads {{ current_card|tojson }} injected
                                        by Flask; re-renders card if a draw was
                                        in progress before navigation)
```

---

## Running Locally

**Requirements:** Python 3.9+, pip, (optional) [Ollama](https://ollama.com) for local AI

```bash
git clone <repo-url>
cd 4columns
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000) and sign in:
- **Username:** `testuser`
- **Password:** `changeme` (or the value of `ADMIN_PASSWORD`)

### Environment Variables

All optional for local dev:

| Variable | Purpose | Default |
| --- | --- | --- |
| `SECRET_KEY` | Flask session signing key | insecure fallback — change in production |
| `ADMIN_PASSWORD` | Login password | `changeme` — change before deploying |
| `OPENAI_API_KEY` | Enables OpenAI Joker task generation | _(disabled)_ |
| `OPENAI_MODEL` | OpenAI model for Joker tasks | `gpt-4.1-mini` |
| `LOG_LEVEL` | Gunicorn log verbosity | `info` |

---

## Deploying with Docker

The app ships with a `Dockerfile` and `docker-compose.yml` for production deployment on any Linux host.

```bash
# 1. Clone the project
git clone <repo-url>
cd 4columns

# 2. Generate a strong secret key
python3 -c "import secrets; print(secrets.token_hex(32))"

# 3. Create a .env file (never commit this)
echo "SECRET_KEY=<paste-key-here>" > .env
echo "ADMIN_PASSWORD=<your-login-password>" >> .env
# Optional:
echo "OPENAI_API_KEY=sk-..." >> .env

# 4. Build and start
docker compose up -d --build

# 5. Check logs
docker compose logs -f
```

App is available at `http://<server-ip>:8000`.

### What Persists

All runtime data is stored in `./instance/` on the host (bind-mounted into the container), so it survives rebuilds and restarts:

| File | Purpose |
| --- | --- |
| `decks.xml` | Card tasks and point values |
| `appvalues.xml` | App config, AI settings, prize metadata |
| `results.db` | SQLite sessions and user data |
| `prize_images/` | Uploaded prize artwork |

### Reverse Proxy (HTTPS)

```nginx
location / {
    proxy_pass         http://127.0.0.1:8000;
    proxy_set_header   Host $host;
    proxy_set_header   X-Real-IP $remote_addr;
    proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header   X-Forwarded-Proto $scheme;
}
```

### Useful Docker Commands

```bash
docker compose up -d --build      # rebuild and restart
docker compose down               # stop and remove container
docker compose logs -f            # tail logs
docker compose exec cardmaze sh   # shell inside container
```

---

## Project Structure

```text
4columns/
├── app.py                      # Flask app — routes, XML helpers, DB logic, auth, AI
├── decks.xml                   # 52 base cards with tasks and point values
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── docker-entrypoint.sh        # Seeds /instance on first run, starts Gunicorn
├── gunicorn.conf.py            # 1 worker, 4 threads, port 8000
├── instance/                   # Runtime data (gitignored, Docker volume)
│   ├── appvalues.xml
│   ├── results.db
│   ├── decks.xml
│   └── prize_images/
├── static/
│   ├── css/navbar.css
│   ├── js/theme.js             # Dark/light theme persistence
│   ├── images/
│   └── vendor/                 # Bootstrap 5 + Inter (local, no CDN)
└── templates/
    ├── _navbar.html            # Shared navbar partial
    ├── index.html              # Main game screen
    ├── login.html
    ├── modify.html             # Deck + Joker task editor
    ├── modify_app.html
    ├── modify_prizes.html
    ├── ai.html                 # AI provider settings
    ├── results.html            # Sessions, podium, card chips
    ├── share.html              # Public share page for a saved session
    ├── export_deck.html        # Export deck configuration
    ├── import_deck.html        # Import deck configuration
    ├── prize.html / prize_detail.html
    ├── cookies_disclaimer.html     # Cookie policy page
    └── nuclear.html
```

---

## Data Files

### `decks.xml` — card schema

```xml
<card id="Ahearts">
  <n>Ace of Hearts</n>
  <maze>hearts</maze>
  <task>Complete this task to earn points.</task>
  <points>14</points>
</card>
```

Rules: card IDs must be unique, `<maze>` must be one of `hearts/diamonds/clubs/spades`, `<points>` must be numeric. The base deck stays at 52 cards — jokers are added at runtime.

### `appvalues.xml` — app config schema

Drives: app title, active username, prize thresholds, per-suit success copy, prize metadata, AI provider selection, and the current Joker task rotation list.

### `results.db` — SQLite database

Tables: `users` (credentials and total score), `results` (saved session snapshots with scores and completed card list), `config` (prizes and app settings), `seeds` (one shared seed per calendar day).

### Daily seed

On each new calendar day the app generates a fresh 8-character alphanumeric seed and stores it in the `seeds` table. All users who play that day share the same shuffle order. The seed is shown in the bottom bar of the game screen.

### Export / Import

The export endpoint (`/export-deck/download`) produces a `deck.xml` file containing all card tasks, prize titles and descriptions, joker tasks, and today's seed. The import endpoint (`/import-deck/apply`) validates the file structure, applies the changes, and immediately reshuffles the deck with the imported seed.

---

## Authentication

All routes except `/login` require an authenticated session. Passwords are stored as bcrypt hashes via `werkzeug.security`.

| Field | Default | How to change |
| --- | --- | --- |
| Username | `testuser` | `/modify-app` |
| Password | `changeme` | `ADMIN_PASSWORD` env var |

---

## License

MIT
