"""Diagnostic script to test different TLS configurations."""

import requests
import ssl
from urllib3.poolmanager import PoolManager
from requests.adapters import HTTPAdapter


class TLSAdapter(HTTPAdapter):
    def __init__(self, minimum_version: ssl.TLSVersion, maximum_version: ssl.TLSVersion):
        self.minimum_version = minimum_version
        self.maximum_version = maximum_version
        super().__init__()

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = self.minimum_version
        ctx.maximum_version = self.maximum_version
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def try_request(label: str, min_ver: ssl.TLSVersion, max_ver: ssl.TLSVersion) -> None:
    session = requests.Session()
    adapter = TLSAdapter(min_ver, max_ver)
    session.mount("https://", adapter)
    try:
        r = session.get("https://api.bybit.com/v5/market/time", timeout=30)
        print(f"{label}: SUCCESS {r.status_code} - {r.text}")
    except Exception as e:
        print(f"{label}: FAILED - {type(e).__name__}: {e}")


if __name__ == "__main__":
    try_request("TLS 1.2 only", ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_2)
    try_request("TLS 1.3 only", ssl.TLSVersion.TLSv1_3, ssl.TLSVersion.TLSv1_3)
