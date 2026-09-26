# Mate independent client migration implementation plan

> Execute inline using superpowers:executing-plans; one independent whole-branch review before publication.

**Goal:** Ship a reversible Mate 4.0.0-rc.1 integration of MATE-API, shared by Docker, HA and Desktop.
**Architecture:** Keep library version separate from V3 command protocol. Vendor the exact tagged pure-Python library under the existing poller payload, with provenance and an integrity manifest. A shared runtime adapter coordinates login, provisioning, history and migration backups. Both entry points initialize it explicitly; no sitecustomize injection or absolute lab paths in production.
**Tech Stack:** Python 3.12, FastAPI, SQLite, Docker, HA Supervisor, MateDesktop Python payload.
**Spec:** User-approved six-point migration scope in this conversation, and outputs/migration-4000-20260926/completion/RESULT.md.

## Global constraints
- Work only in this workspace; original installation read-only. No new worktrees.
- Preserve database, encryption key, private account material and settings. No automatic vehicle control during tests.
- Existing API V3 commands B10 only; unknown capabilities remain denied.
- Publish prereleases without silently upgrading stable users; never advertise physical/platform qualification without evidence.
- Library 0.1.0a8; Mate 4.0.0-rc.1; Desktop shell version independent.

## Review focus
- Crash between backup and provisioning: restart must preserve recoverable original data.
- Windows has no fcntl or directory fsync: importing the adapter must work with native locking.
- Two workers start at once: single migration and coordinated session login.
- Fresh installation without material: setup renders a bounded actionable status, no login storm.
- Candidate release triggers existing stable image/add-on workflows: prereleases must not replace latest or stable HA version.

## Tasks
- [x] 1. Merge verified API PR #2 and publish v0.1.0a7 prerelease. Verify tag, package version, CI.
- [ ] 2. Import proven lab delta into a new branch of the canonical Mate clone; vendor tagged API with exact hashes. Files: poller/mate_api_runtime, poller/vendor, web/mate_api.py, poller/mate_api.py, existing runtime changes. Assert no runtime dependency on legacy SDK in API mode.
- [ ] 3. Make adapter paths and process locks portable; add one-time consistent SQLite + key/material backup before migration. Tests use temporary paths, deliberate interruptions, second boot and separate processes.
- [ ] 4. Integrate startup/history with existing demo/relaunch behavior, Docker and Desktop. Test empty-data setup, demo, HTTP, restored DB and process supervision without network first.
- [ ] 5. Build candidate distribution, add release/rollback docs and protect stable channels from candidate tags. HA candidate uses explicit same image, stable add-on remains unchanged until qualification. Desktop supports candidate payload and same data location.
- [ ] 6. Run source and packaged tests; compare preserved data; deploy candidate only to 4004 with current private volume and backups. Observe read-only cloud sync; no fabricated drive/charge/physical evidence.
- [ ] 7. Fresh-context review, address material findings, publish PRs and candidate artifacts with evidence and outstanding hardware/platform checks. Never close unsupported external gates by assertion.

## Execution ledger
Ruling: a release candidate is the correct new major version until real drive/charge and native platform checks complete. Publishing a stable replacement prematurely would silently remove unverified model support from existing users.
