import os
import sqlite3
import contextlib
import time
import hashlib
from typing import List, Tuple, Optional, Dict, Any

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "olx_spy.db")


@contextlib.contextmanager
def get_db(db_path: str = DB_FILE):
    """
    Context manager that yields a database connection and ensures
    it is properly closed and changes are committed.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str = DB_FILE) -> None:
    """Initializes database tables and safely migrates schema if needed."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()

        # Check existing tasks table structure
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tasks';")
        has_tasks = cursor.fetchone() is not None

        if has_tasks:
            cursor.execute("PRAGMA table_info(tasks);")
            columns = [row["name"] for row in cursor.fetchall()]

            # If tasks table doesn't have an autoincrement id column, migrate to multi-task schema
            if "id" not in columns:
                cursor.execute(
                    """
                    CREATE TABLE tasks_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        chat_id INTEGER NOT NULL,
                        query TEXT NOT NULL,
                        interval_min INTEGER DEFAULT 10,
                        last_checked REAL DEFAULT 0,
                        min_price INTEGER DEFAULT NULL,
                        max_price INTEGER DEFAULT NULL,
                        location TEXT DEFAULT 'list',
                        stop_words TEXT DEFAULT '',
                        seller_type TEXT DEFAULT 'all',
                        sort_by TEXT DEFAULT 'created_at:desc'
                    );
                    """
                )
                cursor.execute(
                    """
                    INSERT INTO tasks_new (chat_id, query, interval_min, last_checked, min_price, max_price, location)
                    SELECT chat_id, query, 
                           COALESCE(interval_min, 10), 
                           COALESCE(last_checked, 0), 
                           min_price, 
                           max_price, 
                           COALESCE(location, 'list') 
                    FROM tasks;
                    """
                )
                cursor.execute("DROP TABLE tasks;")
                cursor.execute("ALTER TABLE tasks_new RENAME TO tasks;")
            else:
                if "stop_words" not in columns:
                    cursor.execute("ALTER TABLE tasks ADD COLUMN stop_words TEXT DEFAULT '';")
                if "seller_type" not in columns:
                    cursor.execute("ALTER TABLE tasks ADD COLUMN seller_type TEXT DEFAULT 'all';")
                if "sort_by" not in columns:
                    cursor.execute("ALTER TABLE tasks ADD COLUMN sort_by TEXT DEFAULT 'created_at:desc';")
        else:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    query TEXT NOT NULL,
                    interval_min INTEGER DEFAULT 10,
                    last_checked REAL DEFAULT 0,
                    min_price INTEGER DEFAULT NULL,
                    max_price INTEGER DEFAULT NULL,
                    location TEXT DEFAULT 'list',
                    stop_words TEXT DEFAULT '',
                    seller_type TEXT DEFAULT 'all',
                    sort_by TEXT DEFAULT 'created_at:desc'
                );
                """
            )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_ads (
                link TEXT PRIMARY KEY
            );
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS favorites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                price TEXT NOT NULL,
                link TEXT NOT NULL,
                photo TEXT DEFAULT NULL,
                created_at REAL NOT NULL,
                UNIQUE(chat_id, link)
            );
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS cached_ads (
                hash_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                price TEXT NOT NULL,
                link TEXT NOT NULL,
                photo TEXT DEFAULT NULL,
                created_at REAL NOT NULL
            );
            """
        )


def cache_ad(title: str, price: str, link: str, photo: Optional[str] = None, db_path: str = DB_FILE) -> str:
    """Caches an ad so it can be added to favorites by short hash within 64-byte callback limit."""
    hash_id = hashlib.md5(link.encode("utf-8")).hexdigest()[:12]
    now = time.time()
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO cached_ads (hash_id, title, price, link, photo, created_at)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            (hash_id, title.strip(), price.strip(), link.strip(), photo, now)
        )
    return hash_id


