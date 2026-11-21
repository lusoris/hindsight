import type { BankRouteContext } from "@/lib/bank-sdk-route";
import { relayBankGet } from "@/lib/dataplane-proxy";

export async function GET(request: Request, context: BankRouteContext) {
  return relayBankGet(request, context, {
    failure: {
      error: "Failed to get audit log stats",
      errorKey: "api.errors.auditLogs.stats",
    },
    action: "getting audit log stats",
    onUpstreamError: "detail",
    suffix: (q) => `/audit-logs/stats${q.size ? `?${q}` : ""}`,
  });
}
