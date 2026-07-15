"""Secure, atomic persistence for versioned download plans."""

from __future__ import annotations

import errno
import os
import secrets
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

from pydantic import TypeAdapter, ValidationError

from wit.errors import WitError
from wit.plans import DownloadPlan, InvalidDownloadPlanError, PlanIdentifier

_DIRECTORY_MODE: Final = 0o700
_FILE_MODE: Final = 0o600
_PLAN_FILE_SUFFIX: Final = ".json"
_PLAN_DIRECTORY_NAME: Final = "plans"
_PLAN_IDENTIFIER_ADAPTER: TypeAdapter[str] = TypeAdapter(PlanIdentifier)

_DIRECTORY_OPEN_FLAGS: Final = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_FILE_READ_FLAGS: Final = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
)
_FILE_WRITE_FLAGS: Final = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)


class PlanStoreError(WitError):
    """Base class for safe plan-persistence failures."""


class InvalidPlanIdentifierError(PlanStoreError):
    """A caller supplied an identifier that cannot be used as a filename."""


class StoredPlanNotFoundError(PlanStoreError):
    """No stored plan exists for a validated identifier."""


class InvalidStoredPlanError(PlanStoreError):
    """A stored file is not a supported download plan."""


class UnsafePlanStoreError(PlanStoreError):
    """A state directory or plan file is unsafe to access."""


class _PlanDirectoryMissing(Exception):
    """Internal signal used to make listing a missing store return no plans."""


def _default_state_directory() -> Path:
    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    if xdg_state_home:
        return Path(xdg_state_home).expanduser() / "wit"
    return Path.home() / ".local" / "state" / "wit"


def _normalise_state_directory(value: str | Path | None) -> Path:
    try:
        candidate = _default_state_directory() if value is None else Path(value).expanduser()
    except (OSError, RuntimeError):
        raise UnsafePlanStoreError("Wit state directory cannot be resolved safely") from None

    if not candidate.is_absolute() or ".." in candidate.parts:
        raise UnsafePlanStoreError(
            "Wit state directory must be an absolute path without parent traversal"
        )

    normalised = Path(os.path.normpath(candidate))
    if normalised == Path(normalised.anchor):
        raise UnsafePlanStoreError("Wit state directory must not be a filesystem root")

    try:
        metadata = normalised.lstat()
    except FileNotFoundError:
        return normalised
    except OSError:
        raise UnsafePlanStoreError("Wit state directory cannot be inspected safely") from None

    if stat.S_ISLNK(metadata.st_mode):
        raise UnsafePlanStoreError("Wit state directory must not be a symbolic link")
    if not stat.S_ISDIR(metadata.st_mode):
        raise UnsafePlanStoreError("Wit state directory must be a directory")
    return normalised


def _validate_plan_identifier(value: str) -> str:
    try:
        return _PLAN_IDENTIFIER_ADAPTER.validate_python(value, strict=True)
    except ValidationError:
        raise InvalidPlanIdentifierError("plan ID contains unsafe characters or length") from None


def _plan_filename(plan_id: str) -> str:
    return f"{plan_id}{_PLAN_FILE_SUFFIX}"


def _plan_id_from_filename(filename: str) -> str | None:
    if not filename.endswith(_PLAN_FILE_SUFFIX):
        return None
    candidate = filename[: -len(_PLAN_FILE_SUFFIX)]
    try:
        plan_id = _validate_plan_identifier(candidate)
    except InvalidPlanIdentifierError:
        return None
    return plan_id if _plan_filename(plan_id) == filename else None


def _validate_owner(metadata: os.stat_result, *, description: str) -> None:
    getuid = getattr(os, "getuid", None)
    if getuid is not None and metadata.st_uid != getuid():
        raise UnsafePlanStoreError(f"{description} must be owned by the current user")


def _validate_directory_descriptor(descriptor: int, *, description: str) -> None:
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        raise UnsafePlanStoreError(f"{description} must be a directory")
    _validate_owner(metadata, description=description)
    if stat.S_IMODE(metadata.st_mode) & 0o022:
        raise UnsafePlanStoreError(f"{description} must not be writable by other users")


