# Evaluation contract

This folder decides how the brick is evaluated. It belongs to the evaluation loop: the dev agent may not change it, and the eval agent may change only the paths that `policy.yml` lists for the evaluation loop.

## Files

| File | Owner | Purpose |
| --- | --- | --- |
| `policy.yml` | Human only | Surfaces for each loop, split rule, selection, seeds, label horizon |
| `evaluate.py` | Evaluation loop | `GATES`, `build()`, `score()`, `generate()` |
| `data.lock` | Human only | The pinned dataset hash, or `unpinned` for a new brick |

## Rules

- `evaluate.py` imports only this brick's `contract` types and non-I/O standard library modules. It never imports or calls `run`, and it does no I/O.
- No other part of the brick imports this folder.
- The runner owns the loop: it builds each case with `build()` in a trusted process, runs the brick in a sandbox, and scores each output with `score()` in a separate trusted process.
- A result is not one total score. Each term has its own gate in `GATES`. `policy.yml` names the metric that ranks candidates that pass every gate.
- Prefer properties that need no labels. A label from a later outcome comes from the `later` records that the runner passes to `score()`.
- A new brick with no real data uses `generate(seed)`. The runner calls it with the pinned seeds and tags each record `synthetic`. When real data exists, a new epoch replaces the synthetic data.
- One change contains code-loop files or evaluation-loop files, never both. The change check in CI enforces this.