def get_cached_ad(hash_id: str, db_path: str = DB_FILE) -> Optional[Dict[str, Any]]:
    """Retrieves cached ad by hash_id."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM cached_ads WHERE hash_id = ?;", (hash_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def add_or_update_task(
    chat_id: int,
    query: str,
    interval_min: int = 10,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    location: str = "list",
    stop_words: str = "",
    seller_type: str = "all",
    sort_by: str = "created_at:desc",
    db_path: str = DB_FILE,
) -> int:
    """
    Adds a new search task or updates an existing one if the exact same query exists for chat_id.
    Returns task id.
    """
    now = time.time()
    clean_query = query.strip()
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM tasks WHERE chat_id = ? AND lower(query) = lower(?);",
            (chat_id, clean_query)
        )
        row = cursor.fetchone()

        if row:
            task_id = row["id"]
            cursor.execute(
                """
                UPDATE tasks
                SET interval_min = ?, min_price = ?, max_price = ?, location = ?, 
                    stop_words = ?, seller_type = ?, sort_by = ?, last_checked = ?
                WHERE id = ?;
                """,
                (interval_min, min_price, max_price, location, stop_words, seller_type, sort_by, now, task_id)
            )
            return task_id
        else:
            cursor.execute(
                """
                INSERT INTO tasks (chat_id, query, interval_min, last_checked, min_price, max_price, location, stop_words, seller_type, sort_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (chat_id, clean_query, interval_min, now, min_price, max_price, location, stop_words, seller_type, sort_by)
            )
            return cursor.lastrowid


def save_task(
    chat_id: int,
    query: str,
    interval_min: Optional[int] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    location: Optional[str] = None,
    stop_words: Optional[str] = None,
    seller_type: Optional[str] = None,
    sort_by: Optional[str] = None,
    db_path: str = DB_FILE,
) -> int:
    """
    Compatibility function: adds or updates task, inheriting previous task settings if available.
    """
    latest = get_latest_task(chat_id, db_path)
    cur_interval = interval_min if interval_min is not None else (latest["interval_min"] if latest else 10)
    cur_min_price = min_price if min_price is not None else (latest["min_price"] if latest else None)
    cur_max_price = max_price if max_price is not None else (latest["max_price"] if latest else None)
    cur_loc = location if location is not None else (latest["location"] if latest else "list")
    cur_stops = stop_words if stop_words is not None else (latest["stop_words"] if latest else "")
    cur_seller = seller_type if seller_type is not None else (latest["seller_type"] if latest else "all")
    cur_sort = sort_by if sort_by is not None else (latest["sort_by"] if latest else "created_at:desc")

    return add_or_update_task(
        chat_id=chat_id,
        query=query,
        interval_min=cur_interval,
        min_price=cur_min_price,
        max_price=cur_max_price,
        location=cur_loc,
        stop_words=cur_stops,
        seller_type=cur_seller,
        sort_by=cur_sort,
        db_path=db_path
    )


def get_latest_task(chat_id: int, db_path: str = DB_FILE) -> Optional[Dict[str, Any]]:
    """Retrieves the most recently created or updated task for a specific chat."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, chat_id, query, interval_min, last_checked, min_price, max_price, location, stop_words, seller_type, sort_by
            FROM tasks WHERE chat_id = ?
            ORDER BY id DESC LIMIT 1;
            """,
            (chat_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def get_task_by_id(task_id: int, chat_id: Optional[int] = None, db_path: str = DB_FILE) -> Optional[Dict[str, Any]]:
    """Retrieves specific task by its unique ID."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        if chat_id is not None:
            cursor.execute("SELECT * FROM tasks WHERE id = ? AND chat_id = ?;", (task_id, chat_id))
        else:
            cursor.execute("SELECT * FROM tasks WHERE id = ?;", (task_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_user_tasks(chat_id: int, db_path: str = DB_FILE) -> List[Dict[str, Any]]:
    """Retrieves all active search tasks for a specific user."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM tasks WHERE chat_id = ? ORDER BY id ASC;",
            (chat_id,)
        )
        return [dict(r) for r in cursor.fetchall()]


