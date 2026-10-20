// The app shell: navigation, the current page, and the agent panel.

import { get } from "./api.js";
import { avatar } from "./avatar.js";
import { renderPanel } from "./agent.js";
import { renderChat } from "./chat.js";
import { renderFeed } from "./feed.js";
import { el, icon } from "./ui.js";

const PAGES = { "/feed": "feed", "/chat": "chat", "/agent": "agent" };
const rail = document.getElementById("rail");
const main = document.getElementById("main");
const panel = document.getElementById("panel");
let me = null;

function navigation(current) {
  const link = (name, label, glyph) =>
    el("a", { class: "nav-link", href: `/${name}`, "data-nav": name, "aria-current": current === name ? "page" : false,
              onclick: (event) => { event.preventDefault(); go(`/${name}`); } }, [icon(glyph), el("span", { text: label })]);
  rail.replaceChildren(
    el("a", { class: "rail-avatar", href: "/agent", "aria-label": "CHIP", onclick: (event) => { event.preventDefault(); go("/agent"); } },
       [avatar(40)]),
    link("chat", "Chat", "chat"),
    link("feed", "Feed", "feed"),
    link("agent", "CHIP", "agent"),
  );
}

async function render() {
  document.body.removeAttribute("data-ready");
  const path = location.pathname in PAGES ? location.pathname : "/feed";
  if (path !== location.pathname) history.replaceState(null, "", path);
  const name = PAGES[path];
  document.title = { feed: "Feed", chat: "Chat", agent: "CHIP" }[name] + " · CHIP";
  navigation(name);
  const work = [];
  if (name === "feed") work.push(renderFeed(main, me, go));
  if (name === "chat") work.push(renderChat(main, me));
  if (name === "agent") {
    work.push(renderPanel(main, me, { asPage: true }));
    panel.replaceChildren();
  } else {
    work.push(renderPanel(panel, me));
  }
  await Promise.all(work);
  document.body.setAttribute("data-ready", "");
}

export function go(path) {
  if (path !== location.pathname) history.pushState(null, "", path);
  window.scrollTo(0, 0);
  render();
}

function signIn() {
  rail.replaceChildren();
  panel.replaceChildren();
  main.replaceChildren(el("section", { class: "page signin" }, [
    avatar(96),
    el("h1", { text: "CHIP" }),
    el("p", { text: "Sign in to see the feed and the chats you share with CHIP." }),
    el("a", { class: "button primary", href: "/auth/google", "data-signin": "" }, ["Sign in with Google"]),
  ]));
  document.body.setAttribute("data-ready", "");
}

async function start() {
  const { status, data } = await get("/api/me");
  if (status !== 200) {
    signIn();
    return;
  }
  me = data;
  window.addEventListener("popstate", render);
  await render();
}

start();
