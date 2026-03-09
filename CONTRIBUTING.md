# Contributing

## Local setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python -m src.main
```

## Validation before pull request

```bash
python -m compileall src
```

## Security and privacy rules

- Never commit `.env` or any real credentials/tokens.
- Never commit local databases or reports (`data/`, `reports/`, `*.db`, `*.sqlite*`).
- Use only `.env.example` for configuration examples.
- If a secret is exposed, rotate it immediately before publishing.

## Commit guidelines

- Keep commits small and focused.
- Use clear commit messages in imperative mood.
- Update documentation when behavior changes.
