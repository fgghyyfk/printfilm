/** Decimal prices use 1/10000 yuan units, rounding each delivered output up to fen. */
export function mediaQuoteFen(price: string | undefined, quantity = 1, seconds = 1): number | null {
  if (!price || !/^\d+(\.\d{1,4})?$/.test(price) || !Number.isInteger(quantity) || !Number.isInteger(seconds) || quantity < 1 || seconds < 1) return null
  const [whole, fraction = ''] = price.split('.')
  const units = BigInt(whole) * 10000n + BigInt(fraction.padEnd(4, '0'))
  if (units <= 0n) return null
  return Number(((units * BigInt(seconds) + 99n) / 100n) * BigInt(quantity))
}
