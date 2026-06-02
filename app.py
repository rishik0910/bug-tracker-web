from collections import Counter, defaultdict
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from email.message import EmailMessage
import json
import os
import random
import smtplib
import re
from urllib import request as urllib_request
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from database import *
import tempfile
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "secret123"
app.config["SMTP_HOST"] = os.environ.get("BUG_TRACKER_SMTP_HOST", "")
app.config["SMTP_PORT"] = os.environ.get("BUG_TRACKER_SMTP_PORT", "")
app.config["SMTP_USERNAME"] = os.environ.get("BUG_TRACKER_SMTP_USERNAME", "")
app.config["SMTP_PASSWORD"] = os.environ.get("BUG_TRACKER_SMTP_PASSWORD", "")
app.config["SMTP_SENDER_EMAIL"] = os.environ.get("BUG_TRACKER_SENDER_EMAIL", "")
app.config["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY", "")
app.config["OPENAI_MODEL"] = os.environ.get("BUG_TRACKER_AI_MODEL", "gpt-4.1-mini")
app.config["UPLOAD_FOLDER"] = os.environ.get("BUG_TRACKER_UPLOAD_FOLDER") or os.path.join(tempfile.gettempdir(), "uploads")
app.config["ALLOWED_IMAGE_EXTENSIONS"] = {"png", "jpg", "jpeg", "gif", "webp"}

DISPLAY_NAME_MAP = {
    "demo_fixer_role2": "Kiran",
    "demo_user_role2": "Ananya",
    "hash_demo_user": "Rohan",
}

DEFAULT_FIXER_ACCOUNTS = [
    {"username": "Kiran", "email": "kiran@bugtracker.local", "password": "Kiran@123"},
    {"username": "Priya", "email": "priya@bugtracker.local", "password": "Priya@123"},
    {"username": "Rahul", "email": "rahul@bugtracker.local", "password": "Rahul@123"},
    {"username": "Sneha", "email": "sneha@bugtracker.local", "password": "Sneha@123"},
    {"username": "Arjun", "email": "arjun@bugtracker.local", "password": "Arjun@123"},
    {"username": "Anjali", "email": "anjali@bugtracker.local", "password": "Anjali@123"},
]

COMMON_BUG_WORDS = {
    "the", "and", "for", "with", "this", "that", "from", "there", "when", "where",
    "which", "have", "has", "into", "after", "before", "while", "user", "users",
    "page", "screen", "click", "button", "field", "input", "issue", "bug", "report",
    "error", "does", "doesnt", "dont", "cant", "cannot", "not", "app", "website",
    "site", "mobile", "web", "portal", "system", "please", "check", "using",
    "login", "logged", "able", "unable"
}

DEFAULT_ADMIN_ACCOUNT = {
    "username": "admin",
    "email": "admin@bugtracker.local",
    "password": "admin",
}
try:
    create_tables()
except Exception as e:
    print("Database setup skipped:", e)
try:
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
except Exception:
    # In read-only environments this may fail; fallback to system temp dir
    app.config["UPLOAD_FOLDER"] = os.environ.get("BUG_TRACKER_UPLOAD_FOLDER") or tempfile.gettempdir()


def is_logged_in():
    return "user" in session


def is_fixer():
    return session.get("role") == "fixer"


def is_admin():
    return session.get("role") == "admin"


def allowed_image(filename):
    if "." not in filename:
        return False

    return filename.rsplit(".", 1)[1].lower() in app.config["ALLOWED_IMAGE_EXTENSIONS"]


def format_display_name(username):
    if not username:
        return "Unassigned"

    if username in DISPLAY_NAME_MAP:
        return DISPLAY_NAME_MAP[username]

    cleaned = str(username).replace("_", " ").strip()
    return cleaned


def format_role_label(role):
    labels = {
        "admin": "Admin",
        "fixer": "Fixer",
        "user": "Reporter",
    }
    return labels.get(role, str(role).title())


def ensure_default_fixers():
    existing_usernames = {
        user["username"]
        for user in get_all_users()
    }

    for fixer in DEFAULT_FIXER_ACCOUNTS:
        if fixer["username"] not in existing_usernames:
            register_user(fixer["username"], fixer["password"], fixer["email"], "fixer")
        elif not login_user(fixer["username"], fixer["password"]):
            update_user_password(fixer["username"], fixer["password"])


def ensure_default_admin():
    existing_usernames = {
        user["username"]
        for user in get_all_users()
    }

    if DEFAULT_ADMIN_ACCOUNT["username"] not in existing_usernames:
        register_user(
            DEFAULT_ADMIN_ACCOUNT["username"],
            DEFAULT_ADMIN_ACCOUNT["password"],
            DEFAULT_ADMIN_ACCOUNT["email"],
            "admin"
        )


@app.context_processor
def inject_display_helpers():
    return {
        "display_name": format_display_name,
        "role_label": format_role_label,
    }


ensure_default_admin()
ensure_default_fixers()


def get_login_quick_accounts():
    return [
        {"label": "Admin", "username": "admin", "password": "admin", "subtitle": "Admin access"},
        {"label": "Reporter", "username": "rishik", "password": "rishik", "subtitle": "User workspace"},
        {"label": "Fixer", "username": "", "password": "", "subtitle": "Fixer workspace"},
    ]


def get_login_fixers():
    login_fixers = []

    for fixer in get_fixers():
        if fixer["username"] in {item["username"] for item in DEFAULT_FIXER_ACCOUNTS}:
            login_fixers.append({
                "label": fixer["username"],
                "username": fixer["username"],
            })

    return login_fixers


def save_uploaded_screenshot(file_storage):
    if not file_storage or not file_storage.filename:
        return ""

    if not allowed_image(file_storage.filename):
        return ""

    filename = secure_filename(file_storage.filename)
    unique_filename = f"{random.randint(100000, 999999)}_{filename}"
    save_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_filename)
    file_storage.save(save_path)
    return os.path.join("uploads", unique_filename).replace("\\", "/")


def assign_fixer():
    fixers = get_fixers()
    eligible_fixers = [
        fixer["username"]
        for fixer in fixers
        if not str(fixer["username"]).startswith("demo_")
    ]

    if not eligible_fixers:
        eligible_fixers = [fixer["username"] for fixer in fixers]

    if eligible_fixers:
        bug_counts = Counter(
            bug["assigned_to"]
            for bug in get_all_bugs()
            if bug["assigned_to"] in eligible_fixers and bug["status"] not in ("Resolved", "Closed")
        )
        least_load = min((bug_counts.get(name, 0) for name in eligible_fixers), default=0)
        least_loaded_fixers = [
            name for name in eligible_fixers
            if bug_counts.get(name, 0) == least_load
        ]
        return random.choice(least_loaded_fixers)

    fallback_fixers = [fixer["username"] for fixer in DEFAULT_FIXER_ACCOUNTS]
    return random.choice(fallback_fixers)


def smtp_is_configured():
    return all([
        app.config["SMTP_HOST"],
        app.config["SMTP_PORT"],
        app.config["SMTP_USERNAME"],
        app.config["SMTP_PASSWORD"],
        app.config["SMTP_SENDER_EMAIL"]
    ])


