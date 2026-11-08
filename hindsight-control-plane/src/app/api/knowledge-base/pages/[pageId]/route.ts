import { NextRequest, NextResponse } from "next/server";
import { localizeApiErrorPayload } from "@/lib/i18n/api-errors";
import { dataplaneBankUrl, getDataplaneHeaders } from "@/lib/hindsight-client";
import { requireQueryBankId } from "@/lib/route-validation";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ pageId: string }> }
) {
  try {
    const bankId = requireQueryBankId(request);
    if (bankId instanceof NextResponse) return bankId;

    const { pageId } = await params;
    const response = await fetch(
      dataplaneBankUrl(
        bankId,
        `/knowledge-base/pages/${encodeURIComponent(decodeURIComponent(pageId))}`
      ),
      { headers: getDataplaneHeaders() }
    );
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: response.statusText }));
      return NextResponse.json(error, { status: response.status });
    }
    return NextResponse.json(await response.json(), { status: 200 });
  } catch (error) {
    console.error("Failed to fetch knowledge page:", error);
    return NextResponse.json(
      localizeApiErrorPayload(request, {
        error: "Failed to fetch knowledge page",
        errorKey: "api.errors.knowledgeBase.fetchPage",
      }),
      { status: 500 }
    );
  }
}
