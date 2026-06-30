import os
import sqlite3
from datetime import datetime, timedelta
from collections import defaultdict
from flask import Flask, g, jsonify, request, render_template

import categorizer

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "budget.db")

CURRENCY = os.environ.get("BUDGET_CURRENCY", "RSD")

# Seed: (name, initial_balance, card_label, card_identifier)
DEFAULT_USERS = [
    ("Ognjen", 0.0, "Main card", "1900"),
    ("Neca", 0.0, "Main card", "3242"),
]

# Categories that are internal money movement, not real spending.
NON_SPENDING = {"Transfer"}

# Default income categories (expense categories come from categorizer.CATEGORIES).
INCOME_CATEGORIES = [
    "Salary",
    "Freelance",
    "Bonus",
    "Gift",
    "Refund",
    "Interest",
    "Investment",
    "Other income",
]

app = Flask(__name__)


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            initial_balance REAL NOT NULL DEFAULT 0
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'checking',
            initial_balance REAL NOT NULL DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            account_id INTEGER,
            label TEXT NOT NULL,
            identifier TEXT NOT NULL UNIQUE,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            account_id INTEGER,
            type TEXT NOT NULL CHECK (type IN ('income', 'expense')),
            amount REAL NOT NULL,
            category TEXT NOT NULL,
            subcategory TEXT,
            merchant TEXT,
            note TEXT,
            date TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'manual',
            transfer_group TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL DEFAULT 'expense'
        )
        """
    )
    # Learned merchant -> category mapping (remembers your manual choices).
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS merchant_categories (
            merchant_key TEXT PRIMARY KEY,
            category TEXT NOT NULL
        )
        """
    )
    # Editable keyword -> category/subcategory rules (the auto-categorizer).
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS category_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL,
            subcategory TEXT
        )
        """
    )

    # Make sure older databases gain the category kind column.
    cat_cols = [r[1] for r in db.execute("PRAGMA table_info(categories)").fetchall()]
    if "kind" not in cat_cols:
        db.execute("ALTER TABLE categories ADD COLUMN kind TEXT NOT NULL DEFAULT 'expense'")

    # Seed the default category lists (expense + income).
    existing = {r[0] for r in db.execute("SELECT name FROM categories").fetchall()}
    for c in categorizer.CATEGORIES:
        if c not in existing:
            db.execute("INSERT INTO categories (name, kind) VALUES (?, 'expense')", (c,))
    for c in INCOME_CATEGORIES:
        if c not in existing:
            db.execute("INSERT INTO categories (name, kind) VALUES (?, 'income')", (c,))

    # Seed the keyword rules into the DB once (from categorizer._KEYWORDS).
    if db.execute("SELECT COUNT(*) FROM category_rules").fetchone()[0] == 0:
        db.executemany(
            "INSERT OR IGNORE INTO category_rules (keyword, category, subcategory) VALUES (?, ?, ?)",
            [(kw, cat, sub) for kw, (cat, sub) in categorizer._KEYWORDS.items()],
        )

    # Migration: add account columns to older databases.
    tx_cols = [r[1] for r in db.execute("PRAGMA table_info(transactions)").fetchall()]
    if "account_id" not in tx_cols:
        db.execute("ALTER TABLE transactions ADD COLUMN account_id INTEGER")
    card_cols = [r[1] for r in db.execute("PRAGMA table_info(cards)").fetchall()]
    if "account_id" not in card_cols:
        db.execute("ALTER TABLE cards ADD COLUMN account_id INTEGER")

    count = db.execute("SELECT COUNT(*) AS c FROM users").fetchone()[0]
    if count == 0:
        for name, balance, card_label, card_id in DEFAULT_USERS:
            ucur = db.execute(
                "INSERT INTO users (name, initial_balance) VALUES (?, ?)",
                (name, balance),
            )
            acur = db.execute(
                "INSERT INTO accounts (user_id, name, type, initial_balance) VALUES (?, 'Checking', 'checking', 0)",
                (ucur.lastrowid,),
            )
            db.execute(
                "INSERT INTO cards (user_id, account_id, label, identifier) VALUES (?, ?, ?, ?)",
                (ucur.lastrowid, acur.lastrowid, card_label, card_id),
            )

    # Backfill: every user needs at least one account; assign orphan rows to it.
    for u in db.execute("SELECT id FROM users").fetchall():
        uid = u[0]
        acc = db.execute(
            "SELECT id FROM accounts WHERE user_id = ? ORDER BY id LIMIT 1", (uid,)
        ).fetchone()
        if acc is None:
            acur = db.execute(
                "INSERT INTO accounts (user_id, name, type, initial_balance) VALUES (?, 'Checking', 'checking', 0)",
                (uid,),
            )
            acc_id = acur.lastrowid
        else:
            acc_id = acc[0]
        db.execute(
            "UPDATE transactions SET account_id = ? WHERE user_id = ? AND account_id IS NULL",
            (acc_id, uid),
        )
        db.execute(
            "UPDATE cards SET account_id = ? WHERE user_id = ? AND account_id IS NULL",
            (acc_id, uid),
        )

    db.commit()
    db.close()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def row_to_dict(row):
    return {k: row[k] for k in row.keys()}


def compute_state(db, user_id):
    """Aggregate balance for a user across all their accounts."""
    accounts = db.execute(
        "SELECT id, initial_balance FROM accounts WHERE user_id = ?", (user_id,)
    ).fetchall()
    initial = sum(a["initial_balance"] for a in accounts)
    rows = db.execute(
        "SELECT type, amount FROM transactions WHERE user_id = ?", (user_id,)
    ).fetchall()
    income = sum(r["amount"] for r in rows if r["type"] == "income")
    expense = sum(r["amount"] for r in rows if r["type"] == "expense")
    return {
        "initial_balance": round(initial, 2),
        "income": round(income, 2),
        "expense": round(expense, 2),
        "balance": round(initial + income - expense, 2),
    }


def account_state(db, account):
    """Balance/income/expense for a single account."""
    rows = db.execute(
        "SELECT type, amount FROM transactions WHERE account_id = ?", (account["id"],)
    ).fetchall()
    income = sum(r["amount"] for r in rows if r["type"] == "income")
    expense = sum(r["amount"] for r in rows if r["type"] == "expense")
    initial = account["initial_balance"]
    return {
        "id": account["id"],
        "user_id": account["user_id"],
        "name": account["name"],
        "type": account["type"],
        "initial_balance": round(initial, 2),
        "income": round(income, 2),
        "expense": round(expense, 2),
        "balance": round(initial + income - expense, 2),
    }


def list_accounts(db, user_id):
    rows = db.execute(
        "SELECT * FROM accounts WHERE user_id = ? ORDER BY id", (user_id,)
    ).fetchall()
    return [account_state(db, a) for a in rows]


def get_account(db, account_id):
    return db.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()


def default_account_id(db, user_id):
    row = db.execute(
        "SELECT id FROM accounts WHERE user_id = ? ORDER BY id LIMIT 1", (user_id,)
    ).fetchone()
    return row["id"] if row else None


def get_user(db, user_id):
    return db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def state_payload(db, user):
    return {
        "id": user["id"],
        "name": user["name"],
        **compute_state(db, user["id"]),
        "accounts": list_accounts(db, user["id"]),
    }


def record_transaction(db, user_id, tx_type, amount, category, merchant="",
                       note="", date=None, source="manual", transfer_group=None,
                       subcategory=None, account_id=None):
    date = date or datetime.utcnow().strftime("%Y-%m-%d")
    if account_id is None:
        account_id = default_account_id(db, user_id)
    cur = db.execute(
        """
        INSERT INTO transactions
            (user_id, account_id, type, amount, category, subcategory, merchant,
             note, date, source, transfer_group, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, account_id, tx_type, amount, category, subcategory, merchant,
         note, date, source, transfer_group, datetime.utcnow().isoformat()),
    )
    db.commit()
    return db.execute(
        "SELECT * FROM transactions WHERE id = ?", (cur.lastrowid,)
    ).fetchone()


