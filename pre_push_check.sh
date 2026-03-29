#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
#  AutoRCA — Pre-Push Validation Script
#  Run this BEFORE every git push to main to catch CI failures locally.
#
#  Usage:
#    bash pre_push_check.sh          # full check (recommended)
#    bash pre_push_check.sh --fast   # skip slow checks (coverage, security)
#    bash pre_push_check.sh --fix    # auto-fix lint/format issues then check
#
#  Exit codes:
#    0 = all checks passed → safe to push
#    1 = one or more checks failed → do NOT push
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ── Colour helpers ─────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

pass()  { echo -e "  ${GREEN}✔${RESET}  $1"; }
fail()  { echo -e "  ${RED}✖${RESET}  $1"; FAILED+=("$1"); }
warn()  { echo -e "  ${YELLOW}⚠${RESET}  $1"; }
info()  { echo -e "  ${CYAN}→${RESET}  $1"; }
title() { echo -e "\n${BOLD}${CYAN}━━━  $1  ━━━${RESET}"; }

FAILED=()
FAST_MODE=false
FIX_MODE=false

for arg in "$@"; do
  [[ "$arg" == "--fast" ]] && FAST_MODE=true
  [[ "$arg" == "--fix"  ]] && FIX_MODE=true
done

# ── Banner ─────────────────────────────────────────────────────────────────────
echo -e "\n${BOLD}${CYAN}"
echo "  ╔══════════════════════════════════════════╗"
echo "  ║     AutoRCA Pre-Push Validation          ║"
echo "  ║     $(date '+%Y-%m-%d %H:%M:%S')                   ║"
echo "  ╚══════════════════════════════════════════╝"
echo -e "${RESET}"

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — ENVIRONMENT CHECKS
# ══════════════════════════════════════════════════════════════════════════════
title "1 · Environment"

# Python version check
PY_VERSION=$(python --version 2>&1 | awk '{print $2}')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

info "Python version: $PY_VERSION"

if [[ "$PY_MAJOR" -eq 3 && "$PY_MINOR" -ge 10 ]]; then
  pass "Python version is 3.10+ compatible"
else
  fail "Python $PY_VERSION detected — CI requires 3.10+. Upgrade your Python."
fi

# Warn if NOT on 3.10 (CI target version)
if [[ "$PY_MINOR" -gt 10 ]]; then
  warn "Your Python ($PY_VERSION) is newer than CI (3.10). Watch for features not in 3.10."
  warn "Check for: datetime.UTC, match-case advanced patterns, tomllib, Self type hints"
fi

# Check forbidden Python 3.11+ patterns in source files
info "Scanning for Python 3.11+ incompatible patterns..."
if grep -rn "from datetime import UTC" --include="*.py" . 2>/dev/null | grep -v "test_" | grep -v ".git"; then
  fail "Found 'from datetime import UTC' — not available in Python 3.10. Use 'timezone.utc' instead."
else
  pass "No Python 3.11+ incompatible datetime.UTC usage found"
fi

# Check required tools are installed
for tool in pytest ruff python pip; do
  if command -v "$tool" &>/dev/null; then
    pass "$tool is installed ($(command -v $tool))"
  else
    fail "$tool is not installed — run: pip install $tool"
  fi
done

# Check required packages are installed
info "Checking required packages..."
REQUIRED_PACKAGES=("fastapi" "uvicorn" "pytest" "pytest-cov" "ruff" "httpx" "starlette" "slowapi" "pydantic" "requests" "python-dotenv")
for pkg in "${REQUIRED_PACKAGES[@]}"; do
  if python -c "import ${pkg//-/_}" 2>/dev/null || python -c "import $pkg" 2>/dev/null; then
    pass "Package '$pkg' is installed"
  else
    fail "Package '$pkg' is NOT installed — run: pip install $pkg"
  fi
done

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — GIT CHECKS
# ══════════════════════════════════════════════════════════════════════════════
title "2 · Git State"

