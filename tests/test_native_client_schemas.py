"""Opt-in checks against installed client parsers, without starting agents."""

import json
import os
import shutil
import subprocess

import pytest
from test_client_config import clients as clients
from test_client_updates import capable
from test_model_context import catalog
from test_model_context import configured as configured

from client_config import parse


def test_installed_dsh_accepts_generated_capabilities(clients, configured, tmp_path):
    variable = "VELA_TEST_DSH_ADAPTER"
    module = os.environ.get(variable)
    if not module or not shutil.which("node"):
        pytest.skip(f"Set {variable} to the installed parser module and install Node")
    _, config = configured
    catalog(config.parent, [{**capable(), "display_name": "Friendly model"}])
    clients.apply(clients.plan("dsh"))
    path = clients.paths("dsh")[0]
    doc = parse(path, path.read_bytes())
    artifact = tmp_path / "native-catalog.json"
    artifact.write_text(json.dumps(doc["llm-pi-ai"]), encoding="utf-8")
    script = """
import {pathToFileURL} from 'node:url';
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
const [modulePath, path] = process.argv.slice(1);
const parser = await import(pathToFileURL(modulePath).href);
const config = parser.Config(JSON.parse(readFileSync(path, 'utf8')));
const model = config.providers.vela.models[0];
assert.deepEqual(model.input, ['text', 'image']);
assert.equal(model.maxTokens, 128000);
assert.equal(model.name, 'Friendly model');
assert.equal(model.reasoningEfforts.max, 'max');
assert.equal(model.reasoningEfforts.off, undefined);
console.log('Native parser accepted generated capabilities');
"""
    result = subprocess.run(
        [shutil.which("node"), "--input-type=module", "-e", script, module, str(artifact)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "accepted" in result.stdout
