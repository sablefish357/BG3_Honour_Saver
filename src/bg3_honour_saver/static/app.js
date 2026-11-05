document.addEventListener("submit", (event) => {
    if (event.target.matches("[data-submit-once]")) {
        event.target.querySelector("button[type=submit]").disabled = true;
    }
});

document.addEventListener("invalid", (event) => {
    const checkbox = event.target;
    if (checkbox.matches("[data-restore-confirm]")) {
        event.preventDefault();
        checkbox.closest(".confirm").classList.add("invalid");
        checkbox.setAttribute("aria-invalid", "true");
        checkbox.focus();
    }
}, true);

document.addEventListener("change", (event) => {
    const checkbox = event.target;
    if (checkbox.matches("[data-restore-confirm]") && checkbox.checked) {
        checkbox.closest(".confirm").classList.remove("invalid");
        checkbox.removeAttribute("aria-invalid");
    }
});

window.addEventListener("pageshow", () => {
    for (const button of document.querySelectorAll("[data-submit-once] button[type=submit]")) {
        button.disabled = false;
    }
});

function showMissingPreview(img) {
    const label = document.createElement("span");
    label.textContent = img.parentElement.dataset.noPreview;
    img.replaceWith(label);
}

document.addEventListener("error", (event) => {
    if (event.target.matches?.(".preview img")) {
        showMissingPreview(event.target);
    }
}, true);

for (const img of document.querySelectorAll(".preview img")) {
    if (img.complete && img.naturalWidth === 0) {
        showMissingPreview(img);
    }
}

// Keep polling when the CDN is unavailable. All forms are ordinary HTML forms.
if (!window.htmx && document.querySelector("[data-poll]")) {
    const poll = async () => {
        if (window.htmx) {
            return;
        }
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
