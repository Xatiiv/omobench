/**
 * The published Windows installer, if there is one.
 *
 * `null` means no release has been published yet. The page renders an honest
 * "not available yet" state in that case rather than a button that leads to a
 * 404, a broken download is worse than a clear absence, because the person
 * cannot tell whether the fault is theirs.
 *
 * When a verified release is published, fill this in from the
 * `version.json` attached to that release. The checksum lets a person compare
 * their download with the published asset. It does not replace code signing.
 */
export type WindowsRelease = {
  /** Product version, e.g. "0.1.0". */
  version: string;
  /** The immutable source revision the installer and its payload were built from. */
  revision: string;
  /** Direct download URL of OmoBenchSetup.exe on the GitHub release. */
  downloadUrl: string;
  /** The release page, for checksums and notes. */
  releaseUrl: string;
  /** Lowercase hex SHA-256 of OmoBenchSetup.exe. */
  sha256: string;
  /** Whether the executable carries an Authenticode signature. */
  signed: boolean;
};

export const windowsRelease: WindowsRelease | null = {
  version: "0.1.3",
  revision: "7d4a74f1278a84b6231423d970ac991f3f386496",
  downloadUrl: "https://github.com/Xatiiv/omobench/releases/download/windows-v0.1.3/OmoBenchSetup.exe",
  releaseUrl: "https://github.com/Xatiiv/omobench/releases/tag/windows-v0.1.3",
  sha256: "a567c6a585404d7460c88eeb914bafd2a6c845de16f2974bbdeb702fa3d66ab3",
  signed: false,
};

export const windowsCompanionGuideUrl =
  "https://github.com/Xatiiv/omobench/blob/main/docs/windows-installer.md";

export const windowsReleasesUrl = "https://github.com/Xatiiv/omobench/releases";
