import type { BankRouteContext } from "@/lib/bank-sdk-route";
import { relayBankGet } from "@/lib/dataplane-proxy";

export async function GET(request: Request, context: BankRouteContext) {
  return relayBankGet(request, context, {
    failure: {
      error: "Failed to list LLM requests",
      errorKey: "api.errors.llmRequests.list",
    },
    action: "listing LLM requests",
    onUpstreamError: "detail",
    suffix: (q) => `/llm-requests${q.size ? `?${q}` : ""}`,
  });
}
