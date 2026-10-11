
import asyncio
import csv
import json
import os
import random
import string
import time

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import aiohttp
from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
    TimeElapsedColumn,
)

# ---------------- CONFIG ----------------

API_URL = "https://api.etcgamer.com/api/name-checker/psn"

# Set ETCGAMER_API_TOKEN in your shell before running.
API_TOKEN = os.environ.get("ETCGAMER_API_TOKEN", "57161718011a1e1916011d1d1b011b1c011e1c181e561b01191c181e18161e181c161a19171b1f19").strip()

TARGET = 50_000
USERNAME_LENGTH = 4
CHARSET = string.ascii_lowercase + string.digits

# Respect the API provider's rate limits.
CONCURRENCY = 5
TIMEOUT = 20
MAX_RETRIES = 4
DEFAULT_429_WAIT = 10
MAX_429_BEFORE_STOP = 10

OUT = Path("results")
OUT.mkdir(exist_ok=True)

NAMES_FILE = OUT / "names.txt"
RESULTS_FILE = OUT / "results.jsonl"
CSV_FILE = OUT / "results.csv"

console = Console()

state_lock = asyncio.Lock()
cooldown_lock = asyncio.Lock()

cooldown_until = 0.0
total_429 = 0
stop_event = asyncio.Event()

# Latest result for each username.
state = {}

stats = {
    "AVAILABLE": 0,
    "UNAVAILABLE": 0,
    "UNKNOWN": 0,
    "ERROR": 0,
}


def now():
    return datetime.now(timezone.utc).isoformat()


def load_state():
    if not RESULTS_FILE.exists():
        return

    with RESULTS_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                item = json.loads(line)
                name = item.get("username")
                status = item.get("status")

                if name and status in (*stats.keys(),):
                    state[name] = item

            except (ValueError, TypeError):
                continue

    # Count only the latest result for each username.
    for item in state.values():
        status = item["status"]
        if status in stats:
            stats[status] += 1


def make_names():
    if NAMES_FILE.exists():
        names = list(dict.fromkeys(
            line.strip()
            for line in NAMES_FILE.read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        ))
        return names

    possible = len(CHARSET) ** USERNAME_LENGTH
    if TARGET > possible:
        raise ValueError(
            f"TARGET={TARGET} exceeds {possible} possible names."
        )

    names = set()

    while len(names) < TARGET:
        names.add(
            "".join(
                random.choices(CHARSET, k=USERNAME_LENGTH)
            )
        )

    names = list(names)
    random.shuffle(names)

    NAMES_FILE.write_text(
        "\n".join(names) + "\n",
        encoding="utf-8",
    )
    return names


def parse_retry_after(value):
    if value:
        try:
            return max(1, min(float(value), 300))
        except (ValueError, TypeError):
            try:
                dt = parsedate_to_datetime(value)
                seconds = (
                    dt - datetime.now(dt.tzinfo)
                ).total_seconds()
                return max(1, min(seconds, 300))
            except (ValueError, TypeError, OverflowError):
                pass

    return DEFAULT_429_WAIT


async def pause_all(seconds):
    global cooldown_until

    async with cooldown_lock:
        cooldown_until = max(
            cooldown_until,
            time.monotonic() + seconds,
        )


async def wait_cooldown():
    while not stop_event.is_set():
        async with cooldown_lock:
            remaining = cooldown_until - time.monotonic()

        if remaining <= 0:
            return

        await asyncio.sleep(min(remaining, 1))


async def save_result(item):
    async with state_lock:
        name = item["username"]
        old = state.get(name)

        if old:
            old_status = old.get("status")
            if old_status in stats:
                stats[old_status] -= 1

        state[name] = item

        status = item["status"]
        if status in stats:
            stats[status] += 1

        # Append-only log; latest record wins during resume.
        with RESULTS_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


