#!/usr/bin/env python3
"""
First-run setup: creates the admin user and seeds the 4 V6Direct nameserver nodes.

Usage:
    python seed.py <admin_username> <admin_email> <admin_password>

The PowerDNS API URLs and keys are read from env vars (or you can edit the
NODES list directly below before running).
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.database import engine, Base, SessionLocal, User, NameserverNode
from app.auth_utils import hash_password

Base.metadata.create_all(bind=engine)

# ── Edit these to match your actual deployment ─────────────────────────
NODES = [
    # AMS is first = default primary (best IPv6 connectivity)
    {
        "name": "Amsterdam",
        "short": "AMS",
        "hostname": "ns1.v6direct.org",
        "ipv4": os.getenv("AMS_IPV4", ""),
        "ipv6": os.getenv("AMS_IPV6", ""),
        "pdns_api_url": os.getenv("AMS_PDNS_URL", "http://10.0.2.1:8081/api/v1"),
        "pdns_api_key": os.getenv("AMS_PDNS_KEY", "changeme"),
        "pdns_server_id": "localhost",
        "lat": 52.3676, "lon": 4.9041,
    },
    {
        "name": "Frankfurt",
        "short": "FRA",
        "hostname": "ns2.v6direct.org",
        "ipv4": os.getenv("FRA_IPV4", ""),
        "ipv6": os.getenv("FRA_IPV6", ""),
        "pdns_api_url": os.getenv("FRA_PDNS_URL", "http://127.0.0.1:8081/api/v1"),
        "pdns_api_key": os.getenv("FRA_PDNS_KEY", "changeme"),
        "pdns_server_id": "localhost",
        "lat": 50.1109, "lon": 8.6821,
    },
    {
        "name": "Düsseldorf",
        "short": "DUS",
        "hostname": "ns3.v6direct.org",
        "ipv4": os.getenv("DUS_IPV4", ""),
        "ipv6": os.getenv("DUS_IPV6", ""),
        "pdns_api_url": os.getenv("DUS_PDNS_URL", "http://10.0.1.1:8081/api/v1"),
        "pdns_api_key": os.getenv("DUS_PDNS_KEY", "changeme"),
        "pdns_server_id": "localhost",
        "lat": 51.2217, "lon": 6.7762,
    },
    {
        "name": "Warsaw",
        "short": "WAW",
        "hostname": "ns4.v6direct.org",
        "ipv4": os.getenv("WAW_IPV4", ""),
        "ipv6": os.getenv("WAW_IPV6", ""),
        "pdns_api_url": os.getenv("WAW_PDNS_URL", "http://10.0.3.1:8081/api/v1"),
        "pdns_api_key": os.getenv("WAW_PDNS_KEY", "changeme"),
        "pdns_server_id": "localhost",
        "lat": 52.2297, "lon": 21.0122,
    },
]


def run(username, email, password):
    db = SessionLocal()

    # Admin user
    if not db.query(User).filter(User.username == username).first():
        db.add(User(
            username=username, email=email,
            hashed_password=hash_password(password),
            is_admin=True, is_active=True,
        ))
        db.commit()
        print(f"✓ Admin user '{username}' created")
    else:
        print(f"  Admin user '{username}' already exists, skipping")

    # Nodes
    for n in NODES:
        existing = db.query(NameserverNode).filter(NameserverNode.short == n["short"]).first()
        if not existing:
            db.add(NameserverNode(**n))
            db.commit()
            print(f"✓ Node {n['short']} ({n['name']}) created")
        else:
            print(f"  Node {n['short']} already exists, skipping")

    db.close()
    print("\nDone. Edit node API keys/URLs via the Admin panel or re-run with updated env vars.")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python seed.py <username> <email> <password>")
        sys.exit(1)
    run(sys.argv[1], sys.argv[2], sys.argv[3])
