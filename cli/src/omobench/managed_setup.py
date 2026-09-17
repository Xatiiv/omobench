"""Reviewed, managed CPU download recipes: review, resumable download, and safe install.

This module never runs an installer, never alters system packages, and never
starts a benchmark by itself. It only prepares verified files under a
contributor-chosen storage folder so the existing launcher/CLI/runner can use
them. Benchmark profiles, GuideLLM command construction, normalization,
packaging, schema, and public v1 execution-device publication rules are
unrelated to this module and are not touched by it.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import posixpath
import re
import shutil
import sys
import tarfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import yaml


CATALOG_RELATIVE_PATH = Path("catalog") / "downloads" / "cpu-v1.json"
MODEL_CATALOG_RELATIVE_PATH = Path("catalog") / "models"
MANAGED_ROOT_NAME = "omobench-managed-v1"
ELIGIBLE_MODALITY = "text-generation"
MUTABLE_REVISION_LABELS = {"main", "master", "latest", "head"}
ALLOWED_DOWNLOAD_HOSTS = {"huggingface.co", "github.com"}
GIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MIN_GLIBC_VERSION = (2, 34)
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
MAX_REDIRECTS = 5
LOOPBACK_HOSTNAMES = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
WINDOWS_STYLE_PATH_PATTERN = re.compile(r"^[A-Za-z]:[\\/]")

ProgressCallback = Callable[[str, int, int], None]


class ManagedSetupError(Exception):
    """Raised for any recipe, platform, download, or installation problem."""


# --------------------------------------------------------------------------
# Recipe catalog loading and validation
# --------------------------------------------------------------------------


def catalog_path(root: Path) -> Path:
    return root / CATALOG_RELATIVE_PATH


def _load_model_catalog_ids(root: Path) -> set[str]:
    catalog_dir = root / MODEL_CATALOG_RELATIVE_PATH
    ids: set[str] = set()
    if not catalog_dir.is_dir():
        return ids
    for path in sorted(catalog_dir.glob("*.yaml")):
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(document, dict):
            continue
        for entry in document.get("models", []):
            if (
                isinstance(entry, dict)
                and isinstance(entry.get("id"), str)
                and entry.get("modality") == ELIGIBLE_MODALITY
            ):
                ids.add(entry["id"])
    return ids


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManagedSetupError(f"Recipe field {label} must be a non-empty string.")
    return value.strip()


def _require_positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ManagedSetupError(f"Recipe field {label} must be a positive integer.")
    return value


def _require_revision(value: Any, label: str) -> str:
    text = _require_text(value, label)
    if text.lower() in MUTABLE_REVISION_LABELS:
        raise ManagedSetupError(f"Recipe field {label} must be an immutable revision, not {text!r}.")
    if not GIT_SHA_PATTERN.fullmatch(text.lower()):
        raise ManagedSetupError(f"Recipe field {label} must be a full 40-character hex commit SHA.")
    return text


def _require_sha256(value: Any, label: str) -> str:
    text = _require_text(value, label)
    if not SHA256_PATTERN.fullmatch(text.lower()):
        raise ManagedSetupError(f"Recipe field {label} must be a 64-character hex SHA-256 digest.")
    return text.lower()


def _require_https_url(value: Any, label: str, *, expected_host: str) -> str:
    text = _require_text(value, label)
    parts = urlsplit(text)
    if parts.scheme != "https":
        raise ManagedSetupError(f"Recipe field {label} must use https.")
    if parts.hostname != expected_host or parts.hostname not in ALLOWED_DOWNLOAD_HOSTS:
        raise ManagedSetupError(f"Recipe field {label} must point at {expected_host}.")
    if parts.username or parts.password:
        raise ManagedSetupError(f"Recipe field {label} must not carry credentials.")
    return text


def _validate_archive_member_name(name: str, label: str) -> None:
    if not name or "\x00" in name:
        raise ManagedSetupError(f"{label}: invalid archive path {name!r}.")
    if "\\" in name:
        raise ManagedSetupError(f"{label}: archive path {name!r} contains a backslash.")
    if name.startswith("/") or name.startswith("~"):
        raise ManagedSetupError(f"{label}: archive path {name!r} is absolute.")
    if any(segment == ".." for segment in name.split("/")):
        raise ManagedSetupError(f"{label}: archive path {name!r} escapes the destination.")


def validate_recipe(root: Path, recipe: dict[str, Any], *, known_model_ids: set[str] | None = None) -> None:
    """Validate one recipe's structure, catalog membership, and provenance fields.

    Raises ManagedSetupError with a specific reason on any problem. Does not
    perform any network access.
    """
    if not isinstance(recipe, dict):
        raise ManagedSetupError("Recipe must be an object.")
    recipe_id = _require_text(recipe.get("id"), "id")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", recipe_id):
        raise ManagedSetupError(f"Recipe id {recipe_id!r} must be lowercase letters, digits, and hyphens.")
    _require_text(recipe.get("title"), "title")
    model_id = _require_text(recipe.get("model_id"), "model_id")

    if known_model_ids is None:
        known_model_ids = _load_model_catalog_ids(root)
    if model_id not in known_model_ids:
        raise ManagedSetupError(
            f"Recipe {recipe_id!r} references model {model_id!r}, which is not an eligible "
            f"text-generation model in {MODEL_CATALOG_RELATIVE_PATH.as_posix()}."
        )

    _require_text(recipe.get("quantization"), "quantization")
    platform_name = _require_text(recipe.get("platform"), "platform")
    if platform_name != "linux-x86_64":
        raise ManagedSetupError(f"Recipe {recipe_id!r} declares unsupported platform {platform_name!r}.")
    _require_text(recipe.get("supported_environment"), "supported_environment")
    parse_glibc_version(_require_text(recipe.get("min_glibc_version"), "min_glibc_version"))
    benchmark_profile = _require_text(recipe.get("benchmark_profile"), "benchmark_profile")
    from . import cli as omobench_cli  # local import: keep managed_setup importable without cli's dependencies

    if benchmark_profile not in omobench_cli.PROFILE_SPECS:
        raise ManagedSetupError(f"Recipe {recipe_id!r} references unknown benchmark profile {benchmark_profile!r}.")
    execution_device = _require_text(recipe.get("execution_device"), "execution_device")
    if execution_device != "cpu":
        raise ManagedSetupError(f"Recipe {recipe_id!r} must declare execution_device 'cpu' in this milestone.")

    model = recipe.get("model")
    if not isinstance(model, dict):
        raise ManagedSetupError(f"Recipe {recipe_id!r} is missing a 'model' download description.")
    _require_text(model.get("repository"), "model.repository")
    _require_revision(model.get("revision"), "model.revision")
    _require_text(model.get("filename"), "model.filename")
    _require_https_url(model.get("url"), "model.url", expected_host="huggingface.co")
    _require_sha256(model.get("sha256"), "model.sha256")
    _require_positive_int(model.get("size_bytes"), "model.size_bytes")
    _require_text(model.get("licence"), "model.licence")
    if model.get("url", "").rstrip("/").rsplit("/", 1)[-1] != model.get("filename"):
        raise ManagedSetupError(f"Recipe {recipe_id!r} model.url must end with model.filename.")

    runtime = recipe.get("runtime")
    if not isinstance(runtime, dict):
        raise ManagedSetupError(f"Recipe {recipe_id!r} is missing a 'runtime' download description.")
    _require_text(runtime.get("repository"), "runtime.repository")
    _require_revision(runtime.get("revision"), "runtime.revision")
    _require_text(runtime.get("version"), "runtime.version")
    _require_text(runtime.get("filename"), "runtime.filename")
    _require_https_url(runtime.get("url"), "runtime.url", expected_host="github.com")
    _require_sha256(runtime.get("sha256"), "runtime.sha256")
    _require_positive_int(runtime.get("size_bytes"), "runtime.size_bytes")
    _require_text(runtime.get("licence"), "runtime.licence")
    if runtime.get("archive_format") != "tar.gz":
        raise ManagedSetupError(f"Recipe {recipe_id!r} runtime.archive_format must be 'tar.gz'.")
    executable_path = _require_text(runtime.get("executable_path"), "runtime.executable_path")
    _validate_archive_member_name(executable_path, f"Recipe {recipe_id!r} runtime.executable_path")
    _require_positive_int(runtime.get("max_extracted_bytes"), "runtime.max_extracted_bytes")
    _require_positive_int(runtime.get("max_entries"), "runtime.max_entries")
    if runtime.get("max_extracted_bytes") < runtime.get("size_bytes"):
        raise ManagedSetupError(
            f"Recipe {recipe_id!r} runtime.max_extracted_bytes must be at least the archive size_bytes."
        )
    if runtime.get("url", "").rstrip("/").rsplit("/", 1)[-1] != runtime.get("filename"):
        raise ManagedSetupError(f"Recipe {recipe_id!r} runtime.url must end with runtime.filename.")


def load_catalog_document(root: Path) -> dict[str, Any]:
    path = catalog_path(root)
    if not path.is_file():
        raise ManagedSetupError(f"Managed download catalog is missing: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ManagedSetupError(f"Could not read managed download catalog: {error}") from error
    if not isinstance(document, dict) or not isinstance(document.get("recipes"), list):
        raise ManagedSetupError("Managed download catalog must contain a 'recipes' list.")
    return document


def load_recipes(root: Path) -> list[dict[str, Any]]:
    """Load and fully validate every recipe in the managed download catalog."""
    document = load_catalog_document(root)
    known_model_ids = _load_model_catalog_ids(root)
    recipes = []
    seen_ids: set[str] = set()
    for entry in document["recipes"]:
        validate_recipe(root, entry, known_model_ids=known_model_ids)
        if entry["id"] in seen_ids:
            raise ManagedSetupError(f"Duplicate recipe id in managed download catalog: {entry['id']!r}")
        seen_ids.add(entry["id"])
        recipes.append(entry)
    return recipes


def get_recipe(root: Path, recipe_id: str) -> dict[str, Any]:
    for recipe in load_recipes(root):
        if recipe["id"] == recipe_id:
            return recipe
    raise ManagedSetupError(f"Unknown managed recipe: {recipe_id!r}. Use --list to see reviewed recipes.")


# --------------------------------------------------------------------------
# Platform checks
# --------------------------------------------------------------------------


def parse_glibc_version(text: str) -> tuple[int, int]:
    match = re.search(r"(\d+)\.(\d+)", text)
    if not match:
        raise ManagedSetupError(f"Could not parse glibc version from {text!r}.")
    return int(match.group(1)), int(match.group(2))


def meets_minimum_glibc(actual: tuple[int, int], minimum: tuple[int, int] = MIN_GLIBC_VERSION) -> bool:
    return actual >= minimum


def detected_glibc_version() -> tuple[int, int] | None:
    try:
        version_string = os.confstr("CS_GNU_LIBC_VERSION")
    except (AttributeError, OSError, ValueError):
        return None
    if not version_string:
        return None
    try:
        return parse_glibc_version(version_string)
    except ManagedSetupError:
        return None


@dataclass
class PlatformStatus:
    ok: bool
    message: str


def check_platform() -> PlatformStatus:
    """Confirm this managed CPU recipe can run here: Linux x64/WSL2, glibc >= 2.34.

    Native Windows is explicitly rejected with an explanation, because the
    current GuideLLM runner extra depends on uvloop, which does not support
    native Windows.
    """
    if sys.platform == "win32":
        return PlatformStatus(
            False,
            "Native Windows cannot run the managed CPU recipe. The GuideLLM runner "
            "depends on uvloop, which does not support native Windows. Start WSL2 "
            "(for example: wsl.exe -d Ubuntu) and run this command from Linux/WSL2 instead.",
        )
    if not sys.platform.startswith("linux"):
        return PlatformStatus(
            False,
            f"The managed CPU recipe currently supports Linux x64 or WSL2 only; detected {sys.platform!r}.",
        )
    machine = os.uname().machine if hasattr(os, "uname") else ""
    if machine not in {"x86_64", "amd64"}:
        return PlatformStatus(
            False, f"The managed CPU recipe currently supports linux-x86_64 only; detected {machine!r}."
        )
    glibc = detected_glibc_version()
    if glibc is None:
        return PlatformStatus(
            False,
            "Could not detect a glibc version on this system (for example musl libc). "
            "The managed CPU recipe requires glibc 2.34 or newer.",
        )
    if not meets_minimum_glibc(glibc):
        return PlatformStatus(
            False,
            f"Detected glibc {glibc[0]}.{glibc[1]}; the managed CPU recipe requires glibc 2.34 or newer.",
        )
    return PlatformStatus(True, f"Linux x86_64 with glibc {glibc[0]}.{glibc[1]}: ready for the managed CPU recipe.")


def normalize_storage_dir(raw: str) -> Path:
    """Validate a contributor-supplied storage folder without converting it.

    Refuses to guess a WSL-style path from a Windows-style one (or vice
    versa): the caller must supply the path exactly as this operating system
    expects it, so a mistaken /mnt/d/... vs D:\\... path fails loudly instead
    of silently writing to the wrong filesystem.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ManagedSetupError("A storage folder is required, for example /mnt/d/OmoBench.")
    text = raw.strip()
    looks_windows_style = bool(WINDOWS_STYLE_PATH_PATTERN.match(text)) or "\\" in text
    if os.name == "nt":
        if not looks_windows_style and text.startswith("/"):
            raise ManagedSetupError(
                f"{text!r} looks like a WSL/Linux path. On native Windows, use a Windows path such as D:\\OmoBench."
            )
    else:
        if looks_windows_style:
            raise ManagedSetupError(
                f"{text!r} looks like a Windows path. In WSL/Linux, use the /mnt/... form instead, "
                "for example /mnt/d/OmoBench for D:\\OmoBench. OmoBench does not silently convert this for you."
            )
        if not text.startswith("/"):
            raise ManagedSetupError(f"Storage folder {text!r} must be an absolute path, for example /mnt/d/OmoBench.")
    return Path(text)


