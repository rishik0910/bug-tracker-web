import sqlite3
from datetime import datetime
from werkzeug.security import check_password_hash, generate_password_hash

DB_NAME = "bugs.db"


def is_password_hash(value):
    if not value:
        return False

    return value.startswith("pbkdf2:") or value.startswith("scrypt:")

def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def create_tables():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        email TEXT,
        role TEXT DEFAULT 'user'
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bugs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        description TEXT,
        priority TEXT,
        status TEXT,
        assigned_to TEXT,
        created_at TEXT,
        reporter TEXT,
        app_name TEXT,
        steps TEXT,
        expected_result TEXT,
        actual_result TEXT,
        contact TEXT,
        resolution_note TEXT,
        fixed_at TEXT,
        screenshot_path TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bug_id INTEGER,
        author TEXT,
        author_role TEXT,
        message TEXT,
        created_at TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bug_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bug_id INTEGER,
        event_type TEXT,
        actor TEXT,
        message TEXT,
        created_at TEXT
    )
    """)

    migrate_users_table(cursor)
    migrate_bugs_table(cursor)
    migrate_bug_status_values(cursor)

    conn.commit()
    conn.close()


def migrate_users_table(cursor):
    cursor.execute("PRAGMA table_info(users)")
    existing_columns = {column["name"] for column in cursor.fetchall()}

    new_columns = {
        "email": "TEXT",
        "role": "TEXT DEFAULT 'user'"
    }

    for column_name, column_type in new_columns.items():
        if column_name not in existing_columns:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {column_name} {column_type}")


def migrate_bugs_table(cursor):
    cursor.execute("PRAGMA table_info(bugs)")
    existing_columns = {column["name"] for column in cursor.fetchall()}

    new_columns = {
        "reporter": "TEXT",
        "app_name": "TEXT",
        "steps": "TEXT",
        "expected_result": "TEXT",
        "actual_result": "TEXT",
        "contact": "TEXT",
        "resolution_note": "TEXT",
        "fixed_at": "TEXT",
        "screenshot_path": "TEXT",
        "ai_summary": "TEXT",
        "ai_priority": "TEXT",
        "ai_suspected_cause": "TEXT",
        "ai_fix_plan": "TEXT",
        "ai_repro_steps": "TEXT",
        "ai_resolution_summary": "TEXT"
    }

    for column_name, column_type in new_columns.items():
        if column_name not in existing_columns:
            cursor.execute(f"ALTER TABLE bugs ADD COLUMN {column_name} {column_type}")


def migrate_bug_status_values(cursor):
    cursor.execute("UPDATE bugs SET status='Open' WHERE status='Pending'")
    cursor.execute("UPDATE bugs SET status='Resolved' WHERE status='Fixed'")


def register_user(username, password, email, role):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        hashed_password = generate_password_hash(password)

        cursor.execute(
            "INSERT INTO users (username, password, email, role) VALUES (?, ?, ?, ?)",
            (username, hashed_password, email, role)
        )

        conn.commit()
        conn.close()
        return True
    except:
        return False


def login_user(username, password):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM users WHERE username=?",
        (username,)
    )

    user = cursor.fetchone()

    if not user:
        conn.close()
        return None

    stored_password = user["password"]
    is_valid = False

    if is_password_hash(stored_password):
        is_valid = check_password_hash(stored_password, password)
    else:
        is_valid = stored_password == password
        if is_valid:
            upgraded_hash = generate_password_hash(password)
            cursor.execute(
                "UPDATE users SET password=? WHERE id=?",
                (upgraded_hash, user["id"])
            )
            conn.commit()
            cursor.execute("SELECT * FROM users WHERE id=?", (user["id"],))
            user = cursor.fetchone()

    conn.close()
    return user if is_valid else None


def get_user_by_username(username):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username=?", (username,))
    user = cursor.fetchone()
    conn.close()
    return user


def get_fixers():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE role='fixer' ORDER BY username")
    users = cursor.fetchall()
    conn.close()
    return users


def get_all_users():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users ORDER BY role, username")
    users = cursor.fetchall()
    conn.close()
    return users


def create_fixer_account(username, email, password):
    return register_user(username, password, email, "fixer")


def update_user_password(username, new_password):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET password=? WHERE username=?",
        (generate_password_hash(new_password), username)
    )
    conn.commit()
    updated = cursor.rowcount > 0
    conn.close()
    return updated


def delete_fixer_account(username):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) AS count FROM bugs WHERE assigned_to=?", (username,))
    assigned_count = cursor.fetchone()["count"]

    if assigned_count:
        conn.close()
        return False

    cursor.execute("DELETE FROM users WHERE username=? AND role='fixer'", (username,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def add_bug_db(title, description, priority, status, assigned_to, reporter, app_name, steps, expected_result, actual_result, contact, screenshot_path=""):
    conn = get_connection()
    cursor = conn.cursor()

    created_at = datetime.now().strftime("%d %b %Y, %I:%M %p")

    cursor.execute("""
    INSERT INTO bugs (
        title, description, priority, status, assigned_to, created_at,
        reporter, app_name, steps, expected_result, actual_result, contact,
        resolution_note, fixed_at, screenshot_path
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        title, description, priority, status, assigned_to, created_at,
        reporter, app_name, steps, expected_result, actual_result, contact,
        "", "", screenshot_path
    ))

    bug_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return bug_id


