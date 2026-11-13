"""Find the model file a local server is serving, and read what it says about itself.

LM Studio keeps its models under one folder, as `<publisher>/<repository>/<file>.gguf`.
Given the identifier the server answers to and, where it reports one, the
quantisation, this finds the file (or the full set of shards) that identifier
most plausibly refers to -- and says when more than one set fits, rather than
choosing.

It also reads a GGUF file's own header. A GGUF records which model it was
converted from (`general.base_model.*`, `general.source.*`), and that is
metadata the file carries about itself, not a guess from a name. That is where
a tokenizer comes from when the served identifier is a bare alias.

Nothing here hashes a file: fingerprinting reads every byte and belongs at
packaging time, outside the measured run. Finding a file reads directory
entries; reading its header reads at most a few megabytes from its start.
"""

from __future__ import annotations

import json
import os
import re
import struct
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

# --- paths --------------------------------------------------------------------


def to_local_path(value: str) -> Path:
    """A path the panel can open, from what a person typed.

    The panel runs in WSL; a Windows path such as `D:\\OmoBench\\lmstudio-models`
    is the same folder at `/mnt/d/OmoBench/lmstudio-models`. A POSIX path is
    taken as given. Nothing else is guessed: a UNC path or a relative one is
    refused rather than resolved against somewhere unexpected.
    """
    text = (value or "").strip().strip('"')
    if not text:
        raise ValueError("Enter the folder your models are in.")
    if re.match(r"^[A-Za-z]:[\\/]", text):
        windows = PureWindowsPath(text)
        drive = windows.drive[0].lower()
        return Path("/mnt", drive, *windows.parts[1:])
    if text.startswith("\\\\"):
        raise ValueError("A network share path cannot be read here; use a local drive.")
    posix = PurePosixPath(text)
    if not posix.is_absolute():
        raise ValueError("Enter the full path to the folder, for example D:\\OmoBench\\lmstudio-models.")
    return Path(text)


# --- GGUF header ----------------------------------------------------------------

_GGUF_MAGIC = b"GGUF"
_SCALARS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?",
            10: "<Q", 11: "<q", 12: "<d"}
_STRING, _ARRAY = 8, 9
# Keys worth reading. The tokenizer arrays that dominate a header are skipped,
# never decoded into memory.
_WANTED_PREFIXES = ("general.",)
_MAX_HEADER_BYTES = 64 * 1024 * 1024


class GGUFError(ValueError):
    pass


class _Reader:
    def __init__(self, handle):
        self.handle = handle
        self.read_bytes = 0

    def take(self, size: int) -> bytes:
        self.read_bytes += size
        if self.read_bytes > _MAX_HEADER_BYTES:
            raise GGUFError("The GGUF header is larger than this reader will read.")
        data = self.handle.read(size)
        if len(data) != size:
            raise GGUFError("The GGUF file ended inside its header.")
        return data

    def skip(self, size: int) -> None:
        self.read_bytes += size
        if self.read_bytes > _MAX_HEADER_BYTES:
            raise GGUFError("The GGUF header is larger than this reader will read.")
        self.handle.seek(size, os.SEEK_CUR)

    def scalar(self, kind: int):
        fmt = _SCALARS[kind]
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]

    def string(self) -> str:
        length = self.scalar(10)
        if length > 1024 * 1024:
            raise GGUFError("A GGUF string is implausibly long.")
        return self.take(length).decode("utf-8", errors="replace")

    def skip_value(self, kind: int) -> None:
        if kind in _SCALARS:
            self.skip(struct.calcsize(_SCALARS[kind]))
        elif kind == _STRING:
            self.skip(self.scalar(10))
        elif kind == _ARRAY:
            inner, count = self.scalar(4), self.scalar(10)
            if inner in _SCALARS:
                self.skip(struct.calcsize(_SCALARS[inner]) * count)
            else:
                for _ in range(count):
                    self.skip_value(inner)
        else:
            raise GGUFError(f"Unknown GGUF value type {kind}.")

    def value(self, kind: int):
        if kind in _SCALARS:
            return self.scalar(kind)
        if kind == _STRING:
            return self.string()
        if kind == _ARRAY:
            inner, count = self.scalar(4), self.scalar(10)
            if inner == _STRING and count <= 16:
                return [self.string() for _ in range(count)]
            # A long array is a vocabulary or similar, and not wanted here.
            if inner in _SCALARS:
                self.skip(struct.calcsize(_SCALARS[inner]) * count)
            else:
                for _ in range(count):
                    self.skip_value(inner)
            return None
        raise GGUFError(f"Unknown GGUF value type {kind}.")