def managed_root(storage_dir: Path) -> Path:
    return storage_dir / MANAGED_ROOT_NAME


# --------------------------------------------------------------------------
# Storage lock: one setup process per storage folder
# --------------------------------------------------------------------------


@contextlib.contextmanager
def storage_lock(storage_dir: Path):
    root = managed_root(storage_dir)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / "setup.lock"
    if lock_path.is_symlink():
        raise ManagedSetupError("The managed setup lock file must be a regular file.")
    with lock_path.open("a+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt

                if lock_path.stat().st_size == 0:
                    lock.write(b"0")
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ManagedSetupError(
                "Another managed setup process is already preparing this storage folder. "
                "Wait for it to finish or stop it first."
            ) from error
        yield


# --------------------------------------------------------------------------
# Review (read-only)
# --------------------------------------------------------------------------


def _disk_usage(path: Path) -> tuple[Path, int] | None:
    current = path
    while True:
        if current.exists():
            usage = shutil.disk_usage(current)
            return current, usage.free
        if current.parent == current:
            return None
        current = current.parent


# --------------------------------------------------------------------------
# Storage-space verification: WSL2's own virtual disk vs. a real backing drive
# --------------------------------------------------------------------------

# WSL2 exposes real Windows drives -- whichever letters happen to be mounted,
# never assumed to be C: -- under /mnt/<letter>, using one of these
# filesystem types. shutil.disk_usage() there passes through to the real
# Windows volume's own free space, exactly like a native Linux mount.
WINDOWS_DRIVE_FSTYPES = {"9p", "drvfs"}

UNVERIFIED_WSL_SPACE_NOTE = (
    "This folder is on WSL2's own internal virtual disk, not a mounted Windows drive. The free space "
    "shown is that virtual filesystem's own reported capacity, which can be larger than what the Windows "
    "drive actually backing it can provide -- whether there is truly enough room is unverified from here. "
    "A folder under a mounted Windows drive (for example /mnt/c/... or /mnt/d/...) can be checked reliably."
)


def _running_under_wsl() -> bool:
    """Best-effort, read-only WSL2 detection: environment variables WSL2 sets,
    then the kernel's own release string. Never mounts or changes anything."""
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return False


def _read_proc_mounts() -> list[tuple[str, str]]:
    """(mount_point, fstype) pairs from /proc/mounts. Read-only; [] where
    unavailable, for example on a non-Linux platform."""
    try:
        lines = Path("/proc/mounts").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    pairs = []
    for line in lines:
        fields = line.split()
        if len(fields) >= 3:
            pairs.append((fields[1], fields[2]))
    return pairs


def _on_a_mounted_windows_drive(path: Path, mounts: list[tuple[str, str]]) -> bool:
    """True when `path` resolves under the longest-matching mount whose
    filesystem type is a real Windows-drive mount (see WINDOWS_DRIVE_FSTYPES).
    Identifies the actual mounted drive from /proc/mounts; never assumes C:.
    """
    resolved = path.resolve().as_posix()
    best_match: tuple[str, str] | None = None
    for mount_point, fstype in mounts:
        if mount_point == "/" or resolved == mount_point or resolved.startswith(mount_point.rstrip("/") + "/"):
            if best_match is None or len(mount_point) > len(best_match[0]):
                best_match = (mount_point, fstype)
    return best_match is not None and best_match[1] in WINDOWS_DRIVE_FSTYPES


def storage_space_status(path: Path) -> dict[str, Any]:
    """Free space for `path`, honest about what can and cannot be verified.

    A path under a real, mounted Windows drive reports disk usage that
    passes through to that actual volume, exactly like ordinary native Linux:
    verified. A path on WSL2's own internal virtual filesystem reports that
    virtual filesystem's own capacity, which is not necessarily how much room
    the Windows host can actually give it -- reported as unverified rather
    than a confident "enough space". Never resizes, mounts, or changes
    anything; read-only throughout.
    """
    usage = _disk_usage(path)
    free_bytes = usage[1] if usage else None
    if free_bytes is None:
        return {"free_space_bytes": None, "free_space_verified": True, "space_note": None}
    if not _running_under_wsl() or _on_a_mounted_windows_drive(path, _read_proc_mounts()):
        return {"free_space_bytes": free_bytes, "free_space_verified": True, "space_note": None}
    return {"free_space_bytes": free_bytes, "free_space_verified": False, "space_note": UNVERIFIED_WSL_SPACE_NOTE}


def _cached_download_is_verified(storage_dir: Path, filename: str, size_bytes: int, sha256: str) -> bool:
    """Whether a byte-identical, hash-verified copy of this file already sits
    in this storage folder's shared downloads/ cache -- independent of which
    recipe (if any) put it there. Both recipes in this catalog pin identical
    model and runtime filenames, so installing one already satisfies the
    other's download step for that file; this is how that reuse is detected,
    honestly, rather than assumed. Read-only: only hashes a file that already
    matches the expected size, and never downloads, writes, or deletes."""
    final_path = managed_root(storage_dir) / "downloads" / filename
    if not final_path.is_file() or final_path.stat().st_size != size_bytes:
        return False
    return sha256_of_file(final_path) == sha256


def review(root: Path, recipe_id: str, storage_dir: Path) -> dict[str, Any]:
    """Read-only report of what a download/install would do. Makes no network calls."""
    recipe = get_recipe(root, recipe_id)
    platform_status = check_platform()
    space = storage_space_status(storage_dir)
    free_bytes = space["free_space_bytes"]
    required_bytes = recipe["model"]["size_bytes"] + recipe["runtime"]["max_extracted_bytes"]

    # Total artifact size (what these two files weigh) vs. bytes this storage
    # folder still actually needs to download (0 for any file it can already
    # verify in its shared cache) are two different numbers; conflating them
    # would describe a cache hit as a fresh multi-gigabyte download.
    model_cached = _cached_download_is_verified(
        storage_dir, recipe["model"]["filename"], recipe["model"]["size_bytes"], recipe["model"]["sha256"]
    )
    runtime_cached = _cached_download_is_verified(
        storage_dir, recipe["runtime"]["filename"], recipe["runtime"]["size_bytes"], recipe["runtime"]["sha256"]
    )
    bytes_to_download = (0 if model_cached else recipe["model"]["size_bytes"]) + (
        0 if runtime_cached else recipe["runtime"]["size_bytes"]
    )

    installed_state: dict[str, Any] = {"installed": False, "valid": None, "error": None}
    manifest = read_manifest(storage_dir, recipe_id)
    if manifest is not None:
        installed_state["installed"] = True
        try:
            verify_installed(root, recipe, storage_dir)
            installed_state["valid"] = True
        except ManagedSetupError as error:
            installed_state["valid"] = False
            installed_state["error"] = str(error)

    return {
        "recipe": {
            "id": recipe["id"],
            "title": recipe["title"],
            "model_id": recipe["model_id"],
            "quantization": recipe["quantization"],
            "platform": recipe["platform"],
            "supported_environment": recipe["supported_environment"],
            "benchmark_profile": recipe["benchmark_profile"],
            "execution_device": recipe["execution_device"],
        },
        "files": [
            {
                "kind": "model",
                "filename": recipe["model"]["filename"],
                "repository": recipe["model"]["repository"],
                "revision": recipe["model"]["revision"],
                "url": recipe["model"]["url"],
                "sha256": recipe["model"]["sha256"],
                "size_bytes": recipe["model"]["size_bytes"],
                "licence": recipe["model"]["licence"],
                "already_cached": model_cached,
            },
            {
                "kind": "runtime",
                "filename": recipe["runtime"]["filename"],
                "repository": recipe["runtime"]["repository"],
                "revision": recipe["runtime"]["revision"],
                "version": recipe["runtime"]["version"],
                "url": recipe["runtime"]["url"],
                "sha256": recipe["runtime"]["sha256"],
                "size_bytes": recipe["runtime"]["size_bytes"],
                "licence": recipe["runtime"]["licence"],
                "already_cached": runtime_cached,
            },
        ],
        "platform": {"ok": platform_status.ok, "message": platform_status.message},
        "storage_dir": str(storage_dir),
        "storage_dir_exists": storage_dir.exists(),
        "bytes_to_download": bytes_to_download,
        "free_space_bytes": free_bytes,
        "free_space_verified": space["free_space_verified"],
        "space_note": space["space_note"],
        "required_bytes": required_bytes,
        "has_enough_space": None if (free_bytes is None or not space["free_space_verified"]) else free_bytes >= required_bytes,
        "already_installed": installed_state,
    }


# --------------------------------------------------------------------------
# Resumable, verified download
# --------------------------------------------------------------------------


class _BoundedSafeRedirectHandler(HTTPRedirectHandler):
    """Follow redirects only while every hop stays https, credential-free, non-loopback."""

    def __init__(self) -> None:
        super().__init__()
        self.hops = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.hops += 1
        if self.hops > MAX_REDIRECTS:
            raise ManagedSetupError("Too many redirects while downloading.")
        parts = urlsplit(newurl)
        if parts.scheme != "https":
            raise ManagedSetupError(f"Refusing an insecure redirect to {newurl!r}.")
        if parts.username or parts.password:
            raise ManagedSetupError("Refusing a redirect that carries credentials.")
        hostname = (parts.hostname or "").lower()
        if not hostname or hostname in LOOPBACK_HOSTNAMES or hostname.endswith(".local"):
            raise ManagedSetupError(f"Refusing an unsafe redirect to {newurl!r}.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _quarantine(managed: Path, path: Path, reason: str) -> Path:
    quarantine_dir = managed / "quarantine"
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    destination = quarantine_dir / f"{path.name}.{int(time.time())}.quarantined"
    with contextlib.suppress(OSError):
        shutil.move(str(path), str(destination))
    (destination.with_suffix(destination.suffix + ".reason.txt")).write_text(reason + "\n", encoding="utf-8")
    return destination


def sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(DOWNLOAD_CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def resumable_download(
    url: str,
    *,
    part_path: Path,
    final_path: Path,
    expected_size: int,
    expected_sha256: str,
    managed: Path,
    label: str,
    progress: ProgressCallback | None = None,
    cancel_event: Any = None,
) -> None:
    """Download url to final_path, resuming from part_path.part when possible.

    Verifies size and SHA-256 before promoting the file into the cache;
    quarantines (rather than uses) a corrupted or truncated result.
    """
    if final_path.is_file() and final_path.stat().st_size == expected_size:
        if sha256_of_file(final_path) == expected_sha256:
            if progress:
                progress(label, expected_size, expected_size)
            return
        _quarantine(managed, final_path, "Existing cached file failed re-verification.")

    part_path.parent.mkdir(parents=True, exist_ok=True)
    resume_from = part_path.stat().st_size if part_path.is_file() else 0
    if resume_from > expected_size:
        resume_from = 0
        part_path.unlink(missing_ok=True)

    headers = {"User-Agent": "omobench-managed-setup/1"}
    if resume_from:
        headers["Range"] = f"bytes={resume_from}-"

    opener = build_opener(ProxyHandler({}), _BoundedSafeRedirectHandler())
    request = Request(url, headers=headers)
    try:
        with opener.open(request, timeout=60) as response:
            status = getattr(response, "status", 200)
            mode = "ab" if status == 206 and resume_from else "wb"
            if mode == "wb":
                resume_from = 0
            with part_path.open(mode) as destination:
                downloaded = resume_from
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise ManagedSetupError(f"{label} download was cancelled.")
                    chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    destination.write(chunk)
                    downloaded += len(chunk)
                    if downloaded > expected_size:
                        raise ManagedSetupError(f"{label} download exceeded the expected size.")
                    if progress:
                        progress(label, downloaded, expected_size)
    except ManagedSetupError:
        raise
    except HTTPError as error:
        raise ManagedSetupError(f"Could not download {label}: HTTP {error.code}.") from error
    except (URLError, OSError, TimeoutError) as error:
        raise ManagedSetupError(f"Could not download {label}: {error}. It can be resumed by retrying.") from error

    final_size = part_path.stat().st_size
    if final_size != expected_size:
        raise ManagedSetupError(
            f"{label} download is incomplete ({final_size} of {expected_size} bytes). Retry to resume it."
        )
    actual_sha256 = sha256_of_file(part_path)
    if actual_sha256 != expected_sha256:
        _quarantine(managed, part_path, f"{label}: SHA-256 mismatch (expected {expected_sha256}, got {actual_sha256}).")
        raise ManagedSetupError(
            f"{label} failed SHA-256 verification and was quarantined, not installed. Retry the download."
        )
    final_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(part_path, final_path)


# --------------------------------------------------------------------------
# Safe archive extraction
# --------------------------------------------------------------------------


def _validate_symlink_target(linkname: str, label: str) -> None:
    """Reject anything but a relative, in-archive symlink target.

    Absolute targets, backslashes, and any '../' segment are refused here so
    a symlink can never be used to point outside the archive namespace.
    """
    if not linkname or "\x00" in linkname:
        raise ManagedSetupError(f"{label}: symlink has an invalid target {linkname!r}.")
    if "\\" in linkname:
        raise ManagedSetupError(f"{label}: symlink target {linkname!r} contains a backslash.")
    if linkname.startswith("/") or linkname.startswith("~") or WINDOWS_STYLE_PATH_PATTERN.match(linkname):
        raise ManagedSetupError(f"{label}: symlink target {linkname!r} is absolute.")
    if any(segment == ".." for segment in linkname.split("/")):
        raise ManagedSetupError(f"{label}: symlink target {linkname!r} escapes the archive.")


def _resolve_symlink_archive_path(name: str, linkname: str) -> str:
    """Resolve a symlink's target relative to the symlink's own containing directory."""
    directory = name.rsplit("/", 1)[0] if "/" in name else ""
    combined = f"{directory}/{linkname}" if directory else linkname
    resolved = posixpath.normpath(combined)
    if resolved == ".." or resolved.startswith("../") or resolved.startswith("/"):
        raise ManagedSetupError(f"Runtime archive: symlink target {linkname!r} resolves outside the archive.")
    return resolved


def _resolve_symlink_source(
    name: str,
    members_by_name: dict[str, tarfile.TarInfo],
    label: str,
) -> tarfile.TarInfo:
    """Follow a chain of internal symlinks to the regular file that provides its bytes.

    Raises on a missing target, a target that is not (eventually) a regular
    file, or a cycle. Never returns a symlink.
    """
    visited: set[str] = set()
    current = name
    for _ in range(len(members_by_name) + 1):
        if current in visited:
            raise ManagedSetupError(f"{label}: symlink {name!r} forms a cycle.")
        visited.add(current)
        member = members_by_name.get(current)
        if member is None:
            raise ManagedSetupError(f"{label}: symlink {name!r} target {current!r} does not exist in the archive.")
        if member.issym():
            _validate_symlink_target(member.linkname, label)
            current = _resolve_symlink_archive_path(current, member.linkname)
            continue
        if member.isfile():
            return member
        raise ManagedSetupError(f"{label}: symlink {name!r} target {current!r} is not a regular file.")
    raise ManagedSetupError(f"{label}: symlink {name!r} target chain is too long.")


def safe_extract_tar_gz(
    archive_path: Path,
    dest_dir: Path,
    *,
    executable_path: str,
    max_entries: int,
    max_extracted_bytes: int,
) -> Path:
    """Extract archive_path into dest_dir, rejecting anything unsafe.

    Rejects absolute paths, ../ traversal, backslashes, device files, hard
    links, duplicate archive paths, and archives that exceed the configured
    entry-count or extracted-byte bounds.

    A tar symlink is allowed only when its target is a relative, in-archive
    path (no leading '/', no '../', no backslashes, no absolute drive form)
    that resolves - relative to the symlink's own containing directory,
    possibly through further internal symlinks - to a regular file in this
    same archive. No symlink is ever written to disk: the resolved target's
    bytes are copied to the symlink's own path instead. A missing target, a
    target that is not eventually a regular file, or a symlink cycle is
    rejected.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    resolved_dest = dest_dir.resolve()
    seen_paths: set[str] = set()
    entry_count = 0
    entries: list[tarfile.TarInfo] = []
    members_by_name: dict[str, tarfile.TarInfo] = {}
    label = "Runtime archive"

    with tarfile.open(archive_path, "r:gz") as archive:
        while True:
            member = archive.next()
            if member is None:
                break
            entry_count += 1
            if entry_count > max_entries:
                raise ManagedSetupError(f"Archive has more than {max_entries} entries; refusing to extract it.")
            name = member.name
            _validate_archive_member_name(name, label)
            if name in seen_paths:
                raise ManagedSetupError(f"{label} contains a duplicate path: {name!r}.")
            seen_paths.add(name)
            if member.islnk():
                raise ManagedSetupError(f"{label} contains a hard link: {name!r}.")
            if member.isdev() or member.ischr() or member.isblk() or member.isfifo():
                raise ManagedSetupError(f"{label} contains a device file: {name!r}.")
            if not (member.isfile() or member.isdir() or member.issym()):
                raise ManagedSetupError(f"{label} contains an unsupported entry type: {name!r}.")
            entries.append(member)
            members_by_name[name] = member

        total_bytes = 0
        executable_found = False
        for member in entries:
            name = member.name
            target = (dest_dir / name).resolve()
            if target != resolved_dest and resolved_dest not in target.parents:
                raise ManagedSetupError(f"{label} path escapes the destination folder: {name!r}.")

            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue

            source_member = _resolve_symlink_source(name, members_by_name, label) if member.issym() else member

            total_bytes += source_member.size
            if total_bytes > max_extracted_bytes:
                raise ManagedSetupError(f"{label} exceeds its extracted-size limit; refusing to extract it.")
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(source_member)
            if source is None:
                raise ManagedSetupError(f"Could not read {label.lower()} member: {source_member.name!r}.")
            with source, target.open("wb") as destination_file:
                shutil.copyfileobj(source, destination_file)
            if source_member.mode & 0o111:
                target.chmod(0o755)
            if name == executable_path:
                executable_found = True
                target.chmod(0o755)

    if not executable_found:
        raise ManagedSetupError(f"Runtime archive did not contain the expected executable: {executable_path!r}.")
    return dest_dir / executable_path


# --------------------------------------------------------------------------
# Install manifest
# --------------------------------------------------------------------------


def manifest_path(storage_dir: Path, recipe_id: str) -> Path:
    return managed_root(storage_dir) / "installed" / f"{recipe_id}.json"


def read_manifest(storage_dir: Path, recipe_id: str) -> dict[str, Any] | None:
    path = manifest_path(storage_dir, recipe_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_manifest(storage_dir: Path, recipe_id: str, manifest: dict[str, Any]) -> None:
    path = manifest_path(storage_dir, recipe_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def verify_installed(root: Path, recipe: dict[str, Any] | str, storage_dir: Path) -> dict[str, Any]:
    """Re-verify installed model and runtime files against the manifest and current recipe.

    Raises ManagedSetupError with a specific reason when anything does not
    match, so a launcher never starts a server against tampered or
    corrupted files.
    """
    if isinstance(recipe, str):
        recipe = get_recipe(root, recipe)
    recipe_id = recipe["id"]
    manifest = read_manifest(storage_dir, recipe_id)
    if manifest is None:
        raise ManagedSetupError(
            f"Recipe {recipe_id!r} is not installed under {storage_dir}. Run "
            f"'omobench prepare --recipe {recipe_id} --storage-dir {storage_dir} --download' first."
        )
    if manifest.get("model", {}).get("sha256") != recipe["model"]["sha256"]:
        raise ManagedSetupError(
            f"The installed model for {recipe_id!r} was verified against a different recipe revision. "
            "Re-run --download to install the current reviewed recipe."
        )
    if manifest.get("runtime", {}).get("archive_sha256") != recipe["runtime"]["sha256"]:
        raise ManagedSetupError(
            f"The installed runtime for {recipe_id!r} was verified against a different recipe revision. "
            "Re-run --download to install the current reviewed recipe."
        )

    model_path = Path(manifest["model"]["path"])
    executable_path = Path(manifest["runtime"]["executable_path"])
    for path, label in [(model_path, "model"), (executable_path, "runtime executable")]:
        if not path.is_file():
            raise ManagedSetupError(f"The installed {label} file is missing: {path}.")

    model_size = model_path.stat().st_size
    if model_size != manifest["model"]["size_bytes"]:
        raise ManagedSetupError("The installed model file size no longer matches the manifest; re-run --download.")
    model_sha256 = sha256_of_file(model_path)
    if model_sha256 != manifest["model"]["sha256"]:
        raise ManagedSetupError("The installed model file failed re-verification; re-run --download.")

    executable_sha256 = sha256_of_file(executable_path)
    if executable_sha256 != manifest["runtime"]["executable_sha256"]:
        raise ManagedSetupError("The installed runtime executable failed re-verification; re-run --download.")
    if os.name != "nt" and not os.access(executable_path, os.X_OK):
        raise ManagedSetupError("The installed runtime executable lost its execute permission; re-run --download.")

    return {
        "model_path": model_path,
        "executable_path": executable_path,
        "model_id": recipe["model_id"],
        "benchmark_profile": recipe["benchmark_profile"],
    }


# --------------------------------------------------------------------------
# Download and install
# --------------------------------------------------------------------------


def download_and_install(
    root: Path,
    recipe_id: str,
    storage_dir: Path,
    *,
    progress: ProgressCallback | None = None,
    cancel_event: Any = None,
) -> dict[str, Any]:
    """Perform the reviewed, verified download and atomic installation.

    Never runs an installer or alters system packages: this only downloads
    two verified files and extracts one archive under
    <storage_dir>/omobench-managed-v1/.
    """
    platform_status = check_platform()
    if not platform_status.ok:
        raise ManagedSetupError(platform_status.message)
    recipe = get_recipe(root, recipe_id)

    with storage_lock(storage_dir):
        managed = managed_root(storage_dir)
        downloads = managed / "downloads"
        downloads.mkdir(parents=True, exist_ok=True)

        model_final = downloads / recipe["model"]["filename"]
        model_part = downloads / (recipe["model"]["filename"] + ".part")
        resumable_download(
            recipe["model"]["url"],
            part_path=model_part,
            final_path=model_final,
            expected_size=recipe["model"]["size_bytes"],
            expected_sha256=recipe["model"]["sha256"],
            managed=managed,
            label="model",
            progress=progress,
            cancel_event=cancel_event,
        )

        runtime_final = downloads / recipe["runtime"]["filename"]
        runtime_part = downloads / (recipe["runtime"]["filename"] + ".part")
        resumable_download(
            recipe["runtime"]["url"],
            part_path=runtime_part,
            final_path=runtime_final,
            expected_size=recipe["runtime"]["size_bytes"],
            expected_sha256=recipe["runtime"]["sha256"],
            managed=managed,
            label="runtime",
            progress=progress,
            cancel_event=cancel_event,
        )

        runtime_extract_root = managed / "runtimes"
        staging_dir = runtime_extract_root / f".{recipe_id}.staging"
        final_dir = runtime_extract_root / recipe_id
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        safe_extract_tar_gz(
            runtime_final,
            staging_dir,
            executable_path=recipe["runtime"]["executable_path"],
            max_entries=recipe["runtime"]["max_entries"],
            max_extracted_bytes=recipe["runtime"]["max_extracted_bytes"],
        )
        if final_dir.exists():
            shutil.rmtree(final_dir)
        os.replace(staging_dir, final_dir)  # atomic: the runtime only ever appears fully extracted
        executable_path = final_dir / recipe["runtime"]["executable_path"]

        manifest = {
            "recipe_id": recipe_id,
            "model_id": recipe["model_id"],
            "installed_at": datetime.now(UTC).isoformat(),
            "model": {
                "path": str(model_final.resolve()),
                "size_bytes": recipe["model"]["size_bytes"],
                "sha256": recipe["model"]["sha256"],
            },
            "runtime": {
                "archive_sha256": recipe["runtime"]["sha256"],
                "executable_path": str(executable_path.resolve()),
                "executable_sha256": sha256_of_file(executable_path),
            },
        }
        write_manifest(storage_dir, recipe_id, manifest)
        if progress:
            progress("install", 1, 1)
        return manifest
