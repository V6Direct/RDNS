import re
import csv
import io
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from ..database import get_db, User, CustomerPrefix, NameserverNode, AuditLog
from ..auth_utils import get_current_user
from ..pdns import get_node_client, ipv6_to_ptr, ip_in_prefix, ptr_in_zone, prefix_to_zone

router = APIRouter()

HOSTNAME_RE = re.compile(r'^(?!-)[A-Za-z0-9\-\.]{1,253}(?<!-)\.?$')


def validate_hostname(h: str) -> bool:
    return bool(HOSTNAME_RE.match(h))


def log_action(db, user_id, action, prefix_id, name, content, ip):
    db.add(AuditLog(
        user_id=user_id, action=action, prefix_id=prefix_id,
        record_name=name, record_content=content, ip_address=ip
    ))
    db.commit()


def get_prefix_or_404(prefix_id: int, db: Session, user: User) -> CustomerPrefix:
    p = db.query(CustomerPrefix).filter(CustomerPrefix.id == prefix_id).first()
    if not p:
        raise HTTPException(404, "Prefix not found")
    if p.user_id != user.id and not user.is_admin:
        raise HTTPException(403, "Not your prefix")
    return p


def primary_client(p: CustomerPrefix):
    if not p.primary_node:
        raise HTTPException(502, "No primary nameserver configured for this prefix")
    return get_node_client(p.primary_node)


# ══ PREFIXES ═══════════════════════════════════════════════════════════

