"""Proxy management module"""
from typing import Optional, List, Tuple
import re
import asyncio
import time
import random
from ..core.database import Database
from ..core.models import ProxyConfig
from ..core.logger import debug_logger

class ProxyManager:
    """Proxy configuration manager"""

    def __init__(self, db: Database):
        self.db = db
        self._warp_reconnect_lock = asyncio.Lock()
        self._proxy_list_cache: List[str] = []
        self._proxy_list_file_cached: Optional[str] = None
        self._proxy_list_cache_time: float = 0
        self._PROXY_LIST_CACHE_TTL = 30.0  # seconds

    def _parse_proxy_line(self, line: str) -> Optional[str]:
        """Convert user proxy input to standard URL format.

        Supported formats:
        - http://user:pass@host:port
        - https://user:pass@host:port
        - socks5://user:pass@host:port
        - socks5h://user:pass@host:port
        - socks5://host:port:user:pass
        - st5 host:port:user:pass
        - host:port
        - host:port:user:pass
        - <Proxy US 0.18s [HTTP: High] 143.42.66.91:80>  (proxybroker2 format)
        """
        if not line:
            return None

        line = line.strip()
        if not line:
            return None

        # proxybroker2 format: <Proxy CC 0.18s [PROTO: Anonymity] host:port>
        pb2_match = re.match(r"^<Proxy\s+\w+\s+[\d.]+s\s+\[(\w+):[^\]]+\]\s+([\w.]+:\d+)>$", line)
        if pb2_match:
            proto = pb2_match.group(1).lower()
            host_port = pb2_match.group(2)
            if proto in ("socks5", "socks4"):
                return f"{proto}://{host_port}"
            return f"http://{host_port}"

        # st5 host:port:user:pass
        st5_match = re.match(r"^st5\s+(.+)$", line, re.IGNORECASE)
        if st5_match:
            rest = st5_match.group(1).strip()
            if "@" in rest:
                return f"socks5://{rest}"
            parts = rest.split(":")
            if len(parts) >= 4 and parts[1].isdigit():
                host = parts[0]
                port = parts[1]
                username = parts[2]
                password = ":".join(parts[3:])
                return f"socks5://{username}:{password}@{host}:{port}"
            return None

        # Protocol prefix format
        if line.startswith(("http://", "https://", "socks5://", "socks5h://")):

            # Already standard user:pass@host:port (or host:port)
            if "@" in line:
                return line

            # Support protocol://host:port:user:pass
            try:
                protocol_end = line.index("://") + 3
                protocol = line[:protocol_end]
                rest = line[protocol_end:]
                parts = rest.split(":")
                if len(parts) >= 4 and parts[1].isdigit():
                    host = parts[0]
                    port = parts[1]
                    username = parts[2]
                    password = ":".join(parts[3:])
                    return f"{protocol}{username}:{password}@{host}:{port}"
                if len(parts) == 2 and parts[1].isdigit():
                    return line
            except Exception:
                return None
            return None

        # No protocol, has @: default to http
        if "@" in line:
            return f"http://{line}"

        # No protocol, determine by colon count
        parts = line.split(":")
        if len(parts) == 2 and parts[1].isdigit():
            # host:port
            return f"http://{parts[0]}:{parts[1]}"

        if len(parts) >= 4 and parts[1].isdigit():
            # host:port:user:pass
            host = parts[0]
            port = parts[1]
            username = parts[2]
            password = ":".join(parts[3:])
            return f"http://{username}:{password}@{host}:{port}"

        return None

    def normalize_proxy_url(self, proxy_url: Optional[str]) -> Optional[str]:
        """Normalize proxy URL; return None for empty input, raise ValueError for invalid format."""
        if proxy_url is None:
            return None

        raw = proxy_url.strip()
        if not raw:
            return None

        parsed = self._parse_proxy_line(raw)
        if not parsed:
            raise ValueError(
                "Invalid proxy URL format, supported examples: "
                "http://user:pass@host:port / "
                "socks5://user:pass@host:port / "
                "host:port:user:pass / st5 host:port:user:pass"
            )
        return parsed

    def _load_proxy_list_file(self, file_path: str) -> List[str]:
        """Load and parse a proxy list file, returning valid normalized proxy URLs."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            proxies = []
            for line in lines:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parsed = self._parse_proxy_line(line)
                if parsed:
                    proxies.append(parsed)
            return proxies
        except FileNotFoundError:
            debug_logger.log_error(f"[ProxyManager] Proxy list file not found: {file_path}")
            return []
        except Exception as e:
            debug_logger.log_error(f"[ProxyManager] Failed to read proxy list file: {e}")
            return []

    def _get_cached_proxy_list(self, file_path: str) -> List[str]:
        """Return cached proxy list, reloading if file path changed or TTL expired."""
        now = time.monotonic()
        if (
            file_path != self._proxy_list_file_cached
            or now - self._proxy_list_cache_time > self._PROXY_LIST_CACHE_TTL
        ):
            self._proxy_list_cache = self._load_proxy_list_file(file_path)
            self._proxy_list_file_cached = file_path
            self._proxy_list_cache_time = now
            debug_logger.log_info(
                f"[ProxyManager] Loaded {len(self._proxy_list_cache)} proxies from {file_path}"
            )
        return self._proxy_list_cache

    async def get_proxy_url(self) -> Optional[str]:
        """Backward-compatible: return request proxy URL"""
        return await self.get_request_proxy_url()

    async def get_request_proxy_url(self) -> Optional[str]:
        """Get request proxy URL if enabled. Proxy list file takes priority over single URL."""
        config = await self.db.get_proxy_config()
        if not config or not config.enabled:
            return None
        if config.proxy_list_file:
            proxies = self._get_cached_proxy_list(config.proxy_list_file)
            if proxies:
                return random.choice(proxies)
        if config.proxy_url:
            return config.proxy_url
        return None

    async def get_media_proxy_url(self) -> Optional[str]:
        """Get media upload/download proxy URL, fallback to request proxy"""
        config = await self.db.get_proxy_config()
        if config and config.media_proxy_enabled and config.media_proxy_url:
            return config.media_proxy_url
        return await self.get_request_proxy_url()

    async def update_proxy_config(
        self,
        enabled: bool,
        proxy_url: Optional[str],
        media_proxy_enabled: Optional[bool] = None,
        media_proxy_url: Optional[str] = None,
        warp_auto_reconnect: Optional[bool] = None,
        proxy_list_file: Optional[str] = None,
    ):
        """Update proxy configuration"""
        from ..core.config import config
        normalized_proxy_url = self.normalize_proxy_url(proxy_url)
        normalized_media_proxy_url = self.normalize_proxy_url(media_proxy_url)
        normalized_list_file = proxy_list_file.strip() if proxy_list_file else None

        await self.db.update_proxy_config(
            enabled=enabled,
            proxy_url=normalized_proxy_url,
            media_proxy_enabled=media_proxy_enabled,
            media_proxy_url=normalized_media_proxy_url,
            warp_auto_reconnect=warp_auto_reconnect,
            proxy_list_file=normalized_list_file or None,
        )
        if warp_auto_reconnect is not None:
            config.set_warp_auto_reconnect(warp_auto_reconnect)
        # Bust cache so new file path is picked up immediately
        self._proxy_list_file_cached = None

    async def get_proxy_config(self) -> ProxyConfig:
        """Get proxy configuration"""
        return await self.db.get_proxy_config()

    async def reconnect_warp(self, settle_seconds: float = 8.0):
        """Cycle the WARP connection to obtain a new IP after a TOO_MUCH_TRAFFIC error.

        Serialized via lock — concurrent callers wait for the in-progress reconnect
        to finish rather than stacking multiple disconnect/connect cycles.
        """
        if self._warp_reconnect_lock.locked():
            debug_logger.log_warning("[WARP] Reconnect already in progress — waiting for it to finish...")
            async with self._warp_reconnect_lock:
                return  # reconnect done by the holder; just proceed

        async with self._warp_reconnect_lock:
            debug_logger.log_warning("[WARP] TOO_MUCH_TRAFFIC — disconnecting WARP...")
            try:
                proc = await asyncio.create_subprocess_shell(
                    "warp-cli disconnect",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await proc.communicate()
                if proc.returncode == 127:
                    debug_logger.log_error("[WARP] warp-cli not found — install Cloudflare WARP or disable warp_auto_reconnect")
                    return
                if proc.returncode != 0:
                    debug_logger.log_error(f"[WARP] disconnect failed (exit {proc.returncode}): {stderr.decode().strip()}")

                await asyncio.sleep(1)
                proc = await asyncio.create_subprocess_shell(
                    "warp-cli connect",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await proc.communicate()
                if proc.returncode != 0:
                    debug_logger.log_error(f"[WARP] connect failed (exit {proc.returncode}): {stderr.decode().strip()}")
                    return
                debug_logger.log_warning(f"[WARP] Reconnected — waiting {settle_seconds}s for new IP...")
                await asyncio.sleep(settle_seconds)
            except Exception as e:
                debug_logger.log_error(f"[WARP] Reconnect failed: {e}")