def parse_amount(data):
    try:
        amount = float(data.get("amount"))
    except (TypeError, ValueError):
        return None, "amount must be a number"
    if amount <= 0:
        return None, "amount must be greater than 0"
    return amount, None


def list_categories(db, kind=None):
    if kind:
        rows = db.execute(
            "SELECT name FROM categories WHERE kind = ? ORDER BY name", (kind,)
        ).fetchall()
    else:
        rows = db.execute("SELECT name FROM categories ORDER BY name").fetchall()
    return [r["name"] for r in rows]


def merchant_key(merchant):
    return (merchant or "").strip().lower()


def learn_merchant(db, merchant, category):
    """Remember that this merchant maps to this category for next time."""
    key = merchant_key(merchant)
    if not key or not category:
        return
    db.execute(
        """
        INSERT INTO merchant_categories (merchant_key, category) VALUES (?, ?)
        ON CONFLICT(merchant_key) DO UPDATE SET category = excluded.category
        """,
        (key, category),
    )
    db.commit()


def match_rule(db, merchant):
    """Match the merchant name against DB keyword rules (longest first)."""
    import re
    text = (merchant or "").lower().strip()
    if not text:
        return None
    rules = db.execute(
        "SELECT keyword, category, subcategory FROM category_rules"
    ).fetchall()
    # Longest keyword wins (e.g. 'nis petrol' over 'petrol').
    for r in sorted(rules, key=lambda r: len(r["keyword"]), reverse=True):
        pattern = r"(?<![a-z0-9])" + re.escape(r["keyword"].lower()) + r"(?![a-z0-9])"
        if re.search(pattern, text):
            return r["category"], r["subcategory"]
    return None