@router.get("/prefixes")
def list_prefixes(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    prefixes = db.query(CustomerPrefix).filter(CustomerPrefix.user_id == current_user.id).all()
    return [serialize_prefix(p) for p in prefixes]


def serialize_prefix(p: CustomerPrefix) -> dict:
    return {
        "id": p.id,
        "prefix": p.prefix,
        "label": p.label,
        "pdns_zone": p.pdns_zone,
        "primary_node": {"id": p.primary_node.id, "name": p.primary_node.name, "short": p.primary_node.short, "hostname": p.primary_node.hostname} if p.primary_node else None,
        "secondary_node": {"id": p.secondary_node.id, "name": p.secondary_node.name, "short": p.secondary_node.short, "hostname": p.secondary_node.hostname} if p.secondary_node else None,
        "created_at": p.created_at.isoformat(),
    }


# ══ UPDATE NS SELECTION ════════════════════════════════════════════════

class UpdateNSRequest(BaseModel):
    primary_node_id: int
    secondary_node_id: int


@router.patch("/prefixes/{prefix_id}/nameservers")
def update_nameservers(
    prefix_id: int,
    req: UpdateNSRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    p = get_prefix_or_404(prefix_id, db, current_user)
    if req.primary_node_id == req.secondary_node_id:
        raise HTTPException(400, "Primary and secondary must be different nodes")

    new_primary = db.query(NameserverNode).filter(NameserverNode.id == req.primary_node_id, NameserverNode.is_active == True).first()
    new_secondary = db.query(NameserverNode).filter(NameserverNode.id == req.secondary_node_id, NameserverNode.is_active == True).first()
    if not new_primary or not new_secondary:
        raise HTTPException(404, "Node not found or inactive")

    old_primary_id = p.primary_node_id
    old_secondary_id = p.secondary_node_id

    # If primary is changing, migrate zone
    if old_primary_id != req.primary_node_id:
        # Create zone on new primary (Native first, then re-configure)
        new_pri_client = get_node_client(new_primary)
        new_sec_client = get_node_client(new_secondary)
        ns_records = [new_primary.hostname + ".", new_secondary.hostname + "."]
        # Copy records from old primary if possible
        existing_records = []
        if p.primary_node:
            try:
                old_cli = get_node_client(p.primary_node)
                existing_records = old_cli.list_ptrs(p.pdns_zone)
            except:
                pass
        # Create on new primary
        if not new_pri_client.zone_exists(p.pdns_zone):
            new_pri_client.create_zone_primary(p.pdns_zone, ns_records, also_notify=[new_secondary.ipv4 or new_secondary.ipv6])
        # Restore records
        if existing_records:
            batch = [{"ptr_name": r["name"], "target": r["content"], "ttl": r["ttl"]} for r in existing_records]
            new_pri_client.upsert_ptr_batch(p.pdns_zone, batch)
        # Remove old zones
        if p.primary_node and p.primary_node_id != req.secondary_node_id:
            get_node_client(p.primary_node).delete_zone(p.pdns_zone)
        if p.secondary_node and p.secondary_node_id not in (req.primary_node_id, req.secondary_node_id):
            get_node_client(p.secondary_node).delete_zone(p.pdns_zone)
        # Create slave on new secondary
        if not new_sec_client.zone_exists(p.pdns_zone):
            new_sec_client.create_zone_secondary(p.pdns_zone, new_primary.ipv4 or new_primary.ipv6)
        new_pri_client.notify_zone(p.pdns_zone)
    else:
        # Only secondary changed
        if p.secondary_node and p.secondary_node_id != req.secondary_node_id:
            get_node_client(p.secondary_node).delete_zone(p.pdns_zone)
        new_sec_client = get_node_client(new_secondary)
        if not new_sec_client.zone_exists(p.pdns_zone):
            new_sec_client.create_zone_secondary(p.pdns_zone, new_primary.ipv4 or new_primary.ipv6)
        get_node_client(new_primary).notify_zone(p.pdns_zone)

    p.primary_node_id = req.primary_node_id
    p.secondary_node_id = req.secondary_node_id
    db.commit()

    log_action(db, current_user.id, "UPDATE_NS", prefix_id,
               p.pdns_zone, f"{new_primary.short}+{new_secondary.short}",
               request.client.host if request.client else "")
    return serialize_prefix(p)


# ══ ZONE STATUS ════════════════════════════════════════════════════════

@router.get("/prefixes/{prefix_id}/status")
def zone_status(
    prefix_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    p = get_prefix_or_404(prefix_id, db, current_user)
    result = {"zone": p.pdns_zone, "nodes": []}
    for node in [p.primary_node, p.secondary_node]:
        if not node:
            continue
        client = get_node_client(node)
        online = client.ping()
        has_zone = client.zone_exists(p.pdns_zone) if online else False
        record_count = len(client.list_ptrs(p.pdns_zone)) if has_zone else 0
        result["nodes"].append({
            "id": node.id,
            "name": node.name,
            "short": node.short,
            "role": "primary" if node.id == p.primary_node_id else "secondary",
            "online": online,
            "has_zone": has_zone,
            "record_count": record_count,
        })
    return result


# ══ PTR RECORDS ════════════════════════════════════════════════════════

@router.get("/prefixes/{prefix_id}/ptrs")
def list_ptrs(
    prefix_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    p = get_prefix_or_404(prefix_id, db, current_user)
    try:
        return primary_client(p).list_ptrs(p.pdns_zone)
    except Exception as e:
        raise HTTPException(502, str(e))


class PTRRequest(BaseModel):
    ip: str
    target: str
    ttl: Optional[int] = 3600


@router.post("/prefixes/{prefix_id}/ptrs")
def upsert_ptr(
    prefix_id: int,
    req: PTRRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    p = get_prefix_or_404(prefix_id, db, current_user)
    if not ip_in_prefix(req.ip, p.prefix):
        raise HTTPException(400, f"{req.ip} is not in {p.prefix}")
    if not validate_hostname(req.target):
        raise HTTPException(400, "Invalid PTR target hostname")
    if not (60 <= req.ttl <= 86400):
        raise HTTPException(400, "TTL must be 60–86400")
    ptr_name = ipv6_to_ptr(req.ip)
    if not ptr_in_zone(ptr_name, p.pdns_zone):
        raise HTTPException(400, "PTR out of zone bounds")
    try:
        primary_client(p).upsert_ptr(p.pdns_zone, ptr_name, req.target, req.ttl)
    except Exception as e:
        raise HTTPException(502, str(e))
    log_action(db, current_user.id, "UPSERT", prefix_id, ptr_name, req.target, request.client.host if request.client else "")
    return {"status": "ok", "ptr": ptr_name, "target": req.target}


class DeletePTRRequest(BaseModel):
    ip: str


@router.delete("/prefixes/{prefix_id}/ptrs")
def delete_ptr(
    prefix_id: int,
    req: DeletePTRRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    p = get_prefix_or_404(prefix_id, db, current_user)
    if not ip_in_prefix(req.ip, p.prefix):
        raise HTTPException(400, f"{req.ip} is not in {p.prefix}")
    ptr_name = ipv6_to_ptr(req.ip)
    try:
        primary_client(p).delete_ptr(p.pdns_zone, ptr_name)
    except Exception as e:
        raise HTTPException(502, str(e))
    log_action(db, current_user.id, "DELETE", prefix_id, ptr_name, "", request.client.host if request.client else "")
    return {"status": "ok", "deleted": ptr_name}


# ══ BULK IMPORT ════════════════════════════════════════════════════════

class BulkImportRequest(BaseModel):
    data: str           # raw text: one "ip hostname" or "ip,hostname" per line
    ttl: Optional[int] = 3600
    dry_run: bool = False


@router.post("/prefixes/{prefix_id}/ptrs/bulk")
def bulk_import(
    prefix_id: int,
    req: BulkImportRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    p = get_prefix_or_404(prefix_id, db, current_user)
    if not (60 <= req.ttl <= 86400):
        raise HTTPException(400, "TTL must be 60–86400")

    lines = req.data.strip().splitlines()
    parsed = []
    errors = []

    for i, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Support both CSV (ip,hostname) and space-separated (ip hostname)
        if "," in line:
            parts = next(csv.reader([line]))
        else:
            parts = line.split(None, 1)

        if len(parts) < 2:
            errors.append({"line": i, "raw": line, "error": "Expected: ip hostname"})
            continue

        ip, target = parts[0].strip(), parts[1].strip()

        if not ip_in_prefix(ip, p.prefix):
            errors.append({"line": i, "raw": line, "error": f"{ip} not in {p.prefix}"})
            continue
        if not validate_hostname(target):
            errors.append({"line": i, "raw": line, "error": f"Invalid hostname: {target}"})
            continue

        ptr_name = ipv6_to_ptr(ip)
        parsed.append({"ip": ip, "ptr_name": ptr_name, "target": target, "ttl": req.ttl})

    if req.dry_run:
        return {"dry_run": True, "valid": len(parsed), "errors": errors, "preview": parsed[:20]}

    if parsed:
        try:
            primary_client(p).upsert_ptr_batch(
                p.pdns_zone,
                [{"ptr_name": r["ptr_name"], "target": r["target"], "ttl": r["ttl"]} for r in parsed]
            )
        except Exception as e:
            raise HTTPException(502, str(e))
        log_action(db, current_user.id, "BULK_IMPORT", prefix_id,
                   p.pdns_zone, f"{len(parsed)} records",
                   request.client.host if request.client else "")

    return {
        "imported": len(parsed),
        "errors": errors,
        "total_lines": len([l for l in lines if l.strip() and not l.startswith("#")]),
    }
