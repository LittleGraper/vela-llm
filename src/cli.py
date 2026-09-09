from __future__ import annotations

import argparse
import json
import os
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

from config_files import active_config_dir, ensure_config_files, resolve_config_path
from github_copilot_patch import apply_github_copilot_oauth_patch
from litellm_registry import register_litellm_model_metadata
from model_store import write_default_model
from settings import get_settings

PYPI_PACKAGE = "vela-llm"
MODEL_TEST_PROGRESS_INTERVAL = 0.1
MODEL_TEST_SPINNER_FRAMES = ("-", "\\", "|", "/")
MODEL_TEST_STATUS_WIDTH = 6
MODEL_TEST_MODEL_WIDTH = len("MODEL")
MODEL_TEST_COLUMN_GAP = "\t"


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
    def __init__(self, registry: list[dict[str, str]]) -> None:
        self.registry = registry
        self.is_interactive = sys.stdout.isatty()
        self.frame_index = 0
        self.rendered_lines = 0
        self.completed = 0
        self.model_width = max(
            MODEL_TEST_MODEL_WIDTH,
            *(len(format_model_test_name(entry)) for entry in registry),
        )
        self.rows = [
            format_model_test_pending(entry, model_width=self.model_width) for entry in registry
        ]
        self.index_by_name = {entry["name"]: index for index, entry in enumerate(registry)}

    def start(self) -> None:
        self._render()

    def tick(self, *, completed: int) -> None:
        if not self.is_interactive:
            return
        self.completed = completed
        self._render()

    def finish(self, result: ModelTestResult) -> None:
        self.completed += 1
        self.rows[self.index_by_name[result.entry["name"]]] = format_model_test_result(
            result,
            model_width=self.model_width,
        )
        if self.is_interactive:
            self._render()

    def stop(self) -> None:
        if not self.is_interactive and self.completed:
            self._render()

    def _render(self) -> None:
        lines = self._lines()
        if self.is_interactive:
            self._rewrite_lines(lines)
            return
        for line in lines:
            print(line, flush=True)

    def _lines(self) -> list[str]:
        frame = MODEL_TEST_SPINNER_FRAMES[self.frame_index % len(MODEL_TEST_SPINNER_FRAMES)]
        self.frame_index += 1
        header = f"{frame} Testing models {self.completed}/{len(self.registry)} complete"
        return [header, format_model_test_header(model_width=self.model_width), *self.rows]

    def _rewrite_lines(self, lines: list[str]) -> None:
        if not self.rendered_lines:
            for line in lines:
                print(line)
            self.rendered_lines = len(lines)
            return
        print(f"\033[{self.rendered_lines}F", end="")
        for line in lines:
            print(f"\r{line}\033[K")
        self.rendered_lines = len(lines)


class ModelSelectionView:
    def __init__(self, registry: list[dict[str, str]], default_model: str) -> None:
        self.registry = registry
        self.default_model = default_model
        self.is_interactive = sys.stdin.isatty()
        self.rendered_lines = 0

    def render(self, selected: int) -> None:
        rows = self._rows(selected)
        if self.is_interactive:
            self._rewrite_rows(rows)
            return
        for line in [self._title(), "", *rows]:
            print(line, flush=True)

    def finish(self) -> None:
        self.rendered_lines = 0

    def _title(self) -> str:
        return "Select default model  [Up/Down] move  [Enter] save  [q] cancel"

    def _rows(self, selected: int) -> list[str]:
        return [
            format_model_choice_row(
                entry,
                is_selected=index == selected,
                is_default=entry["name"] == self.default_model,
            )
            for index, entry in enumerate(self.registry)
        ]

    def _rewrite_rows(self, rows: list[str]) -> None:
        if not self.rendered_lines:
            print(self._title())
            print()
            for line in rows:
                print(line)
            self.rendered_lines = len(rows)
            return
        print(f"\033[{self.rendered_lines}F", end="")
        for line in rows:
            print(f"\r{line}\033[K")
        self.rendered_lines = len(rows)


