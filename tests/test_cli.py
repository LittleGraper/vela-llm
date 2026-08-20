from __future__ import annotations

import threading
from contextlib import suppress

import cli
from settings import Settings, get_settings


def fake_dynamic_model_registry(_: Settings) -> list[dict[str, str]]:
    return [
        {"name": "gpt-4", "upstream": "github_copilot/gpt-4"},
        {"name": "gpt-5.5", "upstream": "github_copilot/gpt-5.5"},
        {"name": "codex", "upstream": "github_copilot/codex", "mode": "responses"},
        {"name": "embed", "upstream": "github_copilot/embed", "mode": "embedding"},
    ]


def write_config(tmp_path) -> None:
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "LOCAL_API_KEY=sk-local-test",
                "VELA_LLM_HOST=127.0.0.1",
                "VELA_LLM_PORT=4321",
                "VELA_LLM_MODELS_CONFIG=models.toml",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "models.toml").write_text(
        """
[models]
default = "gpt-4"

[[models.aliases]]
name = "gpt-4"
upstream = "github_copilot/gpt-4"

[[models.aliases]]
name = "gpt-5.5"
upstream = "github_copilot/gpt-5.5"

[[models.aliases]]
name = "codex"
upstream = "github_copilot/codex"
mode = "responses"

[[models.aliases]]
name = "embed"
upstream = "github_copilot/embed"
mode = "embedding"
""".strip()
        + "\n",
        encoding="utf-8",
    )