def read_gguf_metadata(path: Path) -> dict[str, Any]:
    """The `general.*` keys of a GGUF file's header, and nothing else."""
    with open(path, "rb") as handle:
        reader = _Reader(handle)
        if reader.take(4) != _GGUF_MAGIC:
            raise GGUFError(f"{Path(path).name} is not a GGUF file.")
        version = reader.scalar(4)
        if version not in (2, 3):
            raise GGUFError(f"GGUF version {version} is not one this reader knows.")
        reader.scalar(10)  # tensor count
        count = reader.scalar(10)
        if count > 100000:
            raise GGUFError("The GGUF header claims an implausible number of keys.")
        found: dict[str, Any] = {}
        for _ in range(count):
            key = reader.string()
            kind = reader.scalar(4)
            if key.startswith(_WANTED_PREFIXES):
                found[key] = reader.value(kind)
            else:
                reader.skip_value(kind)
        return found


_HF_URL = re.compile(r"^https?://huggingface\.co/([A-Za-z0-9][\w.-]*/[\w.-]+?)/?$")
_REPO_ID = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")


def tokenizer_sources_from_gguf(meta: dict[str, Any]) -> list[dict[str, str]]:
    """Repositories the file says it came from, best first.

    A converted model names its origin: `general.base_model.N.*` for what it
    was derived from, `general.source.*` for where it was converted from. Those
    are where its tokenizer lives. A GGUF that is itself a fine-tune of a base
    still names the base's tokenizer correctly in nearly every case, but the
    basis is recorded so the person can see which key it came from.
    """
    candidates: list[dict[str, str]] = []

    def add(repo: str | None, basis: str) -> None:
        if not repo:
            return
        repo = repo.strip()
        match = _HF_URL.match(repo)
        if match:
            repo = match.group(1)
        if not _REPO_ID.match(repo) or repo.lower().endswith("-gguf"):
            return
        if any(existing["source"].lower() == repo.lower() for existing in candidates):
            return
        candidates.append({"source": repo, "basis": basis})

    add(meta.get("general.source.huggingface.repository"), "gguf:general.source.huggingface.repository")
    add(meta.get("general.source.url"), "gguf:general.source.url")
    try:
        bases = int(meta.get("general.base_model.count") or 0)
    except (TypeError, ValueError):
        bases = 0
    for index in range(min(bases, 4)):
        prefix = f"general.base_model.{index}."
        add(meta.get(prefix + "repo_url"), f"gguf:{prefix}repo_url")
        organization, name = meta.get(prefix + "organization"), meta.get(prefix + "name")
        if organization and name:
            add(f"{organization}/{name}".replace(" ", "-"), f"gguf:{prefix}organization+name")
    return candidates


# --- finding the served model's files ------------------------------------------

_QUANT = re.compile(
    r"(?:^|[-_.])((?:IQ\d_[A-Z0-9]+(?:_[A-Z]+)?)|(?:Q\d_K(?:_[SMLX]+)?)|(?:Q\d_\d)|(?:Q\d)|"
    r"(?:BF16)|(?:F16)|(?:F32)|(?:MXFP4))(?=$|[-_.])",
    re.IGNORECASE)
_SHARD = re.compile(r"^(?P<stem>.+)-(?P<index>\d{5})-of-(?P<total>\d{5})\.gguf$", re.IGNORECASE)
_MAX_FILES_SCANNED = 20000


def quantization_of(name: str) -> str | None:
    match = _QUANT.search(Path(name).stem)
    return match.group(1).upper() if match else None