def resolve_category(db, merchant, amount):
    """Pick (category, subcategory).

    Priority: learned merchant mapping -> editable DB rules -> optional LLM ->
    'Other'. All sources are database-driven except the optional LLM fallback.
    """
    key = merchant_key(merchant)
    if key:
        row = db.execute(
            "SELECT category FROM merchant_categories WHERE merchant_key = ?", (key,)
        ).fetchone()
        if row:
            return row["category"], None

    rule = match_rule(db, merchant)
    if rule:
        return rule

    llm = categorizer.llm_category(merchant, amount)
    if llm:
        return llm["category"], llm["subcategory"]

    return "Other", None


# --------------------------------------------------------------------------- #
# Views / config
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/config", methods=["GET"])
def config():
    db = get_db()
    return jsonify(
        {
            "currency": CURRENCY,
            "categories": list_categories(db, "expense"),
            "income_categories": list_categories(db, "income"),
            "subcategories": categorizer.SUBCATEGORIES,
        }
    )


# --------------------------------------------------------------------------- #
# Categories
# --------------------------------------------------------------------------- #
@app.route("/api/categories", methods=["GET"])
def get_categories():
    db = get_db()
    kind = request.args.get("kind")
    return jsonify(list_categories(db, kind))


@app.route("/api/categories", methods=["POST"])
def add_category():
    db = get_db()
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    kind = (data.get("kind") or "expense").strip()
    if kind not in ("expense", "income"):
        kind = "expense"
    if not name:
        return jsonify({"error": "name is required"}), 400
    try:
        db.execute("INSERT INTO categories (name, kind) VALUES (?, ?)", (name, kind))
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "category already exists"}), 409
    return jsonify({"name": name, "kind": kind}), 201


@app.route("/api/categories/<name>", methods=["DELETE"])
def delete_category(name):
    db = get_db()
    cur = db.execute("DELETE FROM categories WHERE name = ?", (name,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "not found"}), 404
    return jsonify({"status": "deleted"})


# --------------------------------------------------------------------------- #
# Category rules (editable auto-categorizer)
# --------------------------------------------------------------------------- #
@app.route("/api/rules", methods=["GET"])
def list_rules():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM category_rules ORDER BY keyword"
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@app.route("/api/rules", methods=["POST"])
def add_rule():
    db = get_db()
    data = request.get_json(silent=True) or {}
    keyword = (data.get("keyword") or "").strip().lower()
    category = (data.get("category") or "").strip()
    subcategory = (data.get("subcategory") or "").strip() or None
    if not keyword or not category:
        return jsonify({"error": "keyword and category are required"}), 400
    try:
        cur = db.execute(
            "INSERT INTO category_rules (keyword, category, subcategory) VALUES (?, ?, ?)",
            (keyword, category, subcategory),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "a rule for that keyword already exists"}), 409
    row = db.execute("SELECT * FROM category_rules WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(row_to_dict(row)), 201


@app.route("/api/rules/<int:rule_id>", methods=["DELETE"])
def delete_rule(rule_id):
    db = get_db()
    cur = db.execute("DELETE FROM category_rules WHERE id = ?", (rule_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "not found"}), 404
    return jsonify({"status": "deleted"})


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #
@app.route("/api/users", methods=["GET"])
def list_users():
    db = get_db()
    users = db.execute("SELECT * FROM users ORDER BY id").fetchall()
    return jsonify([state_payload(db, u) for u in users])


@app.route("/api/users", methods=["POST"])
def create_user():
    db = get_db()
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    try:
        initial = float(data.get("initial_balance") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "initial_balance must be a number"}), 400
    try:
        cur = db.execute(
            "INSERT INTO users (name, initial_balance) VALUES (?, ?)", (name, initial)
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "a user with that name already exists"}), 409
    # Every user starts with a Checking account holding the initial balance.
    db.execute(
        "INSERT INTO accounts (user_id, name, type, initial_balance) VALUES (?, 'Checking', 'checking', ?)",
        (cur.lastrowid, initial),
    )
    db.commit()
    return jsonify(state_payload(db, get_user(db, cur.lastrowid))), 201


