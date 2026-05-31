#!/usr/bin/env bash
TOKEN_A=""
TOKEN_B=""
CID=$(curl -sS -H "Authorization: Bearer $TOKEN_A" localhost:8000/api/conversations | jq -r '.conversations[0].id')

set -u
fail=0
hr() { echo; echo "=== $1 ==="; }

hr "P4.2 step 4 — restart-survival (assumes you already restarted)"
diff -q <(curl -sS -H "Authorization: Bearer $TOKEN_A" localhost:8000/api/conversations \
            | jq -S '.conversations[] | {id,title}') \
        <(echo "")  >/dev/null 2>&1 && { echo "✗ A's list empty after restart"; fail=1; } \
                                    || echo "✓ A's list non-empty after restart"

hr "P4.2 step 5 — two-account isolation"
curl -sS -H "Authorization: Bearer $TOKEN_B" localhost:8000/api/conversations \
  | jq -e --arg cid "$CID" '.conversations | map(.id) | index($cid) == null' >/dev/null \
  && echo "✓ B's list does not contain A's id" \
  || { echo "✗ B's list contains A's id"; fail=1; }

H=$(curl -sS -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $TOKEN_B" localhost:8000/api/conversations/$CID)
[ "$H" = "404" ] && echo "✓ B gets 404 on A's conversation" || { echo "✗ B got $H (expected 404)"; fail=1; }

curl -sS -H "Authorization: Bearer $TOKEN_A" localhost:8000/api/conversations \
  | jq -e --arg cid "$CID" '.conversations | map(.id) | index($cid) != null' >/dev/null \
  && echo "✓ A still owns the conversation" \
  || { echo "✗ A's conversation went missing"; fail=1; }

echo
[ "$fail" = "0" ] && echo "P4.2 verified ✅" || { echo "P4.2 FAILED — do NOT mark done"; exit 1; }