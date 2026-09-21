from __future__ import annotations

from io import StringIO

import pytest
from rich.cells import cell_len
from rich.console import Console
from test_model_context import catalog, model
from test_model_context import configured as configured

from model_menu import print_model_snapshot


@pytest.mark.parametrize("width", [48, 80, 110])
def test_rich_snapshot_fits_terminal_and_keeps_literal_model_ids(configured, width):
    settings, path = configured
    catalog(path.parent, [model("[red]模型-model"), model("very-long-model-name-1234567890")])
    stream = StringIO()
    print_model_snapshot(settings, Console(file=stream, width=width, color_system=None))
    output = stream.getvalue()
    assert "[red]" in output
    assert "\033" not in output and "\t" not in output
    assert all(cell_len(line) <= width for line in output.splitlines())


def test_plain_output_includes_entire_catalog(configured):
    settings, path = configured
    catalog(path.parent, [model(f"model-{i:02}") for i in range(40)])
    stream = StringIO()
    print_model_snapshot(settings, Console(file=stream, width=100, color_system=None))
    output = stream.getvalue()
    assert "model-00" in output and "model-39" in output
    assert "\033" not in output
