import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const SENTINEL_RESULT = { data: { sentinel: "result" } };

const { testBankLlm, dryRunExtractMemories, previewPrompt, lowLevelClient } = vi.hoisted(() => ({
  testBankLlm: vi.fn(),
  dryRunExtractMemories: vi.fn(),
  previewPrompt: vi.fn(),
  lowLevelClient: { sentinel: "client" },
}));

vi.mock("@/lib/hindsight-client", () => ({
  sdk: { testBankLlm, dryRunExtractMemories, previewPrompt },
  lowLevelClient,
}));

const { respondWithSdk } = vi.hoisted(() => ({
  respondWithSdk: vi.fn(() => new Response(null, { status: 200 })),
}));

vi.mock("@/lib/sdk-response", () => ({
  respondWithSdk,
}));

import { POST as testBankLlmRoute } from "@/app/api/banks/[bankId]/health/llm/route";
import { POST as dryRunExtractRoute } from "@/app/api/banks/[bankId]/memories/dry-run-extract/route";
import { POST as previewPromptRoute } from "@/app/api/banks/[bankId]/prompts/preview/route";

type RouteCase = {
  name: string;
  handler: (
    request: NextRequest,
    context: { params: Promise<{ bankId: string }> }
  ) => Promise<Response>;
  sdkMethod: ReturnType<typeof vi.fn>;
  label: string;
  body?: Record<string, unknown>;
};

const ROUTES: RouteCase[] = [
  {
    name: "POST health/llm",
    handler: testBankLlmRoute,
    sdkMethod: testBankLlm,
    label: "Failed to test bank LLM connectivity",
  },
  {
    name: "POST memories/dry-run-extract",
    handler: dryRunExtractRoute,
    sdkMethod: dryRunExtractMemories,
    label: "Failed to run dry-run extraction",
    body: { text: "sample text to extract" },
  },
  {
    name: "POST prompts/preview",
    handler: previewPromptRoute,
    sdkMethod: previewPrompt,
    label: "Failed to preview prompt",
    body: { mission: "custom prompt mission" },
  },
];

describe("bank-scoped SDK POST routes", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    testBankLlm.mockResolvedValue(SENTINEL_RESULT);
    dryRunExtractMemories.mockResolvedValue(SENTINEL_RESULT);
    previewPrompt.mockResolvedValue(SENTINEL_RESULT);
    respondWithSdk.mockReturnValue(new Response(null, { status: 200 }));
  });

  it.each(ROUTES)(
    "$name calls its SDK method and respondWithSdk with expected arguments",
    async ({ handler, sdkMethod, label, body }) => {
      const request = new NextRequest("http://localhost/api/banks/bank%201/test", {
        method: "POST",
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      const context = { params: Promise.resolve({ bankId: "bank 1" }) };

      const response = await handler(request, context);

      expect(response.status).toBe(200);
      expect(sdkMethod).toHaveBeenCalledTimes(1);
      expect(sdkMethod).toHaveBeenCalledWith({
        client: lowLevelClient,
        path: { bank_id: "bank 1" },
        ...(body ? { body } : {}),
      });

      expect(respondWithSdk).toHaveBeenCalledTimes(1);
      expect(respondWithSdk).toHaveBeenCalledWith(SENTINEL_RESULT, label, { request });
    }
  );
});
