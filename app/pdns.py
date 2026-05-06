"""
Knot DNS client using RESTKnot API (https://github.com/BiznetGIO/RESTKnot)

RESTKnot API endpoints used:
  POST   /api/domain/add          — create zone
  DELETE /api/domain/delete       — delete zone
  GET    /api/record/list         — list all records (filter by zone client-side)
  POST   /api/record/add          — create record
  PUT    /api/record/edit/:id     — update record
  DELETE /api/record/delete/:id   — delete record

Auth: X-API-Key header (form-data requests, not JSON)

RESTKnot TTL note:
  RESTKnot only accepts a fixed set of TTL values:
  60, 300, 600, 900, 1800, 3600, 7200, 14400, 28800, 43200, 86400
  We silently map any requested TTL to the nearest valid value.

Zone transfer (primary/secondary):
  RESTKnot has no AXFR/NOTIFY API — Knot handles replication natively via
  knot.conf. The portal creates zones on both nodes independently; Knot's
  built-in master/slave NOTIFY+AXFR takes care of synchronisation.
"""

import ipaddress
import requests
from typing import Optional
from .database import NameserverNode

VALID_TTLS = [60, 300, 600, 900, 1800, 3600, 7200, 14400, 28800, 43200, 86400]


def nearest_ttl(ttl: int) -> int:
    return min(VALID_TTLS, key=lambda v: abs(v - ttl))


class KnotNodeClient:
    """REST client for a single Knot node via RESTKnot."""

    def __init__(self, node: NameserverNode):
        self.base = node.pdns_api_url.rstrip("/")
        self.headers = {"X-API-Key": node.pdns_api_key}
        self.node = node

    def _req(self, method: str, path: str, data: dict = None):
        r = requests.request(
            method,
            f"{self.base}{path}",
            headers=self.headers,
            data=data,          # RESTKnot uses multipart/form-data
            timeout=8,
        )
        if r.status_code not in (200, 201, 204):
            raise Exception(f"RESTKnot [{self.node.short}] {r.status_code}: {r.text}")
        if r.status_code == 204:
            return None
        try:
            return r.json()
        except Exception:
            return None

    # ── Zones ─────────────────────────────────────────────────────────

    def zone_exists(self, zone: str) -> bool:
        try:
            records = self._req("GET", "/api/record/list") or []
            zone_clean = zone.rstrip(".")
            return any(r.get("zone", "").rstrip(".") == zone_clean for r in records)
        except Exception:
            return False

    def create_zone(self, zone: str, user_id: str = "1"):
        zone_clean = zone.rstrip(".")
        self._req("POST", "/api/domain/add", data={
            "user_id": user_id,
            "zone": zone_clean,
        })

    def delete_zone(self, zone: str):
        try:
            self._req("DELETE", "/api/domain/delete", data={"zone": zone.rstrip(".")})
        except Exception:
            pass

    # No-ops: Knot handles replication natively via knot.conf
    def create_zone_primary(self, zone: str, ns_records: list = None, also_notify: list = None):
        self.create_zone(zone)

    def create_zone_secondary(self, zone: str, primary_ip: str = None):
        self.create_zone(zone)

    def notify_zone(self, zone: str):
        pass

    def axfr_retrieve(self, zone: str):
        pass

    # ── Records ───────────────────────────────────────────────────────

    def list_records(self, zone: str) -> list[dict]:
        try:
            all_records = self._req("GET", "/api/record/list") or []
            zone_clean = zone.rstrip(".")
            return [r for r in all_records if r.get("zone", "").rstrip(".") == zone_clean]
        except Exception:
            return []

    def list_ptrs(self, zone: str) -> list[dict]:
        result = []
        for r in self.list_records(zone):
            if r.get("type") != "PTR":
                continue
            owner = r.get("owner", "")
            zone_clean = zone.rstrip(".")
            fqdn = f"{owner}.{zone_clean}." if owner != "@" else f"{zone_clean}."
            result.append({
                "id": r.get("id"),
                "name": fqdn,
                "content": r.get("rdata", ""),
                "ttl": int(r.get("ttl", 3600)),
            })
        return result

    def _find_ptr_id(self, zone: str, ptr_name: str) -> Optional[str]:
        zone_clean = zone.rstrip(".")
        ptr_clean = ptr_name.rstrip(".")
        if ptr_clean.endswith("." + zone_clean):
            owner = ptr_clean[: -(len(zone_clean) + 1)]
        elif ptr_clean == zone_clean:
            owner = "@"
        else:
            return None
        for r in self.list_records(zone):
            if r.get("type") == "PTR" and r.get("owner") == owner:
                return str(r.get("id"))
        return None

    def _owner_from_ptr(self, zone: str, ptr_name: str) -> str:
        zone_clean = zone.rstrip(".")
        ptr_clean = ptr_name.rstrip(".")
        if ptr_clean.endswith("." + zone_clean):
            return ptr_clean[: -(len(zone_clean) + 1)]
        elif ptr_clean == zone_clean:
            return "@"
        raise Exception(f"PTR {ptr_name} not in zone {zone}")

    def upsert_ptr(self, zone: str, ptr_name: str, target: str, ttl: int = 3600):
        zone_clean = zone.rstrip(".")
        owner = self._owner_from_ptr(zone, ptr_name)
        target_fqdn = target if target.endswith(".") else target + "."
        ttl_val = nearest_ttl(ttl)
        existing_id = self._find_ptr_id(zone, ptr_name)
        if existing_id:
            self._req("PUT", f"/api/record/edit/{existing_id}", data={
                "zone": zone_clean,
                "owner": owner,
                "rtype": "PTR",
                "rdata": target_fqdn,
                "ttl": ttl_val,
            })
        else:
            self._req("POST", "/api/record/add", data={
                "zone": zone_clean,
                "owner": owner,
                "rtype": "PTR",
                "rdata": target_fqdn,
                "ttl": ttl_val,
            })

    def upsert_ptr_batch(self, zone: str, records: list[dict]):
        for rec in records:
            self.upsert_ptr(zone, rec["ptr_name"], rec["target"], rec.get("ttl", 3600))

    def delete_ptr(self, zone: str, ptr_name: str):
        record_id = self._find_ptr_id(zone, ptr_name)
        if not record_id:
            raise Exception(f"PTR record not found: {ptr_name}")
        self._req("DELETE", f"/api/record/delete/{record_id}")

    def ping(self) -> bool:
        try:
            r = requests.get(
                f"{self.base}/api/record/list",
                headers=self.headers,
                timeout=4,
            )
            return r.status_code in (200, 201, 204)
        except Exception:
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


def get_node_client(node: NameserverNode) -> KnotNodeClient:
    return KnotNodeClient(node)