def prepare_config(monkeypatch, tmp_path) -> None:
    write_config(tmp_path)
    monkeypatch.setenv("VELA_LLM_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("VELA_LLM_DISABLE_DYNAMIC_MODELS", "1")
    monkeypatch.setenv("LOCAL_API_KEY", "sk-local-test")
    monkeypatch.setenv("VELA_LLM_HOST", "127.0.0.1")
    monkeypatch.setenv("VELA_LLM_PORT", "4321")
    monkeypatch.setenv("VELA_LLM_MODELS_CONFIG", "models.toml")
    monkeypatch.setattr(Settings, "dynamic_model_registry", fake_dynamic_model_registry)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()


def test_vl_without_args_prints_help(monkeypatch, tmp_path, capsys) -> None:
    prepare_config(monkeypatch, tmp_path)

    cli.main([])

    output = capsys.readouterr().out
    assert "Local GitHub Copilot proxy" in output
    assert "start" in output
    assert "models" in output


def test_vl_help_groups_duplicate_commands_and_hides_choices(monkeypatch, tmp_path, capsys) -> None:
    prepare_config(monkeypatch, tmp_path)

    cli.main([])

    output = capsys.readouterr().out
    assert "{help,version,login" not in output
    assert "stop||quit          Stop the running proxy instance." in output
    assert "model||models       Interactively change the default model." in output
    assert "    stop            Stop the running proxy instance." not in output
    assert "    quit            Stop the running proxy instance." not in output


def test_vl_version_flag_and_command(monkeypatch, tmp_path, capsys) -> None:
    prepare_config(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "package_version", lambda: "1.2.3")

    cli.main(["-v"])
    cli.main(["version"])

    assert capsys.readouterr().out.count("1.2.3") == 2


def test_vl_config_is_not_registered(monkeypatch, tmp_path, capsys) -> None:
    prepare_config(monkeypatch, tmp_path)

    try:
        cli.main(["config"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("removed subcommand should not be registered")

    output = capsys.readouterr()
    assert "invalid choice: 'config'" in output.err


def test_vl_login_runs_device_flow(monkeypatch, tmp_path) -> None:
    prepare_config(monkeypatch, tmp_path)
    calls: list[bool] = []

    monkeypatch.setattr(cli, "ensure_copilot_authenticated", lambda: calls.append(True))

    cli.main(["login"])

    assert calls == [True]


def test_vl_whoami_prints_current_github_account(monkeypatch, tmp_path, capsys) -> None:
    prepare_config(monkeypatch, tmp_path)
    access_token = tmp_path / "access-token"
    access_token.write_text("saved-token", encoding="utf-8")
    calls: list[dict[str, object]] = []

    class FakeAuthenticator:
        access_token_file = str(access_token)
        api_key_file = str(tmp_path / "api-key.json")

        def get_access_token(self) -> str:
            return "saved-token"

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"login": "octocat", "id": 12345}

    def fake_get(url: str, headers: dict[str, str], timeout: int) -> FakeResponse:
        calls.append({"url": url, "headers": headers, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr(cli, "github_copilot_authenticator", lambda: FakeAuthenticator())
    monkeypatch.setattr(cli.httpx, "get", fake_get)

    cli.main(["whoami"])

    output = capsys.readouterr().out
    assert "GitHub Login:       octocat" in output
    assert "GitHub User ID:     12345" in output
    assert calls == [
        {
            "url": "https://api.github.com/user",
            "headers": {
                "accept": "application/vnd.github+json",
                "authorization": "token saved-token",
                "user-agent": "GithubCopilot/1.155.0",
            },
            "timeout": 30,
        }
    ]


def test_vl_whoami_requires_login_when_access_token_is_missing(
    monkeypatch, tmp_path, capsys
) -> None:
    prepare_config(monkeypatch, tmp_path)

    class FakeAuthenticator:
        access_token_file = str(tmp_path / "missing-access-token")
        api_key_file = str(tmp_path / "api-key.json")

    monkeypatch.setattr(cli, "github_copilot_authenticator", lambda: FakeAuthenticator())

    try:
        cli.main(["whoami"])
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("missing credentials should require vl login")

    assert "Run `vl login` first" in capsys.readouterr().out


def test_vl_api_prints_openai_and_anthropic_settings(monkeypatch, tmp_path, capsys) -> None:
    prepare_config(monkeypatch, tmp_path)

    cli.main(["api"])

    output = capsys.readouterr().out
    assert "OpenAI Compatible:" in output
    assert "API Key:  sk-local-test" in output
    assert "Base URL: http://127.0.0.1:4321/v1" in output
    assert "Anthropic Compatible:" in output
    assert "Base URL: http://127.0.0.1:4321" in output


def test_vl_logout_removes_saved_copilot_credentials(monkeypatch, tmp_path, capsys) -> None:
    prepare_config(monkeypatch, tmp_path)
    access_token = tmp_path / "access-token"
    api_key = tmp_path / "api-key.json"
    access_token.write_text("token", encoding="utf-8")
    api_key.write_text('{"token":"api-key"}', encoding="utf-8")

    class FakeAuthenticator:
        access_token_file = str(access_token)
        api_key_file = str(api_key)

    monkeypatch.setattr(cli, "github_copilot_authenticator", lambda: FakeAuthenticator())

    cli.main(["logout"])

    assert not access_token.exists()
    assert not api_key.exists()
    assert "GitHub Copilot OAuth credentials removed." in capsys.readouterr().out


def test_require_copilot_login_prompts_for_login_when_credentials_are_missing(
    monkeypatch, tmp_path, capsys
) -> None:
    prepare_config(monkeypatch, tmp_path)

    class FakeAuthenticator:
        access_token_file = str(tmp_path / "missing-access-token")
        api_key_file = str(tmp_path / "missing-api-key.json")

        def get_api_key(self) -> str:
            raise AssertionError("device flow should not start from require_copilot_login")

    monkeypatch.setattr(cli, "github_copilot_authenticator", lambda: FakeAuthenticator())

    try:
        cli.require_copilot_login()
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("missing credentials should require vl login")

    assert "Run `vl login` first" in capsys.readouterr().out


def test_vl_update_installs_latest_github_release(monkeypatch, tmp_path, capsys) -> None:
    prepare_config(monkeypatch, tmp_path)
    calls: list[list[str]] = []
    requests: list[dict[str, object]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"tag_name": "v0.1.0"}

    def fake_get(url: str, headers: dict[str, str], timeout: int) -> FakeResponse:
        requests.append({"url": url, "headers": headers, "timeout": timeout})
        return FakeResponse()

    def fake_run(command: list[str], check: bool) -> None:
        calls.append(command)
        assert check is True

    monkeypatch.setattr(cli.httpx, "get", fake_get)
    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    cli.main(["update"])

    assert requests == [
        {
            "url": cli.GITHUB_LATEST_RELEASE_URL,
            "headers": {"accept": "application/vnd.github+json"},
            "timeout": 30,
        }
    ]
    assert calls == [["uv", "tool", "install", "--force", cli.release_install_url("v0.1.0")]]
    output = capsys.readouterr().out
    assert "Updating vl to v0.1.0 from GitHub Releases" in output
    assert "git+https://github.com/LittleGraper/vela-llm.git@v0.1.0" in output


def test_vl_update_explains_missing_github_release(monkeypatch, tmp_path, capsys) -> None:
    prepare_config(monkeypatch, tmp_path)

    class FakeResponse:
        status_code = 404

        def raise_for_status(self) -> None:
            request = cli.httpx.Request("GET", cli.GITHUB_LATEST_RELEASE_URL)
            response = cli.httpx.Response(404, request=request)
            raise cli.httpx.HTTPStatusError("not found", request=request, response=response)

    monkeypatch.setattr(cli.httpx, "get", lambda *_, **__: FakeResponse())
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *_, **__: (_ for _ in ()).throw(AssertionError("install should not run")),
    )

    try:
        cli.main(["update"])
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("missing GitHub release should exit non-zero")

    output = capsys.readouterr().out
    assert "No GitHub release was found for LittleGraper/vela-llm" in output
    assert "Create release v0.1.0 first" in output


def test_vl_update_prints_manual_command_for_windows_uv_tool(monkeypatch, tmp_path, capsys) -> None:
    prepare_config(monkeypatch, tmp_path)

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"tag_name": "v0.1.0"}

    monkeypatch.setattr(cli.httpx, "get", lambda *_, **__: FakeResponse())
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setattr(cli, "is_running_from_uv_tool", lambda: True)
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *_, **__: (_ for _ in ()).throw(AssertionError("install should not run")),
    )

    try:
        cli.main(["update"])
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("manual Windows update should exit non-zero")

    output = capsys.readouterr().out
    assert "uv tool install --force" in output
    assert "git+https://github.com/LittleGraper/vela-llm.git@v0.1.0" in output
    assert "Run the command above after this vl process exits" in output


class FakeProcess:
    pid = 98765

    def poll(self) -> None:
        return None


def test_vl_start_prints_urls_key_and_starts_background_server(
    monkeypatch, tmp_path, capsys
) -> None:
    prepare_config(monkeypatch, tmp_path)
    auth_calls: list[bool] = []
    pid_writes: list[tuple[str, int]] = []

    monkeypatch.setattr(cli, "require_copilot_login", lambda: auth_calls.append(True))
    monkeypatch.setattr(cli, "stop_existing_instance", lambda pid_file: None)
    monkeypatch.setattr(cli, "ensure_port_available", lambda host, port: None)
    monkeypatch.setattr(cli, "start_server_background", lambda settings, config_dir: FakeProcess())
    monkeypatch.setattr(cli, "wait_for_port", lambda host, port, process: True)
    monkeypatch.setattr(
        cli,
        "write_pid_file",
        lambda pid_file, pid: pid_writes.append((str(pid_file), pid)),
    )

    cli.main(["start"])

    output = capsys.readouterr().out
    assert auth_calls == [True]
    assert "OpenAI Base URL:    http://127.0.0.1:4321/v1" in output
    assert "Anthropic Base URL: http://127.0.0.1:4321" in output
    assert "API Key:            sk-local-test" in output
    assert "Default model:      gpt-4" in output
    assert "Proxy is running in the background (pid 98765)." in output
    assert f"Log file:           {tmp_path / 'vela-llm.log'}" in output
    assert pid_writes == [(str(tmp_path / ".vela-llm.pid"), 98765)]


def test_vl_start_foreground_runs_uvicorn_in_current_process(monkeypatch, tmp_path) -> None:
    prepare_config(monkeypatch, tmp_path)
    uvicorn_calls: list[dict[str, object]] = []
    pid_writes: list[tuple[str, int]] = []
    cleanup_calls: list[str] = []

    monkeypatch.setattr(cli, "require_copilot_login", lambda: None)
    monkeypatch.setattr(cli, "stop_existing_instance", lambda pid_file: None)
    monkeypatch.setattr(cli, "ensure_port_available", lambda host, port: None)
    monkeypatch.setattr(
        cli,
        "write_pid_file",
        lambda pid_file, pid: pid_writes.append((str(pid_file), pid)),
    )
    monkeypatch.setattr(
        cli, "cleanup_pid_file", lambda pid_file: cleanup_calls.append(str(pid_file))
    )

    def fake_run(app: str, host: str, port: int, log_level: str) -> None:
        uvicorn_calls.append({"app": app, "host": host, "port": port, "log_level": log_level})

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    cli.main(["start", "--foreground"])

    assert pid_writes == [(str(tmp_path / ".vela-llm.pid"), cli.os.getpid())]
    assert cleanup_calls == [str(tmp_path / ".vela-llm.pid")]
    assert uvicorn_calls == [
        {
            "app": "main:app",
            "host": "127.0.0.1",
            "port": 4321,
            "log_level": "info",
        }
    ]


def test_vl_start_can_skip_auth_check(monkeypatch, tmp_path) -> None:
    prepare_config(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cli,
        "require_copilot_login",
        lambda: (_ for _ in ()).throw(AssertionError("auth should be skipped")),
    )
    monkeypatch.setattr(cli.uvicorn, "run", lambda *_, **__: None)
    monkeypatch.setattr(cli, "stop_existing_instance", lambda pid_file: None)
    monkeypatch.setattr(cli, "ensure_port_available", lambda host, port: None)
    monkeypatch.setattr(cli, "start_server_background", lambda settings, config_dir: FakeProcess())
    monkeypatch.setattr(cli, "wait_for_port", lambda host, port, process: True)
    monkeypatch.setattr(cli, "write_pid_file", lambda pid_file, pid: None)

    cli.main(["start", "--skip-auth-check"])


def test_background_server_uses_resolved_api_key(monkeypatch, tmp_path) -> None:
    popen_calls: list[tuple[list[str], dict[str, object]]] = []
    settings = cli.argparse.Namespace(
        host="127.0.0.1",
        port=4321,
        log_level="info",
        local_api_key="sk-resolved",
    )
    monkeypatch.setattr(
        cli.subprocess,
        "Popen",
        lambda command, **kwargs: popen_calls.append((command, kwargs)) or FakeProcess(),
    )

    cli.start_server_background(settings, tmp_path)

    child_env = popen_calls[0][1]["env"]
    assert child_env["VELA_LLM_CONFIG_DIR"] == str(tmp_path)
    assert child_env["LOCAL_API_KEY"] == "sk-resolved"


def test_vl_stop_uses_pid_file(monkeypatch, tmp_path) -> None:
    prepare_config(monkeypatch, tmp_path)
    pid_file = tmp_path / ".vela-llm.pid"
    pid_file.write_text("12345", encoding="utf-8")
    stopped: list[str] = []

    monkeypatch.setattr(cli, "stop_existing_instance", lambda path: stopped.append(str(path)))

    cli.main(["stop"])
    cli.main(["quit"])

    assert stopped == [str(pid_file), str(pid_file)]


def test_vl_test_checks_configured_models(monkeypatch, tmp_path, capsys) -> None:
    prepare_config(monkeypatch, tmp_path)
    tested: list[str] = []

    class FakeLiteLLM:
        registered: list[dict[str, dict[str, str]]] = []

        @staticmethod
        def register_model(model_cost: dict[str, dict[str, str]]) -> None:
            FakeLiteLLM.registered.append(model_cost)

        @staticmethod
        def completion(
            model: str,
            messages: list[dict[str, str]],
            max_tokens: int,
            timeout: float,
        ) -> object:
            tested.append(f"chat:{model}")
            return object()

        @staticmethod
        def responses(model: str, input: str, max_output_tokens: int, timeout: float) -> object:
            tested.append(f"responses:{model}")
            return object()

        @staticmethod
        def embedding(model: str, input: list[str], timeout: float) -> object:
            tested.append(f"embedding:{model}")
            return object()

    monkeypatch.setattr(cli, "require_copilot_login", lambda: None)
    monkeypatch.setattr(cli, "supports_color", lambda: False)
    monkeypatch.setitem(cli.sys.modules, "litellm", FakeLiteLLM)

    cli.main(["test"])

    assert FakeLiteLLM.registered == [
        {
            "github_copilot/codex": {
                "litellm_provider": "github_copilot",
                "mode": "responses",
            },
            "github_copilot/embed": {
                "litellm_provider": "github_copilot",
                "mode": "embedding",
            },
        }
    ]
    assert set(tested) == {
        "chat:github_copilot/gpt-4",
        "chat:github_copilot/gpt-5.5",
        "responses:github_copilot/codex",
        "embedding:github_copilot/embed",
    }
    output = capsys.readouterr().out
    assert "TEST " not in output
    assert "STATUS\tMODEL  \tPING" in output
    assert "RUN   \tgpt-4" in output
    assert "RUN   \tgpt-5.5" in output
    assert "PASS  \tgpt-4" in output
    assert "github_copilot/gpt-4" not in output
    assert "ms" in output
    assert "All configured models are reachable." in output


def test_vl_test_runs_model_checks_in_parallel(monkeypatch, tmp_path) -> None:
    prepare_config(monkeypatch, tmp_path)
    second_chat_started = threading.Event()

    class FakeLiteLLM:
        @staticmethod
        def register_model(model_cost: dict[str, dict[str, str]]) -> None:
            return None

        @staticmethod
        def completion(
            model: str,
            messages: list[dict[str, str]],
            max_tokens: int,
            timeout: float,
        ) -> object:
            if model.endswith("gpt-5.5"):
                second_chat_started.set()
            if model.endswith("gpt-4") and not second_chat_started.wait(timeout=1):
                raise AssertionError("second model did not start before first model completed")
            return object()

        @staticmethod
        def responses(model: str, input: str, max_output_tokens: int, timeout: float) -> object:
            return object()

        @staticmethod
        def embedding(model: str, input: list[str], timeout: float) -> object:
            return object()

    monkeypatch.setattr(cli, "require_copilot_login", lambda: None)
    monkeypatch.setattr(cli, "supports_color", lambda: False)
    monkeypatch.setitem(cli.sys.modules, "litellm", FakeLiteLLM)

    cli.main(["test"])


def test_vl_test_can_filter_models(monkeypatch, tmp_path) -> None:
    prepare_config(monkeypatch, tmp_path)
    tested: list[str] = []

    class FakeLiteLLM:
        @staticmethod
        def register_model(model_cost: dict[str, dict[str, str]]) -> None:
            return None

        @staticmethod
        def completion(
            model: str,
            messages: list[dict[str, str]],
            max_tokens: int,
            timeout: float,
        ) -> object:
            tested.append(model)
            return object()

    monkeypatch.setattr(cli, "require_copilot_login", lambda: None)
    monkeypatch.setattr(cli, "supports_color", lambda: False)
    monkeypatch.setitem(cli.sys.modules, "litellm", FakeLiteLLM)

    cli.main(["test", "--model", "gpt-5.5"])

    assert tested == ["github_copilot/gpt-5.5"]


def test_vl_test_exits_nonzero_on_model_failure(monkeypatch, tmp_path, capsys) -> None:
    prepare_config(monkeypatch, tmp_path)

    class FakeLiteLLM:
        @staticmethod
        def register_model(model_cost: dict[str, dict[str, str]]) -> None:
            return None

        @staticmethod
        def completion(
            model: str,
            messages: list[dict[str, str]],
            max_tokens: int,
            timeout: float,
        ) -> object:
            if model.endswith("gpt-5.5"):
                raise RuntimeError("boom")
            return object()

        @staticmethod
        def responses(model: str, input: str, max_output_tokens: int, timeout: float) -> object:
            return object()

        @staticmethod
        def embedding(model: str, input: list[str], timeout: float) -> object:
            return object()

    monkeypatch.setattr(cli, "require_copilot_login", lambda: None)
    monkeypatch.setattr(cli, "supports_color", lambda: False)
    monkeypatch.setitem(cli.sys.modules, "litellm", FakeLiteLLM)

    try:
        cli.main(["test"])
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("vl test should exit non-zero when a model fails")

    output = capsys.readouterr().out
    assert "Fail  \tgpt-5.5" in output
    assert "boom" in output


def test_vl_test_retries_transient_model_failure(monkeypatch, tmp_path, capsys) -> None:
    prepare_config(monkeypatch, tmp_path)
    attempts: dict[str, int] = {}

    class FakeLiteLLM:
        @staticmethod
        def register_model(model_cost: dict[str, dict[str, str]]) -> None:
            return None

        @staticmethod
        def completion(
            model: str,
            messages: list[dict[str, str]],
            max_tokens: int,
            timeout: float,
        ) -> object:
            attempts[model] = attempts.get(model, 0) + 1
            if model.endswith("gpt-5.5") and attempts[model] == 1:
                raise RuntimeError("temporary upstream failure")
            return object()

        @staticmethod
        def responses(model: str, input: str, max_output_tokens: int, timeout: float) -> object:
            return object()

        @staticmethod
        def embedding(model: str, input: list[str], timeout: float) -> object:
            return object()

    monkeypatch.setattr(cli, "require_copilot_login", lambda: None)
    monkeypatch.setattr(cli, "supports_color", lambda: False)
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: None)
    monkeypatch.setitem(cli.sys.modules, "litellm", FakeLiteLLM)

    cli.main(["test"])

    assert attempts["github_copilot/gpt-5.5"] == 2
    assert "FAIL" not in capsys.readouterr().out


