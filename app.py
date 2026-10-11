# -*- coding: utf-8 -*-
"""
Мессенджер «Поговорим» — простой чат для обучения.

Что здесь происходит, по-простому:
  * Все данные (люди и сообщения) лежат в базе данных. На своём компьютере это
    файл chat.db, а на хостинге можно подключить «настоящую» базу данных.
  * Пароли мы НЕ храним в открытом виде, а храним их «отпечаток» (хеш).
  * Когда человек входит, Flask кладёт его номер в «сессию» — это как пропуск,
    который браузер носит с собой и показывает на каждой странице.

Маленький словарик:
  * База данных — «умная таблица», где всё сохраняется.
  * SQLAlchemy — «переводчик». Благодаря ему одна и та же программа работает
    и с файлом chat.db, и с большой базой на хостинге. Менять код не придётся.
"""

import os
import re
from datetime import datetime

from flask import (
    Flask,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import (
    Column,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    and_,
    create_engine,
    func,
    insert,
    or_,
    select,
)
from werkzeug.security import check_password_hash, generate_password_hash

# ---------------------------------------------------------------------------
# 1. НАСТРОЙКИ (можно менять снаружи, на хостинге — через «переменные окружения»)
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Папка для данных (файла базы). На своём компьютере — рядом с программой.
# На хостинге можно указать другую папку настройкой DATA_DIR.
DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)
# Если папки для данных ещё нет — создаём её.
# Без этого база не сможет появиться, и сайт при запуске упадёт (ошибка 502).
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except OSError:
    # не получилось (например, папка недоступна) — работаем рядом с программой
    DATA_DIR = BASE_DIR

DB_PATH = os.path.join(DATA_DIR, "chat.db")
SECRET_PATH = os.path.join(DATA_DIR, "secret_key.txt")

# Адрес базы данных. По умолчанию — файл chat.db (это база SQLite).
# На хостинге можно дать «настоящей» базе свой адрес через DATABASE_URL.
DATABASE_URL = os.environ.get("DATABASE_URL") or "sqlite:///" + DB_PATH.replace("\\", "/")
# Некоторые хостинги дают адрес, начинающийся с postgres:// — приводим к нужному виду.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)


def load_secret_key():
    """
    Ключ для подписи «пропусков» (чтобы вход не сбрасывался).
    1) Если на хостинге задан SECRET_KEY — берём его.
    2) Иначе читаем из файла, а если файла нет — создаём случайный.
    """
    from_env = os.environ.get("SECRET_KEY")
    if from_env:
        return from_env
    if os.path.exists(SECRET_PATH):
        with open(SECRET_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()
    key = os.urandom(32).hex()
    try:
        with open(SECRET_PATH, "w", encoding="utf-8") as f:
            f.write(key)
    except OSError:
        pass  # папка может быть «только для чтения» — тогда ключ будет временным
    return key


app = Flask(__name__)
app.secret_key = load_secret_key()

# ---------------------------------------------------------------------------
# 2. БАЗА ДАННЫХ
# ---------------------------------------------------------------------------
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    # Для файла chat.db разрешаем работу из разных потоков — так надёжнее.
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, future=True, connect_args=connect_args)
metadata = MetaData()

# Таблица «Люди»
users_table = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("username", String(20), nullable=False, unique=True),   # логин
    Column("display_name", String(40), nullable=False),            # имя для показа
    Column("password_hash", Text, nullable=False),                 # отпечаток пароля
    Column("created_at", String(30), nullable=False),
)

# Таблица «Сообщения»
messages_table = Table(
    "messages",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("sender_id", Integer, ForeignKey("users.id"), nullable=False),     # кто
    Column("recipient_id", Integer, ForeignKey("users.id"), nullable=False),  # кому
    Column("body", Text, nullable=False),                                     # текст
    Column("created_at", String(30), nullable=False),                         # когда
)


def init_db():
    """Создаём таблицы, если их ещё нет. Можно вызывать сколько угодно раз."""
    # Пишем в лог, куда сохраняется база — чтобы это было видно в Dokploy → Logs.
    if DATABASE_URL.startswith("sqlite"):
        print("Хранилище данных (файл): {}".format(DB_PATH), flush=True)
    else:
        print("Хранилище данных: PostgreSQL (внешняя база)", flush=True)
    metadata.create_all(engine)


def get_conn():
    """Соединение с базой — по одному на каждую открытую страницу."""
    if "conn" not in g:
        g.conn = engine.connect()
    return g.conn


