"""How the redact brick is evaluated. Evaluation loop only.

Each case is a text from a template, so the case knows the exact expected
output: a text with a planted secret (a one-time code near a code word, or a
sign-in, reset, confirm or magic link) expects the same text with the marker in
the secret's place; a text with no secret (prices, phone numbers, times, dates,
order and flight numbers, room numbers, plain links) expects the text unchanged.

Gates:
- no_secret_left:  no planted secret is in the output.
- exact:           the output text is exactly the expected text.
- count:           "hidden" is the number of planted secrets.
- rejects_invalid: an invalid request fails with "invalid:".
"""

import random

GATES: tuple[tuple[str, str, float], ...] = (
    ("no_secret_left", ">=", 1.0),
    ("exact", ">=", 1.0),
    ("count", ">=", 1.0),
    ("rejects_invalid", ">=", 1.0),
)

CODE_MARK, LINK_MARK = "[code hidden]", "[link hidden]"
BRANDS = ["Acme", "Chase", "Notion", "Slack", "Coinbase", "Venmo", "Delta", "Hinge"]
HOSTS = ["acme.example", "bank.example", "notion.so", "app.slack.example", "id.coinbase.example"]

CODE_TEMPLATES = [
    "Your {brand} verification code is {code}. It expires in 10 minutes.",
    "{code} is your {brand} login code. Do not share it.",
    "Use one-time passcode {code} to finish signing in to {brand}.",
    "{brand} security code: {code}",
    "Two-factor check: enter {code} on the {brand} screen.",
    "Your {brand} PIN is {code}, valid for one use.",
    "Hi! The sign-in code for {brand} is {code} (requested just now).",
]
ALNUM_TEMPLATES = [
    "Your {brand} sign in code is {code}",
    "{brand} OTP {code} - expires soon",
]
LINK_TEMPLATES = [
    "Reset your {brand} password here: https://{host}/reset?token={token} and ignore this if it was not you.",
    "Confirm your email for {brand}: https://{host}/confirm/{token} thanks!",
    "Your {brand} magic link: https://{host}/auth/magic?x={token} (one use)",
    "Sign in to {brand}: https://{host}/login?next=%2Fhome&t={token} now",
    "Verify your account at https://{host}/verify/{token} within a day.",
]
CLEAN = [
    "Dinner at Lilia, 565 Union Ave, at 7:30 PM. Table for 4.",
    "Your order #{n5} has shipped and should arrive Friday.",
    "Total: $1,299.00 due by 2026-11-01. Thanks for your business.",
    "Call (718) 576-3095 if you have questions about the code of conduct.",
    "The meeting is in room {n4} on the 5th floor at 14:05.",
    "Zip {n5}, Brooklyn. The party starts at 9 PM.",
    "Flight UA{n4} departs at 14:05 from gate B{n2}.",
    "Version 2.4.1 is out; read the notes at https://news.example/articles/2026/10/fall-guide today.",
    "Your appointment PIN reminder: see you at 10:30 AM on Oct 28.",
    "Sales were up 12.5% to $48,200 in Q3 2026.",
    "Run club meets at 6:30 AM, 3 miles, Prospect Park.",
    "Here is the menu: https://lilianewyork.example/menu and the hours page https://lilianewyork.example/visit for you.",
]


def build(record: dict) -> dict:
    return {k: v for k, v in record["input"].items() if not k.startswith("_")}


def score(record: dict, output: dict | None, later: list[dict], calls=()) -> dict[str, float]:
    inputs = record["input"]
    out = output if isinstance(output, dict) else {}
    if inputs.get("_invalid"):
        return {"rejects_invalid": float(out.get("ok") is False and str(out.get("error", "")).startswith("invalid:"))}
    text = out.get("text") if out.get("ok") is True and isinstance(out.get("text"), str) else None
    terms = {
        "no_secret_left": float(text is not None and not any(s in text for s in inputs["_secrets"])),
        "exact": float(text == inputs["_expect"]),
        "count": float(out.get("hidden") == len(inputs["_secrets"])),
    }
    terms["correct"] = float(all(v >= 1.0 for v in terms.values()))
    return terms


def _code(rng: random.Random) -> str:
    length = rng.choice([4, 6, 6, 8])
    return str(rng.randint(1, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(length - 1))


def _alnum(rng: random.Random) -> str:
    while True:
        code = "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(rng.choice([6, 7, 8])))
        if sum(c.isdigit() for c in code) >= 2 and any(c.isalpha() for c in code):
            return code


def _fill(rng: random.Random, template: str, **extra) -> str:
    values = {"brand": rng.choice(BRANDS), "host": rng.choice(HOSTS), "n2": str(rng.randint(10, 99)),
              "n4": str(rng.randint(1000, 9999)), "n5": str(rng.randint(10000, 99999))}
    values.update(extra)
    return template.format(**values)


def generate(seed: int) -> list[dict]:
    rng = random.Random(seed)
    cases = []
    for template in CODE_TEMPLATES * 2:
        code = _code(rng)
        values = {"brand": rng.choice(BRANDS)}
        cases.append({"input": {"op": "hide", "text": _fill(rng, template, code=code, **values),
                                "_expect": _fill(rng, template, code=CODE_MARK, **values), "_secrets": [code]}})
    for template in ALNUM_TEMPLATES * 2:
        code, values = _alnum(rng), {"brand": rng.choice(BRANDS)}
        cases.append({"input": {"op": "hide", "text": _fill(rng, template, code=code, **values),
                                "_expect": _fill(rng, template, code=CODE_MARK, **values), "_secrets": [code]}})
    for template in LINK_TEMPLATES * 2:
        token = f"{rng.getrandbits(64):016x}"
        values = {"brand": rng.choice(BRANDS), "host": rng.choice(HOSTS)}
        text = _fill(rng, template, token=token, **values)
        start = text.index("https://")
        end = text.index(" ", start) if " " in text[start:] else len(text)
        cases.append({"input": {"op": "hide", "text": text, "_expect": text[:start] + LINK_MARK + text[end:],
                                "_secrets": [text[start:end]]}})
    for template in CLEAN:
        text = _fill(rng, template)
        cases.append({"input": {"op": "hide", "text": text, "_expect": text, "_secrets": []}})
    # A clean text and a secret text in one message: only the secret changes. A sentence of more than 40
    # characters with no digits and no code words keeps the clean numbers out of the secret's window.
    gap = "\n\nWe will keep you posted about everything else that comes up later this week.\n\n"
    for _ in range(4):
        code, values = _code(rng), {"brand": rng.choice(BRANDS)}
        template = rng.choice(CODE_TEMPLATES)
        clean = _fill(rng, rng.choice(CLEAN))
        cases.append({"input": {"op": "hide", "text": clean + gap + _fill(rng, template, code=code, **values),
                                "_expect": clean + gap + _fill(rng, template, code=CODE_MARK, **values),
                                "_secrets": [code]}})
    for bad in ({"op": "hide"}, {"op": "hide", "text": 5}, {"op": "show", "text": "x"}, {"op": "hide", "text": "x" * 200_001}):
        cases.append({"input": {**bad, "_invalid": True}})
    return [{"input": c["input"], "exchanges": []} for c in cases]
