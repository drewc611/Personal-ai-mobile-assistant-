# CLAUDE.md

Guidance for AI assistants working in this repository.

## Security rules for AI-assisted changes (binding)

Added by the 2026-09 security audit. Full text and references in
`docs/security/AI-CODING-GUARDRAILS.md`; findings in `SECURITY-AUDIT-2026-09.md`.

- Never write a literal secret, token, password or API key anywhere in the repo. Read it from the environment and **fail closed** when it is missing (`os.environ["X"]`, `${X:?required}`). No `getenv("X", "dev-secret")`, no `|| "changeme"`, no `${X:-password}`.
- Never emit placeholder credentials (`change-me`, `dev-secret`, `password123`, `admin123`, `letmein`, `supersecret`). If a value is unknown, leave it required and unset, and say so in the PR.
- Authentication defaults on. Debug servers (`debug=True`) are never committed. Containers run as a non-root `USER`.
- Pin every GitHub Action to a full commit SHA; pass `${{ github.event.* }}` through `env:`, never into `run:`.
- Before committing, run `bash scripts/check-placeholder-secrets.sh` and `gitleaks dir . --config .gitleaks.toml`; both must be clean. CI runs the same checks in `.github/workflows/secret-scan.yml`.
