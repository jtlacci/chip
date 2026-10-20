# Source contract

This folder owns the brick's private domain logic. Its internal organization is otherwise unrestricted.

- Receive the brick input and the run context from `runner/`.
- Decide when external or sibling-brick data is needed and call adapters from this brick's `input/` folder.
- Pass the run context into every adapter.
- Never branch on a mode. There is none: production and evaluation differ only in the io inside the run context.
- Do not perform direct network, database, filesystem, subprocess, environment, clock, or sibling-brick calls. Those calls must go through adapters.
- Do not import sibling bricks directly. Import only this brick's input adapters.
- Libraries are allowed only when their use is local and does not make external calls.
- Keep behavior deterministic for the same supplied inputs unless randomness is explicitly passed in. A brick that declares `LANE = "pure"` has the linter hold it to this: no clock calls, no `random` or `time`, no adapters at all.
- Export functions only for use by this brick's `runner/`; the repository-internal brick entry point remains `run` alone.
- There are no tests in this folder. Domain rules and invariants are checked as properties in `evaluation/`, where the dev agent cannot change them.
- Split the brick when unrelated domain responsibilities make this folder change together repeatedly.
