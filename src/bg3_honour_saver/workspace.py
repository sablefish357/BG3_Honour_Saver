import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID

from .bg3 import SaverError, default_story, directory, is_link, valid_component
from .database import Database, Snapshot


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    story: Path | None = None
    slot_name: str | None = None
    enabled: bool = False
    max_backups: int = 0

    @property
    def story_path(self) -> Path:
        return self.story or default_story()


@dataclass(frozen=True)
class Workspace:
    root: Path
    db: Database

    @classmethod
    def open(cls, path: Path) -> "Workspace":
        root = directory(path)
        db_path = root / "backups.sqlite3"
        for item in (root / "backups", db_path, root / "settings.json"):
            try:
                info = item.lstat()
            except FileNotFoundError:
                continue
            if is_link(info):
                raise SaverError("linked_path", str(item))
        (root / "backups").mkdir(exist_ok=True)
        db = Database(db_path)
        db.initialize()
        workspace = cls(root, db)
        if not workspace.settings_path.exists():
            workspace.write_settings(Settings())
        return workspace

    @property
    def settings_path(self) -> Path:
        return self.root / "settings.json"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    def read_settings(self) -> Settings:
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            story = data.get("story")
            slot = data.get("slot_name")
            enabled = data.get("enabled", False)
            max_backups = data.get("max_backups", 0)
            if story is not None and (not isinstance(story, str) or not Path(story).is_absolute()):
                raise ValueError
            if slot is not None and (not isinstance(slot, str) or not valid_component(slot) or story is None):
                raise ValueError
            if not isinstance(enabled, bool) or (enabled and slot is None):
                raise ValueError
            if type(max_backups) is not int or max_backups < 0:
                raise ValueError
        except (OSError, ValueError, AttributeError) as error:
            raise SaverError("settings_unreadable", str(self.settings_path)) from error
        return Settings(Path(story) if story else None, slot, enabled, max_backups)

    def write_settings(self, settings: Settings) -> None:
        data = {
            "story": str(settings.story) if settings.story else None,
            "slot_name": settings.slot_name,
            "enabled": settings.enabled,
            "max_backups": settings.max_backups,
        }
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.root, delete=False) as stream:
            temp = Path(stream.name)
            try:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
                stream.close()
                temp.replace(self.settings_path)
            finally:
                stream.close()
                temp.unlink(missing_ok=True)

    def snapshot_path(self, snapshot: Snapshot, *, required: bool = True) -> Path:
        path = self.root / snapshot.relative_path
        if path.parent.parent != self.backups or path.resolve() != path:
            raise SaverError("missing_backup", snapshot.id)
        if required and not path.is_dir():
            raise SaverError("missing_backup", snapshot.id)
        return path

    def remove_snapshot(self, snapshot: Snapshot) -> None:
        path = self.snapshot_path(snapshot, required=False)
        removed = path.with_name(f".deleted-{snapshot.id}")
        moved = path.exists()
        if moved:
            path.rename(removed)
        try:
            self.db.remove(snapshot.id)
        except Exception:
            if moved:
                removed.rename(path)
            raise
        # The observed fingerprint remains the baseline after an intentional deletion.
        # This prevents the watcher from immediately recreating the same version.
        if moved:
            try:
                shutil.rmtree(removed)
            except OSError as error:
                logger.warning("Backup removed from history; file cleanup will retry: %s", error)

    def recover_deletions(self) -> None:
        for removed in self.backups.glob("*/.deleted-*"):
            snapshot_id = removed.name.removeprefix(".deleted-")
            try:
                UUID(hex=snapshot_id)
            except ValueError:
                continue
            if removed.resolve() != removed or is_link(removed.lstat()) or not removed.is_dir():
                continue
            snapshot = self.db.get(snapshot_id)
            if snapshot is not None:
                # An interrupted deletion before its database commit is rolled back.
                original = self.snapshot_path(snapshot, required=False)
                if original.parent == removed.parent:
                    removed.rename(original)
            else:
                try:
                    shutil.rmtree(removed)
                except OSError as error:
                    logger.warning("Could not finish deleting %s: %s", removed, error)

    def prune_backups(self, limit: int) -> None:
        self.recover_deletions()
        if limit:
            for snapshot in reversed(self.db.history()[limit:]):
                self.remove_snapshot(snapshot)
