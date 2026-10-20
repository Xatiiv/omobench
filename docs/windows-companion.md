# Windows companion (stage 1)

The companion is a small Windows program that turns the website's
**Open OmoBench** button into a running local panel. After one graphical
setup you click that button and the guided benchmark page opens in your
browser — no terminal, no `wsl.exe`, no Python activation, no connection
token to copy.

This first stage **reuses a WSL installation, checkout and Python environment
you already have**. It does not install WSL, Python, or OmoBench, and it does
not update itself. See [scope](#scope-and-follow-up-work) below.

It changes nothing about benchmarking: the panel, engine, profiles, recipes,
telemetry, packaging and submission are exactly the ones documented in
[browser control](browser-control.md) and [the guided launcher](guided-launcher.md).
Launching only opens the panel — downloading a model and starting a benchmark
stay explicit actions inside the guided page.

## What it is, and why it is built this way

A single ~57 KB native Windows executable (WinForms), compiled from the C#
sources in [`windows/companion/src`](../../windows/companion/src) with the C#
compiler that ships inside Windows itself (the in-box .NET Framework 4.x
`csc.exe`).

That compiler was chosen over the .NET SDK because every supported Windows
10/11 already has the .NET Framework runtime: the result runs with nothing for
you to install first, builds with no SDK, no NuGet restore and no network
access, and stays small enough to read end to end. A later stage that needs
code signing, an installer or self-contained .NET can revisit that choice —
the sources are ordinary C# and do not depend on it.

## Build it

```powershell
# Windows PowerShell, from the checkout root
powershell -ExecutionPolicy Bypass -File windows\companion\build.ps1 -Test
```

`-Test` also builds and runs the companion's own test harness (a plain console
program, no test framework or package downloads). The build writes:

```
windows\companion\build\OmoBenchCompanion.exe         the companion
windows\companion\build\OmoBenchCompanion.Tests.exe   its test harness
```

The `build\` folder is ignored by Git; no binary is committed.

## One-time setup (graphical)

1. Double-click `OmoBenchCompanion.exe`. With no configuration saved yet it
   opens **Setup** directly.
2. **WSL distribution** — the list is read from your machine. The companion
   checks the one you pick is WSL **2** and says how to convert it if not.
3. **OmoBench checkout** — browse to the folder containing `cli\` and
   `benchmarks\`. It must be on a local drive, because WSL reaches it as
   `/mnt/<drive>/…`; a network share is refused rather than silently
   mistranslated.
4. **Python interpreter** — the absolute path *inside WSL* of the environment
   that already has OmoBench installed, for example
   `/home/you/.venvs/omobench-runner/bin/python`.
5. **Check this environment** runs that interpreter and verifies two things
   that matter: that it imports OmoBench **from the checkout you selected**
   (not from a different copy installed elsewhere) and that the pinned
   GuideLLM version is installed in the same environment.
6. **Save and register omobench://** stores the configuration and registers
   the link handler for your user account.

No terminal commands are needed for setup or for launching.

## Trying it: three different things, in three different places

The `/run` page in this branch has an **Open OmoBench** button, but that page
is not deployed yet. Be clear about which of these you are testing:

| What you want to test | Where it works today |
| --- | --- |
| The registered `omobench://open` link, end to end | **Now**, on this machine. No terminal needed: open the companion and use **Open panel**, or open any page or shortcut containing an `omobench://open` link. |
| The updated `/run` page with its button | **Now, locally only.** The page lives in this branch: run the site locally (`npm run dev`, then open `/run` on the printed port). To test just the link without the site, double-click [`windows/companion/test-link.html`](../windows/companion/test-link.html). |
| The button on the hosted website | **Not yet.** It appears there only after this branch is pushed, reviewed, and deployed. Until then the hosted `/run` page still shows the old instructions. |

A no-terminal route that needs no website at all:

1. Double-click `OmoBenchCompanion.exe`.
2. Click **Open panel**. The panel starts and your browser opens the guided page.
3. Click **Stop** when you are done.

That exercises exactly the same code the protocol link triggers — the link
only adds "Windows launches the companion for you" in front of it.

## Normal use

Once the page carrying the button is available to you (locally now, hosted
later), click **Open OmoBench** on it. Windows starts the companion, which:

1. picks a free loopback port, starting from your configured one;
2. starts the panel with the interpreter you configured — invoked directly,
   never through a login shell, `activate` script, or whatever `guidellm`
   happens to be on `PATH`;
3. reads the panel's connection details from a private startup document
   (`omobench ui --status-file`), rather than scraping the console text;
4. opens the authenticated `127.0.0.1` address in your default browser.

The companion window shows **starting**, **ready** or **failed**, with
**Open panel**, **Stop**, **Setup…** and a diagnostic log link.

Clicking the website button again reuses the panel the companion already
owns: one companion per user session, one panel, no duplicates.

## Where things live