@app.route("/api/users/<int:user_id>", methods=["PUT"])
def update_user(user_id):
    db = get_db()
    user = get_user(db, user_id)
    if user is None:
        return jsonify({"error": "user not found"}), 404
    data = request.get_json(silent=True) or {}

    name = user["name"]
    if "name" in data:
        name = (data.get("name") or "").strip() or name
    initial = user["initial_balance"]
    if "initial_balance" in data:
        try:
            initial = float(data.get("initial_balance"))
        except (TypeError, ValueError):
            return jsonify({"error": "initial_balance must be a number"}), 400

    try:
        db.execute(
            "UPDATE users SET name = ?, initial_balance = ? WHERE id = ?",
            (name, initial, user_id),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "a user with that name already exists"}), 409
    return jsonify(state_payload(db, get_user(db, user_id)))


@app.route("/api/users/<int:user_id>", methods=["DELETE"])
def delete_user(user_id):
    db = get_db()
    if get_user(db, user_id) is None:
        return jsonify({"error": "user not found"}), 404
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    return jsonify({"status": "deleted"})


@app.route("/api/users/<int:user_id>/state", methods=["GET"])
def user_state(user_id):
    db = get_db()
    user = get_user(db, user_id)
    if user is None:
        return jsonify({"error": "user not found"}), 404
    return jsonify(state_payload(db, user))


@app.route("/api/users/<int:user_id>/withdraw", methods=["POST"])
def withdraw(user_id):
    db = get_db()
    user = get_user(db, user_id)
    if user is None:
        return jsonify({"error": "user not found"}), 404
    data = request.get_json(silent=True) or {}
    amount, err = parse_amount(data)
    if err:
        return jsonify({"error": err}), 400
    category = (data.get("category") or "").strip() or "Cash"
    note = (data.get("note") or "").strip()
    date = (data.get("date") or "").strip() or None
    account_id = data.get("account_id") or default_account_id(db, user_id)
    row = record_transaction(db, user_id, "expense", amount, category, note=note,
                             date=date, account_id=account_id)
    return jsonify({"transaction": row_to_dict(row), "state": state_payload(db, user)}), 201


@app.route("/api/users/<int:user_id>/deposit", methods=["POST"])
def deposit(user_id):
    db = get_db()
    user = get_user(db, user_id)
    if user is None:
        return jsonify({"error": "user not found"}), 404
    data = request.get_json(silent=True) or {}
    amount, err = parse_amount(data)
    if err:
        return jsonify({"error": err}), 400
    category = (data.get("category") or "").strip() or "Deposit"
    note = (data.get("note") or "").strip()
    date = (data.get("date") or "").strip() or None
    account_id = data.get("account_id") or default_account_id(db, user_id)
    row = record_transaction(db, user_id, "income", amount, category, note=note,
                             date=date, account_id=account_id)
    return jsonify({"transaction": row_to_dict(row), "state": state_payload(db, user)}), 201


# --------------------------------------------------------------------------- #
# Accounts
# --------------------------------------------------------------------------- #
@app.route("/api/users/<int:user_id>/accounts", methods=["GET"])
def get_accounts(user_id):
    db = get_db()
    if get_user(db, user_id) is None:
        return jsonify({"error": "user not found"}), 404
    return jsonify(list_accounts(db, user_id))


@app.route("/api/users/<int:user_id>/accounts", methods=["POST"])
def add_account(user_id):
    db = get_db()
    if get_user(db, user_id) is None:
        return jsonify({"error": "user not found"}), 404
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    acc_type = (data.get("type") or "checking").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    try:
        initial = float(data.get("initial_balance") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "initial_balance must be a number"}), 400
    cur = db.execute(
        "INSERT INTO accounts (user_id, name, type, initial_balance) VALUES (?, ?, ?, ?)",
        (user_id, name, acc_type, initial),
    )
    db.commit()
    return jsonify(account_state(db, get_account(db, cur.lastrowid))), 201


@app.route("/api/accounts/<int:account_id>", methods=["PUT", "PATCH"])
def update_account(account_id):
    db = get_db()
    acc = get_account(db, account_id)
    if acc is None:
        return jsonify({"error": "not found"}), 404
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or acc["name"]).strip() or acc["name"]
    acc_type = (data.get("type") or acc["type"]).strip() or acc["type"]
    initial = acc["initial_balance"]
    if "initial_balance" in data:
        try:
            initial = float(data.get("initial_balance"))
        except (TypeError, ValueError):
            return jsonify({"error": "initial_balance must be a number"}), 400
    db.execute(
        "UPDATE accounts SET name = ?, type = ?, initial_balance = ? WHERE id = ?",
        (name, acc_type, initial, account_id),
    )
    db.commit()
    return jsonify(account_state(db, get_account(db, account_id)))


