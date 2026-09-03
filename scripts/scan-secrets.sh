#!/usr/bin/env bash
# Fail if anything a commit would carry looks like a credential.
#
# Not a replacement for GitHub's own scanning, which runs after a push. This
# runs before one, in the same check that runs the tests, and it knows two
# things a generic scanner does not: which file this project keeps its key in,
# and that the file is supposed to be untracked.
#
# It scans tracked files only. An untracked .env is the normal state of a
# developer's checkout and is not a finding; a tracked one is the whole
# problem.

set -euo pipefail

cd "$(dirname "$0")/.."

fail() {
    echo "SECRET SCAN FAILED: $1" >&2
    exit 1
}

# 1. An environment file that made it into git. .gitignore covers this, and
#    `git add -f` goes straight past .gitignore.
#
#    `.env.example` is the exception, and only because it is the opposite of
#    the problem: a committed template whose whole job is to name the
#    variables without carrying any of the values. Anything ending .example
#    is held to that -- the pattern scan below still reads it.
tracked_env=$(git ls-files | grep -E '(^|/)\.env($|\.)' | grep -v '\.example$' || true)
[ -z "$tracked_env" ] || fail "environment file is tracked: ${tracked_env}"

# 2. Provider keys and private keys, by shape. The prefixes are what these
#    tokens actually start with, and the length floor is what keeps the
#    pattern from matching prose about them.
patterns=(
    'gsk_[A-Za-z0-9]{20,}'
    'sk-[A-Za-z0-9]{20,}'
    'ghp_[A-Za-z0-9]{20,}'
    '-----BEGIN [A-Z ]*PRIVATE KEY-----'
)

# A test that proves keys get redacted has to contain something key-shaped.
# Those lines say so, on the line, with this marker -- so the exemption is
# explicit, local to the one string it covers, and visible in review. A whole
# file is never exempt: the next real key pasted into a test is still a
# finding.
MARKER='secret-scan: synthetic'

for pattern in "${patterns[@]}"; do
    # -I skips binary files; the scan is over what git tracks, so build
    # output and dependencies are out of scope by construction.
    # -e, because a pattern beginning with a dash -- the private-key header
    # does -- is otherwise read as options and silently matches nothing.
    hits=$(git grep -I -n -E -e "${pattern}" -- . ':!scripts/scan-secrets.sh' 2>/dev/null || true)
    hits=$(printf '%s\n' "${hits}" | grep -v "${MARKER}" | grep . || true)
    [ -z "${hits}" ] || fail "credential-shaped string found:"$'\n'"${hits}"
done

echo "secret scan clean: $(git ls-files | wc -l | tr -d ' ') tracked files"
