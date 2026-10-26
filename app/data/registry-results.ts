import { readResultRecord, type ResultRecord } from "@/app/lib/result-record";

export type RegistryResult = ResultRecord;

// Vite expands this at build time. Only normalized result.json files under
// results/ are included; raw logs and other evidence remain out of the browser bundle.
const localResultModules = import.meta.glob("../../results/**/result.json", {
  eager: true,
  import: "default",
}) as Record<string, unknown>;

const loadedResults = Object.entries(localResultModules).map(([sourcePath, value]) =>
  readResultRecord(sourcePath, value),
);

const uniqueRunIds = new Set<string>();
for (const result of loadedResults) {
  if (uniqueRunIds.has(result.id)) {
    throw new Error(`Local results contain duplicate run_id: ${result.id}.`);
  }
  uniqueRunIds.add(result.id);
}

export const registryResults = loadedResults.sort((left, right) =>
  right.startedAt.localeCompare(left.startedAt),
);

export function getRegistryResult(runId: string): RegistryResult | undefined {
  return registryResults.find((result) => result.id === runId);
}
