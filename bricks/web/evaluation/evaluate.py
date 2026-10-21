"""How the web brick is evaluated. Evaluation loop only.

The runner builds the app, serves it on 127.0.0.1 and passes a Chromium
browser. ``observe`` opens one page per case, answers every /api/* request
from the case's fixture, and measures from outside the page where it can
(Playwright) and inside it where it must (layout shift, frame timing).
``score`` turns each observation into the terms of the spec's four score
boxes (App, Feed, Chat, Action component).

The page elements are found by the data-* attributes in contract.py.
"""

import json
import random
import time

GATES: tuple[tuple[str, str, float], ...] = (
    ("mobile_rendering", ">=", 1.0),      # App: no horizontal overflow at 375 px
    ("computer_rendering", ">=", 1.0),    # App: no horizontal overflow at 1280 px
    ("render_ms", "<=", 1500.0),          # App: render speed
    ("layout_shift", "<=", 0.1),          # App: component movement
    ("nav", ">=", 1.0),                   # App: nav controls
    ("console_clean", ">=", 1.0),         # every page: no console error
    ("renders_all", ">=", 1.0),           # Feed and Chat: every kind of text and media renders
    ("above_fold", ">=", 1.0),            # Feed, Chat, Agent panel: content over the cut
    ("dropped_frames", "<=", 0.05),       # Feed and Chat: scrolling smoothness
    ("typing_p95_ms", "<=", 50.0),        # Chat: typing smoothness
    ("upload", ">=", 1.0),                # Chat: upload text and media
    ("animation", ">=", 1.0),             # Action component: the character animates
    ("sub_nav", ">=", 1.0),               # Action component: the tabs work
    ("instructions", ">=", 1.0),          # Feed instructions: owners save, guests cannot
    ("sign_in_page", ">=", 1.0),          # no session: the sign-in page
)

PNG = ("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 9'>"
       "<rect width='16' height='9' fill='%23c9b79c'/><rect y='6' width='16' height='3' fill='%238a7356'/></svg>")

CLS_SCRIPT = """
window.__cls = 0;
try {
  new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) if (!entry.hadRecentInput) window.__cls += entry.value;
  }).observe({ type: "layout-shift", buffered: true });
} catch (error) {}
"""

MEASURE = """
() => {
  const width = innerWidth, height = innerHeight;
  let outside = 0;
  for (const node of document.querySelectorAll("body *")) {
    const box = node.getBoundingClientRect();
    if (!box.width || !box.height) continue;
    if (getComputedStyle(node).visibility === "hidden") continue;
    if (box.right > width + 1 || box.left < -1) outside += 1;
  }
  const shown = (selector) => [...document.querySelectorAll(selector)].filter((node) => {
    const box = node.getBoundingClientRect();
    if (!box.width || !box.height || box.top < 0 || box.bottom > height || box.left < 0 || box.right > width) return false;
    const top = document.elementFromPoint(box.left + box.width / 2, box.top + Math.min(box.height / 2, 10));
    return top !== null && (node === top || node.contains(top));
  }).length;
  return {
    overflow: document.documentElement.scrollWidth > width + 1,
    outside,
    posts: document.querySelectorAll("[data-post]").length,
    messages: document.querySelectorAll("[data-message]").length,
    activity: document.querySelectorAll("[data-activity]").length,
    page: (document.querySelector("[data-page]") || {}).dataset?.page || null,
    titles_shown: shown("[data-post-title]"),
    messages_shown: shown("[data-message]"),
    activity_shown: shown("[data-activity]"),
    signin: !!document.querySelector("[data-signin]"),
    cls: window.__cls || 0,
  };
}
"""

SCROLL = """
async (ms) => {
  const gaps = [];
  let last = performance.now(), going = true;
  const step = () => {
    const now = performance.now();
    gaps.push(now - last);
    last = now;
    window.scrollBy(0, 30);
    if (going) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
  await new Promise((done) => setTimeout(done, ms));
  going = false;
  const frames = gaps.slice(3);
  return { frames: frames.length, dropped: frames.filter((gap) => gap > 34).length / Math.max(1, frames.length),
           moved: window.scrollY > 0 };
}
"""

TYPING = """
() => {
  window.__typing = [];
  const box = document.querySelector("[data-composer]");
  box.addEventListener("keydown", () => {
    const start = performance.now();
    requestAnimationFrame(() => window.__typing.push(performance.now() - start));
  });
}
"""


