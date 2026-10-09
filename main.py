from collections import defaultdict, deque
from math import ceil
from threading import Lock
from time import monotonic
from uuid import uuid4
import base64

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="Q11 Orders API")

# Allow the assignment page to call this API from a browser.


TOTAL_ORDERS = 46
RATE_LIMIT = 17
WINDOW_SECONDS = 10

# Store created orders and idempotency keys in memory.
created_orders = {}
idempotency_keys = {}

# Each client has its own rolling 10-second request window.
client_requests = defaultdict(deque)
lock = Lock()


class OrderRequest(BaseModel):
    item: str = "sample-item"
    quantity: int = 1


@app.middleware("http")
async def enforce_rate_limit(request: Request, call_next):
    # CORS preflight requests should not consume the rate limit.
    if request.method == "OPTIONS":
        return await call_next(request)

    client_id = request.headers.get("X-Client-Id", "anonymous")
    now = monotonic()

    with lock:
        requests = client_requests[client_id]

        # Remove requests outside the rolling 10-second window.
        while requests and now - requests[0] >= WINDOW_SECONDS:
            requests.popleft()

        if len(requests) >= RATE_LIMIT:
            retry_after = max(
                1, ceil(WINDOW_SECONDS - (now - requests[0]))
            )
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers={"Retry-After": str(retry_after)},
            )

        requests.append(now)

    return await call_next(request)


@app.get("/")
def home():
    return {
        "message": "Q11 Orders API is running",
        "total_orders": TOTAL_ORDERS,
        "rate_limit": RATE_LIMIT,
        "window_seconds": WINDOW_SECONDS,
    }


@app.post("/orders", status_code=201)
def create_order(
    body: OrderRequest,
    idempotency_key: str = Header(
        ..., alias="Idempotency-Key"
    ),
):
    key = idempotency_key.strip()
    if not key:
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key cannot be empty",
        )

    with lock:
        # Reuse the original result for a repeated key.
        if key in idempotency_keys:
            return idempotency_keys[key]

        order = {
            "id": str(uuid4()),
            "item": body.item,
            "quantity": body.quantity,
        }

        idempotency_keys[key] = order
        created_orders[order["id"]] = order
        return order


@app.get("/orders")
def list_orders(limit: int = 10, cursor: str | None = None):
    if limit < 1:
        raise HTTPException(
            status_code=400, detail="limit must be at least 1"
        )

    # An opaque base64 cursor represents the next catalog position.
    start = 0
    if cursor:
        try:
            decoded = base64.urlsafe_b64decode(
                cursor.encode() + b"=" * (-len(cursor) % 4)
            ).decode()
            start = int(decoded)
        except Exception:
            raise HTTPException(
                status_code=400, detail="Invalid cursor"
            )

    if start < 0 or start > TOTAL_ORDERS:
        raise HTTPException(
            status_code=400, detail="Invalid cursor"
        )

    end = min(start + limit, TOTAL_ORDERS)
    items = [
        {"id": order_id, "item": f"catalog-item-{order_id}"}
        for order_id in range(start + 1, end + 1)
    ]

    next_cursor = None
    if end < TOTAL_ORDERS:
        next_cursor = base64.urlsafe_b64encode(
            str(end).encode()
        ).decode().rstrip("=")

    return {
        "items": items,
        "next_cursor": next_cursor,
    }


# Ensure CORS headers are added to every response, including HTTP 429.
app = CORSMiddleware(
    app=app,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)
