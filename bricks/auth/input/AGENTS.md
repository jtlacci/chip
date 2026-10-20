# Input contract

This folder owns every external-source boundary and sibling-brick boundary for the brick.

## Configuration

- Keep brick configuration in `config.yml`. The runtime passes it to the logic as `ctx["config"]`.
- `config.yml` uses the YAML subset in `bricks/_yaml.py`.
- Never store credentials or secrets in configuration. The runtime records every brick input, output and exchange, so a secret must never be part of one.

## Adapters

- Create one Python file under `adapters/` for each external source or sibling brick.
- APIs, databases, filesystems, queues, clocks, and services are external sources beyond the repository boundary.
- Other bricks in this repository are sibling bricks, not external sources.
- An adapter fetches or sends data; it does not contain domain logic.
- Adapters are called by `src/` with the run context. Adapters never import or call `src/` or `runner/`.
- Every call goes through the run context's io, never directly:
  - an external source: `ctx["io"].external("<source name>", request, call)`
  - a sibling brick: `ctx["io"].sibling("<brick>", request, run)`, where `run` is the sibling's top-level `run`, passed and not called
- In production the io makes the call and records it. In evaluation it replays the recorded response. The adapter never knows which, and never branches on a mode.
- Requests and responses must be JSON values.
- Translate the raw response into this brick's own types after the call. The evaluation runner injects recorded data below this translation, so a change to the translation is tested, not trusted.
