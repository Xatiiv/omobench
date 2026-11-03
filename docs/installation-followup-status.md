# Installation follow-up status

This branch prepares installer version 0.1.1, not a published update. The website must
continue to link to the existing release until a new asset has been built,
tested, checked against its metadata and published.

## Implemented here

- Managed setup explicitly installs CPU-only Torch 2.15.0 and retains that build
  while resolving GuideLLM 0.7.3. Missing wheels fail without a CUDA fallback.
- Dependency consistency must pass before readiness.
- Historical profile reconciliation uses two exact, reviewed byte snapshots.
  Evidence and profile comparison keys are unchanged.

## Windows–WSL bridge

The [experimental bridge](bridge-frontend-status.md) now has an explicit panel
opt-in, a bounded multiplexed Windows stdio helper and both runner integrations.
It parses HTTP/1.1 and strips its own token on every single-request connection.
The public registry still refuses bridged measurements: local reports label the
separate Windows serving and WSL runner hosts and include transport overhead.
The existing redirect restriction remains in place. Windows–WSL behaviour and
overhead still require real-machine verification before a general support claim.

## Required external verification

- Windows build and installer/companion harnesses on CI.
- Actual Windows–WSL NAT transport and overhead checks on a Windows machine,
  with the new companion. Linux and Windows-only CI fixtures cannot establish these.
- Updated installer on the second PC, using isolated test destinations first.
- An eligible real benchmark bundle, reviewed by its owner, for the genuine
  community submission and registry/comparison test. Never submit synthetic
  fixture evidence or invent execution-device confirmation.

## Release sequence

After review and green CI, merge the candidate, build and test the release
revision, choose a new unused version tag matching VERSION, and run the existing
draft-release workflow. Verify its actual EXE, metadata and checksum before
publishing. Update the website download metadata only after the asset exists.
Do not replace the existing windows-v0.1.0 tag or rewrite its assets.
