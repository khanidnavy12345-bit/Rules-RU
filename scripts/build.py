#!/usr/bin/env python3
import base64
import json
import os
import re
import time
import urllib.request
from urllib.error import HTTPError, URLError
from pathlib import Path

from filter_geodat import filter_dat


ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "rules" / "routing.json"
BUILD = ROOT / "build"
RELEASE = ROOT / "release"


GEOSITE_RE = re.compile(r"^geosite:([A-Za-z0-9_-]+)$")
GEOIP_RE = re.compile(r"^geoip:([A-Za-z0-9_-]+)$")


def download(url, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    errors = []
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "v2raya-rules-builder"})
            with urllib.request.urlopen(request, timeout=180) as response:
                path.write_bytes(response.read())
            return
        except (HTTPError, URLError) as exc:
            errors.append(str(exc))
            resolved = resolve_github_latest_asset(url)
            if resolved and resolved != url:
                url = resolved
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"failed to download {url}: {'; '.join(errors)}")


def resolve_github_latest_asset(url):
    match = re.match(r"https://github.com/([^/]+)/([^/]+)/releases/latest/download/([^/?#]+)", url)
    if not match:
        return None
    owner, repo, asset_name = match.groups()
    api = f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=5"
    request = urllib.request.Request(api, headers={"User-Agent": "v2raya-rules-builder"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            releases = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    for release in releases:
        for asset in release.get("assets", []):
            if asset.get("name") == asset_name:
                candidate = asset.get("browser_download_url")
                if url_exists(candidate):
                    return candidate
    return None


def url_exists(url):
    if not url:
        return False
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "v2raya-rules-builder"})
    try:
        with urllib.request.urlopen(request, timeout=30):
            return True
    except Exception:
        return False


def collect_codes(items, regex):
    out = []
    for item in items:
        match = regex.match(item)
        if match:
            out.append(match.group(1).lower())
    return out


def code_for_item(item, kind):
    regex = GEOSITE_RE if kind == "domain" else GEOIP_RE
    match = regex.match(item)
    if not match:
        return None
    return match.group(1).lower()


def build_fast_priority(rules, kind):
    key = "domains" if kind == "domain" else "ips"
    priority = {}
    rank = 0
    for group in rules.get("routingHints", {}).get("fastPath", []):
        for item in group.get(key, []):
            if item not in priority:
                priority[item] = rank
                rank += 1
    return priority


def unique(items):
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def merge_list_section(base, extra, section):
    target = base.setdefault(section, {})
    source = extra.get(section, {})
    for key in ("domains", "ips"):
        if key in source:
            target[key] = unique(target.get(key, []) + source[key])
    for key in ("sites",):
        if key in source:
            target[key] = unique(target.get(key, []) + source[key])


def load_rules():
    rules = json.loads(RULES.read_text(encoding="utf-8"))
    rules.setdefault("localDirect", {"domains": [], "ips": []})

    local_path = os.environ.get("LOCAL_RULES_PATH")
    if not local_path:
        return rules, None

    path = Path(local_path)
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        raise SystemExit(f"LOCAL_RULES_PATH does not exist: {path}")

    local_rules = json.loads(path.read_text(encoding="utf-8"))
    merge_list_section(rules, local_rules, "localDirect")
    return rules, str(path)


def item_stats(item, kind, stats):
    code = code_for_item(item, kind)
    if not code:
        return {"entries": 0, "bytes": 0}
    return stats.get(kind, {}).get(code, {"entries": 1_000_000_000, "bytes": 1_000_000_000})


def sort_items_for_rule(items, kind, stats, priority=None):
    priority = priority or {}
    indexed = list(enumerate(unique(items)))

    def key(pair):
        index, item = pair
        stat = item_stats(item, kind, stats)
        explicit_rank = priority.get(item, 1_000_000)
        has_geo = code_for_item(item, kind) is not None
        return (explicit_rank, has_geo, stat["entries"], stat["bytes"], index)

    return [item for _, item in sorted(indexed, key=key)]


def lines_for_rule(kind, items, target, stats=None, priority=None):
    items = unique(items)
    if not items:
        return []
    if stats is not None:
        items = sort_items_for_rule(items, kind, stats, priority)
    return [f"{kind}({', '.join(items)}) -> {target}"]


def is_global_direct_tld_rule(item):
    return item in {"regexp:.*\\.ru$", "regexp:.*\\.su$", "regexp:.*\\.xn--p1ai$"}


