#!/usr/bin/env bash
# Fails when a placeholder credential, a non-empty default for a secret, or a
# debug server setting is committed. Runs in pre-commit and in CI so an
# AI-generated "change-me" value never reaches main.
#
# Usage: scripts/check-placeholder-secrets.sh [paths...]   (defaults to the tree)
# Exit 1 on any hit. Allowlist a line by appending  # allow-placeholder <reason>
set -u

paths=("$@")
[ ${#paths[@]} -eq 0 ] && paths=(.)

# Directories and files that legitimately hold fake values.
exclude=(--exclude-dir=.git --exclude-dir=node_modules --exclude-dir=dist --exclude-dir=build
         --exclude-dir=.venv --exclude-dir=venv --exclude-dir=__pycache__ --exclude-dir=coverage
         --exclude='*.lock' --exclude='package-lock.json' --exclude='*.min.js' --exclude='*.map'
         --exclude='*.example' --exclude='*.sample' --exclude='*.template' --exclude='*.md'
         --exclude='check-placeholder-secrets.sh' --exclude='.gitleaks.toml' --exclude='.pre-commit-config.yaml')
test_paths='(^|/)(tests?|__tests__|spec|fixtures?|testdata)/|\.(test|spec)\.|node-test|conftest'
comment_or_allowed='allow-placeholder|^[^:]*:[0-9]+:\s*(#|//)'

hits=0
report() { # $1 label, $2 matching lines
  local out
  out="$(printf '%s\n' "$2" | grep -vE "$comment_or_allowed" | grep -v '^$' || true)"
  if [ -n "$out" ]; then
    echo "::error::[$1]"
    echo "$out" | cut -c1-200
    hits=$((hits+1))
  fi
}

# 1. Placeholder vocabulary that AI assistants and scaffolds emit.
report "placeholder credential vocabulary" "$(grep -rniE "${exclude[@]}" \
  -E '\b(change[-_ ]?me|change[-_]this[-_](secret|key|password)|dev[-_]?secret|dev[-_]password|devpassword|super[-_]?secret|secret123|password123|admin123|letmein|hunter2|p@ssw0rd|passw0rd|your[-_]secret[-_]key|my[-_]secret[-_]key|insecure[-_]default|default[-_]secret)\b' \
  "${paths[@]}" 2>/dev/null | grep -vE "$test_paths")"

# 2. Secret-bearing variables given a non-empty default in compose/yaml/shell.
report "secret variable with a non-empty default (use \${VAR:?required} instead)" "$(grep -rnE "${exclude[@]}" \
  --include='*.yml' --include='*.yaml' --include='*.sh' --include='Makefile' --include='Dockerfile*' --include='*.env' \
  -E '\$\{([A-Za-z0-9]+_)*(PASS|PASSWORD|PASSWD|SECRET|TOKEN|API_KEY|APIKEY|PRIVATE_KEY|CREDENTIALS?)(_[A-Za-z0-9]+)*:-[^}$][^}]*\}' \
  "${paths[@]}" 2>/dev/null)"

# 3. Auth switched off or a debug server enabled by default.
report "authentication disabled by default" "$(grep -rnE "${exclude[@]}" --include='*.yml' --include='*.yaml' \
  -E '(AUTH_MODE|AUTH_ENABLED|REQUIRE_AUTH)[A-Za-z_]*:\s*\$\{[^}]*:-(disabled|false|off|none)\}' "${paths[@]}" 2>/dev/null)"
report "Flask debug=True (Werkzeug debugger allows remote code execution)" "$(grep -rnE "${exclude[@]}" --include='*.py' \
  -E 'app\.run\([^)]*debug\s*=\s*True' "${paths[@]}" 2>/dev/null)"

# 4. Literal secrets assigned in source (not read from the environment).
report "literal secret assigned in source" "$(grep -rnE "${exclude[@]}" \
  --include='*.py' --include='*.js' --include='*.mjs' --include='*.ts' --include='*.tsx' --include='*.jsx' --include='*.go' --include='*.rb' --include='*.java' --include='*.cs' \
  -E "(SECRET_KEY|secret_key|JWT_SECRET|jwt_secret|SESSION_SECRET|session_secret|API_KEY|api_key|PRIVATE_KEY|private_key|DB_PASSWORD|db_password)\s*[:=]\s*['\"][^'\"\$]{6,}['\"]" \
  "${paths[@]}" 2>/dev/null | grep -vE "process\.env|os\.environ|getenv|$test_paths")"

# 5. Private key material and dotenv files staged in the tree.
report "private key material" "$(grep -rlE "${exclude[@]}" -E 'BEGIN (RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY' "${paths[@]}" 2>/dev/null | grep -vE "$test_paths")"
report "secret-bearing file committed" "$(find "${paths[@]}" -path '*/.git' -prune -o -path '*/node_modules' -prune -o -type f \
  \( -name '.env' -o -name '.env.*' -o -name '*.pem' -o -name '*.p12' -o -name '*.pfx' -o -name 'id_rsa' -o -name 'id_ed25519' -o -name '*.tfstate' -o -name '*.tfvars' \) -print 2>/dev/null \
  | grep -vE '\.(example|sample|template)$' | grep -vE "$test_paths")"

if [ "$hits" -gt 0 ]; then
  echo
  echo "check-placeholder-secrets: $hits category(ies) failed. Read every value from the environment,"
  echo "fail closed when it is missing (\${VAR:?}), and keep real values in a gitignored .env."
  exit 1
fi
echo "check-placeholder-secrets: clean"
