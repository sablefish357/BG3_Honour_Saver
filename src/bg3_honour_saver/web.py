import logging
import os
import re
import secrets
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Event, Lock
from typing import Annotated, Literal

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import Receive, Scope, Send

from .bg3 import (
    PREVIEW_FILES, SaverError, candidates, default_story, directory, game_id, is_link,
    separate_paths,
)
from .folder_picker import pick_folder
from .i18n import translate
from .snapshots import write_zip
from .watcher import Notice, Saver, View


ASSETS = Path(__file__).parent
Purpose = Literal["library", "story"]
Field = Annotated[str, Form()]
logger = logging.getLogger(__name__)


class TemporaryZipResponse(FileResponse):
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            Path(self.path).unlink(missing_ok=True)


def timestamp(value: float) -> str:
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")


def file_size(value: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:g} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024


def display_paths(value: Path | str) -> str:
    label = str(value)
    if os.name != "nt":
        return label

    for variable, root in (("LOCALAPPDATA", os.environ.get("LOCALAPPDATA")), ("USERPROFILE", str(Path.home()))):
        if not root:
            continue
        path = Path(root)
        windows_path = str(path).rstrip("\\")
        # Error messages can quote paths with escaped backslashes.
        for prefix in (windows_path, path.as_posix().rstrip("/"), windows_path.replace("\\", "\\\\")):
            pattern = re.escape(prefix) + r'''(?=$|[\\/"':])'''
            label = re.sub(pattern, f"%{variable}%", label, flags=re.IGNORECASE)
    return label


