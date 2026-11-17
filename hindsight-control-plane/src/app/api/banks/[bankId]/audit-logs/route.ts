import type { BankRouteContext } from "@/lib/bank-sdk-route";
import { relayBankGet } from "@/lib/dataplane-proxy";

export async function GET(request: Request, context: BankRouteContext) {
  return relayBankGet(request, context, {
    failure: {
      error: "Failed to list audit logs",
      errorKey: "api.errors.auditLogs.list",
    },
    action: "listing audit logs",
    onUpstreamError: "detail",
    suffix: (q) => `/audit-logs${q.size ? `?${q}` : ""}`,
  });
}
