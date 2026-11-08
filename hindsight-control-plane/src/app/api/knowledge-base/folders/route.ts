import { NextRequest, NextResponse } from "next/server";
import { localizeApiErrorPayload } from "@/lib/i18n/api-errors";
import { dataplaneBankUrl, getDataplaneHeaders } from "@/lib/hindsight-client";
import { requireQueryBankId } from "@/lib/route-validation";

/** Creates a knowledge-base folder in the bank from the JSON body. */
export async function POST(request: NextRequest) {
  try {
    const bankId = requireQueryBankId(request);
    if (bankId instanceof NextResponse) return bankId;

    const body = await request.json();
    const response = await fetch(dataplaneBankUrl(bankId, "/knowledge-base/folders"), {
      method: "POST",
      headers: getDataplaneHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: response.statusText }));
      return NextResponse.json(error, { status: response.status });
    }
    return NextResponse.json(await response.json(), { status: response.status });
  } catch (error) {
    console.error("Failed to create folder:", error);
    return NextResponse.json(
      localizeApiErrorPayload(request, {
        error: "Failed to create folder",
        errorKey: "api.errors.knowledgeBase.createFolder",
      }),
      { status: 500 }
    );
  }
}
