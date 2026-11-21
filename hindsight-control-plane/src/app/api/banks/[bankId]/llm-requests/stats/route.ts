import type { BankRouteContext } from "@/lib/bank-sdk-route";
import { relayBankGet } from "@/lib/dataplane-proxy";

export async function GET(request: Request, context: BankRouteContext) {
  return relayBankGet(request, context, {
    failure: {
      error: "Failed to get LLM request stats",
      errorKey: "api.errors.llmRequests.stats",
    },
    action: "getting LLM request stats",
    onUpstreamError: "detail",
    suffix: (q) => `/llm-requests/stats${q.size ? `?${q}` : ""}`,
  });
}
