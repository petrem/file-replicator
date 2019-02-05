from collections import namedtuple
import contextlib
import os
import os.path
import shutil
import tempfile
import time
import threading

import pytest

from file_replicator.lib import *
from file_replicator.tar_adapter import GnuTarAdapter, detect_local_tar


@pytest.fixture
def local_tar():
    acceptable = (GnuTarAdapter(), GnuTarAdapter(prefix="g"))
    tar = detect_local_tar(acceptable=acceptable)
    assert tar is not None
    return tar


@contextlib.contextmanager
def temp_directory():
    """Context manager for creating and cleaning up a temporary directory."""
    directory = tempfile.mkdtemp()
    try:
        yield directory
    finally:
        shutil.rmtree(directory)


def make_test_file(src_dir, relative_path, text, events=None):
    """Create a test file of text, optionally blocking on event and notifying when done."""
    if events and events.wait_on:
        # wait, but timeout -- in case something goes wrong
        events.wait_on.wait(5)
    filename = os.path.join(src_dir, relative_path)
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, "w") as f:
        f.write(text)
    if events and events.created:
        # allow file change to be picked up by a filesystem observer
        time.sleep(0.1)
        events.created.set()
        print("notified created")


def assert_file_contains(filename, text):
    """Assert that given file contains given text."""
    with open(filename) as f:
        assert f.read() == text


def test_empty_directories_are_copied(local_tar):
    with temp_directory() as src_parent_dir, temp_directory() as dest_parent_dir:
        src_dir = os.path.join(src_parent_dir, "test")
        os.makedirs(src_dir)
        with make_file_replicator(
            local_tar, local_tar, src_dir, dest_parent_dir, ("bash",)
        ) as copy_file:
            pass
        assert list(os.listdir(src_dir)) == []
        assert list(os.listdir(dest_parent_dir)) == ["test"]


def test_copy_one_file(local_tar):
    with temp_directory() as src_parent_dir, temp_directory() as dest_parent_dir:
        src_dir = os.path.join(src_parent_dir, "test")
        with make_file_replicator(
            local_tar, local_tar, src_dir, dest_parent_dir, ("bash",)
        ) as copy_file:
            make_test_file(src_dir, "test_file.txt", "hello")
            assert_file_contains(os.path.join(src_dir, "test_file.txt"), "hello")
            copy_file(os.path.join(src_dir, "test_file.txt"))
        assert_file_contains(
            os.path.join(dest_parent_dir, "test/test_file.txt"), "hello"
        )


def test_copy_file_with_unusual_characters_in_name(local_tar):
    with temp_directory() as src_parent_dir, temp_directory() as dest_parent_dir:
        src_dir = os.path.join(src_parent_dir, "test")
        with make_file_replicator(
            local_tar, local_tar, src_dir, dest_parent_dir, ("bash",)
        ) as copy_file:
            make_test_file(src_dir, "test ~$@%-file.txt", "hello")
            assert_file_contains(os.path.join(src_dir, "test ~$@%-file.txt"), "hello")
            copy_file(os.path.join(src_dir, "test ~$@%-file.txt"))
        assert_file_contains(
            os.path.join(dest_parent_dir, "test/test ~$@%-file.txt"), "hello"
        )


def test_make_missing_parent_directories(local_tar):
    with temp_directory() as src_parent_dir, temp_directory() as dest_parent_dir:
        src_dir = os.path.join(src_parent_dir, "test")
        with make_file_replicator(
            local_tar, local_tar, src_dir, dest_parent_dir, ("bash",)
        ) as copy_file:
            make_test_file(src_dir, "a/b/c/test_file.txt", "hello")
            assert_file_contains(os.path.join(src_dir, "a/b/c/test_file.txt"), "hello")
            copy_file(os.path.join(src_dir, "a/b/c/test_file.txt"))
        assert_file_contains(
            os.path.join(dest_parent_dir, "test/a/b/c/test_file.txt"), "hello"
        )


def test_replicate_all_files(local_tar):
    with temp_directory() as src_parent_dir, temp_directory() as dest_parent_dir:
        src_dir = os.path.join(src_parent_dir, "test")
        make_test_file(src_dir, "a.txt", "hello")
        make_test_file(src_dir, "b/c.txt", "goodbye")
        with make_file_replicator(
            local_tar, local_tar, src_dir, dest_parent_dir, ("bash",)
        ) as copy_file:
            replicate_all_files(src_dir, copy_file)
        assert_file_contains(os.path.join(src_dir, "a.txt"), "hello")
        assert_file_contains(os.path.join(src_dir, "b/c.txt"), "goodbye")


EventPair = namedtuple("EventPair", ["wait_on", "created"])


@pytest.fixture
def delay_events():
    return EventPair(threading.Event(), threading.Event())


