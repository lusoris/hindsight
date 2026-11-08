/** Number(value) of the query parameter, undefined when it is absent or empty. */
export function readOptionalNumber(
  searchParams: URLSearchParams,
  name: string
): number | undefined {
  const value = searchParams.get(name);
  return value ? Number(value) : undefined;
}