def test_vl_test_colorizes_ok_and_fail(monkeypatch, tmp_path, capsys) -> None:
    prepare_config(monkeypatch, tmp_path)

    class FakeLiteLLM:
        @staticmethod
        def register_model(model_cost: dict[str, dict[str, str]]) -> None:
            return None

        @staticmethod
        def completion(
            model: str,
            messages: list[dict[str, str]],
            max_tokens: int,
            timeout: float,
        ) -> object:
            if model.endswith("gpt-5.5"):
                raise RuntimeError("boom")
            return object()

    monkeypatch.setattr(cli, "require_copilot_login", lambda: None)
    monkeypatch.setattr(cli, "supports_color", lambda: True)
    monkeypatch.setitem(cli.sys.modules, "litellm", FakeLiteLLM)

    with suppress(SystemExit):
        cli.main(["test"])

    output = capsys.readouterr().out
    assert "\033[32mPASS\033[0m" in output
    assert "\033[31mFail\033[0m" in output


def test_vl_test_handles_keyboard_interrupt_cleanly(monkeypatch, tmp_path, capsys) -> None:
    prepare_config(monkeypatch, tmp_path)

    class FakeLiteLLM:
        @staticmethod
        def register_model(model_cost: dict[str, dict[str, str]]) -> None:
            return None

        @staticmethod
        def completion(
            model: str,
            messages: list[dict[str, str]],
            max_tokens: int,
            timeout: float,
        ) -> object:
            raise KeyboardInterrupt

    monkeypatch.setattr(cli, "require_copilot_login", lambda: None)
    monkeypatch.setitem(cli.sys.modules, "litellm", FakeLiteLLM)

    try:
        cli.main(["test"])
    except SystemExit as exc:
        assert exc.code == 130
    else:
        raise AssertionError("KeyboardInterrupt should exit with code 130")

    assert "Cancelled." in capsys.readouterr().out


