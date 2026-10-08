// npx tsx scripts/nasib.check.ts
import assert from 'node:assert/strict'
import { nasib } from '../src/lib/nasib'

const buy = { side: 'buy' as const, entry: 4122.69, zone: [4122.05, 4123.33] as [number, number], sl: 4119.91, tp: [4129.23, 4132.98] }
const bar = (t: number, o: number, h: number, l: number, c: number) => ({ time: t, open: o, high: h, low: l, close: c })
const t0 = 1000

// screenshot 8 Okt: turun menembus SL lalu kembali ke zona -> kena SL, bukan "harga di zona"
assert.equal(nasib(buy, t0, [bar(1000, 4124, 4125, 4123, 4124), bar(1300, 4124, 4124, 4118.5, 4123)], 2000).status, 'SL')
// candle yang menyentuh entry ditutup di bawah SL: zona jebol, bukan entry sah
assert.equal(nasib(buy, t0, [bar(1000, 4124, 4124, 4118, 4118.2)], 2000).status, 'invalid')
const sell = { side: 'sell' as const, entry: 4130, sl: 4133, tp: [4120] }
assert.equal(nasib(sell, t0, [bar(1000, 4128, 4129.9, 4127, 4129.5), bar(1300, 4129.5, 4129.9, 4119, 4119)], 2000).status, 'batal')
assert.equal(nasib(sell, t0, [bar(1000, 4128, 4130.5, 4127, 4129), bar(1300, 4129, 4129, 4119, 4119.5)], 2000).status, 'TP1')
assert.equal(nasib(sell, t0, [bar(1000, 4128, 4129, 4127, 4128)], t0 + 90000).status, 'kedaluwarsa')
assert.equal(nasib(sell, t0, [bar(1000, 4128, 4130.2, 4127, 4129)], 2000).status, 'berjalan')
assert.equal(nasib(sell, t0, [bar(1000, 4128, 4129, 4127, 4128)], 2000).status, 'menunggu')
const buyAtas = { side: 'buy' as const, entry: 4100, sl: 4097, tp: [4110] }
assert.equal(nasib({ ...buyAtas, side: 'sell' as const, sl: 4103, tp: [4090] }, t0, [bar(1000, 4098, 4099, 4097, 4098), bar(1300, 4104, 4105, 4103.5, 4104)], 2000).status, 'invalid')
assert.equal(nasib({ ...buy, hasil: 'TP' }, t0, [], 2000).status, 'TP1')
console.log('nasib OK')