def build_smtp_status():
    required_fields = [
        ("Host", app.config["SMTP_HOST"]),
        ("Port", app.config["SMTP_PORT"]),
        ("Username", app.config["SMTP_USERNAME"]),
        ("Password", app.config["SMTP_PASSWORD"]),
        ("Sender email", app.config["SMTP_SENDER_EMAIL"]),
    ]
    missing = [label for label, value in required_fields if not value]
    return {
        "configured": len(missing) == 0,
        "sender": app.config["SMTP_SENDER_EMAIL"] or "Not configured",
        "host": app.config["SMTP_HOST"] or "Not configured",
        "port": app.config["SMTP_PORT"] or "Not configured",
        "missing": missing,
    }


def ai_is_configured():
    return bool(app.config["OPENAI_API_KEY"])


def build_bug_url(bug_id):
    return url_for("bug_detail", id=bug_id, _external=True)


def can_access_bug(bug):
    if not bug or not is_logged_in():
        return False

    if is_admin():
        return True

    if is_fixer():
        return bug["assigned_to"] == session["user"]

    return bug["reporter"] == session["user"]

def build_email_html(subject, intro, bug, action_label, action_url, extra_note=""):
    note_block = f"""
        <div style="margin-top:18px;padding:14px 16px;border-radius:14px;background:#f8fafc;border:1px solid #e2e8f0;">
            <strong style="display:block;margin-bottom:6px;color:#0f172a;">Update</strong>
            <div style="color:#475569;line-height:1.6;">{extra_note}</div>
        </div>
    """ if extra_note else ""

    return f"""
    <html>
    <body style="margin:0;background:#f1f5f9;font-family:Arial,sans-serif;color:#0f172a;">
        <div style="max-width:680px;margin:0 auto;padding:32px 20px;">
            <div style="background:linear-gradient(135deg,#111827,#312e81);border-radius:24px;padding:28px 28px 22px;color:#fff;">
                <div style="font-size:12px;letter-spacing:.12em;text-transform:uppercase;opacity:.75;">BugTracker AI</div>
                <h1 style="margin:10px 0 8px;font-size:28px;line-height:1.2;">{subject}</h1>
                <p style="margin:0;color:rgba(255,255,255,.82);line-height:1.6;">{intro}</p>
            </div>
            <div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:24px;padding:24px;margin-top:-10px;box-shadow:0 18px 40px rgba(15,23,42,.08);">
                <div style="display:grid;gap:12px;">
                    <div><strong>Bug ID:</strong> #{bug['id']}</div>
                    <div><strong>Title:</strong> {bug['title'] or 'Bug report'}</div>
                    <div><strong>App / Website:</strong> {bug['app_name'] or 'Not specified'}</div>
                    <div><strong>Priority:</strong> {bug['priority'] or 'Not specified'}</div>
                    <div><strong>Status:</strong> {bug['status'] or 'Open'}</div>
                    <div><strong>Assigned Fixer:</strong> {format_display_name(bug['assigned_to'])}</div>
                </div>
                {note_block}
                <div style="margin-top:22px;">
                    <a href="{action_url}" style="display:inline-block;background:#4f46e5;color:#ffffff;text-decoration:none;padding:12px 18px;border-radius:12px;font-weight:700;">{action_label}</a>
                </div>
                <p style="margin:18px 0 0;color:#64748b;font-size:13px;">You can open the bug page anytime to track status and updates.</p>
            </div>
        </div>
    </body>
    </html>
    """


def send_bug_fixed_email(bug, bug_url=""):
    recipient = bug["contact"]

    if not recipient or "@" not in recipient:
        return False

    if not smtp_is_configured():
        return False

    message = EmailMessage()
    subject = f"Bug Fixed: #{bug['id']} {bug['title'] or 'Your bug report'}"
    message["Subject"] = subject
    message["From"] = app.config["SMTP_SENDER_EMAIL"]
    message["To"] = recipient
    text_body = f"""Hello {bug['reporter'] or 'User'},

Your bug report has been fixed.

Bug ID: #{bug['id']}
Title: {bug['title'] or 'Bug report'}
App / Website: {bug['app_name'] or 'Not specified'}
Assigned Fixer: {bug['assigned_to'] or 'Fixer'}
Status: {bug['status']}

Resolution Note:
{bug['resolution_note'] or 'The fixer marked the issue as resolved.'}

AI Resolution Summary:
{bug['ai_resolution_summary'] or 'The issue was resolved and verified by the assigned fixer.'}

Fixed At: {bug['fixed_at'] or 'Recently'}

Track status: {bug_url or 'Open the app to review your bug'}

Thank you for reporting the issue.
"""
    message.set_content(text_body)
    message.add_alternative(
        build_email_html(
            subject,
            "Your reported issue has been resolved. You can review the final status and note below.",
            bug,
            "Open Bug Update",
            bug_url or "#",
            (bug["ai_resolution_summary"] or bug["resolution_note"] or "The fixer marked this issue as resolved.")
        ),
        subtype="html"
    )

    with smtplib.SMTP(app.config["SMTP_HOST"], int(app.config["SMTP_PORT"])) as smtp:
        smtp.starttls()
        smtp.login(app.config["SMTP_USERNAME"], app.config["SMTP_PASSWORD"])
        smtp.send_message(message)

    return True


def send_bug_reported_email(bug, bug_url=""):
    recipient = bug["contact"]

    if not recipient or "@" not in recipient:
        return False

    if not smtp_is_configured():
        return False

    message = EmailMessage()
    subject = f"Bug Report Received: #{bug['id']} {bug['title'] or 'Your bug report'}"
    message["Subject"] = subject
    message["From"] = app.config["SMTP_SENDER_EMAIL"]
    message["To"] = recipient
    text_body = f"""Hello {bug['reporter'] or 'User'},

We have successfully received your bug report.

Bug ID: #{bug['id']}
Title: {bug['title'] or 'Bug report'}
App / Website: {bug['app_name'] or 'Not specified'}
Priority: {bug['priority'] or 'Not specified'}
Assigned Fixer: {bug['assigned_to'] or 'We will assign one shortly'}
Status: {bug['status'] or 'Open'}

Our team will review it and get back to you once it is resolved.

Track status: {bug_url or 'Open the app to review your bug'}

Thank you for reporting the issue.
BugTracker AI Support
"""
    message.set_content(text_body)
    message.add_alternative(
        build_email_html(
            subject,
            "We have successfully received your bug report. Our team is reviewing it now.",
            bug,
            "View Bug Status",
            bug_url or "#",
            "You will receive another update once the bug is resolved."
        ),
        subtype="html"
    )

    with smtplib.SMTP(app.config["SMTP_HOST"], int(app.config["SMTP_PORT"])) as smtp:
        smtp.starttls()
        smtp.login(app.config["SMTP_USERNAME"], app.config["SMTP_PASSWORD"])
        smtp.send_message(message)

    return True


