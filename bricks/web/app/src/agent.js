// The agent panel: CHIP, its status, what it did, and what it asked to do.

import { get } from "./api.js";
import { avatar } from "./avatar.js";
import { el, icon, timeAgo } from "./ui.js";

function item(entry, kind) {
  const attrs = kind === "activity" ? { "data-activity": entry.id } : { "data-approval": entry.id };
  return el("li", { class: "log-item", ...attrs }, [
    el("span", { class: "log-icon" }, [icon(kind === "activity" ? "check" : "shield")]),
    el("div", { class: "log-text" }, [
      el("p", { class: "log-title", text: entry.title || "" }),
      entry.detail ? el("p", { class: "log-detail", text: entry.detail }) : null,
      el("p", { class: "log-time" }, [
        kind === "approval" && entry.state ? `${entry.state[0].toUpperCase()}${entry.state.slice(1)} · ` : "",
        timeAgo(entry.time),
      ]),
    ]),
  ]);
}

export async function renderPanel(root, me, { asPage = false } = {}) {
  const tabs = el("div", { class: "tabs", role: "tablist" });
  const list = el("ul", { class: "log" });
  const panel = el("section", { class: asPage ? "page agent agent-page" : "agent", ...(asPage ? { "data-page": "agent" } : {}) }, [
    el("div", { class: "agent-card" }, [
      avatar(asPage ? 96 : 72),
      el("p", { class: "agent-name", text: "CHIP" }),
      el("p", { class: "agent-status" }, [el("span", { class: "dot", "aria-hidden": "true" }), "Connected"]),
    ]),
    tabs, list,
  ]);
  root.replaceChildren(panel);
  if (me.role === "guest") {
    list.append(el("li", { class: "empty", text: "Owners can see what CHIP did and what it asked to do." }));
    return;
  }
  const [activity, approvals] = await Promise.all([get("/api/activity"), get("/api/approvals")]);
  const sets = {
    activity: activity.status === 200 ? activity.data.activity || [] : null,
    approvals: approvals.status === 200 ? approvals.data.approvals || [] : null,
  };
  function show(name) {
    for (const tab of tabs.children) tab.setAttribute("aria-selected", String(tab.dataset.tab === name));
    const entries = sets[name];
    if (entries === null) {
      list.replaceChildren(el("li", { class: "empty", text: "This list did not load. Reload the page to try again." }));
    } else if (!entries.length) {
      list.replaceChildren(el("li", { class: "empty", text: name === "activity" ? "CHIP has done nothing yet today." : "No approvals yet." }));
    } else {
      list.replaceChildren(...entries.map((entry) => item(entry, name === "activity" ? "activity" : "approval")));
    }
  }
  for (const [name, label] of [["activity", "Activity"], ["approvals", "Approvals"]]) {
    tabs.append(el("button", { class: "tab", type: "button", role: "tab", "data-tab": name, onclick: () => show(name) }, [label]));
  }
  show("activity");
}
