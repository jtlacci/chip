# Domain brick contract

Each direct child folder of `bricks/` is one discrete domain brick. Rules inherit from the repository contract; a brick-level `AGENTS.md` is optional unless that brick adds domain-specific rules.

`bricks/_runtime.py` and `bricks/_yaml.py` are the framework runtime, not bricks. Keep `bricks/__init__.py` so the package imports.

## Required shape

```text
<brick_name>/
├── __init__.py
├── contract.py
├── input/
│   ├── AGENTS.md
│   ├── config.yml
│   └── adapters/
├── runner/
│   ├── AGENTS.md
│   ├── __init__.py
│   └── run.py
├── src/
│   └── AGENTS.md
└── evaluation/
    ├── AGENTS.md
    ├── policy.yml
    ├── evaluate.py
    └── data.lock
```

## Boundaries

- The brick's top-level `__init__.py` exposes only the repository-internal `run` entry point from `runner/run.py`.
- `contract.py` declares `CONTRACT_VERSION`, typed `BrickInput` and `BrickOutput`, `DETERMINISTIC`, `SIBLING_DEPENDENCIES`, and `OWNED_STATE`. It may also declare `LANE`: `strict` (the default), `pure`, or `workflow`. A lane only adds rules; see the README.
- `contract.py` is not a second public surface. Sibling adapters import only `run`; its annotations carry the boundary types, and the adapter translates into its owning brick's types.
- `DETERMINISTIC = True` only when the same input always gives the same output. The runtime then stores output hashes instead of outputs.
- `SIBLING_DEPENDENCIES` maps each sibling name to `eventual` or `orchestrated`. `eventual` accepts independently committed state and possible lag. `orchestrated` means the importing, dependent brick is responsible for sequencing and compensation; it does not imply a distributed transaction.
- The declared sibling graph must be acyclic. A cycle is a signal to merge responsibilities or introduce a parent brick.
- `OWNED_STATE` lists stable application-resource identifiers. No identifier may be owned by two bricks.
- `runner/` hands each run to the runtime, which builds the run context, calls private `src/` logic, and records the run.
- `input/` owns configuration, external-source adapters and sibling adapters.
- `src/` owns private domain logic and may call its own `input/` adapters when it needs data or an external effect.
- `evaluation/` owns how the brick is evaluated. Nothing else in the brick imports it, and it sees the brick only through `contract.py`.
- Execution-call direction is `runner -> src -> input adapter -> ctx["io"]`. `runner` does not call adapters directly, and adapters never call back into `src` or `runner`.
- Every adapter call goes through the run context's io: `ctx["io"].external(...)` or `ctx["io"].sibling(...)`. A sibling's `run` is passed to the io, never called directly.
- `src` may import adapters from its own brick, but it may not directly import external clients or sibling bricks.
- A sibling brick may be reached only through a sibling adapter that passes the sibling's `run` entry point to the io.
- Every sibling adapter must correspond to a declared sibling dependency.
- Do not import another brick's `src/`, `runner/`, `evaluation/` or configuration.
- Do not package bricks for, or expose them to, consumers outside this repository.

Do not add `__init__.py` merely to mark folders. Keep the repository `bricks/` file, the brick-level file for the internal entry point, and `runner/__init__.py`. The other folders use namespace-package behavior.