# ---------------------------------------------------------------- the browser side

def _path(url: str) -> str:
    rest = url.split("://", 1)[-1]
    path = "/" + rest.split("/", 1)[1] if "/" in rest else "/"
    return path.split("?", 1)[0]


def observe(input, browser, base_url):
    width, height = input["width"], input["height"]
    context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="no-preference")
    page = context.new_page()
    errors, sent = [], []
    # An api error status the fixture chose (401, 403) is logged by Chromium as a failed resource; that is
    # the network, not the app. Every other console error counts.
    page.on("console", lambda message: errors.append(message.text[:300])
            if message.type == "error" and not message.text.startswith("Failed to load resource: the server responded")
            else None)
    page.on("pageerror", lambda error: errors.append(str(error)[:300]))

    def answer(route, request):
        key = f"{request.method} {_path(request.url)}"
        if request.method in ("POST", "PUT", "DELETE"):
            try:
                body = request.post_data_json
            except Exception:
                body = request.post_data
            sent.append({"key": key, "body": body})
        reply = input["api"].get(key, {"status": 404, "body": {"error": "not_found"}})
        route.fulfill(status=reply["status"], content_type="application/json", body=json.dumps(reply["body"]))

    page.route("**/api/**", answer)
    page.add_init_script(CLS_SCRIPT)
    seen = {"errors": errors}
    try:
        start = time.monotonic()
        page.goto(base_url + input["page"], wait_until="domcontentloaded")
        page.wait_for_selector("body[data-ready]", state="attached", timeout=10000)
        seen["render_ms"] = (time.monotonic() - start) * 1000
        page.wait_for_timeout(300)   # let late layout shifts happen before they are counted
        seen.update(page.evaluate(MEASURE))
        check = input.get("check")
        if check == "nav":
            seen["nav"] = _nav(page, input["page"].strip("/"))
        elif check == "scroll":
            seen["scroll"] = page.evaluate(SCROLL, 2000)
        elif check == "typing":
            page.evaluate(TYPING)
            page.click("[data-composer]")
            page.keyboard.type("Is the Frontier Markets party still on tonight?", delay=25)
            page.wait_for_timeout(200)
            seen["typing"] = page.evaluate("window.__typing")
        elif check == "upload":
            seen["upload"] = _upload(page, sent)
        elif check == "animation":
            avatar = page.locator("[data-avatar]:visible").first
            first = avatar.screenshot(animations="allow")
            page.wait_for_timeout(800)
            second = avatar.screenshot(animations="allow")
            seen["animated"] = first != second
        elif check == "tabs":
            seen["tabs"] = _tabs(page)
        elif check == "instructions":
            seen["instructions"] = _instructions(page, sent, input["role"])
    except Exception as error:   # a page that never becomes ready, or a step that cannot run
        seen["failure"] = f"{type(error).__name__}: {str(error)[:400]}"
    finally:
        context.close()
    seen["sent"] = sent
    return seen


def _nav(page, start: str) -> bool:
    ok = True
    for name in ("feed", "chat", "agent"):
        link = page.locator(f'[data-nav="{name}"]').first
        if not link.is_visible():
            ok = False
            continue
        link.click()
        page.wait_for_selector(f'[data-page="{name}"]', timeout=5000)
        page.wait_for_selector("body[data-ready]", state="attached", timeout=5000)
    page.go_back()
    page.wait_for_selector('[data-page="chat"]', timeout=5000)
    return ok and page.evaluate("location.pathname") == "/chat"


def _upload(page, sent: list) -> dict:
    page.set_input_files("input[data-attach]", files=[{"name": "plan.pdf", "mimeType": "application/pdf",
                                                       "buffer": b"%PDF-1.4 plan"}])
    shown = page.locator('[data-attachment="plan.pdf"]').first.is_visible()
    before = page.locator("[data-message]").count()
    page.fill("[data-composer]", "See the plan")
    page.click("[data-send]")
    page.wait_for_timeout(500)
    posted = [s for s in sent if s["key"].startswith("POST /api/chats/") and isinstance(s["body"], dict)]
    text = posted[-1]["body"].get("text", "") if posted else ""
    return {"shown": shown, "sent": "See the plan" in text and "plan.pdf" in text,
            "appended": page.locator("[data-message]").count() > before}


