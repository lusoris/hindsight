import type { BankRouteContext } from "@/lib/bank-sdk-route";
import { relayBankGet } from "@/lib/dataplane-proxy";
import { forwardParams } from "@/lib/query-params";

export async function GET(request: Request, context: BankRouteContext) {
  return relayBankGet(request, context, {
    failure: {
      error: "Failed to list observation scopes",
      errorKey: "api.errors.observations.list",
    },
    action: "listing observation scopes",
    onUpstreamError: "relay",
    suffix: (q) => {
      const query = forwardParams(q, {
        single: ["limit", "offset"],
      }).toString();
      return `/observations/scopes${query ? `?${query}` : ""}`;
    },
  });
}
