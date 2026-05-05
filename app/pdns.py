import ipaddress
import requests
from typing import Optional
from .database import NameserverNode


class PDNSNodeClient:
    """Client for a single PowerDNS node."""

    def __init__(self, node: NameserverNode):
        self.base = f"{node.pdns_api_url}/servers/{node.pdns_server_id}"
        self.headers = {
            "X-API-Key": node.pdns_api_key,
            "Content-Type": "application/json",
        }
        self.node = node

    def _req(self, method: str, path: str, **kwargs):
        r = requests.request(
            method, f"{self.base}{path}",
            headers=self.headers, timeout=8, **kwargs
        )
        if r.status_code not in (200, 201, 204):
            raise Exception(f"PowerDNS [{self.node.short}] {r.status_code}: {r.text}")
        return r

    # ── Zone ──────────────────────────────────────────────────────────

    def zone_exists(self, zone: str) -> bool:
        try:
            self._req("GET", f"/zones/{zone}")
            return True
        except:
            return False

    def create_zone_primary(self, zone: str, ns_records: list[str], also_notify: list[str] = None):
        """Create a Native or Master zone. also_notify = secondary IPs."""
        fqdn = zone if zone.endswith(".") else zone + "."
        payload = {
            "name": fqdn,
            "kind": "Master",
            "nameservers": ns_records,
        }
        if also_notify:
            payload["also-notify"] = [{"ip": ip} for ip in also_notify]
        self._req("POST", "/zones", json=payload)

    def create_zone_secondary(self, zone: str, primary_ip: str):
        """Create a Slave zone pointing at primary."""
        fqdn = zone if zone.endswith(".") else zone + "."
        payload = {
            "name": fqdn,
            "kind": "Slave",
            "masters": [primary_ip],
        }
        self._req("POST", "/zones", json=payload)

    def delete_zone(self, zone: str):
        try:
            self._req("DELETE", f"/zones/{zone}")
        except:
            pass

    def notify_zone(self, zone: str):
        """Trigger NOTIFY to slaves."""
        try:
            self._req("PUT", f"/zones/{zone}/notify")
        except:
            pass

    def axfr_retrieve(self, zone: str):
        """Trigger AXFR retrieval on a slave."""
        try:
            self._req("PUT", f"/zones/{zone}/axfr-retrieve")
        except:
            pass

    # ── Records ───────────────────────────────────────────────────────

    def list_ptrs(self, zone: str) -> list[dict]:
        r = self._req("GET", f"/zones/{zone}")
        rrsets = r.json().get("rrsets", [])
        return [
            {
                "name": rr["name"],
                "content": rr["records"][0]["content"],
                "ttl": rr["ttl"],
            }
            for rr in rrsets
            if rr["type"] == "PTR" and rr.get("records")
        ]

    def upsert_ptr(self, zone: str, ptr_name: str, target: str, ttl: int = 3600):
        fqdn = ptr_name if ptr_name.endswith(".") else ptr_name + "."
        target_fqdn = target if target.endswith(".") else target + "."
        payload = {
            "rrsets": [{
                "name": fqdn,
                "type": "PTR",
                "ttl": ttl,
                "changetype": "REPLACE",
                "records": [{"content": target_fqdn, "disabled": False}],
            }]
        }
        self._req("PATCH", f"/zones/{zone}", json=payload)

    def upsert_ptr_batch(self, zone: str, records: list[dict]):
        """Batch upsert: records = [{ptr_name, target, ttl}]"""
        rrsets = []
        for rec in records:
            fqdn = rec["ptr_name"] if rec["ptr_name"].endswith(".") else rec["ptr_name"] + "."
            target_fqdn = rec["target"] if rec["target"].endswith(".") else rec["target"] + "."
            rrsets.append({
                "name": fqdn,
                "type": "PTR",
                "ttl": rec.get("ttl", 3600),
                "changetype": "REPLACE",
                "records": [{"content": target_fqdn, "disabled": False}],
            })
        self._req("PATCH", f"/zones/{zone}", json={"rrsets": rrsets})

    def delete_ptr(self, zone: str, ptr_name: str):
        fqdn = ptr_name if ptr_name.endswith(".") else ptr_name + "."
        payload = {
            "rrsets": [{
                "name": fqdn,
                "type": "PTR",
                "changetype": "DELETE",
            }]
        }
        self._req("PATCH", f"/zones/{zone}", json=payload)

    def ping(self) -> bool:
        try:
            self._req("GET", "")
            return True
        except:
            return False


# ── IPv6 helpers ──────────────────────────────────────────────────────

def ipv6_to_ptr(ip: str) -> str:
    addr = ipaddress.IPv6Address(ip)
    hex_str = addr.exploded.replace(":", "")
    return ".".join(reversed(hex_str)) + ".ip6.arpa."


def prefix_to_zone(prefix: str) -> str:
    net = ipaddress.IPv6Network(prefix, strict=False)
    nibbles_needed = net.prefixlen // 4
    hex_str = net.network_address.exploded.replace(":", "")
    nibbles = list(reversed(hex_str[:nibbles_needed]))
    return ".".join(nibbles) + ".ip6.arpa."


def ip_in_prefix(ip: str, prefix: str) -> bool:
    try:
        return ipaddress.IPv6Address(ip) in ipaddress.IPv6Network(prefix, strict=False)
    except ValueError:
        return False


def ptr_in_zone(ptr_name: str, zone: str) -> bool:
    return ptr_name.rstrip(".").endswith(zone.rstrip("."))


def get_node_client(node: NameserverNode) -> PDNSNodeClient:
    return PDNSNodeClient(node)
