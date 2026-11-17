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

type ForwardParamsOptions = {
  repeated?: readonly string[];
  single?: readonly string[];
};

/** Copies repeated values (including empty ones), then non-empty first single values, in key order. */
export function forwardParams(
  searchParams: URLSearchParams,
  options: ForwardParamsOptions
): URLSearchParams {
  const forwarded = new URLSearchParams();
  for (const key of options.repeated ?? []) {
    for (const value of searchParams.getAll(key)) {
      forwarded.append(key, value);
    }
  }
  for (const key of options.single ?? []) {
    const value = searchParams.get(key);
    if (value) forwarded.append(key, value);
  }
  return forwarded;
}
