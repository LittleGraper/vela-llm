from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import httpx
import uvicorn
from rich import box
from rich.live import Live
from rich.table import Table
from rich.text import Text

from banner import render_startup_banner
from cli_ui import VlArgumentParser, activity, fields, heading, make_console, message, output
from config_files import active_config_dir, ensure_config_files
from github_copilot_headers import github_user_headers
from github_copilot_patch import apply_github_copilot_oauth_patch
from litellm_registry import register_litellm_model_metadata
from settings import get_settings
from workspace_output import write_model_test_snapshot

PYPI_PACKAGE = "vela-llm"
MODEL_TEST_PROGRESS_INTERVAL = 0.1


@dataclass(frozen=True)
class ModelTestResult:
    entry: dict[str, str]
    ping_ms: float
    error: Exception | None = None


@dataclass(frozen=True)
class PidRecord:
    pid: int
    start_token: str | None


class ModelTestProgress:
    """Rich owns column widths and live redraw, including terminal resizing."""

    def __init__(self, registry: list[dict[str, str]]) -> None:
        self.registry = registry
        self.console = make_console()
        self.results: dict[str, ModelTestResult] = {}
        self.live: Live | None = None
        self.workspace = os.environ.get("VELA_LLM_WORKSPACE_COMMAND") == "1"
        self.last_snapshot_count = -1

    def table(self) -> Table:
        table = Table(
            title=f"Testing models {len(self.results)}/{len(self.registry)} complete",
            box=box.SIMPLE_HEAD,
            header_style="bold #70dfdf",
        )
        table.add_column("STATUS", no_wrap=True)
        table.add_column("MODEL", overflow="fold")
        table.add_column("PING", justify="right", no_wrap=True)
        table.add_column("DETAILS", overflow="fold")
        for entry in self.registry:
            result = self.results.get(entry["name"])
            status = Text("RUN", style="cyan")
            if result is not None:
                status = (
                    Text("PASS", style="green")
                    if result.error is None
                    else Text("Fail", style="red")
                )
            table.add_row(
                status,
                Text(entry["name"]),
                format_ping(result.ping_ms) if result is not None else "...",
                Text(short_error(result.error)) if result is not None and result.error else "",
            )
        return table

    def start(self) -> None:
        if self.workspace:
            self.update_snapshot()
        elif sys.stdout.isatty():
            self.live = Live(self.table(), console=self.console, auto_refresh=False)
            self.live.start(refresh=True)
        else:
            self.console.print(self.table())

    def tick(self, *, completed: int) -> None:
        if self.workspace:
            self.update_snapshot()
        elif self.live:
            self.live.update(self.table(), refresh=True)

    def update_snapshot(self) -> None:
        if self.last_snapshot_count == len(self.results):
            return
        with self.console.capture() as capture:
            self.console.print(self.table())
        write_model_test_snapshot(capture.get())
        self.last_snapshot_count = len(self.results)

    def finish(self, result: ModelTestResult) -> None:
        self.results[result.entry["name"]] = result
        self.tick(completed=len(self.results))

    def stop(self) -> None:
        if self.workspace:
            self.update_snapshot()
        elif self.live:
            self.live.update(self.table())
            self.live.stop()
        else:
            self.console.print(self.table())


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.version:
            output(package_version())
            return

        if args.command is None and sys.stdin.isatty() and sys.stdout.isatty():
            from shell_app import ShellApp

            ensure_config_files()
            ShellApp(get_settings()).run()
            return

        command = args.command or "help"
        titles = {
            "login": "GitHub login",
            "logout": "Sign out",
            "whoami": "Account",
            "api": "API settings",
            "stop": "Stop proxy",
            "quit": "Stop proxy",
            "update": "Update",
            "test": "Model connectivity",
        }
        if command in titles:
            heading(titles[command])
        if command in {"api", "start", "model", "models", "test"}:
            ensure_config_files()
        if command == "help":
            parser.print_help()
        elif command == "version":
            output(package_version())
        elif command == "start":
            start(args)
        elif command in {"stop", "quit"}:
            stop()
        elif command == "login":
            login()
        elif command == "logout":
            logout()
        elif command == "whoami":
            whoami()
        elif command == "api":
            api(show_key=args.show_key)
        elif command in {"model", "models"}:
            handle_models(args)
        elif command == "update":
            update()
        elif command == "test":
            test_models(args)
    except KeyboardInterrupt as exc:
        message("Cancelled.", level="warning")
        raise SystemExit(130) from exc
    except (RuntimeError, ValueError, OSError, httpx.HTTPError) as exc:
        message(short_error(exc), level="error", stderr=True)
        raise SystemExit(1) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = VlArgumentParser(
        prog="vl",
        description="vela-llm provides local access to GitHub Copilot models.",
    )
    parser.add_argument("-v", "--version", action="store_true", help="Show vl version and exit.")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("help", help="Show this help message.")
    subparsers.add_parser("version", help="Show vl version.")
    subparsers.add_parser("login", help="Authenticate GitHub Copilot with device flow.")
    subparsers.add_parser("logout", help="Remove saved GitHub Copilot OAuth credentials.")
    subparsers.add_parser("whoami", help="Show the current GitHub Copilot account.")
    api_parser = subparsers.add_parser("api", help="Show OpenAI and Anthropic API settings.")
    api_parser.add_argument(
        "--show-key",
        action="store_true",
        help="Show the complete local API key instead of masking it.",
    )
    subparsers.add_parser("stop", help="Stop the running proxy instance.")
    subparsers.add_parser("quit", help="Stop the running proxy instance.")
    subparsers.add_parser("update", help="Update vl to the latest stable PyPI release.")

    test_parser = subparsers.add_parser("test", help="Test connectivity for configured models.")
    test_parser.add_argument(
        "--model",
        action="append",
        dest="models",
        help="Only test a specific configured model. Can be passed multiple times.",
    )
    test_parser.add_argument(
        "--skip-auth-check",
        action="store_true",
        help="Run model tests without checking GitHub Copilot OAuth first.",
    )
    test_parser.add_argument(
        "--timeout",
        type=positive_float,
        default=60.0,
        help="Per-model test timeout in seconds. Default: 60.",
    )

    start_parser = subparsers.add_parser("start", help="Start the local proxy.")
    start_parser.add_argument(
        "--skip-auth-check",
        action="store_true",
        help="Start the proxy without checking GitHub Copilot OAuth first.",
    )
    start_parser.add_argument(
        "--no-restart-existing",
        action="store_true",
        help="Do not stop a previous proxy instance recorded in .vela-llm.pid.",
    )
    start_parser.add_argument(
        "--foreground",
        action="store_true",
        help="Run uvicorn in the current terminal instead of starting a background process.",
    )

    for name in ("model", "models"):
        models_parser = subparsers.add_parser(name, help="Manage models and context preferences.")
        models_parser.add_argument(
            "--fullscreen",
            action="store_true",
            help="Use the full-screen workspace (the default; retained for compatibility).",
        )

    return parser


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return parsed