def test_vl_model_non_interactive_prints_choices(monkeypatch, tmp_path, capsys) -> None:
    prepare_config(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "supports_color", lambda: False)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)

    cli.main(["models"])

    output = capsys.readouterr().out
    assert "Select default model  [Up/Down] move  [Enter] save  [q] cancel" in output
    assert "> gpt-4  (default)" in output
    assert "  gpt-5.5" in output
    assert "  codex  (responses)" in output
    assert "CUR" not in output
    assert "github_copilot/gpt-4" not in output


def test_vl_model_does_not_accept_list_subcommand(monkeypatch, tmp_path) -> None:
    prepare_config(monkeypatch, tmp_path)

    try:
        cli.main(["models", "list"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("models list should not be accepted")


def test_vl_model_interactive_uses_arrow_enter(monkeypatch, tmp_path) -> None:
    prepare_config(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "supports_color", lambda: False)
    keys = iter(["down", "enter"])
    monkeypatch.setattr(cli, "read_key", lambda: next(keys))

    cli.main(["model"])

    assert 'default = "gpt-5.5"' in (tmp_path / "models.toml").read_text(encoding="utf-8")


def test_model_selection_redraws_only_choice_rows(monkeypatch, capsys) -> None:
    registry = [
        {"name": "gpt-4", "mode": "chat"},
        {"name": "codex", "mode": "responses"},
    ]
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "supports_color", lambda: False)
    view = cli.ModelSelectionView(registry, "gpt-4")

    view.render(0)
    view.render(1)

    output = capsys.readouterr().out
    assert output.count("Select default model") == 1
    assert "\033[2F" in output
    assert "\033[4F" not in output


def test_stop_existing_instance_terminates_recorded_process(monkeypatch, tmp_path) -> None:
    pid_file = tmp_path / ".vela-llm.pid"
    pid_file.write_text("12345", encoding="utf-8")

    states = iter([True, False])
    terminated: list[int] = []

    monkeypatch.setattr(cli, "is_process_running", lambda pid: next(states))
    monkeypatch.setattr(cli, "terminate_process", lambda pid: terminated.append(pid))
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: None)

    stopped = cli.stop_existing_instance(pid_file)

    assert stopped
    assert terminated == [12345]
    assert not pid_file.exists()


