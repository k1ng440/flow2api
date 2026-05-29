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

FIND_LIMIT = 60
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
FLOW2API_ADMIN_TOKEN = os.environ.get("FLOW2API_ADMIN_TOKEN", "")
# Proxies with fail_count >= this are evicted; matches the 24h cooldown tier (fail 4+)
FAIL_COUNT_EVICT = 4

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


async def test_proxy(proxy_url: str, session: aiohttp.ClientSession) -> bool:
    """Return True if proxy reaches TEST_URL with a genuine Google response."""
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    try:
        async with session.get(
            TEST_URL,
            proxy=proxy_url,
            ssl=False,
            allow_redirects=True,
            max_redirects=5,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=TEST_TIMEOUT, connect=10, sock_read=15),
        ) as resp:
            if resp.status >= 400:
                log.info(f"FLOW FAIL HTTP {resp.status} {proxy_url}")
                return False
            body = await resp.content.read(32768)
            text = body.decode("utf-8", errors="ignore")
            if not TEST_BODY_RE.search(text):
                log.info(f"FLOW FAIL body-check {proxy_url} (status={resp.status})")
                return False
            log.info(f"FLOW PASS {resp.status} {proxy_url}")
            return True
    except asyncio.TimeoutError:
        log.debug(f"FLOW FAIL timeout {proxy_url}")
    except aiohttp.ClientProxyConnectionError as e:
        log.debug(f"FLOW FAIL proxy-conn {proxy_url}: {e}")
    except aiohttp.ClientConnectorError as e:
        log.debug(f"FLOW FAIL connect {proxy_url}: {e}")
    except Exception as e:
        log.debug(f"FLOW FAIL {proxy_url}: {type(e).__name__}: {e}")
    return False


async def fetch_flow2api_reputation(session: aiohttp.ClientSession) -> dict:
    """Fetch proxy reputation from flow2api API. Returns {scheme://host:port: entry} or {} on error/unconfigured."""
    if not FLOW2API_URL or not FLOW2API_ADMIN_TOKEN:
        return {}
    try:
        url = f"{FLOW2API_URL.rstrip('/')}/api/proxy/reputation"
        headers = {"Authorization": f"Bearer {FLOW2API_ADMIN_TOKEN}"}
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
    good: list[str] = []
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
        # Stage 2: actually reach Google Flow through the proxy
        async with flow_sem:
            if await test_proxy(proxy_url, proxy_session):
                good.append(proxy_url)

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
    using_flow2api = bool(FLOW2API_URL and FLOW2API_ADMIN_TOKEN)
    log.info(
        f"Harvesting: types={FIND_TYPES} countries={FIND_COUNTRIES} "
        f"level={FIND_LEVEL} limit={limit} | "
        f"proxycheck={'key' if using_key else 'no-key (100/day limit)'} | "
        f"flow2api={'enabled' if using_flow2api else 'disabled'}"
    )
    t0 = time.monotonic()

    # Fetch flow2api reputation and existing proxy list before harvesting
    reputation = await fetch_flow2api_reputation(direct_session)
    existing = read_existing_proxies(output)

    # Keep existing proxies whose flow2api fail_count is below the eviction threshold
    kept: list[str] = []
    evicted: list[str] = []
    for proxy in existing:
        key = _proxy_display_key(proxy)
        rep = reputation.get(key)
        if rep and rep.get("fail_count", 0) >= FAIL_COUNT_EVICT:
            evicted.append(proxy)
            log.info(f"EVICT fail_count={rep['fail_count']} {key}")
        else:
            kept.append(proxy)

    if reputation:
        log.info(f"Existing proxies: {len(kept)} kept, {len(evicted)} evicted (fail_count >= {FAIL_COUNT_EVICT})")

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
    finally:
        await direct_session.close()
        await proxy_session.close()

    elapsed = time.monotonic() - t0
    log.info(f"Finished in {elapsed:.1f}s — {len(good)}/{limit} new proxies passed all checks")

    # Combine: kept existing + newly validated, dedup by host:port
    seen_keys: set = set()
    final: list[str] = []
    for proxy in kept + good:
        key = _proxy_display_key(proxy)
        if key not in seen_keys:
            seen_keys.add(key)
            final.append(proxy)

    if not final:
        log.warning("No proxies after merge — output file unchanged")
        return

    out_dir = os.path.dirname(os.path.abspath(output))
    with tempfile.NamedTemporaryFile("w", dir=out_dir, delete=False, suffix=".tmp") as f:
        tmp_path = f.name
        for url in final:
            f.write(url + "\n")
    os.replace(tmp_path, output)
    log.info(f"Wrote {len(final)} proxies → {output} ({len(kept)} kept, {len(good)} new, {len(evicted)} evicted)")


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
