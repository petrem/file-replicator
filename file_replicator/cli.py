import os.path

import click

import file_replicator

from .lib import make_file_replicator, replicate_all_files, replicate_files_on_change
from .tar_adapter import *


@click.command()
@click.argument("src_dir")
@click.argument("dest_parent_dir")
@click.argument("connection_command", nargs=-1)
@click.option(
    "--clean-out-first",
    is_flag=True,
    default=False,
    help="Optionally start by cleaning out the destination directory.",
)
@click.option(
    "--with-initial-replication / --no-initial-replication",
    default=True,
    help="Perform (or not) an initial replication of all files.",
)
@click.option(
    "--replicate-on-change / --no-replicate-on-change",
    default=True,
    help="Perform (or not) a wait-for-change-and-replicate cycle.",
)
@click.option(
    "--gitignore / --no-gitignore",
    default=True,
    help="Use .gitignore (or not) to filter files.",
)
@click.option(
    "--debugging", is_flag=True, default=False, help="Print debugging information."
)
@click.option(
    "--local-tar-gnu",
    "local_tar_fn",
    flag_value=GnuTarAdapter,
    help="Local tar is gnu tar.",
)
@click.option(
    "--local-tar-bsd",
    "local_tar_fn",
    flag_value=BsdTarAdapter,
    help="Local tar is bsd tar.",
)
@click.option(
    "--local-tar-gnu-prefix",
    "local_tar_fn",
    flag_value=lambda: GnuTarAdapter(prefix="g"),
    help="Use gtar as gnu tar locally.",
)
@click.option(
    "--local-tar-detect",
    "local_tar_fn",
    flag_value=detect_local_tar,
    default=True,
    help="Attempt to detect local tar flavor.",
)
@click.option(
    "--remote-tar-gnu",
    "remote_tar_fn",
    flag_value=lambda cmd: GnuTarAdapter(),
    default=True,
    help="Remote tar is gnu tar",
)
@click.option(
    "--remote-tar-gnu-prefix",
    "remote_tar_fn",
    flag_value=lambda cmd: GnuTarAdapter(prefix="g"),
    help="Use gtar as gnu tar remotely.",
)
@click.option(
    "--remote-tar-detect",
    "remote_tar_fn",
    flag_value=lambda cmd: detect_remote_tar(cmd),
    help="Attempt to detect remote tar flavor.",
)
@click.version_option(version=file_replicator.__version__)
def main(
    src_dir,
    dest_parent_dir,
    connection_command,
    clean_out_first,
    with_initial_replication,
    replicate_on_change,
    gitignore,
    debugging,
    local_tar_fn,
    remote_tar_fn,
):
    """Replicate files to another computer e.g. for remote development.

    SRC_DIR is the source directory on this machine.

    DEST_PARENT_DIR is the (absolute) destination parent directory on the
    remote machine accessed using the CONNECTION_COMMAND.

    The CONNECTION_COMMAND must result in a running instance of bash ready to receive commands
    on stdin.

    Example CONNECTION_COMMANDs include:

        ssh some.host.com bash

        docker exec -i my_container bash

        docker-compose exec -T my_container bash

    So a full use of the tool might look like:

        file-replicator my_code_dir /home/code -- docker exec -i a_container bash

    (the use of "--" prevents any further processing of command line arguments by
    file-replicator, leaving them all for docker)

    Initially, all files and required directories are recursively copied. Then it
    waits for changes before copying each modified or new file. This can be modified
    with the switches.

    Note that empty directories are not replicated until they contain a file.

    Lastly, the only time the tool deletes files or directories is if called with
    the optional --clean-out-first switch.

    """
    if not connection_command:
        raise click.UsageError(
            "Please provide a connection command to access the destination server."
        )
    if not os.path.isabs(dest_parent_dir):
        raise click.UsageError(
            "The destination parent directory must be an absolute path."
        )
    if not os.path.exists(src_dir) or not os.path.isdir(src_dir):
        raise click.UsageError("The source destination must exist and be a directory.")

    local_tar = local_tar_fn()
    remote_tar = remote_tar_fn(connection_command)
    if debugging:
        print(f"Local tar: {local_tar}")
        print(f"Remote tar: {remote_tar}")
    if not isinstance(remote_tar, GnuTarAdapter):
        click.UsageError("Cannot use non-gnu remote tar!")

    if clean_out_first:
        click.secho(
            "Clearing out all destination files first!", fg="green", bold="true"
        )

    with make_file_replicator(
        local_tar,
        remote_tar,
        src_dir,
        dest_parent_dir,
        connection_command,
        clean_out_first=clean_out_first,
        debugging=debugging,
    ) as copy_file:
        if with_initial_replication:
            replicate_all_files(
                src_dir, copy_file, use_gitignore=gitignore, debugging=debugging
            )
        if replicate_on_change:
            replicate_files_on_change(
                src_dir, copy_file, use_gitignore=gitignore, debugging=debugging
            )
