import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Condition, Lock, Thread
from time import monotonic
from uuid import uuid4

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .bg3 import SaverError, default_story, directory, honour_file, separate_paths, valid_component
from .database import Snapshot
from .restore import restore_snapshot
from .snapshots import capture, inventory
from .workspace import Settings, Workspace


QUIET_SECONDS = 2
SCAN_SECONDS = 30
RETRY_SECONDS = 5
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Notice:
    key: str
    detail: str = ""


class SaveEvents(FileSystemEventHandler):
    def __init__(self, watcher: "SaveWatcher"):
        self.watcher = watcher

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.event_type not in {"created", "modified", "moved", "deleted"}:
            return
        for name in (event.src_path, event.dest_path):
            if name and Path(name).is_relative_to(self.watcher.slot):
                self.watcher.schedule()
                return


class SaveWatcher:
    def __init__(self, workspace: Workspace, story: Path, slot_name: str, operation: Lock, max_backups: int):
        self.workspace = workspace
        self.story = story
        self.slot = story / slot_name
        self.operation = operation
        self.max_backups = max_backups
        self.status = Notice("starting")
        self._condition = Condition()
        self._due: float | None = monotonic() + QUIET_SECONDS
        self._revision = 0
        self._closed = False
        self._observer = None
        self._thread = Thread(target=self._run, name="bg3-save-watcher", daemon=True)
        self._thread.start()

    def schedule(self) -> None:
        with self._condition:
            self._revision += 1
            self._due = monotonic() + QUIET_SECONDS
            self._condition.notify()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify()
        self._thread.join()

    def _stop_observer(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join()
            self._observer = None

    def _ensure_observer(self) -> Notice:
        if self._observer is not None and self._observer.is_alive():
            if self._observer.emitters and all(emitter.is_alive() for emitter in self._observer.emitters):
                return Notice("watching")
        self._stop_observer()
        observer = Observer()
        try:
            observer.schedule(SaveEvents(self), str(self.story), recursive=True)
            observer.start()
        except (OSError, RuntimeError) as error:
            if observer.is_alive():
                observer.stop()
                observer.join()
            return Notice("polling", str(error))
        self._observer = observer
        return Notice("watching")

    def _next_scan(self, next_periodic: float) -> int | None:
        with self._condition:
            while not self._closed:
                due = self._due if self._due is not None else next_periodic
                delay = due - monotonic()
                if delay <= 0:
                    self._due = None
                    return self._revision
                self._condition.wait(delay)
        return None

    def _run(self) -> None:
        try:
            self.status = self._ensure_observer()
            next_periodic = monotonic() + SCAN_SECONDS
            while (revision := self._next_scan(next_periodic)) is not None:
                with self.operation:
                    with self._condition:
                        if self._closed:
                            return
                        if revision != self._revision:
                            continue
                    next_periodic = monotonic() + SCAN_SECONDS
                    ready = self._ensure_observer()
                    try:
                        honour_file(self.slot)
                        before = inventory(self.slot)
                        with self._condition:
                            interrupted = self._condition.wait_for(
                                lambda: self._closed or revision != self._revision, timeout=0.75
                            )
                        if interrupted:
                            continue
                        self.status = Notice("backing_up")
                        capture(self.workspace, self.slot, expected=before)
                        self.workspace.prune_backups(self.max_backups)
                    except FileNotFoundError:
                        self.status = Notice("waiting_save")
                    except SaverError as error:
                        self.status = Notice(error.key, error.detail)
                    except (OSError, sqlite3.Error) as error:
                        logger.warning("Could not back up %s: %s", self.slot, error)
                        self.status = Notice("backup_error", str(error))
                    else:
                        self.status = ready
                        continue
                    with self._condition:
                        if self._due is None:
                            self._due = monotonic() + RETRY_SECONDS
        finally:
            self._stop_observer()


@dataclass(frozen=True)
class View:
    workspace: Workspace
    settings: Settings
    revision: str
    watcher: SaveWatcher | None = None
    notice: Notice | None = None


class Saver:
    def __init__(self):
        self.view: View | None = None
        self._lock = Lock()
        self._operation = Lock()

    def _current(self, revision: str) -> View:
        if self.view is None or self.view.revision != revision:
            raise SaverError("stale_page")
        return self.view

    def close(self) -> None:
        with self._lock:
            self._stop_watcher()
            self.view = None

    def _stop_watcher(self) -> None:
        if self.view and self.view.watcher:
            self.view.watcher.close()

    def _activate(self, workspace: Workspace, settings: Settings, notice: Notice | None = None) -> None:
        self._stop_watcher()
        watcher = None
        if settings.story is not None:
            try:
                story = directory(settings.story)
                separate_paths(workspace.root, story)
            except (OSError, SaverError) as error:
                notice = Notice("story_unavailable", str(error))
            else:
                settings = replace(settings, story=story)
                if settings.enabled and settings.slot_name:
                    watcher = SaveWatcher(workspace, story, settings.slot_name, self._operation, settings.max_backups)
        self.view = View(workspace, settings, uuid4().hex, watcher, notice)

    def open_library(self, path: Path) -> None:
        with self._lock:
            root = directory(path)
            default = default_story().resolve()
            separate_paths(root, default)
            if self.view and self.view.settings.story:
                separate_paths(root, self.view.settings.story)
            with self._operation:
                workspace = Workspace.open(root)
                notice = None
                try:
                    settings = workspace.read_settings()
                except SaverError as error:
                    settings = Settings()
                    notice = Notice(error.key, error.detail)
            self._activate(workspace, settings, notice)
            with self._operation:
                workspace.prune_backups(settings.max_backups)

    def set_story(self, revision: str, path: Path) -> None:
        with self._lock:
            view = self._current(revision)
            story = directory(path)
            separate_paths(view.workspace.root, story)
            settings = view.settings
            if settings.story != story:
                settings = replace(settings, story=story, slot_name=None, enabled=False)
            view.workspace.write_settings(settings)
            self._activate(view.workspace, settings)

    def watch(self, revision: str, slot_name: str) -> None:
        with self._lock:
            view = self._current(revision)
            if not valid_component(slot_name):
                raise SaverError("invalid_save", slot_name)
            story = directory(view.settings.story_path)
            separate_paths(view.workspace.root, story)
            honour_file(story / slot_name)
            settings = replace(view.settings, story=story, slot_name=slot_name, enabled=True)
            view.workspace.write_settings(settings)
            self._activate(view.workspace, settings)

    def pause(self, revision: str) -> None:
        with self._lock:
            view = self._current(revision)
            settings = replace(view.settings, enabled=False)
            view.workspace.write_settings(settings)
            self._activate(view.workspace, settings)

    def set_limit(self, revision: str, limit: int) -> None:
        with self._lock:
            view = self._current(revision)
            if limit < 0:
                raise SaverError("invalid_limit")
            settings = replace(view.settings, max_backups=limit)
            with self._operation:
                view.workspace.write_settings(settings)
                if view.watcher:
                    view.watcher.max_backups = limit
                self.view = replace(view, settings=settings, revision=uuid4().hex)
                view.workspace.prune_backups(limit)

    @contextmanager
    def snapshot(self, snapshot_id: str, revision: str | None = None):
        with self._lock:
            view = self._current(revision) if revision is not None else self.view
            if view is None:
                raise SaverError("missing_backup", snapshot_id)
            with self._operation:
                snapshot = view.workspace.db.get(snapshot_id)
                if snapshot is None or not valid_component(snapshot.slot_name):
                    raise SaverError("missing_backup", snapshot_id)
                yield view, snapshot

    def delete(self, revision: str, snapshot_id: str) -> None:
        with self.snapshot(snapshot_id, revision) as (view, snapshot):
            view.workspace.recover_deletions()
            view.workspace.remove_snapshot(snapshot)

    def restore(self, revision: str, snapshot_id: str, confirmed_story: Path) -> Snapshot:
        with self.snapshot(snapshot_id, revision) as (view, snapshot):
            story = directory(view.settings.story_path)
            if story != confirmed_story:
                raise SaverError("stale_page")
            if view.watcher is None:
                restore_snapshot(view.workspace, story, snapshot, max_backups=view.settings.max_backups)
            else:
                watcher = view.watcher
                previous = watcher.status
                watcher.status = Notice("restoring")
                try:
                    restore_snapshot(view.workspace, story, snapshot, max_backups=view.settings.max_backups)
                finally:
                    watcher.status = previous
                    watcher.schedule()
            self.view = replace(view, revision=uuid4().hex)
            return snapshot
