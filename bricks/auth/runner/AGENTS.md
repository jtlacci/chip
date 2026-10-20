# Runner contract

This folder owns the brick's entry point.

## Brick entry point

- `run.py` contains the brick's only repository-internal entry point: `run`.
- `run` takes exactly one argument, annotated with `BrickInput`, and returns `BrickOutput`. It has no mode options.
- `run` calls `_runtime.run_brick` with the brick's name, the input and the logic function from `src/`. The runtime creates the run ID, loads the configuration, provides the io, calls the logic, and records the run.
- `runner/` does not call input adapters directly.
- Do not expose adapters or `src/` functions to sibling bricks.
- `runner/` is the only part of a brick that may import `bricks._runtime`.

## Records

- The runtime records every run when the deployment sets `BRICK_CAPTURE_DIR`. Nothing in the brick writes run history.
- There are no tests in this folder. Evaluation lives in `evaluation/`.
