"""Short heartbeat windows for watching a lease expire: ``--config examples/lease-aware-client/config``.

Real use can afford wider windows; these make the demo's expiry visible within seconds.
"""

lease_mode = "heartbeat"
heartbeat_interval_seconds = 2
# Strictly greater than the interval, or the server refuses to start.
heartbeat_timeout_seconds = 7
sweep_interval_seconds = 0.5
orphan_grace_seconds = 2
