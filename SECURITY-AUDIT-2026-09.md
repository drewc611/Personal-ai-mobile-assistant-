# Security audit — Personal-ai-mobile-assistant- — 2026-09-27

Part of a 22-repository audit of this account. The cross-repository report (method, pain points, business impact, solution analysis, roadmap) is published at https://claude.ai/artifact/KgdrC9eNyCwdqjvSfwMNuB.

## Summary for this repository

| Severity | Count |
|---|---|
| Low | 3 |

**AI-generated placeholder / default credential findings (★):** PA-1 (open)

Automated passes run against this repository: gitleaks 8.24.2 (full history and tree), the placeholder-credential checker now shipped in `scripts/`, semgrep 1.178.0 (`p/security-audit`, `p/secrets`, `p/owasp-top-ten`, `p/github-actions`), bandit, pip-audit and npm audit where applicable, plus a manual review of auth, input handling, workflows and deployment files.

## Findings

| ID | Severity | Category | Location | Evidence | Impact | Fix | Status |
|---|---|---|---|---|---|---|---|
| PA-1 ★ | Low | Known salt fallback for hashed phone numbers | `errand/ingress/handler.py:50` | `os.environ.get("ERRAND_NUMBER_SALT", "errand")` | Outside Terraform (which sets a random salt) rejected senders' numbers are brute-forceable from audit rows. | Fail loudly when the salt is unset. | open |
| PA-2 | Low | IAM wildcards and a broad deploy role | `errand/infra/iam.tf:117-203; bootstrap/deploy_role.tf:22-34` | `resources = ["*"]` for Transcribe/AgentCore; service-wide `*` on the deploy role (permissions boundary and explicit denies present) | Blast radius larger than needed. | Narrow with conditions or ARN prefixes. | open |
| PA-3 | Low | Floating dependency floors | `requirements-lambda.txt; requirements.txt` | `boto3>=1.35`, `strands-agents>=0.1`, `bedrock-agentcore>=0.1` | Lambda artifact varies run to run (weekly pip-audit mitigates). | Pin exact versions with hashes. | open |

## Guardrails added in this change

- `scripts/check-placeholder-secrets.sh` — fails the build on placeholder credentials, secret defaults, disabled-auth defaults, `debug=True`, literal secret assignments, private keys and committed `.env` files.
- `.gitleaks.toml` — gitleaks defaults plus custom placeholder rules and a fixture allowlist.
- `.github/workflows/secret-scan.yml` — runs both on every push and pull request and weekly over full history (SHA-pinned actions).
- `.pre-commit-config.yaml` — the same checks locally; run `pre-commit install` once.
- `docs/security/AI-CODING-GUARDRAILS.md` — the binding rules for any AI-assisted change, with references.
- A "Security rules for AI-assisted changes" section in `CLAUDE.md` (and `AGENTS.md` / Copilot instructions where present).
- `.gitignore` rules for `.env`, keys and Terraform state where they were missing.

See the cross-repository report for the fail-closed pattern by language and the prioritised fix list.
