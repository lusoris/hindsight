/** Number of the query parameter, undefined when absent, empty, whitespace-only, or not finite. */
export function readOptionalNumber(
  searchParams: URLSearchParams,
  name: string
): number | undefined {
  const value = searchParams.get(name);
  if (value === null || value.trim() === "") {
    return undefined;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}
