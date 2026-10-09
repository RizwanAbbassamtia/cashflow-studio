"""The ``ccs`` command line: ``serve``, ``open`` and ``doctor``."""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

import typer
import uvicorn

from .config import HOST, load_settings
from .doctor import DoctorReport, check_webview2, run_all

app = typer.Typer(
    help="CashCow Studio: turns competitor YouTube channels into finished faceless videos.",
    add_completion=False,
    no_args_is_help=True,
)

WINDOW_TITLE = "CashCow Studio"
WINDOW_SIZE = (1440, 900)
START_TIMEOUT_SECONDS = 30


@app.command()
def serve(
    port: int | None = typer.Option(None, "--port", help="Port on 127.0.0.1 (default 8765)."),
    reload: bool = typer.Option(False, "--reload", help="Restart when the code changes."),
) -> None:
    """Run the API server (and the built app screens) on 127.0.0.1."""
    settings = load_settings()
    chosen = port or settings.port
    typer.echo(f"CashCow Studio API on http://{HOST}:{chosen}")
    uvicorn.run(
        "cashcow_studio.app:create_app",
        factory=True,
        host=HOST,
        port=chosen,
        reload=reload,
        log_level="info",
    )


@app.command(name="open")
def open_window(
    port: int | None = typer.Option(None, "--port", help="Port on 127.0.0.1 (default 8765)."),
) -> None:
    """Start the server in the background and open the app in its own window."""
    settings = load_settings()
    chosen = port or settings.port
    url = f"http://{HOST}:{chosen}"

    server_thread: threading.Thread | None = None
    running = _our_server_answers(chosen)
    if running is True:
        typer.echo(f"Using the CashCow Studio server already running at {url}")
    elif running is False:
        typer.echo(
            f"Another program is already using port {chosen} on this PC, so CashCow Studio "
            f"cannot start there. Close that program, or run: ccs open --port {chosen + 1}",
            err=True,
        )
        raise typer.Exit(code=1)
    else:
        server_thread = _start_server_thread(chosen)
        if not _wait_for_port(chosen, START_TIMEOUT_SECONDS):
            typer.echo(
                f"The app server did not start on port {chosen} within "
                f"{START_TIMEOUT_SECONDS} seconds. Run 'ccs doctor' to see what is wrong.",
                err=True,
            )
            raise typer.Exit(code=1)
        typer.echo(f"CashCow Studio is running at {url}")

    if _open_native_window(url):
        return  # the window was closed; the daemon server thread ends with the process

    typer.echo("Opening CashCow Studio in your web browser instead.")
    webbrowser.open(url)
    if server_thread is not None:
        typer.echo("Keep this window open while you use the app. Press Ctrl+C to stop.")
        try:
            while server_thread.is_alive():
                server_thread.join(timeout=1)
        except KeyboardInterrupt:
            typer.echo("Stopped.")


@app.command()
def doctor() -> None:
    """Check Python, FFmpeg, folders and keys. Exits with code 1 if anything fails."""
    report = run_all(load_settings())
    typer.echo(format_report(report))
    raise typer.Exit(code=0 if report.ok else 1)


# Helpers -------------------------------------------------------------------------------------


def format_report(report: DoctorReport) -> str:
    """A plain text table, no colours, readable in any terminal."""
    rows = [
        (check.status.upper(), check.name, check.detail, check.fix_hint)
        for check in report.checks
    ]
    headers = ("STATUS", "CHECK", "DETAIL", "HOW TO FIX")
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row[:2]):
            widths[index] = max(widths[index], len(cell))
    lines = [
        f"{headers[0]:<{widths[0]}}  {headers[1]:<{widths[1]}}  {headers[2]}",
        "-" * (widths[0] + widths[1] + 4 + len(headers[2])),
    ]
    for status, name, detail, fix_hint in rows:
        lines.append(f"{status:<{widths[0]}}  {name:<{widths[1]}}  {detail}")
        if fix_hint:
            pad = " " * (widths[0] + widths[1] + 4)
            lines.append(f"{pad}{headers[3].title()}: {fix_hint}")
    counts = {
        status: sum(1 for check in report.checks if check.status == status)
        for status in ("ok", "warn", "fail")
    }
    if not report.ok:
        summary = "Some checks failed."
    elif counts["warn"]:
        summary = "Nothing failed, but some warnings need attention."
    else:
        summary = "All checks passed."
    lines.append("")
    lines.append(
        f"{summary} {counts['ok']} ok, {counts['warn']} warnings, {counts['fail']} failed."
    )
    return "\n".join(lines)


def _port_answers(port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((HOST, port), timeout=timeout):
            return True
    except OSError:
        return False


def _our_server_answers(port: int, timeout: float = 2.0) -> bool | None:
    """``True`` if CashCow Studio answers on the port, ``False`` if something else does,
    ``None`` if nothing is listening."""
    if not _port_answers(port):
        return None
    try:
        with urllib.request.urlopen(
            f"http://{HOST}:{port}/api/system/info", timeout=timeout
        ) as response:
            info = json.load(response)
    except Exception:  # not HTTP, not JSON, or an error page: not our server
        return False
    return isinstance(info, dict) and {"version", "app_data_dir"} <= set(info)


def _wait_for_port(port: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _port_answers(port):
            return True
        time.sleep(0.2)
    return False


def _start_server_thread(port: int) -> threading.Thread:
    from .app import create_app

    config = uvicorn.Config(create_app(), host=HOST, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="cfs-server", daemon=True)
    thread.start()
    return thread


def _open_native_window(url: str) -> bool:
    """Show the app in a pywebview window. Returns False if that is not possible."""
    if sys.platform == "win32" and check_webview2().status != "ok":
        typer.echo(
            "The Microsoft Edge WebView2 runtime is not installed, so the app cannot open "
            "in its own window.",
            err=True,
        )
        return False
    try:
        import webview
    except Exception as exc:  # pywebview missing or its platform bits failed to load
        typer.echo(f"The app window could not be prepared: {exc}", err=True)
        return False
    try:
        webview.create_window(WINDOW_TITLE, url, width=WINDOW_SIZE[0], height=WINDOW_SIZE[1])
        webview.start()
    except Exception as exc:
        typer.echo(f"The app window could not be opened: {exc}", err=True)
        return False
    return True


if __name__ == "__main__":
    app()
