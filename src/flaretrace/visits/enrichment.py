"""Retrieve bounded, optional IP-provider metadata without changing visit records."""

import ipaddress
import json
import urllib.request

from flaretrace.utilities import now


def provider_metadata(payload, address):
    """Keep bounded place/network fields from a successful provider response.

    Args:
        payload: Decoded ipwho.is response mapping.
        address: Normalized public IP address being looked up.

    Returns:
        JSON string containing approximate place, provider, ASN, and query time.

    Raises:
        ValueError: The provider response is malformed or reports failure.
    """
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError("IP lookup unavailable or rate limited.")
    network = payload.get("connection") or {}
    information = {
        field: str(payload.get(field, ""))[:200]
        for field in ("country", "region", "city", "country_code")
    }
    information.update(
        {field: str(network.get(field, ""))[:200] for field in ("asn", "isp", "org")}
    )
    information.update(
        provider="ipwho.is",
        queried_at=now(),
        ip=str(address),
        accuracy="Approximate IP location; not GPS or proof of identity",
    )
    return json.dumps(information)


def lookup_ip_information(connection, ip_address):
    """Reuse cached IP information or fetch at most 64 KiB from the provider.

    Args:
        connection: SQLite connection used only to read previously saved results.
        ip_address: Public IPv4/IPv6 address; private addresses are never transmitted.

    Returns:
        Bounded provider metadata encoded as JSON, without saving it to a visit.

    Raises:
        ValueError: The IP is private or the provider response is invalid/oversized.
        OSError: The optional network request fails or times out.
    """
    address = ipaddress.ip_address(ip_address)
    if not address.is_global:
        raise ValueError("Only public IP addresses can be enriched.")
    cached = connection.execute(
        "SELECT ip_info FROM visits WHERE ip=? AND ip_info IS NOT NULL LIMIT 1",
        (str(address),),
    ).fetchone()
    if cached:
        return cached["ip_info"]
    with urllib.request.urlopen(
        "https://ipwho.is/" + str(address), timeout=5
    ) as response:
        encoded = response.read(65537)
    if len(encoded) > 65536:
        raise ValueError("IP lookup response too large.")
    return provider_metadata(json.loads(encoded), address)
