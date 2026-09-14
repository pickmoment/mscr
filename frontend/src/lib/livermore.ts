import type { CanvasRenderingTarget2D } from 'fancy-canvas';
import type { IChartApiBase, IPrimitivePaneRenderer, IPrimitivePaneView, ISeriesApi, ISeriesPrimitive, SeriesAttachedParameter, SeriesType, Time } from 'lightweight-charts';
import type { LivermoreSegment } from './api';

// 국면 코드: 1=상승국면 2=자연반락 3=2차반등 -1=하락국면 -2=자연반등 -3=2차반락.
// 부호로 계열(상승/하락)을, 절댓값으로 단계(추세중=1 / 되돌림중=2 / 재확인대기=3)를 구분해 색을 정한다.
const PHASE_COLORS: Record<number, string> = {
  1: 'rgba(76,175,80,0.16)', 2: 'rgba(255,193,7,0.14)', 3: 'rgba(139,195,74,0.12)',
  [-1]: 'rgba(239,83,80,0.16)', [-2]: 'rgba(255,152,0,0.14)', [-3]: 'rgba(236,64,122,0.12)',
};
export const LIVERMORE_PHASE_LABELS: Record<number, string> = {
  1: '상승국면', 2: '자연반락', 3: '2차반등', [-1]: '하락국면', [-2]: '자연반등', [-3]: '2차반락',
};

type Band = { left: number; width: number; color: string };

class PhaseBandsRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly bands: Band[]) {}
  draw(target: CanvasRenderingTarget2D): void {
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      for (const band of this.bands) {
        context.fillStyle = band.color;
        context.fillRect(band.left, 0, band.width, mediaSize.height);
      }
    });
  }
}

/**
 * 리버모어 국면 구간을 가격판 배경에 옅은 색 띠로 깐다. 캔들보다 아래(zOrder 'bottom')에 그려
 * 캔들·다른 오버레이를 가리지 않는다. 값은 소유자가 들고 있고 이 프리미티브는 매 프레임 좌표만
 * 다시 계산한다 — 줌·스크롤에 맞춰 매번 다시 그려야 하기 때문이다.
 */
export class LivermorePhaseBands implements ISeriesPrimitive<Time> {
  private chart: IChartApiBase<Time> | null = null;
  private series: ISeriesApi<SeriesType, Time> | null = null;
  private redraw: (() => void) | null = null;
  private readonly views: IPrimitivePaneView[];

  constructor(private segments: LivermoreSegment[]) {
    this.views = [{ zOrder: () => 'bottom', renderer: () => this.frame() }];
  }

  attached(param: SeriesAttachedParameter<Time, SeriesType>): void {
    this.chart = param.chart;
    this.series = param.series;
    this.redraw = param.requestUpdate;
  }
  detached(): void { this.chart = null; this.series = null; this.redraw = null; }
  update(): void { this.redraw?.(); }
  setSegments(segments: LivermoreSegment[]): void { this.segments = segments; this.redraw?.(); }
  paneViews(): readonly IPrimitivePaneView[] { return this.views; }

  private frame(): PhaseBandsRenderer | null {
    if (!this.chart || !this.series) return null;
    const timeScale = this.chart.timeScale();
    const barSpacing = timeScale.options().barSpacing;
    const bands = this.segments
      .map((segment): Band | null => {
        const x1 = timeScale.timeToCoordinate(segment.from);
        const x2 = timeScale.timeToCoordinate(segment.to);
        if (x1 === null || x2 === null) return null;
        const left = Math.min(x1, x2) - barSpacing / 2;
        const width = Math.abs(x2 - x1) + barSpacing;
        return { left, width, color: PHASE_COLORS[segment.phase_code] ?? 'rgba(128,128,128,0.08)' };
      })
      .filter((band): band is Band => band !== null);
    return bands.length ? new PhaseBandsRenderer(bands) : null;
  }
}