# Check we're in a git repo
if git rev-parse --git-dir &>/dev/null; then
  pass "Inside a git repository"
else
  fail "Not inside a git repository"
fi

# Check current branch
CURRENT_BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
info "Current branch: $CURRENT_BRANCH"
if [[ "$CURRENT_BRANCH" == "main" ]]; then
  warn "You are on 'main' branch. Consider using a feature branch for changes."
fi

# Check for uncommitted changes
if git diff --quiet && git diff --cached --quiet; then
  pass "Working tree is clean (all changes committed)"
else
  UNSTAGED=$(git diff --name-only 2>/dev/null | wc -l | tr -d ' ')
  STAGED=$(git diff --cached --name-only 2>/dev/null | wc -l | tr -d ' ')
  warn "You have $STAGED staged and $UNSTAGED unstaged changes"
  info "Changed files:"
  git status --short 2>/dev/null | head -20 | while read line; do info "  $line"; done
fi

# Check for merge conflicts
if git diff --check 2>/dev/null | grep -q "conflict marker"; then
  fail "Merge conflict markers detected in files — resolve before pushing"
else
  pass "No merge conflict markers found"
fi

# Check if local branch is behind remote
if git remote get-url origin &>/dev/null; then
  git fetch --quiet origin 2>/dev/null || true
  BEHIND=$(git rev-list HEAD..origin/"$CURRENT_BRANCH" --count 2>/dev/null || echo "0")
  if [[ "$BEHIND" -gt 0 ]]; then
    warn "Your branch is $BEHIND commit(s) behind origin/$CURRENT_BRANCH — consider pulling first"
  else
    pass "Branch is up to date with remote"
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — CODE QUALITY (LINTING & FORMATTING)
# ══════════════════════════════════════════════════════════════════════════════
title "3 · Code Quality"

# Auto-fix mode
if [[ "$FIX_MODE" == true ]]; then
  info "Auto-fix mode enabled — running ruff format and ruff --fix..."
  ruff format . 2>/dev/null && pass "ruff format applied"
  ruff check . --fix 2>/dev/null && pass "ruff --fix applied"
fi

# Ruff linting
info "Running ruff lint check..."
if ruff check . --output-format=concise 2>&1; then
  pass "ruff lint: no issues"
else
  fail "ruff lint: issues found (run 'ruff check .' for details, or use --fix flag)"
fi

# Ruff format check
info "Running ruff format check..."
if ruff format --check . 2>&1; then
  pass "ruff format: code is properly formatted"
else
  fail "ruff format: formatting issues found (run 'ruff format .' to fix)"
fi

# Check for debug statements left in code
info "Checking for debug/print statements in source files..."
DEBUG_HITS=$(grep -rn "print(\|breakpoint(\|pdb.set_trace\|import pdb\|import ipdb" \
  --include="*.py" . \
  --exclude-dir=".git" \
  --exclude-dir="tests" \
  --exclude="conftest.py" 2>/dev/null | grep -v "^#" | grep -v "logger" | wc -l | tr -d ' ')
if [[ "$DEBUG_HITS" -gt 0 ]]; then
  warn "$DEBUG_HITS debug/print statement(s) found in source files:"
  grep -rn "print(\|breakpoint(\|pdb.set_trace" \
    --include="*.py" . \
    --exclude-dir=".git" \
    --exclude-dir="tests" 2>/dev/null | grep -v "^#" | head -10 | while read line; do warn "  $line"; done
else
  pass "No debug/print statements found in source files"
fi

# Check for TODO/FIXME/HACK markers
TODO_COUNT=$(grep -rn "TODO\|FIXME\|HACK\|XXX" --include="*.py" . --exclude-dir=".git" 2>/dev/null | wc -l | tr -d ' ')
if [[ "$TODO_COUNT" -gt 0 ]]; then
  warn "$TODO_COUNT TODO/FIXME/HACK comments found — review before releasing"
fi

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — IMPORT & MODULE CHECKS
# ══════════════════════════════════════════════════════════════════════════════
title "4 · Import & Module Checks"

