import contextlib
import os.path
import subprocess
import time

import pathspec
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.utils import has_attribute, unicode_paths

__all__ = ["make_file_replicator", "replicate_all_files", "replicate_files_on_change"]


# Small receiver code (written in bash for minimum dependencies) which repeatadly reads
# tar files from stdin and extracts them.
# Note that this requires the full tar command, not the busybox "lightweight" version.
RECEIVER_CODE = """
set -e
if {clean_out_first}; then
    rm -rf {dest_dir}/*
fi
mkdir -p {dest_dir}
cd {dest_dir}
while true; do
    {receiver_tar}
done 2>/dev/null
"""


@contextlib.contextmanager
def make_file_replicator(
    local_tar,
    remote_tar,
    src_dir,
    dest_parent_dir,
    bash_connection_command,
    clean_out_first=False,
    debugging=False,
):
    """Yield a copy_file(<filename>) function for replicating files over a "bash connection".

    The <filename> must be in the given <src_dir>. The final path in the <src_dir>
    becomes the destination directory in the <dest_parent_dir>.

    The <bash_connection_command> must be a list.

    """
    src_dir = os.path.abspath(src_dir)
    dest_parent_dir = os.path.abspath(dest_parent_dir)
    dest_dir = os.path.join(dest_parent_dir, os.path.basename(src_dir))

    p = subprocess.Popen(bash_connection_command, stdin=subprocess.PIPE)

    # Get the remote end up and running waiting for tar files.
    receiver_code = RECEIVER_CODE.format(
        dest_dir=dest_dir,
        clean_out_first=str(clean_out_first).lower(),
        receiver_tar=remote_tar.receiver_cmd_str(),
    )
    p.stdin.write(receiver_code.encode())
    p.stdin.flush()

    def copy_file(src_filename):
        src_filename = os.path.abspath(src_filename)
        rel_src_filename = os.path.relpath(src_filename, src_dir)
        if debugging:
            print(f"Sending {src_filename}...")
        result = subprocess.run(
            local_tar.sender_cmd(rel_src_filename),
            cwd=src_dir,
            check=True,
            stdout=p.stdin,
            stderr=subprocess.PIPE,
        )
        if result.stderr:
            if "No such file or directory" in result.stderr.decode():
                # Ignore because file was removed before we had a chance to copy it.
                pass
            else:
                raise RuntimeError(f"ERROR: {result.stderr.decode()}")
        p.stdin.flush()

    try:
        yield copy_file
    finally:
        p.stdin.close()
        p.wait()


def get_pathspec(src_dir, use_gitignore=True):
    gitignore_filename = os.path.join(src_dir, ".gitignore")
    if use_gitignore and os.path.isfile(gitignore_filename):
        with open(gitignore_filename) as f:
            spec = pathspec.PathSpec.from_lines("gitwildmatch", f)
    else:
        spec = pathspec.PathSpec.from_lines("gitwildmatch", [])
    return spec


def replicate_all_files(src_dir, copy_file, use_gitignore=True, debugging=False):
    """Walk src_dir to copy all files using copy_file()."""
    spec = get_pathspec(src_dir, use_gitignore)
    for filename in pathspec.util.iter_tree(src_dir):
        if not spec.match_file(filename):
            copy_file(os.path.join(src_dir, filename))


class CopyFileEventHandler(FileSystemEventHandler):
    """A watchdog.FileSystemEventHandler that copies files using copy_file()."""

    def __init__(self, copy_file, debugging=False):
        self.copy_file = copy_file
        self.debugging = debugging
        self.last_event_timestamp = time.time()

    def on_any_event(self, event):
        self.last_event_timestamp = time.time()
        if self.debugging:
            print(f"Detected change: {event.key}")

        if event.event_type == "deleted":
            return
        if event.is_directory and event.event_type == "modified":
            return
        if event.event_type == "moved":
            self.copy_file(event.dest_path)
        else:
            self.copy_file(event.src_path)


class GitIgnoreCopyFileEventHandler(CopyFileEventHandler):
    def __init__(self, copy_file, ignore_spec, debugging=False):
        super().__init__(copy_file, debugging)
        self.spec = ignore_spec

    def dispatch(self, event):
        if event.src_path and self.spec.match_file(
            unicode_paths.decode(event.src_path)
        ):
            if self.debugging:
                print(f"Ignoring source change on {event.src_path}")
            return
        if has_attribute(event, "dest_path") and self.spec.match_file(
            unicode_paths.decode(event.dest_path)
        ):
            if self.debugging:
                print(f"Ignoring destination change on {event.dest_path}")
            return
        super().dispatch(event)


class NoChangeTimeoutError(Exception):
    pass


def raise_if_timeout(last_change, timeout):
    elapsed = time.time() - last_change
    if elapsed > timeout:
        raise NoChangeTimeoutError(f"No changes detected for {elapsed} seconds.")


def replicate_files_on_change(
    src_dir, copy_file, timeout=None, use_gitignore=True, debugging=False
):
    """Wait for changes to files in src_dir and copy with copy_file().

    If provided, the timeout indicates when to return after that many seconds of no change.
    """
    src_dir = os.path.abspath(src_dir)
    if use_gitignore:
        spec = get_pathspec(src_dir, use_gitignore)
        event_handler = GitIgnoreCopyFileEventHandler(
            copy_file, spec, debugging=debugging
        )
    else:
        event_handler = CopyFileEventHandler(copy_file, debugging=debugging)
    observer = Observer()
    observer.schedule(event_handler, src_dir, recursive=True)
    if debugging:
        print("Starting observer")
    observer.start()
    try:
        while True:
            if timeout:
                raise_if_timeout(event_handler.last_event_timestamp, timeout)
            time.sleep(0.5)
    except (KeyboardInterrupt, NoChangeTimeoutError) as e:
        observer.stop()
        if debugging:
            print("Exitting on {e}")
    observer.join()