| What | Where |
| --- | --- |
| Configuration | `%APPDATA%\OmoBench\companion.config` (plain text; no token) |
| Diagnostic log | `%LOCALAPPDATA%\OmoBench\companion\companion.log` (tokens redacted) |
| Panel startup document | `%LOCALAPPDATA%\OmoBench\companion\panel-<id>.json` — contains the connection token; see [how its privacy is enforced](#how-the-token-document-is-protected). Deleted when the panel stops, and any left by an unclean exit are deleted at the next start |
| Protocol registration | `HKEY_CURRENT_USER\Software\Classes\omobench` (per user; no administrator rights) |
| The panel itself | Inside WSL, in your checkout, exactly as `omobench ui` has always run |

Nothing is written inside the repository, and nothing is added to system-wide
locations or to startup.

## Stopping

**Stop** shuts down only the panel this companion started. If a benchmark or
submission step is active, it says so and asks for confirmation first, because
stopping cancels it — partial evidence stays on disk but the run cannot
complete. A panel you started yourself in a terminal is never stopped and
never adopted: the companion only ever signals its own child process, and
never runs `wsl --shutdown`.

Closing the companion window while it owns a running panel asks what you want
to do first.

## Uninstalling

In **Setup → Remove registration**: removes
`HKEY_CURRENT_USER\Software\Classes\omobench`, and optionally
`%APPDATA%\OmoBench\companion.config`. It refuses to delete a registration
that points at a different program. Your checkout, Python environment,
benchmark evidence and model files are untouched. Deleting the built `.exe`
then removes the companion entirely.

Scripted equivalents, for support or automation (ordinary use never needs
them): `OmoBenchCompanion.exe --register`, `--unregister`, `--check`. Each
writes its outcome to the diagnostic log and reports it as an exit code
(`0` ready, `3` a problem to fix, `1` an error).

## Troubleshooting

| What you see | What it means |
| --- | --- |
| Nothing happens when you click the button | The handler is not registered on this PC, or your browser suppressed it. Open the companion and use **Setup → Save and register**. Windows may ask which app to use the first time. |
| "Another OmoBench panel is already running for this checkout" | The one-panel-per-checkout lock is doing its job. Use the panel that is already running, or stop it, then click again. |
| "Port … is already in use" | Something else holds every port in the probed range. Close it, or choose a different port in Setup. |
| "That interpreter imports OmoBench from a different checkout" | The environment has OmoBench installed from somewhere else. Install it from the checkout you selected, or select the matching interpreter. |
| "The pinned GuideLLM runner is not installed" | The panel would open but benchmarks could not run. Install the runner extra into that environment. |
| "The panel did not report itself ready" | A slow first start (cold Python imports) or a failure inside WSL. The diagnostic log has the panel's own output, with tokens redacted. |

## How the token document is protected

The panel hands over its connection details by writing one small JSON
document that the companion reads and then deletes. That document contains
the connection token, so where it lives matters.

Two protections that look sufficient are not, and the companion does not rely
on either:

- **The POSIX mode is ignored.** The panel creates the file `0600`, but it
  writes it from inside WSL through `/mnt/c`, and the default DrvFs mount
  carries no `metadata` option. The mode is silently discarded — the file
  reads back as `0777` inside WSL. It is still requested, because the same
  code serves a panel writing to a real Linux filesystem, where it is
  honoured.
- **Inheritance is not guaranteed.** `%LOCALAPPDATA%` usually grants only
  SYSTEM, Administrators and the owner, but a machine can carry extra
  inherited groups. On the machine this was verified on, the folder was
  additionally readable by a locally defined group, which would have made the
  token readable by every member of it.

So the companion sets the access list on
`%LOCALAPPDATA%\OmoBench\companion` explicitly: inheritance off, access for
this user and SYSTEM only. The status document inherits that. Applied on
every start, so an existing folder is corrected rather than trusted.

Alongside that:

- The document is refused unless it carries the schema this companion knows
  and a `127.0.0.1` URL, so a stale or edited file cannot send the browser —
  and the token — anywhere else.
- Before asking the panel to shut down, the companion checks the panel
  answers on its port with *this session's* token. A stale document naming
  some other process cannot get that process stopped.
- The writer refuses to follow a symlink planted at its path.
- Everything the panel prints is redacted before it reaches the diagnostic
  log, so the token appears in no log, error message, or support paste.

## Security notes

- The panel still binds to loopback only, still requires its connection token,
  and still checks request origin. This stage does not open cross-origin
  access, and the hosted page cannot reach the panel's APIs.
- `omobench://open` is the only accepted action. Anything else — extra path
  segments, query parameters, fragments, credentials, a different scheme — is
  rejected. No value from a link ever becomes part of a command line: what
  runs comes from your saved configuration.
- Nothing is passed through a shell. `wsl.exe` is invoked with an argument
  list and execs the interpreter directly.
- The connection token appears only in the private startup document and in
  memory. It is redacted from the diagnostic log, and it never reaches the
  repository or the hosted website.

## Scope and follow-up work

**This stage:** an existing Windows + WSL2 + checkout + Python environment;
per-user setup and registration; start, reuse and stop the existing panel.

**Not yet, and deliberately so:**

- installing WSL, Python or OmoBench on a fresh machine, and automatic updates;
- a signed, publicly downloadable installer — there is no public download, and
  the website does not claim one;
- pairing the hosted page with the local panel so the website could show live
  status or drive the local APIs. Today the page only asks Windows to open the
  companion, and says so.
