import base64
import math
import threading
import time
import uuid
from collections import defaultdict, deque

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

TOTAL_ORDERS = 40       # T
RATE_LIMIT = 16         # R requests ...
WINDOW_SECONDS = 10     # ... per 10 seconds

app = FastAPI(title="Orders API")

# Fixed catalog: ids 1..T. Orders created via POST live in a separate store
# (string ids) so they never disturb the paginated 1..T scan.
CATALOG = [{"id": i, "item": f"item-{i}", "status": "confirmed"} for i in range(1, TOTAL_ORDERS + 1)]
CREATED: dict[str, dict] = {}          # order id -> order
IDEMPOTENCY: dict[str, dict] = {}      # idempotency key -> order
LOCK = threading.Lock()

# ---------------- rate limiting (sliding window, per X-Client-Id) ----------------
HITS: dict[str, deque] = defaultdict(deque)
RL_LOCK = threading.Lock()


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    if request.method == "OPTIONS":  # CORS preflight is not counted
        return await call_next(request)

    client_id = request.headers.get("x-client-id") or f"ip:{request.client.host if request.client else 'unknown'}"
    now = time.monotonic()
    with RL_LOCK:
        q = HITS[client_id]
        while q and now - q[0] >= WINDOW_SECONDS:
            q.popleft()
        if len(q) >= RATE_LIMIT:
            retry_after = max(1, math.ceil(WINDOW_SECONDS - (now - q[0])))
            return JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded"},
                headers={"Retry-After": str(retry_after)},
            )
        q.append(now)
    return await call_next(request)


# CORS added after the rate limiter so it is the outermost layer and 429s carry CORS headers too.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After", "Idempotency-Key"],
)


# ---------------- idempotent POST ----------------
@app.post("/orders", status_code=201)
async def create_order(request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            body = {}
    except Exception:
        body = {}

    key = request.headers.get("idempotency-key")
    with LOCK:
        if key and key in IDEMPOTENCY:
            return IDEMPOTENCY[key]
        order = {"id": uuid.uuid4().hex, "status": "created", "data": body}
        CREATED[order["id"]] = order
        if key:
            IDEMPOTENCY[key] = order
    return order


# ---------------- cursor pagination ----------------
def encode_cursor(last_id: int) -> str:
    return base64.urlsafe_b64encode(f"after:{last_id}".encode()).decode()


def decode_cursor(cursor: str) -> int:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        prefix, value = raw.split(":", 1)
        if prefix != "after":
            raise ValueError
        return int(value)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid cursor")


@app.get("/orders")
async def list_orders(limit: int = Query(10, ge=1, le=100), cursor: str | None = None):
    after = decode_cursor(cursor) if cursor else 0
    items = [o for o in CATALOG if o["id"] > after][:limit]
    next_cursor = encode_cursor(items[-1]["id"]) if items and items[-1]["id"] < TOTAL_ORDERS else None
    return {"items": items, "next_cursor": next_cursor}


@app.get("/health")
async def health():
    return {"ok": True}
