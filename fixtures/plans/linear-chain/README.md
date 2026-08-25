# Token bucket rate limiting

A single client can currently exhaust the gateway's request pool. Put a per-client token
bucket in front of it.

## Steps

1. **Bucket store** — bring `src/limiter/store.py` to a state where it holds a
   `TokenBucket` dataclass and an in-memory store keyed by client id, with refill computed
   from elapsed time rather than a background timer.
   Verify: `python3 -m pytest tests/test_store.py -q`

2. **Middleware** — depends on the bucket store. Bring `src/limiter/middleware.py` to a
   state where every inbound request consumes one token and a depleted bucket returns 429
   with a `Retry-After` header.
   Verify: `python3 -m pytest tests/test_middleware.py -q`

3. **Gateway wiring** — depends on the middleware. Bring `src/gateway/app.py` to a state
   where the middleware is installed ahead of routing and its limits come from
   `config/limits.toml`.
   Verify: `python3 -m pytest tests/test_gateway.py -q`
