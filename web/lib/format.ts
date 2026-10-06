/** Деньги в тиынах → «1 234 567,89 ₸». Целые числа, без float-арифметики. */
export function tenge(tiyn: number | null | undefined): string {
  if (tiyn === null || tiyn === undefined) return "—";
  const sign = tiyn < 0 ? "−" : "";
  const abs = Math.abs(tiyn);
  const whole = Math.floor(abs / 100)
    .toString()
    .replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  const frac = (abs % 100).toString().padStart(2, "0");
  return `${sign}${whole},${frac} ₸`;
}

/** «1 234,56» или «1234.56» → тиыны; null, если ввод некорректен. */
export function parseTenge(input: string): number | null {
  const s = input.replace(/[\s ₸]/g, "").replace(",", ".");
  if (!/^\d+(\.\d{1,2})?$/.test(s)) return null;
  const [whole, frac = ""] = s.split(".");
  return Number(whole) * 100 + Number(frac.padEnd(2, "0"));
}

export function percent(share: string | number): string {
  return `${(Number(share) * 100).toFixed(1).replace(".", ",")}%`;
}

export function date(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return `${d}.${m}.${y}`;
}

export function currentPeriod(today = new Date()): string {
  return `${today.getFullYear()}H${today.getMonth() < 6 ? 1 : 2}`;
}

export function periodLabel(code: string): string {
  const m = /^(\d{4})H([12])$/.exec(code);
  return m ? `${m[2]} полугодие ${m[1]}` : code;
}