def create_app() -> FastAPI:
    saver = Saver()
    csrf = secrets.token_urlsafe(32)
    folder_dialog = Lock()
    folder_picker_shutdown = Event()
    shutdown_requested = Event()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            saver.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.folder_picker_shutdown = folder_picker_shutdown
    app.state.shutdown_requested = shutdown_requested
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1"])
    app.mount("/static", StaticFiles(directory=ASSETS / "static"), name="static")
    templates = Jinja2Templates(directory=ASSETS / "templates")
    templates.env.filters.update(timestamp=timestamp, file_size=file_size, display_paths=display_paths)

    @app.middleware("http")
    async def response_headers(request: Request, call_next):
        if request.url.path != "/" and request.headers.get("Sec-Fetch-Site") in {"cross-site", "same-site"}:
            response = Response(status_code=403)
        else:
            response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; "
            "style-src 'self' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def language_for(request: Request) -> str:
        language = request.query_params.get("lang") or request.cookies.get("language")
        return "en" if language == "en" else "zh"

    def render(request: Request, name: str, *, status: int = 200, **context):
        language = language_for(request)
        response = templates.TemplateResponse(
            request=request,
            name=name,
            status_code=status,
            headers={"Cache-Control": "no-store"},
            context={
                "lang": language,
                "t": lambda key: translate(language, key),
                "csrf": csrf,
                "game_id": game_id,
                "year": datetime.now().year,
                **context,
            },
        )
        if "lang" in request.query_params:
            response.set_cookie("language", language, max_age=31_536_000, httponly=True, samesite="strict")
        return response

    def check_token(token: str) -> None:
        if not secrets.compare_digest(token.encode("utf-8"), csrf.encode("ascii")):
            raise SaverError("invalid_request")

    def go(request: Request, name: str, **query) -> RedirectResponse:
        url = request.url_for(name).include_query_params(**query)
        return RedirectResponse(url, status_code=303)

    def game_folder(view: View) -> Path | None:
        try:
            story = directory(view.settings.story_path)
            separate_paths(view.workspace.root, story)
            return story
        except (OSError, SaverError):
            return None

    def setup(view: View | None) -> dict:
        if view is None:
            return {}
        story = game_folder(view)
        found = []
        warning = view.notice
        if story is not None:
            try:
                found = candidates(story)
            except OSError as error:
                warning = Notice("story_unavailable", str(error))
        return {"story": story, "candidates": found, "notice": warning, "default_story": default_story()}

    def dashboard(view: View | None) -> dict:
        if view is None:
            return {}
        if view.watcher:
            status = view.watcher.status
        else:
            status = Notice("paused" if view.settings.slot_name else "no_target")
        return {
            "watch_status": status,
            "history": view.workspace.db.history(),
            "restore_available": game_folder(view) is not None,
        }

    @app.exception_handler(SaverError)
    async def saver_error(request: Request, error: SaverError):
        return render(request, "index.html", status=400, page="error", message=Notice(error.key, error.detail))

    @app.exception_handler(OSError)
    @app.exception_handler(sqlite3.Error)
    async def file_error(request: Request, error: Exception):
        logger.warning("%s: %s", request.url.path, error)
        key = "not_directory" if isinstance(error, FileNotFoundError) else "local_error"
        return render(request, "index.html", status=400, page="error", message=Notice(key, str(error)))

    @app.get("/")
    def home(request: Request, restored: bool = False):
        view = saver.view
        return render(
            request, "index.html", page="home", view=view, restored=restored,
            **setup(view), **dashboard(view),
        )

    @app.post("/exit")
    def exit_program(request: Request, token: Field):
        check_token(token)
        response = render(request, "index.html", page="exiting")
        response.background = BackgroundTask(shutdown_requested.set)
        return response

    @app.get("/dashboard")
    def dashboard_fragment(request: Request, revision: str, story_available: bool):
        view = saver.view
        if view is None or view.revision != revision:
            return Response(headers={"HX-Refresh": "true"})
        context = dashboard(view)
        if context["restore_available"] != story_available:
            return Response(headers={"HX-Refresh": "true"})
        return render(request, "partials/dashboard.html", view=view, **context)

    @app.get("/candidates")
    def candidate_fragment(request: Request, revision: str):
        view = saver.view
        if view is None or view.revision != revision:
            return Response(headers={"HX-Refresh": "true"})
        return render(request, "partials/candidates.html", view=view, **setup(view))

    @app.post("/folders/pick")
    def pick_directory(
        request: Request, token: Field, purpose: Annotated[Purpose, Form()], revision: Field = "",
    ):
        check_token(token)
        if not folder_dialog.acquire(blocking=False):
            raise SaverError("picker_open")
        try:
            view = saver.view
            if purpose == "library":
                start = view.workspace.root if view else Path.home()
                title = "open_library"
            else:
                if view is None or view.revision != revision:
                    raise SaverError("stale_page")
                start = game_folder(view) or view.settings.story_path
                title = "change_story"
            if not start.is_dir():
                start = Path.home()
            selected = pick_folder(start, translate(language_for(request), title), folder_picker_shutdown)
            if selected is not None:
                if purpose == "library":
                    saver.open_library(selected)
                else:
                    saver.set_story(revision, selected)
        finally:
            folder_dialog.release()
        return go(request, "home")

    @app.post("/watch")
    def watch(request: Request, token: Field, revision: Field, slot_name: Field):
        check_token(token)
        saver.watch(revision, slot_name)
        return go(request, "home")

    @app.post("/pause")
    def pause(request: Request, token: Field, revision: Field):
        check_token(token)
        saver.pause(revision)
        return go(request, "home")

    @app.post("/settings/limit")
    def set_backup_limit(request: Request, token: Field, revision: Field, max_backups: Annotated[int, Form(ge=0)]):
        check_token(token)
        saver.set_limit(revision, max_backups)
        return go(request, "home")

    @app.post("/versions/{snapshot_id}/delete")
    def delete_version(request: Request, snapshot_id: str, token: Field, revision: Field):
        check_token(token)
        saver.delete(revision, snapshot_id)
        return go(request, "home")

    @app.get("/versions/{snapshot_id}/preview")
    def preview(snapshot_id: str):
        with saver.snapshot(snapshot_id) as (view, snapshot):
            if snapshot.preview not in PREVIEW_FILES:
                return Response(status_code=404)
            path = view.workspace.snapshot_path(snapshot) / snapshot.preview
            if not path.is_file() or is_link(path.lstat()):
                return Response(status_code=404)
            media_type = {".webp": "image/webp", ".png": "image/png"}.get(path.suffix, "image/jpeg")
            return Response(path.read_bytes(), media_type=media_type, headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/versions/{snapshot_id}/download")
    def download(snapshot_id: str):
        with saver.snapshot(snapshot_id) as (view, snapshot):
            with NamedTemporaryFile(suffix=".zip", delete=False) as stream:
                archive = Path(stream.name)
            try:
                write_zip(view.workspace, snapshot, archive)
                date = datetime.fromtimestamp(snapshot.saved_at).strftime("%Y%m%d-%H%M%S")
                return TemporaryZipResponse(
                    archive, media_type="application/zip", filename=f"HonourMode-{date}.zip",
                    headers={"Cache-Control": "no-store"},
                )
            except Exception:
                archive.unlink(missing_ok=True)
                raise

    @app.get("/versions/{snapshot_id}/restore")
    def restore_form(request: Request, snapshot_id: str):
        with saver.snapshot(snapshot_id) as (view, snapshot):
            if not snapshot.restorable:
                raise SaverError("damaged_backup", snapshot.id)
            story = game_folder(view)
            if story is None:
                raise SaverError("choose_story")
            return render(
                request, "index.html", page="restore", view=view, snapshot=snapshot,
                story=story, destination=story / snapshot.slot_name,
            )

    @app.post("/versions/{snapshot_id}/restore")
    def restore_version(
        request: Request, snapshot_id: str, token: Field, revision: Field, story: Field, confirmed: Field = "",
    ):
        check_token(token)
        if confirmed != "yes":
            raise SaverError("confirm_required")
        saver.restore(revision, snapshot_id, Path(story))
        return go(request, "home", restored=1)

    return app
