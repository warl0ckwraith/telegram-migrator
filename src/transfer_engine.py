"""Message transfer engine (forward/copy with resume state)."""

import asyncio
import hashlib
import random
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from telethon.errors import FloodWaitError, RPCError


FATAL_SUBSTRINGS = (
    "CHAT_WRITE_FORBIDDEN",
    "CHANNEL_PRIVATE",
    "USER_BANNED_IN_CHANNEL",
    "CHAT_ADMIN_REQUIRED",
    "PEER_ID_INVALID",
    "ENTITY_NOT_FOUND",
)

RETRYABLE_SUBSTRINGS = (
    "TIMEOUT",
    "INTERNAL",
    "SERVER",
    "CONNECTION",
)


def is_fatal_rpc(err: RPCError) -> bool:
    text = str(err).upper()
    return any(token in text for token in FATAL_SUBSTRINGS)


def is_retryable_rpc(err: RPCError) -> bool:
    text = str(err).upper()
    return any(token in text for token in RETRYABLE_SUBSTRINGS)


def db_connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def db_init(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            job_key TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            dest TEXT NOT NULL,
            mode TEXT NOT NULL,
            last_id INTEGER NOT NULL DEFAULT 0,
            forwarded_count INTEGER NOT NULL DEFAULT 0,
            skipped_count INTEGER NOT NULL DEFAULT 0,
            flood_seconds INTEGER NOT NULL DEFAULT 0,
            started_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS skips (
            job_key TEXT NOT NULL,
            msg_id INTEGER NOT NULL,
            error_type TEXT NOT NULL,
            error_text TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (job_key, msg_id),
            FOREIGN KEY (job_key) REFERENCES jobs(job_key) ON DELETE CASCADE
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS batches (
            job_key TEXT NOT NULL,
            batch_end_id INTEGER NOT NULL,
            batch_size INTEGER NOT NULL,
            ok_count INTEGER NOT NULL,
            skip_count INTEGER NOT NULL,
            flood_added INTEGER NOT NULL,
            elapsed REAL NOT NULL,
            created_at REAL NOT NULL
        );
        """
    )


def db_load_job(conn: sqlite3.Connection, job_key: str) -> Optional[Tuple]:
    cur = conn.execute(
        "SELECT last_id, forwarded_count, skipped_count, flood_seconds, started_at "
        "FROM jobs WHERE job_key = ?",
        (job_key,),
    )
    return cur.fetchone()


def db_upsert_job(
    conn: sqlite3.Connection,
    job_key: str,
    source: str,
    dest: str,
    mode: str,
    last_id: int,
    forwarded_count: int,
    skipped_count: int,
    flood_seconds: int,
    started_at: float,
) -> None:
    now = time.time()
    conn.execute(
        """
        INSERT INTO jobs(
            job_key, source, dest, mode, last_id,
            forwarded_count, skipped_count, flood_seconds, started_at, updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(job_key) DO UPDATE SET
          last_id=excluded.last_id,
          forwarded_count=excluded.forwarded_count,
          skipped_count=excluded.skipped_count,
          flood_seconds=excluded.flood_seconds,
          updated_at=excluded.updated_at
        """,
        (
            job_key,
            source,
            dest,
            mode,
            int(last_id),
            int(forwarded_count),
            int(skipped_count),
            int(flood_seconds),
            float(started_at),
            float(now),
        ),
    )


def db_insert_skip(conn: sqlite3.Connection, job_key: str, msg_id: int, err: Exception) -> bool:
    try:
        conn.execute(
            "INSERT INTO skips(job_key, msg_id, error_type, error_text, created_at) VALUES(?, ?, ?, ?, ?)",
            (job_key, int(msg_id), err.__class__.__name__, str(err), time.time()),
        )
        return True
    except sqlite3.IntegrityError:
        return False


def db_has_skip(conn: sqlite3.Connection, job_key: str, msg_id: int) -> bool:
    cur = conn.execute("SELECT 1 FROM skips WHERE job_key=? AND msg_id=? LIMIT 1", (job_key, int(msg_id)))
    return cur.fetchone() is not None


def db_insert_batch(
    conn: sqlite3.Connection,
    job_key: str,
    batch_end_id: int,
    batch_size: int,
    ok_count: int,
    skip_count: int,
    flood_added: int,
    elapsed: float,
) -> None:
    conn.execute(
        "INSERT INTO batches(job_key, batch_end_id, batch_size, ok_count, skip_count, flood_added, elapsed, created_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
        (
            job_key,
            int(batch_end_id),
            int(batch_size),
            int(ok_count),
            int(skip_count),
            int(flood_added),
            float(elapsed),
            time.time(),
        ),
    )


@dataclass
class RateEstimator:
    """EMA estimator for processed messages per second."""

    ema_rate: float = 0.0
    alpha: float = 0.12

    def update(self, processed: int, elapsed: float) -> float:
        if elapsed <= 0:
            return self.ema_rate
        instant = processed / elapsed
        if self.ema_rate <= 0:
            self.ema_rate = instant
        else:
            self.ema_rate = self.alpha * instant + (1 - self.alpha) * self.ema_rate
        return self.ema_rate


@dataclass
class AdaptiveThrottle:
    """Adaptive batch + delay tuning from flood behavior."""

    batch: int
    delay: float
    jitter: float
    batch_min: int
    batch_max: int
    delay_min: float
    delay_max: float
    stable_batches: int = 0

    def on_batch(self, flood_added: int) -> None:
        if flood_added > 0:
            self.stable_batches = 0
            self.batch = max(self.batch_min, int(self.batch * 0.7))
            self.delay = min(self.delay_max, self.delay * 1.4 + 0.2)
            self.jitter = min(self.delay_max, self.jitter * 1.2 + 0.1)
            return

        self.stable_batches += 1
        if self.stable_batches >= 8:
            self.stable_batches = 0
            self.batch = min(self.batch_max, int(self.batch * 1.15) + 1)
            self.delay = max(self.delay_min, self.delay * 0.92)
            self.jitter = max(0.0, self.jitter * 0.95)

    async def sleep(self) -> None:
        await asyncio.sleep(max(0.0, self.delay) + random.random() * max(0.0, self.jitter))


class TransferEngine:
    """Forward/copy messages from source to destination with resume state."""

    def __init__(self, client, config, console):
        self.client = client
        self.config = config
        self.console = console
        self.db_path = Path(config.transfer_db_path)

    def _build_job_key(self) -> str:
        """Build a stable job key for resume tracking."""
        base = f"{self.config.target} -> {self.config.dest_target} ({self.config.transfer_mode})"
        selected_ids = getattr(self.config, "transfer_message_ids", None) or []
        if not selected_ids:
            return base

        canonical = ",".join(str(mid) for mid in sorted(set(selected_ids)))
        digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]
        return f"{base} [selected:{len(set(selected_ids))}:{digest}]"

    async def run(self, source_entity, dest_entity) -> dict:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = db_connect(self.db_path)
        db_init(conn)

        job_key = self._build_job_key()
        existing = db_load_job(conn, job_key)
        if existing:
            last_id, forwarded_count, skipped_count, flood_seconds, started_at = existing
        else:
            last_id = 0
            forwarded_count = 0
            skipped_count = 0
            flood_seconds = 0
            started_at = time.time()
            db_upsert_job(
                conn,
                job_key,
                self.config.target,
                self.config.dest_target,
                self.config.transfer_mode,
                last_id,
                forwarded_count,
                skipped_count,
                flood_seconds,
                started_at,
            )

        estimator = RateEstimator(alpha=self.config.transfer_eta_alpha)
        throttle = AdaptiveThrottle(
            batch=self.config.transfer_batch,
            delay=self.config.transfer_delay,
            jitter=self.config.transfer_jitter,
            batch_min=self.config.transfer_batch_min,
            batch_max=self.config.transfer_batch_max,
            delay_min=self.config.transfer_delay_min,
            delay_max=self.config.transfer_delay_max,
        )

        self.console.print(
            f"[cyan]↪ Transfer mode: {self.config.transfer_mode} | resume from message id {last_id}[/cyan]"
        )

        selected_ids = sorted(set(getattr(self.config, "transfer_message_ids", None) or []))
        if selected_ids:
            self.console.print(
                f"[cyan]↪ Selected transfer: {len(selected_ids)} message ID(s)[/cyan]"
            )

        last_eta_print = 0.0
        batch_ids: List[int] = []
        newest_id_in_batch: Optional[int] = None

        try:
            if selected_ids:
                for mid in selected_ids:
                    mid = int(mid)

                    # Resume guard for selected IDs.
                    if mid <= last_id:
                        continue

                    if self.config.transfer_skip_known_bad and db_has_skip(conn, job_key, mid):
                        continue

                    batch_ids.append(mid)
                    newest_id_in_batch = mid

                    if len(batch_ids) < throttle.batch:
                        continue

                    (
                        last_id,
                        forwarded_count,
                        skipped_count,
                        flood_seconds,
                        last_eta_print,
                    ) = await self._flush_batch(
                        conn=conn,
                        job_key=job_key,
                        source=source_entity,
                        dest=dest_entity,
                        batch_ids=batch_ids,
                        newest_id=newest_id_in_batch,
                        last_id=last_id,
                        forwarded_count=forwarded_count,
                        skipped_count=skipped_count,
                        flood_seconds=flood_seconds,
                        started_at=started_at,
                        estimator=estimator,
                        throttle=throttle,
                        last_eta_print=last_eta_print,
                    )

                    batch_ids.clear()
                    newest_id_in_batch = None
                    await throttle.sleep()
            else:
                async for message in self.client.iter_messages(source_entity, reverse=True, min_id=last_id):
                    mid = getattr(message, "id", None)
                    if mid is None:
                        continue
                    mid = int(mid)

                    # Strong resume guard
                    if mid <= last_id:
                        continue

                    # Optional service-message skip
                    if self.config.skip_service_messages:
                        if getattr(message, "action", None) is not None:
                            continue
                        if not getattr(message, "message", None) and not getattr(message, "media", None):
                            continue

                    # Avoid known-bad IDs if enabled
                    if self.config.transfer_skip_known_bad and db_has_skip(conn, job_key, mid):
                        continue

                    batch_ids.append(mid)
                    newest_id_in_batch = mid

                    if len(batch_ids) < throttle.batch:
                        continue

                    (
                        last_id,
                        forwarded_count,
                        skipped_count,
                        flood_seconds,
                        last_eta_print,
                    ) = await self._flush_batch(
                        conn=conn,
                        job_key=job_key,
                        source=source_entity,
                        dest=dest_entity,
                        batch_ids=batch_ids,
                        newest_id=newest_id_in_batch,
                        last_id=last_id,
                        forwarded_count=forwarded_count,
                        skipped_count=skipped_count,
                        flood_seconds=flood_seconds,
                        started_at=started_at,
                        estimator=estimator,
                        throttle=throttle,
                        last_eta_print=last_eta_print,
                    )

                    batch_ids.clear()
                    newest_id_in_batch = None
                    await throttle.sleep()

            # flush tail
            if batch_ids and newest_id_in_batch is not None:
                (
                    last_id,
                    forwarded_count,
                    skipped_count,
                    flood_seconds,
                    _,
                ) = await self._flush_batch(
                    conn=conn,
                    job_key=job_key,
                    source=source_entity,
                    dest=dest_entity,
                    batch_ids=batch_ids,
                    newest_id=newest_id_in_batch,
                    last_id=last_id,
                    forwarded_count=forwarded_count,
                    skipped_count=skipped_count,
                    flood_seconds=flood_seconds,
                    started_at=started_at,
                    estimator=estimator,
                    throttle=throttle,
                    last_eta_print=last_eta_print,
                    force_log=True,
                )
        finally:
            conn.close()

        elapsed = max(0.001, time.time() - started_at)
        return {
            "mode": self.config.transfer_mode,
            "source": self.config.target,
            "dest": self.config.dest_target,
            "last_id": int(last_id),
            "forwarded_count": int(forwarded_count),
            "skipped_count": int(skipped_count),
            "flood_seconds": int(flood_seconds),
            "elapsed_time": elapsed,
            "db_path": str(self.db_path),
        }

    async def _flush_batch(
        self,
        conn,
        job_key,
        source,
        dest,
        batch_ids,
        newest_id,
        last_id,
        forwarded_count,
        skipped_count,
        flood_seconds,
        started_at,
        estimator,
        throttle,
        last_eta_print,
        force_log: bool = False,
    ):
        t0 = time.time()
        ok_count, skip_count, flood_added = await self._send_ids_isolating(
            conn=conn,
            job_key=job_key,
            source=source,
            dest=dest,
            ids=list(batch_ids),
        )
        elapsed = max(0.001, time.time() - t0)

        flood_seconds += flood_added
        forwarded_count += ok_count
        skipped_count += skip_count
        last_id = newest_id or last_id

        rate = estimator.update(ok_count + skip_count, elapsed)
        throttle.on_batch(flood_added)

        db_insert_batch(
            conn,
            job_key,
            last_id,
            len(batch_ids),
            ok_count,
            skip_count,
            flood_added,
            elapsed,
        )
        db_upsert_job(
            conn,
            job_key,
            self.config.target,
            self.config.dest_target,
            self.config.transfer_mode,
            last_id,
            forwarded_count,
            skipped_count,
            flood_seconds,
            started_at,
        )

        now = time.time()
        should_log = force_log or (now - last_eta_print >= self.config.transfer_eta_every)
        if should_log:
            self.console.print(
                "[cyan]Progress[/cyan] "
                f"id<={last_id} ok={forwarded_count} skipped={skipped_count} "
                f"speed={rate * 60.0:.1f} msg/min "
                f"flood_total={flood_seconds}s "
                f"batch={throttle.batch} delay={throttle.delay:.2f}s"
            )
            last_eta_print = now

        return last_id, forwarded_count, skipped_count, flood_seconds, last_eta_print

    async def _send_ids_once(self, source, dest, ids: List[int]) -> None:
        if self.config.transfer_mode == "copy":
            copy_fn = getattr(self.client, "copy_messages", None)
            if callable(copy_fn):
                await copy_fn(dest, ids, from_peer=source)
                return
        await self.client.forward_messages(dest, ids, from_peer=source)

    async def _send_ids_isolating(self, conn, job_key: str, source, dest, ids: List[int]) -> Tuple[int, int, int]:
        """Send IDs with retry and binary-split isolation for bad messages."""
        flood_added_total = 0

        async def send_chunk(chunk: List[int]) -> Tuple[int, int]:
            nonlocal flood_added_total

            if not chunk:
                return 0, 0

            # Avoid repeated retries for known bad singleton.
            if len(chunk) == 1 and db_has_skip(conn, job_key, chunk[0]):
                return 0, 1

            retries = 0
            backoff = 0.7
            last_error: Optional[Exception] = None
            while True:
                try:
                    await self._send_ids_once(source, dest, chunk)
                    return len(chunk), 0
                except FloodWaitError as e:
                    last_error = e
                    wait_s = int(getattr(e, "seconds", 0) or 1)
                    flood_added_total += wait_s
                    self.console.print(f"[yellow]FloodWait {wait_s}s for chunk size {len(chunk)}[/yellow]")
                    await asyncio.sleep(wait_s)
                    retries += 1
                    if retries <= self.config.transfer_max_retries:
                        continue
                except RPCError as e:
                    last_error = e
                    if is_fatal_rpc(e):
                        raise
                    if is_retryable_rpc(e) and retries < self.config.transfer_max_retries:
                        await asyncio.sleep(backoff)
                        backoff = min(20.0, backoff * 1.8)
                        retries += 1
                        continue

                break

            # Isolation fallback
            if len(chunk) == 1:
                error = last_error or Exception("isolated_failure")
                inserted = db_insert_skip(conn, job_key, chunk[0], error)
                if inserted:
                    self.console.print(f"[yellow]Skipping message id {chunk[0]}[/yellow]")
                return 0, 1

            midx = len(chunk) // 2
            left = chunk[:midx]
            right = chunk[midx:]
            ok1, sk1 = await send_chunk(left)
            ok2, sk2 = await send_chunk(right)
            return ok1 + ok2, sk1 + sk2

        ok, skipped = await send_chunk(ids)
        return ok, skipped, flood_added_total
