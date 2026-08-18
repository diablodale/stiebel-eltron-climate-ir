# Hardware session artifacts

`journal.jsonl` lands here — the raw record of everything the KC868-AG's receiver
heard, written by the `acp35_bench` component inside the devcontainer's Home
Assistant. It is gitignored. Frames worth keeping are promoted into the protocol
document as Pronto blocks with `tools/hw.py pronto`, which is what makes them part
of the regression corpus; the rest is session noise.

Nothing here is a source of truth. The protocol document is.

**Order records by `index`, not by `at`.** The development host's clock runs fast
and is stepped backwards several times a minute, so a later record can carry an
earlier timestamp and the difference between two timestamps can be negative. The
index is assigned in the same callback that receives the frame, so it always
reflects delivery order. The timings themselves are measured by the ESP32 and are
unaffected. See *Known issue: the development host's clock is unusable for
measurement* in [plan.md](../../docs/ha_ir_platform/plan.md).

See [devcontainer.md](../../docs/ha_ir_platform/devcontainer.md) for wiring the
device up, and the *Open questions the hardware must settle* section of
[plan.md](../../docs/ha_ir_platform/plan.md) for what the sessions are for.