def delete_task_by_id(task_id: int, chat_id: int, db_path: str = DB_FILE) -> bool:
    """Deletes a specific task by ID belonging to user."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM tasks WHERE id = ? AND chat_id = ?;", (task_id, chat_id))
        return cursor.rowcount > 0


def delete_all_tasks(chat_id: int, db_path: str = DB_FILE) -> bool:
    """Deletes all tasks for a specific chat."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM tasks WHERE chat_id = ?;", (chat_id,))
        return cursor.rowcount > 0


def delete_task(chat_id: int, db_path: str = DB_FILE) -> bool:
    """Compatibility alias for deleting all tasks for chat."""
    return delete_all_tasks(chat_id, db_path)


def set_price_filter(
    chat_id: int,
    min_price: Optional[int],
    max_price: Optional[int],
    task_id: Optional[int] = None,
    db_path: str = DB_FILE
) -> bool:
    """Updates min_price and max_price for task or all chat tasks."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        if task_id:
            cursor.execute(
                "UPDATE tasks SET min_price = ?, max_price = ? WHERE id = ? AND chat_id = ?;",
                (min_price, max_price, task_id, chat_id)
            )
        else:
            cursor.execute(
                "UPDATE tasks SET min_price = ?, max_price = ? WHERE chat_id = ?;",
                (min_price, max_price, chat_id)
            )
        return cursor.rowcount > 0


def set_location(
    chat_id: int,
    location: str,
    task_id: Optional[int] = None,
    db_path: str = DB_FILE
) -> bool:
    """Updates location for task or all chat tasks."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        clean_loc = location.strip().lower()
        if task_id:
            cursor.execute(
                "UPDATE tasks SET location = ? WHERE id = ? AND chat_id = ?;",
                (clean_loc, task_id, chat_id)
            )
        else:
            cursor.execute(
                "UPDATE tasks SET location = ? WHERE chat_id = ?;",
                (clean_loc, chat_id)
            )
        return cursor.rowcount > 0


def set_stop_words(
    chat_id: int,
    stop_words: str,
    task_id: Optional[int] = None,
    db_path: str = DB_FILE
) -> bool:
    """Updates stop_words for task or all chat tasks."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        clean_stops = stop_words.strip()
        if task_id:
            cursor.execute(
                "UPDATE tasks SET stop_words = ? WHERE id = ? AND chat_id = ?;",
                (clean_stops, task_id, chat_id)
            )
        else:
            cursor.execute(
                "UPDATE tasks SET stop_words = ? WHERE chat_id = ?;",
                (clean_stops, chat_id)
            )
        return cursor.rowcount > 0


def set_seller_type(
    chat_id: int,
    seller_type: str,
    task_id: Optional[int] = None,
    db_path: str = DB_FILE
) -> bool:
    """Updates seller_type ('all', 'private', 'business')."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        clean_type = seller_type.strip().lower()
        if task_id:
            cursor.execute(
                "UPDATE tasks SET seller_type = ? WHERE id = ? AND chat_id = ?;",
                (clean_type, task_id, chat_id)
            )
        else:
            cursor.execute(
                "UPDATE tasks SET seller_type = ? WHERE chat_id = ?;",
                (clean_type, chat_id)
            )
        return cursor.rowcount > 0


