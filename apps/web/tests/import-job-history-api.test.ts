import { getImportJob, listImportJobs } from "@/features/imports/api";

const { apiRequestMock } = vi.hoisted(() => ({
  apiRequestMock: vi.fn(),
}));

vi.mock("@/lib/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/client")>();
  return { ...actual, apiRequest: apiRequestMock };
});

function success(data: unknown) {
  return {
    success: true,
    data,
    error: null,
    request_id: "request-1",
  };
}

beforeEach(() => {
  apiRequestMock.mockReset();
});

describe("Import Job history API contract", () => {
  it("uses the exact GET list query with fixed caller-provided pagination", async () => {
    apiRequestMock.mockResolvedValue(
      success({ items: [], total: 0, offset: 100, limit: 50 }),
    );

    await listImportJobs(100, 50);

    expect(apiRequestMock).toHaveBeenCalledExactlyOnceWith(
      "/import-jobs?offset=100&limit=50",
    );
  });

  it("uses the read-only detail GET path with an encoded Job id", async () => {
    apiRequestMock.mockResolvedValue(success({ id: "job/id" }));

    await getImportJob("job/id");

    expect(apiRequestMock).toHaveBeenCalledExactlyOnceWith(
      "/import-jobs/job%2Fid",
    );
  });
});
