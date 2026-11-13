import { NextRequest, NextResponse } from "next/server";
import { localizeApiErrorPayload } from "@/lib/i18n/api-errors";
import type { BankRouteContext } from "@/lib/bank-sdk-route";
import { relayBankPost } from "@/lib/dataplane-proxy";

const CLONE_SCOPE_FLAGS = ["include_data", "include_bank_config", "include_history"] as const;

/**
 * Clone a bank into a new one.
 *
 * Proxies to POST /v1/default/banks/{bank_id}/clone, which runs the export and
 * the import back to back server-side and returns an operation to poll. The
 * scope flags are only forwarded when the caller set them, so the dataplane's
 * own defaults stay the single source of truth for what a clone carries.
 */
export async function POST(request: NextRequest, context: BankRouteContext) {
  return relayBankPost(
    request,
    context,
    {
      successStatus: 202,
      failure: { error: "Failed to clone bank", errorKey: "api.errors.banks.clone" },
      action: "cloning bank",
    },
    async () => {
      const body = await request.json().catch(() => ({}));
      const targetBankId = body?.target_bank_id;
      if (!targetBankId || typeof targetBankId !== "string") {
        return NextResponse.json(
          localizeApiErrorPayload(request, {
            error: "target_bank_id is required",
            errorKey: "api.errors.validation.targetBankIdRequired",
          }),
          { status: 400 }
        );
      }

      const qs = new URLSearchParams({ target_bank_id: targetBankId });
      for (const flag of CLONE_SCOPE_FLAGS) {
        if (typeof body?.[flag] === "boolean") {
          qs.set(flag, String(body[flag]));
        }
      }
      return { suffix: `/clone?${qs.toString()}` };
    }
  );
}
