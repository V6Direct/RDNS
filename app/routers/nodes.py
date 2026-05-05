from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..database import get_db, NameserverNode
from ..auth_utils import get_current_user, require_admin
from ..pdns import get_node_client
from ..database import User
from pydantic import BaseModel
from typing import Optional

router = APIRouter()

@router.get("/")
def list_nodes(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    nodes = db.query(NameserverNode).filter(NameserverNode.is_active == True).all()
    return [
        {
            "id": n.id,
            "name": n.name,
            "short": n.short,
            "hostname": n.hostname,
            "ipv4": n.ipv4,
            "ipv6": n.ipv6,
            "lat": n.lat,
            "lon": n.lon,
        }
        for n in nodes
    ]

@router.get("/status")
def nodes_status(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    nodes = db.query(NameserverNode).all()
    result = []
    for n in nodes:
        client = get_node_client(n)
        result.append({
            "id": n.id,
            "name": n.name,
            "short": n.short,
            "hostname": n.hostname,
            "online": client.ping(),
            "is_active": n.is_active,
        })
    return result


# ── Admin CRUD for nodes ──────────────────────────────────────────────

class NodeCreate(BaseModel):
    name: str
    short: str
    hostname: str
    ipv4: Optional[str] = None
    ipv6: Optional[str] = None
    pdns_api_url: str
    pdns_api_key: str
    pdns_server_id: str = "localhost"
    lat: Optional[float] = None
    lon: Optional[float] = None

@router.post("/")
def create_node(req: NodeCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    node = NameserverNode(**req.model_dump())
    db.add(node)
    db.commit()
    db.refresh(node)
    return {"id": node.id, "name": node.name}

@router.patch("/{node_id}")
def update_node(node_id: int, req: NodeCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    node = db.query(NameserverNode).filter(NameserverNode.id == node_id).first()
    if not node:
        from fastapi import HTTPException
        raise HTTPException(404, "Node not found")
    for k, v in req.model_dump().items():
        setattr(node, k, v)
    db.commit()
    return {"id": node.id}

@router.delete("/{node_id}")
def delete_node(node_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    node = db.query(NameserverNode).filter(NameserverNode.id == node_id).first()
    if not node:
        from fastapi import HTTPException
        raise HTTPException(404, "Node not found")
    db.delete(node)
    db.commit()
    return {"status": "deleted"}
