# Tests

```
tests/
├── backend/    pytest suite for the FastAPI service
└── frontend/   empty -- frontend tests arrive with the UI (milestone M2)
```

## Running

From the repository root, with dev dependencies installed
(`pip install -r backend/requirements-dev.txt`):

```bash
pytest
```

Configuration lives in `pytest.ini` at the repository root, which puts
`backend/` on the import path so tests can `import app`.