def start(args: argparse.Namespace) -> None:
    settings = get_settings()
    settings.validate_local_api_key()
    config_dir = active_config_dir()
    pid_file = config_dir / ".vela-llm.pid"

    if not args.skip_auth_check:
        require_copilot_login()

    refresh_model_cache(settings.model_cache_path.parent)
    settings.validate_runtime()
    if not args.no_restart_existing and stop_existing_instance(pid_file):
        wait_for_port_available(settings.host, settings.port)
    ensure_port_available(settings.host, settings.port)
    print_startup_info(settings)
    if args.foreground:
        run_server_foreground(settings, pid_file)
        return

    process = start_server_background(settings, config_dir)
    write_pid_file(pid_file, process.pid)
    log_file = config_dir / "vela-llm.log"
    with activity("Waiting for the local proxy…"):
        ready = wait_for_port(settings.host, settings.port, process)
    if ready:
        message(f"Proxy is running in the background (pid {process.pid}).", level="success")
    else:
        message(
            f"Proxy was started in the background (pid {process.pid}), "
            "but the port did not become ready yet.",
            level="warning",
        )
    fields("Process", [("PID", process.pid), ("Log file", log_file)])


def update() -> None:
    command = ["uv", "tool", "install", "--force", PYPI_PACKAGE]
    message("Updating vela-llm to the latest stable PyPI release...", level="info")
    fields("Install command", [("Run", " ".join(command))])
    if should_print_manual_update_command():
        message(
            "Windows cannot replace a running uv tool environment. "
            "Run the command above after this vl process exits.",
            level="warning",
        )
        raise SystemExit(1)
    try:
        subprocess.run(command, check=True)
        message("vela-llm update completed.", level="success")
    except subprocess.CalledProcessError as exc:
        message(f"Update command failed with exit code {exc.returncode}.", level="error")
        raise SystemExit(exc.returncode) from exc


