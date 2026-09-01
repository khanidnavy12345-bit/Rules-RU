# v2rayA Rules Builder

This project automates compact routing rules for v2rayA/Xray clients that need reliable access to blocked services while keeping Russian and local traffic direct by default. Instead of manually maintaining router rules and large upstream geodata files, the repository keeps one canonical rule set, rebuilds selected geosite/geoip categories from RunetFreedom sources, and publishes ready-to-use release files.

Key features:

- Automatic rebuilds from the latest RunetFreedom `geosite.dat` and `geoip.dat`.
- Compact geodata: only categories referenced by the rules are copied into the published `.dat` files.
- No `ext:` files: clients only need the standard `geosite.dat` and `geoip.dat` pair.
- Deterministic rule order: explicit blocked/proxy categories win before broad direct fallbacks such as `.ru`, `.su`, and `.xn--p1ai`.
- Fast-path ordering for common services such as Yandex, Telegram, Meta/Instagram, YouTube, and Google.
- Rule item ordering based on category size, so smaller checks are placed before larger geosite/geoip groups where this is safe.
- HAPP import link generation for clients that support `happ://routing/add/...`, including 3x-ui-style HAPP link usage.
- Router-side private exceptions, so personal proxy server addresses can stay on the router and never appear in public GitHub releases.
- GitHub Actions publishes a continuously updated `latest` release with generated rules and compact `.dat` files.

The repository builds these release artifacts:

- `release/v2raya-rules.txt`
- `release/routing.happ`
- `release/routing.happ.json`
- `release/geosite.dat`
- `release/geoip.dat`

The router should only download `v2raya-rules.txt`, `geosite.dat`, and `geoip.dat` from `release/`.
The generated rules do not use `ext:` files so they stay compatible with clients that support only the standard `geoip.dat` and `geosite.dat` pair.

## Latest Files

Always-latest release downloads:

- [`geosite.dat`](https://github.com/khanidnavy12345-bit/Rules-RU/releases/latest/download/geosite.dat)
- [`geoip.dat`](https://github.com/khanidnavy12345-bit/Rules-RU/releases/latest/download/geoip.dat)
- [`v2raya-rules.txt`](https://github.com/khanidnavy12345-bit/Rules-RU/releases/latest/download/v2raya-rules.txt)
- [`routing.happ`](https://github.com/khanidnavy12345-bit/Rules-RU/releases/latest/download/routing.happ)

`routing.happ` contains a `happ://routing/add/...` link for adding the generated routing profile in clients that understand HAPP routing links. This is the file to use when a UI asks for a HAPP rules link, including 3x-ui-related workflows that accept this format.

## Private local exceptions

Public rules must not contain personal proxy server addresses. Keep them in a private ignored file:

```sh
cp rules/local.example.json rules/local.private.json
```

Edit `rules/local.private.json`:

```json
{
  "localDirect": {
    "domains": ["contains:your-vpn-domain"],
    "ips": ["203.0.113.10"]
  }
}
```

Public GitHub Actions builds do not load this file. For a private local build, run:

```sh
LOCAL_RULES_PATH=rules/local.private.json python scripts/build.py
```

On Windows PowerShell:

```powershell
$env:LOCAL_RULES_PATH = "rules/local.private.json"
python scripts\build.py
Remove-Item Env:\LOCAL_RULES_PATH
```

Do not commit generated `release/*` files after a private build unless the repository is private too, because `v2raya-rules.txt`, `routing.happ`, and `routing.happ.json` will contain those private exceptions.

If this repository was previously committed with private addresses, publish it with a clean history or rewrite history before pushing to a public remote.

## Scripts

- `scripts/build.py` is the main builder. It downloads full RunetFreedom geosite/geoip files, filters selected categories into mini `release/geosite.dat` and `release/geoip.dat`, and generates `release/v2raya-rules.txt` plus Happ files.
- `scripts/filter_geodat.py` is used by `build.py` to copy selected top-level categories from Xray `.dat` protobuf files.
- `scripts/router-update.sh` is installed on OpenWrt. It downloads `release/v2raya-rules.txt`, `release/geosite.dat`, and `release/geoip.dat`, applies rules through the v2rayA API, restarts v2rayA, validates Xray config, and rolls back on failure.
- `scripts/audit_geodata.py` is not part of the build. Use it manually to inspect selected geosite categories and exact overlaps:

```sh
python scripts/audit_geodata.py --geosite release/geosite.dat
```

## Workflow

The GitHub Actions workflow runs:

- on push to rules/scripts/workflow files;
- manually via `workflow_dispatch`;
- at `08:05`, `14:05`, `19:05`, and `23:05` UTC via `schedule`.

GitHub cannot subscribe directly to another public repository's release event unless that repository sends `repository_dispatch`.
The scheduled workflow rebuilds from latest RunetFreedom assets and commits only when generated output changes. The schedule is intentionally delayed after observed RunetFreedom release times so both GeoSite and GeoIP assets are usually published before the build starts.

The workflow also publishes a GitHub Release with tag `latest` and overwrites release assets on each successful build. Use `/releases/latest/download/...` links for clients and routers that should always fetch the newest generated files.

## Router setup

Install `scripts/router-update.sh` as `/usr/bin/update-v2raya-from-github` and create:

```sh
cat >/etc/v2raya-github-rules.conf <<'EOF'
BASE_URL="https://github.com/khanidnavy12345-bit/Rules-RU/releases/latest/download"
V2RAYA_USERNAME="root"
V2RAYA_PASSWORD="change-me"
EOF
chmod 600 /etc/v2raya-github-rules.conf
```

Optional private routing lines can stay only on the router:

```sh
cat >/etc/v2raya-local-rules.txt <<'EOF'
# private proxy server direct exceptions
ip(203.0.113.10) -> direct
domain(contains:your-vpn-domain) -> direct
EOF
chmod 600 /etc/v2raya-local-rules.txt
```

`scripts/router-update.sh` inserts `/etc/v2raya-local-rules.txt` into downloaded `v2raya-rules.txt` before applying it through the v2rayA API. This keeps personal server addresses out of GitHub releases.

Then add cron:

```sh
10 8,14,19,23 * * * /usr/bin/update-v2raya-from-github
```

The script backs up current geodata and `bolt.db`, applies new files and `routingA`, restarts v2rayA, starts Xray, validates `xray run -test`, and restores the backup on failure.
