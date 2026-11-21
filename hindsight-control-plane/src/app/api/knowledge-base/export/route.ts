import { NextRequest, NextResponse } from "next/server";
import { relayUpstreamJson } from "@/lib/dataplane-proxy";
import { localizeApiErrorPayload } from "@/lib/i18n/api-errors";
import { dataplaneBankUrl, getDataplaneHeaders } from "@/lib/hindsight-client";
import { requireQueryBankId } from "@/lib/route-validation";

/** Returns the bank's knowledge-base export from the dataplane. */
export async function GET(request: NextRequest) {
  try {
    const bankId = requireQueryBankId(request);
    if (bankId instanceof NextResponse) return bankId;

    const response = await fetch(dataplaneBankUrl(bankId, "/knowledge-base/export"), {
      headers: getDataplaneHeaders(),
    });
    return await relayUpstreamJson(response, 200);
  } catch (error) {
    console.error("Failed to export knowledge base:", error);
    return NextResponse.json(
      localizeApiErrorPayload(request, {
        error: "Failed to export knowledge base",
        errorKey: "api.errors.knowledgeBase.export",
      }),
      { status: 500 }
    );
  }
}