def update_bug_ai_fields(
    bug_id,
    ai_summary,
    ai_priority,
    ai_suspected_cause,
    ai_fix_plan,
    ai_repro_steps="",
    ai_resolution_summary=""
):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE bugs
        SET ai_summary=?, ai_priority=?, ai_suspected_cause=?, ai_fix_plan=?, ai_repro_steps=?, ai_resolution_summary=?
        WHERE id=?
        """,
        (
            ai_summary,
            ai_priority,
            ai_suspected_cause,
            ai_fix_plan,
            ai_repro_steps,
            ai_resolution_summary,
            bug_id
        )
    )
    conn.commit()
    conn.close()


def record_bug_event(bug_id, event_type, actor, message):
    conn = get_connection()
    cursor = conn.cursor()
    created_at = datetime.now().strftime("%d %b %Y, %I:%M %p")
    cursor.execute(
        """
        INSERT INTO bug_events (bug_id, event_type, actor, message, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (bug_id, event_type, actor, message, created_at)
    )
    conn.commit()
    conn.close()


def get_bug_events(bug_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM bug_events WHERE bug_id=? ORDER BY id ASC", (bug_id,))
    events = cursor.fetchall()
    conn.close()
    return events


def get_bugs():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM bugs ORDER BY id DESC")
    bugs = cursor.fetchall()

    conn.close()
    return bugs


def delete_bug_db(bug_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM bugs WHERE id=?", (bug_id,))
    conn.commit()
    conn.close()


def get_bug_by_id(bug_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM bugs WHERE id=?", (bug_id,))
    bug = cursor.fetchone()

    conn.close()
    return bug


def get_bugs_for_reporter(username, status_filter="", priority_filter="", search_query=""):
    conn = get_connection()
    cursor = conn.cursor()

    query = "SELECT * FROM bugs WHERE reporter=?"
    params = [username]

    if status_filter:
        query += " AND status=?"
        params.append(status_filter)

    if priority_filter:
        query += " AND priority=?"
        params.append(priority_filter)

    if search_query:
        query += " AND (title LIKE ? OR app_name LIKE ? OR description LIKE ? OR assigned_to LIKE ?)"
        like_query = f"%{search_query}%"
        params.extend([like_query, like_query, like_query, like_query])

    query += " ORDER BY id DESC"
    cursor.execute(query, params)
    bugs = cursor.fetchall()
    conn.close()
    return bugs


def get_all_bugs(status_filter="", priority_filter="", fixer_filter="", search_query=""):
    conn = get_connection()
    cursor = conn.cursor()

    query = "SELECT * FROM bugs WHERE 1=1"
    params = []

    if status_filter:
        query += " AND status=?"
        params.append(status_filter)

    if priority_filter:
        query += " AND priority=?"
        params.append(priority_filter)

    if fixer_filter:
        query += " AND assigned_to=?"
        params.append(fixer_filter)

    if search_query:
        query += " AND (title LIKE ? OR app_name LIKE ? OR description LIKE ? OR reporter LIKE ? OR assigned_to LIKE ?)"
        like_query = f"%{search_query}%"
        params.extend([like_query, like_query, like_query, like_query, like_query])

    query += " ORDER BY id DESC"
    cursor.execute(query, params)
    bugs = cursor.fetchall()
    conn.close()
    return bugs


def get_bugs_for_fixer(username, status_filter="", priority_filter="", search_query=""):
    conn = get_connection()
    cursor = conn.cursor()

    query = "SELECT * FROM bugs WHERE assigned_to=?"
    params = [username]

    if status_filter:
        query += " AND status=?"
        params.append(status_filter)

    if priority_filter:
        query += " AND priority=?"
        params.append(priority_filter)

    if search_query:
        query += " AND (title LIKE ? OR app_name LIKE ? OR description LIKE ? OR reporter LIKE ?)"
        like_query = f"%{search_query}%"
        params.extend([like_query, like_query, like_query, like_query])

    query += " ORDER BY id DESC"
    cursor.execute(query, params)
    bugs = cursor.fetchall()
    conn.close()
    return bugs


def update_bug(bug_id, title, description, priority, status, assigned_to, app_name, steps, expected_result, actual_result, contact, resolution_note, screenshot_path=""):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT status FROM bugs WHERE id=?", (bug_id,))
    current_bug = cursor.fetchone()
    fixed_at = None

    if current_bug and current_bug["status"] != "Resolved" and status == "Resolved":
        fixed_at = datetime.now().strftime("%d %b %Y, %I:%M %p")

    if fixed_at:
        cursor.execute("""
        UPDATE bugs
        SET title=?, description=?, priority=?, status=?, assigned_to=?, app_name=?,
            steps=?, expected_result=?, actual_result=?, contact=?, resolution_note=?,
            fixed_at=?, screenshot_path=?
        WHERE id=?
        """, (
            title, description, priority, status, assigned_to, app_name,
            steps, expected_result, actual_result, contact, resolution_note,
            fixed_at, screenshot_path, bug_id
        ))
    else:
        cursor.execute("""
        UPDATE bugs
        SET title=?, description=?, priority=?, status=?, assigned_to=?, app_name=?,
            steps=?, expected_result=?, actual_result=?, contact=?, resolution_note=?, screenshot_path=?
        WHERE id=?
        """, (
            title, description, priority, status, assigned_to, app_name,
            steps, expected_result, actual_result, contact, resolution_note,
            screenshot_path, bug_id
        ))

    conn.commit()
    conn.close()


def get_fixed_notifications(username):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT * FROM bugs
    WHERE reporter=? AND status='Resolved'
    ORDER BY fixed_at DESC, id DESC
    """, (username,))

    bugs = cursor.fetchall()

    conn.close()
    return bugs


def add_comment(bug_id, author, author_role, message):
    conn = get_connection()
    cursor = conn.cursor()
    created_at = datetime.now().strftime("%d %b %Y, %I:%M %p")
    cursor.execute(
        """
        INSERT INTO comments (bug_id, author, author_role, message, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (bug_id, author, author_role, message, created_at)
    )
    conn.commit()
    conn.close()


def get_comments_for_bug(bug_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM comments WHERE bug_id=? ORDER BY id ASC", (bug_id,))
    comments = cursor.fetchall()
    conn.close()
    return comments
