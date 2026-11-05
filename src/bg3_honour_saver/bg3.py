import ntpath
import os
import stat
from dataclasses import dataclass
from pathlib import Path


SAVE_FILE = "HonourMode.lsv"
HONOUR_SUFFIX = "__HonourMode"
PREVIEW_FILES = ("HonourMode.webp", "HonourMode.png", "HonourMode.jpg", "HonourMode.jpeg")


class SaverError(Exception):
    def __init__(self, key: str, detail: str = ""):
        super().__init__(detail or key)
        self.key = key
        self.detail = detail


def default_story() -> Path:
    appdata = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return appdata / "Larian Studios" / "Baldur's Gate 3" / "PlayerProfiles" / "Public" / "Savegames" / "Story"


def valid_component(name: str) -> bool:
    return (
        bool(name) and name not in {".", ".."}
        and "/" not in name and "\\" not in name and not ntpath.isreserved(name)
    )


def is_link(info: os.stat_result) -> bool:
    # Windows marks junctions as name surrogates, unlike cloud placeholders.
    name_surrogate = 0x20000000
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_reparse_tag", 0) & name_surrogate)


def directory(path: Path) -> Path:
    if not path.is_absolute():
        raise SaverError("absolute_path")
    if os.name == "nt" and path.drive.startswith("\\\\"):
        raise SaverError("local_path")
    try:
        resolved = path.resolve(strict=True)
    except (ValueError, RuntimeError) as error:
        raise SaverError("not_directory", str(path)) from error
    if os.name == "nt" and resolved.drive.startswith("\\\\"):
        raise SaverError("local_path")
    if not resolved.is_dir():
        raise SaverError("not_directory", str(path))
    return resolved


def separate_paths(library: Path, story: Path) -> None:
    if library.is_relative_to(story) or story.is_relative_to(library):
        raise SaverError("separate_paths")


def honour_file(slot: Path) -> Path:
    info = slot.lstat()
    if is_link(info) or not stat.S_ISDIR(info.st_mode):
        raise SaverError("invalid_save", slot.name)
    file = slot / SAVE_FILE
    info = file.lstat()
    if is_link(info) or not stat.S_ISREG(info.st_mode):
        raise SaverError("invalid_save", slot.name)
    return file


def game_id(slot_name: str) -> str:
    if slot_name.casefold().endswith(HONOUR_SUFFIX.casefold()):
        prefix = slot_name[:-len(HONOUR_SUFFIX)]
        if valid_component(prefix):
            return prefix
    return slot_name


@dataclass(frozen=True)
class Candidate:
    name: str
    game_id: str
    modified_at: float


def candidates(story: Path) -> list[Candidate]:
    found = []
    for slot in story.iterdir():
        if not valid_component(slot.name):
            continue
        try:
            info = honour_file(slot).stat()
        except (OSError, SaverError):
            continue
        found.append(Candidate(slot.name, game_id(slot.name), info.st_mtime))
    return sorted(found, key=lambda item: item.modified_at, reverse=True)
