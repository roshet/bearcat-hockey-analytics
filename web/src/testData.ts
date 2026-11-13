import { it } from "vitest";

/* Recorded pages built from ACHA data (`bca scouting build`). They are kept out of the
   public copy of this project, so tests that need them skip, with the reason, when the
   files are absent. */

const files = import.meta.glob<unknown>("./__fixtures__/*.json", { eager: true, import: "default" });

/** A recorded page by file name (without .json); undefined when it isn't here. */
export function recorded<T>(name: string): T {
  return files[`./__fixtures__/${name}.json`] as T;
}

/** Define the tests that read recorded pages, or one skipped test saying why not. */
export function withRecordedData(body: () => void): void {
  if (Object.keys(files).length > 0) body();
  else it.skip("needs recorded ACHA data, which is not redistributed", () => {});
}
