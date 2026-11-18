# Contributing

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy && pytest
```

CI runs the same four commands on Python 3.11 to 3.13.

## Pull requests

- Titles follow [Conventional Commits](https://www.conventionalcommits.org/): `feat: ...`, `fix: ...`,
  `chore: ...`. A check enforces it, and the title becomes the squash commit.
- Tests mock the HTTP layer; none may need a real key.

## Where things go

- A tool's name, description, shown fields, defaults and caps: the `google-adk-python` surface in
  the Decodo schema, not this repository.
- A field's type, allowed values or limits: the Decodo API description.
- How a response is read, and what counts as a failure in it: the tool's result rule in the surface.
- How much of a result the model gets, and how long a results tool waits: the tool's `limits` in
  the surface. `decodo_adk/tool_specs.py` holds only the fallback values and the ceilings.
- Tests read `tests/fixtures/ir.json`, a cut of the published schema. Refresh it when the surface
  or the flows change.