@app.route("/api/accounts/<int:account_id>", methods=["DELETE"])
def delete_account(account_id):
    db = get_db()
    acc = get_account(db, account_id)
    if acc is None:
        return jsonify({"error": "not found"}), 404
    remaining = db.execute(
        "SELECT COUNT(*) FROM accounts WHERE user_id = ?", (acc["user_id"],)
    ).fetchone()[0]
    if remaining <= 1:
        return jsonify({"error": "a user must keep at least one account"}), 400
    db.execute("DELETE FROM transactions WHERE account_id = ?", (account_id,))
    db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
    db.commit()
    return jsonify({"status": "deleted"})


# --------------------------------------------------------------------------- #
# Cards
# --------------------------------------------------------------------------- #
@app.route("/api/users/<int:user_id>/cards", methods=["GET"])
def list_cards(user_id):
    db = get_db()
    if get_user(db, user_id) is None:
        return jsonify({"error": "user not found"}), 404
    rows = db.execute("SELECT * FROM cards WHERE user_id = ? ORDER BY id", (user_id,)).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@app.route("/api/users/<int:user_id>/cards", methods=["POST"])
def add_card(user_id):
    db = get_db()
    if get_user(db, user_id) is None:
        return jsonify({"error": "user not found"}), 404
    data = request.get_json(silent=True) or {}
    label = (data.get("label") or "Card").strip()
    identifier = (data.get("identifier") or "").strip()
    account_id = data.get("account_id") or default_account_id(db, user_id)
    if not identifier:
        return jsonify({"error": "identifier is required"}), 400
    try:
        cur = db.execute(
            "INSERT INTO cards (user_id, account_id, label, identifier) VALUES (?, ?, ?, ?)",
            (user_id, account_id, label, identifier),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "that card identifier is already in use"}), 409
    row = db.execute("SELECT * FROM cards WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(row_to_dict(row)), 201


@app.route("/api/cards/<int:card_id>", methods=["PUT", "PATCH"])
def update_card(card_id):
    db = get_db()
    card = db.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    if card is None:
        return jsonify({"error": "not found"}), 404
    data = request.get_json(silent=True) or {}

    label = card["label"]
    if "label" in data:
        label = (data.get("label") or "").strip() or label
    identifier = card["identifier"]
    if "identifier" in data:
        identifier = (data.get("identifier") or "").strip() or identifier
    account_id = card["account_id"]
    if "account_id" in data:
        account_id = data.get("account_id") or account_id

    try:
        db.execute(
            "UPDATE cards SET label = ?, identifier = ?, account_id = ? WHERE id = ?",
            (label, identifier, account_id, card_id),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "that card identifier is already in use"}), 409
    row = db.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    return jsonify(row_to_dict(row))


@app.route("/api/cards/<int:card_id>", methods=["DELETE"])
def delete_card(card_id):
    db = get_db()
    cur = db.execute("DELETE FROM cards WHERE id = ?", (card_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "not found"}), 404
    return jsonify({"status": "deleted"})


# --------------------------------------------------------------------------- #
# Apple Wallet payment -> auto-categorized expense
# --------------------------------------------------------------------------- #
@app.route("/api/wallet/payment", methods=["GET", "POST"])
def wallet_payment():
    """Receive a card payment and auto-categorize it.

    Parameters can be sent either in the URL (query string) or as JSON, so it
    works from a simple link/shortcut, e.g.:

        /api/wallet/payment?card=1900&merchant=Maxi&amount=1240

    The `card` value is matched against a stored card identifier to resolve the
    owning user. If the card is unknown it is registered automatically: attach
    it to an existing user via `user` (id or name), or a new user is created.
    Optional `label` names the new card. The category is inferred from the
    merchant name (LLM if configured, otherwise the offline heuristic), unless
    a previously learned merchant mapping applies.
    """
    db = get_db()
    # Merge URL query params, form data and JSON body.
    data = dict(request.values)
    data.update(request.get_json(silent=True) or {})

    merchant = (str(data.get("merchant") or "")).strip()
    card_value = (str(data.get("card") or "")).strip()
    amount, err = parse_amount(data)
    if err:
        return jsonify({"error": err}), 400
    if not card_value:
        return jsonify({"error": "card is required"}), 400

    card = db.execute(
        "SELECT * FROM cards WHERE identifier = ?", (card_value,)
    ).fetchone()

    if card is None:
        # Auto-register the unknown card.
        user = _resolve_or_create_user(db, data.get("user"))
        label = (str(data.get("label") or "")).strip() or "Auto-added card"
        acct = default_account_id(db, user["id"])
        cur = db.execute(
            "INSERT INTO cards (user_id, account_id, label, identifier) VALUES (?, ?, ?, ?)",
            (user["id"], acct, label, card_value),
        )
        db.commit()
        card = db.execute("SELECT * FROM cards WHERE id = ?", (cur.lastrowid,)).fetchone()
        card_created = True
    else:
        card_created = False

    user = get_user(db, card["user_id"])
    category, subcategory = resolve_category(db, merchant, amount)
    date = (str(data.get("date") or "")).strip() or None

    row = record_transaction(
        db, user["id"], "expense", amount, category,
        merchant=merchant, date=date, source="wallet", subcategory=subcategory,
        account_id=card["account_id"],
    )
    return jsonify(
        {
            "transaction": row_to_dict(row),
            "category": category,
            "subcategory": subcategory,
            "user": user["name"],
            "card_created": card_created,
            "state": state_payload(db, user),
        }
    ), 201


