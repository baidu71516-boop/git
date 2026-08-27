import { mergeConfig, defineConfig } from "vitest/config";

import baseConfig from "./vitest.config";
import ReleaseSequencer from "./tests/release-sequencer";

// Timeout fields are intentionally absent: this gate preserves Vitest defaults and
// existing explicit per-test values rather than replacing either with runner policy.
export default mergeConfig(
  baseConfig,
  defineConfig({
    test: {
      pool: "forks",
      maxWorkers: 1,
      fileParallelism: false,
      isolate: true,
      retry: 0,
      allowOnly: false,
      sequence: {
        shuffle: false,
        concurrent: false,
        sequencer: ReleaseSequencer,
      },
      reporters: ["default", "./scripts/release-vitest-reporter.mjs"],
    },
  }),
);