def send_bug_status_email(bug, bug_url="", note=""):
    recipient = bug["contact"]

    if not recipient or "@" not in recipient:
        return False

    if not smtp_is_configured():
        return False

    subject = f"Bug Status Updated: #{bug['id']} {bug['title'] or 'Your bug report'}"
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = app.config["SMTP_SENDER_EMAIL"]
    message["To"] = recipient
    text_body = f"""Hello {bug['reporter'] or 'User'},

Your bug report status was updated.

Bug ID: #{bug['id']}
Title: {bug['title'] or 'Bug report'}
App / Website: {bug['app_name'] or 'Not specified'}
Assigned Fixer: {bug['assigned_to'] or 'Fixer'}
New Status: {bug['status']}

Update:
{note or 'The team updated your bug report.'}

Track status: {bug_url or 'Open the app to review your bug'}

BugTracker AI Support
"""
    message.set_content(text_body)
    message.add_alternative(
        build_email_html(
            subject,
            "Your reported issue has a new workflow update.",
            bug,
            "View Bug Update",
            bug_url or "#",
            note or "The team updated your bug report."
        ),
        subtype="html"
    )

    with smtplib.SMTP(app.config["SMTP_HOST"], int(app.config["SMTP_PORT"])) as smtp:
        smtp.starttls()
        smtp.login(app.config["SMTP_USERNAME"], app.config["SMTP_PASSWORD"])
        smtp.send_message(message)

    return True


def infer_bug_priority(title, description):
    text = f"{title} {description}".lower()
    critical_terms = ["crash", "down", "payment", "security", "data loss", "not opening", "cannot login", "login failed"]
    high_terms = ["login", "not working", "does not respond", "error", "broken", "blocked", "unable"]
    low_terms = ["typo", "color", "alignment", "spacing", "text", "small"]

    if any(term in text for term in critical_terms):
        return "Critical"
    if any(term in text for term in high_terms):
        return "High"
    if any(term in text for term in low_terms):
        return "Low"
    return "Medium"


def clean_bug_title(title):
    text = re.sub(r"\s+", " ", (title or "").strip())
    text = re.sub(r"^(bug|issue)\s*[:-]?\s*", "", text, flags=re.IGNORECASE)
    if not text:
        return "Bug report"
    return text[0].upper() + text[1:]


def build_repro_steps(description):
    parts = re.split(r"[.\n]", description or "")
    cleaned = [part.strip(" -") for part in parts if part.strip()]
    if len(cleaned) >= 3:
        return cleaned[:4]
    return [
        "Open the affected page or app flow.",
        "Follow the steps described by the reporter.",
        "Observe the unexpected behavior or failed response.",
        "Compare the actual result with the expected behavior."
    ]


def tokenize_text(value):
    return {
        token
        for token in re.findall(r"[a-z0-9]+", (value or "").lower())
        if len(token) > 2 and token not in COMMON_BUG_WORDS
    }


def normalize_text(value):
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def build_duplicate_confidence(score):
    if score >= 8:
        return "Strong match"
    if score >= 5:
        return "Possible match"
    return "Related report"


def find_similar_bugs(title, description, app_name, limit=3):
    normalized_title = normalize_text(title)
    normalized_app = normalize_text(app_name)
    title_tokens = tokenize_text(title)
    description_tokens = tokenize_text(description)
    app_tokens = tokenize_text(app_name)
    needle_tokens = title_tokens | description_tokens | app_tokens

    if not needle_tokens and not normalized_title:
        return []

    similar = []
    for bug in get_bugs():
        bug_title = bug["title"] or ""
        bug_description = bug["description"] or ""
        bug_app_name = bug["app_name"] or ""
        haystack_title_tokens = tokenize_text(bug_title)
        haystack_description_tokens = tokenize_text(bug_description)
        haystack_app_tokens = tokenize_text(bug_app_name)
        haystack_tokens = haystack_title_tokens | haystack_description_tokens | haystack_app_tokens
        overlap = needle_tokens & haystack_tokens
        title_overlap = title_tokens & haystack_title_tokens
        exact_app_match = bool(normalized_app and normalized_app == normalize_text(bug_app_name))
        title_ratio = SequenceMatcher(None, normalized_title, normalize_text(bug_title)).ratio() if normalized_title and bug_title else 0
        description_overlap = description_tokens & haystack_description_tokens

        if not overlap and title_ratio < 0.72:
            continue

        score = (len(title_overlap) * 3) + len(description_overlap) + len(app_tokens & haystack_app_tokens)
        if exact_app_match:
            score += 3
        if title_ratio >= 0.82:
            score += 4
        elif title_ratio >= 0.68:
            score += 2

        should_include = (
            len(title_overlap) >= 2 or
            (exact_app_match and len(overlap) >= 2) or
            title_ratio >= 0.72
        )

        if should_include and score >= 4:
            similar.append({
                "id": bug["id"],
                "title": bug_title or "Bug report",
                "status": bug["status"] or "Open",
                "app_name": bug_app_name or "General",
                "score": score,
                "confidence": build_duplicate_confidence(score),
                "shared_terms": sorted(list(overlap))[:4],
            })

    similar.sort(key=lambda item: (-item["score"], -item["id"]))
    return similar[:limit]


def build_resolution_summary(bug, resolution_note):
    fixer_name = format_display_name(bug["assigned_to"])
    app_name = bug["app_name"] or "the reported app"
    note = resolution_note or "The issue was corrected and verified by the fixer."
    return (
        f"The issue in {app_name} was resolved by {fixer_name}. "
        f"The team identified the bug, applied the fix, and marked the report as complete. "
        f"Resolution note: {note}"
    )


def build_local_bug_ai(title, description, app_name, selected_priority):
    suggested_priority = infer_bug_priority(title, description)
    priority_note = suggested_priority if suggested_priority != "Critical" else "High"
    clean_title = clean_bug_title(title)
    clean_app = app_name or "the affected app"
    repro_steps = build_repro_steps(description)
    summary = (
        f"{clean_title} is affecting {clean_app}. The report should be checked by reproducing "
        "the user flow, reviewing recent changes, and confirming the expected behavior."
    )
    suspected_cause = (
        "Likely causes include a broken UI handler, validation issue, API failure, or recent code change "
        "around the affected workflow."
    )
    fix_plan = json.dumps([
        "Reproduce the issue using the reporter details.",
        "Check browser console, server logs, and recent commits for errors.",
        "Patch the failing validation, event handler, API call, or data flow.",
        "Verify the fix and add a clear resolution note for the reporter."
    ])
    return {
        "clean_title": clean_title,
        "summary": summary,
        "priority": priority_note or selected_priority or "Medium",
        "suspected_cause": suspected_cause,
        "fix_plan": fix_plan,
        "repro_steps": json.dumps(repro_steps),
    }


def parse_ai_fix_plan(value):
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass
    return [line.strip("- ").strip() for line in str(value).splitlines() if line.strip()]


def extract_response_text(payload):
    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                return content.get("text", "")
    return ""


