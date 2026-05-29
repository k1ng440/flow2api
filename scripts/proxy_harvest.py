#!/usr/bin/env python3
"""
Proxy harvester + reputation filter + Google Flow validator.

Pipeline:
  proxybroker2 find → ProxyCheck.io reputation → Google Flow body-check → save

Usage:
    python3 proxy_harvest.py [--limit N] [--output PATH]

Env:
    PROXYCHECK_KEY   ProxyCheck.io API key (free at proxycheck.io — 1000/day vs 100/day)
"""

import argparse
import asyncio
import fcntl
import json
import logging
import os
import re
import sys
import tempfile
import time
from urllib.parse import urlparse

import aiohttp

OUTPUT_FILE = "/opt/flow2api/data/proxies.txt"
LOCK_FILE = "/tmp/proxy_harvest.lock"
REPUTE_CACHE_FILE = "/opt/flow2api/data/repute_cache.json"
REPUTE_CACHE_TTL = 7 * 24 * 3600  # 7 days — IP reputation changes slowly

TEST_URL = "https://labs.google/fx/tools/flow"
TEST_TIMEOUT = 20
TEST_BODY_RE = re.compile(r"<title[^>]*>[^<]*flow[^<]*<", re.IGNORECASE)

# Stage 3 — must be reachable through the proxy for reCAPTCHA solving to work
RECAPTCHA_JS_URL = "https://www.google.com/recaptcha/enterprise.js"
RECAPTCHA_SITE_KEY = "6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV"
API_DOMAIN_URL = "https://aisandbox-pa.googleapis.com/"
GOOGLE_TEST_TIMEOUT = 15

FIND_LIMIT = 60
MAX_PROXIES = 20  # cap on proxies.txt — keep the N with lowest fail_count
FIND_TYPES = ["HTTP", "HTTPS"]
FIND_COUNTRIES = ["US", "GB", "DE", "NL"]
FIND_LEVEL = "High"
CONCURRENT_TESTS = 8

# ProxyCheck.io — only used to block confirmed TOR exit nodes.
# NOTE: risk=100 for ALL public proxies by definition; don't filter on risk.
# type field for raw IPs returns protocol (HTTP/SOCKS5), not Residential/Datacenter.
REPUTE_DELAY = 0.6  # seconds between calls (free tier: ~1 req/sec)

# flow2api reputation integration — set these to enable
FLOW2API_URL = os.environ.get("FLOW2API_URL", "")
FLOW2API_USERNAME = os.environ.get("FLOW2API_USERNAME", "")
FLOW2API_PASSWORD = os.environ.get("FLOW2API_PASSWORD", "")
# Proxies with fail_count >= this are evicted; matches the 24h cooldown tier (fail 4+)
FAIL_COUNT_EVICT = 4
# Proxies with error_rate >= this are also evicted regardless of fail_count
ERROR_RATE_EVICT = 0.8

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("proxy_harvest")

_repute_cache: dict = {}


def _load_repute_cache() -> None:
    global _repute_cache
    try:
        with open(REPUTE_CACHE_FILE) as f:
            _repute_cache = json.load(f)
        log.info(f"Loaded {len(_repute_cache)} cached reputation entries")
    except FileNotFoundError:
        _repute_cache = {}
    except Exception as e:
        log.warning(f"Failed to load repute cache: {e} — starting fresh")
        _repute_cache = {}


def _save_repute_cache() -> None:
    try:
        dir_ = os.path.dirname(REPUTE_CACHE_FILE)
        with tempfile.NamedTemporaryFile("w", dir=dir_, delete=False, suffix=".tmp") as f:
            json.dump(_repute_cache, f)
        os.replace(f.name, REPUTE_CACHE_FILE)
    except Exception as e:
        log.warning(f"Failed to save repute cache: {e}")


async def check_reputation(ip: str, session: aiohttp.ClientSession, sem: asyncio.Semaphore) -> bool:
    """
    Return False only for confirmed TOR exit nodes.
    Everything else passes — risk score is always 100 for public proxies, useless as a filter.
    Falls through on API errors.
    """
    # Cache hit
    entry = _repute_cache.get(ip)
    if entry and time.time() - entry["ts"] < REPUTE_CACHE_TTL:
        result = entry["result"]
        log.debug(f"REPUTE CACHE {'OK' if result else 'SKIP'} {ip} type={entry.get('type')} provider={entry.get('provider')}")
        return result

    api_key = os.environ.get("PROXYCHECK_KEY", "")
    url = f"https://proxycheck.io/v2/{ip}?vpn=1&asn=1"
    if api_key:
        url += f"&key={api_key}"

    async with sem:
        await asyncio.sleep(REPUTE_DELAY)
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    log.debug(f"REPUTE api-error {ip} status={resp.status}")
                    return True
                data = await resp.json()
        except Exception as e:
            log.debug(f"REPUTE api-fail {ip}: {e}")
            return True

    if data.get("status") not in ("ok", "warning"):
        return True

    info = data.get(ip, {})
    ip_type = info.get("type", "")
    provider = info.get("provider", "")
    result = ip_type != "TOR"

    _repute_cache[ip] = {"result": result, "type": ip_type, "provider": provider, "ts": time.time()}
    _save_repute_cache()

    if not result:
        log.info(f"REPUTE SKIP {ip} TOR exit node")
    else:
        log.info(f"REPUTE OK   {ip} type={ip_type} provider={provider}")
    return result


