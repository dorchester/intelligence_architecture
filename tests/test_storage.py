"""Tests for storage abstraction and run isolation."""

import tempfile
from pathlib import Path

import pytest

from storage.local import LocalStorage


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(base_dir=tmp_path)


def test_write_and_read(storage):
    data = b"test content"
    storage.write("run-1", "client-a", "input", "file.csv", data)
    assert storage.read("run-1", "client-a", "input", "file.csv") == data


def test_exists(storage):
    assert not storage.exists("run-1", "client-a", "input", "file.csv")
    storage.write("run-1", "client-a", "input", "file.csv", b"data")
    assert storage.exists("run-1", "client-a", "input", "file.csv")


def test_list_files(storage):
    storage.write("run-1", "client-a", "working", "a.txt", b"a")
    storage.write("run-1", "client-a", "working", "b.txt", b"b")
    files = storage.list_files("run-1", "client-a", "working")
    assert set(files) == {"a.txt", "b.txt"}


def test_run_isolation_different_runs(storage):
    """Different run_ids cannot see each other's data."""
    storage.write("run-1", "client-a", "input", "data.csv", b"run1 data")
    storage.write("run-2", "client-a", "input", "data.csv", b"run2 data")

    assert storage.read("run-1", "client-a", "input", "data.csv") == b"run1 data"
    assert storage.read("run-2", "client-a", "input", "data.csv") == b"run2 data"


def test_client_isolation(storage):
    """Different client_ids cannot see each other's data."""
    storage.write("run-1", "client-a", "input", "data.csv", b"client-a data")
    storage.write("run-1", "client-b", "input", "data.csv", b"client-b data")

    assert storage.read("run-1", "client-a", "input", "data.csv") == b"client-a data"
    assert storage.read("run-1", "client-b", "input", "data.csv") == b"client-b data"


def test_cross_run_access_fails(storage):
    """Attempting to read from a non-existent run raises an error."""
    storage.write("run-1", "client-a", "input", "data.csv", b"data")
    with pytest.raises(FileNotFoundError):
        storage.read("run-2", "client-a", "input", "data.csv")


def test_cross_client_access_fails(storage):
    """Attempting to read from a different client raises an error."""
    storage.write("run-1", "client-a", "input", "data.csv", b"data")
    with pytest.raises(FileNotFoundError):
        storage.read("run-1", "client-b", "input", "data.csv")
