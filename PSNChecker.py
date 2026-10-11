
#!/usr/bin/env python3
import asyncio
import aiohttp
import json
import csv
import os
import random
import string
import time
from pathlib import Path
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.progress import BarColumn, TaskProgressColumn, TimeElapsedColumn
from rich.table import Table
from rich.panel import Panel

# ---------------- CONFIG ----------------

API_URL = "https://api.etcgamer.com/api/name-checker/psn"
API_TOKEN = os.environ.get("ETCGAMER_API_TOKEN", "57161718011a1e1916011d1d1b011b1c011e1c181e561b01191c181e18161e181c161a19171b1f19").strip()

TARGET = 50_000
USERNAME_LENGTH = 4
CHARSET = string.ascii_lowercase + string.digits

CONCURRENCY = 5
TIMEOUT = 20
MAX_RETRIES = 5
DEFAULT_429_WAIT = 10

OUT = Path("results")
OUT.mkdir(exist_ok=True)

NAMES_FILE = OUT / "names.txt"
RESULTS_FILE = OUT / "results.jsonl"
CSV_FILE = OUT / "results.csv"

console = Console()
lock = asyncio.Lock()
cooldown_lock = asyncio.Lock()
cooldown_until = 0.0

stats = {
    "AVAILABLE": 0,
    "UNAVAILABLE": 0,
    "UNKNOWN": 0,
    "ERROR": 0,
    "429": 0,
}
done = set()


def now():
    return datetime.now(timezone.utc).isoformat()


def load_state():
    if not RESULTS_FILE.exists():
        return

    with RESULTS_FILE.open(encoding="utf-8") as f:
        for line in f:
            try:
                item = json.loads(line)
                name = item.get("username")
                status = item.get("status")
                if name and status in stats and status != "ERROR":
                    if name not in done:
                        done.add(name)
                        stats[status] += 1
            except (ValueError, TypeError):
                continue


def make_names():
    if NAMES_FILE.exists():
        return list(dict.fromkeys(
            x.strip()
            for x in NAMES_FILE.read_text(encoding="utf-8").splitlines()
            if x.strip()
        ))

    if TARGET > len(CHARSET) ** USERNAME_LENGTH:
        raise ValueError("TARGET exceeds possible unique usernames.")

    names = set()
    while len(names) < TARGET:
        names.add("".join(random.choices(CHARSET, k=USERNAME_LENGTH)))

    result = list(names)
    random.shuffle(result)
    NAMES_FILE.write_text("\n".join(result) + "\n", encoding="utf-8")
    return result


def parse_retry_after(value):
    if value:
        try:
            return max(1, min(float(value), 300))
        except ValueError:
            try:
                dt = parsedate_to_datetime(value)
                return max(
                    1,
                    min((dt - datetime.now(dt.tzinfo)).total_seconds(), 300)
                )
            except (ValueError, TypeError, OverflowError):
                pass
    return DEFAULT_429_WAIT


async def pause_all(seconds):
    global cooldown_until
    async with cooldown_lock:
        cooldown_until = max(cooldown_until, time.monotonic() + seconds)


async def wait_cooldown():
    while True:
        async with cooldown_lock:
            remaining = cooldown_until - time.monotonic()
        if remaining <= 0:
            return
        await asyncio.sleep(min(remaining, 1))


async def check(session, name):
    for attempt in range(MAX_RETRIES + 1):
        await wait_cooldown()

        try:
            # Adjust this to the API provider's documented auth format.
            async with session.get(
                API_URL,
                params={"name": name},
            ) as response:
                code = response.status
                retry_after = response.headers.get("Retry-After")
                body = await response.text()

            if code == 429:
                stats["429"] += 1
                await pause_all(parse_retry_after(retry_after))
                if attempt < MAX_RETRIES:
                    continue
                return {
                    "username": name, "status": "ERROR",
                    "http_status": 429, "detail": "Retry limit reached",
                    "timestamp": now(),
                }

            if code in (401, 403):
                return {
                    "username": name, "status": "ERROR",
                    "http_status": code,
                    "detail": "Authorization/access denied",
                    "timestamp": now(),
                }

            if code in (500, 502, 503, 504) and attempt < MAX_RETRIES:
                await asyncio.sleep(min(2 ** attempt, 30))
                continue

            if code != 200:
                return {
                    "username": name, "status": "ERROR",
                    "http_status": code, "detail": body[:200],
                    "timestamp": now(),
                }

            try:
                result = json.loads(body).get("result")
            except (ValueError, AttributeError):
                result = None

            status = (
                "AVAILABLE" if result == 1 else
                "UNAVAILABLE" if result == 0 else
                "UNKNOWN"
            )
            return {
                "username": name, "status": status,
                "http_status": code, "detail": "",
                "timestamp": now(),
            }

        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            if attempt < MAX_RETRIES:
                await asyncio.sleep(min(2 ** attempt, 30))
                continue
            return {
                "username": name, "status": "ERROR",
                "http_status": None, "detail": str(exc)[:200],
                "timestamp": now(),
            }