def parse_json_object(text):
    if not text:
        return None

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None

    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def analyze_bug_with_ai(bug):
    if not ai_is_configured():
        return None, "OPENAI_API_KEY is not configured yet."

    prompt = f"""
You are an expert bug triage assistant.
Return only valid JSON with these keys:
summary, severity, suspected_cause, debugging_steps, fixer_update, user_reply

Bug title: {bug['title'] or 'Untitled bug'}
App or website: {bug['app_name'] or 'Not specified'}
Priority: {bug['priority'] or 'Not specified'}
Status: {bug['status'] or 'Open'}
Reporter: {bug['reporter'] or 'Unknown'}
Assigned to: {bug['assigned_to'] or 'Unassigned'}
Description: {bug['description'] or 'No description'}
Resolution note: {bug['resolution_note'] or 'None yet'}

Rules:
- summary: 1 short paragraph
- severity: one of Critical, High, Medium, Low
- suspected_cause: 1 short paragraph
- debugging_steps: array of 4 short action items
- fixer_update: a short professional progress note
- user_reply: a short friendly update for the user
"""

    body = json.dumps({
        "model": app.config["OPENAI_MODEL"],
        "input": prompt
    }).encode("utf-8")

    req = urllib_request.Request(
        "https://api.openai.com/v1/responses",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {app.config['OPENAI_API_KEY']}"
        },
        method="POST"
    )

    with urllib_request.urlopen(req, timeout=45) as response:
        payload = json.loads(response.read().decode("utf-8"))

    parsed = parse_json_object(extract_response_text(payload))
    if not parsed:
        return None, "The AI response could not be parsed."

    return parsed, None


def build_bug_snapshot(bugs, limit=12):
    snapshot = []
    for bug in bugs[:limit]:
        snapshot.append({
            "id": bug["id"],
            "title": bug["title"] or "Bug report",
            "app_name": bug["app_name"] or "General",
            "status": bug["status"] or "Open",
            "priority": bug["priority"] or "Medium",
            "assigned_to": format_display_name(bug["assigned_to"]),
            "reporter": format_display_name(bug["reporter"]),
            "created_at": bug["created_at"] or "Recently",
        })
    return snapshot


def build_admin_ai_insights(bugs):
    open_bugs = [bug for bug in bugs if bug["status"] not in ("Resolved", "Closed")]
    priority_counter = Counter((bug["priority"] or "Medium") for bug in bugs)
    app_counter = Counter((bug["app_name"] or "General") for bug in bugs)
    fixer_counter = Counter(format_display_name(bug["assigned_to"]) for bug in open_bugs if bug["assigned_to"])

    oldest_open = sorted(
        open_bugs,
        key=lambda bug: parse_bug_datetime(bug["created_at"]) or datetime.max
    )[:3]

    cards = [
        {
            "label": "High priority pressure",
            "value": priority_counter.get("High", 0),
            "detail": "High-priority bugs currently tracked"
        },
        {
            "label": "Top problem area",
            "value": app_counter.most_common(1)[0][0] if app_counter else "General",
            "detail": "App with the most bug volume"
        },
        {
            "label": "Busiest fixer",
            "value": fixer_counter.most_common(1)[0][0] if fixer_counter else "Unassigned",
            "detail": "Highest active workload right now"
        },
    ]

    actions = []
    if oldest_open:
        actions.append(
            f"Review oldest open bug #{oldest_open[0]['id']} first to reduce backlog age."
        )
    if priority_counter.get("High", 0) >= 3:
        actions.append("High-priority volume is building up. Consider temporarily rebalancing fixer workload.")
    if app_counter:
        hottest_app, hottest_count = app_counter.most_common(1)[0]
        actions.append(f"{hottest_app} has {hottest_count} reported issues, so it is the best candidate for root-cause review.")
    if fixer_counter:
        busiest_name, busiest_count = fixer_counter.most_common(1)[0]
        actions.append(f"{busiest_name} is carrying {busiest_count} active bugs. Move one or two tickets if response time slips.")

    return {
        "cards": cards,
        "actions": actions[:4],
        "oldest_open": oldest_open
    }


def build_assistant_context(bugs, role):
    metrics = build_bug_metrics(bugs)
    return {
        "role": role,
        "metrics": {
            "total": metrics["total"],
            "open": metrics["open"],
            "in_progress": metrics["in_progress"],
            "resolved": metrics["resolved"],
        },
        "bugs": build_bug_snapshot(bugs, limit=12)
    }


def fallback_assistant_reply(message, context):
    metrics = context["metrics"]
    lower_message = message.lower()

    if "open" in lower_message:
        return f"There are {metrics['open']} open bugs and {metrics['in_progress']} in progress right now."
    if "resolved" in lower_message or "closed" in lower_message:
        return f"So far, {metrics['resolved']} bugs are resolved or closed."
    if "fixer" in lower_message or "assigned" in lower_message:
        if context["bugs"]:
            top_bug = context["bugs"][0]
            return f"The latest visible bug is #{top_bug['id']} assigned to {top_bug['assigned_to']} with status {top_bug['status']}."
    if "summary" in lower_message or "summarize" in lower_message:
        if context["bugs"]:
            summaries = [f"#{bug['id']} {bug['title']} ({bug['status']})" for bug in context["bugs"][:3]]
            return "Here is a quick summary: " + "; ".join(summaries) + "."

    return (
        f"I can help with bug summaries, workload, and status questions. "
        f"Right now I can see {metrics['total']} visible bugs, with {metrics['open']} open and {metrics['in_progress']} in progress."
    )


