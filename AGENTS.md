# Repository contract

This repository uses Python file boilerplate to organize one codebase as discrete domain bricks.

## Terminology

- The **repository boundary** separates this repository from APIs, databases, files, services, and other outside systems. Only these are called external.
- A **brick boundary** separates one brick from its sibling bricks inside this repository.
- A **brick entry point** is `run`. It is repository-internal and is called by sibling-brick adapters.
- A **sibling adapter** is an adapter whose target is another brick's `run` entry point.
- The **runtime** is `bricks/_runtime.py`: it runs bricks, routes their io and captures what crosses each boundary.
- The **evaluation runner** is a separate trusted service. It is the only thing that reads evaluation data, runs evaluations and promotes bricks.
- The **code loop** is the dev agent changing a brick's implementation. The **evaluation loop** is the eval agent changing how a brick is evaluated.

## Purpose

- Optimize for human and agent visibility, not framework machinery.
- A brick may be complex inside `src/`, but its contract and its evaluation must make its behavior understandable.
- Evaluation confidence is more important than development speed.
- The framework keeps one small standard-library runtime (`bricks/_runtime.py` and `bricks/_yaml.py`). Do not add other shared runtimes, generators or dependencies unless the user explicitly requests them.
- Keep enforcement in `tools/` lightweight and standard-library-only.

## Non-negotiable rules

- Put every domain brick in its own named folder under `bricks/`.
- Every brick has `input/`, `runner/`, `src/` and `evaluation/`.
- Every brick declares a typed, versioned input and output boundary in `contract.py`, and whether it is deterministic.
- A brick exposes only its `run` entry point to sibling bricks.
- External access crosses an external-source adapter; sibling-brick access crosses a sibling adapter. Every adapter call goes through the run context's io.
- Every sibling dependency declares its consistency policy as `eventual` or `orchestrated`. Dependency cycles are not allowed; introduce or reshape a parent brick instead.
- Every persistent application-state resource has exactly one owning brick.
- Bricks are used only inside this repository. Do not package or expose them for outside consumers.
- There are no mode options, saved examples, run records, unit tests or smoke tests. Evaluation lives in each brick's `evaluation/` folder and runs only in the evaluation runner.
- One change contains code-loop files or evaluation-loop files, never both. `contract.py`, `evaluation/policy.yml` and `evaluation/data.lock` are human-only.
- Never put a credential or secret in a brick input, output, request or response: the runtime records them.

## Contract placement and precedence

`AGENTS.md` rules apply to their folder and descendants. A nested `AGENTS.md` adds rules for its subtree and may not relax a parent rule.

Not every folder needs an `AGENTS.md`. Add one only where the folder introduces a distinct responsibility or additional rule. This boilerplate keeps contracts at the repository root, `bricks/`, and each brick's `input/`, `runner/`, `src/` and `evaluation/` boundaries. A brick-level `AGENTS.md` is optional and should contain only domain-specific additions.

## When to split a brick

Split or reshape a brick when its `run` becomes a large operation dispatcher, its adapter set is no longer easy to understand, or unrelated changes repeatedly touch the same `src/`. Encapsulation is not permission for an internal monolith.