def _resolve_or_create_user(db, user_ref):
    """Find a user by id or name, creating one if needed (for new cards)."""
    user_ref = (str(user_ref).strip() if user_ref is not None else "")
    if user_ref:
        if user_ref.isdigit():
            row = get_user(db, int(user_ref))
            if row:
                return row
        row = db.execute("SELECT * FROM users WHERE name = ?", (user_ref,)).fetchone()
        if row:
            return row
        name = user_ref
    else:
        # No hint given: reuse a shared bucket so we don't spawn many users.
        row = db.execute("SELECT * FROM users WHERE name = ?", ("Unassigned",)).fetchone()
        if row:
            return row
        name = "Unassigned"

    cur = db.execute("INSERT INTO users (name, initial_balance) VALUES (?, 0)", (name,))
    db.commit()
    # Give the new user a default account so transactions have somewhere to go.
    db.execute(
        "INSERT INTO accounts (user_id, name, type, initial_balance) VALUES (?, 'Checking', 'checking', 0)",
        (cur.lastrowid,),
    )
    db.commit()
    return get_user(db, cur.lastrowid)


# --------------------------------------------------------------------------- #
# Transfers between accounts (and users)
# --------------------------------------------------------------------------- #
@app.route("/api/transfer", methods=["POST"])
def transfer():
    db = get_db()
    data = request.get_json(silent=True) or {}
    amount, err = parse_amount(data)
    if err:
        return jsonify({"error": err}), 400

    from_acc_id = data.get("from_account_id")
    to_acc_id = data.get("to_account_id")

    # Fall back to user-level transfer (first account of each) if accounts
    # weren't specified.
    if from_acc_id is None and data.get("from_user_id") is not None:
        from_acc_id = default_account_id(db, data.get("from_user_id"))
    if to_acc_id is None and data.get("to_user_id") is not None:
        to_acc_id = default_account_id(db, data.get("to_user_id"))

    if from_acc_id == to_acc_id:
        return jsonify({"error": "cannot transfer to the same account"}), 400

    src = get_account(db, from_acc_id) if from_acc_id else None
    dst = get_account(db, to_acc_id) if to_acc_id else None
    if src is None or dst is None:
        return jsonify({"error": "valid from_account_id and to_account_id are required"}), 400

    sender = get_user(db, src["user_id"])
    receiver = get_user(db, dst["user_id"])
    note = (data.get("note") or "").strip()
    date = (data.get("date") or "").strip() or None
    group = f"tr-{datetime.utcnow().timestamp()}"

    def label(u, acc):
        return acc["name"] if u["id"] == sender["id"] == receiver["id"] else f"{u['name']} · {acc['name']}"

    record_transaction(db, src["user_id"], "expense", amount, "Transfer",
                        merchant=f"To {label(receiver, dst)}", note=note, date=date,
                        source="transfer", transfer_group=group, account_id=src["id"])
    record_transaction(db, dst["user_id"], "income", amount, "Transfer",
                        merchant=f"From {label(sender, src)}", note=note, date=date,
                        source="transfer", transfer_group=group, account_id=dst["id"])

    return jsonify(
        {
            "from": state_payload(db, get_user(db, sender["id"])),
            "to": state_payload(db, get_user(db, receiver["id"])),
        }
    ), 201


