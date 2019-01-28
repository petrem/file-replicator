import os
from abc import ABCMeta, abstractmethod
from pathlib import Path

import pytest

from file_replicator.tar_adapter import (
    BsdTarAdapter,
    BusyBoxTarAdapter,
    GnuTarAdapter,
    detect_local_tar,
    detect_remote_tar,
)


@pytest.mark.parametrize("prefix", ["", "g"])
def test_gnu_tar_adapter(prefix):
    tar = GnuTarAdapter(prefix=prefix)
    assert tar.cmd == f"{prefix}tar"
    assert tar.receiver_cmd() == [
        f"{prefix}tar",
        "--no-same-owner",
        "--extract",
        "--verbose",
    ]
    assert tar.receiver_cmd_str() == " ".join(tar.receiver_cmd())
    assert tar.sender_cmd("foo") == [
        f"{prefix}tar",
        "--create",
        "foo",
        "--to-stdout",
        "--ignore-failed-read",
    ]
    assert tar.sender_cmd_str("foo") == " ".join(tar.sender_cmd("foo"))


def test_bsd_tar_adapter():
    tar = BsdTarAdapter()
    assert tar.cmd == "tar"
    assert tar.receiver_cmd() == ["tar", "-o", "-x", "-v"]
    assert tar.sender_cmd("foo") == [f"tar", "-c", "-f", "-", "foo"]


# not so useful, but here we go
def test_detect_real_local_tar():
    tar = detect_local_tar()
    assert isinstance(tar, (GnuTarAdapter, BsdTarAdapter, BusyBoxTarAdapter, None))


class AbstractMockTar(metaclass=ABCMeta):
    @property
    @abstractmethod
    def version():
        raise NotImplementedError

    def create_mock_tar(self):
        return f"""#!/bin/sh
/bin/cat << EOF
{self.version}
EOF
"""


class MockGnuTar(AbstractMockTar):
    @property
    def version(self):
        return """
tar (GNU tar) 1.31
Copyright (C) 2019 Free Software Foundation, Inc.
License GPLv3+: GNU GPL version 3 or later <https://gnu.org/licenses/gpl.html>.
This is free software: you are free to change and redistribute it.
There is NO WARRANTY, to the extent permitted by law.

Written by John Gilmore and Jay Fenlason.
"""


class MockBsdTar(AbstractMockTar):
    @property
    def version(self):
        return "bsdtar 2.8.3 - libarchive 2.8.3"


class MockBusyBoxTar(AbstractMockTar):
    @property
    def version(self):
        return "tar (busybox) 1.28.4"


class MockUnknownTar(AbstractMockTar):
    @property
    def version(self):
        return "Some Unknown tar v.1.0"


@pytest.fixture
def temp_dir_as_path(tmp_path, monkeypatch):
    with monkeypatch.context() as m:
        m.setenv("PATH", str(tmp_path))
        yield tmp_path


@pytest.fixture
def mock_tar(request, temp_dir_as_path):
    mock_tar, tar_name = request.param
    tar_cmd = temp_dir_as_path / tar_name
    tar_cmd.write_text(mock_tar.create_mock_tar())
    tar_cmd.chmod(0o755)


def which(command):
    try:
        path = next(
            p
            for p in map(Path, os.environ["PATH"].split(":"))
            if p.is_dir() and any(f.name == "bash" for f in p.iterdir())
        )
        return path / command
    except StopIteration:
        return None


@pytest.fixture(scope="module")
def bash():
    return str(which("bash"))


ALL_TARS = [
    GnuTarAdapter(),
    GnuTarAdapter(prefix="g"),
    BsdTarAdapter(),
    BusyBoxTarAdapter(),
]


@pytest.mark.parametrize(
    "mock_tar,expect",
    [
        ((MockGnuTar(), "tar"), GnuTarAdapter),
        ((MockGnuTar(), "gtar"), GnuTarAdapter),
        ((MockBsdTar(), "tar"), BsdTarAdapter),
        ((MockBusyBoxTar(), "tar"), BusyBoxTarAdapter),
        ((MockUnknownTar(), "tar"), type(None)),
    ],
    indirect=["mock_tar"],
)
def test_detect_tar(mock_tar, expect, bash):
    tar = detect_local_tar(acceptable=ALL_TARS)
    assert isinstance(tar, expect)
    remote_tar = detect_remote_tar(bash, acceptable=ALL_TARS)
    assert isinstance(remote_tar, expect)


def test_no_tar_cmd(temp_dir_as_path, bash):
    tar = detect_local_tar(acceptable=ALL_TARS)
    assert tar is None
    remote_tar = detect_remote_tar(bash, acceptable=ALL_TARS)
    assert remote_tar is None
