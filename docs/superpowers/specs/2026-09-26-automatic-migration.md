# Automatic Mate migration

The normal update must preserve the installation: database, history, encrypted credentials,
PIN, certificates, MQTT entity identities and research mode. No migration wizard, external
bundle or Desktop shell reinstall is part of the upgrade contract. V3 denotes the command
protocol, not the API package version.

## Ownership and transition

Both entrypoints select one account-wide backend before importing application readers or
writers. Existing installations are backed up, then qualified in an isolated private database
copy. The qualification process has a bounded runtime and sends only authentication and
read requests. Public, checksum-pinned application parameters are supplied with Mate;
application certificates always come from the installation.

On success only new-client session/rights settings are copied in a SQLite transaction; user
history and configuration are never replaced. The qualified account certificate generation
stays private and addressable. On failure or an unqualified model the same updated Mate
runs its retained legacy client, preserving functionality and the upstream parser/UI fixes.
The decision is shared by both processes, scoped to the account and release. No command is
retried through a different backend. A failed decision is retried on a subsequent release,
not independently by one of the running processes.

Fresh setup and demo remain available. A configured installation with incomplete material
must not be mistaken for a fresh setup. The legacy dependency remains until all supported
models qualify; removal is a later transition.

## Proof and publication

Verify preservation, successful promotion, rejected and timed-out qualification, concurrent
startup, profile recovery and model fallback. Run the same payload on checksum-verified
released Desktop 1.0 binaries on Windows and macOS. Test Docker upgrades using a copy of
4004 data, without application bundle mounts or new vehicle commands. Preserve existing
HA/Beta slugs and volumes. Publish stable channels only when their compatibility gates pass;
reports must distinguish automated tests from real Supervisor/hardware qualification.
