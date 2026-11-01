# ld-client

A Python client for Lunar Dig engines. It starts an engine process that speaks I-1 over
stdin and stdout (the native `ld-engine-cli`, or the browser build under Node), sends
requests, numbers commands, drains events, and reads the claim view. Standard library
only.

```python
from ld_client import Engine, alpha

e = Engine("ld-engine-cli")
e.create(**alpha(lots=[...]))
e.submit("INSTALL", lot_id="kits", product="solar-kit", location="pad-a", as_id="solar-1")
e.advance_by(60 * 1_000_000)
print(e.view().machine("solar-1"))
```

See `docs/guide.md` in the SDK.
