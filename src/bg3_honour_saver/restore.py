import shutil
import time
from pathlib import Path
from tempfile import mkdtemp

from .bg3 import SAVE_FILE, SaverError, directory, honour_file, separate_paths, valid_component
from .database import Snapshot
from .snapshots import capture, deny_writers, file_digest, inventory, stable_copy, tree_digest
from .workspace import Workspace


def restore_snapshot(workspace: Workspace, story: Path, snapshot: Snapshot) -> None:
    story = directory(story)
    separate_paths(workspace.root, story)
    if not valid_component(snapshot.slot_name):
        raise SaverError("invalid_save", snapshot.slot_name)
    if not snapshot.restorable:
        raise SaverError("damaged_backup", snapshot.id)
    source = workspace.snapshot_path(snapshot)
    target = story / snapshot.slot_name
    staging = Path(mkdtemp(prefix=".bg3-honour-saver-restore-", dir=story))
    incoming = staging / "incoming"
    previous = staging / "previous"
    installed = False
    displaced = False
    preserve_staging = False
    try:
        honour_file(source)
        copied = stable_copy(source, incoming, inventory(source))
        if tree_digest(incoming, copied) != snapshot.tree_digest:
            raise SaverError("damaged_backup", snapshot.id)
        if file_digest(incoming / SAVE_FILE) != snapshot.fingerprint:
            raise SaverError("damaged_backup", snapshot.id)

        if target.exists() or target.is_symlink() or target.is_junction():
            before = inventory(target)
            time.sleep(1)
            files = (target / relative for relative, entry in before.items() if not entry.folder)
            with deny_writers(files):
                capture(workspace, target, kind="before_restore", expected=before)
            # Windows may refuse to move a directory while its files have open handles.
            if inventory(target) != before:
                raise SaverError("save_changing")
            target.rename(previous)
            displaced = True
            # A rename can change the directory entry's own metadata.
            after_move = inventory(previous)
            after_move[Path(".")] = before[Path(".")]
            if after_move != before:
                raise SaverError("save_changing")

        if target.exists() or target.is_symlink() or target.is_junction():
            raise SaverError("save_changing")
        incoming.rename(target)
        installed = True
        workspace.db.set_baseline(snapshot.slot_name, snapshot.fingerprint)
    except Exception:
        try:
            if installed:
                target.rename(incoming)
            if displaced:
                if target.exists() or target.is_symlink() or target.is_junction():
                    raise SaverError("save_changing")
                previous.rename(target)
        except (OSError, SaverError) as error:
            preserve_staging = True
            raise SaverError("rollback_failed", str(staging)) from error
        raise
    finally:
        if not preserve_staging:
            shutil.rmtree(staging, ignore_errors=True)