# --------------------------------------------------------------------------- #
# Transactions
# --------------------------------------------------------------------------- #
@app.route("/api/transactions", methods=["GET"])
def list_transactions():
    db = get_db()
    user_id = request.args.get("user_id", type=int)
    account_id = request.args.get("account_id", type=int)
    if account_id is not None:
        rows = db.execute(
            "SELECT * FROM transactions WHERE account_id = ? ORDER BY date DESC, id DESC",
            (account_id,),
        ).fetchall()
    elif user_id is not None:
        rows = db.execute(
            "SELECT * FROM transactions WHERE user_id = ? ORDER BY date DESC, id DESC",
            (user_id,),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM transactions ORDER BY date DESC, id DESC"
        ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@app.route("/api/transactions", methods=["POST"])
def add_transaction():
    db = get_db()
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id")
    if user_id is None or get_user(db, user_id) is None:
        return jsonify({"error": "valid user_id is required"}), 400
    tx_type = data.get("type")
    if tx_type not in ("income", "expense"):
        return jsonify({"error": "type must be 'income' or 'expense'"}), 400
    amount, err = parse_amount(data)
    if err:
        return jsonify({"error": err}), 400

    account_id = data.get("account_id") or default_account_id(db, user_id)
    merchant = (data.get("merchant") or "").strip()
    note = (data.get("note") or "").strip()
    date = (data.get("date") or "").strip() or None

    # Auto-categorize expenses when no category was supplied.
    category = (data.get("category") or "").strip()
    subcategory = (data.get("subcategory") or "").strip() or None
    if not category:
        if tx_type == "expense":
            category, auto_sub = resolve_category(db, merchant, amount)
            subcategory = subcategory or auto_sub
        else:
            category = "Other income"
    elif merchant:
        # Explicit category on an expense teaches the merchant mapping.
        learn_merchant(db, merchant, category)

    row = record_transaction(db, user_id, tx_type, amount, category,
                             merchant=merchant, note=note, date=date,
                             subcategory=subcategory, account_id=account_id)
    return jsonify(row_to_dict(row)), 201


@app.route("/api/transactions/<int:tx_id>", methods=["PUT", "PATCH"])
def update_transaction(tx_id):
    db = get_db()
    row = db.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    if row is None:
        return jsonify({"error": "not found"}), 404
    data = request.get_json(silent=True) or {}

    category = (data.get("category") or row["category"]).strip()
    subcategory = data.get("subcategory", row["subcategory"])
    subcategory = (subcategory or "").strip() or None
    merchant = data.get("merchant", row["merchant"])
    merchant = (merchant or "").strip()
    note = data.get("note", row["note"])
    note = (note or "").strip()
    date = (data.get("date") or row["date"]).strip()

    amount = row["amount"]
    if "amount" in data:
        amount, err = parse_amount(data)
        if err:
            return jsonify({"error": err}), 400

    db.execute(
        "UPDATE transactions SET category = ?, subcategory = ?, merchant = ?, note = ?, date = ?, amount = ? WHERE id = ?",
        (category, subcategory, merchant, note, date, amount, tx_id),
    )
    db.commit()

    # Remember the merchant -> category choice for next time.
    if row["type"] == "expense" and category not in NON_SPENDING:
        learn_merchant(db, merchant, category)

    updated = db.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    return jsonify(row_to_dict(updated))


@app.route("/api/transactions/<int:tx_id>", methods=["DELETE"])
def delete_transaction(tx_id):
    db = get_db()
    cur = db.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "not found"}), 404
    return jsonify({"status": "deleted"})


# --------------------------------------------------------------------------- #
# Spending analysis
# --------------------------------------------------------------------------- #
def _month_floor(d):
    return d.replace(day=1)


def _add_month(d):
    return d.replace(year=d.year + 1, month=1, day=1) if d.month == 12 \
        else d.replace(month=d.month + 1, day=1)


def _resolve_period(args):
    """Return (start, end, label) as YYYY-MM-DD strings.

    Priority: explicit start/end -> month=YYYY-MM (1st to 1st) -> current month.
    `end` is exclusive (the 1st of the following period).
    """
    start = (args.get("start") or "").strip()
    end = (args.get("end") or "").strip()
    if start and end:
        return start, end, f"{start} → {end}"

    month = (args.get("month") or "").strip()
    if month:
        try:
            ref = datetime.strptime(month + "-01", "%Y-%m-%d")
        except ValueError:
            ref = _month_floor(datetime.utcnow())
    else:
        ref = _month_floor(datetime.utcnow())

    nxt = _add_month(ref)
    label = ref.strftime("%b %Y") + f" (1 {ref.strftime('%b')} – 1 {nxt.strftime('%b')})"
    return ref.strftime("%Y-%m-%d"), nxt.strftime("%Y-%m-%d"), label


