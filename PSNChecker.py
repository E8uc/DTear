
#!/usr/bin/env python3
# PSN Checker - Async optimized edition

import asyncio
import csv
import json
import random
import string
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.progress import (
    Progress,
    BarColumn,
    TextColumn,
    TimeElapsedColumn,
)

# ================= CONFIG =================

API_URL = "https://api.etcgamer.com/api/name-checker/psn"

API_TOKEN = (
    "57161718011a1e1916011d1d1b011b1c"
    "011e1c181e561b01191c181e18161e181c161a19171b1f19"
)

TOTAL_NAMES = 5000
USERNAME_LENGTH = 4
CHARSET = string.ascii_lowercase + string.digits

# Increase cautiously if the API supports it.
CONCURRENCY = 10

TIMEOUT = 20
MAX_RETRIES = 3
MAX_CONSECUTIVE_429 = 3

RETRY_BASE_DELAY = 1.0
UI_REFRESH_INTERVAL = 0.25
FLUSH_EVERY = 25

OUT = Path("results")

console = Console()

# ==========================================


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def generate_names(count):
    possible = len(CHARSET) ** USERNAME_LENGTH

    if not 0 <= count <= possible:
        raise ValueError(
            f"Count must be between 0 and {possible}."
        )

    # Sampling without repeatedly generating duplicate names.
    names = random.sample(
        range(possible),
        count,
    )

    base = len(CHARSET)
    result = []

    for number in names:
        chars = []

        for _ in range(USERNAME_LENGTH):
            chars.append(CHARSET[number % base])
            number //= base

        result.append("".join(reversed(chars)))

    return result


def classify(http_status, body):
    if http_status != 200:
        return "ERROR", None

    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return "UNKNOWN", None

    value = data.get("result")

    if type(value) is int and value == 1:
        return "AVAILABLE", value

    if type(value) is int and value == 0:
        return "UNAVAILABLE", value

    return "UNKNOWN", value


async def check_name(session, username):
    params = {
        "id": username,
        "token": API_TOKEN,
    }

    for attempt in range(MAX_RETRIES + 1):
        try:
            async with session.get(
                API_URL,
                params=params,
            ) as response:
                status_code = response.status
                body = await response.text()

            # Retry temporary service errors.
            if (
                status_code in (500, 502, 503, 504)
                and attempt < MAX_RETRIES
            ):
                await asyncio.sleep(
                    min(RETRY_BASE_DELAY * (2 ** attempt), 30)
                )
                continue

            # Respect the API's rate-limit response.
            if status_code == 429 and attempt < MAX_RETRIES:
                retry_after = response.headers.get("Retry-After")

                try:
                    delay = max(
                        1.0,
                        min(float(retry_after), 300.0),
                    )
                except (TypeError, ValueError):
                    delay = min(
                        RETRY_BASE_DELAY * (2 ** (attempt + 1)),
                        60.0,
                    )

                await asyncio.sleep(delay)
                continue

            result_status, api_result = classify(
                status_code,
                body,
            )

            return {
                "username": username,
                "status": result_status,
                "http_status": status_code,
                "result": api_result,
                "response": body[:2000],
                "checked_at": timestamp(),
            }

        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            if attempt < MAX_RETRIES:
                await asyncio.sleep(
                    min(RETRY_BASE_DELAY * (2 ** attempt), 30)
                )
                continue

            return {
                "username": username,
                "status": "ERROR",
                "http_status": 0,
                "result": None,
                "response": str(exc)[:1000],
                "checked_at": timestamp(),
            }

    return {
        "username": username,
        "status": "ERROR",
        "http_status": 0,
        "result": None,
        "response": "Retry limit reached",
        "checked_at": timestamp(),
    }