def _normal(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _core(text: str) -> str:
    """A model name with the parts that are not its name removed."""
    name = Path(text).name
    shard = _SHARD.match(name)
    if shard:
        name = shard.group("stem")
    stem = re.sub(r"\.gguf$", "", name, flags=re.IGNORECASE)
    stem = re.sub(r"[-_.]gguf$", "", stem, flags=re.IGNORECASE)
    quant = _QUANT.search(stem)
    if quant:
        stem = stem[:quant.start(1)].rstrip("-_.")
    return _normal(stem)


def served_core(served_model_id: str) -> str:
    """`qwen/qwen3-vl-4b:3` and `qwen2.5-coder-14b-instruct` reduced to a name."""
    text = (served_model_id or "").strip()
    text = re.sub(r":\d+$", "", text)
    text = text.split("/")[-1]
    return _core(text)


def _shard_group(path: Path) -> list[Path] | None:
    """Every shard of a split model, or None when `path` is not a shard."""
    match = _SHARD.match(path.name)
    if not match:
        return None
    total = int(match.group("total"))
    group = [path.with_name(f"{match.group('stem')}-{index:05d}-of-{total:05d}.gguf")
             for index in range(1, total + 1)]
    return group


def find_model_files(root: Path, served_model_id: str, *,
                     quantization: str | None = None) -> dict[str, Any]:
    """The weight-file set(s) under `root` that `served_model_id` names.

    Returns `{"state": "found" | "ambiguous" | "none", "groups": [...]}`. A
    group is one model's files -- one GGUF, or every shard of a split one. Only
    a group whose name matches the identifier is considered, and when the
    server reported a quantisation only groups of that quantisation count.
    Several groups left is "ambiguous": the person chooses.
    """
    root = Path(root)
    if not root.is_dir():
        raise ValueError(f"{root} is not a folder this panel can read.")
    want = served_core(served_model_id)
    if not want:
        return {"state": "none", "groups": []}
    groups: dict[tuple[str, ...], dict[str, Any]] = {}
    scanned = 0
    for directory, subdirectories, files in os.walk(root):
        depth = len(Path(directory).relative_to(root).parts)
        if depth >= 4:
            subdirectories[:] = []
        subdirectories[:] = [name for name in subdirectories if not name.startswith(".")]
        for name in files:
            scanned += 1
            if scanned > _MAX_FILES_SCANNED:
                break
            if not name.lower().endswith(".gguf") or name.lower().startswith("mmproj"):
                continue
            path = Path(directory) / name
            names = [_core(name)] + [_core(part) for part in Path(directory).relative_to(root).parts[-1:]]
            if want not in names:
                continue
            shards = _shard_group(path)
            members = shards if shards else [path]
            key = tuple(str(member) for member in members)
            if key in groups:
                continue
            missing = [member for member in members if not member.is_file()]
            groups[key] = {
                "files": [member.relative_to(root).as_posix() for member in members],
                "quantization": quantization_of(name),
                "complete": not missing,
                "missing": [member.name for member in missing],
                "bytes": sum(member.stat().st_size for member in members if member.is_file()),
                "folder": Path(directory).relative_to(root).as_posix(),
            }
    found = list(groups.values())
    if quantization:
        matching = [group for group in found
                    if (group["quantization"] or "").upper() == quantization.upper()]
        found = matching
    found.sort(key=lambda group: group["files"][0])
    state = "found" if len(found) == 1 else ("ambiguous" if found else "none")
    return {"state": state, "groups": found}


# --- file identity, and a fingerprint cache that knows when it is stale --------


def file_identity(paths: list[Path]) -> list[dict[str, Any]]:
    """What can be read about the files without reading their contents."""
    identity = []
    for path in paths:
        status = Path(path).stat()
        identity.append({"path": str(path), "bytes": status.st_size, "modified_ns": status.st_mtime_ns})
    return identity


def identity_unchanged(recorded: list[dict[str, Any]]) -> tuple[bool, str]:
    """Whether the files recorded earlier are still the same files."""
    for entry in recorded or []:
        path = Path(entry["path"])
        if not path.is_file():
            return False, f"{path.name} is no longer there."
        status = path.stat()
        if status.st_size != entry.get("bytes") or status.st_mtime_ns != entry.get("modified_ns"):
            return False, f"{path.name} has changed since it was recorded."
    return bool(recorded), "" if recorded else "No files were recorded."


class FingerprintCache:
    """Fingerprints of weight files, valid only while the files are unchanged."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _key(paths: list[Path]) -> str:
        return "\n".join(sorted(str(Path(path)) for path in paths))

    def get(self, paths: list[Path]) -> dict[str, Any] | None:
        entry = self._load().get(self._key(paths))
        if not isinstance(entry, dict):
            return None
        unchanged, _ = identity_unchanged(entry.get("identity") or [])
        return entry if unchanged else None

    def put(self, paths: list[Path], manifest: dict[str, Any], revision: str) -> None:
        data = self._load()
        data[self._key(paths)] = {"identity": file_identity(paths), "manifest": manifest,
                                  "revision": revision}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)
