import os
import sys

_MCP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp-server")
if _MCP not in sys.path:
    sys.path.insert(0, _MCP)

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import auth
from src import orchestrator
from src.roles import get as get_role
from src.tools import registry as tool_registry

STORE, CREDS = auth.seed()
app = FastAPI(title="TAT-C role demo")
_HERE = os.path.dirname(__file__)


def _client():
    if os.environ.get("GEMINI_API_KEY"):
        from src.models import GeminiClient

        return GeminiClient()
    import demo_client

    return demo_client.DemoClient()


def current_user(authorization: str = Header(default="")):
    token = authorization[7:] if authorization.lower().startswith("bearer ") else authorization
    user = auth.verify_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    return user


def require(capability: str):
    def dependency(user=Depends(current_user)):
        role = get_role(user["role"])
        if not role.can(capability):
            raise HTTPException(status_code=403, detail=f"role '{role.name}' lacks '{capability}'")
        return user

    return dependency


class LoginReq(BaseModel):
    email: str
    password: str


class AskReq(BaseModel):
    question: str


@app.post("/login")
def login(req: LoginReq):
    user = STORE.verify(req.email, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="bad credentials")
    role = get_role(user["role"])
    return {
        "token": auth.issue_token(user["email"], user["role"]),
        "email": user["email"],
        "role": role.name,
        "capabilities": sorted(role.capabilities),
    }


@app.get("/me")
def me(user=Depends(current_user)):
    role = get_role(user["role"])
    return {"email": user["email"], "role": role.name, "capabilities": sorted(role.capabilities)}


@app.post("/ask")
def ask(req: AskReq, user=Depends(require("query"))):
    role = get_role(user["role"])
    run = orchestrator.run(req.question, _client(), toolset=tool_registry, role=role)
    return {
        "answer": run.answer,
        "steps": [{"tool": s.tool, "arguments": s.arguments} for s in run.steps],
        "turns": run.turns,
        "truncated": run.truncated,
    }


@app.post("/author")
def author(user=Depends(require("author"))):
    return {"ok": True, "message": "Authoring session started in a sandbox (demo stub)."}


@app.post("/run_eval")
def run_eval(user=Depends(require("run_eval"))):
    return {"ok": True, "message": "Eval run queued against the pinned tier (demo stub)."}


@app.post("/promote")
def promote(user=Depends(require("promote"))):
    return {"ok": True, "message": "Authored tool verified and promoted to users (demo stub)."}


@app.get("/users")
def users(user=Depends(require("manage_users"))):
    return {"users": STORE.emails()}


@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(_HERE, "static", "index.html"), encoding="utf-8") as f:
        return f.read()
