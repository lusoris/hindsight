import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DataplaneProxyOptions } from "@/lib/dataplane-proxy";

const { proxyDataplane } = vi.hoisted(() => ({
  proxyDataplane: vi.fn<(request: Request, options: DataplaneProxyOptions) => Promise<Response>>(),
}));

vi.mock("@/lib/dataplane-proxy", () => ({ proxyDataplane }));

import { GET } from "@/app/api/banks/[bankId]/mental-models/[mentalModelId]/history/route";

describe("mental-models/[mentalModelId]/history route", () => {
  beforeEach(() => {
    proxyDataplane.mockReset();
    proxyDataplane.mockResolvedValue(new Response(null, { status: 200 }));
  });

  it("GET hands proxyDataplane its options", async () => {
    const request = new Request("http://localhost/api/test", { method: "GET" });
    const params = { bankId: "bank 1", mentalModelId: "mm/1" };

    await GET(request, { params: Promise.resolve(params) });

    expect(proxyDataplane).toHaveBeenCalledTimes(1);
    const [forwardedRequest, options] = proxyDataplane.mock.calls[0];
    expect(forwardedRequest).toBe(request);
    expect(options).toEqual({
      required: ["bank 1", "mm/1"],
      missing: {
        error: "bank_id and mental_model_id are required",
        errorKey: "api.errors.validation.bankAndMentalModelIdRequired",
      },
      bankId: "bank 1",
      suffix: "/mental-models/mm%2F1/history",
      method: "GET",
      failure: {
        error: "Failed to fetch mental model history",
        errorKey: "api.errors.mentalModels.history",
      },
      action: "fetching mental model history",
      notFound: {
        error: "Mental model not found",
        errorKey: "api.errors.mentalModels.notFound",
      },
      nonOkAsServerError: true,
    });
  });
});
