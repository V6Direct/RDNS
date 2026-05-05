# V6Direct rDNS Portal v2

Self-service IPv6 reverse DNS portal. Customers log in, pick 2 of 4
nameserver PoPs (Frankfurt / Düsseldorf / Amsterdam / Warsaw) and manage
PTR records for their assigned prefixes.

## Architecture

```
                   ┌─────────────────────────────────┐
                   │   rdns.v6direct.org (portal)    │
                   │   FastAPI + SQLite/Postgres      │
                   └────────────┬────────────────────┘
                                │ PowerDNS REST API
          ┌─────────────────────┼──────────────────────────┐
          ▼                     ▼                          ▼
   ns1 Frankfurt          ns2 Düsseldorf           ns3 Amsterdam
   (Master zone)          (Slave zone)             (Slave zone)
                                                   ns4 Warsaw
                                                   (Slave zone)
```

Each customer prefix gets a Master zone on their chosen primary PoP
and a Slave zone on their secondary. The portal writes PTR records to
the Master via REST; the Slave pulls via AXFR on NOTIFY.

---

## Deploy: Portal VM

```bash
# 1. Clone / copy files
cp -r rdns-portal /opt/rdns-portal
cd /opt/rdns-portal

# 2. Create a dedicated user
useradd -r -s /bin/false rdns

# 3. Python venv
python3 -m venv venv
venv/bin/pip install -r requirements.txt

# 4. Configure
cp .env.example .env
# Edit .env — set SECRET_KEY, DATABASE_URL, and all node IP/key vars

# 5. Seed admin + nodes
source .env  # load env vars
venv/bin/python seed.py admin noc@v6direct.org 'StrongPassword!'

# 6. Systemd
cp rdns-portal.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now rdns-portal

# 7. Nginx + TLS
cp nginx.conf /etc/nginx/sites-available/rdns.v6direct.org
ln -s /etc/nginx/sites-available/rdns.v6direct.org /etc/nginx/sites-enabled/
certbot --nginx -d rdns.v6direct.org
nginx -t && systemctl reload nginx
```

---

## Deploy: Each Nameserver Node (×4)

```bash
# Install PowerDNS Auth
apt install pdns-server pdns-backend-sqlite3

# Configure
cp pdns.conf.template /etc/powerdns/pdns.conf
# Edit: set api-key, webserver-address, allow-axfr-ips

# Init SQLite backend
sqlite3 /var/lib/powerdns/pdns.db < /usr/share/doc/pdns-backend-sqlite3/schema.sqlite3.sql
chown pdns:pdns /var/lib/powerdns/pdns.db

systemctl enable --now pdns

# Verify API is reachable from portal
curl -H "X-API-Key: YOURKEY" http://127.0.0.1:8081/api/v1/servers/localhost
```

---

## Workflow: assigning a prefix to a customer

1. Admin panel → Users → Create User
2. Admin panel → Prefix Assignments → Assign Prefix
   - Select customer, enter prefix (e.g. `2a0e:b107:1234::/48`)
   - Pick primary PoP (click once → blue) and secondary (click again → green)
   - Click "Assign & Create Zones" — portal creates Master on primary,
     Slave on secondary, sends NOTIFY
3. Customer logs in, sees their prefix, creates PTR records

---

## NS records to set in RIPE

```
ns1.v6direct.org  A     <FRA_IPV4>
ns1.v6direct.org  AAAA  <FRA_IPV6>
ns2.v6direct.org  A     <DUS_IPV4>
ns2.v6direct.org  AAAA  <DUS_IPV6>
ns3.v6direct.org  A     <AMS_IPV4>
ns3.v6direct.org  AAAA  <AMS_IPV6>
ns4.v6direct.org  A     <WAW_IPV4>
ns4.v6direct.org  AAAA  <WAW_IPV6>
```

In RIPE portal → My Resources → your IPv6 allocation → DNS tab,
add all 4 NS hostnames. RIPE will validate both NS are live before
confirming delegation.

---

## Changing a customer's nameserver locations

Customer goes to their prefix → "⇄ Change NS" button:
- Click a node once = Primary (blue)
- Click a different node = Secondary (green)  
- Click Apply

The portal will:
1. Copy all existing PTR records from old primary
2. Create new Master zone on new primary with copied records
3. Create new Slave zone on new secondary
4. Delete old zones
5. Send NOTIFY to trigger AXFR

---

## Bulk import format

Paste into Bulk Import, one record per line:

```
# Space-separated
2a0e:b107:1234::1 server1.example.com
2a0e:b107:1234::2 server2.example.com

# CSV
2a0e:b107:1234::3,server3.example.com

# Comments are ignored
```

Use "Dry Run" first to validate before committing.
