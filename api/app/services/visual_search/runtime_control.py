"""Fail-closed runtime switch and durable daily budget for paid model calls."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from .images import VisualError

SHANGHAI = timezone(timedelta(hours=8))
SCHEMA_VERSION = 1


class RuntimeSwitch:
    """A private local marker controls AI calls without restarting FastAPI."""

    def __init__(self, visual_root: Path):
        self.root = Path(visual_root).resolve()
        self.runtime_dir = self.root / "runtime"
        self.enabled_file = self.runtime_dir / "ai-search.enabled"

    def is_enabled(self) -> bool:
        try:
            if self.runtime_dir.is_symlink() or self.enabled_file.is_symlink():
                return False
            info = self.enabled_file.stat()
            if not self.enabled_file.is_file() or info.st_nlink != 1:
                return False
            return self.enabled_file.read_text(encoding="ascii") == "enabled\n"
        except (OSError, UnicodeError):
            return False

    def require_enabled(self) -> None:
        if not self.is_enabled():
            raise VisualError("AI找货当前已关闭，请联系管理员", 503)

    def set_enabled(self, enabled: bool) -> None:
        """Change only the marker; callers must be a trusted OS operator."""
        self.root.mkdir(parents=True, exist_ok=True)
        if self.runtime_dir.is_symlink():
            raise VisualError("AI开关目录无效，拒绝修改", 503)
        self.runtime_dir.mkdir(mode=0o700, exist_ok=True)
        if self.enabled_file.is_symlink():
            raise VisualError("AI开关文件无效，拒绝修改", 503)
        if not enabled:
            try:
                self.enabled_file.unlink()
            except FileNotFoundError:
                pass
            except OSError as error:
                raise VisualError("无法关闭AI找货，请检查本机文件权限", 503) from error
            return

        temporary = self.runtime_dir / f".ai-search-enabled-{uuid4().hex}.tmp"
        try:
            with temporary.open("x", encoding="ascii", newline="\n") as marker:
                marker.write("enabled\n")
                marker.flush()
                os.fsync(marker.fileno())
            if os.name != "nt":
                temporary.chmod(0o600)
            os.replace(temporary, self.enabled_file)
        except OSError as error:
            raise VisualError("无法启用AI找货，请检查本机文件权限", 503) from error
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


class DailyBudgetLedger:
    """Persisted reservations count every attempt, including unknown failures.

    The configured per-call reserve is charged conservatively even when the
    reported cost is lower. If known actual usage exceeds that reserve, the
    excess is added to the day's budget immediately and future calls fail
    closed once the limit is reached.
    """

    def __init__(self, path: Path, config):
        self.path = Path(path)
        self.config = config

    def _policy(self) -> tuple[int, Decimal, Decimal]:
        calls = getattr(self.config, "daily_max_calls", None)
        budget = getattr(self.config, "daily_budget_cny", None)
        reserve = getattr(self.config, "call_reserve_cny", None)
        if calls is None or budget is None or reserve is None:
            raise VisualError("AI每日调用预算尚未配置，已暂停模型请求", 503)
        try:
            budget = Decimal(str(budget))
            reserve = Decimal(str(reserve))
        except (InvalidOperation, ValueError) as error:
            raise VisualError("AI每日调用预算配置无效，已暂停模型请求", 503) from error
        if (type(calls) is not int or calls <= 0 or not budget.is_finite() or
                not reserve.is_finite() or budget <= 0 or reserve <= 0 or reserve > budget):
            raise VisualError("AI每日调用预算配置无效，已暂停模型请求", 503)
        return calls, budget, reserve

    @staticmethod
    def _day() -> str:
        return datetime.now(SHANGHAI).date().isoformat()

    def _connect(self):
        db = None
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self.path.is_symlink():
                raise OSError("budget ledger path is a symbolic link")
            fresh = not self.path.exists()
            db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
            db.execute("PRAGMA busy_timeout=5000")
            if self.path.stat().st_nlink != 1:
                raise OSError("budget ledger has an unexpected hard link")
            if fresh:
                if os.name != "nt":
                    self.path.chmod(0o600)
                db.execute("BEGIN IMMEDIATE")
                db.execute("""CREATE TABLE daily_budget (
                    day TEXT PRIMARY KEY,
                    call_count INTEGER NOT NULL,
                    budgeted_cny TEXT NOT NULL,
                    actual_cny TEXT NOT NULL,
                    unknown_calls INTEGER NOT NULL
                )""")
                db.execute("""CREATE TABLE call_reservations (
                    id TEXT PRIMARY KEY,
                    day TEXT NOT NULL,
                    reserve_cny TEXT NOT NULL,
                    actual_cny TEXT,
                    state TEXT NOT NULL
                )""")
                db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                db.commit()
            else:
                version = db.execute("PRAGMA user_version").fetchone()[0]
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if version != SCHEMA_VERSION or not {"daily_budget", "call_reservations"}.issubset(tables):
                    raise OSError("budget ledger schema is invalid")
            return db
        except (OSError, sqlite3.Error) as error:
            if db is not None:
                db.close()
            raise VisualError("AI费用记录不可用，已暂停模型请求", 503) from error

    @staticmethod
    def _daily_values(row) -> tuple[int, Decimal]:
        if row is None:
            return 0, Decimal(0)
        calls, amount = row[0], Decimal(row[1])
        if type(calls) is not int or calls < 0 or not amount.is_finite() or amount < 0:
            raise ValueError("daily budget ledger values are invalid")
        return calls, amount

    def preflight(self) -> None:
        """Fail before multipart parsing when budget is absent, broken, or spent."""
        calls_limit, budget_limit, reserve = self._policy()
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT call_count,budgeted_cny FROM daily_budget WHERE day=?", (self._day(),)).fetchone()
            call_count, budgeted = self._daily_values(row)
            if call_count >= calls_limit or budgeted + reserve > budget_limit:
                raise VisualError("AI今日调用额度已用完，请明天再试或联系管理员", 503)
            db.rollback()
        except VisualError:
            db.rollback()
            raise
        except (sqlite3.Error, InvalidOperation, ValueError, TypeError) as error:
            db.rollback()
            raise VisualError("AI费用记录不可用，已暂停模型请求", 503) from error
        finally:
            db.close()

    def reserve(self) -> tuple[str, str, Decimal]:
        calls_limit, budget_limit, reserve = self._policy()
        day = self._day()
        reservation_id = uuid4().hex
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT call_count,budgeted_cny FROM daily_budget WHERE day=?", (day,)).fetchone()
            call_count, budgeted = self._daily_values(row)
            if call_count >= calls_limit or budgeted + reserve > budget_limit:
                raise VisualError("AI今日调用额度已用完，请明天再试或联系管理员", 503)
            next_count, next_budgeted = call_count + 1, budgeted + reserve
            db.execute("""INSERT INTO daily_budget(day,call_count,budgeted_cny,actual_cny,unknown_calls)
                VALUES(?,?,?,?,0) ON CONFLICT(day) DO UPDATE SET
                call_count=excluded.call_count,budgeted_cny=excluded.budgeted_cny""",
                (day, next_count, str(next_budgeted), "0"))
            db.execute("INSERT INTO call_reservations(id,day,reserve_cny,actual_cny,state) VALUES(?,?,?,?,?)",
                       (reservation_id, day, str(reserve), None, "reserved"))
            db.commit()
            return reservation_id, day, reserve
        except VisualError:
            db.rollback()
            raise
        except (sqlite3.Error, InvalidOperation, ValueError, TypeError) as error:
            db.rollback()
            raise VisualError("AI费用记录不可用，已暂停模型请求", 503) from error
        finally:
            db.close()

    def settle(self, reservation: tuple[str, str, Decimal], usage: dict, *, known: bool) -> None:
        reservation_id, _day, reserve = reservation
        actual = self._usage_cost(usage) if known else None
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT day,reserve_cny,state FROM call_reservations WHERE id=?", (reservation_id,)).fetchone()
            if row is None or row[2] != "reserved":
                raise sqlite3.DatabaseError("reservation state is invalid")
            day, stored_reserve = row[0], Decimal(row[1])
            if stored_reserve != reserve:
                raise sqlite3.DatabaseError("reservation amount is invalid")
            if actual is None:
                cursor = db.execute("UPDATE daily_budget SET unknown_calls=unknown_calls+1 WHERE day=?", (day,))
                if cursor.rowcount != 1:
                    raise sqlite3.DatabaseError("daily budget row is missing")
                state, actual_text = "unknown", None
            else:
                extra = max(Decimal(0), actual - reserve)
                totals = db.execute("SELECT budgeted_cny,actual_cny FROM daily_budget WHERE day=?", (day,)).fetchone()
                if totals is None:
                    raise sqlite3.DatabaseError("daily budget row is missing")
                budgeted, actual_total = Decimal(totals[0]), Decimal(totals[1])
                if not budgeted.is_finite() or not actual_total.is_finite() or budgeted < 0 or actual_total < 0:
                    raise ValueError("daily budget totals are invalid")
                db.execute("UPDATE daily_budget SET budgeted_cny=?,actual_cny=? WHERE day=?",
                           (str(budgeted + extra), str(actual_total + actual), day))
                state, actual_text = "settled", str(actual)
            db.execute("UPDATE call_reservations SET actual_cny=?,state=? WHERE id=?", (actual_text, state, reservation_id))
            db.commit()
        except (sqlite3.Error, InvalidOperation, ValueError, TypeError) as error:
            db.rollback()
            raise VisualError("AI费用记录无法结算，后续模型请求已暂停", 503) from error
        finally:
            db.close()

    def snapshot(self, day: str | None = None) -> dict:
        """Diagnostic helper used by offline tests and local administration."""
        day = day or self._day()
        db = self._connect()
        try:
            row = db.execute("SELECT call_count,budgeted_cny,actual_cny,unknown_calls FROM daily_budget WHERE day=?", (day,)).fetchone()
            if row is None:
                return {"day": day, "call_count": 0, "budgeted_cny": "0", "actual_cny": "0", "unknown_calls": 0}
            return {"day": day, "call_count": row[0], "budgeted_cny": row[1], "actual_cny": row[2], "unknown_calls": row[3]}
        finally:
            db.close()

    def report(self) -> dict:
        try:
            calls_limit, budget_limit, reserve = self._policy()
        except VisualError:
            return {"configured": False, "state": "unconfigured"}
        try:
            current = self.snapshot()
            spent = Decimal(current["budgeted_cny"])
            return {
                "configured": True,
                "state": "ready" if current["call_count"] < calls_limit and spent + reserve <= budget_limit else "exhausted",
                "daily_max_calls": calls_limit,
                "daily_budget_cny": str(budget_limit),
                "per_call_reserve_cny": str(reserve),
                "call_count": current["call_count"],
                "conservative_budgeted_cny": str(spent),
                "actual_known_cny": current["actual_cny"],
                "unknown_usage_calls": current["unknown_calls"],
            }
        except (VisualError, InvalidOperation, ValueError, TypeError):
            return {"configured": True, "state": "unavailable"}

    def _usage_cost(self, usage: dict) -> Decimal | None:
        keys = ("input_tokens", "image_tokens", "total_tokens")
        if (not isinstance(usage, dict) or any(type(usage.get(key)) is not int or usage[key] < 0 for key in keys) or
                usage["total_tokens"] != usage["input_tokens"] + usage["image_tokens"]):
            return None
        try:
            image_price = Decimal(str(self.config.image_price))
            text_price = Decimal(str(self.config.text_price))
            cost = (Decimal(usage["input_tokens"]) * text_price +
                    Decimal(usage["image_tokens"]) * image_price) / Decimal(1000)
            return cost if cost.is_finite() and cost >= 0 else None
        except (InvalidOperation, ValueError):
            return None
