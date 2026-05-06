# Knot DNS + RESTKnot Setup Guide

This guide covers installing and configuring Knot DNS with the RESTKnot API layer on each of the four V6Direct nameserver nodes (Amsterdam, Frankfurt, Düsseldorf, Warsaw). All four nodes run identical software — the role a node plays (primary vs. secondary) is determined per-zone by the portal at zone creation time.

**References:**
- Knot DNS official docs: https://www.knot-dns.cz/docs/3.4/singlehtml/
- RESTKnot API: https://github.com/BiznetGIO/RESTKnot
- RESTKnot API reference: https://restknot.readthedocs.io/en/stable/restapi.html

---

## Architecture Overview

```
                    ┌─────────────────────────────┐
                    │   rdns.v6direct.org          │
                    │   Portal (FastAPI)            │
                    │   Talks to RESTKnot on each  │
                    │   node via HTTP REST API      │
                    └──────────┬──────────────────┘
                               │ HTTP (port 5000)
          ┌────────────────────┼──────────────────────────┐
          ▼                    ▼                          ▼
   ┌─────────────┐    ┌─────────────────┐    ┌─────────────────┐
   │ ns1 (AMS)   │    │ ns2 (FRA)       │    │ ns3 (DUS)       │
   │ Knot DNS    │    │ Knot DNS        │    │ Knot DNS        │
   │ RESTKnot    │◄──►│ RESTKnot        │◄──►│ RESTKnot        │
   │ port 53     │    │ port 53         │    │ port 53         │
   └─────────────┘    └─────────────────┘    └─────────────────┘
                                                   ▲
                                          ┌────────┘
                                  ┌───────────────┐
                                  │ ns4 (WAW)     │
                                  │ Knot DNS      │
                                  │ RESTKnot      │
                                  └───────────────┘

Zone replication: NOTIFY + AXFR between nodes (Knot native, knot.conf)
Record management: Portal → RESTKnot API → knotc socket → Knot
```

Each customer prefix gets:
- A **primary zone** on whichever node the customer picks first
- A **secondary zone** on their second chosen node, pulling via AXFR

---

## Requirements

Per node (VM or bare metal):

| Resource | Minimum | Notes |
|----------|---------|-------|
| CPU | 1 core | Knot scales linearly with cores |
| RAM | 512 MB | ~3× zone size in plain text per Knot docs |
| OS | Debian 12 / Ubuntu 22.04+ | Recommended |
| Python | 3.10+ | For RESTKnot |
| Open ports | 53/tcp, 53/udp | Public DNS |
| Internal port | 5000/tcp | RESTKnot API (portal only, not public) |

---

## Step 1 — Install Knot DNS

The Knot DNS project provides an official package repository. Using it is always preferred over distribution packages to get the latest stable release.

### Add the official Knot DNS repository (Debian/Ubuntu)

```bash
apt install -y apt-transport-https lsb-release ca-certificates curl

# Import signing key
curl -fsSL https://deb.knot-dns.cz/apt/apt.gpg | gpg --dearmor \
  -o /usr/share/keyrings/knot-dns-archive-keyring.gpg

# Add repository
echo "deb [signed-by=/usr/share/keyrings/knot-dns-archive-keyring.gpg] \
  https://deb.knot-dns.cz/apt/ $(lsb_release -sc) main" \
  > /etc/apt/sources.list.d/knot.list

apt update
apt install -y knot knot-dnsutils
```

Verify the installation:

```bash
knotd --version
# Knot DNS 3.x.x
```

---

## Step 2 — Configure Knot DNS

Knot DNS uses a YAML-style configuration file at `/etc/knot/knot.conf`. The configuration below sets up the server to listen on all interfaces, defines all four peer nodes as remotes, and allows AXFR/NOTIFY between them.

Create `/etc/knot/knot.conf` — **replace the placeholder IPs with your actual node IPs**:

```yaml
# /etc/knot/knot.conf
# V6Direct nameserver node configuration
# Deploy identical config on all 4 nodes — zone role is per-zone (set by RESTKnot)

server:
  rundir: "/run/knot"
  user: knot:knot
  listen:
    - "0.0.0.0@53"
    - "::@53"

log:
  - target: syslog
    any: info

database:
  storage: "/var/lib/knot"

# ── Peer nodes ─────────────────────────────────────────────────────────────
# All four PoP nodes. Any node can be primary or secondary for any given zone.
remote:
  - id: ams
    address: AMS_IPV4@53         # Amsterdam — ns1.v6direct.org
  - id: fra
    address: FRA_IPV4@53         # Frankfurt — ns2.v6direct.org
  - id: dus
    address: DUS_IPV4@53         # Düsseldorf — ns3.v6direct.org
  - id: waw
    address: WAW_IPV4@53         # Warsaw — ns4.v6direct.org

# ── ACL: allow AXFR and NOTIFY from all peer nodes ─────────────────────────
acl:
  - id: peers
    address:
      - AMS_IPV4
      - FRA_IPV4
      - DUS_IPV4
      - WAW_IPV4
    action: [transfer, notify]

# ── Default zone template ──────────────────────────────────────────────────
# Zones inherit these settings unless overridden.
# RESTKnot will set the master: field on secondary zones at creation time.
template:
  - id: default
    storage: "/var/lib/knot/zones"
    file: "%s.zone"
    acl: [peers]
    semantic-checks: on
    zonefile-sync: 0             # Keep zone files updated on disk
    journal-content: changes     # Store incremental history for IXFR
```