async def test_proxy(proxy_url: str, session: aiohttp.ClientSession) -> tuple[bool, float]:
    """Return (passed, latency_ms). latency_ms is 0 on failure."""
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    t0 = time.monotonic()
    try:
        async with session.get(
            TEST_URL,
            proxy=proxy_url,
            ssl=True,
            allow_redirects=True,
            max_redirects=5,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=TEST_TIMEOUT, connect=10, sock_read=15),
        ) as resp:
            latency_ms = (time.monotonic() - t0) * 1000
            if resp.status >= 400:
                log.info(f"FLOW FAIL HTTP {resp.status} {proxy_url}")
                return False, 0.0
            body = await resp.content.read(32768)
            text = body.decode("utf-8", errors="ignore")
            if not TEST_BODY_RE.search(text):
                log.info(f"FLOW FAIL body-check {proxy_url} (status={resp.status})")
                return False, 0.0
            log.info(f"FLOW PASS {resp.status} {proxy_url} ({latency_ms:.0f}ms)")
            return True, latency_ms
    except asyncio.TimeoutError:
        log.debug(f"FLOW FAIL timeout {proxy_url}")
    except aiohttp.ClientProxyConnectionError as e:
        log.debug(f"FLOW FAIL proxy-conn {proxy_url}: {e}")
    except aiohttp.ClientConnectorError as e:
        log.debug(f"FLOW FAIL connect {proxy_url}: {e}")
    except Exception as e:
        log.debug(f"FLOW FAIL {proxy_url}: {type(e).__name__}: {e}")
    return False, 0.0


async def test_google_endpoints(proxy_url: str, session: aiohttp.ClientSession) -> bool:
    """Stage 3: verify proxy can reach reCAPTCHA Enterprise JS and the generation API domain.

    A proxy that passes the Flow page test may still be blocked from these endpoints,
    which would cause token-solving and generation failures at runtime.
    """
    timeout = aiohttp.ClientTimeout(total=GOOGLE_TEST_TIMEOUT, connect=8, sock_read=10)
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    }

    # Check 1: reCAPTCHA Enterprise JS must be fetchable with recognizable content
    try:
        async with session.get(
            f"{RECAPTCHA_JS_URL}?render={RECAPTCHA_SITE_KEY}",
            proxy=proxy_url,
            ssl=True,
            timeout=timeout,
            headers=headers,
        ) as resp:
            if resp.status >= 400:
                log.info(f"RCAP FAIL HTTP {resp.status} {proxy_url}")
                return False
            body = await resp.content.read(512)
            text = body.decode("utf-8", errors="ignore")
            if "recaptcha" not in text.lower():
                log.info(f"RCAP FAIL body {proxy_url}")
                return False
    except asyncio.TimeoutError:
        log.debug(f"RCAP FAIL timeout {proxy_url}")
        return False
    except Exception as e:
        log.debug(f"RCAP FAIL {proxy_url}: {type(e).__name__}: {e}")
        return False

    # Check 2: generation API domain must be reachable (any HTTP response = success)
    try:
        async with session.get(
            API_DOMAIN_URL,
            proxy=proxy_url,
            ssl=True,
            timeout=timeout,
            headers=headers,
            allow_redirects=False,
        ) as _resp:
            pass
    except asyncio.TimeoutError:
        log.debug(f"API FAIL timeout {proxy_url}")
        return False
    except Exception as e:
        log.debug(f"API FAIL {proxy_url}: {type(e).__name__}: {e}")
        return False

    log.info(f"GOOGLE PASS {proxy_url}")
    return True


