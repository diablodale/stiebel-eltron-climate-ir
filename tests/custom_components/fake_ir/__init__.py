"""Dev-only fake infrared emitter.

Records the commands it is asked to send instead of transmitting them, so the
whole chain

    climate service call -> Acp35Command -> infrared.async_send_command() -> emitter

can be exercised in the devcontainer with no KC868-AG and no air conditioner.

This never ships. It lives only in the devcontainer's ``config/``.
"""

DOMAIN = "fake_ir"

# hass.data key holding the list of recorded sends, newest last.
DATA_SENT = f"{DOMAIN}_sent"
