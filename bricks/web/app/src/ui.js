// Small DOM helpers. No HTML strings from data: text is always set as text.

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (value === false || value === undefined || value === null) continue;
    if (name === "class") node.className = value;
    else if (name === "text") node.textContent = value;
    else if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
    else node.setAttribute(name, value === true ? "" : value);
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function icon(name) {
  const paths = {
    chat: "M4 5h16v11H8l-4 4z",
    feed: "M5 4h10l4 4v12H5zM8 11h8M8 15h8M8 7h5",
    agent: "M12 4a8 8 0 1 1 0 16a8 8 0 0 1 0-16M9 11v.5M15 11v.5M9 15c1.6 1.4 4.4 1.4 6 0",
    sliders: "M4 7h10M18 7h2M4 17h4M12 17h8M14 5v4M8 15v4",
    heart: "M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z",
    discuss: "M5 6h14v9H10l-5 4z",
    plus: "M12 5v14M5 12h14",
    send: "M12 19V6M6 11l6-6 6 6",
    check: "M5 12.5l4.5 4.5L19 7.5",
    shield: "M12 3l7 3v5c0 4.5-3 8-7 10c-4-2-7-5.5-7-10V6z",
    close: "M6 6l12 12M18 6L6 18",
    list: "M5 7h14M5 12h14M5 17h14",
  };
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("class", "icon");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", paths[name] || paths.list);
  svg.append(path);
  return svg;
}

export function timeAgo(iso) {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  const minutes = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export function clock(iso) {
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "";
  return then.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

const LINK = /(https?:\/\/[^\s<>"')]+)/g;
const BOLD = /\*\*([^*]+)\*\*/g;

function inline(text) {
  // Bold and links, built as nodes.
  const nodes = [];
  for (const [index, part] of text.split(BOLD).entries()) {
    if (index % 2 === 1) {
      nodes.push(el("b", { text: part }));
      continue;
    }
    for (const [i, piece] of part.split(LINK).entries()) {
      if (!piece) continue;
      nodes.push(i % 2 === 1 ? el("a", { href: piece, target: "_blank", rel: "noopener noreferrer", text: piece }) : piece);
    }
  }
  return nodes;
}

export function richText(text) {
  // Paragraphs, and "- " lines as a list.
  const box = el("div", { class: "rich" });
  let list = null;
  for (const line of String(text || "").split("\n")) {
    if (/^\s*[-•]\s+/.test(line)) {
      if (!list) {
        list = el("ul");
        box.append(list);
      }
      list.append(el("li", {}, inline(line.replace(/^\s*[-•]\s+/, ""))));
    } else {
      list = null;
      if (line.trim()) box.append(el("p", {}, inline(line)));
    }
  }
  return box;
}