async def check(session, name):
    global total_429

    for attempt in range(MAX_RETRIES + 1):
        if stop_event.is_set():
            return None

        await wait_cooldown()

        if stop_event.is_set():
            return None

        try:
            # API format confirmed by the supplied example:
            # GET /api/name-checker/psn?id=USERNAME&token=TOKEN
            async with session.get(
                API_URL,
                params={
                    "id": name,
                    "token": API_TOKEN,
                },
            ) as response:
                code = response.status
                retry_after = response.headers.get("Retry-After")
                body = await response.text()

            if code == 429:
                total_429 += 1
                wait_seconds = parse_retry_after(retry_after)

                await pause_all(wait_seconds)

                if total_429 >= MAX_429_BEFORE_STOP:
                    stop_event.set()
                    console.print(
                        "\n[bold red]Too many HTTP 429 responses. "
                        "Stopping to protect the API.[/bold red]"
                    )
                    return None

                if attempt < MAX_RETRIES:
                    continue

                return {
                    "username": name,
                    "status": "ERROR",
                    "http_status": 429,
                    "detail": "Retry limit reached",
                    "timestamp": now(),
                }

            if code in (401, 403):
                stop_event.set()
                console.print(
                    f"\n[bold red]HTTP {code}: "
                    "authentication/access denied. Stopping.[/bold red]"
                )
                return {
                    "username": name,
                    "status": "ERROR",
                    "http_status": code,
                    "detail": "Authorization/access denied",
                    "timestamp": now(),
                }

            if code in (500, 502, 503, 504):
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(min(2 ** attempt, 30))
                    continue

            if code != 200:
                return {
                    "username": name,
                    "status": "ERROR",
                    "http_status": code,
                    "detail": body[:200],
                    "timestamp": now(),
                }

            try:
                payload = json.loads(body)
                result = payload.get("result")
            except (ValueError, AttributeError):
                result = None

            if result == 1:
                status = "AVAILABLE"
            elif result == 0:
                status = "UNAVAILABLE"
            else:
                status = "UNKNOWN"

            return {
                "username": name,
                "status": status,
                "http_status": 200,
                "detail": "",
                "timestamp": now(),
            }

        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            if attempt < MAX_RETRIES:
                await asyncio.sleep(min(2 ** attempt, 30))
                continue

            return {
                "username": name,
                "status": "ERROR",
                "http_status": None,
                "detail": str(exc)[:200],
                "timestamp": now(),
            }

    return None


def export_csv():
    with CSV_FILE.open("w", newline="", encoding="utf-8") as f:
        columns = [
            "username",
            "status",
            "http_status",
            "detail",
            "timestamp",
        ]
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()

        for name in sorted(state):
            writer.writerow({
                key: state[name].get(key, "")
                for key in columns
            })


async def worker(queue, session, progress, task):
    while True:
        name = await queue.get()

        try:
            if name is None:
                return

            if stop_event.is_set():
                continue

            result = await check(session, name)

            if result is not None:
                await save_result(result)

                if result["status"] == "ERROR":
                    console.print(
                        f"[yellow]{name}: "
                        f"HTTP {result.get('http_status')} "
                        f"{result.get('detail', '')}[/yellow]"
                    )

            progress.advance(task)

        finally:
            queue.task_done()


async def main():
    if not API_TOKEN:
        console.print(
            "[bold red]Missing API token.[/bold red]\n"
            "Set ETCGAMER_API_TOKEN before running."
        )
        return

    load_state()
    names = make_names()

    # Successful and UNKNOWN results are complete.
    # ERROR entries are eligible for retry on a later run.
    pending = [
        name for name in names
        if name not in state
        or state[name].get("status") == "ERROR"
    ]

    console.print(
        "[bold cyan]DTear PSNChecker[/bold cyan]\n"
        f"Target: {len(names):,}\n"
        f"Previously completed: {len(state):,}\n"
        f"Remaining: {len(pending):,}\n"
        f"Concurrency: {CONCURRENCY}"
    )

    if not pending:
        export_csv()
        console.print("[green]All names already have results.[/green]")
        return

    queue = asyncio.Queue()
    for name in pending:
        queue.put_nowait(name)

    timeout = aiohttp.ClientTimeout(total=TIMEOUT)
    connector = aiohttp.TCPConnector(
        limit=CONCURRENCY,
        limit_per_host=CONCURRENCY,
        ttl_dns_cache=300,
    )

    headers = {
        "Accept": "application/json",
        "User-Agent": "DTear/2.1",
    }

    start = time.monotonic()

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(
                "Checking",
                total=len(pending),
            )

            async with aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                headers=headers,
            ) as session:
                workers = [
                    asyncio.create_task(
                        worker(queue, session, progress, task)
                    )
                    for _ in range(CONCURRENCY)
                ]

                await queue.join()

                for _ in workers:
                    queue.put_nowait(None)

                await asyncio.gather(*workers)

    except KeyboardInterrupt:
        stop_event.set()
        console.print("\n[yellow]Interrupted by user.[/yellow]")

    finally:
        export_csv()

    elapsed = time.monotonic() - start

    console.print("\n[bold]Summary[/bold]")
    for status, count in stats.items():
        console.print(f"{status}: {count:,}")

    console.print(f"HTTP 429 responses: {total_429:,}")
    console.print(f"Elapsed: {elapsed:.1f} seconds")
    console.print(f"Results: {RESULTS_FILE}")
    console.print(f"CSV: {CSV_FILE}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped.[/yellow]")