def test_detect_and_copy_new_file(local_tar, delay_events):
    with temp_directory() as src_parent_dir, temp_directory() as dest_parent_dir:
        src_dir = os.path.join(src_parent_dir, "test")

        # Make one file now and don't change it.
        make_test_file(src_dir, "a.txt", "hello")

        # Make another file in a short while (after the watcher has started).
        delayed_t = threading.Thread(
            target=make_test_file, args=(src_dir, "b.txt", "goodbye", delay_events)
        )
        delayed_t.start()

        # Confirm we have the files we expect.
        assert os.path.exists(os.path.join(src_dir, "a.txt"))
        assert not os.path.exists(os.path.join(dest_parent_dir, "test/a.txt"))
        assert not os.path.exists(os.path.join(src_dir, "b.txt"))
        assert not os.path.exists(os.path.join(dest_parent_dir, "test/b.txt"))

        # Watch for changes and copy files, and stop after short while of inactvitiy.
        # The second file (see above) should be created during this internval.
        print("before repl")
        with make_file_replicator(
            local_tar, local_tar, src_dir, dest_parent_dir, ("bash",)
        ) as copy_file:
            replicate_files_on_change(
                src_dir,
                copy_file,
                observer_up_event=delay_events.wait_on,
                terminate_event=delay_events.created,
                debugging=True,
            )
        delayed_t.join()
        print("after repl")

        # Confirm we have the files we expect.
        assert os.path.exists(os.path.join(src_dir, "a.txt"))
        assert not os.path.exists(os.path.join(dest_parent_dir, "test/a.txt"))
        assert os.path.exists(os.path.join(src_dir, "b.txt"))
        assert os.path.exists(os.path.join(dest_parent_dir, "test/b.txt"))

        # Double check that contents is correct too.
        assert_file_contains(os.path.join(dest_parent_dir, "test/b.txt"), "goodbye")


def test_detect_and_copy_modified_file(local_tar, delay_events):
    with temp_directory() as src_parent_dir, temp_directory() as dest_parent_dir:
        src_dir = os.path.join(src_parent_dir, "test")

        # Make one file now and don't change it.
        make_test_file(src_dir, "a.txt", "hello")

        # Change that file in a short while (after the watcher has started).
        delayed_t = threading.Thread(
            target=make_test_file, args=(src_dir, "a.txt", "hello again", delay_events)
        )
        delayed_t.start()

        # Confirm we have the files we expect.
        assert os.path.exists(os.path.join(src_dir, "a.txt"))
        assert not os.path.exists(os.path.join(dest_parent_dir, "test/a.txt"))

        # Watch for changes and copy files, and stop after short while of inactvitiy.
        # The second file (see above) should be created during this internval.
        with make_file_replicator(
            local_tar, local_tar, src_dir, dest_parent_dir, ("bash",)
        ) as copy_file:
            replicate_files_on_change(
                src_dir,
                copy_file,
                observer_up_event=delay_events.wait_on,
                terminate_event=delay_events.created,
                debugging=True,
            )
        delayed_t.join()

        # Confirm we have the files we expect.
        assert os.path.exists(os.path.join(src_dir, "a.txt"))
        assert os.path.exists(os.path.join(dest_parent_dir, "test/a.txt"))

        # Double check that contents is correct too.
        assert_file_contains(os.path.join(dest_parent_dir, "test/a.txt"), "hello again")


def test_detect_and_copy_new_file_in_new_directories(local_tar, delay_events):
    with temp_directory() as src_parent_dir, temp_directory() as dest_parent_dir:
        src_dir = os.path.join(src_parent_dir, "test")

        # Make one file now and don't change it.
        make_test_file(src_dir, "a.txt", "hello")

        # Create a new file in nested new directories in a short while (after the watcher has started).
        delayed_t = threading.Thread(
            target=make_test_file,
            args=(src_dir, "a/b/c/d/e/a.txt", "hello again", delay_events),
        )
        delayed_t.start()

        # Confirm we have the files we expect.
        assert os.path.exists(os.path.join(src_dir, "a.txt"))
        assert not os.path.exists(os.path.join(dest_parent_dir, "test/a.txt"))
        assert not os.path.exists(os.path.join(src_dir, "a/b/c/d/e/a.txt"))
        assert not os.path.exists(os.path.join(dest_parent_dir, "test/a/b/c/d/e/a.txt"))

        # Watch for changes and copy files, and stop after short while of inactvitiy.
        # The second file (see above) should be created during this internval.
        with make_file_replicator(
            local_tar, local_tar, src_dir, dest_parent_dir, ("bash",)
        ) as copy_file:
            replicate_files_on_change(
                src_dir,
                copy_file,
                observer_up_event=delay_events.wait_on,
                terminate_event=delay_events.created,
                debugging=True,
            )
        delayed_t.join()

        # Confirm we have the files we expect.
        assert os.path.exists(os.path.join(src_dir, "a.txt"))
        assert not os.path.exists(os.path.join(dest_parent_dir, "test/a.txt"))
        assert os.path.exists(os.path.join(src_dir, "a/b/c/d/e/a.txt"))
        assert os.path.exists(os.path.join(dest_parent_dir, "test/a/b/c/d/e/a.txt"))

        # Double check that contents is correct too.
        assert_file_contains(
            os.path.join(dest_parent_dir, "test/a/b/c/d/e/a.txt"), "hello again"
        )
