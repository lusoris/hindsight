import { NextResponse } from "next/server";
import { relayUpstreamJson } from "@/lib/dataplane-proxy";
import { localizeApiErrorPayload } from "@/lib/i18n/api-errors";
import { dataplaneBankUrl, getDataplaneHeaders } from "@/lib/hindsight-client";
import { validateBankId } from "@/lib/route-validation";

/** Proxies POST /v1/default/banks/{bank_id}/consolidation-strategies/preview — the
 *  strategy editor's "which existing scopes does this rule match" summary. */
export async function POST(request: Request, { params }: { params: Promise<{ bankId: string }> }) {
  try {
    const { bankId } = await params;
    const bankIdError = validateBankId(request, bankId);
    if (bankIdError) return bankIdError;

    const body = await request.text();
    // dataplaneBankUrl encodes the id as one segment: the raw `${bankId}` this replaced let a
    // decoded `a/../b` route param reach bank `b`.
    const response = await fetch(dataplaneBankUrl(bankId, "/consolidation-strategies/preview"), {
      method: "POST",
      headers: getDataplaneHeaders({ "Content-Type": "application/json" }),
      body,
    });

    return await relayUpstreamJson(response, 200);
  } catch (error) {
    console.error("Error previewing consolidation strategies:", error);
    return NextResponse.json(
      localizeApiErrorPayload(request, {
        error: "Failed to preview consolidation strategies",
        errorKey: "api.errors.consolidation.previewStrategies",
      }),
      { status: 500 }
    );
  }
}