def _tabs(page) -> dict:
    tabs = {}
    for name, selector in (("approvals", "[data-approval]"), ("activity", "[data-activity]")):
        page.click(f'[data-tab="{name}"]')
        page.wait_for_timeout(100)
        tabs[name] = page.locator(selector).count()
    return tabs


def _instructions(page, sent: list, role: str) -> dict:
    page.click("[data-instructions-open]")
    page.wait_for_selector("[data-instructions][open]", timeout=5000)
    result = {"text": page.input_value("[data-instructions-text]"),
              "can_save": page.locator("[data-instructions-save]").is_enabled(),
              "editable": page.locator("[data-instructions-text]").is_editable()}
    if role in ("owner", "admin") and result["can_save"]:
        page.fill("[data-instructions-text]", "Only NYC events this weekend")
        page.click("[data-instructions-save]")
        page.wait_for_timeout(400)
        result["saved"] = any(s["key"] == "PUT /api/feed/instructions" and isinstance(s["body"], dict)
                              and s["body"].get("text") == "Only NYC events this weekend" for s in sent)
        result["closed"] = page.locator("[data-instructions][open]").count() == 0
    return result


# ---------------------------------------------------------------- scoring

def build(record: dict) -> dict:
    return record["input"]


def _expected(inputs: dict) -> dict:
    api = inputs["api"]
    me = api.get("GET /api/me", {}).get("body", {})
    chats = api.get("GET /api/chats", {}).get("body", {}).get("chats", [])
    first = chats[0]["id"] if chats else None
    messages = api.get(f"GET /api/chats/{first}/messages", {}).get("body", {}).get("messages", []) if first else []
    activity = api.get("GET /api/activity", {}).get("body", {}).get("activity", [])
    return {"posts": len(api.get("GET /api/feed", {}).get("body", {}).get("posts", [])),
            "messages": len(messages), "activity": 0 if me.get("role") == "guest" else len(activity),
            "approvals": len(api.get("GET /api/approvals", {}).get("body", {}).get("approvals", []))}


def score(record: dict, output: dict | None, later: list[dict]) -> dict[str, float]:
    inputs = record["input"]
    if not isinstance(output, dict) or "failure" in output or "render_ms" not in output:
        return {"correct": 0.0, "console_clean": 0.0}
    want = _expected(inputs)
    wide = inputs["width"] >= 1000
    name = inputs["page"].strip("/")
    terms: dict[str, float] = {"console_clean": 0.0 if output["errors"] else 1.0}
    flat = not output["overflow"] and output["outside"] == 0
    terms["computer_rendering" if wide else "mobile_rendering"] = 1.0 if flat else 0.0
    terms["render_ms"] = float(output["render_ms"])
    terms["layout_shift"] = float(output["cls"])
    check = inputs.get("check")
    if inputs.get("signed_out"):
        terms["sign_in_page"] = 1.0 if output["signin"] and flat else 0.0
    elif check in (None, "render"):
        if name == "feed":
            terms["renders_all"] = 1.0 if output["posts"] == want["posts"] else 0.0
            if want["posts"] >= 2:
                terms["above_fold"] = 1.0 if output["titles_shown"] >= (2 if wide else 1) else 0.0
        elif name == "chat":
            terms["renders_all"] = 1.0 if output["messages"] == want["messages"] else 0.0
            if want["messages"] >= 3:
                terms["above_fold"] = 1.0 if output["messages_shown"] >= (3 if wide else 2) else 0.0
        elif name == "agent":
            terms["renders_all"] = 1.0 if output["activity"] == want["activity"] else 0.0
            if want["activity"] >= 4:
                terms["above_fold"] = 1.0 if output["activity_shown"] >= 4 else 0.0
    elif check == "nav":
        terms["nav"] = 1.0 if output.get("nav") else 0.0
    elif check == "scroll":
        scroll = output.get("scroll") or {}
        terms["dropped_frames"] = float(scroll.get("dropped", 1.0)) if scroll.get("moved") else 1.0
    elif check == "typing":
        times = sorted(output.get("typing") or [])
        terms["typing_p95_ms"] = times[int(0.95 * (len(times) - 1))] if len(times) >= 20 else 1000.0
    elif check == "upload":
        upload = output.get("upload") or {}
        terms["upload"] = 1.0 if upload.get("shown") and upload.get("sent") and upload.get("appended") else 0.0
    elif check == "animation":
        terms["animation"] = 1.0 if output.get("animated") else 0.0
    elif check == "tabs":
        tabs = output.get("tabs") or {}
        terms["sub_nav"] = 1.0 if tabs.get("activity") == want["activity"] and tabs.get("approvals") == want["approvals"] else 0.0
    elif check == "instructions":
        result = output.get("instructions") or {}
        wanted = inputs["api"]["GET /api/feed/instructions"]["body"]["text"]
        if inputs["role"] == "guest":
            ok = result.get("text") == wanted and not result.get("can_save") and not result.get("editable")
        else:
            ok = result.get("text") == wanted and result.get("saved") and result.get("closed")
        terms["instructions"] = 1.0 if ok else 0.0
    limits = {"render_ms": 1500.0, "layout_shift": 0.1, "dropped_frames": 0.05, "typing_p95_ms": 50.0}
    terms["correct"] = 1.0 if all(v <= limits[k] if k in limits else v >= 1.0 for k, v in terms.items()) else 0.0
    return terms


