from __future__ import annotations

from io import StringIO

import pytest
from rich.cells import cell_len
from rich.console import Console
from test_cli import prepare_config

import cli
import cli_ui
from workspace_output import CommandOutput


@pytest.mark.parametrize("chunk_size", [1, 37, 4096])
def test_model_progress_replaces_table_and_preserves_messages(monkeypatch, capsys, chunk_size):
    from rich.text import Text

    monkeypatch.setenv("VELA_LLM_WORKSPACE_COMMAND", "1")
    entries = [{"name": "model-a"}, {"name": "model-b"}]
    progress = cli.ModelTestProgress(entries)
    output = CommandOutput()

    def drain():
        raw = capsys.readouterr().out
        for offset in range(0, len(raw), chunk_size):
            output.feed(raw[offset : offset + chunk_size])
        return Text.from_ansi(output.text).plain

    cli_ui.message("Testing configured models...")
    progress.start()
    assert "0/2 complete" in drain()
    progress.finish(cli.ModelTestResult(entries[0], 123))
    cli_ui.message("Diagnostic between results", level="warning")
    partial = drain()
    assert "1/2 complete" in partial and partial.count("STATUS") == 1
    assert "PASS" in partial and "RUN" in partial
    progress.finish(cli.ModelTestResult(entries[1], 456, RuntimeError("连接失败")))
    progress.stop()
    cli_ui.message("1/2 model checks failed.", level="error")
    result = drain()
    assert result.count("STATUS") == 1 and "2/2 complete" in result
    assert "0/2 complete" not in result and "1/2 complete" not in result
    assert "RUN" not in result and "Fail" in result and "连接失败" in result
    assert "Testing configured models" in result and "Diagnostic between results" in result
    assert "1/2 model checks failed" in result
    assert "\x1e" not in output.text and "\x1b[" in output.text


def test_workspace_pipe_keeps_rich_colors_without_cursor_controls(monkeypatch):
    monkeypatch.setenv("VELA_LLM_WORKSPACE_COMMAND", "1")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")
    stream = StringIO()
    console = cli_ui.make_console(file=stream)
    cli_ui.fields("Account", [("GitHub Login", "example-user")], console=console)
    monkeypatch.setattr(cli_ui, "make_console", lambda **kwargs: console)
    with cli_ui.activity("Waiting…"):
        cli_ui.message("Ready", level="success")
    output = stream.getvalue()
    assert "\x1b[" in output
    from rich.text import Text

    decoded = Text.from_ansi(output)
    assert "example-user" in decoded.plain and "Ready" in decoded.plain
    assert "Waiting" not in decoded.plain
    assert "\x1b[?" not in output  # No spinner cursor hide/show sequences.
    assert "\x1b[2K" not in output


@pytest.mark.parametrize("command", ["start", "test", "api", "models", "login", "update"])
def test_subcommand_help_keeps_argparse_options_without_side_effects(
    command, monkeypatch, tmp_path, capsys
):
    config = tmp_path / "uncreated"
    monkeypatch.setenv("VELA_LLM_CONFIG_DIR", str(config))
    with pytest.raises(SystemExit) as result:
        cli.main([command, "--help"])
    assert result.value.code == 0
    output = capsys.readouterr().out
    assert f"VELA / vl {command}" in output
    assert "--help" in output
    for option in cli.build_parser()._subparsers._group_actions[0].choices[command]._actions:
        for flag in option.option_strings:
            assert flag in output
    assert "\033" not in output
    assert not config.exists()


def test_login_device_card_keeps_polling_and_never_prints_tokens(monkeypatch, tmp_path, capsys):
    prepare_config(monkeypatch, tmp_path)
    polled = []

    class Authenticator:
        def _get_device_code(self):
            return {
                "device_code": "private-device-token",
                "user_code": "ABCD-1234",
                "verification_uri": "https://github.com/login/device",
            }

        def _poll_for_access_token(self, code):
            polled.append(code)
            return "private-access-token"

        def get_api_key(self):
            assert self._login() == "private-access-token"
            return "private-api-key"

    monkeypatch.setattr(cli, "github_copilot_authenticator", Authenticator)
    monkeypatch.setattr(cli, "apply_github_copilot_oauth_patch", lambda: None)
    cli.main(["login"])
    output = capsys.readouterr().out
    assert "Authorize this device" in output
    assert "https://github.com/login/device" in output and "ABCD-1234" in output
    assert polled == ["private-device-token"]
    assert "private-" not in output
    assert "credentials are ready" in output


def test_login_with_saved_key_does_not_request_device_code(monkeypatch, tmp_path, capsys):
    prepare_config(monkeypatch, tmp_path)

    class Authenticator:
        def get_api_key(self):
            return "private-api-key"

        def _get_device_code(self):
            raise AssertionError("Must not initiate another login")

    monkeypatch.setattr(cli, "github_copilot_authenticator", Authenticator)
    monkeypatch.setattr(cli, "apply_github_copilot_oauth_patch", lambda: None)
    cli.main(["login"])
    output = capsys.readouterr().out
    assert "credentials are ready" in output
    assert "Authorize this device" not in output
    assert "private-api-key" not in output


def test_expected_command_failure_is_readable_on_stderr(monkeypatch, tmp_path, capsys):
    prepare_config(monkeypatch, tmp_path)

    def fail():
        raise OSError("cannot open [red]config[/red]")

    monkeypatch.setattr(cli, "stop", fail)
    with pytest.raises(SystemExit) as result:
        cli.main(["stop"])
    assert result.value.code == 1
    output = capsys.readouterr()
    assert "ERROR" in output.err and "[red]config[/red]" in output.err
    assert "Traceback" not in output.err
    assert "\033" not in output.err


@pytest.mark.parametrize("width", [48, 80, 110])
def test_help_and_api_cards_fit_narrow_output_without_losing_values(width, monkeypatch, tmp_path):
    prepare_config(monkeypatch, tmp_path)
    stream = StringIO()
    console = Console(file=stream, width=width, theme=cli_ui.THEME, color_system=None)
    monkeypatch.setattr(cli_ui, "make_console", lambda **kwargs: console)
    cli.main(["help"])
    cli.main(["api"])
    output = stream.getvalue()
    assert all(cell_len(line) <= width for line in output.splitlines())
    assert "sk-local-test" not in output and "sk-...test" in output
    assert "http://127.0.0.1:4321/v1" in output
    assert "start" in output and "model / models" in output
    assert "\033" not in output
