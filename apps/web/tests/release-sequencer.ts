import path from "node:path";

import { BaseSequencer } from "vitest/node";

export const INTERACTION_TEST_PATHS = [
  "apps/web/tests/candidate-pool-detail-tabs.test.tsx",
  "apps/web/tests/candidate-selection.test.ts",
  "apps/web/tests/candidate-run-detail-selection.test.tsx",
  "apps/web/tests/bulk-import-workspace.test.tsx",
  "apps/web/tests/bulk-preview-workflow.test.tsx",
  "apps/web/tests/data-collection-workspace.test.tsx",
] as const;

function normalizedPath(moduleId: string): string {
  const repositoryRoot = process.env.WEB_RELEASE_REPOSITORY_ROOT;
  if (!repositoryRoot) {
    throw new Error(
      "WEB_RELEASE_REPOSITORY_ROOT is required by the release sequencer",
    );
  }

  return path.relative(repositoryRoot, moduleId).replaceAll("\\", "/");
}

export function lexicalReleaseOrder(paths: readonly string[]): string[] {
  return [...paths].sort((left, right) =>
    left < right ? -1 : left > right ? 1 : 0,
  );
}

export default class ReleaseSequencer extends BaseSequencer {
  override async sort(files: Parameters<BaseSequencer["sort"]>[0]) {
    const mode = process.env.WEB_RELEASE_SEQUENCE_MODE ?? "full";
    const requestedOrder =
      mode === "interaction"
        ? [...INTERACTION_TEST_PATHS]
        : process.env.WEB_RELEASE_CONTROLLED_ORDER
          ? (JSON.parse(process.env.WEB_RELEASE_CONTROLLED_ORDER) as string[])
          : undefined;

    if (requestedOrder) {
      const positions = new Map(
        requestedOrder.map((testPath, index) => [testPath, index]),
      );
      const unexpected = files
        .map((file) => normalizedPath(file.moduleId))
        .filter((testPath) => !positions.has(testPath));
      if (unexpected.length > 0) {
        throw new Error(
          `Release sequencer received unexpected files: ${unexpected.join(", ")}`,
        );
      }

      return [...files].sort((left, right) => {
        const leftPath = normalizedPath(left.moduleId);
        const rightPath = normalizedPath(right.moduleId);
        const leftPosition = positions.get(leftPath);
        const rightPosition = positions.get(rightPath);
        if (leftPosition === undefined || rightPosition === undefined) {
          throw new Error(
            "Release sequencer could not order an unrecognized file",
          );
        }
        return leftPosition - rightPosition;
      });
    }

    return [...files].sort((left, right) => {
      const leftPath = normalizedPath(left.moduleId);
      const rightPath = normalizedPath(right.moduleId);
      return leftPath < rightPath ? -1 : leftPath > rightPath ? 1 : 0;
    });
  }
}
