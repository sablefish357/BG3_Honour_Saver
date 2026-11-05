import logging
import webbrowser
from threading import Thread


def open_home(url: str) -> None:
    logger = logging.getLogger("uvicorn.error")
    try:
        if webbrowser.open_new_tab(url):
            return
    except (OSError, webbrowser.Error):
        pass
    logger.warning("Could not open the browser. Open %s manually.", url)


def main() -> None:
    import uvicorn

    from .web import create_app

    app = create_app()

    class BrowserServer(uvicorn.Server):
        async def startup(self, sockets=None) -> None:
            await super().startup(sockets=sockets)
            if self.started and not self.should_exit:
                url = f"http://{self.config.host}:{self.config.port}/"
                Thread(target=open_home, args=(url,), daemon=True).start()

        async def on_tick(self, counter: int) -> bool:
            if app.state.shutdown_requested.is_set():
                self.should_exit = True
            return await super().on_tick(counter)

        async def shutdown(self, sockets=None) -> None:
            app.state.folder_picker_shutdown.set()
            await super().shutdown(sockets=sockets)

    config = uvicorn.Config(app, host="127.0.0.1", port=8765, proxy_headers=False)
    BrowserServer(config).run()