@app.teardown_appcontext
def close_conn(exception=None):
    """Закрываем соединение, когда страница отдана браузеру."""
    conn = g.pop("conn", None)
    if conn is not None:
        conn.close()


# ---------------------------------------------------------------------------
# 3. НЕБОЛЬШИЕ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ---------------------------------------------------------------------------

# Логин: только латинские буквы, цифры и «_», длина от 3 до 20
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")

# Базовые смайлики для кнопок в чате. Список можно менять как угодно.
EMOJI = [
    "😀", "😂", "😅", "😊", "😍", "😎", "😉", "🤔", "😢", "😭",
    "😡", "😴", "👍", "👎", "❤️", "🔥", "🎉", "🙏", "🥳", "💩",
]


def valid_username(name):
    return bool(USERNAME_RE.match(name))


def format_time(iso_string):
    """Из «2026-10-11T14:05:00» делаем красивое «14:05»."""
    try:
        return datetime.fromisoformat(iso_string).strftime("%H:%M")
    except (ValueError, TypeError):
        return ""


def current_user():
    """Возвращает данные вошедшего человека или None, если никто не вошёл."""
    uid = session.get("user_id")
    if uid is None:
        return None
    conn = get_conn()
    return conn.execute(
        select(users_table).where(users_table.c.id == uid)
    ).mappings().first()


@app.context_processor
def inject_user():
    """Делает current_user доступным во всех шаблонах (в base.html)."""
    return {"current_user": current_user()}


def not_logged_in_redirect():
    """Если человек не вошёл — отправляем его на страницу входа."""
    if "user_id" not in session:
        return redirect(url_for("login"))
    return None


