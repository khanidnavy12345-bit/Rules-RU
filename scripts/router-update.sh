#!/bin/sh
set -eu

CONF="${CONF:-/etc/v2raya-github-rules.conf}"
[ -r "$CONF" ] && . "$CONF"

: "${BASE_URL:?set BASE_URL in /etc/v2raya-github-rules.conf}"
: "${V2RAYA_USERNAME:?set V2RAYA_USERNAME in /etc/v2raya-github-rules.conf}"
: "${V2RAYA_PASSWORD:?set V2RAYA_PASSWORD in /etc/v2raya-github-rules.conf}"

API="${V2RAYA_API:-http://127.0.0.1:2017/api}"
ASSET_DIR="${ASSET_DIR:-/usr/share/xray}"
V2RAYA_DB="${V2RAYA_DB:-/etc/v2raya/bolt.db}"
LOCAL_RULES_FILE="${LOCAL_RULES_FILE:-/etc/v2raya-local-rules.txt}"

tmp="/tmp/v2raya-github-rules.$$"
mkdir -p "$tmp"
trap 'rm -rf "$tmp"' EXIT

fetch() {
  curl -fsSL --retry 3 --retry-delay 2 -o "$tmp/$1" "$BASE_URL/$1"
}

login() {
  resp="$(curl -fsS -H 'Content-Type: application/json' \
    --data "{\"username\":\"$V2RAYA_USERNAME\",\"password\":\"$V2RAYA_PASSWORD\"}" \
    "$API/login")"
  TOKEN="$(printf '%s' "$resp" | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')"
  [ -n "$TOKEN" ]
}

wait_api() {
  i=0
  while [ "$i" -lt 20 ]; do
    if login >/dev/null 2>&1; then
      return 0
    fi
    i=$((i + 1))
    sleep 2
  done
  return 1
}

fetch geosite.dat
fetch geoip.dat
fetch v2raya-rules.txt

if [ -s "$LOCAL_RULES_FILE" ]; then
  awk -v local_rules="$LOCAL_RULES_FILE" '
    {
      print
      if (!inserted && $0 == "network(udp) -> direct") {
        while ((getline line < local_rules) > 0) {
          print line
        }
        close(local_rules)
        inserted = 1
      }
    }
    END {
      if (!inserted) {
        print ""
        while ((getline line < local_rules) > 0) {
          print line
        }
        close(local_rules)
      }
    }
  ' "$tmp/v2raya-rules.txt" >"$tmp/v2raya-rules.merged.txt"
  mv "$tmp/v2raya-rules.merged.txt" "$tmp/v2raya-rules.txt"
fi

backup="$tmp/backup"
mkdir -p "$backup"
cp "$ASSET_DIR/geosite.dat" "$backup/geosite.dat" 2>/dev/null || true
cp "$ASSET_DIR/geoip.dat" "$backup/geoip.dat" 2>/dev/null || true
cp "$V2RAYA_DB" "$backup/bolt.db" 2>/dev/null || true

restore() {
  echo "restore previous v2rayA rules/geodata" >&2
  /etc/init.d/v2raya stop >/dev/null 2>&1 || true
  [ -f "$backup/geosite.dat" ] && cp "$backup/geosite.dat" "$ASSET_DIR/geosite.dat"
  [ -f "$backup/geoip.dat" ] && cp "$backup/geoip.dat" "$ASSET_DIR/geoip.dat"
  [ -f "$backup/bolt.db" ] && cp "$backup/bolt.db" "$V2RAYA_DB"
  /etc/init.d/v2raya restart >/dev/null 2>&1 || true
}

cp "$tmp/geosite.dat" "$ASSET_DIR/geosite.dat"
cp "$tmp/geoip.dat" "$ASSET_DIR/geoip.dat"
rm -f "$ASSET_DIR/geoip-asn"

login
awk 'BEGIN { printf "{\"routingA\":\"" }
{
  gsub(/\\/, "\\\\")
  gsub(/"/, "\\\"")
  printf "%s\\n", $0
}
END { printf "\"}" }' "$tmp/v2raya-rules.txt" >"$tmp/routingA.json"

curl -fsS -X PUT \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  --data-binary "@$tmp/routingA.json" \
  "$API/routingA" >/dev/null || { restore; exit 1; }

/etc/init.d/v2raya restart >/dev/null
wait_api || { restore; exit 1; }
curl -fsS -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" --data '{}' "$API/v2ray" >/dev/null || { restore; exit 1; }
sleep 8

if ! /usr/bin/xray run -test -config=/etc/v2raya/config.json >/tmp/v2raya-github-rules-test.log 2>&1; then
  cat /tmp/v2raya-github-rules-test.log >&2 || true
  restore
  exit 1
fi
rm -f /tmp/v2raya-github-rules-test.log

echo "updated v2rayA rules/geodata from $BASE_URL"
ls -lh "$ASSET_DIR/geosite.dat" "$ASSET_DIR/geoip.dat" 2>/dev/null || true
