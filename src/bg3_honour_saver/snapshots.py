import hashlib
import os
import shutil
import stat
import time
from collections.abc import Iterable
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from .bg3 import PREVIEW_FILES, SAVE_FILE, SaverError, game_id, is_link
from .database import Snapshot
from .workspace import Workspace


@dataclass(frozen=True)
class Entry:
    folder: bool
    size: int
    modified_ns: int
    changed_ns: int
    inode: int


@contextmanager
def deny_writers(paths: Iterable[Path]):
    if os.name != "nt":
        yield
        return

    import ctypes
    from ctypes import wintypes

    generic_read = 0x80000000
    share_read = 0x00000001
    open_existing = 3
    normal_attributes = 0x80
    invalid_handle = ctypes.c_void_p(-1).value
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    open_file = kernel.CreateFileW
    open_file.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    open_file.restype = wintypes.HANDLE
    close_handle = kernel.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    with ExitStack() as handles:
        for path in paths:
            # Use extended Win32 paths so long file names work here too.
            name = str(path)
            if not name.startswith("\\\\?\\"):
                name = "\\\\?\\UNC\\" + name[2:] if name.startswith("\\\\") else "\\\\?\\" + name
            handle = open_file(name, generic_read, share_read, None, open_existing, normal_attributes, None)
            if handle == invalid_handle:
                error = ctypes.WinError(ctypes.get_last_error())
                if error.winerror in (32, 33):  # Sharing or lock violation.
                    raise SaverError("save_in_use", str(path)) from error
                raise error
            handles.callback(close_handle, handle)
        yield


def inventory(root: Path) -> dict[Path, Entry]:
    entries = {}

    def visit(path: Path) -> None:
        info = path.lstat()
        folder = stat.S_ISDIR(info.st_mode)
        if is_link(info) or (not folder and not stat.S_ISREG(info.st_mode)):
            raise SaverError("unsupported_file", str(path))
        entries[path.relative_to(root)] = Entry(
            folder, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino
        )
        if folder:
            for child in sorted(path.iterdir()):
                visit(child)

    visit(root)
    return entries


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def tree_digest(root: Path, entries: dict[Path, Entry]) -> str:
    digest = hashlib.sha256()
    for relative, entry in sorted(entries.items()):
        digest.update(b"d" if entry.folder else b"f")
        digest.update(relative.as_posix().encode("utf-8", "surrogatepass") + b"\0")
        if not entry.folder:
            digest.update(bytes.fromhex(file_digest(root / relative)))
    return digest.hexdigest()


def stable_copy(source: Path, destination: Path, entries: dict[Path, Entry]) -> dict[Path, Entry]:
    for relative, entry in entries.items():
        origin = source / relative
        target = destination / relative
        if is_link(origin.lstat()):
            raise SaverError("save_changing")
        if entry.folder:
            target.mkdir()
        else:
            shutil.copy2(origin, target)
    copied = inventory(destination)
    if entries != inventory(source) or copied.keys() != entries.keys():
        raise SaverError("save_changing")
    for relative, entry in entries.items():
        duplicate = copied[relative]
        if entry.folder != duplicate.folder or (not entry.folder and entry.size != duplicate.size):
            raise SaverError("save_changing")
    return copied


def capture(
    workspace: Workspace,
    slot: Path,
    *,
    kind: str = "auto",
    expected: dict[Path, Entry] | None = None,
) -> Snapshot | None:
    entries = inventory(slot)
    root = entries[Path(".")]
    if not root.folder:
        raise SaverError("invalid_save", slot.name)
    if expected is not None and entries != expected:
        raise SaverError("save_changing")
    main = entries.get(Path(SAVE_FILE))
    if main is not None and main.folder:
        main = None
    # A pre-restore backup can preserve an incomplete slot for download.
    if kind == "auto" and (main is None or main.size == 0):
        raise SaverError("save_changing")
    fingerprint = file_digest(slot / SAVE_FILE) if main is not None else ""
    if kind == "auto" and workspace.db.baseline(slot.name) == fingerprint:
        if inventory(slot) != entries:
            raise SaverError("save_changing")
        return None

    snapshot_id = uuid4().hex
    identifier = game_id(slot.name)
    group = workspace.backups / identifier
    if is_link(workspace.backups.lstat()):
        raise SaverError("linked_path", str(workspace.backups))
    group.mkdir(exist_ok=True)
    if is_link(group.lstat()):
        raise SaverError("linked_path", str(group))
    destination = group / snapshot_id
    with TemporaryDirectory(dir=group, prefix=".pending-") as staging:
        payload = Path(staging) / "save"
        copied = stable_copy(slot, payload, entries)
        if main is not None and file_digest(payload / SAVE_FILE) != fingerprint:
            raise SaverError("save_changing")
        checksum = tree_digest(payload, copied)
        preview = next((name for name in PREVIEW_FILES if (payload / name).is_file()), None)
        if kind == "auto":
            # Last-write times can lag until Windows closes the writer's handle.
            with deny_writers((slot / SAVE_FILE,)):
                pass
        if inventory(slot) != entries:
            raise SaverError("save_changing")
        payload.rename(destination)
        snapshot = Snapshot(
            snapshot_id, slot.name, identifier, (main or root).modified_ns / 1e9, time.time(),
            sum(entry.size for entry in copied.values() if not entry.folder),
            fingerprint, checksum, destination.relative_to(workspace.root).as_posix(), preview, kind,
        )
        try:
            workspace.db.add(snapshot)
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise
    return snapshot


def write_zip(workspace: Workspace, snapshot: Snapshot, destination: Path) -> None:
    source = workspace.snapshot_path(snapshot)
    with ZipFile(destination, "w", ZIP_DEFLATED, strict_timestamps=False) as archive:
        for relative in inventory(source):
            archive.write(source / relative, (Path(snapshot.slot_name) / relative).as_posix())