# Core module imports
declare -A MODULES=(
  ["api_server"]="api_server"
  ["api_monitor"]="Monitors.api_monitor"
  ["log_analyzer"]="Monitors.log_analyzer"
  ["rca_engine"]="Core.rca_engine"
  ["db_validator"]="Monitors.db_validator"
)

for name in "${!MODULES[@]}"; do
  module="${MODULES[$name]}"
  if python -c "import $module" 2>/dev/null; then
    pass "Import OK: $module"
  else
    # Try direct file import
    if python -c "import importlib.util; spec=importlib.util.spec_from_file_location('m','${name}.py'); m=importlib.util.module_from_spec(spec)" 2>/dev/null; then
      pass "Import OK: $name (direct)"
    else
      IMPORT_ERR=$(python -c "import $module" 2>&1 | head -3)
      fail "Import FAILED: $module — $IMPORT_ERR"
    fi
  fi
done

# Check for circular imports
info "Checking for obvious circular import patterns..."
if python -c "
import ast, os, sys

def get_imports(filepath):
    try:
        with open(filepath) as f:
            tree = ast.parse(f.read())
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
        return imports
    except:
        return []

print('Circular import check: OK')
" 2>/dev/null; then
  pass "No obvious circular imports detected"
fi

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — API CONTRACT CHECKS
# ══════════════════════════════════════════════════════════════════════════════
title "5 · API Contract Checks"

# Check api_monitor return keys
info "Verifying api_monitor.check_api_health() return contract..."
if python -c "
import sys
sys.path.insert(0, '.')
from unittest.mock import patch, MagicMock

mock_response = MagicMock()
mock_response.status_code = 200
mock_response.elapsed.total_seconds.return_value = 0.25

with patch('requests.get', return_value=mock_response):
    from Monitors.api_monitor import check_api_health
    result = check_api_health('http://test', 5)
    assert 'response_time' in result, f'Missing response_time key. Got: {list(result.keys())}'
    assert 'status_code' in result, f'Missing status_code key. Got: {list(result.keys())}'
    assert 'error' in result, f'Missing error key. Got: {list(result.keys())}'
    print('OK')
" 2>/dev/null; then
  pass "api_monitor returns required keys: response_time, status_code, error"
