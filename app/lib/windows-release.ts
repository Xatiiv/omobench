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
  version: "0.1.0",
  revision: "13a3842478e4c37ac4c459e0c10c8252408d4b32",
  downloadUrl: "https://github.com/Xatiiv/omobench/releases/download/windows-v0.1.0/OmoBenchSetup.exe",
  releaseUrl: "https://github.com/Xatiiv/omobench/releases/tag/windows-v0.1.0",
  sha256: "afa11a567165b3aec76ac44b4385b5d05b6b8e0d5d2974f3ce8c4cbf0222ca27",
  signed: false,
};

export const windowsCompanionGuideUrl =
  "https://github.com/Xatiiv/omobench/blob/main/docs/windows-installer.md";

export const windowsReleasesUrl = "https://github.com/Xatiiv/omobench/releases";
