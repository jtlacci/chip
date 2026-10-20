// The feed: posts CHIP made from the feed instructions, and the instructions dialog.

import { get, put } from "./api.js";
import { clock, el, icon, richText, timeAgo } from "./ui.js";

function card(data) {
  if (!data || typeof data !== "object") return null;
  if (data.type === "calendar") {
    return el("div", { class: "card calendar" }, [
      el("div", { class: "calendar-day", text: data.day || "" }),
      ...(data.rows || []).map((row) =>
        el("div", { class: row.now ? "calendar-row now" : "calendar-row" }, [
          el("span", { class: "calendar-time", text: row.time || "" }),
          el("span", { class: "calendar-title", text: row.title || "" }),
          row.note ? el("span", { class: "calendar-note", text: row.note }) : null,
        ])),
    ]);
  }
  if (data.type === "image" && typeof data.src === "string") {
    return el("figure", { class: "card image" }, [
      el("img", { src: data.src, alt: data.alt || "", width: data.width || 640, height: data.height || 360, loading: "lazy" }),
    ]);
  }
  if (data.type === "link" && typeof data.url === "string" && /^https?:\/\//.test(data.url)) {
    return el("a", { class: "card link", href: data.url, target: "_blank", rel: "noopener noreferrer" }, [
      el("span", { class: "link-title", text: data.title || data.url }),
      el("span", { class: "link-host", text: new URL(data.url).host }),
    ]);
  }
  return null;
}

function post(item, openChat) {
  const liked = { on: false };
  const like = el("button", { class: "action", type: "button", "aria-pressed": "false", "aria-label": "Like" }, [icon("heart")]);
  like.addEventListener("click", () => {
    liked.on = !liked.on;
    like.setAttribute("aria-pressed", String(liked.on));
  });
  return el("article", { class: "post", "data-post": item.id }, [
    el("div", { class: "post-icon", "aria-hidden": "true", text: item.icon || "✦" }),
    el("div", { class: "post-main" }, [
      el("header", { class: "post-head" }, [
        el("h2", { class: "post-title", "data-post-title": "", text: item.title || "Untitled" }),
        el("time", { class: "post-time", datetime: item.time || "", title: clock(item.time), text: timeAgo(item.time) }),
      ]),
      richText(item.body),
      card(item.card),
      el("div", { class: "post-actions" }, [
        like,
        el("button", { class: "action", type: "button", onclick: () => openChat(item) }, [icon("discuss"), "Discuss"]),
        item.action ? el("button", { class: "action primary", type: "button" }, [item.action.label || "Do it"]) : null,
      ]),
    ]),
  ]);
}

function instructionsDialog(me) {
  const canEdit = me.role === "owner" || me.role === "admin";
  const text = el("textarea", { "data-instructions-text": "", rows: 7, maxlength: 4000, readonly: !canEdit,
                                "aria-label": "Feed instructions" });
  const status = el("p", { class: "dialog-status", role: "status" });
  const save = el("button", { class: "button primary", type: "button", "data-instructions-save": "", disabled: !canEdit }, ["Save"]);
  const cancel = el("button", { class: "button", type: "button", "data-instructions-cancel": "" }, ["Cancel"]);
  const dialog = el("dialog", { class: "dialog", "data-instructions": "", "aria-labelledby": "instructions-title" }, [
    el("h2", { id: "instructions-title", text: "Feed instructions" }),
    el("p", { class: "dialog-help", text: canEdit
      ? "CHIP makes the feed from these instructions. A change applies to new posts."
      : "CHIP makes the feed from these instructions. Owners can change them." }),
    text, status,
    el("div", { class: "dialog-buttons" }, [cancel, save]),
  ]);
  cancel.addEventListener("click", () => dialog.close());
  save.addEventListener("click", async () => {
    save.disabled = true;
    const { status: code } = await put("/api/feed/instructions", { text: text.value });
    save.disabled = false;
    if (code === 200) {
      dialog.close();
    } else {
      status.textContent = code === 400 ? "Write 1 to 4000 characters." : "The instructions were not saved. Try again.";
    }
  });
  return {
    dialog,
    async open() {
      const { status: code, data } = await get("/api/feed/instructions");
      text.value = code === 200 ? data.text || "" : "";
      status.textContent = "";
      dialog.showModal();
    },
  };
}

export async function renderFeed(root, me, go) {
  const instructions = instructionsDialog(me);
  const page = el("section", { class: "page feed", "data-page": "feed" }, [
    el("header", { class: "page-head" }, [
      el("h1", { text: "Feed" }),
      el("button", { class: "icon-button", type: "button", "data-instructions-open": "", "aria-label": "Feed instructions",
                     onclick: () => instructions.open() }, [icon("sliders")]),
    ]),
  ]);
  const list = el("div", { class: "posts" });
  page.append(list, instructions.dialog);
  root.replaceChildren(page);
  const { status, data } = await get("/api/feed");
  if (status !== 200) {
    list.append(el("p", { class: "empty", text: "The feed did not load. Reload the page to try again." }));
    return;
  }
  const posts = data.posts || [];
  if (!posts.length) {
    list.append(el("p", { class: "empty", text: "No posts yet. CHIP posts here when it finds something for you." }));
  }
  list.append(...posts.map((item) => post(item, () => go("/chat"))));
}
