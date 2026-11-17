import type { MetadataRoute } from "next";
import { registryResults } from "./data/registry-results";
import { SITE_URL } from "./lib/site";

// Published results only. Illustrative examples are not listed.
export default function sitemap(): MetadataRoute.Sitemap {
  return [
    { url: `${SITE_URL}/` },
    { url: `${SITE_URL}/run` },
    { url: `${SITE_URL}/compare` },
    ...registryResults.map(result => ({ url: `${SITE_URL}/runs/${encodeURIComponent(result.id)}` })),
  ];
}