async def save(item):
    async with lock:
        with RESULTS_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

        status = item["status"]
        name = item["username"]

        if status != "ERROR":
            done.add(name)
        stats[status] += 1

        output = {
            "AVAILABLE": "available.txt",
            "UNAVAILABLE": "unavailable.txt",
            "UNKNOWN": "unknown.txt",
            "ERROR": "errors.jsonl",
        }[status]

        path = OUT / output
        with path.open("a", encoding="utf-8") as f:
            if status == "ERROR":
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
            else:
                f.write(name + "\n")


async def worker(queue, session, progress, task):
    while True:
        name = await queue.get()
        try:
            if name is None:
                return

            item = await check(session, name)
            await save(item)
            progress.advance(task)

            if item["status"] == "ERROR":
                console.print(
                    f"[yellow]{name}: HTTP {item.get('http_status')} "
                    f"{item.get('detail', '')}[/yellow]"
                )
        finally:
            queue.task_done()


def export_csv():
    with CSV_FILE.open("w", newline="", encoding="utf-8") as f:
        fields = ["username", "status", "http_status", "detail", "timestamp"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        if RESULTS_FILE.exists():
            with RESULTS_FILE.open(encoding="utf-8") as src:
                for line in src:
                    try:
                        writer.writerow(json.loads(line))
                    except ValueError:
                        pass


async def main():
    if not API_TOKEN:
        console.print(
            "[red]Set ETCGAMER_API_TOKEN to your API token first.[/red]"
        )
        return

    load_state()
    names = make_names()
    pending = [n for n in names if n not in done]

    if not pending:
        console.print("[green]Nothing left to check.[/green]")
        export_csv()
        return

    console.print(Panel.fit(
        f"[bold cyan]DTear PSNChecker[/bold cyan]\n"
        f"Target: {len(names):,}\n"
        f"Previously completed: {len(done):,}\n"
        f"Remaining: {len(pending):,}\n"
        f"Concurrency: {CONCURRENCY}"
    ))

    start = time.monotonic()
    timeout = aiohttp.ClientTimeout(total=TIMEOUT)
    connector = aiohttp.TCPConnector(
        limit=CONCURRENCY,
        limit_per_host=CONCURRENCY,
        ttl_dns_cache=300,
    )
    headers = {
        "Accept": "application/json",
        "User-Agent": "DTear/2.0",
        "Authorization": f"Bearer {API_TOKEN}",
    }

    queue = asyncio.Queue()
    for name in pending:
        queue.put_nowait(name)

    with Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Checking", total=len(pending))

        async with aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            headers=headers,
        ) as session:
            workers = [
                asyncio.create_task(worker(queue, session, progress, task))
                for _ in range(CONCURRENCY)
            ]

            await queue.join()

            for _ in workers:
                queue.put_nowait(None)
            await asyncio.gather(*workers)

    export_csv()
    elapsed = max(time.monotonic() - start, 0.001)

    table = Table(title="DTear Results")
    table.add_column("Metric")
    table.add_column("Value", justify="right")

    for label, value in stats.items():
        table.add_row(label, f"{value:,}")

    table.add_row("Elapsed", f"{elapsed:.1f}s")
    table.add_row("Results/sec", f"{sum(stats[k] for k in ('AVAILABLE', 'UNAVAILABLE', 'UNKNOWN')) / elapsed:.2f}")
    table.add_row("Remaining", f"{len(names) - len(done):,}")
    console.print(table)
    console.print(f"Files: {OUT.resolve()}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("[yellow]Stopped. Saved results will be reused next run.[/yellow]")