def set_sort_by(
    chat_id: int,
    sort_by: str,
    task_id: Optional[int] = None,
    db_path: str = DB_FILE
) -> bool:
    """Updates sort_by ('created_at:desc' or 'filter_float_price:asc')."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        if task_id:
            cursor.execute(
                "UPDATE tasks SET sort_by = ? WHERE id = ? AND chat_id = ?;",
                (sort_by, task_id, chat_id)
            )
        else:
            cursor.execute(
                "UPDATE tasks SET sort_by = ? WHERE chat_id = ?;",
                (sort_by, chat_id)
            )
        return cursor.rowcount > 0


def update_interval(
    chat_id: int,
    interval_min: int,
    task_id: Optional[int] = None,
    db_path: str = DB_FILE
) -> bool:
    """Updates monitoring interval in minutes."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        if task_id:
            cursor.execute(
                "UPDATE tasks SET interval_min = ? WHERE id = ? AND chat_id = ?;",
                (interval_min, task_id, chat_id)
            )
        else:
            cursor.execute(
                "UPDATE tasks SET interval_min = ? WHERE chat_id = ?;",
                (interval_min, chat_id)
            )
        return cursor.rowcount > 0


def update_last_checked(task_id: int, timestamp: float, db_path: str = DB_FILE) -> None:
    """Updates timestamp when task was last checked."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE tasks SET last_checked = ? WHERE id = ?;", (timestamp, task_id))


def get_task(chat_id: int, db_path: str = DB_FILE) -> Optional[str]:
    """Compatibility: retrieves latest query for chat."""
    latest = get_latest_task(chat_id, db_path)
    return latest["query"] if latest else None


def get_task_data(chat_id: int, db_path: str = DB_FILE) -> Optional[Dict[str, Any]]:
    """Compatibility: retrieves latest task details for chat."""
    return get_latest_task(chat_id, db_path)


def get_all_tasks(db_path: str = DB_FILE) -> List[Dict[str, Any]]:
    """Returns all active monitoring tasks."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks;")
        return [dict(r) for r in cursor.fetchall()]


def get_due_tasks(now: float, db_path: str = DB_FILE) -> List[Dict[str, Any]]:
    """
    Returns tasks that are due for check based on:
    (now - last_checked) >= (interval_min * 60)
    """
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks;")
        rows = cursor.fetchall()
        due = []
        for row in rows:
            interval_sec = (row["interval_min"] or 10) * 60
            last_checked = row["last_checked"] or 0
            if (now - last_checked) >= interval_sec:
                due.append(dict(row))
        return due


# --- Seen Ads Operations ---

def is_ad_seen(link: str, db_path: str = DB_FILE) -> bool:
    """Checks whether an ad link has already been seen/sent."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM seen_ads WHERE link = ?;", (link.strip(),))
        return cursor.fetchone() is not None


def add_seen_ad(link: str, db_path: str = DB_FILE) -> bool:
    """Inserts ad link using INSERT OR IGNORE."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO seen_ads (link) VALUES (?);",
            (link.strip(),),
        )
        return cursor.rowcount > 0


def add_seen_ads(links: List[str], db_path: str = DB_FILE) -> None:
    """Batch inserts seen ads."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.executemany(
            "INSERT OR IGNORE INTO seen_ads (link) VALUES (?);",
            [(link.strip(),) for link in links],
        )


# --- Favorites Operations ---

def add_favorite(
    chat_id: int,
    title: str,
    price: str,
    link: str,
    photo: Optional[str] = None,
    db_path: str = DB_FILE,
) -> bool:
    """Adds an ad to user's favorites list. Returns True if inserted."""
    now = time.time()
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR IGNORE INTO favorites (chat_id, title, price, link, photo, created_at)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            (chat_id, title.strip(), price.strip(), link.strip(), photo, now)
        )
        return cursor.rowcount > 0


def get_favorites(chat_id: int, db_path: str = DB_FILE) -> List[Dict[str, Any]]:
    """Retrieves all saved favorite ads for user."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM favorites WHERE chat_id = ? ORDER BY id DESC;",
            (chat_id,)
        )
        return [dict(r) for r in cursor.fetchall()]


def delete_favorite(fav_id: int, chat_id: int, db_path: str = DB_FILE) -> bool:
    """Deletes an ad from user's favorites by favorite ID."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM favorites WHERE id = ? AND chat_id = ?;", (fav_id, chat_id))
        return cursor.rowcount > 0