class VlHelpFormatter(argparse.HelpFormatter):
    def _format_action(self, action: argparse.Action) -> str:
        if isinstance(action, argparse._SubParsersAction):
            choices = grouped_subparser_choices(action)
            if not choices:
                return ""
            action_width = max(18, max(len(command) for command, _ in choices))
            self._indent()
            try:
                return "".join(
                    self._format_subparser_choice(command, help_text, action_width)
                    for command, help_text in choices
                )
            finally:
                self._dedent()
        return super()._format_action(action)

    def _format_subparser_choice(
        self, command: str, help_text: str | None, action_width: int
    ) -> str:
        line = f"{' ' * self._current_indent}{command:<{action_width}}"
        if not help_text:
            return f"{line}\n"
        return f"{line}  {help_text}\n"


def grouped_subparser_choices(
    action: argparse._SubParsersAction,
) -> list[tuple[str, str | None]]:
    grouped: list[tuple[list[str], str | None]] = []
    for choice_action in action._choices_actions:
        for commands, help_text in grouped:
            if choice_action.help == help_text:
                commands.append(choice_action.dest)
                break
        else:
            grouped.append(([choice_action.dest], choice_action.help))
    return [("||".join(commands), help_text) for commands, help_text in grouped]


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.version:
            print(package_version())
            return

        command = args.command or "help"
        if command in {"api", "start", "model", "models", "test"}:
            ensure_config_files()
        if command == "help":
            parser.print_help()
        elif command == "version":
            print(package_version())
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
        print("\nCancelled.", flush=True)
        raise SystemExit(130) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="vela-llm provides local access to GitHub Copilot models.",
        formatter_class=VlHelpFormatter,
        usage="%(prog)s [-h] [-v] [command] ...",
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
        subparsers.add_parser(name, help="Interactively change the default model.")

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

    if not refresh_model_cache(config_dir):
        os.environ["VELA_LLM_DISABLE_DYNAMIC_MODELS"] = "1"
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
    if wait_for_port(settings.host, settings.port, process):
        print(f"Proxy is running in the background (pid {process.pid}).", flush=True)
    else:
        print(
            f"Proxy was started in the background (pid {process.pid}), "
            "but the port did not become ready yet.",
            flush=True,
        )
    print(f"Log file:           {log_file}", flush=True)


def update() -> None:
    command = ["uv", "tool", "install", "--force", PYPI_PACKAGE]
    print("Updating vela-llm to the latest stable PyPI release...", flush=True)
    print(" ".join(command), flush=True)
    if should_print_manual_update_command():
        print(
            "Windows cannot replace a running uv tool environment. "
            "Run the command above after this vl process exits.",
            flush=True,
        )
        raise SystemExit(1)
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"Update command failed with exit code {exc.returncode}.", flush=True)
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
        print("GitHub Copilot OAuth credentials removed.", flush=True)
    else:
        print("No saved GitHub Copilot OAuth credentials found.", flush=True)


def whoami() -> None:
    authenticator = github_copilot_authenticator()
    if not has_access_token(authenticator):
        print("GitHub Copilot is not authenticated. Run `vl login` first.", flush=True)
        raise SystemExit(1)
    try:
        account = fetch_github_account(authenticator)
    except Exception as exc:
        print(f"Unable to read GitHub account: {short_error(exc)}", flush=True)
        print("Run `vl login` to authenticate again.", flush=True)
        raise SystemExit(1) from exc

    login = account.get("login")
    user_id = account.get("id")
    if login:
        print(f"GitHub Login:       {login}", flush=True)
    if user_id is not None:
        print(f"GitHub User ID:     {user_id}", flush=True)
    if not login and user_id is None:
        raise RuntimeError("GitHub user response did not include login or id.")


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

    print("Testing configured models...", flush=True)
    results = test_model_entries_parallel(litellm, registry, timeout=args.timeout)
    failures = sum(1 for result in results if result.error is not None)
    if failures:
        raise SystemExit(1)
    print("All configured models are reachable.", flush=True)


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


def format_model_test_header(*, model_width: int = MODEL_TEST_MODEL_WIDTH) -> str:
    return format_model_test_row("STATUS", "MODEL", "PING", model_width=model_width)


