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

## Windows–WSL bridge implementation prerequisite

The current proposal combines opaque byte forwarding with stripping an HTTP
header containing a bridge token. Those are different protocol layers. A raw
TCP relay cannot safely remove per-request headers on a persistent connection
without parsing HTTP framing; HTTP/2 makes that distinction more important.
This must be resolved before a bridge is enabled in a downloadable installer.

The implementation must choose and test either an authenticated transport below
HTTP with explicit runner integration, or a limited HTTP-aware frontend with
correct request framing and secret stripping. It must not simply strip the
first header block and then forward a persistent stream. Tests must include
multiple requests per connection, malformed/duplicate authentication headers,
HTTP/2 behaviour and pipelining, in addition to the existing 14-case plan.
The frontend's protocol and overhead must be recorded in local reports.

The existing rule remains: one explicitly selected Windows loopback destination,
no LAN listener/firewall changes, no redirects, independent concurrent streams,
and no automatic public submission while serving-host provenance is unrepresented.

## Required external verification

- Windows build and installer/companion harnesses on CI.
- Actual Windows–WSL NAT transport and overhead checks on a Windows machine,
  once the bridge is implemented. Linux fixtures cannot establish these.
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