def build_routing_a(rules, stats=None):
    stats = stats or {}
    domain_priority = build_fast_priority(rules, "domain")
    ip_priority = build_fast_priority(rules, "ip")
    default_outbound = rules.get("defaultOutbound", "proxy")
    if default_outbound not in {"proxy", "direct"}:
        raise ValueError("defaultOutbound must be proxy or direct")
    lines = [f"default: {default_outbound}", ""]
    lines += ["# block from subscription", f"# routeOrder: {rules['routeOrder']}"]
    lines += lines_for_rule("domain", rules["block"]["sites"], "block", stats)
    lines += lines_for_rule("ip", rules["block"]["ips"], "block", stats)
    lines += [""]

    lines += ["# local/direct exceptions kept from current OpenWrt setup"]
    if rules["udpPolicy"].get("blockUdp443"):
        lines += ["# block QUIC / UDP 443 so TCP-only VLESS Reality handles HTTPS"]
        lines += ["network(udp) && port(443) -> block"]
    lines += lines_for_rule("ip", rules["udpPolicy"].get("proxyIpsBeforeDirectUdp", []), "proxy")
    for group in rules["udpPolicy"].get("proxyPortRanges", []):
        lines += [f"# {group.get('comment', 'selected UDP ports through proxy')}"]
        lines += [f"network(udp) && port({group['ports']}) -> proxy"]
    if rules["udpPolicy"].get("directOtherUdp"):
        lines += ["network(udp) -> direct"]
    lines += lines_for_rule("ip", rules["localDirect"]["ips"], "direct")
    lines += lines_for_rule("domain", rules["localDirect"]["domains"], "direct")
    lines += [""]

    used_domains = set()
    used_ips = set()
    for group in rules.get("routingHints", {}).get("fastPath", []):
        domains = unique(group.get("domains", []))
        ips = unique(group.get("ips", []))
        target = group["target"]
        if domains or ips:
            lines += [f"# {group.get('comment', 'fast path')}"]
            lines += lines_for_rule("domain", domains, target, stats, domain_priority)
            lines += lines_for_rule("ip", ips, target, stats, ip_priority)
            lines += [""]
            used_domains.update(domains)
            used_ips.update(ips)

    proxy_before_direct = set(rules.get("routingHints", {}).get("proxyBeforeDirectSites", []))
    geosite_proxy = [x for x in rules["proxy"]["sites"] if x.startswith("geosite:")]
    early_geosite_proxy = [x for x in geosite_proxy if x in proxy_before_direct and x not in used_domains]
    late_geosite_proxy = [x for x in geosite_proxy if x not in proxy_before_direct and x not in used_domains]
    custom_proxy = [x for x in rules["proxy"]["sites"] if not x.startswith("geosite:") and x not in used_domains]
    early_proxy_ips = [x for x in rules["proxy"]["ips"] if x == "geoip:ru-blocked" and x not in used_ips]
    late_proxy_ips = [x for x in rules["proxy"]["ips"] if x != "geoip:ru-blocked" and x not in used_ips]
    lines += ["# proxy before broad direct: categories that contain RU/SU/RF or blocked domains"]
    lines += lines_for_rule("domain", early_geosite_proxy, "proxy", stats)
    lines += lines_for_rule("ip", early_proxy_ips, "proxy", stats)
    lines += [""]

    direct_sites = [x for x in rules["direct"]["sites"] if x not in used_domains]
    fallback_direct_sites = [x for x in direct_sites if is_global_direct_tld_rule(x)]
    early_direct_sites = [x for x in direct_sites if not is_global_direct_tld_rule(x)]
    lines += ["# direct from subscription: exact categories before broad TLD fallback"]
    lines += lines_for_rule("domain", early_direct_sites, "direct", stats)
    lines += lines_for_rule("ip", [x for x in rules["direct"]["ips"] if x not in used_ips], "direct", stats)
    lines += [""]

    lines += ["# proxy after broad direct: non-RU service categories and exact custom domains"]
    lines += lines_for_rule("domain", late_geosite_proxy + custom_proxy, "proxy", stats)
    lines += lines_for_rule("ip", late_proxy_ips, "proxy", stats)
    lines += [""]

    lines += ["# fallback direct TLDs: kept last so explicit proxy categories win first"]
    lines += lines_for_rule("domain", fallback_direct_sites, "direct")
    return "\n".join(lines).strip() + "\n"