def format_model_test_pending(
    entry: dict[str, str], *, model_width: int = MODEL_TEST_MODEL_WIDTH
) -> str:
    return format_model_test_row(
        "RUN",
        format_model_test_name(entry),
        "...",
        model_width=model_width,
    )


def format_model_test_result(
    result: ModelTestResult,
    *,
    colorize: bool = True,
    model_width: int = MODEL_TEST_MODEL_WIDTH,
) -> str:
    entry = result.entry
    ping = format_ping(result.ping_ms)
    if result.error is None:
        status = green("PASS") if colorize else "PASS"
        return format_model_test_row(
            status,
            format_model_test_name(entry),
            ping,
            model_width=model_width,
        )
    status = red("Fail") if colorize else "Fail"
    return (
        format_model_test_row(status, format_model_test_name(entry), ping, model_width=model_width)
        + f"  {short_error(result.error)}"
    )


def format_model_test_name(entry: dict[str, str]) -> str:
    return entry["name"]


def format_model_test_row(
    status: str, model: str, ping: str, *, model_width: int = MODEL_TEST_MODEL_WIDTH
) -> str:
    return (
        f"{pad_visible(status, MODEL_TEST_STATUS_WIDTH)}{MODEL_TEST_COLUMN_GAP}"
        f"{pad_visible(model, model_width)}{MODEL_TEST_COLUMN_GAP}"
        f"{ping}"
    )


def pad_visible(text: str, width: int) -> str:
    return f"{text}{' ' * max(0, width - visible_length(text))}"


def visible_length(text: str) -> int:
    plain_text = text.replace("\033[32m", "").replace("\033[31m", "").replace("\033[0m", "")
    return len(plain_text)


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
        print("No running vela-llm instance was recorded.")
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
    select_model_interactively()


def set_default_model(name: str) -> None:
    settings = get_settings()
    models_path = resolve_config_path(settings.models_config)
    registry = settings.model_registry()
    names = {entry["name"] for entry in registry}
    if name not in names:
        msg = f"Unknown model '{name}'. Run `vl models` to see configured models."
        raise RuntimeError(msg)
    write_default_model(models_path, name)
    get_settings.cache_clear()
    print(f"Default model set to {green(name)}.")


def select_model_interactively() -> None:
    settings = get_settings()
    registry = settings.model_registry()
    if not registry:
        raise RuntimeError("No models configured.")

    selected = next(
        (index for index, entry in enumerate(registry) if entry["name"] == settings.default_model),
        0,
    )
    if not sys.stdin.isatty():
        print_model_choices(registry, settings.default_model)
        return

    view = ModelSelectionView(registry, settings.default_model)
    while True:
        view.render(selected)
        key = read_key()
        if key == "up":
            selected = (selected - 1) % len(registry)
        elif key == "down":
            selected = (selected + 1) % len(registry)
        elif key == "enter":
            view.finish()
            set_default_model(registry[selected]["name"])
            return
        elif key in {"q", "ctrl_c"}:
            view.finish()
            print("Cancelled.")
            return


def print_model_choices(registry: list[dict[str, str]], default_model: str) -> None:
    selected = next(
        (index for index, entry in enumerate(registry) if entry["name"] == default_model),
        0,
    )
    ModelSelectionView(registry, default_model).render(selected)


def ensure_copilot_authenticated() -> None:
    apply_github_copilot_oauth_patch()

    print("Checking GitHub Copilot OAuth credentials...", flush=True)
    print(
        "If this machine is not authenticated yet, follow the GitHub device link "
        "and enter the code printed below.",
        flush=True,
    )
    github_copilot_authenticator().get_api_key()
    print("GitHub Copilot OAuth credentials are ready.", flush=True)


def require_copilot_login() -> None:
    apply_github_copilot_oauth_patch()
    authenticator = github_copilot_authenticator()
    if not has_valid_api_key(authenticator) and not has_access_token(authenticator):
        print("GitHub Copilot is not authenticated. Run `vl login` first.", flush=True)
        raise SystemExit(1)
    try:
        authenticator.get_api_key()
    except Exception as exc:
        print(
            f"GitHub Copilot credentials are invalid or expired: {short_error(exc)}",
            flush=True,
        )
        print("Run `vl login` to authenticate again.", flush=True)
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


