export function ema<T extends { time: number; close: number }>(bars: T[], n: number) {
  if (bars.length < n) return []
  const k = 2 / (n + 1)
  let v = bars.slice(0, n).reduce((s, b) => s + b.close, 0) / n
  const out = [{ time: bars[n - 1].time, value: v }]
  for (let i = n; i < bars.length; i++) {
    v = bars[i].close * k + v * (1 - k)
    out.push({ time: bars[i].time, value: v })
  }
  return out
}
