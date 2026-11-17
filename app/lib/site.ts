// The public address of the website, and the guide it points people to.
//
// SITE_URL is the canonical production address. Preview deployments keep
// working at their own addresses; canonical and Open Graph metadata simply
// name this one as the page's home.
export const SITE_URL = "https://www.omobench.org";

export const SITE_NAME = "OmoBench";

export const SITE_DESCRIPTION =
  "Measure how fast open-weight language models run on your own hardware, with published evidence anyone can inspect.";

export const REPOSITORY_URL = "https://github.com/Xatiiv/omobench";

/** The step-by-step guide for running a first benchmark. */
export const USER_GUIDE_URL = `${REPOSITORY_URL}/blob/main/docs/user-guide.md`;

/** A section of the user guide, by its heading anchor. */
export function userGuideSection(anchor: string): string {
  return `${USER_GUIDE_URL}#${anchor}`;
}
