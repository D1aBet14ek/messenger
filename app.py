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
    delete,
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

# Таблица «Группы» (общие беседы)
groups_table = Table(
    "groups",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(60), nullable=False),                              # название группы
    Column("created_by", Integer, ForeignKey("users.id"), nullable=False),   # кто создал
    Column("created_at", String(30), nullable=False),
)

# Таблица «Участники групп» — кто в какой группе и кем является
group_members_table = Table(
    "group_members",
    metadata,
    Column("group_id", Integer, ForeignKey("groups.id"), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("role", String(10), nullable=False),   # "admin" — админ, "member" — участник
    Column("joined_at", String(30), nullable=False),
)

# Таблица «Сообщения групп»
group_messages_table = Table(
    "group_messages",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("group_id", Integer, ForeignKey("groups.id"), nullable=False),
    Column("sender_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("body", Text, nullable=False),
    Column("created_at", String(30), nullable=False),
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


def load_chat_lists(conn, me_id):
    """Возвращает два списка для страницы чатов: (люди, мои группы)."""
    others = conn.execute(
        select(users_table.c.id, users_table.c.username, users_table.c.display_name)
        .where(users_table.c.id != me_id)
        .order_by(func.lower(users_table.c.display_name))
    ).mappings().all()

    my_groups = conn.execute(
        select(groups_table.c.id, groups_table.c.name)
        .select_from(
            groups_table.join(
                group_members_table, groups_table.c.id == group_members_table.c.group_id
            )
        )
        .where(group_members_table.c.user_id == me_id)
        .order_by(func.lower(groups_table.c.name))
    ).mappings().all()

    return others, my_groups


@app.route("/users")
def users():
    """
    Страница со списком чатов. Сам список показывается в отдельной рамке (iframe),
    поэтому при самообновлении мигает только рамка, а не вся страница.
    """
    r = not_logged_in_redirect()
    if r:
        return r
    return render_template("users.html")


@app.route("/chats-frame")
def chats_frame():
    """Маленькое «окошко» со списком чатов и людей — для самообновляемой рамки."""
    r = not_logged_in_redirect()
    if r:
        return r

    me = current_user()
    others, my_groups = load_chat_lists(get_conn(), me["id"])
    return render_template("chats_list.html", others=others, groups=my_groups)


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
            "sender": None,  # в личном чате имя показывать не нужно
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
# 4b. ГРУППЫ (общие беседы с админами)
# ---------------------------------------------------------------------------


def group_membership(conn, group_id, user_id):
    """Участие человека в группе: возвращает роль («admin»/«member») или None."""
    return conn.execute(
        select(group_members_table.c.role).where(
            and_(
                group_members_table.c.group_id == group_id,
                group_members_table.c.user_id == user_id,
            )
        )
    ).mappings().first()


def group_conversation(conn, group_id, me_id):
    """Сообщения группы по порядку, вместе с именами авторов."""
    rows = conn.execute(
        select(
            group_messages_table.c.sender_id,
            group_messages_table.c.body,
            group_messages_table.c.created_at,
            users_table.c.display_name,
        )
        .select_from(
            group_messages_table.join(
                users_table, group_messages_table.c.sender_id == users_table.c.id
            )
        )
        .where(group_messages_table.c.group_id == group_id)
        .order_by(group_messages_table.c.id)
    ).mappings().all()
    return [
        {
            "mine": row["sender_id"] == me_id,
            "body": row["body"],
            "time": format_time(row["created_at"]),
            "sender": row["display_name"],
        }
        for row in rows
    ]


@app.route("/groups/new", methods=["GET", "POST"])
def new_group():
    """Создание новой группы. Создатель становится администратором."""
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

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        chosen = request.form.getlist("members")  # отмеченные галочками люди

        if not (1 <= len(name) <= 60):
            flash("Название группы: от 1 до 60 символов.")
        else:
            now = datetime.now().isoformat(timespec="seconds")
            result = conn.execute(
                insert(groups_table).values(
                    name=name, created_by=me["id"], created_at=now
                )
            )
            group_id = result.inserted_primary_key[0]

            # Создатель — админ группы
            conn.execute(
                insert(group_members_table).values(
                    group_id=group_id, user_id=me["id"], role="admin", joined_at=now
                )
            )
            # Отмеченные люди — участники
            for raw_id in chosen:
                try:
                    uid = int(raw_id)
                except ValueError:
                    continue
                if uid == me["id"]:
                    continue
                conn.execute(
                    insert(group_members_table).values(
                        group_id=group_id, user_id=uid, role="member", joined_at=now
                    )
                )
            conn.commit()
            flash("Группа «{}» создана!".format(name))
            return redirect(url_for("group_chat", group_id=group_id))

    return render_template("new_group.html", others=others)


@app.route("/groups/<int:group_id>", methods=["GET", "POST"])
def group_chat(group_id):
    """Чат внутри группы."""
    r = not_logged_in_redirect()
    if r:
        return r

    me = current_user()
    conn = get_conn()
    group = conn.execute(
        select(groups_table).where(groups_table.c.id == group_id)
    ).mappings().first()
    membership = group_membership(conn, group_id, me["id"])

    if group is None or membership is None:
        flash("Такой группы нет или ты не её участник.")
        return redirect(url_for("users"))

    draft_key = "gdraft_{}".format(group_id)

    if request.method == "POST":
        raw = request.form.get("body", "")
        body = raw.strip()
        emoji = request.form.get("add_emoji")
        if emoji:
            session[draft_key] = (raw + emoji)[:2000]
            return redirect(url_for("group_chat", group_id=group_id))
        if not body:
            flash("Пустое сообщение отправлять не нужно. 🙂")
            session[draft_key] = ""
        elif len(body) > 2000:
            flash("Слишком длинное сообщение (максимум 2000 символов).")
            session[draft_key] = raw[:2000]
        else:
            conn.execute(
                insert(group_messages_table).values(
                    group_id=group_id,
                    sender_id=me["id"],
                    body=body,
                    created_at=datetime.now().isoformat(timespec="seconds"),
                )
            )
            conn.commit()
            session.pop(draft_key, None)
        return redirect(url_for("group_chat", group_id=group_id))

    # Участники группы
    members = conn.execute(
        select(
            group_members_table.c.user_id,
            group_members_table.c.role,
            users_table.c.display_name,
            users_table.c.username,
        )
        .select_from(
            group_members_table.join(
                users_table, group_members_table.c.user_id == users_table.c.id
            )
        )
        .where(group_members_table.c.group_id == group_id)
        .order_by(group_members_table.c.role, func.lower(users_table.c.display_name))
    ).mappings().all()

    # Люди, которых ещё нет в группе (чтобы админ мог добавить)
    member_ids = {m["user_id"] for m in members}
    everybody = conn.execute(
        select(users_table.c.id, users_table.c.username, users_table.c.display_name)
        .where(users_table.c.id != me["id"])
        .order_by(func.lower(users_table.c.display_name))
    ).mappings().all()
    candidates = [u for u in everybody if u["id"] not in member_ids]

    draft = session.get(draft_key, "")
    return render_template(
        "group_chat.html",
        group=group,
        members=members,
        candidates=candidates,
        draft=draft,
        emoji=EMOJI,
        is_admin=(membership["role"] == "admin"),
    )


@app.route("/group-messages/<int:group_id>")
def group_messages(group_id):
    """Окошко с сообщениями группы (для самообновляемой рамки iframe)."""
    r = not_logged_in_redirect()
    if r:
        return r

    me = current_user()
    conn = get_conn()
    if group_membership(conn, group_id, me["id"]) is None:
        return redirect(url_for("users"))

    return render_template(
        "chat_messages.html",
        messages=group_conversation(conn, group_id, me["id"]),
    )


@app.route("/groups/<int:group_id>/add", methods=["POST"])
def group_add_members(group_id):
    """Админ добавляет в группу новых людей."""
    r = not_logged_in_redirect()
    if r:
        return r

    me = current_user()
    conn = get_conn()
    membership = group_membership(conn, group_id, me["id"])
    if membership is None:
        return redirect(url_for("users"))
    if membership["role"] != "admin":
        flash("Добавлять людей может только админ группы.")
        return redirect(url_for("group_chat", group_id=group_id))

    now = datetime.now().isoformat(timespec="seconds")
    added = 0
    for raw_id in request.form.getlist("members"):
        try:
            uid = int(raw_id)
        except ValueError:
            continue
        if uid == me["id"]:
            continue
        if group_membership(conn, group_id, uid) is None:
            conn.execute(
                insert(group_members_table).values(
                    group_id=group_id, user_id=uid, role="member", joined_at=now
                )
            )
            added += 1
    conn.commit()
    flash("Добавлено людей: {}.".format(added) if added else "Никого не выбрано.")
    return redirect(url_for("group_chat", group_id=group_id))


@app.route("/groups/<int:group_id>/remove/<int:user_id>", methods=["POST"])
def group_remove_member(group_id, user_id):
    """Админ убирает человека из группы."""
    r = not_logged_in_redirect()
    if r:
        return r

    me = current_user()
    conn = get_conn()
    membership = group_membership(conn, group_id, me["id"])
    if membership is None:
        return redirect(url_for("users"))
    if membership["role"] != "admin":
        flash("Убирать людей может только админ группы.")
        return redirect(url_for("group_chat", group_id=group_id))
    if user_id == me["id"]:
        flash("Себя из группы убрать нельзя.")
        return redirect(url_for("group_chat", group_id=group_id))

    conn.execute(
        delete(group_members_table).where(
            and_(
                group_members_table.c.group_id == group_id,
                group_members_table.c.user_id == user_id,
            )
        )
    )
    conn.commit()
    flash("Человек убран из группы.")
    return redirect(url_for("group_chat", group_id=group_id))


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