def should_print_manual_update_command() -> bool:
    return sys.platform == "win32" and is_running_from_uv_tool()


def is_running_from_uv_tool() -> bool:
    parts = {part.casefold() for part in Path(sys.prefix).parts}
    return {"uv", "tools", "vela-llm"}.issubset(parts)


def login() -> None:
    ensure_copilot_authenticated()


def logout() -> None:
    authenticator = github_copilot_authenticator()
    removed = 0
    for path in (authenticator.access_token_file, authenticator.api_key_file):
        token_file = Path(path)
        try:
            token_file.unlink()
            removed += 1
        except FileNotFoundError:
            continue
    if removed:
        message("GitHub Copilot OAuth credentials removed.", level="success")
    else:
        message("No saved GitHub Copilot OAuth credentials found.", level="info")


def whoami() -> None:
    authenticator = github_copilot_authenticator()
    if not has_access_token(authenticator):
        message("GitHub Copilot is not authenticated. Run `vl login` first.", level="warning")
        raise SystemExit(1)
    try:
        with activity("Reading GitHub account…"):
            account = fetch_github_account(authenticator)
    except Exception as exc:
        message(f"Unable to read GitHub account: {short_error(exc)}", level="error")
        message("Run `vl login` to authenticate again.", level="info")
        raise SystemExit(1) from exc

    login = account.get("login")
    user_id = account.get("id")
    if not login and user_id is None:
        raise RuntimeError("GitHub user response did not include login or id.")
    rows = []
    if login:
        rows.append(("GitHub Login:", login))
    if user_id is not None:
        rows.append(("GitHub User ID:", user_id))
    fields("Authenticated account", rows)


def api(*, show_key: bool = False) -> None:
    settings = get_settings()
    if not settings.local_api_key:
        msg = "LOCAL_API_KEY is required. Copy .env.example to .env and set a local key."
        raise RuntimeError(msg)
    print_api_info(settings, show_key=show_key)


def test_models(args: argparse.Namespace) -> None:
    settings = get_settings()
    settings.validate_runtime()
    selected = set(args.models or [])
    registry = [
        entry for entry in settings.model_registry() if not selected or entry["name"] in selected
    ]
    missing = selected - {entry["name"] for entry in registry}
    if missing:
        msg = f"Unknown configured model(s): {', '.join(sorted(missing))}"
        raise RuntimeError(msg)
    if not registry:
        raise RuntimeError("No models configured.")

    if not args.skip_auth_check:
        require_copilot_login()

    import litellm

    register_litellm_model_metadata(litellm, registry)

    message("Testing configured models...", level="info")
    results = test_model_entries_parallel(litellm, registry, timeout=args.timeout)
    failures = sum(1 for result in results if result.error is not None)
    if failures:
        message(f"{failures}/{len(results)} model checks failed.", level="error")
        raise SystemExit(1)
    message("All configured models are reachable.", level="success")


def test_model_entries_parallel(
    litellm, registry: list[dict[str, str]], *, timeout: float
) -> list[ModelTestResult]:
    progress = ModelTestProgress(registry)
    progress.start()
    with ThreadPoolExecutor(max_workers=len(registry)) as executor:
        futures_by_entry = {
            executor.submit(test_model_entry_timed, litellm, entry, timeout=timeout): entry
            for entry in registry
        }
        pending: set[Future[ModelTestResult]] = set(futures_by_entry)
        results: list[ModelTestResult] = []
        try:
            while pending:
                done, pending = wait(
                    pending,
                    timeout=MODEL_TEST_PROGRESS_INTERVAL,
                    return_when=FIRST_COMPLETED,
                )
                if not done:
                    progress.tick(completed=len(results))
                    continue
                for future in done:
                    result = future.result()
                    results.append(result)
                    progress.finish(result)
                progress.tick(completed=len(results))
        finally:
            progress.stop()
        return results


