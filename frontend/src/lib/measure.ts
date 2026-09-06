import type { CanvasRenderingTarget2D } from 'fancy-canvas';
import type { IChartApiBase, IPrimitivePaneRenderer, IPrimitivePaneView, ISeriesApi, ISeriesPrimitive, SeriesAttachedParameter, SeriesType, Time } from 'lightweight-charts';
import { alpha } from './tokens';

export type MeasureBar = { time: Time; close: number; index: number };
// low/high는 a~b 사이 모든 봉의 최저·최고다(선택한 두 봉의 종가가 아니라 그 구간 전체가 그린 가격대).
export type MeasureRange = { a: MeasureBar; b: MeasureBar; low: number; high: number };
export type MeasureColors = { up: string; down: string; back: string };

const LABEL_FONT = "bold 13px 'DM Mono', ui-monospace, SFMono-Regular, monospace";
const money = (value: number) => Math.round(value).toLocaleString('ko-KR');
const PILL_HEIGHT = 20;
const PILL_PADDING = 7;

type Box = { left: number; right: number; top: number; bottom: number; up: boolean; barsLabel: string; amountLabel: string; pctLabel: string };

class MeasureRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly box: Box | null, private readonly colors: MeasureColors) {}
  draw(target: CanvasRenderingTarget2D): void {
    const box = this.box;
    if (!box) return;
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      const tone = box.up ? this.colors.up : this.colors.down;
      const height = Math.max(1, box.bottom - box.top);
      context.fillStyle = alpha(tone, 0.14);
      context.fillRect(box.left, box.top, box.right - box.left, height);
      context.strokeStyle = tone;
      context.lineWidth = 1.5;
      context.setLineDash([6, 4]);
      context.strokeRect(box.left + 0.5, box.top + 0.5, Math.max(1, box.right - box.left - 1), Math.max(1, height - 1));
      context.setLineDash([]);
      context.font = LABEL_FONT;
      context.textBaseline = 'middle';
      // 가운데 정렬로 그리되, 판 바깥으로 나가면 안쪽으로 붙잡아 둔다.
      const pill = (text: string, centerX: number, centerY: number) => {
        const width = context.measureText(text).width + PILL_PADDING * 2;
        const left = Math.min(Math.max(centerX - width / 2, 2), mediaSize.width - width - 2);
        const top = Math.min(Math.max(centerY - PILL_HEIGHT / 2, 2), mediaSize.height - PILL_HEIGHT - 2);
        context.fillStyle = this.colors.back;
        context.fillRect(left, top, width, PILL_HEIGHT);
        context.strokeStyle = tone;
        context.lineWidth = 1;
        context.strokeRect(left + 0.5, top + 0.5, width - 1, PILL_HEIGHT - 1);
        context.fillStyle = tone;
        context.textAlign = 'left';
        context.fillText(text, left + PILL_PADDING, top + PILL_HEIGHT / 2);
      };
      // 봉 개수는 박스 바로 아래 가운데, 금액·변화율은 박스 오른쪽 옆에 위아래로.
      pill(box.barsLabel, (box.left + box.right) / 2, box.bottom + PILL_HEIGHT / 2 + 6);
      const midY = (box.top + box.bottom) / 2;
      pill(box.amountLabel, box.right + PILL_PADDING * 2 + context.measureText(box.amountLabel).width / 2, midY - PILL_HEIGHT / 2 - 2);
      pill(box.pctLabel, box.right + PILL_PADDING * 2 + context.measureText(box.pctLabel).width / 2, midY + PILL_HEIGHT / 2 + 2);
    });
  }
}

/**
 * 클릭으로 고른 두 봉 사이 구간을 박스로 그리는 시리즈 프리미티브.
 *
 * 박스의 상하 경계는 두 봉의 종가가 아니라 그 구간 전체(a~b)의 최고·최저를 감싼다 — 그 사이
 * 어디까지 가격이 오르내렸는지 한눈에 보기 위해서다. 금액·변화율도 그 감싼 범위(최고가-최저가)
 * 자체의 폭이다 — 박스가 보여주는 것과 라벨이 말하는 것이 어긋나지 않게 한다. 박스 색(상승/하락)만
 * 선택한 두 봉의 종가 방향으로 정한다. 봉 개수는 박스 아래에, 가격 범위는 박스 오른쪽에 따로 붙여
 * 박스 안 글자가 캔들과 겹치지 않게 한다.
 *
 * 값(선택된 두 봉·구간 최고저)은 소유자(React 상태)가 들고 있고 프리미티브는 매 프레임 좌표만
 * 다시 계산해서 그린다. PositionZones와 달리 시간축 좌표(x)도 필요해서 attached() 때 받는
 * chart를 들고 있는다.
 */
export class MeasureTool implements ISeriesPrimitive<Time> {
  private chart: IChartApiBase<Time> | null = null;
  private series: ISeriesApi<SeriesType, Time> | null = null;
  private redraw: (() => void) | null = null;
  private readonly views: IPrimitivePaneView[];

  constructor(private readonly read: () => MeasureRange | null, private readonly colors: MeasureColors) {
    this.views = [{ zOrder: () => 'top', renderer: () => this.frame() }];
  }

  attached(param: SeriesAttachedParameter<Time, SeriesType>): void { this.chart = param.chart; this.series = param.series; this.redraw = param.requestUpdate; }
  detached(): void { this.chart = null; this.series = null; this.redraw = null; }
  update(): void { this.redraw?.(); }
  paneViews(): readonly IPrimitivePaneView[] { return this.views; }

  private frame(): MeasureRenderer {
    const range = this.read();
    if (!range || !this.chart || !this.series) return new MeasureRenderer(null, this.colors);
    const x1 = this.chart.timeScale().timeToCoordinate(range.a.time);
    const x2 = this.chart.timeScale().timeToCoordinate(range.b.time);
    const yTop = this.series.priceToCoordinate(range.high);
    const yBottom = this.series.priceToCoordinate(range.low);
    if (x1 === null || x2 === null || yTop === null || yBottom === null) return new MeasureRenderer(null, this.colors);
    const bars = Math.abs(range.b.index - range.a.index) + 1;
    const diff = range.b.close - range.a.close; // 박스 색(상승/하락 구간)만 이걸로 정한다.
    const spread = range.high - range.low; // 가격 변동폭 — 박스가 실제로 감싸는 높이다.
    const spreadPct = range.low ? (spread / range.low) * 100 : 0; // 변화율 — 그 변동폭을 구간 최저가 대비 비율로.
    const box: Box = {
      left: Math.min(x1, x2), right: Math.max(x1, x2), top: yTop, bottom: yBottom,
      up: diff >= 0,
      barsLabel: `${bars}봉`,
      amountLabel: `${money(spread)}원`,
      pctLabel: `${spreadPct.toFixed(2)}%`,
    };
    return new MeasureRenderer(box, this.colors);
  }
}