@app.route("/api/analytics", methods=["GET"])
def analytics():
    db = get_db()
    user_id = request.args.get("user_id", type=int)
    account_id = request.args.get("account_id", type=int)
    start, end, label = _resolve_period(request.args)

    if account_id is not None:
        all_rows = db.execute(
            "SELECT * FROM transactions WHERE account_id = ?", (account_id,)
        ).fetchall()
    elif user_id is not None:
        all_rows = db.execute(
            "SELECT * FROM transactions WHERE user_id = ?", (user_id,)
        ).fetchall()
    else:
        all_rows = db.execute("SELECT * FROM transactions").fetchall()

    # Rows within the active period [start, end).
    rows = [r for r in all_rows if start <= (r["date"] or "") < end]

    by_category = defaultdict(float)
    by_subcategory = defaultdict(float)
    by_day = defaultdict(float)
    by_merchant = defaultdict(float)
    total_income = 0.0
    total_expense = 0.0
    expense_count = 0

    for r in rows:
        if r["type"] == "income":
            total_income += r["amount"]
            continue
        total_expense += r["amount"]
        if r["category"] in NON_SPENDING:
            continue
        expense_count += 1
        by_category[r["category"]] += r["amount"]
        sub = r["subcategory"] or "Other"
        by_subcategory[f"{r['category']} · {sub}"] += r["amount"]
        by_day[r["date"]] += r["amount"]
        if r["merchant"]:
            by_merchant[r["merchant"]] += r["amount"]

    spending = sum(by_category.values())

    categories = [
        {
            "category": c,
            "amount": round(a, 2),
            "percent": round(100 * a / spending, 1) if spending else 0,
        }
        for c, a in sorted(by_category.items(), key=lambda kv: kv[1], reverse=True)
    ]
    subcategories = [
        {"name": s, "amount": round(a, 2)}
        for s, a in sorted(by_subcategory.items(), key=lambda kv: kv[1], reverse=True)
    ]
    # Daily series across the whole period (zero-filled).
    days = []
    cur = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    while cur < end_dt:
        key = cur.strftime("%Y-%m-%d")
        days.append({"date": key, "expense": round(by_day.get(key, 0.0), 2)})
        cur += timedelta(days=1)
    top_merchants = [
        {"merchant": m, "amount": round(a, 2)}
        for m, a in sorted(by_merchant.items(), key=lambda kv: kv[1], reverse=True)[:5]
    ]

    # 6-month trend (1st-to-1st months), independent of the active period.
    trend = defaultdict(lambda: {"income": 0.0, "expense": 0.0})
    for r in all_rows:
        m = (r["date"] or "")[:7]
        if not m:
            continue
        if r["type"] == "income":
            trend[m]["income"] += r["amount"]
        elif r["category"] not in NON_SPENDING:
            trend[m]["expense"] += r["amount"]
    months = [
        {"month": m, "income": round(v["income"], 2), "expense": round(v["expense"], 2)}
        for m, v in sorted(trend.items())
    ][-6:]

    avg_tx = round(spending / expense_count, 2) if expense_count else 0

    return jsonify(
        {
            "period": {"start": start, "end": end, "label": label},
            "total_income": round(total_income, 2),
            "total_expense": round(total_expense, 2),
            "net": round(total_income - total_expense, 2),
            "spending": round(spending, 2),
            "avg_transaction": avg_tx,
            "expense_count": expense_count,
            "top_category": categories[0]["category"] if categories else None,
            "by_category": categories,
            "by_subcategory": subcategories,
            "by_day": days,
            "by_month": months,
            "top_merchants": top_merchants,
        }
    )


@app.route("/api/summary", methods=["GET"])
def summary():
    db = get_db()
    user_id = request.args.get("user_id", type=int)
    if user_id is not None:
        rows = db.execute(
            "SELECT type, amount, category FROM transactions WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    else:
        rows = db.execute("SELECT type, amount, category FROM transactions").fetchall()

    income = sum(r["amount"] for r in rows if r["type"] == "income")
    expense = sum(r["amount"] for r in rows if r["type"] == "expense")
    by_category = defaultdict(float)
    for r in rows:
        if r["type"] == "expense" and r["category"] not in NON_SPENDING:
            by_category[r["category"]] += r["amount"]
    return jsonify(
        {
            "income": round(income, 2),
            "expense": round(expense, 2),
            "balance": round(income - expense, 2),
            "by_category": by_category,
        }
    )


init_db()


if __name__ == "__main__":
    debug = os.environ.get("BUDGET_DEBUG", "1") == "1"
    app.run(host="0.0.0.0", port=5000, debug=debug)