def test_model_entry_timed(litellm, entry: dict[str, str], *, timeout: float) -> ModelTestResult:
    start = time.perf_counter()
    try:
        test_model_entry_with_retry(litellm, entry, timeout=timeout)
    except Exception as exc:
        return ModelTestResult(entry=entry, ping_ms=elapsed_ms_since(start), error=exc)
    return ModelTestResult(entry=entry, ping_ms=elapsed_ms_since(start))


def elapsed_ms_since(start: float) -> float:
    return (time.perf_counter() - start) * 1000


def format_ping(ping_ms: float) -> str:
    return f"{ping_ms:.0f}ms"


def test_model_entry_with_retry(litellm, entry: dict[str, str], *, timeout: float) -> None:
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            test_model_entry(litellm, entry, timeout=timeout)
            return
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(1)
    if last_error is not None:
        raise last_error


def test_model_entry(litellm, entry: dict[str, str], *, timeout: float) -> None:
    mode = entry.get("mode", "chat")
    if mode == "responses":
        litellm.responses(
            model=entry["upstream"],
            input="Reply with exactly: OK",
            max_output_tokens=16,
            timeout=timeout,
        )
        return
    if mode == "embedding":
        litellm.embedding(model=entry["upstream"], input=["connectivity test"], timeout=timeout)
        return
    litellm.completion(
        model=entry["upstream"],
        messages=[{"role": "user", "content": "Reply with exactly: OK"}],
        max_tokens=8,
        timeout=timeout,
    )


def short_error(exc: Exception) -> str:
    message = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
    prefixes = [
        "litellm.BadRequestError: ",
        "litellm.AuthenticationError: ",
        "litellm.APIConnectionError: ",
    ]
    for prefix in prefixes:
        if message.startswith(prefix):
            return message.removeprefix(prefix)
    return message


def stop() -> None:
    pid_file = active_config_dir() / ".vela-llm.pid"
    pid = read_pid_file(pid_file)
    if pid is None:
        message("No running vela-llm instance was recorded.", level="info")
        return
    stop_existing_instance(pid_file)


def run_server_foreground(settings, pid_file: Path) -> None:
    write_pid_file(pid_file, os.getpid())
    try:
        uvicorn.run(
            "main:app",
            host=settings.host,
            port=settings.port,
            log_level=settings.log_level,
        )
    finally:
        cleanup_pid_file(pid_file)


def start_server_background(settings, config_dir: Path) -> subprocess.Popen:
    log_file = config_dir / "vela-llm.log"
    env = os.environ.copy()
    env["VELA_LLM_CONFIG_DIR"] = str(config_dir)
    env["LOCAL_API_KEY"] = settings.local_api_key
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "main:app",
        "--host",
        settings.host,
        "--port",
        str(settings.port),
        "--log-level",
        settings.log_level,
    ]
    creationflags = 0
    start_new_session = False
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        start_new_session = True
    with log_file.open("ab") as log_handle:
        return subprocess.Popen(
            command,
            cwd=config_dir,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            start_new_session=start_new_session,
        )


def wait_for_port(host: str, port: int, process: subprocess.Popen, timeout: float = 10.0) -> bool:
    probe_host = readiness_probe_host(host)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        if is_port_open(probe_host, port):
            return True
        time.sleep(0.1)
    return False


def readiness_probe_host(host: str) -> str:
    host = host.strip()
    if host in {"", "0.0.0.0"}:
        return "127.0.0.1"
    if host in {"::", "[::]"}:
        return "::1"
    return host


def is_port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


def handle_models(args: argparse.Namespace) -> None:
    from model_menu import print_model_snapshot
    from shell_app import ShellApp

    settings = get_settings()
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print_model_snapshot(settings)
        return
    ShellApp(settings, initial_page="models").run()


