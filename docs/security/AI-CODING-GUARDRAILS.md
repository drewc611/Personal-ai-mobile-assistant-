# Guardrails for AI-assisted coding

These rules bind every change made in this repository, whether a person or an
AI assistant (Claude Code, Copilot, Codex, Cursor) writes it. They exist
because the September 2026 audit of this account's repositories found the same
defect class repeated by code generators: a working system shipped with a
placeholder credential, a secret given a "safe looking" default, or a debug
switch left on. Each rule names the failure it stops and the check that
enforces it.

## The rules

1. **No secret ever has a literal value in the repository.** Not in code,
   config, compose files, workflows, notebooks, or docs. A secret is read from
   the environment (or a secret manager) at runtime. A file that documents a
   secret uses `.env.example` with an empty value and a comment.
   *Stops:* CWE-798 hard-coded credentials. *Enforced by:* gitleaks
   (`.gitleaks.toml`), `scripts/check-placeholder-secrets.sh`.

2. **A missing secret fails closed, never falls back.** `os.environ["X"]`
   raises; `${X:?X is required}` stops `docker compose`; a config loader
   raises at import. Never `os.getenv("X", "dev-secret")`, never
   `process.env.X || "changeme"`, never `${X:-password}`.
   *Stops:* CWE-1188 insecure defaults that survive into production.
   *Enforced by:* placeholder check, category 2.

3. **Placeholder words are banned in committed values.** `change-me`,
   `changeme`, `change-this-secret`, `dev-secret`, `password123`, `admin123`,
   `letmein`, `supersecret` and their variants never appear outside a test
   fixture. If a value is fake, the file lives under `tests/` or `fixtures/`.
   *Enforced by:* placeholder check, category 1; gitleaks rule
   `placeholder-credential`.

4. **Authentication is on by default.** A compose or config default of
   `AUTH_MODE=disabled`, `REQUIRE_AUTH=false` or an empty API-key list is a
   production outage waiting to become a breach. Local development sets the
   variable explicitly in a gitignored `.env`.
   *Enforced by:* placeholder check, category 3.

5. **No debug servers.** `app.run(debug=True)`, `DEBUG=True` defaults, and
   `0.0.0.0` binds without an explicit opt-in are refused. The Werkzeug
   debugger executes arbitrary Python from the browser (Pallets, n.d.).
   *Enforced by:* placeholder check, category 3.

6. **Every third-party GitHub Action is pinned to a full commit SHA** with the
   version in a trailing comment. A tag can be moved; a SHA cannot (GitHub,
   n.d.-a). Workflows declare the least `permissions:` they need and never
   interpolate `${{ github.event.* }}` text into a `run:` step; pass it through
   `env:` instead (GitHub, n.d.-a).

7. **Untracked secret files stay untracked.** `.gitignore` carries `.env`,
   `.env.*`, `*.pem`, `*.key`, `*.tfstate`, `*.tfvars` and the pre-commit hook
   `forbid-dotenv` refuses the commit.

8. **A dependency with a known vulnerability is a finding, not a note.**
   `npm audit --omit=dev`, `pip-audit -r requirements.txt` run in CI;
   Dependabot is enabled. A High or Critical without a fix version is
   documented in `SECURITY.md` with the compensating control.

9. **Containers do not run as root.** Every Dockerfile ends with a `USER`
   directive; Kubernetes manifests set `allowPrivilegeEscalation: false` and
   `runAsNonRoot: true`.

10. **Tests never disable a control to pass.** A test that needs a token
    generates one at runtime (`secrets.token_hex`), it does not hard-code one
    the application would accept.

## What an AI assistant must do before it commits

- Run `bash scripts/check-placeholder-secrets.sh` and `gitleaks dir . --config .gitleaks.toml`. Both must print clean.
- Grep your own diff for `password`, `secret`, `token`, `key`, `:-`, `debug=True`. Explain every hit in the commit message or remove it.
- When a value is unknown, leave the variable **unset and required**, and say so in the PR body. Do not invent a value so the code runs.
- When adding a workflow, copy the SHA-pinned `uses:` lines from `.github/workflows/secret-scan.yml`.

## Local setup (one time)

```bash
pip install pre-commit        # or: pipx install pre-commit
pre-commit install            # installs the hooks in .pre-commit-config.yaml
```

## References (APA)

- Cybersecurity and Infrastructure Security Agency. (2023). *Secure by design: Shifting the balance of cybersecurity risk*. https://www.cisa.gov/securebydesign
- Docker, Inc. (n.d.). *Interpolation* (Compose file reference). https://docs.docker.com/reference/compose-file/interpolation/
- GitHub. (n.d.-a). *Security hardening for GitHub Actions*. https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions
- GitHub Security Lab. (2021). *Keeping your GitHub Actions and workflows secure part 1: Preventing pwn requests*. https://securitylab.github.com/resources/github-actions-preventing-pwn-requests/
- Gitleaks. (n.d.). *Gitleaks* [Computer software]. https://github.com/gitleaks/gitleaks
- MITRE. (n.d.-a). *CWE-798: Use of hard-coded credentials*. https://cwe.mitre.org/data/definitions/798.html
- MITRE. (n.d.-b). *CWE-1188: Initialization of a resource with an insecure default*. https://cwe.mitre.org/data/definitions/1188.html
- MITRE. (n.d.-c). *CWE-489: Active debug code*. https://cwe.mitre.org/data/definitions/489.html
- National Institute of Standards and Technology. (2020). *Security and privacy controls for information systems and organizations* (NIST SP 800-53 Rev. 5). https://doi.org/10.6028/NIST.SP.800-53r5
- OpenSSF. (n.d.). *Scorecard checks: Pinned-Dependencies*. https://github.com/ossf/scorecard/blob/main/docs/checks.md#pinned-dependencies
- OWASP Foundation. (2021). *OWASP Top 10:2021*. https://owasp.org/Top10/
- OWASP Foundation. (n.d.). *Secrets management cheat sheet*. https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html
- Pallets. (n.d.). *Debugging application errors* (Flask documentation). https://flask.palletsprojects.com/en/stable/debugging/
- pre-commit. (n.d.). *pre-commit: A framework for managing multi-language pre-commit hooks*. https://pre-commit.com