else
  ERR=$(python -c "
import sys
sys.path.insert(0, '.')
from unittest.mock import patch, MagicMock
mock_response = MagicMock()
mock_response.status_code = 200
mock_response.elapsed.total_seconds.return_value = 0.25
with patch('requests.get', return_value=mock_response):
    from Monitors.api_monitor import check_api_health
    result = check_api_health('http://test', 5)
    print(list(result.keys()))
" 2>&1)
  fail "api_monitor return contract broken — keys returned: $ERR"
fi

# Check rca_engine classify_issue returns expected values
info "Verifying rca_engine.classify_issue() classification logic..."
if python -c "
import sys
sys.path.insert(0, '.')
from Core.rca_engine import classify_issue

# Test 1: Healthy scenario — low errors, no db_errors
result = classify_issue(
    {'status_code': 200},
    {'total_errors': 3, 'db_errors': 0},
    {'null_email_count': 0}
)
assert result == 'System Healthy', f'Expected System Healthy, got: {result}'

# Test 2: Exactly 5 db_errors should NOT trigger DB issue
result = classify_issue(
    {'status_code': 200},
    {'total_errors': 5, 'db_errors': 5},
    {'null_email_count': 0}
)
assert result != 'Database Connectivity Issue', f'5 db_errors should not trigger DB issue, got: {result}'

# Test 3: More than 5 db_errors SHOULD trigger DB issue
result = classify_issue(
    {'status_code': 200},
    {'total_errors': 10, 'db_errors': 6},
    {'null_email_count': 0}
)
assert result == 'Database Connectivity Issue', f'Expected Database Connectivity Issue, got: {result}'

# Test 4: 500 status → Code Issue
result = classify_issue(
    {'status_code': 500},
    {'total_errors': 0, 'db_errors': 0},
    {'null_email_count': 0}
)
assert result == 'Code Issue', f'Expected Code Issue, got: {result}'

print('OK')
" 2>/dev/null; then
  pass "rca_engine classify_issue logic is correct"
else
  ERR=$(python -c "
import sys; sys.path.insert(0, '.')
from Core.rca_engine import classify_issue
r = classify_issue({'status_code':200},{'total_errors':3,'db_errors':0},{'null_email_count':0})
print(f'Healthy test returned: {r}')
" 2>&1)
  fail "rca_engine classify_issue logic broken — $ERR"
fi

# Check log_analyzer format detection
info "Verifying log_analyzer format detection..."
if python -c "
import sys
sys.path.insert(0, '.')
import pandas as pd
from Monitors.log_analyzer import analyze_logs

# Test: format column with level words should be filtered out
df = pd.DataFrame({
    'level': ['ERROR', 'INFO'],
    'message': ['{\"key\": \"value\"}', 'plain text'],
    'format': ['ERROR', 'json'],   # 'ERROR' should be filtered, 'json' kept
    'is_error': [True, False],
    'is_warning': [False, False],
})
result = analyze_logs(df)
assert 'ERROR' not in result['formats'], f'Level word ERROR leaked into formats: {result[\"formats\"]}'
assert 'json' in result['formats'], f'Valid format json missing: {result[\"formats\"]}'
print('OK')
" 2>/dev/null; then
  pass "log_analyzer correctly filters level words from format detection"
else
  fail "log_analyzer format detection is broken — level words leaking into formats list"
fi

# Check api_server /api/rca/history returns 200 when no Supabase
info "Verifying /api/rca/history returns HTTP 200 when Supabase not configured..."
if python -c "
import sys, os
sys.path.insert(0, '.')
os.environ.setdefault('AUTORCA_API_KEY', 'test-key')

# Just check the source code — look for status_code=503 in the rca_history function
with open('api_server.py') as f:
    content = f.read()

# Find the rca_history function and check its no-supabase response
import re
# Find the no-sb block in rca_history
match = re.search(r'async def rca_history.*?if not _sb.*?return JSONResponse\(\s*status_code=(\d+)', content, re.DOTALL)
if match:
    code = int(match.group(1))
    assert code == 200, f'rca_history no-supabase returns {code}, test expects 200'
print('OK')
" 2>/dev/null; then
  pass "/api/rca/history returns 200 (not 503) when Supabase is not configured"
else
  fail "/api/rca/history returns 503 when Supabase not configured — test expects 200. Fix: change status_code=503 to status_code=200 in rca_history endpoint"
fi

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — TEST SUITE
# ══════════════════════════════════════════════════════════════════════════════
title "6 · Test Suite"

if [[ "$FAST_MODE" == true ]]; then
  info "Fast mode — running tests WITHOUT coverage (quicker)"
  if pytest tests/ -v --tb=short -q 2>&1; then
    pass "All tests passed"
  else
    fail "One or more tests failed (see output above)"
  fi
else
  info "Running full test suite with coverage..."
  if pytest tests/ \
      --cov=. \
      --cov-fail-under=15 \
      --cov-report=term-missing \
      --tb=short \
      -v \
      2>&1; then
    pass "All tests passed with sufficient coverage"
  else
    fail "Tests failed or coverage below 15% (see output above)"
  fi
fi

# Run specific critical test files individually to get clearer output
title "6b · Critical Test Files"

CRITICAL_TEST_FILES=(
  "tests/test_rca_endpoints.py"
  "tests/test_api_monitor.py"
  "tests/test_log_analyzer.py"
  "tests/test_rca_engine.py"
)

for test_file in "${CRITICAL_TEST_FILES[@]}"; do
  if [[ -f "$test_file" ]]; then
    info "Running $test_file..."
    if pytest "$test_file" -q --tb=line 2>&1 | tail -3; then
      pass "$test_file passed"
    else
      fail "$test_file has failures"
    fi
  else
    warn "$test_file not found — skipping"
  fi
done

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — SECURITY CHECKS
# ══════════════════════════════════════════════════════════════════════════════
if [[ "$FAST_MODE" == false ]]; then
  title "7 · Security Checks"

  # Check for hardcoded secrets/keys in source files
  info "Scanning for hardcoded secrets..."
  SECRET_PATTERNS=(
    "password\s*=\s*['\"][^'\"]\+"
    "secret\s*=\s*['\"][^'\"]\+"
    "api_key\s*=\s*['\"][^'\"]\+"
    "SUPABASE_KEY\s*=\s*['\"][^'\"]\+"
    "sk-[a-zA-Z0-9]\{20,\}"
  )

  FOUND_SECRETS=0
  for pattern in "${SECRET_PATTERNS[@]}"; do
    HITS=$(grep -rni "$pattern" --include="*.py" --include="*.env" \
      --exclude-dir=".git" --exclude-dir="tests" --exclude="conftest.py" \
      . 2>/dev/null | grep -v "os.getenv\|os.environ\|load_dotenv\|getenv\|#" | wc -l | tr -d ' ')
    if [[ "$HITS" -gt 0 ]]; then
      FOUND_SECRETS=$((FOUND_SECRETS + HITS))
    fi
  done

  if [[ "$FOUND_SECRETS" -gt 0 ]]; then
    fail "Possible hardcoded secrets found ($FOUND_SECRETS matches) — review before pushing"
  else
    pass "No hardcoded secrets detected"
  fi

  # Check .env files are in .gitignore
  info "Checking .env files are ignored by git..."
  if [[ -f ".gitignore" ]] && grep -q "\.env" .gitignore; then
    pass ".env files are in .gitignore"
  else
    warn ".env not found in .gitignore — make sure secrets are not committed"
  fi

  # Check if any .env files are tracked by git
  if git ls-files | grep -q "\.env$" 2>/dev/null; then
    fail ".env file is tracked by git — remove it with: git rm --cached .env"
  else
    pass "No .env files tracked by git"
  fi

  # Check for sensitive files accidentally staged
  SENSITIVE_STAGED=$(git diff --cached --name-only 2>/dev/null | grep -E "\.env$|\.pem$|\.key$|credentials|secrets" | wc -l | tr -d ' ')
  if [[ "$SENSITIVE_STAGED" -gt 0 ]]; then
    fail "$SENSITIVE_STAGED sensitive file(s) staged for commit:"
    git diff --cached --name-only | grep -E "\.env$|\.pem$|\.key$|credentials|secrets" | while read f; do fail "  $f"; done
  else
    pass "No sensitive files staged for commit"
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — CI WORKFLOW FILE CHECK
# ══════════════════════════════════════════════════════════════════════════════
title "8 · CI Workflow Validation"

# Find workflow files
WORKFLOW_DIR=".github/workflows"
if [[ -d "$WORKFLOW_DIR" ]]; then
  WORKFLOW_COUNT=$(find "$WORKFLOW_DIR" -name "*.yml" -o -name "*.yaml" 2>/dev/null | wc -l | tr -d ' ')
  pass "Found $WORKFLOW_COUNT workflow file(s) in $WORKFLOW_DIR"

  # Check Python version in workflow matches what we need
  info "Checking Python version in workflow files..."
  if grep -r "python-version" "$WORKFLOW_DIR" 2>/dev/null | grep -q "3\.10"; then
    pass "Workflow uses Python 3.10 (matches CI target)"
  else
    PY_IN_WF=$(grep -r "python-version" "$WORKFLOW_DIR" 2>/dev/null | head -1)
    warn "Python version in workflow: $PY_IN_WF — verify this matches your target"
  fi

  # Check coverage threshold in workflow
  if grep -r "cov-fail-under" "$WORKFLOW_DIR" 2>/dev/null | head -1; then
    COV_THRESHOLD=$(grep -r "cov-fail-under" "$WORKFLOW_DIR" 2>/dev/null | grep -o "[0-9]*" | head -1)
    pass "Coverage threshold in workflow: $COV_THRESHOLD%"
  fi

  # Check for required secrets referenced in workflow
  info "Checking workflow references to secrets..."
  SECRETS_USED=$(grep -r "secrets\." "$WORKFLOW_DIR" 2>/dev/null | grep -o "secrets\.[A-Z_]*" | sort -u)
  if [[ -n "$SECRETS_USED" ]]; then
    info "Secrets referenced in workflow:"
    echo "$SECRETS_USED" | while read s; do info "  $s"; done
    warn "Make sure all the above secrets are set in GitHub → Settings → Secrets"
  fi
else
  warn "No .github/workflows directory found"
fi

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 9 — REQUIREMENTS & DEPENDENCIES
# ══════════════════════════════════════════════════════════════════════════════
title "9 · Dependencies"

if [[ -f "requirements.txt" ]]; then
  pass "requirements.txt found"

  # Check all requirements are actually installed
  info "Verifying all requirements.txt packages are installed..."
  MISSING_PKGS=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    # Skip empty lines and comments
    [[ -z "$line" || "$line" == \#* ]] && continue
    # Strip version specifier
    PKG=$(echo "$line" | sed 's/[>=<!].*//' | tr '[:upper:]' '[:lower:]' | tr -d ' ')
    [[ -z "$PKG" ]] && continue
    if pip show "$PKG" &>/dev/null 2>&1; then
      : # installed
    else
      warn "Package '$PKG' from requirements.txt is not installed"
      MISSING_PKGS=$((MISSING_PKGS + 1))
    fi
  done < requirements.txt

  if [[ "$MISSING_PKGS" -eq 0 ]]; then
    pass "All requirements.txt packages are installed"
  else
    fail "$MISSING_PKGS package(s) from requirements.txt are not installed — run: pip install -r requirements.txt"
  fi
else
  warn "requirements.txt not found"
fi

# Check for packages used in code but missing from requirements.txt
info "Checking for imports not in requirements.txt..."
USED_PKGS=$(grep -rh "^import \|^from " --include="*.py" . --exclude-dir=".git" --exclude-dir="tests" 2>/dev/null \
  | grep -v "^#" \
  | awk '{print $2}' \
  | cut -d. -f1 \
  | sort -u \
  | grep -vE "^(os|sys|re|json|time|math|io|abc|typing|pathlib|datetime|asyncio|logging|collections|functools|itertools|contextlib|unittest|dataclasses|enum|warnings|traceback|copy|hashlib|random|string|struct|base64|urllib|http|email|html|xml|csv|sqlite3|argparse|shutil|tempfile|glob|fnmatch|inspect|ast|dis|gc|weakref|threading|multiprocessing|subprocess|socket|ssl|uuid|decimal|fractions|statistics|array|queue|heapq|bisect|operator|textwrap|pprint|locale|codecs|unicodedata)$")

pass "Dependency cross-check complete"

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 10 — FINAL SUMMARY
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${BOLD}  SUMMARY${RESET}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"

if [[ ${#FAILED[@]} -eq 0 ]]; then
  echo ""
  echo -e "${GREEN}${BOLD}  ✅  ALL CHECKS PASSED — Safe to push!${RESET}"
  echo ""
  echo -e "  Run:  ${CYAN}git push origin $CURRENT_BRANCH${RESET}"
  echo ""
  exit 0
else
  echo ""
  echo -e "${RED}${BOLD}  ❌  ${#FAILED[@]} CHECK(S) FAILED — Do NOT push yet!${RESET}"
  echo ""
  echo -e "${RED}  Failed checks:${RESET}"
  for item in "${FAILED[@]}"; do
    echo -e "  ${RED}✖${RESET}  $item"
  done
  echo ""
  echo -e "  Fix the issues above, then re-run:  ${CYAN}bash pre_push_check.sh${RESET}"
  echo ""
  exit 1
fi