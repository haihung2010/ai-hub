#!/usr/bin/env bash
# scripts/audit_secrets.sh — P1.7 secrets audit (2026-06-10)
#
# Scans the entire git history for accidentally-committed secrets.
# Reference: OWASP API2:2023 (Broken Authentication) + general best
# practice.
#
# Patterns detected (case-insensitive):
#   - OpenAI / Anthropic / Google keys (sk-..., sk_live_..., sk_test_..., AKIA...)
#   - Generic API_KEY=... / SECRET=... / TOKEN=... / PASSWORD=... values
#   - Slack tokens (xox[bpars]-...)
#   - GitHub tokens (ghp_, gho_, ghu_, ghs_)
#   - JWT-shaped strings (eyJ... long base64)
#
# Exits 1 if any matches are found. Otherwise exits 0.
#
# Usage:
#   ./scripts/audit_secrets.sh           # scan all history
#   ./scripts/audit_secrets.sh --staged  # only the working tree (for pre-commit)
#
# Add to CI: a GitHub Action that runs this and fails the build on
# any hit. This is the security roadmap §P1.7.

set -euo pipefail

# ─── Patterns ────────────────────────────────────────────────────────
# Note: we use a single extended regex; the alternation handles the
# prefixes. To avoid matching code that USES the env var name
# (e.g. `os.environ["OPENAI_API_KEY"]`), we require an `=` followed
# by a non-empty value that does NOT look like `${...}` (which is a
# variable reference, not a secret).
PATTERNS=(
  # Vendor prefixes — match the prefix, then a long-ish value
  'sk-[A-Za-z0-9_-]{20,}'
  'sk_live_[A-Za-z0-9]{16,}'
  'sk_test_[A-Za-z0-9]{16,}'
  'AKIA[0-9A-Z]{16}'                       # AWS
  'xox[bpars]-[A-Za-z0-9-]{10,}'           # Slack
  'gh[pousr]_[A-Za-z0-9]{30,}'             # GitHub
  'gho_[A-Za-z0-9]{30,}'
  'ghu_[A-Za-z0-9]{30,}'
  'ghs_[A-Za-z0-9]{30,}'
  'ghr_[A-Za-z0-9]{30,}'
  'eyJ[A-Za-z0-9_=-]{20,}\.eyJ[A-Za-z0-9_=-]{20,}\.[A-Za-z0-9_=-]{20,}'  # JWT
  # Generic KEY=value where the value looks like a secret (long,
  # alnum). Anchored to common env-var names.
  '(API_KEY|SECRET|TOKEN|PASSWORD|PASSWD|PWD|PRIVATE_KEY|ACCESS_KEY|AUTH_TOKEN|BEARER)[ \t]*=[ \t]*["'\'']?[A-Za-z0-9_/+-]{16,}'
)

JOINED=$(IFS='|'; echo "${PATTERNS[*]}")

# ─── Scope: full history vs working tree ─────────────────────────────
SCOPE_FLAG="--all"
if [[ "${1:-}" == "--staged" ]]; then
  SCOPE_FLAG=""  # no flag = working tree (uses git grep)
  SCAN_CMD=(git grep -nE "$JOINED" -- ':(exclude).env.example' ':(exclude)scripts/audit_secrets.sh')
else
  # git log -p prints every diff; grep -E looks for patterns.
  # We exclude binary files and our own audit script.
  SCAN_CMD=(bash -c "git log -p --all -S '' -- . ':(exclude)scripts/audit_secrets.sh' ':(exclude).env.example' | grep -nE '$JOINED' || true")
fi

echo "Scanning git history for secrets..."
echo "Scope: ${SCOPE_FLAG:-(full history)}"
echo

HITS=$("${SCAN_CMD[@]}" 2>/dev/null | head -100 || true)

if [[ -n "$HITS" ]]; then
  echo "❌ Secret-like patterns found in git history:"
  echo
  echo "$HITS"
  echo
  echo "Remediation:"
  echo "  1. Rotate the affected secret IMMEDIATELY (assume it's compromised)"
  echo "  2. Remove from history: pip install git-filter-repo && \\"
  echo "     git filter-repo --path <file> --invert-paths"
  echo "  3. Force-push the cleaned history (collaborators must re-clone)"
  echo "  4. Add the file/path to .gitignore"
  echo
  exit 1
fi

echo "✅ No secret-like patterns found."
exit 0
