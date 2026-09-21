from __future__ import annotations

import ctypes
import json
import os
import socket
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path
from uuid import uuid4

import pytest

from process_lifetime import WORKSPACE_JOB_ENV, WorkspaceJobs, command_python, join_workspace_job

# Run actual Windows processes: the owner represents Textual, the command represents
# /start, and the listening grandchild represents the detached proxy.
PROCESS_SCRIPT = """
import json, os, socket, subprocess, sys, time
from pathlib import Path
from process_lifetime import WORKSPACE_JOB_ENV, WorkspaceJob, join_workspace_job

mode, root, command_lifetime = sys.argv[1:]
root = Path(root)
if mode == 'owner':
    job = WorkspaceJob()
    env = os.environ.copy()
    env[WORKSPACE_JOB_ENV] = job.name
    child = subprocess.Popen(
        [sys.executable, __file__, 'command', str(root), command_lifetime],
        env=env, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    (root / 'command.json').write_text(json.dumps({'pid': child.pid}))
    if command_lifetime == 'exit':
        child.wait(timeout=10)
    action = sys.stdin.readline().strip()
    if action == 'release':
        job.release()
    else:
        job.close()
elif mode == 'command':
    join_workspace_job()
    subprocess.Popen(
        [sys.executable, __file__, 'proxy', str(root), command_lifetime],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    if command_lifetime == 'running':
        time.sleep(60)
else:
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    sock.listen()
    (root / 'proxy.json').write_text(json.dumps({
        'pid': os.getpid(), 'port': sock.getsockname()[1],
        'job_env': os.environ.get(WORKSPACE_JOB_ENV),
    }))
    time.sleep(60)
"""


def wait_record(path):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            return json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(0.02)
    pytest.fail(f"Process did not become ready: {path.name}")


class ProcessHandle:
    """Keep an identity-stable handle for assertions and cleanup (no PID reuse)."""

    def __init__(self, pid):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, args, result in (
            ("OpenProcess", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            ("WaitForSingleObject", [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            ("TerminateProcess", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
        ):
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = args, result
        self.handle = self.kernel.OpenProcess(0x100001, False, pid)
        assert self.handle, ctypes.WinError(ctypes.get_last_error())

    def exited(self, timeout_ms=0):
        result = self.kernel.WaitForSingleObject(self.handle, timeout_ms)
        assert result in (0, 258)
        return result == 0

    def cleanup(self):
        if not self.exited():
            self.kernel.TerminateProcess(self.handle, 1)
            assert self.exited(5000)
        self.kernel.CloseHandle(self.handle)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Objects")
@pytest.mark.parametrize("action", ["close", "kill", "release"])
@pytest.mark.parametrize("command_lifetime", ["running", "exit"])
def test_workspace_owns_only_its_descendants(tmp_path, action, command_lifetime):
    script = tmp_path / "processes.py"
    script.write_text(PROCESS_SCRIPT)
    env = os.environ.copy()
    source = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (source, env.get("PYTHONPATH"))))
    handles = []
    # Windows venv python.exe is a redirector: target the actual interpreter so
    # kill() simulates the workspace dying, not just its waiting launcher.
    interpreter = sys._base_executable
    unrelated = subprocess.Popen([interpreter, "-c", "import time; time.sleep(60)"])
    owner = subprocess.Popen(
        [interpreter, str(script), "owner", str(tmp_path), command_lifetime],
        stdin=subprocess.PIPE,
        env=env,
    )
    try:
        command = wait_record(tmp_path / "command.json")
        if command_lifetime == "running":
            handles.append(ProcessHandle(command["pid"]))
        proxy = wait_record(tmp_path / "proxy.json")
        handles.append(ProcessHandle(proxy["pid"]))
        assert proxy["job_env"] is None
        with socket.create_connection(("127.0.0.1", proxy["port"]), timeout=2):
            pass
        if action == "kill":
            owner.kill()  # No Python finally, atexit, or Textual unmount can run.
        else:
            owner.stdin.write(f"{action}\n".encode())
            owner.stdin.flush()
        owner.wait(timeout=10)
        if action == "release":
            assert all(not handle.exited() for handle in handles)
            with socket.create_connection(("127.0.0.1", proxy["port"]), timeout=2):
                pass
        else:
            assert all(handle.exited(5000) for handle in handles)
            with pytest.raises(OSError):
                socket.create_connection(("127.0.0.1", proxy["port"]), timeout=1)
        assert unrelated.poll() is None
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=10)
        owner.stdin.close()
        for handle in handles:
            handle.cleanup()
        unrelated.terminate()
        unrelated.wait(timeout=10)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Objects")
@pytest.mark.parametrize("action", ["close", "release"])
def test_multiple_venv_commands_retain_proxies_and_prune_empty_jobs(tmp_path, action):
    jobs = WorkspaceJobs()
    handles = []
    script = tmp_path / "processes.py"
    script.write_text(PROCESS_SCRIPT)
    try:
        for index in range(2):
            root = tmp_path / str(index)
            root.mkdir()
            env = os.environ.copy()
            env[WORKSPACE_JOB_ENV] = jobs.new_command()
            subprocess.run(
                [command_python(env), str(script), "command", str(root), "exit"],
                env=env,
                check=True,
                timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            proxy = wait_record(root / "proxy.json")
            handles.append(ProcessHandle(proxy["pid"]))
            jobs.finish_command()
            assert len(jobs.jobs) == index + 1
        env[WORKSPACE_JOB_ENV] = jobs.new_command()
        subprocess.run(
            [
                command_python(env),
                "-c",
                "from process_lifetime import join_workspace_job; join_workspace_job()",
            ],
            env=env,
            check=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        jobs.finish_command()
        assert len(jobs.jobs) == 2
        getattr(jobs, action)()
        assert not jobs.jobs
        if action == "close":
            assert all(handle.exited(5000) for handle in handles)
        else:
            assert all(not handle.exited() for handle in handles)
    finally:
        jobs.close()
        for handle in handles:
            handle.cleanup()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Objects")
def test_release_cancels_unfinished_command_tree(tmp_path):
    jobs = WorkspaceJobs()
    script = tmp_path / "processes.py"
    script.write_text(PROCESS_SCRIPT)
    env = os.environ.copy()
    env[WORKSPACE_JOB_ENV] = jobs.new_command()
    child = subprocess.Popen(
        [command_python(env), str(script), "command", str(tmp_path), "running"],
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    handle = None
    try:
        proxy = wait_record(tmp_path / "proxy.json")
        handle = ProcessHandle(proxy["pid"])
        jobs.release()
        child.wait(timeout=10)
        assert handle.exited(5000)
        assert not jobs.jobs
    finally:
        jobs.close()
        if handle is not None:
            handle.cleanup()
        child.wait(timeout=10)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Objects")
def test_missing_owner_prevents_command_execution():
    env = os.environ.copy()
    env[WORKSPACE_JOB_ENV] = f"Local\\VELA-missing-{uuid4()}"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from process_lifetime import join_workspace_job; join_workspace_job(); "
            "print('COMMAND EXECUTED')",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "COMMAND EXECUTED" not in result.stdout


def test_standalone_command_does_not_join_job(monkeypatch):
    monkeypatch.delenv(WORKSPACE_JOB_ENV, raising=False)
    join_workspace_job()


def test_non_windows_ignores_job_environment(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv(WORKSPACE_JOB_ENV, "unused")
    join_workspace_job()
    assert WORKSPACE_JOB_ENV not in os.environ