### Create the zone storage directory

```bash
mkdir -p /var/lib/knot/zones
chown knot:knot /var/lib/knot/zones
```

### Validate the configuration

```bash
knotc conf-check
# OK
```

### Start and enable Knot DNS

```bash
systemctl enable --now knot
systemctl status knot
```

### Check Knot is listening

```bash
kdig @127.0.0.1 version.server CH TXT
# Should return the Knot version string
```

---

## Step 3 — Install RESTKnot

RESTKnot consists of two components that must both run on each node:

- **Agent** — communicates with Knot via the `knotc` Unix socket
- **API** — exposes the HTTP REST interface that the portal uses

### Install RESTKnot via pip

```bash
apt install -y python3 python3-pip python3-venv

python3 -m venv /opt/restknot
/opt/restknot/bin/pip install restknot-agent restknot-api
```

### Configure the RESTKnot agent

Create `/etc/restknot/agent.yml`:

```yaml
server:
  host: 127.0.0.1
  port: 5555            # Internal agent port, not exposed

knot:
  socket: /run/knot/knot.sock   # Knot control socket path
```

### Configure the RESTKnot API

Create `/etc/restknot/api.yml`:

```yaml
server:
  host: 0.0.0.0         # Change to internal/management IP in production
  port: 5000

# This key must match pdns_api_key in the portal's node config
api_key: CHANGEME_STRONG_RANDOM_KEY

agent:
  host: 127.0.0.1
  port: 5555
```

> **Security note:** The `api_key` here is what you enter as `pdns_api_key` in the portal's Admin → Nodes panel (or seed.py). Generate a strong random key per node:
> ```bash
> python3 -c "import secrets; print(secrets.token_hex(32))"
> ```

### Create systemd units for RESTKnot

**Agent** — `/etc/systemd/system/restknot-agent.service`:

```ini
[Unit]
Description=RESTKnot Agent
After=knot.service
Requires=knot.service

[Service]
Type=simple
ExecStart=/opt/restknot/bin/restknot-agent -c /etc/restknot/agent.yml
Restart=on-failure
RestartSec=5
User=knot

[Install]
WantedBy=multi-user.target
```

**API** — `/etc/systemd/system/restknot-api.service`:

```ini
[Unit]
Description=RESTKnot API
After=restknot-agent.service
Requires=restknot-agent.service

[Service]
Type=simple
ExecStart=/opt/restknot/bin/restknot-api -c /etc/restknot/api.yml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Start RESTKnot services

```bash
systemctl daemon-reload
systemctl enable --now restknot-agent restknot-api
systemctl status restknot-agent restknot-api
```

### Verify the API is responding

```bash
curl -H "X-API-Key: CHANGEME_STRONG_RANDOM_KEY" http://127.0.0.1:5000/api/record/list
# Should return [] or a JSON array
```

---

## Step 4 — Firewall Rules

Run these on **each node**. Replace IP placeholders with actual node addresses:

```bash
# Allow public DNS queries from anywhere
ufw allow 53/tcp
ufw allow 53/udp

# Allow AXFR and NOTIFY between nodes (TCP port 53)
ufw allow from AMS_IPV4 to any port 53 proto tcp comment "Knot AXFR AMS"
ufw allow from FRA_IPV4 to any port 53 proto tcp comment "Knot AXFR FRA"
ufw allow from DUS_IPV4 to any port 53 proto tcp comment "Knot AXFR DUS"
ufw allow from WAW_IPV4 to any port 53 proto tcp comment "Knot AXFR WAW"

# Allow RESTKnot API access from the portal VM only
ufw allow from PORTAL_IP to any port 5000 proto tcp comment "RESTKnot API portal"

# Block RESTKnot API from public internet
ufw deny 5000

ufw reload
```

---

## Step 5 — Configure the Portal

Once all four nodes are running, add them in the portal's **Admin → Nameserver Nodes** panel, or let `seed.py` do it automatically on first run.

Key field mapping:

| Portal field | Value |
|---|---|
| `pdns_api_url` | `http://<node-internal-ip>:5000` |
| `pdns_api_key` | The `api_key` from that node's `api.yml` |
| `pdns_server_id` | Leave as `localhost` (unused for Knot) |