def ordered_happ_items(rules, kind, target, stats):
    section = "sites" if kind == "domain" else "ips"
    fast_key = "domains" if kind == "domain" else "ips"
    fast_priority = build_fast_priority(rules, kind)
    used = set()
    items = []

    for group in rules.get("routingHints", {}).get("fastPath", []):
        if group.get("target") == target:
            fast_items = sort_items_for_rule(group.get(fast_key, []), kind, stats, fast_priority)
            items += fast_items
            used.update(fast_items)

    base_items = [x for x in rules[target][section] if x not in used]
    if target == "direct" and kind == "domain":
        early = [x for x in base_items if not is_global_direct_tld_rule(x)]
        fallback = [x for x in base_items if is_global_direct_tld_rule(x)]
        items += sort_items_for_rule(early, kind, stats)
        items += fallback
    elif target == "proxy" and kind == "domain":
        proxy_before_direct = set(rules.get("routingHints", {}).get("proxyBeforeDirectSites", []))
        early = [x for x in base_items if x.startswith("geosite:") and x in proxy_before_direct]
        late = [x for x in base_items if x not in early]
        items += sort_items_for_rule(early, kind, stats)
        items += sort_items_for_rule(late, kind, stats)
    elif target == "proxy" and kind == "ip":
        early = [x for x in base_items if x == "geoip:ru-blocked"]
        late = [x for x in base_items if x != "geoip:ru-blocked"]
        items += sort_items_for_rule(early, kind, stats)
        items += sort_items_for_rule(late, kind, stats)
    else:
        items += sort_items_for_rule(base_items, kind, stats)
    return unique(items)


def dns_hosts(rules):
    hosts = {}
    for host, value in rules.get("dnsHosts", {}).items():
        if value:
            hosts[host] = value
    return hosts


def happ_payload(rules, base_url, stats=None):
    stats = stats or {}
    return {
        "name": rules["name"],
        "globalProxy": rules["globalProxy"],
        "routeOrder": rules["routeOrder"],
        "domainStrategy": rules["domainStrategy"],
        "remoteDNSType": rules["remoteDNSType"],
        "remoteDNSDomain": rules["remoteDNSDomain"],
        "remoteDNSIP": rules["remoteDNSIP"],
        "domesticDNSType": rules["domesticDNSType"],
        "domesticDNSDomain": rules["domesticDNSDomain"],
        "domesticDNSIP": rules["domesticDNSIP"],
        "geoipUrl": f"{base_url}/geoip.dat",
        "geositeUrl": f"{base_url}/geosite.dat",
        "directSites": ordered_happ_items(rules, "domain", "direct", stats),
        "directIp": ordered_happ_items(rules, "ip", "direct", stats),
        "proxySites": ordered_happ_items(rules, "domain", "proxy", stats),
        "proxyIp": ordered_happ_items(rules, "ip", "proxy", stats),
        "blockSites": sort_items_for_rule(rules["block"]["sites"], "domain", stats),
        "blockIp": sort_items_for_rule(rules["block"]["ips"], "ip", stats),
        "dnsHosts": dns_hosts(rules),
        "fakeDnsEnable": False,
        "useChunkFiles": False,
    }


def main():
    rules, _local_rules_path = load_rules()
    RELEASE.mkdir(exist_ok=True)
    BUILD.mkdir(exist_ok=True)

    geosite_codes = []
    for group in ("block", "direct", "proxy"):
        geosite_codes += collect_codes(rules[group]["sites"], GEOSITE_RE)
    for group in rules.get("routingHints", {}).get("fastPath", []):
        geosite_codes += collect_codes(group.get("domains", []), GEOSITE_RE)
    geoip_codes = list(rules["alwaysKeep"].get("geoip", []))
    for group in ("block", "direct", "proxy"):
        geoip_codes += collect_codes(rules[group]["ips"], GEOIP_RE)
    for group in rules.get("routingHints", {}).get("fastPath", []):
        geoip_codes += collect_codes(group.get("ips", []), GEOIP_RE)

    geosite_codes = sorted(set(geosite_codes))
    geoip_codes = sorted(set(geoip_codes))

    source_geosite = BUILD / "geosite.full.dat"
    source_geoip = BUILD / "geoip.full.dat"
    download(rules["sources"]["geosite"], source_geosite)
    download(rules["sources"]["geoip"], source_geoip)

    geosite_result = filter_dat(source_geosite, RELEASE / "geosite.dat", geosite_codes)
    geoip_result = filter_dat(source_geoip, RELEASE / "geoip.dat", geoip_codes)
    stats = {
        "domain": geosite_result.get("categoryStats", {}),
        "ip": geoip_result.get("categoryStats", {}),
    }
    old_asn = RELEASE / "geoip-asn"
    if old_asn.exists():
        old_asn.unlink()

    routing_a = build_routing_a(rules, stats)
    (RELEASE / "v2raya-rules.txt").write_text(routing_a, encoding="utf-8")

    repo = os.environ.get("GITHUB_REPOSITORY", "OWNER/REPO")
    ref = os.environ.get("GITHUB_REF_NAME", "main")
    base_url = os.environ.get("PUBLIC_BASE_URL", f"https://raw.githubusercontent.com/{repo}/{ref}/release")
    payload = happ_payload(rules, base_url, stats)
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    happ = "happ://routing/add/" + base64.urlsafe_b64encode(payload_json.encode("utf-8")).decode("ascii").rstrip("=")
    (RELEASE / "routing.happ").write_text(happ + "\n", encoding="utf-8")
    (RELEASE / "routing.happ.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
