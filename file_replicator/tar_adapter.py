import subprocess
from abc import ABCMeta, abstractmethod

__all__ = [
    "GnuTarAdapter",
    "BsdTarAdapter",
    "BusyBoxTarAdapter",
    "detect_local_tar",
    "detect_remote_tar",
]


class AbstractTarAdapter(metaclass=ABCMeta):
    @abstractmethod
    def __str__(self):
        raise NotImplementedError

    def __repr__(self):
        cls = type(self)
        return (
            f"<{cls.__module__}.{cls.__name__} ({str(self)}) object at 0x{id(self):x}>"
        )

    @property
    def cmd(self):
        return "tar"

    @abstractmethod
    def receiver_options(self):
        raise NotImplementedError

    @abstractmethod
    def sender_options(self, src_file):
        raise NotImplementedError

    def receiver_cmd(self):
        return [self.cmd] + self.receiver_options()

    def receiver_cmd_str(self):
        return " ".join(self.receiver_cmd())

    def sender_cmd(self, src_file):
        return [self.cmd] + self.sender_options(src_file)

    def sender_cmd_str(self, src_file):
        return " ".join(self.sender_cmd(src_file))

    @property
    def version_option(self):
        return "--version"

    @abstractmethod
    def match_flavor_output(self, output):
        raise NotImplementedError


class PrefixedTarAdapter(AbstractTarAdapter):
    def __init__(self, prefix=""):
        self._prefix = prefix
        super().__init__()

    @property
    def cmd(self):
        return f"{self._prefix}tar"


class GnuTarAdapter(PrefixedTarAdapter):
    def __str__(self):
        return f"Gnu Tar [{self.cmd}]"

    def receiver_options(self):
        return ["--no-same-owner", "--extract", "--verbose"]

    def sender_options(self, src_file):
        return ["--create", src_file, "--to-stdout", "--ignore-failed-read"]

    def match_flavor_output(self, output):
        return "GNU tar" in output


class BsdTarAdapter(AbstractTarAdapter):
    def __str__(self):
        return "BSD Tar"

    def receiver_options(self):
        return ["-o", "-x", "-v"]

    def sender_options(self, src_file):
        return ["-c", "-f", "-", src_file]

    def match_flavor_output(self, output):
        return "bsdtar" in output


class BusyBoxTarAdapter(AbstractTarAdapter):
    def __str__(self):
        return "BusyBox Tar"

    def receiver_options(self):
        return ["x", "-v"]

    def sender_options(self, src_file):
        return ["c", "-f", "-", src_file]

    def match_flavor_output(self, output):
        return "busybox" in output


def detect_local_tar(acceptable=None):
    """Determine, if any, a suitable sender tar"""
    if acceptable is None:
        acceptable = [
            GnuTarAdapter(),
            GnuTarAdapter(prefix="g"),
            BsdTarAdapter(),
            BusyBoxTarAdapter(),
        ]
    for tar_flavor in acceptable:
        try:
            result = subprocess.run(
                [tar_flavor.cmd, tar_flavor.version_option],
                stdout=subprocess.PIPE,
                check=True,
                universal_newlines=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            continue
        if tar_flavor.match_flavor_output(result.stdout):
            return tar_flavor
    return None


def detect_remote_tar(connection_command, acceptable=None):
    """Determine, if any, a suitable receiver tar"""
    if acceptable is None:
        acceptable = [GnuTarAdapter(), GnuTarAdapter(prefix="g")]
    for tar_flavor in acceptable:
        try:
            result = subprocess.run(
                connection_command,
                input=f"{tar_flavor.cmd} {tar_flavor.version_option}",
                stdout=subprocess.PIPE,
                universal_newlines=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            raise RuntimeError(f"Error using connection command: {e}")
        if tar_flavor.match_flavor_output(result.stdout):
            return tar_flavor
    return None