---

## Step 6 — Verify Zone Replication

After assigning a prefix to a customer and creating a test PTR record via the portal, verify the zone propagated to both nodes:

```bash
# Query the primary node directly
kdig @AMS_IPV4 -x 2a0e:b107::1

# Query the secondary node directly
kdig @FRA_IPV4 -x 2a0e:b107::1

# Both should return the same PTR record
```

To check the zone transfer status on a secondary:

```bash
knotc zone-status 7.0.1.b.e.0.a.2.ip6.arpa.
# Shows: serial, last transfer time, next refresh
```

---

## Knot DNS: Key `knotc` Commands

```bash
# Check server status
knotc status

# List all configured zones
knotc zone-status --

# Reload a specific zone from disk
knotc zone-reload 7.0.1.b.e.0.a.2.ip6.arpa.

# Force AXFR retransfer on a secondary zone
knotc zone-retransfer 7.0.1.b.e.0.a.2.ip6.arpa.

# Read zone contents
knotc zone-read 7.0.1.b.e.0.a.2.ip6.arpa.

# Check configuration syntax
knotc conf-check

# Reload configuration without restart
knotc reload

# Graceful stop
knotc stop
```

---

## Performance Tuning (Optional)

For a DNS server handling significant query load, the official Knot docs recommend the following sysctl and NIC tuning. Apply on each node as needed:

```bash
# /etc/sysctl.d/99-knot.conf

# Increase socket buffers
net.core.wmem_max     = 1048576
net.core.wmem_default = 1048576
net.core.rmem_max     = 1048576
net.core.rmem_default = 1048576

# Reduce busy-poll latency
net.core.busy_read = 0
net.core.busy_poll = 0

# Increase backlog
net.core.netdev_max_backlog = 40000
net.core.optmem_max = 20480
```

```bash
sysctl -p /etc/sysctl.d/99-knot.conf
```

NIC tuning (replace `eth0` with your interface):

```bash
ethtool -A eth0 autoneg off rx off tx off
ethtool -K eth0 tso off gro off ufo off
ethtool -G eth0 rx 4096 tx 4096
```

Per the Knot docs, the number of NIC multi-queues should equal the number of CPU cores for best performance.

---

## File Descriptor Limits

For DNS hosting with many zones, the default OS file descriptor limits are often too low. Increase them for the `knot` user:

```bash
# /etc/security/limits.d/knot.conf
knot soft nofile 65536
knot hard nofile 65536
```

```bash
# Or via systemd override
mkdir -p /etc/systemd/system/knot.service.d/
cat > /etc/systemd/system/knot.service.d/limits.conf <<EOF
[Service]
LimitNOFILE=65536
EOF
systemctl daemon-reload
systemctl restart knot
```

---

## Troubleshooting

**Zone not transferring to secondary:**
```bash
# Check Knot logs on both nodes
journalctl -u knot -f

# Manually trigger AXFR on secondary
knotc zone-retransfer <zone-name>

# Verify ACL allows AXFR from primary IP
knotc conf-read acl
```

**RESTKnot API not responding:**
```bash
# Check both services are running
systemctl status restknot-agent restknot-api

# Test knotc socket directly
sudo -u knot knotc status

# Check agent can reach the socket
ls -la /run/knot/knot.sock
# Should be owned by knot:knot with srw permissions
```

**Portal shows node offline:**
- Verify port 5000 is reachable from the portal VM
- Check the `api_key` in `api.yml` matches what's configured in the portal's node settings
- Check firewall: `ufw status` on the node

**PTR record created but not resolving:**
```bash
# Check the record exists in Knot
knotc zone-read <ip6arpa-zone> <ptr-owner>

# Check SOA serial is incrementing on writes
kdig @127.0.0.1 SOA <ip6arpa-zone>
```

---

## RIPE NS Delegation

Once all four nodes are confirmed working, set NS records in RIPE NCC:

1. Log into the RIPE NCC portal → My Resources → your IPv6 allocation → DNS tab
2. Add all four nameservers:

```
ns1.v6direct.org  →  AMS node  (primary — best IPv6)
ns2.v6direct.org  →  FRA node
ns3.v6direct.org  →  DUS node
ns4.v6direct.org  →  WAW node
```

3. RIPE will perform lame delegation checks — both listed NS records must be live and authoritative for the zone before delegation is confirmed.

In your `v6direct.org` forward zone:

```dns
ns1   IN  A     AMS_IPV4
ns1   IN  AAAA  AMS_IPV6
ns2   IN  A     FRA_IPV4
ns2   IN  AAAA  FRA_IPV6
ns3   IN  A     DUS_IPV4
ns3   IN  AAAA  DUS_IPV6
ns4   IN  A     WAW_IPV4
ns4   IN  AAAA  WAW_IPV6
```