def ensure_copilot_authenticated() -> None:
    apply_github_copilot_oauth_patch()

    message("Checking GitHub Copilot OAuth credentials...", level="info")
    output(
        "If this machine is not authenticated yet, follow the GitHub device link "
        "and enter the code printed below.",
    )
    authenticator = github_copilot_authenticator()

    def device_login():
        info = authenticator._get_device_code()
        fields(
            "Authorize this device",
            [
                ("Open in browser", info["verification_uri"]),
                ("Device code", Text(info["user_code"], style="bold #70dfdf")),
            ],
        )
        message("Enter the code in your browser. Ctrl+C cancels.")
        with activity("Waiting for GitHub authorization…"):
            return authenticator._poll_for_access_token(info["device_code"])

    # Scope presentation to this explicit CLI login; keep upstream retries and storage.
    authenticator._login = device_login
    try:
        authenticator.get_api_key()
    except Exception as exc:
        raise RuntimeError(f"GitHub login failed: {short_error(exc)}") from exc
    message("GitHub Copilot OAuth credentials are ready.", level="success")


def require_copilot_login() -> None:
    apply_github_copilot_oauth_patch()
    authenticator = github_copilot_authenticator()
    if not has_valid_api_key(authenticator) and not has_access_token(authenticator):
        message("GitHub Copilot is not authenticated. Run `vl login` first.", level="warning")
        raise SystemExit(1)
    try:
        with activity("Checking GitHub Copilot credentials…"):
            authenticator.get_api_key()
    except Exception as exc:
        message(
            f"GitHub Copilot credentials are invalid or expired: {short_error(exc)}",
            level="error",
        )
        message("Run `vl login` to authenticate again.", level="info")
        raise SystemExit(1) from exc


def github_copilot_authenticator():
    apply_github_copilot_oauth_patch()

    from litellm.llms.github_copilot.authenticator import Authenticator

    return Authenticator()


