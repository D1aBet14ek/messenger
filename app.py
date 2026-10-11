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
from datetime import datetime, timedelta

from flask import (
    Flask,
    Response,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from sqlalchemy import (
    Column,
    ForeignKey,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    and_,
    create_engine,
    delete,
    func,
    insert,
    inspect,
    or_,
    select,
    text,
    update,
)
from werkzeug.security import check_password_hash, generate_password_hash

# ---------------------------------------------------------------------------
# 1. НАСТРОЙКИ (можно менять снаружи, на хостинге — через «переменные окружения»)
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Папка для данных (файла базы). На своём компьютере — рядом с программой.
# На хостинге можно указать другую папку настройкой DATA_DIR.
# Где хранить данные (файл базы).
#   1) Если задано DATA_DIR — берём его.
#   2) Иначе, если подключён постоянный диск /data — используем его автоматически.
#   3) Иначе — папку рядом с программой.
DATA_DIR = os.environ.get("DATA_DIR")
if not DATA_DIR:
    if os.path.ismount("/data"):
        DATA_DIR = "/data"   # постоянный том подключён — данные не пропадут
    else:
        DATA_DIR = BASE_DIR

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
# Аватар не должен быть больше 2 МБ
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
# Вход «запоминается» надолго: закрыл и открыл — ты по-прежнему в аккаунте.
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=365)

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
    Column("username", String(20), nullable=False, unique=True),   # логин (у телефона = цифры)
    Column("phone", String(20)),                                   # номер телефона
    Column("display_name", String(40), nullable=False),            # имя для показа
    Column("password_hash", Text, nullable=False),                 # отпечаток пароля
    Column("created_at", String(30), nullable=False),
    Column("bio", Text),                                           # описание (о себе)
    Column("background", String(20)),                              # выбранный фон чата
    Column("avatar", LargeBinary),                                 # картинка-аватар
    Column("avatar_mime", String(50)),                             # вид картинки (png/jpeg)
)

# Таблица «Дружбы» — заявки и подтверждённые друзья
friendships_table = Table(
    "friendships",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("requester_id", Integer, ForeignKey("users.id"), nullable=False),  # кто пригласил
    Column("addressee_id", Integer, ForeignKey("users.id"), nullable=False),  # кого пригласили
    Column("status", String(10), nullable=False),   # "pending" — заявка, "accepted" — друзья
    Column("created_at", String(30), nullable=False),
)

