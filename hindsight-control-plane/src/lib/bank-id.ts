/**
 * Whether a bank id is a URL dot segment ("." or "..").
 *
 * `encodeURIComponent` leaves both unchanged, and a WHATWG URL parser removes a "." segment and
 * resolves ".." against the segment before it (it reads "%2e", "%2E" and ".%2e" the same way, so
 * no encoding keeps them in place). `${DATAPLANE_URL}/v1/default/banks/..${suffix}` would reach
 * `/v1/default${suffix}` instead of a bank, so such ids are refused before any URL is built.
 */
export function isDotSegmentBankId(bankId: string): boolean {
  return bankId === "." || bankId === "..";
}
