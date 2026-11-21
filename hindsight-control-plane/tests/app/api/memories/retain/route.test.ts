import type { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { retainBatch } = vi.hoisted(() => ({
  retainBatch: vi.fn(),
}));

vi.mock("@/lib/hindsight-client", () => ({
  hindsightClient: { retainBatch },
}));

import { POST } from "@/app/api/memories/retain/route";

function retain(body: string) {
  return POST(
    new Request("http://localhost/api/memories/retain", {
      method: "POST",
      body,
    }) as unknown as NextRequest
  );
}

describe("POST /api/memories/retain", () => {
  beforeEach(() => {
    retainBatch.mockReset();
  });

  it("maps request-level observation_scopes into the items it retains", async () => {
    retainBatch.mockResolvedValue({ success: true });

    const response = await retain(
      JSON.stringify({
        bank_id: "b",
        items: [{ content: "one" }, { content: "two", observation_scopes: "own" }],
        observation_scopes: "shared",
      })
    );

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({ success: true });
    expect(retainBatch).toHaveBeenCalledWith(
      "b",
      [
        { content: "one", observation_scopes: "shared" },
        { content: "two", observation_scopes: "own" },
      ],
      { documentId: undefined, documentTags: undefined }
    );
  });

  it("relays the status of an error the client raises", async () => {
    retainBatch.mockRejectedValue(
      Object.assign(new Error("payload too large"), { statusCode: 413 })
    );

    const response = await retain(JSON.stringify({ bank_id: "b", items: [] }));

    expect(response.status).toBe(413);
  });

  it("answers 500 for a client error without a status", async () => {
    retainBatch.mockRejectedValue(new Error("socket hang up"));

    const response = await retain(JSON.stringify({ bank_id: "b", items: [] }));

    expect(response.status).toBe(500);
  });

  it("answers 500 for a body that is not JSON, without calling the client", async () => {
    const response = await retain("{not-json");

    expect(response.status).toBe(500);
    expect(retainBatch).not.toHaveBeenCalled();
  });

  it("refuses a dot-segment bank id before calling the client", async () => {
    const response = await retain(JSON.stringify({ bank_id: "..", items: [] }));

    expect(response.status).toBe(400);
    expect(retainBatch).not.toHaveBeenCalled();
  });
});
