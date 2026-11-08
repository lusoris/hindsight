import type { NextResponse } from "next/server";
import type { RequestLike } from "@/lib/i18n/api-errors";
import { respondWithSdk, type SdkResult } from "@/lib/sdk-response";

/** Route context of a `/banks/[bankId]/...` handler. */
export type BankRouteContext = { params: Promise<{ bankId: string }> };

/**
 * Awaits the bank id from the route params, runs one bank-scoped SDK call and
 * serializes it with `respondWithSdk` (same failure label, `{ request }` for localization).
 */
export async function respondWithBankSdk<T>(
  request: RequestLike,
  context: BankRouteContext,
  failureLabel: string,
  call: (bankId: string) => Promise<SdkResult<T>>
): Promise<NextResponse> {
  const { bankId } = await context.params;
  return respondWithSdk(await call(bankId), failureLabel, { request });
}
