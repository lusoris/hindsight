import type { BankRouteContext } from "@/lib/bank-sdk-route";
import { relayBankGet } from "@/lib/dataplane-proxy";
import { forwardParams } from "@/lib/query-params";

export async function GET(request: Request, context: BankRouteContext) {
  return relayBankGet(request, context, {
    failure: {
      error: "Failed to list tags",
      errorKey: "api.errors.tags.list",
    },
    action: "listing tags",
    onUpstreamError: "log",
    suffix: (q) => {
      const query = forwardParams(q, {
        single: ["q", "source", "limit", "offset"],
      }).toString();
      return `/tags${query ? `?${query}` : ""}`;
    },
  });
}
