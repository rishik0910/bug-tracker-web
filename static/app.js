document.addEventListener("DOMContentLoaded", () => {
    window.setTimeout(() => {
        document.querySelectorAll(".flash").forEach((flash) => {
            flash.style.transition = "opacity 0.25s ease";
            flash.style.opacity = "0";
            window.setTimeout(() => flash.remove(), 250);
        });
    }, 3500);

    const launcher = document.getElementById("assistant-launcher");
    const widget = document.getElementById("assistant-widget");
    const closeButton = document.getElementById("assistant-close");
    const form = document.getElementById("assistant-form");
    const input = document.getElementById("assistant-input");
    const body = document.getElementById("assistant-body");
    const logoutLink = document.querySelector(".sidebar-logout-button");

    const appendMessage = (text, sender) => {
        if (!body) return;
        const node = document.createElement("div");
        node.className = `assistant-message assistant-message-${sender}`;
        node.textContent = text;
        body.appendChild(node);
        body.scrollTop = body.scrollHeight;
    };

    const toggleWidget = (open) => {
        if (!widget) return;
        widget.classList.toggle("open", open);
        widget.setAttribute("aria-hidden", open ? "false" : "true");
        if (open && input) input.focus();
    };

    launcher?.addEventListener("click", () => {
        toggleWidget(!widget.classList.contains("open"));
    });

    closeButton?.addEventListener("click", () => {
        toggleWidget(false);
    });

    logoutLink?.addEventListener("click", (event) => {
        event.preventDefault();
        const target = logoutLink.getAttribute("href") || "/logout";
        window.location.assign(target);
    });

    document.querySelectorAll("[data-assistant-prompt]").forEach((button) => {
        button.addEventListener("click", () => {
            if (input) {
                input.value = button.dataset.assistantPrompt || "";
                input.focus();
            }
        });
    });

    form?.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!input) return;

        const message = input.value.trim();
        if (!message) return;

        appendMessage(message, "user");
        input.value = "";

        try {
            const response = await fetch("/assistant/query", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message })
            });
            const data = await response.json();
            appendMessage(data.reply || data.error || "I could not answer that just now.", "bot");
        } catch (error) {
            appendMessage("The assistant is temporarily unavailable.", "bot");
        }
    });
});