def test_is_process_running_uses_windows_api(monkeypatch) -> None:
    checked: list[int] = []
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setattr(
        cli,
        "is_windows_process_running",
        lambda pid: checked.append(pid) or True,
    )

    assert cli.is_process_running(12345)
    assert checked == [12345]


def test_terminate_process_kills_windows_process_tree(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)),
    )

    cli.terminate_process(12345)

    assert calls[0][0] == ["taskkill", "/PID", "12345", "/T", "/F"]
    assert calls[0][1]["check"] is True


def test_readiness_probe_host_normalizes_wildcard_bind_addresses() -> None:
    assert cli.readiness_probe_host("") == "127.0.0.1"
    assert cli.readiness_probe_host("0.0.0.0") == "127.0.0.1"
    assert cli.readiness_probe_host(" :: ") == "::1"
    assert cli.readiness_probe_host("[::]") == "::1"
    assert cli.readiness_probe_host("127.0.0.1") == "127.0.0.1"
    assert cli.readiness_probe_host("localhost") == "localhost"


def test_wait_for_port_uses_connectable_host_for_wildcard_bind(monkeypatch) -> None:
    checked: list[tuple[str, int]] = []

    def fake_is_port_open(host: str, port: int) -> bool:
        checked.append((host, port))
        return True

    monkeypatch.setattr(cli, "is_port_open", fake_is_port_open)

    assert cli.wait_for_port("0.0.0.0", 4000, FakeProcess())
    assert checked == [("127.0.0.1", 4000)]


def test_wait_for_port_available_retries(monkeypatch) -> None:
    availability = iter([False, False, True])
    sleeps: list[float] = []
    monkeypatch.setattr(cli, "is_port_available", lambda host, port: next(availability))
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: sleeps.append(seconds))

    cli.wait_for_port_available("127.0.0.1", 4000)

    assert sleeps == [0.1, 0.1]


def test_ensure_port_available_fails_for_unmanaged_process(monkeypatch) -> None:
    monkeypatch.setattr(cli, "is_port_available", lambda host, port: False)

    try:
        cli.ensure_port_available("127.0.0.1", 4000)
    except RuntimeError as exc:
        assert "no restartable Copilot Proxy instance was found" in str(exc)
    else:
        raise AssertionError("occupied unmanaged port should fail")
