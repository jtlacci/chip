// Chat: the shared chats, one thread, and the message box.

import { get, post } from "./api.js";
import { clock, el, icon, richText } from "./ui.js";

function message(item, me) {
  const mine = item.author === me.user_id;
  const agent = item.author === "chip";
  return el("div", { class: `message ${mine ? "mine" : agent ? "agent" : "other"}`, "data-message": item.id }, [
    !mine && !agent ? el("div", { class: "message-name", text: item.name || "" }) : null,
    el("div", { class: "bubble" }, [richText(item.text)]),
    el("time", { class: "message-time", datetime: item.time || "", text: clock(item.time) }),
  ]);
}

function composer(onSend) {
  const text = el("textarea", { "data-composer": "", rows: 1, maxlength: 4000, placeholder: "Message", "aria-label": "Message" });
  const files = el("input", { type: "file", "data-attach": "", class: "visually-hidden", id: "attach", multiple: true });
  const tray = el("div", { class: "tray" });
  const note = el("p", { class: "composer-note", role: "status", "aria-live": "polite" });
  const send = el("button", { class: "send", type: "button", "data-send": "", "aria-label": "Send" }, [icon("send")]);
  const attached = [];

  function showFiles() {
    tray.replaceChildren(...attached.map((file, index) => el("span", { class: "attachment", "data-attachment": file.name }, [
      file.name,
      el("button", { type: "button", class: "attachment-remove", "aria-label": `Remove ${file.name}`,
                     onclick: () => { attached.splice(index, 1); showFiles(); } }, [icon("close")]),
    ])));
  }

  files.addEventListener("change", () => {
    attached.push(...files.files);
    files.value = "";
    showFiles();
  });
  text.addEventListener("input", () => {
    text.style.height = "auto";
    text.style.height = `${Math.min(text.scrollHeight, 160)}px`;
  });
  async function submit() {
    const value = text.value.trim();
    if (!value && !attached.length) return;
    const names = attached.map((file) => file.name);
    const body = names.length ? `${value}${value ? "\n" : ""}${names.map((n) => `- ${n}`).join("\n")}` : value;
    send.disabled = true;
    const ok = await onSend(body);
    send.disabled = false;
    // A send that fails says so: the text stays, so nothing is lost.
    note.textContent = ok ? "" : "The message was not sent. Try again.";
    if (ok) {
      text.value = "";
      text.style.height = "auto";
      attached.length = 0;
      showFiles();
    }
  }
  send.addEventListener("click", submit);
  text.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  });
  return el("div", { class: "composer" }, [
    note,
    tray,
    el("div", { class: "composer-row" }, [
      el("label", { class: "icon-button", for: "attach", "data-attach-button": "", "aria-label": "Attach a file" }, [icon("plus")]),
      files, text, send,
    ]),
  ]);
}

export async function renderChat(root, me) {
  const page = el("section", { class: "page chat", "data-page": "chat" });
  const thread = el("div", { class: "thread", "aria-live": "polite" });
  const title = el("h1", { text: "Chat" });
  // The chat list is a menu in the sticky header, so it opens where the button is,
  // however far the thread is scrolled.
  const picker = el("div", { class: "chat-picker", id: "chat-picker", role: "menu", hidden: true });
  const chatsButton = el("button", { class: "pill", type: "button", "aria-expanded": "false", "aria-controls": "chat-picker",
                                     onclick: () => setPicker(picker.hidden) }, [icon("list"), "Chats"]);

  function setPicker(show) {
    picker.hidden = !show;
    chatsButton.setAttribute("aria-expanded", String(show));
    if (show) picker.querySelector("button")?.focus();
  }

  // Escape and a click outside close the menu. The page is replaced when the user
  // goes elsewhere, so each listener removes itself once its menu is gone.
  function onKey(event) {
    if (!picker.isConnected) return document.removeEventListener("keydown", onKey);
    if (event.key === "Escape" && !picker.hidden) {
      setPicker(false);
      chatsButton.focus();
    }
  }
  function onClick(event) {
    if (!picker.isConnected) return document.removeEventListener("click", onClick);
    if (!picker.hidden && !picker.contains(event.target) && !chatsButton.contains(event.target)) setPicker(false);
  }
  document.addEventListener("keydown", onKey);
  document.addEventListener("click", onClick);
  let current = null;
  let shown = new Set();   // the IDs of the messages in the thread
  let waiting = 0;         // the newest wait for CHIP's answer; a new chat or a new send ends the old one
  const typing = el("p", { class: "typing", "data-typing": "", role: "status", hidden: true }, ["CHIP is typing…"]);

  async function awaitReply(chat, since) {
    // CHIP answers on the server, a few seconds later: check every 2 s, for 90 s at most.
    const mine = ++waiting;
    typing.hidden = false;
    for (let i = 0; i < 45; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      if (mine !== waiting || !page.isConnected || current !== chat) return;
      const { status, data } = await get(`/api/chats/${encodeURIComponent(chat.id)}/messages`);
      if (status !== 200) continue;
      const fresh = (data.messages || []).filter((m) => !shown.has(m.id));
      for (const m of fresh) {
        shown.add(m.id);
        thread.append(message(m, me));
      }
      if (fresh.length) window.scrollTo(0, document.body.scrollHeight);
      if (fresh.some((m) => m.author === "chip" && String(m.time) >= String(since))) break;
    }
    if (mine === waiting) typing.hidden = true;
  }

  async function open(chat) {
    current = chat;
    waiting += 1;
    typing.hidden = true;
    title.textContent = chat.title || "Chat";
    setPicker(false);
    const { status, data } = await get(`/api/chats/${encodeURIComponent(chat.id)}/messages`);
    shown = new Set(status === 200 ? (data.messages || []).map((m) => m.id) : []);
    thread.replaceChildren(...(status === 200 ? (data.messages || []).map((m) => message(m, me))
      : [el("p", { class: "empty", text: "This chat did not load. Reload the page to try again." })]));
    if (status === 200 && !(data.messages || []).length) {
      thread.append(el("p", { class: "empty", text: "No messages yet. Ask CHIP something." }));
    }
    window.scrollTo(0, document.body.scrollHeight);
  }

  async function sendMessage(text) {
    if (!current) return false;
    const { status, data } = await post(`/api/chats/${encodeURIComponent(current.id)}/messages`, { text });
    if (status !== 201) return false;
    thread.querySelector(".empty")?.remove();
    shown.add(data.message.id);
    thread.append(message(data.message, me));
    window.scrollTo(0, document.body.scrollHeight);
    awaitReply(current, data.message.time);
    return true;
  }

  page.append(el("header", { class: "page-head" }, [chatsButton, title, picker]), thread, typing, composer(sendMessage));
  root.replaceChildren(page);
  const { status, data } = await get("/api/chats");
  const chats = status === 200 ? data.chats || [] : [];
  picker.replaceChildren(...chats.map((chat) => el("button", { class: "chat-choice", type: "button", role: "menuitem", onclick: () => open(chat) },
                                                   [chat.title || chat.id])));
  if (chats.length) {
    await open(chats[0]);
  } else {
    thread.append(el("p", { class: "empty", text: status === 200 ? "No chats yet." : "Chats did not load. Reload the page to try again." }));
  }
}
