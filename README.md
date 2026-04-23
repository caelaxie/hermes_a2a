# a2a

Minimal scaffold for a Hermes directory plugin repository.

This repository is shaped to be placed directly at `~/.hermes/plugins/a2a/`.

- the root `plugin.yaml` is the manifest Hermes reads
- the root `__init__.py` wires schemas to handlers
- the root `schemas.py` defines the tool contract the model sees
- the root `tools.py` implements the handler logic
- the plugin registers one smoke-test tool, `a2a_status`
- the plugin also registers a small CLI surface: `hermes a2a status`

## Layout

```text
.
├── __init__.py
├── plugin.yaml
├── schemas.py
├── tests/
    ├── test_register.py
    ├── test_root_plugin.py
    └── test_tools.py
└── tools.py
```

## Local development

```bash
python3 -m unittest discover -s tests -v
```

## Deployment

```bash
mkdir -p ~/.hermes/plugins
git clone <repo-url> ~/.hermes/plugins/a2a
```

Then restart Hermes and verify:

```bash
hermes plugins list
hermes a2a status
```

## Next implementation steps

- Replace the placeholder `a2a_status` handler with real A2A integration logic.
- Add any required environment variables to the root `plugin.yaml`.
- Expand the schemas and handlers once the A2A contract is defined.
