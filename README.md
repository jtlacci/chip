# chip

A personal agent web app, built on [brick-framework](https://github.com/jtlacci/brick-framework). The agent reads your calendar and Gmail, makes a feed, talks with you in chat and does tasks. It asks for your approval before each action that changes something outside CHIP.

| Brick | What it does | Deterministic |
| --- | --- | --- |
| `web` | The browser app: feed, feed instructions, chat, agent panel (`KIND = "web"`, plain JavaScript) | Yes |
| `api` | The HTTP surface: asks `auth`, then reads or writes `store` | Yes |
| `auth` | Roles (admin, owner, guest), sessions, 7-day one-use invites, the permission table | Yes |
| `store` | Records in collections over one SQLite database | Yes |

| `calendar` | CHIP's own Google Calendar, read side | No |
| `gmail` | CHIP's own Gmail, read side; hides one-time codes and sign-in links | No |
| `model` | One interface for every model provider; version 1 is Anthropic (Claude). The provider and the model are in `input/config.yml` | No |
| `feed` | Chooses what the feed shows from the calendar, the mail and the instructions; the model writes the posts; every post is checked | No |
| `memory` | CHIP's shared memory: the agent saves facts and finds them by words (SQLite full-text search); secrets are refused | No |
| `agent` | CHIP's answer in chat: a tool loop on the model brick over a snapshot of the calendar, the mail and the memory | No |
| `browser` | Reads the web for a task, read-only: the model chooses one typed action per step (open a link, back, more, look, answer, fail); GET only, a new browser profile for each page, no private addresses | No |

Approvals come in a later milestone.

## Running CHIP

CHIP runs at https://184-197-162-29.sslip.io on its own EC2 instance (`deploy/app/bootstrap.sh`): Caddy serves HTTPS, and a timer deploys `main` every 2 minutes. Sign-in is Google, with the openid, email and profile scopes only. The admin connects CHIP's own Google account once from the agent panel; the refresh token stays in a file on the server and in SSM, never in a brick.

`server/serve.py` serves the app and turns each `/api/*` request into one api run. It mints session and invite tokens and passes only their hashes to the bricks. Run it locally:

```sh
(cd bricks/web/app && node build.js)
CHIP_DEV=1 CHIP_DB=chip.db python3 server/serve.py --port 8000
# open http://127.0.0.1:8000/auth/dev?as=yourname: the first person to sign in is the admin
```

Pull requests into `main` are evaluated by the brick-runner service, which posts the `brick-runner` status and a comment. Comment `/brick holdout <brick>` or `/brick promote <brick>` to run the holdout or promote (allowed logins only). A new brick starts with synthetic data (`data.lock` is `unpinned`); pin captured data when it exists.

---

# Internal Python bricks

This repository organizes one codebase as discrete domain bricks. Bricks are used only by sibling bricks inside this repository; they are not external packages or services.

Define every domain as a named folder under `bricks/`. `example_brick` is an empty placeholder showing the required files and contracts.

```text
bricks/
├── _runtime.py           # framework runtime: runs, io routing, capture
├── _yaml.py              # the YAML subset for config and policy
└── <brick_name>/
    ├── __init__.py       # exposes only the internal run entry point
    ├── contract.py       # typed boundary, determinism, dependencies, state ownership
    ├── AGENTS.md         # optional domain-specific additions
    ├── input/            # config and adapters
    ├── runner/           # the run entry point
    ├── src/              # private logic
    └── evaluation/       # how the brick is evaluated: policy, evaluate.py, data.lock
```

- Rules inherit from the closest `AGENTS.md`; not every folder needs one. The `input/`, `runner/`, `src/` and `evaluation/` folders have contracts because each owns a different boundary.
- Add one file under `input/adapters/` for every external source and sibling brick.
- `src/` reaches external sources and sibling bricks only through its own `input/` adapters, and adapters make every call through the run context's io.
- Expose only `run` to sibling bricks through the brick's top-level `__init__.py`.
- Declare every sibling dependency and its `eventual` or `orchestrated` consistency policy in `contract.py`. The declared graph must be acyclic.
- Declare stable identifiers for application state owned by the brick. One state resource has one owner.

`contract.py` contains:

```python
CONTRACT_VERSION = 1
LANE = "strict"
DETERMINISTIC = False
SIBLING_DEPENDENCIES = {"another_brick": "eventual"}
OWNED_STATE = ("database:example-records",)

class BrickInput(TypedDict): ...
class BrickOutput(TypedDict): ...
```

Inputs, outputs, requests and responses must be JSON values, because the runtime records them.

Sibling adapters still import only the sibling's top-level `run`; `contract.py` is not another exported API. The `run` annotations expose the boundary to static tooling, while each adapter translates into its owning brick's types.

Split a brick when `run` becomes a large dispatcher, its adapters stop being easy to understand, or unrelated changes repeatedly touch the same `src/`.

## Runs and io

The public Python entry point has one shape:

```python
run(inputs: BrickInput) -> BrickOutput
```

There are no mode options. `run` calls `_runtime.run_brick`, which builds the run context and records the run. Adapters make every call through the context's io:

```python
ctx["io"].external("prices", request, _fetch_live)   # an external source
ctx["io"].sibling("calendar", request, run)          # a sibling's run, passed, not called
```

| Where | io | What happens |
| --- | --- | --- |
| Production | `LiveIO` | The call is made. With `BRICK_CAPTURE_DIR` set, it is appended to a local spool file. |
| Evaluation | The runner's replay io | Recorded responses are injected below the adapter's translation. |

What the runtime captures:

| Data | Stored |
| --- | --- |
| External exchanges (app-boundary data) | Full request and response |
| Sibling exchanges and brick runs | Full output if the brick has `DETERMINISTIC = False`, else a hash, because a replay can compute it again |

## Evaluation

Each brick has an `evaluation/` folder. The evaluation runner, a separate trusted service, uses it to evaluate the brick. Agents hand work to the runner; no agent runs evaluations itself.

| File | Owner | Purpose |
| --- | --- | --- |
| `policy.yml` | Human only | Surfaces for each loop, split rule, selection, seeds, label horizon |
| `evaluate.py` | Evaluation loop | `GATES`, `build()`, `score()`, `generate()` |
| `data.lock` | Human only | The pinned dataset hash, or `unpinned` for a new brick |

Two agent loops change a brick. One change belongs to one loop:

| | Code loop | Evaluation loop |
| --- | --- | --- |
| Agent | Dev agent | Eval agent |
| Can change | `code_loop` surfaces in `policy.yml` | `eval_loop` surfaces in `policy.yml` |
| Frozen | All of `evaluation/` | The production implementation |

There are no unit tests or smoke tests. Domain rules are properties in `evaluation/`, where the dev agent cannot change them.

## Checks

Check brick shape, import direction, public exports, io routing and evaluation policy with:

```sh
python3 tools/lint_bricks.py
```

Check that a change belongs to one loop, classified by the policy on the base commit:

```sh
python3 tools/check_change.py --base <sha> --head <sha>
```

Both run in CI through the reusable workflow `.github/workflows/checks.yml`. Copy `.github/CODEOWNERS.example` to `.github/CODEOWNERS` so human-only files need human approval.

Render the declared sibling-dependency graph as Mermaid with:

```sh
python3 tools/graph_bricks.py
```

Each node lists the state its brick owns. A solid arrow is an `orchestrated` dependency and a dashed arrow is an `eventual` one. Bricks that no sibling depends on are drawn with a thicker border; those are the top-level flows.

## Lanes

A lane is the enforcement class a brick declares for itself with `LANE` in `contract.py`. Every rule above applies to every brick; a lane only adds rules, so no brick can opt out of the boundary. A brick that declares no `LANE` is `strict`.

| Lane | What it means | What the linter adds |
| --- | --- | --- |
| `strict` | The regular brick. | Nothing beyond the rules every brick gets. |
| `pure` | Output is a function of input alone. | No adapters and no sibling dependencies. The direct-I/O ban that `src/` already carries widens to the whole brick, plus `random` and `time`, plus any `.now()`, `.today()`, or `.utcnow()` call. `runner/` and `evaluation/` are exempt; `datetime` stays importable for annotations and arithmetic. |
| `workflow` | Invoked as a whole operation, never imported for its parts. | No contract may name it as a sibling dependency. It alone may carry a process door, `__main__.py`, and that door may import nothing from the brick tree except the brick's own `run`, and no direct-I/O module. |

A lane cannot exist without an enforcer: the `LANES` table in `tools/lint_bricks.py` maps each name to the function that checks it, and the linter rejects any other value.

## Framework tests

The framework's own tools have tests:

```sh
python3 -m unittest discover -s tools/tests -t . -v
```