# ---------------------------------------------------------------------------
# 4. СТРАНИЦЫ
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    """Главная: если вошёл — показываем список людей, иначе — вход."""
    if "user_id" in session:
        return redirect(url_for("users"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    """Страница регистрации: тут человек «представляется»."""
    if "user_id" in session:
        return redirect(url_for("users"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        display_name = request.form.get("display_name", "").strip()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")

        # Проверяем всё по очереди и запоминаем одну понятную ошибку.
        error = None
        if not valid_username(username):
            error = "Логин: латинские буквы, цифры и «_», от 3 до 20 символов. Пример: anna_2026"
        elif not (1 <= len(display_name) <= 40):
            error = "Имя для показа: от 1 до 40 символов."
        elif len(password) < 6:
            error = "Пароль: минимум 6 символов."
        elif password != password2:
            error = "Пароли не совпадают."

        if error is None:
            conn = get_conn()
            taken = conn.execute(
                select(users_table.c.id).where(users_table.c.username == username)
            ).first()
            if taken:
                error = "Такой логин уже занят — выбери другой."
            else:
                conn.execute(
                    insert(users_table).values(
                        username=username,
                        display_name=display_name,
                        password_hash=generate_password_hash(password),
                        created_at=datetime.now().isoformat(timespec="seconds"),
                    )
                )
                conn.commit()
                flash("Аккаунт создан! Теперь войди под своим логином. 🎉")
                return redirect(url_for("login"))

        flash(error)  # покажем сообщение об ошибке на странице

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    """Страница входа."""
    if "user_id" in session:
        return redirect(url_for("users"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        conn = get_conn()
        user = conn.execute(
            select(users_table).where(users_table.c.username == username)
        ).mappings().first()

        # Проверяем и логин, и пароль. Сообщение об ошибке одинаковое —
        # так чужие люди не узнают, какие логины вообще существуют.
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Неверный логин или пароль.")
        else:
            session.clear()
            session["user_id"] = user["id"]
            flash("С возвращением, {}!".format(user["display_name"]))
            return redirect(url_for("users"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    """Выход: забываем «пропуск»."""
    session.clear()
    flash("Ты вышел из аккаунта.")
    return redirect(url_for("login"))


@app.route("/users")
def users():
    """Список всех людей, кроме меня — с кем можно поговорить."""
    r = not_logged_in_redirect()
    if r:
        return r

    me = current_user()
    conn = get_conn()
    others = conn.execute(
        select(users_table.c.id, users_table.c.username, users_table.c.display_name)
        .where(users_table.c.id != me["id"])
        .order_by(func.lower(users_table.c.display_name))
    ).mappings().all()
    return render_template("users.html", others=others)


@app.route("/chat/<int:other_id>", methods=["GET", "POST"])
def chat(other_id):
    """Сам чат с выбранным человеком."""
    r = not_logged_in_redirect()
    if r:
        return r

    me = current_user()
    conn = get_conn()
    other = conn.execute(
        select(users_table.c.id, users_table.c.username, users_table.c.display_name)
        .where(users_table.c.id == other_id)
    ).mappings().first()

    # Если такого человека нет (или это я сам) — вернёмся к списку.
    if other is None or other["id"] == me["id"]:
        flash("Такого собеседника нет.")
        return redirect(url_for("users"))

    # Черновик — то, что человек уже набрал, но ещё не отправил.
    # Храним отдельно для каждого собеседника.
    draft_key = "draft_{}".format(other["id"])

    if request.method == "POST":
        raw = request.form.get("body", "")
        body = raw.strip()

        # Нажали смайлик? Тогда просто добавляем его к тексту и возвращаемся,
        # ничего не отправляя. JavaScript для этого не нужен.
        emoji = request.form.get("add_emoji")
        if emoji:
            session[draft_key] = (raw + emoji)[:2000]
            return redirect(url_for("chat", other_id=other["id"]))

        # Иначе — это отправка сообщения.
        if not body:
            flash("Пустое сообщение отправлять не нужно. 🙂")
            session[draft_key] = ""
        elif len(body) > 2000:
            flash("Слишком длинное сообщение (максимум 2000 символов).")
            session[draft_key] = raw[:2000]
        else:
            conn.execute(
                insert(messages_table).values(
                    sender_id=me["id"],
                    recipient_id=other["id"],
                    body=body,
                    created_at=datetime.now().isoformat(timespec="seconds"),
                )
            )
            conn.commit()
            session.pop(draft_key, None)  # текст отправлен — черновик больше не нужен
        # После отправки возвращаемся на эту же страницу (защита от повторной отправки)
        return redirect(url_for("chat", other_id=other["id"]))

    draft = session.get(draft_key, "")
    return render_template("chat.html", other=other, draft=draft, emoji=EMOJI)


def conversation(conn, me_id, other_id):
    """Возвращает переписку двух людей по порядку: старые сверху, новые снизу."""
    rows = conn.execute(
        select(
            messages_table.c.sender_id,
            messages_table.c.body,
            messages_table.c.created_at,
        )
        .where(
            or_(
                and_(messages_table.c.sender_id == me_id,
                     messages_table.c.recipient_id == other_id),
                and_(messages_table.c.sender_id == other_id,
                     messages_table.c.recipient_id == me_id),
            )
        )
        .order_by(messages_table.c.id)
    ).mappings().all()
    return [
        {
            "mine": row["sender_id"] == me_id,
            "body": row["body"],
            "time": format_time(row["created_at"]),
        }
        for row in rows
    ]


@app.route("/messages/<int:other_id>")
def messages(other_id):
    """
    Маленькое «окошко» только с сообщениями. Его показывает рамка (iframe)
    внутри страницы чата. В шаблоне стоит <meta refresh>, поэтому браузер сам
    перезагружает только это окошко каждые несколько секунд. Поле ввода и твой
    набранный текст при этом остаются на месте. JavaScript не нужен.
    """
    r = not_logged_in_redirect()
    if r:
        return r

    me = current_user()
    conn = get_conn()
    other = conn.execute(
        select(users_table.c.id, users_table.c.username, users_table.c.display_name)
        .where(users_table.c.id == other_id)
    ).mappings().first()
    if other is None or other["id"] == me["id"]:
        return redirect(url_for("users"))

    return render_template(
        "chat_messages.html",
        other=other,
        messages=conversation(conn, me["id"], other["id"]),
    )


# ---------------------------------------------------------------------------
# 5. ЗАПУСК
# ---------------------------------------------------------------------------

# Готовим базу сразу — таблицы создаются, если их ещё нет.
# Это нужно и на компьютере, и на хостинге (там программу не «запускают», а подключают).
init_db()

if __name__ == "__main__":
    # Настройки берём из окружения (на хостинге они заданы, дома берём по умолчанию).
    debug = os.environ.get("DEBUG", "1") == "1"
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    print("Мессенджер запущен. Открой в браузере: http://{}:{}".format(host, port))
    # host="127.0.0.1" — доступен только на этом компьютере (безопасно для учёбы).
    # debug=True — при ошибке покажет подробности (удобно учиться).
    app.run(host=host, port=port, debug=debug)