def fetch_github_account(authenticator) -> dict[str, object]:
    access_token = authenticator.get_access_token()
    response = httpx.get(
        os.getenv("GITHUB_COPILOT_USER_URL", "https://api.github.com/user"),
        headers=github_user_headers(access_token),
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("GitHub user response was not an object.")
    return data


def has_access_token(authenticator) -> bool:
    try:
        return bool(Path(authenticator.access_token_file).read_text(encoding="utf-8").strip())
    except OSError:
        return False


def has_valid_api_key(authenticator) -> bool:
    try:
        data = json.loads(Path(authenticator.api_key_file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    token = data.get("token")
    expires_at = data.get("expires_at", 0)
    return (
        bool(token)
        and isinstance(expires_at, int | float)
        and expires_at > datetime.now().timestamp()
    )


def stop_existing_instance(pid_file: Path) -> bool:
    record = read_pid_record(pid_file)
    if record is None:
        return False
    pid = record.pid
    if not is_process_running(pid):
        cleanup_pid_file(pid_file)
        return False

    current_start_token = process_start_token(pid)
    if record.start_token is None or current_start_token != record.start_token:
        cleanup_pid_file(pid_file)
        message(
            f"Ignored stale vela-llm PID record for pid {pid}; no process was stopped.",
            level="warning",
        )
        return False

    message(f"Stopping previous vela-llm instance (pid {pid})...", level="info")
    terminate_process(pid)
    for _ in range(50):
        if not is_process_running(pid):
            cleanup_pid_file(pid_file)
            message("Previous vela-llm instance stopped.", level="success")
            return True
        time.sleep(0.1)

    msg = f"Previous vela-llm instance (pid {pid}) did not stop in time."
    raise RuntimeError(msg)


def wait_for_port_available(host: str, port: int) -> None:
    for _ in range(50):
        if is_port_available(host, port):
            return
        time.sleep(0.1)


def read_pid_file(pid_file: Path) -> int | None:
    record = read_pid_record(pid_file)
    return record.pid if record is not None else None


def read_pid_record(pid_file: Path) -> PidRecord | None:
    try:
        data = json.loads(pid_file.read_text(encoding="utf-8").strip())
        if isinstance(data, int):
            return PidRecord(pid=data, start_token=None)
        if not isinstance(data, dict):
            return None
        pid = data.get("pid")
        start_token = data.get("start_token")
        if not isinstance(pid, int) or (
            start_token is not None and not isinstance(start_token, str)
        ):
            return None
        return PidRecord(pid=pid, start_token=start_token)
    except (OSError, json.JSONDecodeError):
        return None


def write_pid_file(pid_file: Path, pid: int) -> None:
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    record = {"pid": pid, "start_token": process_start_token(pid)}
    pid_file.write_text(json.dumps(record, separators=(",", ":")), encoding="utf-8")


def process_start_token(pid: int) -> str | None:
    if pid <= 0:
        return None
    if sys.platform == "win32":
        return windows_process_start_token(pid)
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
    except OSError:
        return None
    return fields[21] if len(fields) > 21 else None


def windows_process_start_token(pid: int) -> str | None:
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    get_process_times = kernel32.GetProcessTimes
    get_process_times.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    get_process_times.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    process = open_process(process_query_limited_information, False, pid)
    if not process:
        return None
    try:
        created = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not get_process_times(
            process,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None
        return str((created.dwHighDateTime << 32) | created.dwLowDateTime)
    finally:
        close_handle(process)


def cleanup_pid_file(pid_file: Path) -> None:
    try:
        pid_file.unlink()
    except FileNotFoundError:
        return


def is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return is_windows_process_running(pid)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def is_windows_process_running(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    get_exit_code = kernel32.GetExitCodeProcess
    get_exit_code.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    get_exit_code.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    process = open_process(process_query_limited_information, False, pid)
    if not process:
        return False
    try:
        exit_code = wintypes.DWORD()
        if not get_exit_code(process, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        close_handle(process)


def terminate_process(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    os.kill(pid, signal.SIGTERM)


def ensure_port_available(host: str, port: int) -> None:
    if is_port_available(host, port):
        return
    msg = (
        f"Port {port} on {host} is already in use, but no restartable vela-llm "
        "instance was found. Stop the process using that port or change VELA_LLM_PORT."
    )
    raise RuntimeError(msg)


def is_port_available(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
    except OSError:
        return False
    return True


def print_startup_info(settings) -> None:
    root_base_url, openai_base_url = local_api_urls(settings)
    console = make_console()
    console.print()
    if os.environ.get("VELA_LLM_WORKSPACE_COMMAND") != "1":
        console.print(
            Text.from_ansi(
                render_startup_banner(
                    color=supports_color(), width=shutil.get_terminal_size().columns
                )
            )
        )
    heading("Start proxy", "vela-llm is starting...", console=console)
    fields(
        "Local endpoints",
        [
            ("OpenAI Base URL:", openai_base_url),
            ("Anthropic Base URL:", root_base_url),
            ("API Key:", mask_api_key(settings.local_api_key)),
            ("Default model:", settings.default_model),
        ],
        console=console,
    )


def refresh_model_cache(config_dir: Path) -> bool:
    from github_copilot_models import refresh_model_cache as refresh

    cache_path = config_dir / "models-cache.json"
    try:
        with activity("Refreshing model catalog…"):
            registry = refresh(cache_path)
    except Exception as exc:
        fallback = "cached metadata" if cache_path.exists() else "the configured default model"
        message(
            f"Model metadata: refresh failed ({short_error(exc)}); using {fallback}.",
            level="warning",
        )
        return False
    message(f"Model metadata:     refreshed {len(registry)} models", level="success")
    return True


def print_api_info(settings, *, show_key: bool = False) -> None:
    root_base_url, openai_base_url = local_api_urls(settings)
    api_key = settings.local_api_key if show_key else mask_api_key(settings.local_api_key)

    fields("OpenAI Compatible:", [("Base URL:", openai_base_url), ("API Key:", api_key)])
    fields("Anthropic Compatible:", [("Base URL:", root_base_url), ("API Key:", api_key)])
    if not show_key:
        output("Use vl api --show-key to reveal the complete local key.", style="vela.muted")


def mask_api_key(api_key: str) -> str:
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:3]}...{api_key[-4:]}"


def local_api_urls(settings) -> tuple[str, str]:
    host = local_api_display_host(settings.host)
    root_base_url = f"http://{host}:{settings.port}"
    return root_base_url, f"{root_base_url}/v1"


def local_api_display_host(host: str) -> str:
    host = host.strip()
    if host in {"", "0.0.0.0", "::", "[::]"}:
        return "127.0.0.1"
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def package_version() -> str:
    try:
        return version("vela-llm")
    except PackageNotFoundError:
        return "0.0.0+unknown"


def supports_color() -> bool:
    return sys.stdout.isatty() and not os.getenv("NO_COLOR")


if __name__ == "__main__":
    main(sys.argv[1:])
