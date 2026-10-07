import type {
  ISeriesApi, ISeriesPrimitive, ISeriesPrimitivePaneRenderer, ISeriesPrimitivePaneView, SeriesAttachedParameter, SeriesType, Time,
} from 'lightweight-charts'

export type Band = { lo: number; hi: number; side: 'sell' | 'buy' | 'range'; label: string }

export const css = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim()
const FILL = { sell: '--zone-sell', buy: '--zone-buy', range: '--zone-range' }
const EDGE = { sell: '--down', buy: '--up', range: '--muted' }

export class ZoneBands implements ISeriesPrimitive<Time> {
  private zones: Band[] = []
  private series: ISeriesApi<SeriesType> | null = null
  private requestUpdate: (() => void) | null = null

  attached(p: SeriesAttachedParameter<Time>) {
    this.series = p.series
    this.requestUpdate = p.requestUpdate
  }
  detached() { this.series = null }
  setZones(z: Band[]) {
    this.zones = z
    this.requestUpdate?.()
  }
  updateAllViews() {}

  // Isi zona di bawah candle, label di atas candle supaya tetap terbaca.
  paneViews(): readonly ISeriesPrimitivePaneView[] {
    const draw = (label: boolean): ISeriesPrimitivePaneRenderer => ({
      draw: (target) => {
        const series = this.series
        if (!series) return
        target.useBitmapCoordinateSpace((scope) => {
          const ctx = scope.context, w = scope.bitmapSize.width, vr = scope.verticalPixelRatio, hr = scope.horizontalPixelRatio
          for (const z of this.zones) {
            const y1 = series.priceToCoordinate(z.hi), y2 = series.priceToCoordinate(z.lo)
            if (y1 == null || y2 == null) continue
            const top = Math.min(y1, y2) * vr, h = Math.max(Math.abs(y2 - y1) * vr, 2 * vr)
            const edge = css(EDGE[z.side])
            if (!label) {
              ctx.fillStyle = css(FILL[z.side])
              ctx.fillRect(0, top, w, h)
              ctx.fillStyle = edge
              ctx.fillRect(0, top, w, 1 * vr)
              ctx.fillRect(0, top + h - 1 * vr, w, 1 * vr)
              continue
            }
            ctx.font = `600 ${11 * hr}px ${css('--font-num')}`
            const tw = ctx.measureText(z.label).width
            ctx.fillStyle = css('--card')
            ctx.fillRect(4 * hr, top - 17 * vr, tw + 8 * hr, 15 * vr)
            ctx.fillStyle = edge
            ctx.fillText(z.label, 8 * hr, top - 5 * vr)
          }
        })
      },
    })
    const fill = draw(false), text = draw(true)
    return [{ zOrder: () => 'bottom' as const, renderer: () => fill }, { zOrder: () => 'top' as const, renderer: () => text }]
  }
}
