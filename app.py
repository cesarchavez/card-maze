from flask import Flask, render_template, jsonify, session, request, redirect, url_for, flash, make_response
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import xml.etree.ElementTree as ET
import random
import secrets
import re
import os
import string
import sqlite3
import json
from datetime import datetime, timedelta
from urllib import error as urlerror
from urllib import request as urlrequest

try:
    import ollama as _ollama_lib
    OLLAMA_AVAILABLE = True
except ImportError:
    OLLAMA_AVAILABLE = False

app = Flask(__name__)

# ── Secret key — fail loudly if using the insecure fallback in production ─────
_SECRET_KEY = os.getenv('SECRET_KEY', '')
if not _SECRET_KEY:
    import warnings
    _SECRET_KEY = secrets.token_hex(32)   # random per-process key (dev only)
    warnings.warn(
        'SECRET_KEY is not set — a random key is being used. Sessions will be '
        'invalidated on every restart. Set SECRET_KEY in your environment.',
        RuntimeWarning, stacklevel=1,
    )
app.secret_key = _SECRET_KEY

# ── Session cookie hardening ───────────────────────────────────────────────────
app.config['SESSION_COOKIE_HTTPONLY']  = True          # no JS access to cookie
app.config['SESSION_COOKIE_SAMESITE']  = 'Lax'         # CSRF mitigation
app.config['SESSION_COOKIE_SECURE']    = os.getenv('HTTPS', '0') == '1'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)
app.config['MAX_CONTENT_LENGTH']       = 5 * 1024 * 1024   # 5 MB upload limit

# ── Auth ──────────────────────────────────────────────────────────────────────

login_manager = LoginManager(app)
login_manager.login_view = 'login'

ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', '')
if not ADMIN_PASSWORD:
    import warnings
    ADMIN_PASSWORD = 'changeme'
    warnings.warn(
        'ADMIN_PASSWORD is not set — using insecure default "changeme". '
        'Set ADMIN_PASSWORD in your environment before deploying.',
        RuntimeWarning, stacklevel=1,
    )


class User(UserMixin):
    def __init__(self, user_id, username):
        self.id       = user_id
        self.username = username


@login_manager.user_loader
def load_user(user_id):
    conn = get_db()
    row  = conn.execute('SELECT id, username FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    if row is None:
        return None
    return User(row['id'], row['username'])

XML_PATH = os.path.join(os.path.dirname(__file__), 'decks.xml')
APP_XML_PATH = os.path.join(os.path.dirname(__file__), 'appvalues.xml')
DB_PATH  = os.path.join(os.path.dirname(__file__), 'results.db')
PRIZES_IMAGE_DIR = os.path.join(os.path.dirname(__file__), 'static', 'prize_images')
ALLOWED_IMAGE_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}
SECOND_PRIZE_THRESHOLD = 91
TOTAL_CARD_COUNT = 56
JOKER_POINTS = 10
JOKER_COUNT = 4
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '').strip()
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-4.1-mini').strip() or 'gpt-4.1-mini'

SUIT_META = {
    'hearts':   {'symbol': '♥', 'label': 'Hearts',   'red': True},
    'diamonds': {'symbol': '♦', 'label': 'Diamonds', 'red': True},
    'clubs':    {'symbol': '♣', 'label': 'Clubs',    'red': False},
    'spades':   {'symbol': '♠', 'label': 'Spades',   'red': False},
}
TRACK_META = dict(SUIT_META, joker={'symbol': '★', 'label': 'Joker', 'red': False})
BASE_SUIT_KEYS = tuple(SUIT_META.keys())
TRACK_KEYS = tuple(TRACK_META.keys())

EMPTY_SCORES = {key: 0 for key in TRACK_KEYS}
JOKER_PLACEHOLDER_TASKS = [
    'The joker bends the rules. Invent a twist that changes the next turn in a surprising but fair way.',
    'Channel wildcard energy. Perform a bold creative challenge before drawing your next card.',
    'The joker rewards improvisation. Solve a spontaneous mini-task chosen by the table.',
    'Lucky chaos. Create a fast side quest and complete it before returning to the main deck.',
]
DEFAULT_APP_CONFIG = {
    'title': 'Card Maze',
    'username': 'testuser',
    'prize_threshold': 40,
    'prize_messages': {
        'hearts': 'You conquered the Hearts deck. Claim your reward and keep going.',
        'diamonds': 'You conquered the Diamonds deck. Claim your reward and keep going.',
        'clubs': 'You conquered the Clubs deck. Claim your reward and keep going.',
        'spades': 'You conquered the Spades deck. Claim your reward and keep going.',
        'joker': 'You conquered the Joker deck. Claim your reward and keep going.',
    },
    'joker_ai_enabled': False,
    'joker_tasks': list(JOKER_PLACEHOLDER_TASKS),
    'joker_ai_provider': 'openai',         # 'openai' | 'ollama'
    'ollama_host':  'http://localhost:11434',
    'ollama_model': 'gemma3',
}


# ── Database helpers ─────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