def _validate_plan_file_descriptor(descriptor: int) -> None:
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        raise UnsafePlanStoreError("stored download plan must be a regular file")
    _validate_owner(metadata, description="stored download plan")
    if stat.S_IMODE(metadata.st_mode) & 0o022:
        raise UnsafePlanStoreError("stored download plan must not be writable by other users")


def _open_directory_path(path: Path) -> int:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        raise _PlanDirectoryMissing from None
    except OSError:
        raise UnsafePlanStoreError("plan store directory cannot be inspected safely") from None

    if stat.S_ISLNK(metadata.st_mode):
        raise UnsafePlanStoreError("plan store directory must not be a symbolic link")
    if not stat.S_ISDIR(metadata.st_mode):
        raise UnsafePlanStoreError("plan store path must be a directory")

    try:
        descriptor = os.open(path, _DIRECTORY_OPEN_FLAGS)
    except FileNotFoundError:
        raise _PlanDirectoryMissing from None
    except OSError:
        raise UnsafePlanStoreError("plan store directory cannot be opened safely") from None

    try:
        _validate_directory_descriptor(descriptor, description="plan store directory")
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _open_child_directory(parent_descriptor: int, name: str) -> int:
    try:
        metadata = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        raise _PlanDirectoryMissing from None
    except OSError:
        raise UnsafePlanStoreError("plan directory cannot be inspected safely") from None

    if stat.S_ISLNK(metadata.st_mode):
        raise UnsafePlanStoreError("plan directory must not be a symbolic link")
    if not stat.S_ISDIR(metadata.st_mode):
        raise UnsafePlanStoreError("plan directory path must be a directory")

    try:
        descriptor = os.open(name, _DIRECTORY_OPEN_FLAGS, dir_fd=parent_descriptor)
    except FileNotFoundError:
        raise _PlanDirectoryMissing from None
    except OSError:
        raise UnsafePlanStoreError("plan directory cannot be opened safely") from None

    try:
        _validate_directory_descriptor(descriptor, description="plan directory")
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