def github_user_headers(access_token: str) -> dict[str, str]:
    return {
        "accept": "application/vnd.github+json",
        "authorization": f"token {access_token}",
        "user-agent": "GithubCopilot/1.155.0",
    }


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
        print(
            f"Ignored stale vela-llm PID record for pid {pid}; no process was stopped.",
            flush=True,
        )
        return False

    print(f"Stopping previous vela-llm instance (pid {pid})...", flush=True)
    terminate_process(pid)
    for _ in range(50):
        if not is_process_running(pid):
            cleanup_pid_file(pid_file)
            print("Previous vela-llm instance stopped.", flush=True)
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

    print("", flush=True)
    print("vela-llm is starting...", flush=True)
    print(f"OpenAI Base URL:    {openai_base_url}", flush=True)
    print(f"Anthropic Base URL: {root_base_url}", flush=True)
    print(f"API Key:            {mask_api_key(settings.local_api_key)}", flush=True)
    print(f"Default model:      {settings.default_model}", flush=True)
    print("", flush=True)


def refresh_model_cache(config_dir: Path) -> bool:
    from github_copilot_models import refresh_model_cache as refresh

    cache_path = config_dir / "models-cache.json"
    try:
        registry = refresh(cache_path)
    except Exception as exc:
        fallback = "cached metadata" if cache_path.exists() else "the configured default model"
        print(
            f"Model metadata:     refresh failed ({short_error(exc)}); using {fallback}.",
            flush=True,
        )
        return False
    print(f"Model metadata:     refreshed {len(registry)} models", flush=True)
    return True


def print_api_info(settings, *, show_key: bool = False) -> None:
    root_base_url, openai_base_url = local_api_urls(settings)
    api_key = settings.local_api_key if show_key else mask_api_key(settings.local_api_key)

    print("OpenAI Compatible:", flush=True)
    print(f"API Key:  {api_key}", flush=True)
    print(f"Base URL: {openai_base_url}", flush=True)
    print("", flush=True)
    print("Anthropic Compatible:", flush=True)
    print(f"API Key:  {api_key}", flush=True)
    print(f"Base URL: {root_base_url}", flush=True)


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


def format_model_choice_row(entry: dict[str, str], *, is_selected: bool, is_default: bool) -> str:
    current = green(">") if is_selected else " "
    model = entry["name"]
    if is_selected:
        model = green(model)
    tags = []
    if is_default:
        tags.append(green("default"))
    if entry.get("mode") == "responses":
        tags.append("responses")
    suffix = f"  ({', '.join(tags)})" if tags else ""
    return f"{current} {model}{suffix}"


def green(text: str) -> str:
    if not supports_color():
        return text
    return f"\033[32m{text}\033[0m"


def red(text: str) -> str:
    if not supports_color():
        return text
    return f"\033[31m{text}\033[0m"


def package_version() -> str:
    try:
        return version("vela-llm")
    except PackageNotFoundError:
        return "0.0.0+unknown"


def supports_color() -> bool:
    return sys.stdout.isatty() and not os.getenv("NO_COLOR")


def clear_screen() -> None:
    if supports_color():
        print("\033[2J\033[H", end="")
    else:
        print("\n" * 2)


def read_key() -> str:
    if sys.platform == "win32":
        import msvcrt

        char = msvcrt.getch()
        if char in {b"\x00", b"\xe0"}:
            second = msvcrt.getch()
            if second == b"H":
                return "up"
            if second == b"P":
                return "down"
        if char == b"\r":
            return "enter"
        if char == b"\x03":
            return "ctrl_c"
        return char.decode(errors="ignore").lower()

    import termios
    import tty

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        char = sys.stdin.read(1)
        if char == "\x1b":
            sequence = sys.stdin.read(2)
            if sequence == "[A":
                return "up"
            if sequence == "[B":
                return "down"
        if char in {"\r", "\n"}:
            return "enter"
        if char == "\x03":
            return "ctrl_c"
        return char.lower()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


if __name__ == "__main__":
    main(sys.argv[1:])