# Таблица «Прочитано» — id последнего прочитанного сообщения.
# kind = "user" (личный чат) или "group" (группа).
reads_table = Table(
    "reads",
    metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("kind", String(10), primary_key=True),
    Column("peer_id", Integer, primary_key=True),
    Column("last_seen_id", Integer, nullable=False),
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


def ensure_columns():
    """
    Простая «миграция». Если база создана раньше и в таблице users нет новых
    столбцов (bio, avatar, avatar_mime) — добавляем их на месте.
    Работает и с SQLite, и с PostgreSQL.
    """
    insp = inspect(engine)
    if not insp.has_table("users"):
        return
    existing = {col["name"] for col in insp.get_columns("users")}
    # В разных базах данных свой тип для «двоичных данных»
    blob_type = "BYTEA" if engine.dialect.name == "postgresql" else "BLOB"
    needed = {
        "phone": "VARCHAR(20)",
        "bio": "TEXT",
        "background": "VARCHAR(20)",
        "avatar": blob_type,
        "avatar_mime": "VARCHAR(50)",
    }
    missing = {name: ddl for name, ddl in needed.items() if name not in existing}
    if not missing:
        return
    with engine.begin() as conn:
        for name, ddl in missing.items():
            conn.execute(text("ALTER TABLE users ADD COLUMN {} {}".format(name, ddl)))
            print("Создан новый столбец users.{}".format(name), flush=True)


def init_db():
    """Создаём таблицы, если их ещё нет. Можно вызывать сколько угодно раз."""
    # Полезные строки для проверки в Dokploy → Logs.
    if DATABASE_URL.startswith("sqlite"):
        print("Хранилище данных (файл): {}".format(DB_PATH), flush=True)
        # Проверяем, что папка данных — это настоящий постоянный том.
        if DATA_DIR != BASE_DIR and not os.path.ismount(DATA_DIR):
            print(
                "ВНИМАНИЕ: папка {} не является постоянным томом. "
                "Аккаунты будут пропадать при перезапуске!".format(DATA_DIR),
                flush=True,
            )
    else:
        print("Хранилище данных: PostgreSQL (внешняя база)", flush=True)

    if not os.environ.get("SECRET_KEY"):
        print(
            "Подсказка: SECRET_KEY не задан — вход может сбрасываться.",
            flush=True,
        )

    try:
        metadata.create_all(engine)
        ensure_columns()
    except Exception as exc:  # noqa: BLE001 — покажем понятную ошибку в логе
        print("ОШИБКА БАЗЫ ДАННЫХ:", exc, flush=True)
        print(
            "Проверь DATABASE_URL: адрес базы должен быть ВНУТРЕННИМ (Internal), "
            "база — в том же проекте и запущена.",
            flush=True,
        )
        # Специально НЕ падаем: приложение останется доступным, а страница
        # /health покажет, что именно не так. Так проще понять причину.


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

# Варианты фона для чата. Сами цвета и узоры заданы в style.css (классы bg-<имя>).
BACKGROUNDS = [
    "default", "gray", "blue", "green", "pink", "yellow",
    "sky", "sunset", "mint", "dots",
    "stripes", "diag", "cross", "checks", "grid",
    "hearts", "stars", "plus",
]


def valid_username(name):
    return bool(USERNAME_RE.match(name))


def format_time(iso_string):
    """Из «2026-10-11T14:05:00» делаем красивое «14:05»."""
    try:
        return datetime.fromisoformat(iso_string).strftime("%H:%M")
    except (ValueError, TypeError):
        return ""


def normalize_phone(raw):
    """Оставляем только цифры — так номера сравниваются надёжно."""
    return "".join(ch for ch in (raw or "") if ch.isdigit())


def friendship_between(conn, a_id, b_id):
    """Возвращает запись о дружбе между двумя людьми (в любую сторону) или None."""
    return conn.execute(
        select(friendships_table).where(
            or_(
                and_(friendships_table.c.requester_id == a_id,
                     friendships_table.c.addressee_id == b_id),
                and_(friendships_table.c.requester_id == b_id,
                     friendships_table.c.addressee_id == a_id),
            )
        )
    ).mappings().first()


def are_friends(conn, a_id, b_id):
    """True, если два человека уже подтверждённые друзья."""
    f = friendship_between(conn, a_id, b_id)
    return f is not None and f["status"] == "accepted"


def friend_ids(conn, me_id):
    """Множество id моих друзей (подтверждённых)."""
    rows = conn.execute(
        select(friendships_table.c.requester_id, friendships_table.c.addressee_id)
        .where(
            and_(
                friendships_table.c.status == "accepted",
                or_(friendships_table.c.requester_id == me_id,
                    friendships_table.c.addressee_id == me_id),
            )
        )
    ).mappings().all()
    ids = set()
    for r in rows:
        ids.add(r["addressee_id"] if r["requester_id"] == me_id else r["requester_id"])
    return ids


def get_reads(conn, user_id):
    """Что прочитано: {(kind, peer_id): id последнего прочитанного сообщения}."""
    rows = conn.execute(
        select(reads_table).where(reads_table.c.user_id == user_id)
    ).mappings().all()
    return {(r["kind"], r["peer_id"]): r["last_seen_id"] for r in rows}


def mark_read(conn, user_id, kind, peer_id, last_seen_id):
    """Отмечаем, что человек прочитал переписку вплоть до сообщения last_seen_id."""
    where = and_(
        reads_table.c.user_id == user_id,
        reads_table.c.kind == kind,
        reads_table.c.peer_id == peer_id,
    )
    row = conn.execute(
        select(reads_table.c.last_seen_id).where(where)
    ).mappings().first()
    if row is None:
        conn.execute(
            insert(reads_table).values(
                user_id=user_id, kind=kind, peer_id=peer_id, last_seen_id=last_seen_id
            )
        )
        conn.commit()
    elif row["last_seen_id"] < last_seen_id:
        conn.execute(update(reads_table).where(where).values(last_seen_id=last_seen_id))
        conn.commit()


def user_cols():
    """
    Столбцы «профиля» пользователя. Картинку-аватар целиком НЕ тянем,
    только признак has_avatar (есть он или нет) — так быстрее.
    """
    return [
        users_table.c.id,
        users_table.c.username,
        users_table.c.phone,
        users_table.c.display_name,
        users_table.c.bio,
        users_table.c.background,
        users_table.c.avatar.is_not(None).label("has_avatar"),
    ]


def current_user():
    """Возвращает данные вошедшего человека или None, если никто не вошёл."""
    uid = session.get("user_id")
    if uid is None:
        return None
    conn = get_conn()
    return conn.execute(
        select(*user_cols()).where(users_table.c.id == uid)
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


@app.route("/health")
def health():
    """
    Страничка состояния. Открой её, если сайт не работает или «слетают» аккаунты:
    она скажет, доступна ли база и ГДЕ она хранится.
    """
    try:
        conn = get_conn()
        conn.execute(select(users_table.c.id).limit(1))
    except Exception as exc:  # noqa: BLE001
        return "ОШИБКА базы данных: {}".format(exc), 500

    if DATABASE_URL.startswith("sqlite"):
        warn = ""
        if DATA_DIR != BASE_DIR and not os.path.ismount(DATA_DIR):
            warn = "  <-- ВНИМАНИЕ: это НЕ постоянный том, аккаунты слетят!"
        return "OK: база доступна. Файл: {}{}".format(DB_PATH, warn)
    return "OK: база доступна (PostgreSQL — постоянная)"


@app.route("/manifest.webmanifest")
def manifest():
    """«Паспорт» приложения: имя, иконка, цвета — нужен для установки на телефон."""
    return Response(
        render_template("manifest.webmanifest"),
        mimetype="application/manifest+json",
    )


@app.route("/sw.js")
def service_worker():
    """Служебный файл приложения (для установки). На работу мессенджера не влияет."""
    resp = Response(render_template("sw.js"), mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


# Файл программы для ПК и её версия (лежат в папке desktop проекта)
DESKTOP_EXE = os.path.join(BASE_DIR, "desktop", "Pogovorim.exe")
DESKTOP_VERSION_FILE = os.path.join(BASE_DIR, "desktop", "version.txt")


@app.route("/desktop-version")
def desktop_version():
    """Сообщает программе на ПК: какая сейчас версия и откуда скачать новую."""
    version = "1.0"
    try:
        with open(DESKTOP_VERSION_FILE, "r", encoding="utf-8") as f:
            version = f.read().strip() or "1.0"
    except OSError:
        pass
    return jsonify(
        {"version": version, "url": url_for("desktop_download", _external=True)}
    )


@app.route("/desktop-download")
def desktop_download():
    """Отдаёт файл программы для ПК (нужен для автоматического обновления)."""
    if not os.path.exists(DESKTOP_EXE):
        return ("Файл программы не найден", 404)
    return send_file(DESKTOP_EXE, as_attachment=True, download_name="Pogovorim.exe")


@app.route("/register", methods=["GET", "POST"])
def register():
    """Страница регистрации: человек придумывает логин (@имя), как в Telegram."""
    if "user_id" in session:
        return redirect(url_for("users"))

    if request.method == "POST":
        username = request.form.get("username", "").strip().lstrip("@").lower()
        display_name = request.form.get("display_name", "").strip()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")

        # Проверяем всё по очереди и запоминаем одну понятную ошибку.
        error = None
        if not valid_username(username):
            error = "Логин: латинские буквы, цифры и «_», от 3 до 20 символов. Например: anna_2026"
        elif not (1 <= len(display_name) <= 40):
            error = "Имя для показа: от 1 до 40 символов."
        elif len(password) < 6:
            error = "Пароль: минимум 6 символов."
        elif password != password2:
            error = "Пароли не совпадают."

        if error is None:
            conn = get_conn()
            taken = conn.execute(
                select(users_table.c.id).where(
                    func.lower(users_table.c.username) == username
                )
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
    """Страница входа: по логину."""
    if "user_id" in session:
        return redirect(url_for("users"))

    if request.method == "POST":
        username = request.form.get("username", "").strip().lstrip("@").lower()
        password = request.form.get("password", "")

        conn = get_conn()
        user = conn.execute(
            select(users_table).where(
                func.lower(users_table.c.username) == username
            )
        ).mappings().first()

        # Проверяем и логин, и пароль. Сообщение об ошибке одинаковое —
        # так чужие люди не узнают, кто вообще зарегистрирован.
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Неверный логин или пароль.")
        else:
            session.clear()
            session.permanent = True  # вход запоминается надолго
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


@app.route("/profile", methods=["GET", "POST"])
def profile():
    """Редактирование своего профиля: имя, описание, аватар, пароль."""
    r = not_logged_in_redirect()
    if r:
        return r

    me = current_user()
    conn = get_conn()
    user = conn.execute(
        select(users_table).where(users_table.c.id == me["id"])
    ).mappings().first()

    if request.method == "POST":
        display_name = request.form.get("display_name", "").strip()
        bio = request.form.get("bio", "").strip()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")
        avatar_file = request.files.get("avatar")
        remove_avatar = request.form.get("remove_avatar")

        error = None
        if not (1 <= len(display_name) <= 40):
            error = "Имя для показа: от 1 до 40 символов."
        elif len(bio) > 300:
            error = "Описание: не больше 300 символов."
        elif password and len(password) < 6:
            error = "Пароль: минимум 6 символов."
        elif password != password2:
            error = "Пароли не совпадают."

        new_avatar = None
        new_mime = None
        if error is None and avatar_file and avatar_file.filename:
            mime = avatar_file.mimetype or ""
            data = avatar_file.read()
            if not mime.startswith("image/"):
                error = "Аватар должен быть картинкой (png, jpg, gif и т.п.)."
            elif len(data) > 2 * 1024 * 1024:
                error = "Картинка слишком большая (максимум 2 МБ)."
            else:
                new_avatar = data
                new_mime = mime

        if error is None:
            values = {"display_name": display_name, "bio": bio}
            if password:
                values["password_hash"] = generate_password_hash(password)
            if new_avatar is not None:
                values["avatar"] = new_avatar
                values["avatar_mime"] = new_mime
            elif remove_avatar:
                values["avatar"] = None
                values["avatar_mime"] = None
            conn.execute(
                update(users_table).where(users_table.c.id == me["id"]).values(**values)
            )
            conn.commit()
            flash("Профиль сохранён. 👍")
            return redirect(url_for("profile"))

        flash(error)

    user = conn.execute(
        select(users_table).where(users_table.c.id == me["id"])
    ).mappings().first()
    return render_template("profile.html", user=user)


@app.route("/avatar/<int:user_id>")
def avatar(user_id):
    """Отдаёт картинку-аватар человека (или пустой ответ, если картинки нет)."""
    conn = get_conn()
    row = conn.execute(
        select(users_table.c.avatar, users_table.c.avatar_mime)
        .where(users_table.c.id == user_id)
    ).mappings().first()
    if row is None or row["avatar"] is None:
        return ("", 404)
    return Response(bytes(row["avatar"]), mimetype=row["avatar_mime"] or "image/png")


@app.errorhandler(413)
def avatar_too_large(error):
    """Если картинка больше 2 МБ — покажем понятное сообщение."""
    flash("Картинка слишком большая (максимум 2 МБ).")
    return redirect(url_for("profile"))


def load_contacts(conn, me_id):
    """Данные страницы контактов: друзья, группы, входящие и исходящие заявки."""
    reads = get_reads(conn, me_id)

    # Мои группы (с числом непрочитанных)
    group_rows = conn.execute(
        select(groups_table.c.id, groups_table.c.name)
        .select_from(
            groups_table.join(
                group_members_table, groups_table.c.id == group_members_table.c.group_id
            )
        )
        .where(group_members_table.c.user_id == me_id)
        .order_by(func.lower(groups_table.c.name))
    ).mappings().all()
    my_groups = []
    for g in group_rows:
        d = dict(g)
        last = reads.get(("group", d["id"]), 0)
        d["unread"] = conn.execute(
            select(func.count())
            .select_from(group_messages_table)
            .where(
                and_(
                    group_messages_table.c.group_id == d["id"],
                    group_messages_table.c.sender_id != me_id,
                    group_messages_table.c.id > last,
                )
            )
        ).scalar() or 0
        my_groups.append(d)

    # Мои друзья (только подтверждённые!, с числом непрочитанных)
    fids = friend_ids(conn, me_id)
    friends = []
    if fids:
        rows = conn.execute(
            select(*user_cols())
            .where(users_table.c.id.in_(fids))
            .order_by(func.lower(users_table.c.display_name))
        ).mappings().all()
        for u in rows:
            d = dict(u)
            last = reads.get(("user", d["id"]), 0)
            d["unread"] = conn.execute(
                select(func.count())
                .select_from(messages_table)
                .where(
                    and_(
                        messages_table.c.sender_id == d["id"],
                        messages_table.c.recipient_id == me_id,
                        messages_table.c.id > last,
                    )
                )
            ).scalar() or 0
            friends.append(d)

    # Входящие заявки в друзья (мне)
    incoming = conn.execute(
        select(
            friendships_table.c.id,
            users_table.c.id.label("user_id"),
            users_table.c.display_name,
            users_table.c.username,
            users_table.c.phone,
            users_table.c.avatar.is_not(None).label("has_avatar"),
        )
        .select_from(
            friendships_table.join(
                users_table, friendships_table.c.requester_id == users_table.c.id
            )
        )
        .where(
            and_(
                friendships_table.c.addressee_id == me_id,
                friendships_table.c.status == "pending",
            )
        )
        .order_by(func.lower(users_table.c.display_name))
    ).mappings().all()

    # Исходящие заявки (от меня, ждут подтверждения)
    outgoing = conn.execute(
        select(
            friendships_table.c.id,
            users_table.c.display_name,
            users_table.c.username,
        )
        .select_from(
            friendships_table.join(
                users_table, friendships_table.c.addressee_id == users_table.c.id
            )
        )
        .where(
            and_(
                friendships_table.c.requester_id == me_id,
                friendships_table.c.status == "pending",
            )
        )
        .order_by(func.lower(users_table.c.display_name))
    ).mappings().all()

    return friends, my_groups, incoming, outgoing


@app.route("/users")
def users():
    """
    Страница контактов: добавить в друзья, список друзей и заявки.
    Списки показываются в отдельной рамке (iframe), чтобы страница не мигала.
    """
    r = not_logged_in_redirect()
    if r:
        return r
    return render_template("users.html")


@app.route("/chats-frame")
def chats_frame():
    """«Окошко» с друзьями, заявками и группами — для самообновляемой рамки."""
    r = not_logged_in_redirect()
    if r:
        return r

    me = current_user()
    friends, my_groups, incoming, outgoing = load_contacts(get_conn(), me["id"])
    return render_template(
        "chats_list.html",
        friends=friends, groups=my_groups, incoming=incoming, outgoing=outgoing,
    )


@app.route("/friends/add", methods=["POST"])
def friends_add():
    """Отправить заявку в друзья по номеру телефона ИЛИ по имени."""
    r = not_logged_in_redirect()
    if r:
        return r

    me = current_user()
    conn = get_conn()
    who = request.form.get("who", "").strip().lstrip("@").lower()

    # Ищем человека по логину (@имя).
    target = None
    if who:
        target = conn.execute(
            select(*user_cols()).where(
                func.lower(users_table.c.username) == who
            )
        ).mappings().first()

    if target is None:
        flash("Не нашли такой логин. Проверь @имя.")
    elif target["id"] == me["id"]:
        flash("Это ты сам 🙂")
    elif friendship_between(conn, me["id"], target["id"]) is not None:
        flash("У вас уже есть заявка или вы уже друзья.")
    else:
        conn.execute(
            insert(friendships_table).values(
                requester_id=me["id"],
                addressee_id=target["id"],
                status="pending",
                created_at=datetime.now().isoformat(timespec="seconds"),
            )
        )
        conn.commit()
        flash("Заявка отправлена: {}".format(target["display_name"]))
    return redirect(url_for("users"))


@app.route("/friends/accept/<int:friendship_id>", methods=["POST"])
def friends_accept(friendship_id):
    """Принять заявку в друзья."""
    r = not_logged_in_redirect()
    if r:
        return r

    me = current_user()
    conn = get_conn()
    f = conn.execute(
        select(friendships_table).where(friendships_table.c.id == friendship_id)
    ).mappings().first()
    if f is None or f["addressee_id"] != me["id"] or f["status"] != "pending":
        flash("Заявка не найдена.")
    else:
        conn.execute(
            update(friendships_table)
            .where(friendships_table.c.id == friendship_id)
            .values(status="accepted")
        )
        conn.commit()
        flash("Теперь вы друзья! 🎉")
    return redirect(url_for("users"))


@app.route("/friends/decline/<int:friendship_id>", methods=["POST"])
def friends_decline(friendship_id):
    """Отклонить заявку (или убрать из друзей)."""
    r = not_logged_in_redirect()
    if r:
        return r

    me = current_user()
    conn = get_conn()
    f = conn.execute(
        select(friendships_table).where(friendships_table.c.id == friendship_id)
    ).mappings().first()
    if f is None or me["id"] not in (f["requester_id"], f["addressee_id"]):
        flash("Заявка не найдена.")
    else:
        conn.execute(
            delete(friendships_table).where(friendships_table.c.id == friendship_id)
        )
        conn.commit()
        flash("Готово.")
    return redirect(url_for("users"))


@app.route("/chat/<int:other_id>", methods=["GET", "POST"])
def chat(other_id):
    """Сам чат с выбранным человеком."""
    r = not_logged_in_redirect()
    if r:
        return r

    me = current_user()
    conn = get_conn()
    other = conn.execute(
        select(*user_cols())
        .where(users_table.c.id == other_id)
    ).mappings().first()

    # Если такого человека нет (или это я сам) — вернёмся к списку.
    if other is None or other["id"] == me["id"]:
        flash("Такого собеседника нет.")
        return redirect(url_for("users"))

    # Писать можно только друзьям.
    if not are_friends(conn, me["id"], other["id"]):
        flash("Сначала добавьте друг друга в друзья.")
        return redirect(url_for("users"))

    # Черновик — то, что человек уже набрал, но ещё не отправил.
    # Храним отдельно для каждого собеседника.
    draft_key = "draft_{}".format(other["id"])

    if request.method == "POST":
        # Смена фона чата (нажали цветной квадратик)
        set_bg = request.form.get("set_bg")
        if set_bg in BACKGROUNDS:
            conn.execute(
                update(users_table)
                .where(users_table.c.id == me["id"])
                .values(background=set_bg)
            )
            conn.commit()
            return redirect(url_for("chat", other_id=other["id"]))

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
    return render_template(
        "chat.html",
        other=other,
        draft=draft,
        emoji=EMOJI,
        backgrounds=BACKGROUNDS,
        background=me["background"] or "default",
    )


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
        select(*user_cols())
        .where(users_table.c.id == other_id)
    ).mappings().first()
    if other is None or other["id"] == me["id"]:
        return redirect(url_for("users"))

    if not are_friends(conn, me["id"], other["id"]):
        return redirect(url_for("users"))

    # Пока чат открыт — отмечаем сообщения прочитанными
    max_id = conn.execute(
        select(func.max(messages_table.c.id)).where(
            or_(
                and_(messages_table.c.sender_id == me["id"],
                     messages_table.c.recipient_id == other["id"]),
                and_(messages_table.c.sender_id == other["id"],
                     messages_table.c.recipient_id == me["id"]),
            )
        )
    ).scalar() or 0
    mark_read(conn, me["id"], "user", other["id"], max_id)

    return render_template(
        "chat_messages.html",
        other=other,
        messages=conversation(conn, me["id"], other["id"]),
        background=me["background"] or "default",
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
    # Добавлять в группу можно только друзей
    ids = friend_ids(conn, me["id"])
    others = []
    if ids:
        others = conn.execute(
            select(*user_cols())
            .where(users_table.c.id.in_(ids))
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
        # Смена фона чата
        set_bg = request.form.get("set_bg")
        if set_bg in BACKGROUNDS:
            conn.execute(
                update(users_table)
                .where(users_table.c.id == me["id"])
                .values(background=set_bg)
            )
            conn.commit()
            return redirect(url_for("group_chat", group_id=group_id))

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
            users_table.c.bio,
            users_table.c.avatar.is_not(None).label("has_avatar"),
        )
        .select_from(
            group_members_table.join(
                users_table, group_members_table.c.user_id == users_table.c.id
            )
        )
        .where(group_members_table.c.group_id == group_id)
        .order_by(group_members_table.c.role, func.lower(users_table.c.display_name))
    ).mappings().all()

    # Друзья, которых ещё нет в группе (чтобы админ мог добавить)
    member_ids = {m["user_id"] for m in members}
    ids = friend_ids(conn, me["id"])
    candidates = []
    if ids:
        candidates = conn.execute(
            select(*user_cols())
            .where(and_(users_table.c.id.in_(ids), users_table.c.id.notin_(member_ids)))
            .order_by(func.lower(users_table.c.display_name))
        ).mappings().all()

    draft = session.get(draft_key, "")
    return render_template(
        "group_chat.html",
        group=group,
        members=members,
        candidates=candidates,
        draft=draft,
        emoji=EMOJI,
        is_admin=(membership["role"] == "admin"),
        backgrounds=BACKGROUNDS,
        background=me["background"] or "default",
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

    # Пока группа открыта — отмечаем сообщения прочитанными
    max_id = conn.execute(
        select(func.max(group_messages_table.c.id)).where(
            group_messages_table.c.group_id == group_id
        )
    ).scalar() or 0
    mark_read(conn, me["id"], "group", group_id, max_id)

    return render_template(
        "chat_messages.html",
        messages=group_conversation(conn, group_id, me["id"]),
        background=me["background"] or "default",
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
        if not are_friends(conn, me["id"], uid):
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