async def login_flow2api(session: aiohttp.ClientSession) -> str:
    """Login to flow2api and return a session token, or empty string on failure."""
    if not FLOW2API_URL or not FLOW2API_USERNAME or not FLOW2API_PASSWORD:
        return ""
    try:
        url = f"{FLOW2API_URL.rstrip('/')}/api/admin/login"
        async with session.post(
            url,
            json={"username": FLOW2API_USERNAME, "password": FLOW2API_PASSWORD},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                log.warning(f"flow2api login failed: HTTP {resp.status}")
                return ""
            data = await resp.json()
            token = data.get("token", "")
            if token:
                log.info("flow2api login successful")
            return token
    except Exception as e:
        log.warning(f"flow2api login error: {e}")
        return ""


async def fetch_flow2api_reputation(session: aiohttp.ClientSession, token: str) -> dict:
    """Fetch proxy reputation from flow2api API. Returns {scheme://host:port: entry} or {} on error/unconfigured."""
    if not FLOW2API_URL or not token:
        return {}
    try:
        url = f"{FLOW2API_URL.rstrip('/')}/api/proxy/reputation"
        headers = {"Authorization": f"Bearer {token}"}
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                log.warning(f"flow2api reputation fetch failed: HTTP {resp.status}")
                return {}
            data = await resp.json()
            reputation = {p["proxy"]: p for p in data.get("proxies", [])}
            log.info(f"flow2api reputation: {len(reputation)} proxies fetched")
            return reputation
    except Exception as e:
        log.warning(f"flow2api reputation fetch error: {e}")
        return {}


def _proxy_display_key(proxy_url: str) -> str:
    """Return scheme://host:port for matching against reputation keys (strips credentials)."""
    try:
        p = urlparse(proxy_url)
        return f"{p.scheme}://{p.hostname}:{p.port}"
    except Exception:
        return proxy_url


def read_existing_proxies(path: str) -> list[str]:
    """Read existing proxy list file, return non-comment proxy URLs."""
    try:
        with open(path) as f:
            return [l.strip() for l in f if l.strip() and not l.startswith("#")]
    except FileNotFoundError:
        return []
    except Exception as e:
        log.warning(f"Failed to read existing proxy list: {e}")
        return []


async def run(limit: int, output: str):
    try:
        from proxybroker import Broker
    except ImportError:
        log.error("proxybroker2 not installed — pip3 install proxybroker2")
        sys.exit(1)

    found_queue: asyncio.Queue = asyncio.Queue()
    broker = Broker(found_queue)
    flow_sem = asyncio.Semaphore(CONCURRENT_TESTS)
    repute_sem = asyncio.Semaphore(2)
    good: list[tuple[str, float]] = []  # (url, latency_ms) — newly harvested
    kept_alive: list[tuple[str, float]] = []  # (url, latency_ms) — existing proxies that re-passed
    tasks: list[asyncio.Task] = []

    # Shared sessions: one direct (reputation), one proxied (flow test)
    direct_connector = aiohttp.TCPConnector(ssl=False)
    proxy_connector = aiohttp.TCPConnector(ssl=False, limit=CONCURRENT_TESTS)
    direct_session = aiohttp.ClientSession(connector=direct_connector)
    proxy_session = aiohttp.ClientSession(connector=proxy_connector)

    async def validate(proxy_url: str):
        ip = urlparse(proxy_url).hostname
        # Stage 1: reputation pre-filter
        if ip and not await check_reputation(ip, direct_session, repute_sem):
            return
        # Stage 2 + 3: Flow page check then Google reCAPTCHA + API domain check
        async with flow_sem:
            passed, latency_ms = await test_proxy(proxy_url, proxy_session)
            if not passed:
                return
            if not await test_google_endpoints(proxy_url, proxy_session):
                return
            good.append((proxy_url, latency_ms))

    async def retest_existing(proxy_url: str):
        """Re-validate an existing pool proxy — skip reputation check, run all connectivity tests."""
        async with flow_sem:
            passed, latency_ms = await test_proxy(proxy_url, proxy_session)
            if not passed:
                log.info(f"DEAD (flow fail) {_proxy_display_key(proxy_url)}")
                return
            if not await test_google_endpoints(proxy_url, proxy_session):
                log.info(f"DEAD (google endpoints fail) {_proxy_display_key(proxy_url)}")
                return
            kept_alive.append((proxy_url, latency_ms))

    async def drain():
        while True:
            proxy = await found_queue.get()
            if proxy is None:
                break
            proto = "https" if "HTTPS" in proxy.types else "http"
            url = f"{proto}://{proxy.host}:{proxy.port}"
            log.info(f"Found {url}")
            tasks.append(asyncio.create_task(validate(url)))

    _load_repute_cache()
    using_key = bool(os.environ.get("PROXYCHECK_KEY"))
    using_flow2api = bool(FLOW2API_URL and FLOW2API_USERNAME and FLOW2API_PASSWORD)
    log.info(
        f"Harvesting: types={FIND_TYPES} countries={FIND_COUNTRIES} "
        f"level={FIND_LEVEL} limit={limit} | "
        f"proxycheck={'key' if using_key else 'no-key (100/day limit)'} | "
        f"flow2api={'enabled' if using_flow2api else 'disabled'}"
    )
    t0 = time.monotonic()

    # Fetch flow2api reputation and existing proxy list before harvesting
    flow2api_token = await login_flow2api(direct_session)
    reputation = await fetch_flow2api_reputation(direct_session, flow2api_token)
    existing = read_existing_proxies(output)

    # Evict existing proxies with too many failures or a high error rate.
    # Dead proxies (network-unreachable) are caught by re-testing below; this evicts
    # proxies that connect but consistently fail reCAPTCHA/Flow validation.
    kept: list[str] = []
    evicted: list[str] = []
    for proxy in existing:
        key = _proxy_display_key(proxy)
        rep = reputation.get(key)
        fail_count = rep.get("fail_count", 0) if rep else 0
        error_rate = rep.get("error_rate") if rep else None
        if fail_count >= FAIL_COUNT_EVICT:
            evicted.append(proxy)
            log.info(f"EVICT fail_count={fail_count} {key}")
        elif error_rate is not None and error_rate >= ERROR_RATE_EVICT:
            evicted.append(proxy)
            log.info(f"EVICT error_rate={error_rate:.2f} {key}")
        else:
            kept.append(proxy)

    if reputation:
        log.info(f"Existing proxies: {len(kept)} kept, {len(evicted)} evicted (fail_count >= {FAIL_COUNT_EVICT} or error_rate >= {ERROR_RATE_EVICT})")

    try:
        await asyncio.gather(
            broker.find(
                types=FIND_TYPES,
                lvl=FIND_LEVEL,
                countries=FIND_COUNTRIES,
                strict=True,
                limit=limit,
            ),
            drain(),
        )
        if tasks:
            await asyncio.gather(*tasks)
        # Re-test existing proxies to flush dead ones (connectivity check, no reputation step)
        if kept:
            log.info(f"Re-testing {len(kept)} existing proxies...")
            await asyncio.gather(*[retest_existing(p) for p in kept])
            log.info(f"Re-test done: {len(kept_alive)}/{len(kept)} alive")
    finally:
        await direct_session.close()
        await proxy_session.close()

    elapsed = time.monotonic() - t0
    log.info(f"Finished in {elapsed:.1f}s — {len(good)}/{limit} new proxies passed all checks")

    # Combine re-tested existing (real latency) + newly validated (real latency).
    # Dedup by host:port, then sort by reputation bucket + latency.
    seen_keys: set = set()
    candidates: list[tuple[str, float]] = []
    for proxy_url, latency_ms in kept_alive:
        key = _proxy_display_key(proxy_url)
        if key not in seen_keys:
            seen_keys.add(key)
            candidates.append((proxy_url, latency_ms))
    for proxy_url, latency_ms in good:
        key = _proxy_display_key(proxy_url)
        if key not in seen_keys:
            seen_keys.add(key)
            candidates.append((proxy_url, latency_ms))

    def _sort_key(item: tuple[str, float]) -> tuple:
        url, latency_ms = item
        rep = reputation.get(_proxy_display_key(url), {})
        error_rate = rep.get("error_rate")
        # Two buckets: proxies with known error_rate sort before unknown (no history).
        # Within each bucket, lower latency wins.
        if error_rate is not None:
            bucket = (0, error_rate)
        else:
            bucket = (1, 0.0)
        return (*bucket, latency_ms)

    candidates.sort(key=_sort_key)
    final = [url for url, _ in candidates]

    if not final:
        log.warning("No proxies after merge — output file unchanged")
        return

    if len(final) > MAX_PROXIES:
        dropped = len(final) - MAX_PROXIES
        log.info(f"Capping proxy list to {MAX_PROXIES} (dropped {dropped} worst)")
        final = final[:MAX_PROXIES]

    out_dir = os.path.dirname(os.path.abspath(output))
    with tempfile.NamedTemporaryFile("w", dir=out_dir, delete=False, suffix=".tmp") as f:
        tmp_path = f.name
        for url in final:
            f.write(url + "\n")
    os.replace(tmp_path, output)
    kept_dead = len(kept) - len(kept_alive)
    log.info(f"Wrote {len(final)} proxies → {output} ({len(kept_alive)} kept, {kept_dead} dead, {len(good)} new, {len(evicted)} evicted, cap={MAX_PROXIES})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=FIND_LIMIT)
    parser.add_argument("--output", default=OUTPUT_FILE)
    args = parser.parse_args()

    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.error("Another proxy_harvest instance is running — exiting")
        sys.exit(1)

    try:
        asyncio.run(run(args.limit, args.output))
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()
        try:
            os.unlink(LOCK_FILE)
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
