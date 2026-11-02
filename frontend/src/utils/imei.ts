/** Client-side IMEI checks for instant feedback while typing. The server re-validates everything. */
export function luhnValid(digits: string): boolean {
  if (!/^\d+$/.test(digits)) return false;
  let total = 0;
  for (let i = 0; i < digits.length; i += 1) {
    let d = Number(digits[digits.length - 1 - i]);
    if (i % 2 === 1) { d *= 2; if (d > 9) d -= 9; }
    total += d;
  }
  return total % 10 === 0;
}

export type ImeiProblem = "empty" | "digits" | "length" | "checksum" | null;

export function imeiProblem(raw: string): ImeiProblem {
  const v = raw.replace(/\s+/g, "");
  if (!v) return "empty";
  if (!/^\d+$/.test(v)) return "digits";
  if (v.length !== 15) return "length";
  return luhnValid(v) ? null : "checksum";
}
