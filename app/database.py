from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
from .config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id           = Column(Integer, primary_key=True, index=True)
    username     = Column(String(64), unique=True, nullable=False)
    email        = Column(String(128), unique=True, nullable=False)
    hashed_password = Column(String(256), nullable=False)
    is_admin     = Column(Boolean, default=False)
    is_active    = Column(Boolean, default=True)
    created_at   = Column(DateTime, default=datetime.utcnow)
    prefixes     = relationship("CustomerPrefix", back_populates="owner", cascade="all, delete")


class NameserverNode(Base):
    """A physical PoP running its own PowerDNS instance."""
    __tablename__ = "nameserver_nodes"
    id           = Column(Integer, primary_key=True, index=True)
    name         = Column(String(64), unique=True, nullable=False)   # e.g. "Frankfurt"
    short        = Column(String(8),  unique=True, nullable=False)   # e.g. "FRA"
    hostname     = Column(String(128), nullable=False)               # ns1.v6direct.org
    ipv4         = Column(String(48), nullable=True)
    ipv6         = Column(String(64), nullable=True)
    pdns_api_url = Column(String(256), nullable=False)               # http://10.x.x.x:8081/api/v1
    pdns_api_key = Column(String(256), nullable=False)
    pdns_server_id = Column(String(64), default="localhost")
    is_active    = Column(Boolean, default=True)
    # Approximate coords for UI display
    lat          = Column(Float, nullable=True)
    lon          = Column(Float, nullable=True)


class CustomerPrefix(Base):
    __tablename__ = "customer_prefixes"
    id           = Column(Integer, primary_key=True, index=True)
    user_id      = Column(Integer, ForeignKey("users.id"), nullable=False)
    prefix       = Column(String(64), nullable=False)
    label        = Column(String(128), nullable=True)
    pdns_zone    = Column(String(256), nullable=False)
    # Node IDs — first = primary, second = secondary
    primary_node_id   = Column(Integer, ForeignKey("nameserver_nodes.id"), nullable=True)
    secondary_node_id = Column(Integer, ForeignKey("nameserver_nodes.id"), nullable=True)
    created_at   = Column(DateTime, default=datetime.utcnow)
    owner        = relationship("User", back_populates="prefixes")
    primary_node   = relationship("NameserverNode", foreign_keys=[primary_node_id])
    secondary_node = relationship("NameserverNode", foreign_keys=[secondary_node_id])


class AuditLog(Base):
    __tablename__ = "audit_log"
    id             = Column(Integer, primary_key=True, index=True)
    user_id        = Column(Integer, ForeignKey("users.id"), nullable=True)
    action         = Column(String(32), nullable=False)
    prefix_id      = Column(Integer, nullable=True)
    record_name    = Column(String(256))
    record_content = Column(Text)
    ip_address     = Column(String(64))
    timestamp      = Column(DateTime, default=datetime.utcnow)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
