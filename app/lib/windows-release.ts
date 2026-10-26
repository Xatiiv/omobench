/**
 * The published Windows installer, if there is one.
 *
 * `null` means no release has been published yet. The page renders an honest
 * "not available yet" state in that case rather than a button that leads to a
 * 404 — a broken download is worse than a clear absence, because the person
 * cannot tell whether the fault is theirs.
 *
 * When the release workflow publishes a build, fill this in from the
 * `version.json` attached to that release. The checksum matters: the installer
 * is not code-signed, so comparing it is the only way a person can confirm
 * they have the file this project built.
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

export const windowsRelease: WindowsRelease | null = null;

export const windowsCompanionGuideUrl =
  "https://github.com/Xatiiv/omobench/blob/main/docs/windows-installer.md";

export const windowsReleasesUrl = "https://github.com/Xatiiv/omobench/releases";
