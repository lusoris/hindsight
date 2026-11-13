import { NextRequest } from "next/server";
import type { BankRouteContext } from "@/lib/bank-sdk-route";
import { relayBankPost } from "@/lib/dataplane-proxy";

/**
 * Import a bank template manifest into the bank.
 *
 * Proxies to POST /v1/default/banks/{bank_id}/import with the request's JSON
 * manifest as the body; `?dry_run=true` is forwarded so the dataplane only
 * validates. Direct fetch since the SDK doesn't have this operation yet.
 */
export async function POST(request: NextRequest, context: BankRouteContext) {
  return relayBankPost(
    request,
    context,
    {
      successStatus: 200,
      failure: {
        error: "Failed to import bank template",
        errorKey: "api.errors.banks.importTemplate",
      },
      action: "importing bank template",
    },
    async () => {
      const dryRun = request.nextUrl.searchParams.get("dry_run") === "true";
      return {
        suffix: `/import${dryRun ? "?dry_run=true" : ""}`,
        jsonBody: await request.json(),
      };
    }
  );
}
