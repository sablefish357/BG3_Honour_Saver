for (const form of document.querySelectorAll("[data-submit-once]")) {
    const button = form.querySelector("button[type=submit]");
    form.addEventListener("submit", () => { button.disabled = true; });
    window.addEventListener("pageshow", () => { button.disabled = false; });
}

// Keep polling when the CDN is unavailable. All forms are ordinary HTML forms.
if (!window.htmx && document.querySelector("[data-poll]")) {
    const poll = async () => {
        const panels = [...document.querySelectorAll("[data-poll]")];
        await Promise.allSettled(panels.map(async (panel) => {
            const response = await fetch(panel.dataset.poll, { cache: "no-store" });
            if (response.headers.get("HX-Refresh") === "true") {
                window.location.reload();
            } else if (response.ok) {
                const markup = await response.text();
                const focusedId = panel.contains(document.activeElement) ? document.activeElement.id : "";
                panel.outerHTML = markup;
                if (focusedId) {
                    document.getElementById(focusedId)?.focus({ preventScroll: true });
                }
            }
        }));
        window.setTimeout(poll, 5000);
    };
    window.setTimeout(poll, 5000);
}