class PlanStore:
    """Persist plans below one validated Wit state directory."""

    def __init__(self, state_dir: str | Path | None = None) -> None:
        self._state_dir = _normalise_state_directory(state_dir)

    @property
    def state_dir(self) -> Path:
        """Return the Wit state directory used by this store."""
        return self._state_dir

    @property
    def plans_dir(self) -> Path:
        """Return the private directory containing plan JSON files."""
        return self._state_dir / _PLAN_DIRECTORY_NAME

    def save(self, plan: DownloadPlan) -> Path:
        """Atomically store a complete plan and return its final path."""
        plan_id = _validate_plan_identifier(plan.plan_id)
        filename = _plan_filename(plan_id)
        payload = plan.to_json().encode("utf-8")

        try:
            with self._open_plans_directory(create=True) as directory_descriptor:
                self._assert_safe_destination(directory_descriptor, filename)
                self._write_atomic(directory_descriptor, filename, payload)
        except PlanStoreError:
            raise
        except OSError:
            raise PlanStoreError("download plan could not be stored safely") from None

        return self.plans_dir / filename

    def load(self, plan_id: str) -> DownloadPlan:
        """Load one strictly validated plan by its safe identifier."""
        validated_id = _validate_plan_identifier(plan_id)
        try:
            with self._open_plans_directory(create=False) as directory_descriptor:
                return self._load_from_directory(directory_descriptor, validated_id)
        except _PlanDirectoryMissing:
            raise StoredPlanNotFoundError("stored download plan was not found") from None
        except PlanStoreError:
            raise
        except OSError:
            raise PlanStoreError("stored download plan could not be read safely") from None

    def list_plans(self) -> tuple[DownloadPlan, ...]:
        """Return validated plans in plan-ID order, ignoring unrelated entries."""
        try:
            with self._open_plans_directory(create=False) as directory_descriptor:
                with os.scandir(directory_descriptor) as entries:
                    plan_ids = sorted(
                        plan_id
                        for entry in entries
                        if (plan_id := _plan_id_from_filename(entry.name)) is not None
                    )
                return tuple(
                    self._load_from_directory(directory_descriptor, plan_id) for plan_id in plan_ids
                )
        except _PlanDirectoryMissing:
            return ()
        except PlanStoreError:
            raise
        except OSError:
            raise PlanStoreError("stored download plans could not be listed safely") from None

    @contextmanager
    def _open_plans_directory(self, *, create: bool) -> Iterator[int]:
        state_descriptor: int | None = None
        plans_descriptor: int | None = None
        try:
            if create:
                try:
                    self._state_dir.mkdir(mode=_DIRECTORY_MODE, parents=True, exist_ok=True)
                except OSError:
                    raise PlanStoreError(
                        "Wit state directory could not be created safely"
                    ) from None

            state_descriptor = _open_directory_path(self._state_dir)
            if create:
                os.fchmod(state_descriptor, _DIRECTORY_MODE)
                try:
                    os.mkdir(
                        _PLAN_DIRECTORY_NAME,
                        mode=_DIRECTORY_MODE,
                        dir_fd=state_descriptor,
                    )
                except FileExistsError:
                    pass

            plans_descriptor = _open_child_directory(state_descriptor, _PLAN_DIRECTORY_NAME)
            if create:
                os.fchmod(plans_descriptor, _DIRECTORY_MODE)
            yield plans_descriptor
        finally:
            if plans_descriptor is not None:
                os.close(plans_descriptor)
            if state_descriptor is not None:
                os.close(state_descriptor)

    @staticmethod
    def _assert_safe_destination(directory_descriptor: int, filename: str) -> None:
        try:
            metadata = os.stat(
                filename,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return
        except OSError:
            raise UnsafePlanStoreError("stored download plan cannot be inspected safely") from None

        if stat.S_ISLNK(metadata.st_mode):
            raise UnsafePlanStoreError("stored download plan must not be a symbolic link")
        if not stat.S_ISREG(metadata.st_mode):
            raise UnsafePlanStoreError("stored download plan must be a regular file")
        _validate_owner(metadata, description="stored download plan")

    @staticmethod
    def _write_atomic(directory_descriptor: int, filename: str, payload: bytes) -> None:
        temporary_name: str | None = None
        temporary_descriptor: int | None = None
        try:
            for _ in range(128):
                candidate = f".plan-{secrets.token_hex(16)}.tmp"
                try:
                    temporary_descriptor = os.open(
                        candidate,
                        _FILE_WRITE_FLAGS,
                        _FILE_MODE,
                        dir_fd=directory_descriptor,
                    )
                except FileExistsError:
                    continue
                temporary_name = candidate
                break
            else:
                raise PlanStoreError("temporary plan file could not be created safely")

            os.fchmod(temporary_descriptor, _FILE_MODE)
            with os.fdopen(temporary_descriptor, "wb") as stream:
                temporary_descriptor = None
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())

            os.replace(
                temporary_name,
                filename,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
            )
            temporary_name = None
            os.fsync(directory_descriptor)
        finally:
            if temporary_descriptor is not None:
                os.close(temporary_descriptor)
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name, dir_fd=directory_descriptor)
                except OSError:
                    pass

    @staticmethod
    def _load_from_directory(directory_descriptor: int, plan_id: str) -> DownloadPlan:
        filename = _plan_filename(plan_id)
        descriptor: int | None = None
        try:
            descriptor = os.open(filename, _FILE_READ_FLAGS, dir_fd=directory_descriptor)
            _validate_plan_file_descriptor(descriptor)
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = None
                payload = stream.read()
        except FileNotFoundError:
            raise StoredPlanNotFoundError("stored download plan was not found") from None
        except OSError as error:
            if error.errno == errno.ELOOP:
                raise UnsafePlanStoreError(
                    "stored download plan must not be a symbolic link"
                ) from None
            raise
        finally:
            if descriptor is not None:
                os.close(descriptor)

        try:
            plan = DownloadPlan.from_json(payload)
        except InvalidDownloadPlanError:
            raise InvalidStoredPlanError(
                "stored download plan is corrupt or uses an unsupported schema version"
            ) from None
        if plan.plan_id != plan_id:
            raise InvalidStoredPlanError("stored download plan ID does not match its filename")
        return plan
