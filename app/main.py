import os
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from .database import engine, Base
from .routers import auth, records, admin, nodes

Base.metadata.create_all(bind=engine)

app = FastAPI(title="V6Direct rDNS Portal", version="2.0.0")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

app.include_router(auth.router,    prefix="/api/auth",    tags=["auth"])
app.include_router(records.router, prefix="/api/records", tags=["records"])
app.include_router(admin.router,   prefix="/api/admin",   tags=["admin"])
app.include_router(nodes.router,   prefix="/api/nodes",   tags=["nodes"])

@app.get("/", response_class=HTMLResponse)
async def frontend():
    path = os.path.join(os.path.dirname(__file__), "../templates/index.html")
    with open(path) as f:
        return f.read()

@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0"}
