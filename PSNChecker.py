
#!/usr/bin/env python3
# PSN Checker - STear-inspired terminal interface

import csv
import json
import random
import string
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    Progress,
    BarColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text


# ================= CONFIG =================

API_URL = "https://api.etcgamer.com/api/name-checker/psn"

# Enter your ETCGamer API token here.
API_TOKEN = "57161718011a1e1916011d1d1b011b1c011e1c181e561b01191c181e18161e181c161a19171b1f19"

TOTAL_NAMES = 5000
USERNAME_LENGTH = 4
CHARSET = string.ascii_lowercase + string.digits

REQUEST_DELAY = 2.0
TIMEOUT = 20
MAX_RETRIES = 3
MAX_CONSECUTIVE_429 = 3

OUT = Path("results")
SAVE_EVERY = 1

console = Console()

# ==========================================


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def generate_names(count):
    possible = len(CHARSET) ** USERNAME_LENGTH

    if count > possible:
        raise ValueError("Too many names for this character set.")

    names = set()
    while len(names) < count:
        names.add(
            "".join(
                random.choices(CHARSET, k=USERNAME_LENGTH)
            )
        )

    result = list(names)
    random.shuffle(result)
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


def check_name(username):
    params = urllib.parse.urlencode({
        "id": username,
        "token": API_TOKEN,
    })

    url = f"{API_URL}?{params}"

    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "PSNChecker/1.0",
        },
        method="GET",
    )

    for attempt in range(MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(
                request, timeout=TIMEOUT
            ) as response:
                status_code = response.status
                body = response.read().decode(
                    "utf-8", errors="replace"
                )

            status, api_result = classify(status_code, body)

            return {
                "username": username,
                "status": status,
                "http_status": status_code,
                "result": api_result,
                "response": body[:2000],
                "checked_at": timestamp(),
            }

        except urllib.error.HTTPError as exc:
            body = exc.read().decode(
                "utf-8", errors="replace"
            )

            if exc.code == 429 and attempt < MAX_RETRIES:
                retry_after = exc.headers.get("Retry-After")

                try:
                    delay = max(
                        2.0, min(float(retry_after), 300.0)
                    )
                except (TypeError, ValueError):
                    delay = min(10 * (attempt + 1), 120)

                time.sleep(delay)
                continue

            if exc.code in (500, 502, 503, 504):
                if attempt < MAX_RETRIES:
                    time.sleep(min(5 * (2 ** attempt), 60))
                    continue

            return {
                "username": username,
                "status": "ERROR",
                "http_status": exc.code,
                "result": None,
                "response": body[:2000],
                "checked_at": timestamp(),
            }

        except (TimeoutError, OSError, urllib.error.URLError) as exc:
            if attempt < MAX_RETRIES:
                time.sleep(min(3 * (2 ** attempt), 30))
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


def make_dashboard(stats, recent, progress, started):
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

    elapsed = max(time.monotonic() - started, 0.001)
    processed = stats["processed"]
    speed = processed / elapsed
    percent = (
        processed * 100 / stats["target"]
        if stats["target"] else 0
    )

    info = Table.grid(expand=True, padding=(0, 1))
    info.add_column(style="bold cyan")
    info.add_column(justify="right")
    info.add_row("Target", str(stats["target"]))
    info.add_row("Processed", f'{processed}/{stats["target"]}')
    info.add_row("Progress", f"{percent:.2f}%")
    info.add_row("Speed", f"{speed:.3f} checks/s")
    info.add_row("Elapsed", f"{elapsed:.1f}s")

    counters = Table(box=box.ROUNDED, expand=True)
    counters.add_column("Status")
    counters.add_column("Count", justify="right")

    counters.add_row(
        "[green]AVAILABLE[/]",
        str(stats["AVAILABLE"]),
    )
    counters.add_row(
        "[red]UNAVAILABLE[/]",
        str(stats["UNAVAILABLE"]),
    )
    counters.add_row(
        "[yellow]UNKNOWN[/]",
        str(stats["UNKNOWN"]),
    )
    counters.add_row(
        "[magenta]ERROR[/]",
        str(stats["ERROR"]),
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

    progress.update(
        progress.task_ids[0],
        completed=processed,
    )

    return Group(
        header,
        Panel(info, title="Runtime", border_style="blue"),
        Panel(counters, title="Statistics", border_style="cyan"),
        progress,
        recent_table,
    )


def main():
    if (
        not API_TOKEN
        or API_TOKEN == "PASTE_YOUR_ETCGAMER_TOKEN_HERE"
    ):
        console.print(
            "[red]Set your ETCGamer API token in API_TOKEN first.[/]"
        )
        return

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
    last_request = 0.0
    consecutive_429 = 0

    fields = [
        "username",
        "status",
        "http_status",
        "result",
        "response",
        "checked_at",
    ]

    console.print(
        Panel(
            "[bold cyan]PSN CHECKER[/]\n"
            "4-character usernames | letters + numbers\n"
            f"Target: {TOTAL_NAMES}\n"
            f"Minimum request interval: {REQUEST_DELAY}s",
            border_style="cyan",
            box=box.DOUBLE,
        )
    )

    try:
        names = generate_names(TOTAL_NAMES)

        with (
            paths["all"].open("w", encoding="utf-8") as json_file,
            paths["ERROR"].open("w", encoding="utf-8") as error_file,
            paths["csv"].open(
                "w", newline="", encoding="utf-8"
            ) as csv_file,
            paths["AVAILABLE"].open("w", encoding="utf-8") as available_file,
            paths["UNAVAILABLE"].open("w", encoding="utf-8") as unavailable_file,
            paths["UNKNOWN"].open("w", encoding="utf-8") as unknown_file,
        ):
            csv_writer = csv.DictWriter(
                csv_file, fieldnames=fields
            )
            csv_writer.writeheader()

            progress = Progress(
                TextColumn("[bold cyan]Progress"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                TimeElapsedColumn(),
                console=console,
            )
            progress.add_task("checks", total=TOTAL_NAMES)

            with Live(
                make_dashboard(stats, recent, progress, started),
                console=console,
                refresh_per_second=4,
                transient=False,
            ) as live:
                for username in names:
                    elapsed = time.monotonic() - last_request

                    if elapsed < REQUEST_DELAY:
                        time.sleep(REQUEST_DELAY - elapsed)

                    last_request = time.monotonic()
                    record = check_name(username)
                    status = record["status"]

                    stats["processed"] += 1
                    stats[status] += 1
                    recent.append(record)

                    line = json.dumps(
                        record, ensure_ascii=False
                    )

                    json_file.write(line + "\n")
                    csv_writer.writerow(record)

                    if status == "AVAILABLE":
                        available_file.write(username + "\n")
                    elif status == "UNAVAILABLE":
                        unavailable_file.write(username + "\n")
                    elif status == "UNKNOWN":
                        unknown_file.write(line + "\n")
                    elif status == "ERROR":
                        error_file.write(line + "\n")

                    # Keep progress and results on disk.
                    for file_obj in (
                        json_file,
                        error_file,
                        csv_file,
                        available_file,
                        unavailable_file,
                        unknown_file,
                    ):
                        file_obj.flush()

                    if record["http_status"] == 429:
                        consecutive_429 += 1
                    else:
                        consecutive_429 = 0

                    live.update(
                        make_dashboard(
                            stats, recent, progress, started
                        )
                    )

                    if consecutive_429 >= MAX_CONSECUTIVE_429:
                        live.console.print(
                            "[yellow]Repeated HTTP 429. "
                            "Stopping to respect API limits.[/]"
                        )
                        break

    except KeyboardInterrupt:
        console.print(
            "\n[yellow]Stopped by user. Saved results are retained.[/]"
        )

    except Exception as exc:
        console.print(
            f"\n[red]Fatal error: {type(exc).__name__}: {exc}[/]"
        )

    finally:
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


if __name__ == "__main__":
    main()
