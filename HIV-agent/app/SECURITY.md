# Security Baseline

This application must not load production secrets from files committed with the
source tree.

- `.env` and `app/.env` are development-only and ignored by git.
- Production secrets must come from the deployment secret manager or injected
  environment variables.
- `CDSS_PATIENT_SALT` is required in production and must be at least 16 bytes.
- `DATABASE_URL`, `PUTER_AUTH_TOKEN`, and `GROQ_API_KEY` must not be logged.
- Local LanceDB indexes and audit stores under `app/data/` are runtime artifacts.