def init_db():
    """Create tables and seed the default user if they don't exist."""
    conn = get_db()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY,
            username      TEXT    NOT NULL UNIQUE,
            totalscore    INTEGER NOT NULL DEFAULT 0,
            password_hash TEXT,
            email         TEXT
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS results (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT    NOT NULL,
            date     TEXT    NOT NULL,
            result   TEXT    NOT NULL
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS config (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS seeds (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            seed TEXT    NOT NULL,
            date TEXT    NOT NULL UNIQUE
        )
    ''')
    # Migrate existing databases: add columns if absent
    try:
        conn.execute('ALTER TABLE users ADD COLUMN password_hash TEXT')
    except Exception:
        pass  # Column already exists — safe to ignore
    try:
        conn.execute('ALTER TABLE users ADD COLUMN email TEXT')
    except Exception:
        pass  # Column already exists — safe to ignore
    conn.commit()
    conn.close()

    # Load config after tables exist (triggers XML migration if needed)
    config = load_app_config()
    username = config['username']

    conn = get_db()
    # Seed the default user only when the table is fresh
    conn.execute('''
        INSERT OR IGNORE INTO users (id, username, totalscore)
        VALUES (1, ?, 0)
    ''', (username,))

    # Set the default password for any user that has none yet
    conn.execute(
        'UPDATE users SET password_hash = ? WHERE password_hash IS NULL',
        (generate_password_hash(ADMIN_PASSWORD),)
    )
    # Set email for testuser if not set
    conn.execute(
        "UPDATE users SET email = 'example@example.com' WHERE username = 'testuser' AND email IS NULL"
    )

    sync_active_user(conn, username)
    conn.commit()
    conn.close()


def reset_app_database():
    """Clear results and users, then recreate the default user row."""
    conn = get_db()
    conn.execute('DELETE FROM results')
    conn.execute('DELETE FROM users')
    conn.execute('DELETE FROM sqlite_sequence WHERE name IN (?, ?)', ('results', 'users'))
    conn.execute(
        "INSERT INTO users (id, username, totalscore, email) VALUES (1, 'testuser', 0, 'example@example.com')"
    )
    conn.commit()
    conn.close()


# ── XML helpers ─────────────────────────────────────────────────────────────

def _load_app_config_from_xml():
    """Read app config from the legacy appvalues.xml (used only for one-time migration)."""
    tree = ET.parse(APP_XML_PATH)
    root = tree.getroot()

    def read_text(path, fallback=''):
        node = root.find(path)
        if node is None or node.text is None:
            return fallback
        return node.text.strip()

    prize_messages = {}
    for suit in BASE_SUIT_KEYS:
        prize_messages[suit] = read_text(
            f'prize_messages/{suit}',
            DEFAULT_APP_CONFIG['prize_messages'][suit]
        )
    prize_messages['joker'] = DEFAULT_APP_CONFIG['prize_messages']['joker']

    try:
        prize_threshold = int(read_text('prize_threshold', str(DEFAULT_APP_CONFIG['prize_threshold'])))
    except ValueError:
        prize_threshold = DEFAULT_APP_CONFIG['prize_threshold']

    joker_ai_enabled = read_text('joker_ai_enabled', 'false').lower() == 'true'
    joker_tasks = []
    for i in range(1, JOKER_COUNT + 1):
        task = read_text(f'joker_tasks/task_{i}', JOKER_PLACEHOLDER_TASKS[i - 1])
        joker_tasks.append(task or JOKER_PLACEHOLDER_TASKS[i - 1])

    return {
        'title':            read_text('title',    DEFAULT_APP_CONFIG['title'])    or DEFAULT_APP_CONFIG['title'],
        'username':         read_text('username', DEFAULT_APP_CONFIG['username']) or DEFAULT_APP_CONFIG['username'],
        'prize_threshold':  prize_threshold,
        'prize_messages':   prize_messages,
        'joker_ai_enabled': joker_ai_enabled,
        'joker_tasks':      joker_tasks,
        'joker_ai_provider': read_text('joker_ai_provider', DEFAULT_APP_CONFIG['joker_ai_provider']),
        'ollama_host':       read_text('ollama_host',  DEFAULT_APP_CONFIG['ollama_host']),
        'ollama_model':      read_text('ollama_model', DEFAULT_APP_CONFIG['ollama_model']),
    }


def load_app_config():
    """Load app config from the database, migrating from XML on first run."""
    conn = get_db()
    row = conn.execute("SELECT value FROM config WHERE key = 'app_config'").fetchone()
    conn.close()
    if row:
        config = dict(DEFAULT_APP_CONFIG)
        config.update(json.loads(row['value']))
        return config
    # One-time migration from XML → DB
    if os.path.exists(APP_XML_PATH):
        config = _load_app_config_from_xml()
    else:
        config = dict(DEFAULT_APP_CONFIG)
    save_app_config(config)
    return config


def save_app_config(config):
    """Persist app config to the database (concurrent-write safe)."""
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('app_config', ?)",
        (json.dumps(config),)
    )
    conn.commit()
    conn.close()


def ensure_app_config_file():
    """Kept for compatibility — ensures config exists in the DB."""
    load_app_config()


def allowed_image(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXT


def _prize_defaults():
    return {suit: [{'title': '', 'image': '', 'description': ''} for _ in range(2)]
            for suit in BASE_SUIT_KEYS}


def prize_placeholders(suit):
    label = TRACK_META[suit]['label']
    source_hint = 'Add a custom title, image, and description from the prize editor.' if suit in BASE_SUIT_KEYS else 'Add custom Joker prize content in the templates or app configuration.'
    return [
        {
            'title': f'{label} Prize 1',
            'description': f'A default {label.lower()} reward is ready here. {source_hint}',
        },
        {
            'title': f'{label} Prize 2',
            'description': f'This bonus {label.lower()} reward unlocks at {SECOND_PRIZE_THRESHOLD} points. {source_hint}',
        },
    ]


def decorate_prizes(suit, prizes):
    placeholders = prize_placeholders(suit)
    decorated = []
    for index, prize in enumerate(prizes, start=1):
        fallback = placeholders[index - 1]
        decorated.append({
            'slot': index,
            'title': prize.get('title') or fallback['title'],
            'description': prize.get('description') or fallback['description'],
            'image': prize.get('image', ''),
            'has_custom_title': bool(prize.get('title')),
            'has_custom_description': bool(prize.get('description')),
            'has_image': bool(prize.get('image')),
        })
    return decorated


def prizes_for_track(suit):
    prizes = load_prizes()
    return prizes.get(suit, [{'title': '', 'image': '', 'description': ''} for _ in range(2)])


def visible_prize_count(suit, score):
    if suit == 'joker':
        return 1
    return 2 if score >= SECOND_PRIZE_THRESHOLD else 1


def _load_prizes_from_xml():
    """Read prizes from the legacy appvalues.xml (used only for one-time migration)."""
    tree = ET.parse(APP_XML_PATH)
    root = tree.getroot()
    prizes = _prize_defaults()
    prizes_node = root.find('prizes')
    if prizes_node is None:
        return prizes
    for suit in BASE_SUIT_KEYS:
        suit_node = prizes_node.find(suit)
        if suit_node is None:
            continue
        for i in range(1, 3):
            p = suit_node.find(f'prize[@id="{i}"]')
            if p is None:
                continue
            entry = {}
            for field in ('title', 'image', 'description'):
                node = p.find(field)
                entry[field] = (node.text or '') if node is not None else ''
            prizes[suit][i - 1] = entry
    return prizes


def load_prizes():
    """Return {suit: [{title, image, description}, ...], ...} for 2 prizes per suit."""
    conn = get_db()
    row = conn.execute("SELECT value FROM config WHERE key = 'prizes'").fetchone()
    conn.close()
    if row:
        stored = json.loads(row['value'])
        prizes = _prize_defaults()
        prizes.update(stored)
        return prizes
    # One-time migration from XML → DB
    if os.path.exists(APP_XML_PATH):
        prizes = _load_prizes_from_xml()
    else:
        prizes = _prize_defaults()
    save_prizes_data(prizes)
    return prizes


def save_prizes_data(prizes):
    """Persist prize configuration to the database (concurrent-write safe)."""
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('prizes', ?)",
        (json.dumps(prizes),)
    )
    conn.commit()
    conn.close()


def sync_active_user(conn, username):
    row = conn.execute('SELECT username FROM users WHERE id = 1').fetchone()
    if row is None:
        conn.execute(
            'INSERT INTO users (id, username, totalscore) VALUES (1, ?, 0)',
            (username,)
        )
        return

    current_username = row['username']
    if current_username == username:
        return

    existing = conn.execute(
        'SELECT id FROM users WHERE username = ?',
        (username,)
    ).fetchone()
    if existing is None:
        conn.execute(
            'UPDATE users SET username = ? WHERE id = 1',
            (username,)
        )


def load_base_cards():
    """Parse decks.xml and return the editable 52-card base deck keyed by card id."""
    tree = ET.parse(XML_PATH)
    root = tree.getroot()
    cards = {}
    for card in root.findall('card'):
        card_id = card.get('id')
        cards[card_id] = {
            'id':     card_id,
            'name':   card.find('name').text,
            'maze':   card.find('maze').text,
            'task':   card.find('task').text,
            'points': card.find('points').text,
        }
    return cards


def _extract_task_list(parsed):
    """Pull a flat list of task strings out of various JSON shapes models may return."""
    if isinstance(parsed, list):
        items = []
        for item in parsed:
            if isinstance(item, str):
                items.append(item)
            elif isinstance(item, dict):
                for k in ('task', 'description', 'action', 'text', 'content', 'prompt'):
                    if isinstance(item.get(k), str):
                        items.append(item[k])
                        break
        return items
    if isinstance(parsed, dict):
        for key in ('tasks', 'micro_tasks', 'actions', 'items', 'results', 'challenges', 'prompts'):
            val = parsed.get(key)
            if isinstance(val, list):
                return _extract_task_list(val)
    return []


def _generate_tasks_openai():
    """Generate joker tasks via OpenAI (falls back to placeholders on any error)."""
    if not OPENAI_API_KEY:
        return list(JOKER_PLACEHOLDER_TASKS)

    prompt = (
        'Generate exactly four short tabletop task prompts for Joker cards in a fantasy task deck game. '
        'Each task must be one sentence, playable in under two minutes, and feel surprising but safe. '
        'Return JSON only in the shape {"tasks":["...","...","...","..."]}.'
    )
    payload = json.dumps({
        'model': OPENAI_MODEL,
        'messages': [{'role': 'user', 'content': prompt}],
        'temperature': 0.9,
    }).encode('utf-8')
    req = urlrequest.Request(
        'https://api.openai.com/v1/chat/completions',
        data=payload,
        headers={
            'Authorization': f'Bearer {OPENAI_API_KEY}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    try:
        with urlrequest.urlopen(req, timeout=12) as resp:
            body = json.loads(resp.read().decode('utf-8'))
        text = body['choices'][0]['message']['content'].strip()
        if not text:
            raise ValueError('Empty response from OpenAI')
        parsed = json.loads(text)
        tasks = parsed.get('tasks', [])
        if len(tasks) != JOKER_COUNT or not all(isinstance(task, str) and task.strip() for task in tasks):
            raise ValueError('Invalid joker task payload')
        return [task.strip()[:160] for task in tasks]
    except (urlerror.URLError, TimeoutError, ValueError, json.JSONDecodeError, KeyError):
        return list(JOKER_PLACEHOLDER_TASKS)


def _generate_tasks_ollama(avoid_tasks=None):
    """Generate joker tasks via a local Ollama instance (falls back to placeholders on any error)."""
    if not OLLAMA_AVAILABLE:
        return list(JOKER_PLACEHOLDER_TASKS)

    avoid = avoid_tasks or list(JOKER_PLACEHOLDER_TASKS)
    # Summarise tasks to keep the prompt concise (first 80 chars each)
    avoid_str = '; '.join(f'"{t[:80].strip()}"' for t in avoid if t.strip())

    prompt = (
        'Create 4 micro tasks for personal improvement, make the answer short and direct, '
        'return them in json format with a "tasks" key containing an array of 4 strings. '
        f'Avoid using the following 4 actions: {avoid_str}'
    )

    cfg   = load_app_config()
    host  = cfg.get('ollama_host',  DEFAULT_APP_CONFIG['ollama_host'])
    model = cfg.get('ollama_model', DEFAULT_APP_CONFIG['ollama_model'])

    try:
        client   = _ollama_lib.Client(host=host)
        response = client.generate(model=model, prompt=prompt, format='json', stream=False)
        raw      = response.response if hasattr(response, 'response') else response.get('response', '')
        parsed   = json.loads(raw.strip())
        tasks    = _extract_task_list(parsed)
        if len(tasks) != JOKER_COUNT or not all(isinstance(t, str) and t.strip() for t in tasks):
            raise ValueError(f'Expected {JOKER_COUNT} tasks, got {len(tasks)}')
        return [t.strip()[:160] for t in tasks]
    except Exception:
        return list(JOKER_PLACEHOLDER_TASKS)


def generate_joker_tasks():
    """Dispatch to the configured AI provider and return four task strings."""
    config   = load_app_config()
    provider = config.get('joker_ai_provider', 'openai')
    avoid    = config.get('joker_tasks', list(JOKER_PLACEHOLDER_TASKS))

    if provider == 'ollama':
        return _generate_tasks_ollama(avoid)
    return _generate_tasks_openai()


def build_joker_cards(tasks=None):
    tasks = tasks or generate_joker_tasks()
    return {
        f'joker_{index}': {
            'id': f'joker_{index}',
            'name': f'Joker {index}',
            'maze': 'joker',
            'task': tasks[index - 1],
            'points': str(JOKER_POINTS),
            'image': 'joker.png',
            'value': 'J',
        }
        for index in range(1, JOKER_COUNT + 1)
    }


def placeholder_joker_cards():
    return build_joker_cards(list(JOKER_PLACEHOLDER_TASKS))


def get_session_joker_tasks():
    tasks = session.get('joker_tasks')
    if isinstance(tasks, list) and len(tasks) == JOKER_COUNT and all(isinstance(task, str) for task in tasks):
        return tasks
    return list(JOKER_PLACEHOLDER_TASKS)


def load_cards():
    cards = load_base_cards()
    cards.update(build_joker_cards(get_session_joker_tasks()))
    return cards


def resolve_card(card_id):
    cards = load_base_cards()
    if card_id in cards:
        return cards[card_id]
    joker_cards = build_joker_cards(get_session_joker_tasks())
    if card_id in joker_cards:
        return joker_cards[card_id]
    return None


def card_value(card_id):
    """Extract display value (2-10, J, Q, K, A) from a card id."""
    if card_id.startswith('joker_'):
        return 'JOKER'
    m = re.match(r'^(\d+|[JQKA])', card_id)
    return m.group(0) if m else '?'


def load_suits():
    """
    Return an ordered dict of suits, each containing its meta info and
    all 13 cards in XML order, with a display value field added.
    """
    tree = ET.parse(XML_PATH)
    root = tree.getroot()
    suits = {key: dict(meta, cards=[]) for key, meta in SUIT_META.items()}
    for card in root.findall('card'):
        card_id  = card.get('id')
        suit_key = card.find('maze').text.lower()
        if suit_key in suits:
            suits[suit_key]['cards'].append({
                'id':     card_id,
                'name':   card.find('name').text,
                'value':  card_value(card_id),
                'task':   card.find('task').text,
                'points': card.find('points').text,
            })
    return suits


def save_xml(updated_fields: dict):
    """
    updated_fields = {card_id: {'task': '...', 'points': '...'}, ...}
    Writes changes back to decks.xml preserving all other content.
    """
    tree = ET.parse(XML_PATH)
    root = tree.getroot()
    for card in root.findall('card'):
        card_id = card.get('id')
        if card_id in updated_fields:
            card.find('task').text   = updated_fields[card_id]['task']
            card.find('points').text = updated_fields[card_id]['points']
    try:
        ET.indent(tree, space='  ')          # pretty-print (Python ≥ 3.9)
    except AttributeError:
        pass
    tree.write(XML_PATH, encoding='UTF-8', xml_declaration=True)


# ── Deck / session helpers ───────────────────────────────────────────────────

def _generate_seed():  # noqa: E302
    """Generate a cryptographically random 8-character alphanumeric seed."""
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(8))


def get_or_create_daily_seed():
    """Return today's shared seed; generate and store one if none exists for today."""
    today = datetime.now().strftime('%Y-%m-%d')
    conn = get_db()
    row = conn.execute('SELECT seed FROM seeds WHERE date = ?', (today,)).fetchone()
    if row:
        conn.close()
        return row['seed']
    new_seed = _generate_seed()
    conn.execute('INSERT INTO seeds (seed, date) VALUES (?, ?)', (new_seed, today))
    conn.commit()
    conn.close()
    return new_seed


def init_deck(seed=None):
    config = load_app_config()
    if config['joker_ai_enabled']:
        joker_tasks = generate_joker_tasks()
        config['joker_tasks'] = joker_tasks
        save_app_config(config)
    else:
        joker_tasks = config['joker_tasks']
    session['joker_tasks'] = joker_tasks
    all_ids = list(load_base_cards().keys()) + [f'joker_{index}' for index in range(1, JOKER_COUNT + 1)]
    # Use a seeded RNG so shuffle is reproducible; isolate from global random state
    deck_seed = seed if seed is not None else get_or_create_daily_seed()
    random.Random(deck_seed).shuffle(all_ids)
    session['deck']   = all_ids
    session['scores'] = dict(EMPTY_SCORES)
    session['seed']   = deck_seed


def ensure_deck():
    if 'deck' not in session:
        init_deck()
    if 'seed' not in session:
        session['seed'] = get_or_create_daily_seed()
    if 'joker_tasks' not in session:
        config = load_app_config()
        session['joker_tasks'] = (
            generate_joker_tasks() if config['joker_ai_enabled'] else config['joker_tasks']
        )
    if 'scores' not in session:
        session['scores'] = dict(EMPTY_SCORES)
        return
    for key, default in EMPTY_SCORES.items():
        session['scores'].setdefault(key, default)


@app.context_processor
def inject_app_config():
    config = load_app_config()
    return {
        'app_title': config['title'],
        'app_username': config['username'],
        'prize_threshold': config['prize_threshold'],
        'prize_messages': config['prize_messages'],
        'track_meta': TRACK_META,
        'track_keys': TRACK_KEYS,
        'total_cards': TOTAL_CARD_COUNT,
    }


# ── Auth guard ───────────────────────────────────────────────────────────────

# Endpoints that don't require a logged-in user
_PUBLIC_ENDPOINTS = {'login', 'new_user', 'share', 'static'}


@app.before_request
def require_login():
    if current_user.is_authenticated or request.endpoint in _PUBLIC_ENDPOINTS:
        return
    # AJAX / JSON callers get a 401 instead of an HTML redirect
    if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    return redirect(url_for('login', next=request.path))


def _safe_next(target):
    """Return target only if it is a safe relative URL, otherwise fall back to index."""
    if target and target.startswith('/') and not target.startswith('//'):
        return target
    return url_for('index')


# ── Login / Logout routes ─────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        next_url = request.form.get('next', '')

        conn = get_db()
        row  = conn.execute(
            'SELECT id, username, password_hash FROM users WHERE username = ?',
            (username,)
        ).fetchone()
        conn.close()

        if row and row['password_hash'] and check_password_hash(row['password_hash'], password):
            login_user(User(row['id'], row['username']))
            # Reset deck so each login starts with a freshly shuffled deck
            session.pop('deck', None)
            session.pop('scores', None)
            session.pop('joker_tasks', None)
            return redirect(_safe_next(next_url))

        flash('Invalid username or password.', 'danger')
        return render_template('login.html', next=next_url)

    return render_template('login.html', next=request.args.get('next', ''))


@app.route('/logout', methods=['POST'])
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.route('/new-user', methods=['GET', 'POST'])
def new_user():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email    = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()

        if not username or not email or not password:
            flash('All fields are required.', 'danger')
            return render_template('new_user.html')

        conn = get_db()
        existing = conn.execute(
            'SELECT id FROM users WHERE username = ?', (username,)
        ).fetchone()

        if existing:
            conn.close()
            flash('Username already taken. Please choose another.', 'danger')
            return render_template('new_user.html')

        conn.execute(
            'INSERT INTO users (username, totalscore, password_hash, email) VALUES (?, 0, ?, ?)',
            (username, generate_password_hash(password), email)
        )
        conn.commit()
        conn.close()

        flash('Account created! You can now sign in.', 'success')
        return redirect(url_for('login'))

    return render_template('new_user.html')


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route('/ai', methods=['GET', 'POST'])
def ai_page():
    ensure_deck()
    remaining = len(session['deck'])

    if request.method == 'POST':
        cfg = load_app_config()
        cfg['joker_ai_provider'] = request.form.get('joker_ai_provider', 'openai')
        host  = request.form.get('ollama_host',  '').strip()
        model = request.form.get('ollama_model', '').strip()
        cfg['ollama_host']  = host  or DEFAULT_APP_CONFIG['ollama_host']
        cfg['ollama_model'] = model or DEFAULT_APP_CONFIG['ollama_model']
        save_app_config(cfg)
        flash('AI settings saved!', 'success')
        return redirect(url_for('ai_page'))

    config = load_app_config()
    return render_template(
        'ai.html',
        remaining=remaining,
        config=config,
        openai_configured=bool(OPENAI_API_KEY),
        ollama_available=OLLAMA_AVAILABLE,
    )


@app.route('/')
def index():
    ensure_deck()
    remaining       = len(session['deck'])
    scores          = session.get('scores', dict(EMPTY_SCORES))
    seed            = session.get('seed', '')
    current_card    = session.get('current_card')
    completed_cards = session.get('completed_cards', [])
    return render_template(
        'index.html',
        remaining=remaining,
        scores=scores,
        seed=seed,
        current_card=current_card,
        completed_cards=completed_cards,
    )


@app.route('/draw')
def draw():
    ensure_deck()
    deck = session['deck']
    if not deck:
        return jsonify({'empty': True, 'remaining': 0})

    card_id = deck.pop(0)
    session['deck'] = deck

    card = resolve_card(card_id)
    if card is None:
        return jsonify({'error': 'Card not found'}), 404

    # Persist the pending card so navigating away and back restores it
    session['current_card'] = {k: v for k, v in card.items()}
    session.modified = True

    card['remaining'] = len(deck)
    return jsonify(card)


@app.route('/complete', methods=['POST'])
def complete():
    data   = request.get_json(silent=True) or {}
    points = int(data.get('points', 0))
    suit   = data.get('suit', '').lower()

    scores = session.get('scores', dict(EMPTY_SCORES))
    if suit in scores:
        scores[suit] = scores[suit] + points
    session['scores'] = scores

    # Record completed card (full dict) and clear the pending slot
    card = session.pop('current_card', None)
    if card:
        completed = session.get('completed_cards', [])
        completed.append({
            'id':     card.get('id', ''),
            'name':   card.get('name', ''),
            'suit':   (card.get('maze') or '').lower(),
            'points': card.get('points', 0),
            'task':   card.get('task', ''),
        })
        session['completed_cards'] = completed

    session.modified  = True
    return jsonify({'success': True, 'scores': scores})


@app.route('/reset', methods=['POST'])
def reset():
    init_deck()
    session.pop('current_card',    None)
    session.pop('completed_cards', None)
    session.modified = True
    return jsonify({
        'success':   True,
        'remaining': len(session['deck']),
        'scores':    dict(EMPTY_SCORES),
        'seed':      session.get('seed', ''),
    })


@app.route('/seed', methods=['POST'])
def seed_run():
    data = request.get_json(silent=True) or {}
    seed = str(data.get('seed', '')).strip()
    if not seed:
        return jsonify({'success': False, 'error': 'Seed is required'}), 400
    init_deck(seed=seed)
    session.pop('current_card',    None)
    session.pop('completed_cards', None)
    session.modified = True
    return jsonify({
        'success':   True,
        'remaining': len(session['deck']),
        'scores':    dict(EMPTY_SCORES),
        'seed':      session.get('seed', ''),
    })


@app.route('/modify', methods=['GET', 'POST'])
def modify():
    ensure_deck()
    remaining = len(session['deck'])

    if request.method == 'POST':
        # ── Standard card edits → decks.xml ──────────────────────────────────
        all_ids = load_base_cards().keys()
        updated = {}
        for cid in all_ids:
            task   = request.form.get(f'task_{cid}',   '').strip()
            points = request.form.get(f'points_{cid}', '').strip()
            if task or points:
                updated[cid] = {
                    'task':   task   if task   else '',
                    'points': points if points else '0',
                }
        save_xml(updated)

        # ── Joker config → appvalues.xml ──────────────────────────────────────
        cfg               = load_app_config()
        joker_ai_enabled  = request.form.get('joker_ai_enabled') == '1'
        cfg['joker_ai_enabled'] = joker_ai_enabled

        if not joker_ai_enabled:
            # Textareas were enabled, so their values were submitted
            joker_tasks = []
            for i in range(1, JOKER_COUNT + 1):
                task = request.form.get(f'joker_task_{i}', '').strip()
                joker_tasks.append(task or JOKER_PLACEHOLDER_TASKS[i - 1])
            cfg['joker_tasks'] = joker_tasks
        # When AI is on, textareas are disabled (not submitted) — preserve existing tasks
        save_app_config(cfg)

        flash('Deck saved successfully!', 'success')
        return redirect(url_for('modify'))

    suits = load_suits()
    config = load_app_config()
    return render_template(
        'modify.html',
        suits=suits,
        remaining=remaining,
        joker_ai_enabled=config['joker_ai_enabled'],
        joker_tasks=config['joker_tasks'],
        joker_ai_provider=config['joker_ai_provider'],
        ollama_model=config['ollama_model'],
        ollama_available=OLLAMA_AVAILABLE,
        openai_configured=bool(OPENAI_API_KEY),
    )


@app.route('/modify-app', methods=['GET', 'POST'])
def modify_app():
    ensure_deck()
    remaining = len(session['deck'])
    config = load_app_config()

    if request.method == 'POST':
        updated_config = {
            'title': request.form.get('title', '').strip() or DEFAULT_APP_CONFIG['title'],
            'username': config['username'],
            'prize_threshold': config['prize_threshold'],
            # Success messages are now edited on the Prizes page — preserve them unchanged
            'prize_messages': config['prize_messages'],
        }

        save_app_config(updated_config)

        flash('App settings saved successfully!', 'success')
        return redirect(url_for('modify_app'))

    return render_template(
        'modify_app.html',
        remaining=remaining,
        config=config,
        suits=SUIT_META,
    )


@app.route('/modify-prizes', methods=['GET', 'POST'])
def modify_prizes():
    ensure_deck()
    remaining = len(session['deck'])

    if request.method == 'POST':
        current = load_prizes()
        for suit in BASE_SUIT_KEYS:
            for i in range(1, 3):
                title       = request.form.get(f'p_{suit}_{i}_title', '').strip()
                description = request.form.get(f'p_{suit}_{i}_desc',  '').strip()
                img_file    = request.files.get(f'p_{suit}_{i}_image')
                image_name  = current[suit][i - 1].get('image', '')

                if img_file and img_file.filename and allowed_image(img_file.filename):
                    ext        = img_file.filename.rsplit('.', 1)[1].lower()
                    image_name = f'prize_{suit}_{i}.{ext}'
                    os.makedirs(PRIZES_IMAGE_DIR, exist_ok=True)
                    img_file.save(os.path.join(PRIZES_IMAGE_DIR, image_name))
                elif request.form.get(f'p_{suit}_{i}_clear_image') == '1':
                    if image_name:
                        old_path = os.path.join(PRIZES_IMAGE_DIR, image_name)
                        if os.path.exists(old_path):
                            os.remove(old_path)
                    image_name = ''

                current[suit][i - 1] = {
                    'title':       title,
                    'image':       image_name,
                    'description': description,
                }
        save_prizes_data(current)

        # Also persist the per-suit prize-page success messages
        cfg = load_app_config()
        for suit in BASE_SUIT_KEYS:
            fallback = DEFAULT_APP_CONFIG['prize_messages'][suit]
            cfg['prize_messages'][suit] = (
                request.form.get(f'prize_message_{suit}', '').strip() or fallback
            )
        save_app_config(cfg)

        flash('Prizes saved!', 'success')
        return redirect(url_for('modify_prizes'))

    return render_template('modify_prizes.html',
                           remaining=remaining,
                           prizes=load_prizes(),
                           suits=SUIT_META,
                           config=load_app_config())


@app.route('/nuclear', methods=['GET', 'POST'])
def nuclear():
    if request.method == 'POST':
        reset_app_database()
        flash('App database reset. Results cleared and testuser recreated with id 1.', 'success')
        return redirect(url_for('nuclear'))
    return render_template('nuclear.html')


@app.route('/prize/<suit>')
def prize(suit):
    if suit not in TRACK_META:
        return redirect(url_for('index'))
    all_prizes = decorate_prizes(suit, prizes_for_track(suit))
    meta   = TRACK_META[suit]
    scores = session.get('scores', dict(EMPTY_SCORES))
    score  = scores.get(suit, 0)
    visible_count = visible_prize_count(suit, score)
    return render_template(
        'prize.html',
        suit=suit,
        meta=meta,
        score=score,
        prizes=all_prizes[:visible_count],
        second_prize_locked=(suit != 'joker' and score < SECOND_PRIZE_THRESHOLD),
        second_prize_threshold=SECOND_PRIZE_THRESHOLD,
        success_text=load_app_config()['prize_messages'][suit],
    )


@app.route('/prize/<suit>/<int:slot>')
def prize_detail(suit, slot):
    if suit not in TRACK_META or slot not in (1, 2):
        return redirect(url_for('index'))

    meta = TRACK_META[suit]
    scores = session.get('scores', dict(EMPTY_SCORES))
    score = scores.get(suit, 0)
    prizes = decorate_prizes(suit, prizes_for_track(suit))
    if slot > visible_prize_count(suit, score):
        return redirect(url_for('prize', suit=suit))

    return render_template(
        'prize_detail.html',
        suit=suit,
        meta=meta,
        score=score,
        prize=prizes[slot - 1],
    )


@app.route('/save', methods=['POST'])
def save():
    """Persist the current session's scores and completed cards to the DB."""
    data            = request.get_json(silent=True) or {}
    username        = current_user.username
    scores          = data.get('scores',   dict(EMPTY_SCORES))
    completed_cards = data.get('completed_cards', [])

    result_payload = json.dumps({
        'scores':          scores,
        'completed_cards': completed_cards,
        'seed':            session.get('seed', ''),
    }, ensure_ascii=False)

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    try:
        conn = get_db()
        conn.execute(
            'INSERT OR IGNORE INTO users (username, totalscore) VALUES (?, 0)',
            (username,)
        )
        conn.execute(
            'INSERT INTO results (username, date, result) VALUES (?, ?, ?)',
            (username, now, result_payload)
        )
        conn.commit()
        row = conn.execute('SELECT last_insert_rowid() AS id').fetchone()
        saved_id = row['id']
        conn.close()
        return jsonify({'success': True, 'id': saved_id, 'date': now})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/results')
def results_page():
    conn = get_db()
    rows = conn.execute(
        'SELECT id, username, date, result FROM results WHERE username = ? ORDER BY date DESC',
        (current_user.username,)
    ).fetchall()
    conn.close()

    entries = []
    for row in rows:
        raw = row['result']
        parsed = {}
        scores = dict(EMPTY_SCORES)
        completed_cards = []
        try:
            parsed = json.loads(raw)
            pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
            if isinstance(parsed, dict):
                scores.update(parsed.get('scores', {}) or {})
                completed_cards = parsed.get('completed_cards', []) or []
        except Exception:
            pretty = raw
        # Short preview: compact single-line snippet, capped at 120 chars
        preview = raw.replace('\n', ' ')
        if len(preview) > 120:
            preview = preview[:117] + '…'
        total = sum(int(scores.get(track, 0) or 0) for track in TRACK_KEYS)
        entries.append({
            'id':       row['id'],
            'username': row['username'],
            'date':     row['date'],
            'preview':  preview,
            'pretty':   pretty,
            'data':     parsed,
            'scores':   scores,
            'completed_cards': completed_cards,
            'total':    total,
        })

    result_cards = load_base_cards()
    result_cards.update(placeholder_joker_cards())
    return render_template('results.html', entries=entries, cards=result_cards)


@app.route('/share/<int:result_id>')
def share(result_id):
    conn = get_db()
    row = conn.execute(
        'SELECT id, username, date, result FROM results WHERE id = ?',
        (result_id,)
    ).fetchone()
    conn.close()

    if row is None:
        return render_template('share.html', entry=None), 404

    scores = dict(EMPTY_SCORES)
    completed_cards = []
    seed = ''
    try:
        parsed = json.loads(row['result'])
        if isinstance(parsed, dict):
            scores.update(parsed.get('scores', {}) or {})
            completed_cards = parsed.get('completed_cards', []) or []
            seed = parsed.get('seed', '')
    except Exception:
        pass

    total = sum(int(scores.get(track, 0) or 0) for track in TRACK_KEYS)
    entry = {
        'id':              row['id'],
        'username':        row['username'],
        'date':            row['date'],
        'scores':          scores,
        'completed_cards': completed_cards,
        'total':           total,
        'seed':            seed,
    }
    return render_template('share.html', entry=entry, track_meta=TRACK_META)


@app.route('/export-deck')
def export_deck():
    return render_template('export_deck.html')


@app.route('/cookies-disclaimer')
def cookies_disclaimer():
    return render_template('cookies_disclaimer.html')


@app.route('/export-deck/download')
def export_deck_download():
    """Generate and return a deck.xml file with cards, prizes, and today's seed."""
    cards  = load_base_cards()
    prizes = load_prizes()
    config = load_app_config()
    seed   = get_or_create_daily_seed()

    root = ET.Element('deck-export')

    ET.SubElement(root, 'seed').text = seed

    cards_el = ET.SubElement(root, 'cards')
    for card_id, card in cards.items():
        card_el = ET.SubElement(cards_el, 'card', id=card_id)
        ET.SubElement(card_el, 'name').text   = card.get('name', '')
        ET.SubElement(card_el, 'maze').text   = card.get('maze', '')
        ET.SubElement(card_el, 'task').text   = card.get('task', '')
        ET.SubElement(card_el, 'points').text = str(card.get('points', ''))

    prizes_el = ET.SubElement(root, 'prizes')
    for suit, prize_list in prizes.items():
        suit_el = ET.SubElement(prizes_el, suit)
        for i, prize in enumerate(prize_list, start=1):
            prize_el = ET.SubElement(suit_el, 'prize', id=str(i))
            ET.SubElement(prize_el, 'title').text       = prize.get('title', '') or ''
            ET.SubElement(prize_el, 'description').text = prize.get('description', '') or ''

    joker_tasks_el = ET.SubElement(root, 'joker_tasks')
    for i, task in enumerate(config.get('joker_tasks', []), start=1):
        ET.SubElement(joker_tasks_el, f'task_{i}').text = task

    try:
        ET.indent(root, space='  ')
    except AttributeError:
        pass

    xml_bytes = ET.tostring(root, encoding='UTF-8', xml_declaration=True)

    resp = make_response(xml_bytes)
    resp.headers['Content-Type']        = 'application/xml; charset=utf-8'
    resp.headers['Content-Disposition'] = 'attachment; filename="deck.xml"'
    return resp


@app.route('/import-deck')
def import_deck():
    return render_template('import_deck.html')


@app.route('/import-deck/apply', methods=['POST'])
def import_deck_apply():
    """Validate an uploaded deck.xml and apply cards, prizes, and seed."""
    xml_file = request.files.get('xml')
    if not xml_file:
        return jsonify({'success': False, 'error': 'No file uploaded.'}), 400

    # Validate extension (belt-and-suspenders alongside MAX_CONTENT_LENGTH)
    filename = xml_file.filename or ''
    if not filename.lower().endswith('.xml'):
        return jsonify({'success': False, 'error': 'Only .xml files are accepted.'}), 400

    raw = xml_file.read(5 * 1024 * 1024 + 1)   # read up to 5 MB + 1 byte
    if len(raw) > 5 * 1024 * 1024:
        return jsonify({'success': False, 'error': 'File exceeds the 5 MB size limit.'}), 413

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        return jsonify({'success': False, 'error': f'Invalid XML: {e}'}), 400

    # ── Validate structure ────────────────────────────────────────────────────
    if root.tag != 'deck-export':
        return jsonify({'success': False,
                        'error': 'Not a valid deck export — root element must be <deck-export>.'}), 400

    cards_el  = root.find('cards')
    prizes_el = root.find('prizes')
    seed_el   = root.find('seed')

    if cards_el is None:
        return jsonify({'success': False, 'error': 'Missing <cards> section.'}), 400
    if prizes_el is None:
        return jsonify({'success': False, 'error': 'Missing <prizes> section.'}), 400

    imported_cards = cards_el.findall('card')
    if not imported_cards:
        return jsonify({'success': False, 'error': '<cards> section is empty.'}), 400

    # ── Apply cards ───────────────────────────────────────────────────────────
    updated_fields = {}
    for card_el in imported_cards:
        card_id = card_el.get('id')
        task_el   = card_el.find('task')
        points_el = card_el.find('points')
        if card_id and task_el is not None and points_el is not None:
            updated_fields[card_id] = {
                'task':   task_el.text   or '',
                'points': points_el.text or '0',
            }
    if updated_fields:
        save_xml(updated_fields)

    # ── Apply prizes ──────────────────────────────────────────────────────────
    current_prizes = load_prizes()
    for suit in current_prizes:
        suit_el = prizes_el.find(suit)
        if suit_el is None:
            continue
        for i in range(len(current_prizes[suit])):
            prize_el = suit_el.find(f'prize[@id="{i + 1}"]')
            if prize_el is None:
                continue
            title_el = prize_el.find('title')
            desc_el  = prize_el.find('description')
            if title_el is not None:
                current_prizes[suit][i]['title']       = title_el.text or ''
            if desc_el is not None:
                current_prizes[suit][i]['description'] = desc_el.text  or ''
    save_prizes_data(current_prizes)

    # ── Apply seed ────────────────────────────────────────────────────────────
    seed_applied = False
    if seed_el is not None and seed_el.text and seed_el.text.strip():
        new_seed = seed_el.text.strip()
        today    = datetime.now().strftime('%Y-%m-%d')
        conn = get_db()
        conn.execute(
            'INSERT OR REPLACE INTO seeds (seed, date) VALUES (?, ?)',
            (new_seed, today)
        )
        conn.commit()
        conn.close()
        session['seed'] = new_seed
        seed_applied = True

    # ── Apply joker tasks ─────────────────────────────────────────────────────
    joker_tasks_el = root.find('joker_tasks')
    if joker_tasks_el is not None:
        cfg = load_app_config()
        imported_tasks = []
        for i in range(1, 5):
            t_el = joker_tasks_el.find(f'task_{i}')
            if t_el is not None and t_el.text and t_el.text.strip():
                imported_tasks.append(t_el.text.strip())
        if imported_tasks:
            cfg['joker_tasks'] = imported_tasks
            save_app_config(cfg)

    # ── Reset and reshuffle deck with the imported seed ───────────────────────
    import_seed = (seed_el.text.strip()
                   if seed_el is not None and seed_el.text and seed_el.text.strip()
                   else None)
    init_deck(seed=import_seed)

    return jsonify({
        'success':       True,
        'cards_updated': len(updated_fields),
        'seed_applied':  seed_applied,
    })


# Initialise DB on every startup (safe — uses CREATE IF NOT EXISTS)
init_db()

if __name__ == '__main__':
    debug = os.getenv('FLASK_DEBUG', '0') == '1'
    app.run(debug=debug)
