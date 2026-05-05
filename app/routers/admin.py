from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional
from ..database import get_db, User, CustomerPrefix, NameserverNode, AuditLog
from ..auth_utils import require_admin, hash_password
from ..pdns import get_node_client, prefix_to_zone

router = APIRouter()


# ── Users ──────────────────────────────────────────────────────────────

class CreateUserRequest(BaseModel):
    username: str
    email: str
    password: str
    is_admin: bool = False


@router.get("/users")
def list_users(db: Session = Depends(get_db), _=Depends(require_admin)):
    return [
        {"id": u.id, "username": u.username, "email": u.email,
         "is_admin": u.is_admin, "is_active": u.is_active,
         "created_at": u.created_at.isoformat(),
         "prefix_count": len(u.prefixes)}
        for u in db.query(User).all()
    ]


@router.post("/users")
def create_user(req: CreateUserRequest, db: Session = Depends(get_db), _=Depends(require_admin)):
    if db.query(User).filter(User.username == req.username).first():
        raise HTTPException(409, "Username already exists")
    user = User(
        username=req.username, email=req.email,
        hashed_password=hash_password(req.password),
        is_admin=req.is_admin,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"id": user.id, "username": user.username}


@router.patch("/users/{user_id}/toggle")
def toggle_user(user_id: int, db: Session = Depends(get_db), _=Depends(require_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "Not found")
    user.is_active = not user.is_active
    db.commit()
    return {"id": user.id, "is_active": user.is_active}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db), _=Depends(require_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "Not found")
    db.delete(user)
    db.commit()
    return {"status": "deleted"}


# ── Prefixes ───────────────────────────────────────────────────────────

class AssignPrefixRequest(BaseModel):
    user_id: int
    prefix: str
    label: Optional[str] = None
    primary_node_id: int
    secondary_node_id: int


@router.get("/prefixes")
def list_all_prefixes(db: Session = Depends(get_db), _=Depends(require_admin)):
    prefixes = db.query(CustomerPrefix).all()
    result = []
    for p in prefixes:
        user = db.query(User).filter(User.id == p.user_id).first()
        result.append({
            "id": p.id,
            "user_id": p.user_id,
            "username": user.username if user else "?",
            "prefix": p.prefix,
            "label": p.label,
            "pdns_zone": p.pdns_zone,
            "primary_node": p.primary_node.name if p.primary_node else None,
            "secondary_node": p.secondary_node.name if p.secondary_node else None,
            "created_at": p.created_at.isoformat(),
        })
    return result


@router.post("/prefixes")
def assign_prefix(req: AssignPrefixRequest, db: Session = Depends(get_db), _=Depends(require_admin)):
    user = db.query(User).filter(User.id == req.user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    if req.primary_node_id == req.secondary_node_id:
        raise HTTPException(400, "Primary and secondary must differ")

    primary_node = db.query(NameserverNode).filter(NameserverNode.id == req.primary_node_id).first()
    secondary_node = db.query(NameserverNode).filter(NameserverNode.id == req.secondary_node_id).first()
    if not primary_node or not secondary_node:
        raise HTTPException(404, "Node not found")

    zone = prefix_to_zone(req.prefix)
    ns_records = [primary_node.hostname + ".", secondary_node.hostname + "."]

    # Create zone on primary
    pri_client = get_node_client(primary_node)
    if not pri_client.zone_exists(zone):
        try:
            pri_client.create_zone_primary(
                zone, ns_records,
                also_notify=[secondary_node.ipv4 or secondary_node.ipv6]
            )
        except Exception as e:
            raise HTTPException(502, f"Primary zone creation failed: {e}")

    # Create slave on secondary
    sec_client = get_node_client(secondary_node)
    if not sec_client.zone_exists(zone):
        try:
            sec_client.create_zone_secondary(zone, primary_node.ipv4 or primary_node.ipv6)
            pri_client.notify_zone(zone)
        except Exception as e:
            raise HTTPException(502, f"Secondary zone creation failed: {e}")

    cp = CustomerPrefix(
        user_id=req.user_id, prefix=req.prefix,
        label=req.label, pdns_zone=zone,
        primary_node_id=req.primary_node_id,
        secondary_node_id=req.secondary_node_id,
    )
    db.add(cp)
    db.commit()
    db.refresh(cp)
    return {"id": cp.id, "pdns_zone": zone}


@router.delete("/prefixes/{prefix_id}")
def remove_prefix(prefix_id: int, db: Session = Depends(get_db), _=Depends(require_admin)):
    cp = db.query(CustomerPrefix).filter(CustomerPrefix.id == prefix_id).first()
    if not cp:
        raise HTTPException(404, "Not found")
    # Optionally delete zones from nodes
    for node in [cp.primary_node, cp.secondary_node]:
        if node:
            try:
                get_node_client(node).delete_zone(cp.pdns_zone)
            except:
                pass
    db.delete(cp)
    db.commit()
    return {"status": "deleted"}


# ── Audit log ──────────────────────────────────────────────────────────

@router.get("/audit")
def get_audit(limit: int = 100, db: Session = Depends(get_db), _=Depends(require_admin)):
    logs = db.query(AuditLog).order_by(AuditLog.timestamp.desc()).limit(limit).all()
    users = {u.id: u.username for u in db.query(User).all()}
    return [
        {
            "id": l.id,
            "username": users.get(l.user_id, str(l.user_id)),
            "action": l.action,
            "prefix_id": l.prefix_id,
            "record_name": l.record_name,
            "record_content": l.record_content,
            "ip_address": l.ip_address,
            "timestamp": l.timestamp.isoformat(),
        }
        for l in logs
    ]


# ── Stats ──────────────────────────────────────────────────────────────

@router.get("/stats")
def get_stats(db: Session = Depends(get_db), _=Depends(require_admin)):
    return {
        "users": db.query(User).count(),
        "active_users": db.query(User).filter(User.is_active == True).count(),
        "prefixes": db.query(CustomerPrefix).count(),
        "nodes": db.query(NameserverNode).count(),
        "audit_entries": db.query(AuditLog).count(),
    }
