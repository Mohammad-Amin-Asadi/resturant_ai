# storage.py
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

class WalletMeetingDB:
    """
    Minimal SQLite access layer for wallets and meetings.
    Schema is created by init_db.py (separate script).
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._lock = threading.Lock()
        # check_same_thread=False because we'll use asyncio.to_thread
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")

    @contextmanager
    def _cursor(self):
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cur.close()

    # ---------- Wallets ----------
    def get_wallet_balance(self, customer_id=None, phone=None):
        """
        Returns a dict:
        {
          "found": bool,
          "customer_id": "...",
          "phone_number": "...",
          "balance": 12345,            # integer minor units (e.g., IRR)
          "currency": "IRR",
          "error": "...optional..."
        }
        """
        if not customer_id and not phone:
            return {"found": False, "balance": 0, "currency": "IRR",
                    "error": "customer_id or phone_number is required"}

        try:
            with self._cursor() as c:
                if customer_id:
                    c.execute("SELECT customer_id, phone, balance_cents FROM wallets WHERE customer_id = ?",
                              (customer_id,))
                else:
                    c.execute("SELECT customer_id, phone, balance_cents FROM wallets WHERE phone = ?",
                              (phone,))
                row = c.fetchone()
            if not row:
                return {"found": False, "balance": 0, "currency": "IRR"}
            return {
                "found": True,
                "customer_id": row["customer_id"],
                "phone_number": row["phone"],
                "balance": int(row["balance_cents"]),
                "currency": "IRR"
            }
        except sqlite3.OperationalError as e:
            return {"found": False, "balance": 0, "currency": "IRR",
                    "error": f"Schema missing or locked: {e}"}

    # ---------- Meetings ----------
    @staticmethod
    def _validate_date(date_str: str):
        datetime.strptime(date_str, "%Y-%m-%d")  # raises if invalid

    @staticmethod
    def _validate_time(time_str: str):
        datetime.strptime(time_str, "%H:%M")  # raises if invalid

    def schedule_meeting(self, date: str, time: str,
                         customer_id: str = None,
                         duration_minutes: int = 30,
                         subject: str = None):
        """
        Returns a dict:
        {
          "scheduled": bool,
          "meeting_id": 123 (if scheduled),
          "conflict": true/false,
          "date": "YYYY-MM-DD",
          "time": "HH:MM",
          "duration_minutes": 30,
          "subject": "...",
          "error": "...optional..."
        }
        """
        if not date or not time:
            return {"scheduled": False, "conflict": False,
                    "error": "date and time are required"}

        try:
            self._validate_date(date)
            self._validate_time(time)
        except Exception:
            return {"scheduled": False, "conflict": False,
                    "date": date, "time": time,
                    "error": "Invalid date/time format. Use YYYY-MM-DD and HH:MM (24h)."}

        try:
            with self._cursor() as c:
                # conflict = any meeting at the exact same date+time
                c.execute("SELECT id FROM meetings WHERE date = ? AND time = ?", (date, time))
                exists = c.fetchone() is not None
                if exists:
                    return {"scheduled": False, "conflict": True,
                            "date": date, "time": time, "duration_minutes": duration_minutes,
                            "subject": subject}

                c.execute("""
                    INSERT INTO meetings (date, time, duration_minutes, subject, customer_id)
                    VALUES (?, ?, ?, ?, ?)
                """, (date, time, int(duration_minutes), subject, customer_id))
                meeting_id = c.lastrowid

            return {"scheduled": True, "conflict": False,
                    "meeting_id": meeting_id,
                    "date": date, "time": time,
                    "duration_minutes": int(duration_minutes),
                    "subject": subject}
        except sqlite3.OperationalError as e:
            return {"scheduled": False, "conflict": False,
                    "date": date, "time": time,
                    "error": f"Schema missing or locked: {e}"}