def ask_assistant(message, bugs, role, user_name):
    context = build_assistant_context(bugs, role)

    if not ai_is_configured():
        return fallback_assistant_reply(message, context)

    prompt = f"""
You are BugTracker AI Assistant. Answer concisely and helpfully using the provided bug tracker data.
If the answer depends on the data, rely on the data.

User role: {role}
User name: {format_display_name(user_name)}
Question: {message}

Visible workspace data:
{json.dumps(context, indent=2)}
"""

    body = json.dumps({
        "model": app.config["OPENAI_MODEL"],
        "input": prompt
    }).encode("utf-8")

    req = urllib_request.Request(
        "https://api.openai.com/v1/responses",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {app.config['OPENAI_API_KEY']}"
        },
        method="POST"
    )

    try:
        with urllib_request.urlopen(req, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return extract_response_text(payload) or fallback_assistant_reply(message, context)
    except Exception:
        return fallback_assistant_reply(message, context)


def parse_bug_datetime(value):
    if not value:
        return None

    try:
        return datetime.strptime(value, "%d %b %Y, %I:%M %p")
    except ValueError:
        return None


def build_fixer_summaries(fixers, bugs):
    active_statuses = {"Open", "In Progress"}
    summaries = []
    active_counts = Counter(
        bug["assigned_to"]
        for bug in bugs
        if bug["assigned_to"] and bug["status"] in active_statuses
    )
    max_active = max(active_counts.values(), default=1)

    for fixer in fixers:
        username = fixer["username"]
        owned_bugs = [bug for bug in bugs if bug["assigned_to"] == username]
        active = [bug for bug in owned_bugs if bug["status"] in active_statuses]
        resolved = [bug for bug in owned_bugs if bug["status"] in {"Resolved", "Closed"}]
        latest_bug = max(
            owned_bugs,
            key=lambda bug: parse_bug_datetime(bug["created_at"]) or datetime.min,
            default=None
        )
        summaries.append({
            "username": username,
            "label": format_display_name(username),
            "email": fixer["email"] or "No email",
            "assigned_total": len(owned_bugs),
            "active_total": len(active),
            "resolved_total": len(resolved),
            "active_percent": round((len(active) / max_active) * 100) if max_active else 0,
            "latest_title": latest_bug["title"] if latest_bug else "No bugs assigned yet",
            "latest_status": latest_bug["status"] if latest_bug else "Idle",
        })

    return sorted(summaries, key=lambda item: (-item["active_total"], item["label"].lower()))


def build_reporter_bug_cards(bugs):
    status_order = ["Open", "In Progress", "Resolved", "Closed"]
    next_step_map = {
        "Open": "A fixer will review the report and start investigation.",
        "In Progress": "The fixer is working on the issue and will post the next update soon.",
        "Resolved": "Please review the fix and confirm the result on your side.",
        "Closed": "This report is complete. Reopen only if the issue returns."
    }
    cards = []

    for bug in bugs:
        events = get_bug_events(bug["id"])
        last_event = events[-1] if events else None
        active_index = status_order.index(bug["status"]) if bug["status"] in status_order else 0
        progress_percent = round(((active_index + 1) / len(status_order)) * 100)
        step_rows = []
        for index, label in enumerate(status_order):
            step_rows.append({
                "label": label,
                "active": index <= active_index,
                "current": label == bug["status"],
            })

        cards.append({
            "bug": bug,
            "events": events[-4:],
            "steps": step_rows,
            "last_update": last_event["message"] if last_event else "Your report was created and is waiting for the next update.",
            "last_update_at": last_event["created_at"] if last_event else bug["created_at"],
            "progress_percent": progress_percent,
            "next_step": next_step_map.get(bug["status"], "Your report is being monitored by the team."),
        })

    return cards


def build_bug_metrics(bugs):
    total = len(bugs)
    open_count = len([bug for bug in bugs if bug["status"] == "Open"])
    in_progress_count = len([bug for bug in bugs if bug["status"] == "In Progress"])
    resolved_count = len([bug for bug in bugs if bug["status"] in ("Resolved", "Closed")])

    priority_counter = Counter((bug["priority"] or "Medium").title() for bug in bugs)
    status_counter = Counter((bug["status"] or "Open") for bug in bugs)
    assignee_counter = Counter(
        bug["assigned_to"] or "Unassigned"
        for bug in bugs
        if bug["assigned_to"]
    )

    today = datetime.now()
    trend_labels = []
    new_bug_values = []
    resolved_values = []

    for day_offset in range(6, -1, -1):
        current_day = today - timedelta(days=day_offset)
        label = current_day.strftime("%d %b")
        trend_labels.append(label)
        created_total = 0
        resolved_total = 0

        for bug in bugs:
            created_at = parse_bug_datetime(bug["created_at"])
            fixed_at = parse_bug_datetime(bug["fixed_at"])

            if created_at and created_at.date() == current_day.date():
                created_total += 1

            if fixed_at and fixed_at.date() == current_day.date():
                resolved_total += 1

        new_bug_values.append(created_total)
        resolved_values.append(resolved_total)

    workload = []
    max_assigned = max(assignee_counter.values(), default=1)
    for assignee, count in assignee_counter.most_common():
        workload.append({
            "name": assignee,
            "count": count,
            "percent": round((count / max_assigned) * 100) if max_assigned else 0
        })

    severity_distribution = [
        {"label": "High", "count": priority_counter.get("High", 0), "color": "#d97706"},
        {"label": "Medium", "count": priority_counter.get("Medium", 0), "color": "#4f46e5"},
        {"label": "Low", "count": priority_counter.get("Low", 0), "color": "#16a34a"},
    ]

    return {
        "total": total,
        "open": open_count,
        "in_progress": in_progress_count,
        "resolved": resolved_count,
        "status_counter": dict(status_counter),
        "priority_counter": dict(priority_counter),
        "workload": workload,
        "trend_labels": trend_labels,
        "new_bug_values": new_bug_values,
        "resolved_values": resolved_values,
        "severity_distribution": severity_distribution
    }


def build_project_summaries(bugs):
    projects = defaultdict(list)

    for bug in bugs:
        project_name = (bug["app_name"] or "General").strip() or "General"
        projects[project_name].append(bug)

    project_summaries = []
    for name, project_bugs in projects.items():
        metrics = build_bug_metrics(project_bugs)
        latest_bug = max(
            project_bugs,
            key=lambda bug: parse_bug_datetime(bug["created_at"]) or datetime.min
        )
        project_summaries.append({
            "name": name,
            "total": metrics["total"],
            "open": metrics["open"],
            "in_progress": metrics["in_progress"],
            "resolved": metrics["resolved"],
            "latest_title": latest_bug["title"] or "Bug report",
            "latest_created_at": latest_bug["created_at"] or "Recently",
        })

    return sorted(project_summaries, key=lambda project: (-project["total"], project["name"].lower()))


@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = login_user(request.form["username"], request.form["password"])

        if user:
            session["user"] = user["username"]
            session["role"] = user["role"] or "user"
            session["email"] = user["email"] or ""
            flash(f"Welcome back, {user['username']}.", "success")
            if user["role"] == "fixer":
                return redirect(url_for("view_bugs"))
            return redirect(url_for("dashboard"))

        flash("Invalid username or password.", "error")

    return render_template(
        "login_modern.html",
        quick_accounts=get_login_quick_accounts(),
        login_fixers=get_login_fixers()
    )


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        if request.form["password"] != request.form["confirm_password"]:
            flash("Passwords do not match.", "error")
            return redirect(url_for("signup"))

        success = register_user(
            request.form["username"],
            request.form["password"],
            request.form["email"],
            request.form["role"]
        )

        if success:
            flash("Account created successfully. Please log in.", "success")
            return redirect(url_for("login"))

        flash("That username already exists.", "error")
        return redirect(url_for("signup"))

    return render_template("signup_modern.html")


@app.route("/dashboard")
def dashboard():
    if not is_logged_in():
        return redirect(url_for("login"))

    if is_admin():
        bugs = get_all_bugs()
        notifications = [bug for bug in bugs if bug["status"] in ("Resolved", "Closed")][:5]
    elif is_fixer():
        bugs = get_bugs_for_fixer(session["user"])
        notifications = [bug for bug in bugs if bug["status"] in ("Resolved", "Closed")]
    else:
        bugs = get_bugs_for_reporter(session["user"])
        notifications = get_fixed_notifications(session["user"])

    metrics = build_bug_metrics(bugs)

    return render_template(
        "dashboard.html",
        user=session["user"],
        role=session.get("role", "user"),
        total=metrics["total"],
        pending=metrics["open"] + metrics["in_progress"],
        fixed=metrics["resolved"],
        notifications=notifications,
        metrics=metrics,
        page_title="Dashboard",
        active_page="dashboard",
        chart_data=json.dumps({
            "trendLabels": metrics["trend_labels"],
            "newBugValues": metrics["new_bug_values"],
            "resolvedValues": metrics["resolved_values"],
            "severityLabels": [item["label"] for item in metrics["severity_distribution"]],
            "severityValues": [item["count"] for item in metrics["severity_distribution"]],
            "severityColors": [item["color"] for item in metrics["severity_distribution"]]
        })
    )


# 🔥 ADD BUG (AUTO ASSIGN)
@app.route("/add", methods=["GET", "POST"])
def add_bug():
    if not is_logged_in():
        return redirect(url_for("login"))

    if is_fixer():
        flash("Fixer accounts cannot create new bug reports.", "info")
        return redirect(url_for("view_bugs"))

    duplicate_matches = []

    if request.method == "POST":
        duplicate_matches = find_similar_bugs(
            request.form["title"],
            request.form["description"],
            request.form["app_name"]
        )
        assigned_fixer = assign_fixer()
        ai_triage = build_local_bug_ai(
            request.form["title"],
            request.form["description"],
            request.form["app_name"],
            request.form["priority"]
        )
        bug_id = add_bug_db(
            ai_triage["clean_title"],
            request.form["description"],
            request.form["priority"],
            "Open",
            assigned_fixer,
            session["user"],
            request.form["app_name"],
            "\n".join(json.loads(ai_triage["repro_steps"])),
            "",
            "",
            request.form["contact"],
            save_uploaded_screenshot(request.files.get("screenshot"))
        )

        update_bug_ai_fields(
            bug_id,
            ai_triage["summary"],
            ai_triage["priority"],
            ai_triage["suspected_cause"],
            ai_triage["fix_plan"],
            ai_triage["repro_steps"],
            ""
        )
        record_bug_event(
            bug_id,
            "created",
            session["user"],
            f"Bug reported and automatically assigned to {format_display_name(assigned_fixer)}."
        )
        record_bug_event(
            bug_id,
            "ai_triage",
            "BugTracker AI",
            f"AI suggested priority: {ai_triage['priority']}. {ai_triage['summary']}"
        )
        if duplicate_matches:
            record_bug_event(
                bug_id,
                "duplicate_check",
                "BugTracker AI",
                "Possible similar reports were detected in the workspace before submission."
            )

        created_bug = get_bug_by_id(bug_id)
        bug_url = build_bug_url(bug_id)

        try:
            if created_bug and send_bug_reported_email(created_bug, bug_url):
                flash("Bug report submitted and confirmation email sent.", "success")
            else:
                flash("Bug report submitted successfully.", "success")
        except Exception:
            flash("Bug report submitted, but confirmation email could not be sent.", "info")
        if duplicate_matches:
            flash("AI noticed similar existing bug reports. Review the suggested matches below.", "info")
        return redirect(url_for("view_bugs"))

    return render_template(
        "add_bug.html",
        duplicate_matches=duplicate_matches,
        user_email=session.get("email", ""),
        role=session.get("role", "user"),
        active_page="new-bug"
    )


# VIEW BUGS
@app.route("/view")
def view_bugs():
    if not is_logged_in():
        return redirect(url_for("login"))

    status_filter = request.args.get("status", "").strip()
    priority_filter = request.args.get("priority", "").strip()
    search_query = request.args.get("q", "").strip()

    fixer_filter = request.args.get("fixer", "").strip() if is_admin() else ""

    if is_admin():
        bugs = get_all_bugs(status_filter, priority_filter, fixer_filter, search_query)
        page_title = "Admin Bug Control"
        page_subtitle = "Manage all bug reports, assignments, statuses, and report activity in one place."
    elif is_fixer():
        bugs = get_bugs_for_fixer(session["user"], status_filter, priority_filter, search_query)
        page_title = "Assigned Bugs"
        page_subtitle = "Review the bugs assigned to you, update progress, and close them when fixed."
    else:
        bugs = get_bugs_for_reporter(session["user"], status_filter, priority_filter, search_query)
        page_title = "My Bug Reports"
        page_subtitle = "Track every bug you submitted, see who is fixing it, and check the latest status update."

    return render_template(
        "reports.html",
        bugs=bugs,
        reporter_cards=build_reporter_bug_cards(bugs) if session.get("role", "user") == "user" else [],
        role=session.get("role", "user"),
        page_title=page_title,
        page_subtitle=page_subtitle,
        fixers=get_fixers(),
        active_page="bugs",
        filters={
            "status": status_filter,
            "priority": priority_filter,
            "q": search_query,
            "fixer": fixer_filter
        }
    )


# DELETE BUG
@app.route("/delete/<int:id>")
def delete_bug(id):
    if not is_logged_in():
        return redirect(url_for("login"))

    bug = get_bug_by_id(id)
    if not bug or not can_access_bug(bug):
        flash("You do not have permission to delete this bug report.", "error")
        return redirect(url_for("view_bugs"))

    delete_bug_db(id)
    flash("Bug report deleted.", "success")
    return redirect(url_for("view_bugs"))


# 🔥 EDIT BUG (KEEP MANUAL CHANGE ALLOWED)
@app.route("/edit/<int:id>", methods=["GET", "POST"])
def edit_bug(id):
    if not is_logged_in():
        return redirect(url_for("login"))

    bug = get_bug_by_id(id)
    if not bug:
        flash("Bug report not found.", "error")
        return redirect(url_for("view_bugs"))

    if not (is_fixer() or is_admin()):
        flash("Only fixer or admin accounts can update bug status.", "error")
        return redirect(url_for("view_bugs"))

    if is_fixer() and bug["assigned_to"] != session["user"]:
        flash("This bug is assigned to a different fixer.", "error")
        return redirect(url_for("view_bugs"))

    if request.method == "POST":
        was_fixed_before = bug["status"] in ("Resolved", "Closed")
        previous_status = bug["status"] or "Open"
        previous_assignee = bug["assigned_to"] or ""
        next_status = request.form["status"]
        next_assignee = request.form["assigned_to"] if is_admin() else bug["assigned_to"]
        progress_note = request.form.get("progress_note", "").strip()
        screenshot_path = bug["screenshot_path"] or ""
        uploaded_screenshot = save_uploaded_screenshot(request.files.get("screenshot"))
        if uploaded_screenshot:
            screenshot_path = uploaded_screenshot

        update_bug(
            id,
            request.form["title"],
            request.form["description"],
            request.form["priority"],
            next_status,
            next_assignee,
            request.form["app_name"],
            "",
            "",
            "",
            request.form["contact"],
            request.form["resolution_note"],
            screenshot_path
        )

        updated_bug = get_bug_by_id(id)
        if updated_bug["status"] in {"Resolved", "Closed"}:
            resolution_summary = build_resolution_summary(updated_bug, request.form["resolution_note"])
            update_bug_ai_fields(
                id,
                updated_bug["ai_summary"] or "",
                updated_bug["ai_priority"] or "",
                updated_bug["ai_suspected_cause"] or "",
                updated_bug["ai_fix_plan"] or "",
                updated_bug["ai_repro_steps"] or "",
                resolution_summary
            )
            updated_bug = get_bug_by_id(id)
        if previous_assignee != next_assignee:
            record_bug_event(
                id,
                "reassigned",
                session["user"],
                f"Bug reassigned from {format_display_name(previous_assignee)} to {format_display_name(next_assignee)}."
            )

        if previous_status != next_status:
            record_bug_event(
                id,
                "status",
                session["user"],
                f"Status changed from {previous_status} to {next_status}."
            )

        if progress_note:
            add_comment(id, session["user"], session.get("role", "user"), progress_note)
            record_bug_event(id, "progress", session["user"], progress_note)

        if not was_fixed_before and updated_bug["status"] in ("Resolved", "Closed"):
            try:
                if send_bug_fixed_email(updated_bug, build_bug_url(id)):
                    flash("Bug updated and email notification sent.", "success")
                else:
                    flash("Bug updated. Email notification was skipped because SMTP is not configured or the contact is not an email.", "info")
            except Exception:
                flash("Bug updated, but email notification failed to send.", "error")
        else:
            if previous_status != updated_bug["status"] or previous_assignee != updated_bug["assigned_to"]:
                try:
                    send_bug_status_email(
                        updated_bug,
                        build_bug_url(id),
                        progress_note or f"Bug is now {updated_bug['status']} and assigned to {format_display_name(updated_bug['assigned_to'])}."
                    )
                except Exception:
                    pass
            flash("Bug updated successfully.", "success")

        return redirect(url_for("view_bugs"))

    return render_template(
        "edit_bug.html",
        bug=bug,
        fixers=get_fixers(),
        role=session.get("role", "user"),
        active_page="bugs"
    )


@app.route("/bug/<int:id>/status", methods=["POST"])
def update_bug_status(id):
    if not is_logged_in():
        return redirect(url_for("login"))

    bug = get_bug_by_id(id)
    if not bug:
        flash("Bug report not found.", "error")
        return redirect(url_for("view_bugs"))

    if not (is_fixer() or is_admin()):
        flash("Only fixer or admin accounts can change bug status.", "error")
        return redirect(url_for("view_bugs"))

    if is_fixer() and bug["assigned_to"] != session["user"]:
        flash("This bug is assigned to a different fixer.", "error")
        return redirect(url_for("view_bugs"))

    next_status = request.form.get("status", "").strip()
    allowed_statuses = {"Open", "In Progress", "Resolved", "Closed"}
    if next_status not in allowed_statuses:
        flash("Invalid bug status.", "error")
        return redirect(url_for("view_bugs"))

    was_fixed_before = bug["status"] in ("Resolved", "Closed")
    previous_status = bug["status"] or "Open"
    resolution_note = request.form.get("resolution_note", "").strip() or (bug["resolution_note"] or "")
    if next_status in {"Resolved", "Closed"} and not resolution_note:
        resolution_note = "The fixer marked this issue as completed."

    update_bug(
        id,
        bug["title"],
        bug["description"],
        bug["priority"],
        next_status,
        bug["assigned_to"],
        bug["app_name"],
        bug["steps"] or "",
        bug["expected_result"] or "",
        bug["actual_result"] or "",
        bug["contact"],
        resolution_note,
        bug["screenshot_path"] or ""
    )

    updated_bug = get_bug_by_id(id)
    if updated_bug["status"] in {"Resolved", "Closed"}:
        resolution_summary = build_resolution_summary(updated_bug, resolution_note)
        update_bug_ai_fields(
            id,
            updated_bug["ai_summary"] or "",
            updated_bug["ai_priority"] or "",
            updated_bug["ai_suspected_cause"] or "",
            updated_bug["ai_fix_plan"] or "",
            updated_bug["ai_repro_steps"] or "",
            resolution_summary
        )
        updated_bug = get_bug_by_id(id)
    if previous_status != next_status:
        record_bug_event(
            id,
            "status",
            session["user"],
            f"Status changed from {previous_status} to {next_status}."
        )

    if not was_fixed_before and updated_bug["status"] in ("Resolved", "Closed"):
        try:
            send_bug_fixed_email(updated_bug, build_bug_url(id))
        except Exception:
            pass
    elif previous_status != next_status:
        try:
            send_bug_status_email(updated_bug, build_bug_url(id), f"Bug #{id} moved to {next_status}.")
        except Exception:
            pass

    flash(f"Bug #{id} moved to {next_status}.", "success")
    return redirect(url_for("view_bugs"))


@app.route("/bug/<int:id>", methods=["GET", "POST"])
def bug_detail(id):
    if not is_logged_in():
        return redirect(url_for("login"))

    bug = get_bug_by_id(id)
    if not bug or not can_access_bug(bug):
        flash("You do not have permission to view this bug report.", "error")
        return redirect(url_for("view_bugs"))

    ai_analysis = {
        "summary": bug["ai_summary"] or "",
        "severity": bug["ai_priority"] or "",
        "suspected_cause": bug["ai_suspected_cause"] or "",
        "debugging_steps": parse_ai_fix_plan(bug["ai_fix_plan"] or ""),
        "fixer_update": "I am reviewing the reported flow and will update the status as soon as the cause is confirmed.",
        "user_reply": "Thanks for reporting this. We have assigned it to a fixer and will update you as progress is made.",
    } if bug["ai_summary"] or bug["ai_priority"] or bug["ai_suspected_cause"] else None

    if request.method == "POST":
        action = request.form.get("action", "comment").strip()

        if action == "analyze":
            try:
                ai_analysis, error_message = analyze_bug_with_ai(bug)
                if error_message:
                    flash(error_message, "info")
                else:
                    update_bug_ai_fields(
                        id,
                        ai_analysis.get("summary", ""),
                        ai_analysis.get("severity", ""),
                        ai_analysis.get("suspected_cause", ""),
                        json.dumps(ai_analysis.get("debugging_steps", [])),
                        bug["ai_repro_steps"] or "",
                        bug["ai_resolution_summary"] or ""
                    )
                    record_bug_event(
                        id,
                        "ai_triage",
                        "BugTracker AI",
                        f"AI analysis refreshed with severity {ai_analysis.get('severity', 'Medium')}."
                    )
                    flash("AI analysis generated.", "success")
            except Exception:
                flash("AI analysis failed. Check your OpenAI API key and internet access.", "error")
        elif action == "progress_note":
            if not (is_fixer() or is_admin()):
                flash("Only fixers or admins can add progress notes.", "error")
                return redirect(url_for("bug_detail", id=id))

            message = request.form["message"].strip()
            if message:
                add_comment(id, session["user"], session.get("role", "user"), message)
                record_bug_event(id, "progress", session["user"], message)
                try:
                    send_bug_status_email(bug, build_bug_url(id), message)
                except Exception:
                    pass
                flash("Progress note added.", "success")
            else:
                flash("Progress note cannot be empty.", "error")

            return redirect(url_for("bug_detail", id=id))
        else:
            message = request.form["message"].strip()
            if message:
                add_comment(id, session["user"], session.get("role", "user"), message)
                flash("Comment added.", "success")
            else:
                flash("Comment cannot be empty.", "error")

            return redirect(url_for("bug_detail", id=id))

    return render_template(
        "bug_detail.html",
        bug=bug,
        comments=get_comments_for_bug(id),
        events=get_bug_events(id),
        role=session.get("role", "user"),
        active_page="bugs",
        ai_analysis=ai_analysis,
        ai_configured=ai_is_configured()
    )


@app.route("/assistant/query", methods=["POST"])
def assistant_query():
    if not is_logged_in():
        return jsonify({"error": "Not logged in"}), 401

    payload = request.get_json(silent=True) or {}
    message = (payload.get("message") or "").strip()
    if not message:
        return jsonify({"error": "Message is required"}), 400

    if is_admin():
        bugs = get_all_bugs()
    elif is_fixer():
        bugs = get_bugs_for_fixer(session["user"])
    else:
        bugs = get_bugs_for_reporter(session["user"])

    reply = ask_assistant(message, bugs, session.get("role", "user"), session.get("user", "User"))
    return jsonify({"reply": reply})


@app.route("/bug/check-duplicates", methods=["POST"])
def check_duplicates():
    if not is_logged_in():
        return jsonify({"matches": []}), 401

    payload = request.get_json(silent=True) or {}
    title = (payload.get("title") or "").strip()
    description = (payload.get("description") or "").strip()
    app_name = (payload.get("app_name") or "").strip()

    matches = find_similar_bugs(title, description, app_name)
    return jsonify({"matches": matches})


@app.route("/admin", methods=["GET", "POST"])
def admin_panel():
    if not is_logged_in():
        return redirect(url_for("login"))

    if not is_admin():
        flash("Only admin accounts can access the admin panel.", "error")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()

        if not username or not email or not password:
            flash("All fixer account fields are required.", "error")
            return redirect(url_for("admin_panel"))

        if create_fixer_account(username, email, password):
            flash(f"Fixer account created for {username}.", "success")
        else:
            flash("Could not create fixer account. The username may already exist.", "error")

        return redirect(url_for("admin_panel"))

    bugs = get_all_bugs()
    users = get_all_users()
    fixers = get_fixers()
    fixer_workload = Counter(bug["assigned_to"] for bug in bugs if bug["assigned_to"])
    smtp_status = build_smtp_status()

    return render_template(
        "admin.html",
        bugs=bugs,
        users=users,
        fixers=fixers,
        fixer_summaries=build_fixer_summaries(fixers, bugs),
        total_bugs=len(bugs),
        total_users=len(users),
        total_fixers=len(fixers),
        open_bugs=len([bug for bug in bugs if bug["status"] == "Open"]),
        in_progress_bugs=len([bug for bug in bugs if bug["status"] == "In Progress"]),
        resolved_bugs=len([bug for bug in bugs if bug["status"] in ("Resolved", "Closed")]),
        active_page="admin",
        ai_insights=build_admin_ai_insights(bugs),
        email_configured=smtp_is_configured(),
        smtp_status=smtp_status,
        fixer_workload=fixer_workload
    )


@app.route("/admin/fixer/<username>/delete", methods=["POST"])
def delete_fixer(username):
    if not is_logged_in():
        return redirect(url_for("login"))

    if not is_admin():
        flash("Only admin accounts can remove fixers.", "error")
        return redirect(url_for("dashboard"))

    if delete_fixer_account(username):
        flash(f"Fixer {format_display_name(username)} removed.", "success")
    else:
        flash("This fixer still has assigned bugs. Reassign those bugs before removing the account.", "error")

    return redirect(url_for("admin_panel"))


@app.route("/bug/<int:id>/assign", methods=["POST"])
def reassign_bug(id):
    if not is_logged_in():
        return redirect(url_for("login"))

    if not is_admin():
        flash("Only admin accounts can reassign bugs.", "error")
        return redirect(url_for("view_bugs"))

    bug = get_bug_by_id(id)
    if not bug:
        flash("Bug report not found.", "error")
        return redirect(url_for("view_bugs"))

    next_assignee = request.form.get("assigned_to", "").strip()
    assignee = get_user_by_username(next_assignee)
    if not assignee or assignee["role"] != "fixer":
        flash("Choose a valid fixer.", "error")
        return redirect(url_for("view_bugs"))

    previous_assignee = bug["assigned_to"] or ""
    if previous_assignee == next_assignee:
        flash("Bug is already assigned to that fixer.", "info")
        return redirect(url_for("view_bugs"))

    update_bug(
        id,
        bug["title"],
        bug["description"],
        bug["priority"],
        bug["status"],
        next_assignee,
        bug["app_name"],
        bug["steps"] or "",
        bug["expected_result"] or "",
        bug["actual_result"] or "",
        bug["contact"],
        bug["resolution_note"] or "",
        bug["screenshot_path"] or ""
    )

    updated_bug = get_bug_by_id(id)
    record_bug_event(
        id,
        "reassigned",
        session["user"],
        f"Bug reassigned from {format_display_name(previous_assignee)} to {format_display_name(next_assignee)}."
    )
    try:
        send_bug_status_email(
            updated_bug,
            build_bug_url(id),
            f"Your bug has been reassigned to {format_display_name(next_assignee)} for faster handling."
        )
    except Exception:
        pass

    flash(f"Bug #{id} reassigned to {format_display_name(next_assignee)}.", "success")
    return redirect(url_for("view_bugs"))


@app.route("/profile", methods=["GET", "POST"])
def profile():
    if not is_logged_in():
        return redirect(url_for("login"))

    current_user = get_user_by_username(session["user"])
    if not current_user:
        flash("User account not found.", "error")
        return redirect(url_for("logout"))

    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not current_password or not new_password or not confirm_password:
            flash("Please fill in all password fields.", "error")
            return redirect(url_for("profile"))

        if new_password != confirm_password:
            flash("New passwords do not match.", "error")
            return redirect(url_for("profile"))

        if not login_user(session["user"], current_password):
            flash("Current password is incorrect.", "error")
            return redirect(url_for("profile"))

        if update_user_password(session["user"], new_password):
            flash("Password updated successfully.", "success")
        else:
            flash("Password could not be updated.", "error")

        return redirect(url_for("profile"))

    return render_template(
        "profile.html",
        role=session.get("role", "user"),
        active_page="profile",
        user=current_user
    )


@app.route("/projects")
def projects():
    if not is_logged_in():
        return redirect(url_for("login"))

    if is_admin():
        bugs = get_all_bugs()
    elif is_fixer():
        bugs = get_bugs_for_fixer(session["user"])
    else:
        bugs = get_bugs_for_reporter(session["user"])

    return render_template(
        "projects.html",
        role=session.get("role", "user"),
        projects=build_project_summaries(bugs),
        active_page="projects"
    )


@app.route("/analytics")
def analytics():
    if not is_logged_in():
        return redirect(url_for("login"))

    if is_admin():
        bugs = get_all_bugs()
    elif is_fixer():
        bugs = get_bugs_for_fixer(session["user"])
    else:
        bugs = get_bugs_for_reporter(session["user"])

    metrics = build_bug_metrics(bugs)
    status_rows = [
        {"label": "Open", "count": metrics["status_counter"].get("Open", 0), "color": "#dc2626"},
        {"label": "In Progress", "count": metrics["status_counter"].get("In Progress", 0), "color": "#d97706"},
        {"label": "Resolved", "count": metrics["resolved"], "color": "#16a34a"},
    ]
    max_status = max((row["count"] for row in status_rows), default=1)
    for row in status_rows:
        row["percent"] = round((row["count"] / max_status) * 100) if max_status else 0

    return render_template(
        "analytics.html",
        role=session.get("role", "user"),
        metrics=metrics,
        status_rows=status_rows,
        active_page="analytics",
        analytics_chart_data=json.dumps({
            "trendLabels": metrics["trend_labels"],
            "newBugValues": metrics["new_bug_values"],
            "resolvedValues": metrics["resolved_values"],
            "priorityLabels": [item["label"] for item in metrics["severity_distribution"]],
            "priorityValues": [item["count"] for item in metrics["severity_distribution"]],
            "priorityColors": [item["color"] for item in metrics["severity_distribution"]]
        })
    )


# LOGOUT
@app.route("/logout", methods=["GET", "POST"])
def logout():
    session.pop("user", None)
    session.pop("role", None)
    session.pop("email", None)
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


if __name__ == "__main__":
    app.run(debug=True)
