"""Pin the private-hostname config to the fixture fakes.

The real list lives outside the repo (see harness/lib/sanitize.py); tests must
not depend on whatever the machine running them has configured.
"""

from __future__ import annotations

import pytest

from harness.lib import sanitize

FAKE_HOSTNAMES = (
    "example-tailnet-host-50a809",
    "example-tailnet-host-c77ea0",
    "example-private-host-33ba82",
)


@pytest.fixture(autouse=True)
def fake_private_hostnames(monkeypatch, tmp_path):
    monkeypatch.setenv(sanitize.HOSTNAMES_ENV, ",".join(FAKE_HOSTNAMES))
    monkeypatch.setenv(sanitize.HOSTNAMES_FILE_ENV, str(tmp_path / "absent.txt"))
    sanitize.reload_private_hostnames()
    yield
    monkeypatch.undo()
    sanitize.reload_private_hostnames()
