# Oracle authority release — 2026-09-23

Released code: `844b8fd6c796a48a6600a28abee4f0799c7e81c8`, main on origin and Oracle.
Kimi counterpart: `a4ca9da126a93ede300639f8d61cd20e1a9b790b`.
The owner approved commit, main integration, push and Oracle deployment.

Safe launch command, from /home/ubuntu/Market-AI:

```sh
sudo docker-compose -f docker-compose.yml -f deploy/authority-disabled.yml up -d --no-build --wait brain backend frontend
```

Rendered configuration and running endpoints verified: authority DISABLED,
operating mode learning, execution disabled, kill switch on, only loopback host
ports. GO_SERVER_HOST=0.0.0.0 is container-internal reachability, not a public host
binding. Brain logs confirm heartbeat-only mode. All three containers healthy;
legacy /api/signals and /api/orders/pending return404. Frontend proxy returns the
same disabled operating-mode response as the direct backend.

The old dashboard remains a LEGACY client: disconnected placeholders and zero
metrics in this mode are not broker evidence. No real trades or provider calls
were part of release verification. Kimi's Paper experiments page explains that
its owner/executor mapping is not configured. Activation is a separate step.

Fresh release gates: full Go/race/vet passed; Python204/no skips; frontend
lint/build passed; Kimi405/check/lint/build passed and cross-process fake-broker
proof passed again. Existing bundle/linker warnings remain.

Data preserved: the stopped marketflow.db plus WAL/SHM were archived under
/home/ubuntu/backups/kimi-authority-20260923/market-db.tar.gz. Old images retained
as market-ai-{brain,backend,frontend}:rollback-authority-20260923. No legacy
database migration/reset ran in DISABLED mode. No unrelated services restarted.

Rollback on unexpected enablement, errors or restarts: stop these services and
preserve the database. Do not blindly start retained legacy images: they may
revive independent trading. Verify a disabled rollback configuration first.

Notion status and architecture pages were updated; old strategy descriptions
are historical LEGACY behavior, not current execution authority.