def make_dashboard(stats, recent, progress, task_id, started):
    elapsed = max(time.monotonic() - started, 0.001)
    processed = stats["processed"]
    speed = processed / elapsed

    percent = (
        processed * 100 / stats["target"]
        if stats["target"]
        else 0
    )

    info = Table.grid(expand=True, padding=(0, 1))
    info.add_column(style="bold cyan")
    info.add_column(justify="right")

    info.add_row("Target", str(stats["target"]))
    info.add_row("Processed", f'{processed}/{stats["target"]}')
    info.add_row("Progress", f"{percent:.2f}%")
    info.add_row("Speed", f"{speed:.2f} checks/s")
    info.add_row("Elapsed", f"{elapsed:.1f}s")

    counters = Table(box=box.ROUNDED, expand=True)
    counters.add_column("Status")
    counters.add_column("Count", justify="right")

    for status, color in (
        ("AVAILABLE", "green"),
        ("UNAVAILABLE", "red"),
        ("UNKNOWN", "yellow"),
        ("ERROR", "magenta"),
    ):
        counters.add_row(
            f"[{color}]{status}[/]",
            str(stats[status]),
        )

    recent_table = Table(
        title="Recent checks",
        box=box.SIMPLE,
        expand=True,
    )
    recent_table.add_column("Username", style="cyan")
    recent_table.add_column("Status")
    recent_table.add_column("HTTP", justify="right")

    colors = {
        "AVAILABLE": "green",
        "UNAVAILABLE": "red",
        "UNKNOWN": "yellow",
        "ERROR": "magenta",
    }

    for item in recent:
        color = colors.get(item["status"], "white")
        recent_table.add_row(
            item["username"],
            f"[{color}]{item['status']}[/]",
            str(item["http_status"]),
        )

    progress.update(task_id, completed=processed)

    header = Panel(
        Align.center(
            Text(
                "PSN CHECKER  |  ETCGAMER API",
                style="bold cyan",
            )
        ),
        box=box.DOUBLE,
        border_style="cyan",
    )

    return Group(
        header,
        Panel(info, title="Runtime", border_style="blue"),
        Panel(counters, title="Statistics", border_style="cyan"),
        progress,
        recent_table,
    )


async def worker(session, name_queue, result_queue, stop_event):
    while not stop_event.is_set():
        try:
            username = name_queue.get_nowait()
        except asyncio.QueueEmpty:
            return

        record = await check_name(session, username)
        await result_queue.put(record)


