import logging
import os
import subprocess
import sys
from pathlib import Path
from threading import Event

from .bg3 import SaverError


logger = logging.getLogger(__name__)

# Tk opens the Windows folder dialog. A short-lived process keeps its UI loop
# and any working directory changes separate from the web server.
DIALOG = """
import sys
from tkinter import Tk, filedialog

owner = Tk()
try:
    owner.withdraw()
    owner.overrideredirect(True)
    owner.geometry("1x1+0+0")
    owner.attributes("-alpha", 0.0)
    owner.attributes("-topmost", True)
    owner.deiconify()
    owner.update_idletasks()
    path = filedialog.askdirectory(
        parent=owner, initialdir=sys.argv[1], title=sys.argv[2], mustexist=True
    )
    if path:
        sys.stdout.buffer.write(path.encode("utf-8"))
finally:
    owner.destroy()
"""


def pick_folder(start: Path, title: str, closing: Event) -> Path | None:
    if os.name != "nt":
        raise SaverError("picker_failed")
    if closing.is_set():
        return None

    with subprocess.Popen(
        [sys.executable, "-I", "-c", DIALOG, str(start), title],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    ) as process:
        while not closing.is_set():
            try:
                stdout, stderr = process.communicate(timeout=0.25)
            except subprocess.TimeoutExpired:
                continue
            if process.returncode:
                logger.warning("Windows folder dialog failed: %s", stderr.decode("utf-8", errors="replace"))
                raise SaverError("picker_failed")
            return Path(stdout.decode("utf-8")) if stdout else None

        process.terminate()
        process.communicate()
        return None