# ---------------------------------------------------------------- synthetic data

def _iso(hours_ago: float) -> str:
    total = int(hours_ago * 60)
    day, minute = 20 - total // (24 * 60), (24 * 60 - total % (24 * 60)) % (24 * 60)
    return f"2026-10-{max(day, 1):02d}T{minute // 60:02d}:{minute % 60:02d}:00+00:00"


def _posts(rng: random.Random, count: int) -> list[dict]:
    kinds = ["text", "calendar", "image", "link", "long"]
    posts = []
    for i in range(count):
        kind = kinds[i % len(kinds)] if i < len(kinds) else rng.choice(kinds)
        post = {"id": f"p{i}", "icon": rng.choice(["🎟️", "📅", "🏛️", "✉️", "☕"]), "time": _iso(i * 1.5 + 0.5),
                "title": rng.choice(["Free this weekend? I'll find what's hot and score you the tickets.",
                                     "Meeting with Jacob tomorrow at 10:30 AM",
                                     "Open House New York unlocks 300+ sites this weekend"]),
                "body": "The meeting you added yesterday is the first of five engagements stacked through Friday evening.",
                "kind": kind}
        if kind == "calendar":
            post["card"] = {"type": "calendar", "day": "Friday, Oct 16", "rows": [
                {"time": "10:30 AM", "title": "Meeting with Jacob", "note": "(30 min)"},
                {"time": "12:00 PM", "title": "Team updates and product retro", "note": "(1 hr)"},
                {"time": "8:30 PM", "title": "Frontier Markets after party", "note": "(Club Reign)", "now": True}]}
        elif kind == "image":
            post["card"] = {"type": "image", "src": PNG, "alt": "The interior of a historic building", "width": 640, "height": 360}
        elif kind == "link":
            post["card"] = {"type": "link", "url": "https://ohny.org/weekend", "title": "Open House New York Weekend"}
            post["action"] = {"label": "Let's do it"}
        elif kind == "long":
            post["body"] = ("Supercalifragilisticexpialidociousandthensomeverylongwordwithoutanyspaces " * 3
                            + "\n- First thing to know\n- Second thing, with a link https://example.org/a/very/long/path/that/keeps/going")
        posts.append(post)
    return posts


def _messages(rng: random.Random, count: int, me: str) -> list[dict]:
    texts = ["i ordered a few", "You've got a collection going, all size L:\n- **Dark Shadow** hoodie, twice\n- **Cobalt** hoodie\n- Plus Dark Shadow sweatpants to match",
             "Frontier Markets After Party tomorrow: down to 2 spots left. Last real window to get in: https://luma.com/reign-fm",
             "Heads up: a password-reset email landed at 5:08 PM today. Did you ask for it?",
             "Averyveryveryveryveryveryveryveryveryveryveryveryverylongwordthatmustwrap"]
    authors = [(me, "You"), ("chip", "CHIP"), ("google:jamie", "Jamie")]
    out = []
    for i in range(count):
        author, name = authors[i % 3] if i < 3 else rng.choice(authors)
        time_text = _iso((count - i) * 0.2)
        out.append({"id": f"general/{time_text}/{author}/{i}", "author": author, "name": name,
                    "text": texts[i % len(texts)], "time": time_text})
    return out


