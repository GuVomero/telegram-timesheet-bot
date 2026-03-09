# Security Policy

## Reporting a vulnerability

If you find a security issue (credential leak, token exposure, unsafe data handling), do not open a public issue with sensitive details.

Send a private report with:

- Description of the issue
- Reproduction steps
- Potential impact
- Suggested fix (if available)

## Sensitive data handling

This project may process personal and attendance data.

- Keep `.env` local and private.
- Keep generated files local (`data/`, `reports/`).
- Rotate any leaked Telegram bot token immediately.