async def run_checker():
    OUT.mkdir(parents=True, exist_ok=True)

    paths = {
        "all": OUT / "results.jsonl",
        "AVAILABLE": OUT / "available.txt",
        "UNAVAILABLE": OUT / "unavailable.txt",
        "UNKNOWN": OUT / "unknown.txt",
        "ERROR": OUT / "errors.jsonl",
        "csv": OUT / "results.csv",
    }

    stats = {
        "target": TOTAL_NAMES,
        "processed": 0,
        "AVAILABLE": 0,
        "UNAVAILABLE": 0,
        "UNKNOWN": 0,
        "ERROR": 0,
    }

    recent = deque(maxlen=8)
    started = time.monotonic()
    last_ui_update = 0.0
    consecutive_429 = 0
    stop_event = asyncio.Event()

    fields = [
        "username",
        "status",
        "http_status",
        "result",
        "response",
        "checked_at",
    ]

    names = generate_names(TOTAL_NAMES)
    name_queue = asyncio.Queue()

    for username in names:
        name_queue.put_nowait(username)

    result_queue = asyncio.Queue()

    timeout = aiohttp.ClientTimeout(total=TIMEOUT)
    connector = aiohttp.TCPConnector(
        limit=CONCURRENCY,
        limit_per_host=CONCURRENCY,
        ttl_dns_cache=300,
    )

    console.print(
        Panel(
            "[bold cyan]PSN CHECKER[/]\n"
            "4-character usernames | letters + numbers\n"
            f"Target: {TOTAL_NAMES}\n"
            f"Concurrency: {CONCURRENCY}",
            border_style="cyan",
            box=box.DOUBLE,
        )
    )

    processed_since_flush = 0

    with (
        paths["all"].open("w", encoding="utf-8") as json_file,
        paths["ERROR"].open("w", encoding="utf-8") as error_file,
        paths["csv"].open(
            "w", newline="", encoding="utf-8"
        ) as csv_file,
        paths["AVAILABLE"].open(
            "w", encoding="utf-8"
        ) as available_file,
        paths["UNAVAILABLE"].open(
            "w", encoding="utf-8"
        ) as unavailable_file,
        paths["UNKNOWN"].open(
            "w", encoding="utf-8"
        ) as unknown_file,
    ):
        csv_writer = csv.DictWriter(
            csv_file,
            fieldnames=fields,
        )
        csv_writer.writeheader()

        progress = Progress(
            TextColumn("[bold cyan]Progress"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=console,
        )
        task_id = progress.add_task(
            "checks",
            total=TOTAL_NAMES,
        )

        async with aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            headers={
                "Accept": "application/json",
                "User-Agent": "PSNChecker/2.0",
            },
        ) as session:
            workers = [
                asyncio.create_task(
                    worker(
                        session,
                        name_queue,
                        result_queue,
                        stop_event,
                    )
                )
                for _ in range(CONCURRENCY)
            ]

            with Live(
                make_dashboard(
                    stats, recent, progress, task_id, started
                ),
                console=console,
                refresh_per_second=4,
                transient=False,
            ) as live:
                try:
                    while (
                        any(not task.done() for task in workers)
                        or not result_queue.empty()
                    ):
                        try:
                            record = await asyncio.wait_for(
                                result_queue.get(),
                                timeout=0.1,
                            )
                        except asyncio.TimeoutError:
                            record = None

                        if record is not None:
                            status = record["status"]
                            stats["processed"] += 1
                            stats[status] += 1
                            recent.append(record)

                            line = json.dumps(
                                record,
                                ensure_ascii=False,
                            )

                            json_file.write(line + "\n")
                            csv_writer.writerow(record)

                            if status == "AVAILABLE":
                                available_file.write(
                                    record["username"] + "\n"
                                )
                            elif status == "UNAVAILABLE":
                                unavailable_file.write(
                                    record["username"] + "\n"
                                )
                            elif status == "UNKNOWN":
                                unknown_file.write(line + "\n")
                            elif status == "ERROR":
                                error_file.write(line + "\n")

                            if record["http_status"] == 429:
                                consecutive_429 += 1
                            else:
                                consecutive_429 = 0

                            if (
                                consecutive_429
                                >= MAX_CONSECUTIVE_429
                            ):
                                stop_event.set()

                            processed_since_flush += 1

                            if (
                                processed_since_flush >= FLUSH_EVERY
                            ):
                                for file_obj in (
                                    json_file,
                                    error_file,
                                    csv_file,
                                    available_file,
                                    unavailable_file,
                                    unknown_file,
                                ):
                                    file_obj.flush()

                                processed_since_flush = 0

                        now = time.monotonic()

                        if (
                            now - last_ui_update
                            >= UI_REFRESH_INTERVAL
                            or (
                                record is not None
                                and stats["processed"] == TOTAL_NAMES
                            )
                        ):
                            live.update(
                                make_dashboard(
                                    stats,
                                    recent,
                                    progress,
                                    task_id,
                                    started,
                                )
                            )
                            last_ui_update = now

                    await asyncio.gather(
                        *workers,
                        return_exceptions=True,
                    )

                except KeyboardInterrupt:
                    stop_event.set()
                    for task in workers:
                        task.cancel()

                    await asyncio.gather(
                        *workers,
                        return_exceptions=True,
                    )

                finally:
                    for file_obj in (
                        json_file,
                        error_file,
                        csv_file,
                        available_file,
                        unavailable_file,
                        unknown_file,
                    ):
                        file_obj.flush()

    console.print("\n[bold cyan]Run summary[/]")
    for key in (
        "processed",
        "AVAILABLE",
        "UNAVAILABLE",
        "UNKNOWN",
        "ERROR",
    ):
        console.print(f"  {key}: {stats[key]}")

    console.print(f"\nResults folder: {OUT.resolve()}")


def main():
    if not API_TOKEN:
        console.print("[red]API_TOKEN is missing.[/]")
        return

    try:
        asyncio.run(run_checker())
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped by user.[/]")
    except Exception as exc:
        console.print(
            f"\n[red]Fatal error: {type(exc).__name__}: {exc}[/]"
        )


if __name__ == "__main__":
    main()
