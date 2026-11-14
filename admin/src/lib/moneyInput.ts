/** Parse a decimal yuan amount without rounding away digits or accepting exponent notation. */
export function yuanInputToFen(value: string): number | null {
  const text = value.trim();
  if (!/^\d+(?:\.\d{1,2})?$/.test(text)) return null;
  const [yuan, fraction = ""] = text.split(".");
  const fen = BigInt(yuan) * 100n + BigInt(fraction.padEnd(2, "0"));
  return fen <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(fen) : null;
}
