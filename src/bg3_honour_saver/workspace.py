import json
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

from .bg3 import SaverError, default_story, directory, is_link, valid_component
from .database import Database, Snapshot


@dataclass(frozen=True)
class Settings:
    story: Path | None = None
    slot_name: str | None = None
    enabled: bool = False

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
            if story is not None and (not isinstance(story, str) or not Path(story).is_absolute()):
                raise ValueError
            if slot is not None and (not isinstance(slot, str) or not valid_component(slot) or story is None):
                raise ValueError
            if not isinstance(enabled, bool) or (enabled and slot is None):
                raise ValueError
        except (OSError, ValueError, AttributeError) as error:
            raise SaverError("settings_unreadable", str(self.settings_path)) from error
        return Settings(Path(story) if story else None, slot, enabled)

    def write_settings(self, settings: Settings) -> None:
        data = {
            "story": str(settings.story) if settings.story else None,
            "slot_name": settings.slot_name,
            "enabled": settings.enabled,
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

    def snapshot_path(self, snapshot: Snapshot) -> Path:
        path = self.root / snapshot.relative_path
        if not path.is_relative_to(self.backups) or path.resolve() != path or not path.is_dir():
            raise SaverError("missing_backup", snapshot.id)
        return path