def _api(rng: random.Random, role: str, posts: int = 6, messages: int = 9) -> dict:
    me = {"user_id": f"google:{role}", "role": role, "name": role.title(), "email": f"{role}@example.com"}
    chats = [{"id": "general", "title": "General", "updated": _iso(0.1)}, {"id": "trip", "title": "Trip", "updated": _iso(5)}]
    activity = [{"id": f"a{i}", "title": t, "detail": d, "time": _iso(i + 0.3)} for i, (t, d) in enumerate([
        ("Search reset emails", "Gmail search for reset emails"), ("Find grey hoodie colour", "Found Dark Shadow and Heather Frost"),
        ("Get hoodie checkout link", "Asked for approval before sharing the link"), ("Place book pre-order", "Placing the pre-order"),
        ("Order hoodie and article", "Added the article to the order"), ("Find exact grey hoodie", "Checked the order history")])]
    approvals = [{"id": f"r{i}", "title": t, "state": s, "time": _iso(i * 2 + 1)} for i, (t, s) in enumerate([
        ("Send this email in Gmail", "allowed"), ("Create a calendar event", "denied"), ("Continue on this checkout page?", "allowed")])]
    deny = {"status": 403, "body": {"error": "forbidden"}}
    message_list = _messages(rng, messages, me["user_id"])
    return {
        "GET /api/me": {"status": 200, "body": me},
        "GET /api/feed": {"status": 200, "body": {"posts": _posts(rng, posts)}},
        "GET /api/feed/instructions": {"status": 200, "body": {"text": "Make me a feed about my personal life and local NYC events."}},
        "PUT /api/feed/instructions": deny if role == "guest" else {"status": 200, "body": {"text": "Only NYC events this weekend"}},
        "GET /api/chats": {"status": 200, "body": {"chats": chats}},
        "GET /api/chats/general/messages": {"status": 200, "body": {"messages": message_list}},
        "GET /api/chats/trip/messages": {"status": 200, "body": {"messages": []}},
        "POST /api/chats/general/messages": {"status": 201, "body": {"message": {
            "id": "general/new", "author": me["user_id"], "name": me["name"], "text": "See the plan\n- plan.pdf", "time": _iso(0)}}},
        "GET /api/activity": deny if role == "guest" else {"status": 200, "body": {"activity": activity}},
        "GET /api/approvals": deny if role == "guest" else {"status": 200, "body": {"approvals": approvals}},
        "GET /api/account": {"status": 200, "body": rng.choice([{"connected": True, "email": "chip@example.com"},
                                                               {"connected": False, "email": ""}])},
    }


PHONE, COMPUTER = (375, 812), (1280, 800)


def generate(seed: int) -> list[dict]:
    rng = random.Random(seed)
    cases = []

    def add(page, size, role="owner", check=None, **fixture):
        inputs = {"page": page, "width": size[0], "height": size[1], "role": role, "api": _api(rng, role, **fixture)}
        if check:
            inputs["check"] = check
        cases.append({"input": inputs})

    for page in ("/feed", "/chat", "/agent"):
        for size in (PHONE, COMPUTER):
            for role in ("owner", "guest"):
                add(page, size, role)
    # Every gate needs cases in every split (development, validation, holdout), and a
    # record goes to a split by its hash. So each check gets several cases per seed.
    either = lambda: rng.choice([PHONE, COMPUTER])
    for _ in range(8):
        add("/chat", either(), check="nav")
        add("/chat", COMPUTER, check="typing")
        add("/chat", either(), check="upload")
        add("/agent", PHONE, check="animation")
        # On a phone the agent panel is its own page; on a computer it is beside every page.
        add("/agent", PHONE, check="tabs")
        add(rng.choice(["/agent", "/feed", "/chat"]), COMPUTER, check="tabs")
        add("/feed", either(), "owner", check="instructions")
        add("/feed", either(), "guest", check="instructions")
        signed_out = {"page": rng.choice(["/feed", "/chat"]), "width": either()[0], "height": 812, "role": "guest",
                      "signed_out": True, "api": {"GET /api/me": {"status": 401, "body": {"error": "sign_in_required"}}}}
        cases.append({"input": signed_out})
    for _ in range(5):
        add("/feed", COMPUTER, check="scroll", posts=200)
        add("/chat", PHONE, check="scroll", messages=500)
    return cases
