"""SQLite 存储层。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .models import (
    Account,
    AccountStatus,
    ChatRole,
    ChatTurn,
    Notification,
    NotificationStatus,
    SessionRecord,
    WeChatMessage,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path) -> None:
    """启动时自动建表。"""

    conn = _connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id              TEXT PRIMARY KEY,
                bot_token       TEXT NOT NULL,
                ilink_bot_id    TEXT NOT NULL,
                baseurl          TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'pending',
                get_updates_buf TEXT NOT NULL DEFAULT '',
                ilink_user_id   TEXT NOT NULL DEFAULT '',
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id          TEXT PRIMARY KEY,
                account_id  TEXT NOT NULL,
                from_user_id TEXT NOT NULL,
                msg_type    TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS sessions (
                account_id       TEXT NOT NULL,
                wechat_user_id   TEXT NOT NULL,
                history_json     TEXT NOT NULL DEFAULT '[]',
                summary          TEXT NOT NULL DEFAULT '',
                runtime_config_json TEXT NOT NULL DEFAULT '{}',
                updated_at       TEXT NOT NULL,
                PRIMARY KEY (account_id, wechat_user_id),
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id            TEXT PRIMARY KEY,
                account_id    TEXT NOT NULL,
                wechat_user_id TEXT NOT NULL,
                text          TEXT NOT NULL,
                status        TEXT NOT NULL DEFAULT 'pending',
                attempts      INTEGER NOT NULL DEFAULT 0,
                last_error    TEXT NOT NULL DEFAULT '',
                created_at    TEXT NOT NULL,
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def row_to_account(row: sqlite3.Row) -> Account:
    return Account(
        id=row["id"],
        bot_token=row["bot_token"],
        ilink_bot_id=row["ilink_bot_id"],
        baseurl=row["baseurl"],
        status=AccountStatus(row["status"]),
        get_updates_buf=row["get_updates_buf"],
        ilink_user_id=row["ilink_user_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def save_account(db_path: Path, account: Account) -> None:
    """插入或更新账号。"""

    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO accounts (id, bot_token, ilink_bot_id, baseurl, status, get_updates_buf, ilink_user_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                bot_token=excluded.bot_token,
                ilink_bot_id=excluded.ilink_bot_id,
                baseurl=excluded.baseurl,
                status=excluded.status,
                get_updates_buf=excluded.get_updates_buf,
                ilink_user_id=excluded.ilink_user_id,
                updated_at=excluded.updated_at
            """,
            (account.id, account.bot_token, account.ilink_bot_id, account.baseurl,
             account.status.value, account.get_updates_buf, account.ilink_user_id,
             account.created_at.isoformat(), account.updated_at.isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def get_account(db_path: Path, account_id: str) -> Account | None:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return row_to_account(row) if row else None
    finally:
        conn.close()


def list_accounts(db_path: Path, status: AccountStatus | None = None) -> list[Account]:
    conn = _connect(db_path)
    try:
        if status:
            rows = conn.execute("SELECT * FROM accounts WHERE status = ?", (status.value,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM accounts").fetchall()
        return [row_to_account(r) for r in rows]
    finally:
        conn.close()


def delete_account(db_path: Path, account_id: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        conn.commit()
    finally:
        conn.close()


def update_get_updates_buf(db_path: Path, account_id: str, buf: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE accounts SET get_updates_buf = ?, updated_at = ? WHERE id = ?",
            (buf, _now(), account_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_account_status(db_path: Path, account_id: str, status: AccountStatus) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE accounts SET status = ?, updated_at = ? WHERE id = ?",
            (status.value, _now(), account_id),
        )
        conn.commit()
    finally:
        conn.close()


def is_message_processed(db_path: Path, msg_id: str) -> bool:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT 1 FROM messages WHERE id = ?", (msg_id,)).fetchone()
        return row is not None
    finally:
        conn.close()


def mark_message_processed(db_path: Path, account_id: str, msg: WeChatMessage) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO messages (id, account_id, from_user_id, msg_type, created_at) VALUES (?, ?, ?, ?, ?)",
            (msg.msg_id, account_id, msg.from_user_id, msg.message_type.value, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def get_session(db_path: Path, account_id: str, wechat_user_id: str) -> SessionRecord:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM sessions WHERE account_id = ? AND wechat_user_id = ?",
            (account_id, wechat_user_id),
        ).fetchone()
        if row:
            history = [ChatTurn(role=ChatRole(t["role"]), content=t["content"]) for t in json.loads(row["history_json"])]
            return SessionRecord(
                account_id=account_id,
                wechat_user_id=wechat_user_id,
                history=history,
                summary=row["summary"],
                runtime_config=json.loads(row["runtime_config_json"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
        return SessionRecord(account_id=account_id, wechat_user_id=wechat_user_id)
    finally:
        conn.close()


def save_session(db_path: Path, session: SessionRecord) -> None:
    conn = _connect(db_path)
    try:
        history_json = json.dumps([{"role": t.role.value, "content": t.content} for t in session.history])
        conn.execute(
            """
            INSERT INTO sessions (account_id, wechat_user_id, history_json, summary, runtime_config_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, wechat_user_id) DO UPDATE SET
                history_json=excluded.history_json,
                summary=excluded.summary,
                runtime_config_json=excluded.runtime_config_json,
                updated_at=excluded.updated_at
            """,
            (session.account_id, session.wechat_user_id, history_json, session.summary,
             json.dumps(session.runtime_config), _now()),
        )
        conn.commit()
    finally:
        conn.close()


def clear_session(db_path: Path, account_id: str, wechat_user_id: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE sessions SET history_json = '[]', summary = '', updated_at = ? WHERE account_id = ? AND wechat_user_id = ?",
            (_now(), account_id, wechat_user_id),
        )
        conn.commit()
    finally:
        conn.close()


def save_notification(db_path: Path, notification: Notification) -> None:
    if not notification.id:
        notification.id = str(uuid4())
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO notifications (id, account_id, wechat_user_id, text, status, attempts, last_error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,
                attempts=excluded.attempts,
                last_error=excluded.last_error
            """,
            (notification.id, notification.account_id, notification.wechat_user_id,
             notification.text, notification.status.value, notification.attempts,
             notification.last_error, notification.created_at.isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def purge_expired_data(db_path: Path, retention_days: int) -> dict[str, int]:
    """删除超过留存期的会话、消息与通知记录（PIPL 数据最小化）。

    会话按 updated_at 判断（长期不活跃即删除整行，含历史与摘要）；
    消息去重表与通知记录按 created_at 判断。返回各表删除行数。
    """

    conn = _connect(db_path)
    try:
        threshold = datetime.now(timezone.utc).timestamp() - retention_days * 86400
        cutoff = datetime.fromtimestamp(threshold, timezone.utc).isoformat()
        deleted: dict[str, int] = {}
        deleted["sessions"] = conn.execute(
            "DELETE FROM sessions WHERE updated_at < ?", (cutoff,)
        ).rowcount
        deleted["messages"] = conn.execute(
            "DELETE FROM messages WHERE created_at < ?", (cutoff,)
        ).rowcount
        deleted["notifications"] = conn.execute(
            "DELETE FROM notifications WHERE created_at < ?", (cutoff,)
        ).rowcount
        conn.commit()
        return deleted
    finally:
        conn.close()


def list_notifications(db_path: Path, account_id: str | None = None) -> list[Notification]:
    conn = _connect(db_path)
    try:
        if account_id:
            rows = conn.execute("SELECT * FROM notifications WHERE account_id = ?", (account_id,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM notifications").fetchall()
        return [
            Notification(
                id=r["id"],
                account_id=r["account_id"],
                wechat_user_id=r["wechat_user_id"],
                text=r["text"],
                status=NotificationStatus(r["status"]),
                attempts=r["attempts"],
                last_error=r["last_error"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]
    finally:
        conn.close()
