import type { CanvasRenderingTarget2D } from 'fancy-canvas';
import type { AutoscaleInfo, IPrimitivePaneRenderer, IPrimitivePaneView, ISeriesApi, ISeriesPrimitive, ISeriesPrimitiveAxisView, PrimitiveHoveredItem, SeriesAttachedParameter, SeriesType, Time } from 'lightweight-charts';
import type { TradePlan } from './api';

export type PositionLevel = 'entry' | 'stop' | 'target' | 'target2';
// target2는 트레이딩 계획(3분할)의 2차 익절가다. 계획 폼을 열 때만 값이 들어가고, 평소에는 null이라 그리지 않는다.
// origin은 이 블록이 어느 트레이딩 계획에서 왔는지다. 있으면 저장이 새 계획 생성이 아니라 그 계획의 수정이 된다.
export type PositionOrigin = { id: number; name: string; tp1Ratio: number; tp2Ratio: number; trailing: number; orderType: 'limit' | 'market'; enabled: boolean; note: string | null };
export type PositionPlan = { entry: number; stop: number; target: number; target2: number | null; quantity: number; origin: PositionOrigin | null };
export const positionLevels: PositionLevel[] = ['entry', 'stop', 'target', 'target2'];
export const levelLabel: Record<PositionLevel, string> = { entry: '진입', stop: '손절', target: '청산', target2: '2차 청산' };

// KRX 호가가격단위(2023-01-25 개정). 백엔드 autoplan._tick_size와 같은 규칙이라 차트에서 잡은 값이 계획 생성기와 어긋나지 않는다.
export const tickSize = (price: number, kind: string): number => {
  if (kind !== 'stock') return 5;
  for (const [ceiling, tick] of [[2000, 1], [5000, 5], [20000, 10], [50000, 50], [200000, 100], [500000, 500]]) if (price < ceiling) return tick;
  return 1000;
};
export const alignTick = (price: number, kind: string): number => { const tick = tickSize(price, kind); return Math.max(tick, Math.round(price / tick) * tick); };

export type PositionMetrics = { long: boolean; risk: number; reward: number; rr: number | null; rr2: number | null; stopPct: number | null; targetPct: number | null; target2Pct: number | null; loss: number; gain: number; cost: number; warning: string | null };
export const positionMetrics = (plan: PositionPlan): PositionMetrics => {
  const long = plan.target >= plan.entry;
  const risk = Math.abs(plan.entry - plan.stop);
  const reward = Math.abs(plan.target - plan.entry);
  // 손절·청산 수익률은 방향과 무관하게 "그 가격에 청산했을 때의 손익률"이다. 매도(하락 베팅) 계획도 손절은 음수로 나온다.
  const sign = long ? 1 : -1;
  const rate = (price: number) => plan.entry > 0 ? sign * (price - plan.entry) / plan.entry : null;
  const misplaced = long ? plan.stop >= plan.entry : plan.stop <= plan.entry;
  return {
    long, risk, reward,
    rr: risk > 0 ? reward / risk : null,
    rr2: risk > 0 && plan.target2 !== null ? Math.abs(plan.target2 - plan.entry) / risk : null,
    stopPct: rate(plan.stop), targetPct: rate(plan.target), target2Pct: plan.target2 === null ? null : rate(plan.target2),
    loss: risk * plan.quantity, gain: reward * plan.quantity, cost: plan.entry * plan.quantity,
    warning: misplaced ? `손절가가 진입가 ${long ? '위' : '아래'}에 있어 손실 구간이 성립하지 않습니다` : reward === 0 ? '청산가가 진입가와 같습니다' : null,
  };
};

/** 현재가 기준 기본 계획. 손절 -5%, 청산 +10%로 잡고 호가 단위에 맞춘다. */
export const defaultPlan = (price: number, kind: string, quantity: number): PositionPlan => ({
  entry: alignTick(price, kind), stop: alignTick(price * 0.95, kind), target: alignTick(price * 1.1, kind), target2: null, quantity: Math.max(0, Math.round(quantity)), origin: null,
});

/** 3분할 비율(%). 계획 폼의 입력값이거나 연결된 계획의 값이다. */
export type PositionSplit = { tp1Ratio: number; tp2Ratio: number; trailing: number };
export type PositionLeg = { key: 'tp1' | 'tp2' | 'trailing'; label: string; quantity: number; price: number | null; weight: number; r: number | null; amount: number; floor: boolean };
export type PositionOutcome = { legs: PositionLeg[]; totalR: number | null; totalAmount: number };

/** 백엔드 trading._leg_quantities와 같은 배분. 반올림 오차는 트레일링 레그가 흡수한다. */
export const legQuantities = (quantity: number, tp1Ratio: number, tp2Ratio: number): [number, number, number] => {
  if (!Number.isInteger(quantity)) return [quantity * tp1Ratio / 100, quantity * tp2Ratio / 100, quantity * (100 - tp1Ratio - tp2Ratio) / 100];
  const first = Math.round(quantity * tp1Ratio / 100);
  const second = Math.round(quantity * tp2Ratio / 100);
  return [first, second, quantity - first - second];
};

/**
 * 분할 비중을 반영한 레그별 손익. R은 "전량이 손절폭만큼 움직였을 때"를 1R로 두고 각 레그가 기여하는 몫이라
 * 세 값을 더하면 계획 전체의 R이 된다.
 *
 * 트레일링 레그의 청산가는 사후에만 확정된다(2차 익절 이후 최고가 기준). 여기서는 2차 도달 직후 곧바로
 * 되돌리는 최악의 경우인 `2차 청산가 × (1 ∓ 트레일링%)`를 하한으로 쓴다. 실제 청산가는 이보다 높다.
 */
export const positionOutcome = (plan: PositionPlan, split: PositionSplit): PositionOutcome => {
  const metrics = positionMetrics(plan);
  const sign = metrics.long ? 1 : -1;
  const quantities = legQuantities(plan.quantity, split.tp1Ratio, split.tp2Ratio);
  const ratios = [split.tp1Ratio / 100, split.tp2Ratio / 100, (100 - split.tp1Ratio - split.tp2Ratio) / 100];
  const trailingPrice = plan.target2 === null ? null : plan.target2 * (1 - sign * split.trailing / 100);
  const prices = [plan.target, plan.target2, trailingPrice];
  const labels = ['1차', '2차', '3차'] as const;
  const keys = ['tp1', 'tp2', 'trailing'] as const;
  const legs = keys.map((key, index) => {
    const price = prices[index];
    const quantity = quantities[index];
    // 수량을 아직 안 넣었으면 비율만으로 기여 R을 낸다. 수량이 있으면 정수 배분된 실제 몫을 쓴다.
    const weight = plan.quantity > 0 ? quantity / plan.quantity : ratios[index];
    const gain = price === null ? null : sign * (price - plan.entry);
    return {
      key, label: labels[index], quantity, price, weight,
      r: gain === null || metrics.risk <= 0 ? null : gain / metrics.risk * weight,
      amount: gain === null ? 0 : gain * quantity,
      floor: key === 'trailing',
    };
  });
  const missing = legs.some(leg => leg.r === null);
  return { legs, totalR: missing ? null : legs.reduce((sum, leg) => sum + (leg.r ?? 0), 0), totalAmount: legs.reduce((sum, leg) => sum + leg.amount, 0) };
};

/** 트레이딩 계획을 차트 블록으로 되돌린다. 1차·2차 익절가가 각각 청산·2차 청산선이 된다. */
export const positionFromTradePlan = (plan: TradePlan): PositionPlan => ({
  entry: plan.entry_price, stop: plan.stop_price, target: plan.tp1_price, target2: plan.tp2_price, quantity: plan.quantity,
  origin: { id: plan.id, name: plan.name, tp1Ratio: plan.tp1_ratio * 100, tp2Ratio: plan.tp2_ratio * 100, trailing: plan.tp3_trailing_pct, orderType: plan.order_type, enabled: plan.enabled, note: plan.note },
});

const STORE_KEY = 'mscr-chart-position';
export const POSITION_EVENT = 'mscr-chart-position-changed';
// 포지션 블록은 종목별로 기억한다. 목록을 훑다가 다시 돌아와도 잡아 둔 손절·청산이 남아 있어야 비교가 된다.
export const loadPositionPlans = (): Record<string, PositionPlan> => {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY) || 'null');
    return saved && typeof saved === 'object' ? saved as Record<string, PositionPlan> : {};
  } catch {
    return {};
  }
};
/** 저장 후 이벤트를 쏜다. 이미 그 종목을 보고 있는 차트도 다시 읽어야 하기 때문이다. */
export const storePositionPlan = (ticker: string, plan: PositionPlan | null): void => {
  const store = loadPositionPlans();
  if (plan) store[ticker] = plan; else delete store[ticker];
  localStorage.setItem(STORE_KEY, JSON.stringify(store));
  window.dispatchEvent(new Event(POSITION_EVENT));
};

type Palette = { entry: string; stop: string; target: string; target2: string; profitFill: string; profitFill2: string; lossFill: string; labelBack: string };
const palette = (light: boolean): Palette => ({
  entry: light ? '#334155' : '#cbd5e1',
  stop: '#2563eb', target: '#ef4444', target2: '#c2410c',
  profitFill: 'rgba(239,68,68,0.13)', profitFill2: 'rgba(239,68,68,0.07)', lossFill: 'rgba(37,99,235,0.14)',
  labelBack: light ? 'rgba(255,255,255,0.86)' : 'rgba(11,14,19,0.8)',
});
const LABEL_FONT = "11px 'DM Mono', ui-monospace, SFMono-Regular, monospace";
const money = (value: number) => Math.round(value).toLocaleString('ko-KR');
const signed = (value: number | null) => value == null ? '' : ` (${value >= 0 ? '+' : ''}${(value * 100).toFixed(1)}%)`;

type Band = { top: number; height: number; color: string };
// below=true면 라벨을 선 아래에 그린다. 세 선이 붙어 있을 때 진입·청산 라벨과 겹치지 않게 손절만 바깥쪽으로 뺀다.
type Marker = { y: number; color: string; dashed: boolean; text: string; below: boolean };
type Frame = { bands: Band[]; markers: Marker[]; back: string };

class ZoneRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly frame: Frame) {}
  draw(target: CanvasRenderingTarget2D): void {
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      for (const band of this.frame.bands) { context.fillStyle = band.color; context.fillRect(0, band.top, mediaSize.width, band.height); }
      context.lineWidth = 1;
      for (const marker of this.frame.markers) {
        context.beginPath();
        context.setLineDash(marker.dashed ? [5, 4] : []);
        context.strokeStyle = marker.color;
        context.moveTo(0, marker.y + 0.5);
        context.lineTo(mediaSize.width, marker.y + 0.5);
        context.stroke();
      }
      context.setLineDash([]);
      context.font = LABEL_FONT;
      context.textBaseline = 'alphabetic';
      // 레벨이 붙어 있으면 라벨끼리 겹친다. 선호 위치대로 늘어놓은 뒤 위에서부터 최소 간격만큼 밀어낸다.
      const placed = this.frame.markers
        .map(marker => ({ marker, baseline: marker.below ? marker.y + 15 : marker.y - 5 }))
        .sort((left, right) => left.baseline - right.baseline);
      let floor = 32; // 좌상단 OHLCV 범례 아래에서 시작해 범례와 겹치지 않게 한다.
      for (const item of placed) { item.baseline = Math.max(item.baseline, floor); floor = item.baseline + 15; }
      const overflow = floor - 15 - (mediaSize.height - 3);
      if (overflow > 0) for (const item of placed) item.baseline -= overflow;
      for (const { marker, baseline } of placed) {
        const width = context.measureText(marker.text).width;
        context.fillStyle = this.frame.back;
        context.fillRect(6, baseline - 11, width + 10, 15);
        context.fillStyle = marker.color;
        context.fillText(marker.text, 11, baseline);
      }
    });
  }
}

/**
 * 진입·손절·청산 가격을 캔들 위에 블록으로 그리는 시리즈 프리미티브.
 *
 * 값은 소유자(React 상태)가 들고 있고 프리미티브는 매 프레임 읽기만 한다. 드래그 중인 레벨은
 * autoscale 대상에서 빼는데, 끌어내린 레벨이 가격축을 넓히면 같은 픽셀이 다른 가격을 가리켜
 * 커서와 레벨이 서로를 밀어내기 때문이다.
 */
export class PositionZones implements ISeriesPrimitive<Time> {
  private series: ISeriesApi<SeriesType, Time> | null = null;
  private redraw: (() => void) | null = null;
  private dragging: PositionLevel | null = null;
  private readonly views: IPrimitivePaneView[];
  private readonly axisViews: ISeriesPrimitiveAxisView[];
  private readonly colors: Palette;

  constructor(private readonly read: () => PositionPlan | null, light: boolean) {
    this.colors = palette(light);
    this.views = [{ zOrder: () => 'top', renderer: () => this.frame() }];
    this.axisViews = positionLevels.map(level => ({
      coordinate: () => this.coordinate(level) ?? -100,
      visible: () => this.coordinate(level) !== null,
      text: () => money(this.read()?.[level] || 0),
      textColor: () => '#ffffff',
      backColor: () => this.colors[level],
    }));
  }

  attached(param: SeriesAttachedParameter<Time, SeriesType>): void { this.series = param.series; this.redraw = param.requestUpdate; }
  detached(): void { this.series = null; this.redraw = null; }
  update(): void { this.redraw?.(); }
  setDragging(level: PositionLevel | null): void { this.dragging = level; this.redraw?.(); }

  paneViews(): readonly IPrimitivePaneView[] { return this.views; }
  priceAxisViews(): readonly ISeriesPrimitiveAxisView[] { return this.read() ? this.axisViews : []; }

  autoscaleInfo(): AutoscaleInfo | null {
    const plan = this.read();
    if (!plan) return null;
    const prices = positionLevels.filter(level => level !== this.dragging).map(level => plan[level]).filter((price): price is number => price !== null && price > 0);
    if (!prices.length) return null;
    return { priceRange: { minValue: Math.min(...prices), maxValue: Math.max(...prices) } };
  }

  /** 커서와 6px 안에 있는 레벨. 드래그 시작 판정과 커서 모양에 함께 쓴다. */
  levelAt(y: number): PositionLevel | null {
    if (!this.read()) return null;
    let hit: PositionLevel | null = null;
    let best = 6;
    for (const level of positionLevels) {
      const coordinate = this.coordinate(level);
      if (coordinate === null) continue;
      const distance = Math.abs(coordinate - y);
      if (distance <= best) { best = distance; hit = level; }
    }
    return hit;
  }

  hitTest(_x: number, y: number): PrimitiveHoveredItem | null {
    const level = this.levelAt(y);
    return level ? { externalId: `position-${level}`, zOrder: 'top', cursorStyle: 'ns-resize', hitTestPriority: 1 } : null;
  }

  private coordinate(level: PositionLevel): number | null {
    const plan = this.read();
    if (!plan || !this.series) return null;
    const price = plan[level];
    if (price === null || !(price > 0)) return null;
    return this.series.priceToCoordinate(price);
  }

  private frame(): ZoneRenderer | null {
    const plan = this.read();
    if (!plan) return null;
    const entry = this.coordinate('entry');
    const stop = this.coordinate('stop');
    const target = this.coordinate('target');
    const target2 = this.coordinate('target2');
    if (entry === null) return null;
    const metrics = positionMetrics(plan);
    const gainAt = (price: number) => Math.abs(price - plan.entry) * plan.quantity;
    const bands: Band[] = [];
    if (target !== null) bands.push({ top: Math.min(entry, target), height: Math.abs(target - entry), color: this.colors.profitFill });
    if (target !== null && target2 !== null) bands.push({ top: Math.min(target, target2), height: Math.abs(target2 - target), color: this.colors.profitFill2 });
    if (stop !== null) bands.push({ top: Math.min(entry, stop), height: Math.abs(stop - entry), color: this.colors.lossFill });
    const exitLabel = plan.target2 === null ? '청산' : '1차 청산';
    const markers: Marker[] = [{ y: entry, color: this.colors.entry, dashed: true, below: false, text: `진입 ${money(plan.entry)}${plan.quantity > 0 ? ` · ${plan.quantity}주 ${money(metrics.cost)}원` : ''}` }];
    if (stop !== null) markers.push({ y: stop, color: this.colors.stop, dashed: false, below: stop > entry, text: `손절 ${money(plan.stop)}${signed(metrics.stopPct)}${plan.quantity > 0 ? ` · -${money(metrics.loss)}원` : ''}` });
    if (target !== null) markers.push({ y: target, color: this.colors.target, dashed: false, below: target > entry, text: `${exitLabel} ${money(plan.target)}${signed(metrics.targetPct)}${metrics.rr === null ? '' : ` · ${metrics.rr.toFixed(2)}R`}${plan.quantity > 0 ? ` · +${money(metrics.gain)}원` : ''}` });
    if (target2 !== null && plan.target2 !== null) markers.push({ y: target2, color: this.colors.target2, dashed: false, below: target2 > entry, text: `2차 청산 ${money(plan.target2)}${signed(metrics.target2Pct)}${metrics.rr2 === null ? '' : ` · ${metrics.rr2.toFixed(2)}R`}${plan.quantity > 0 ? ` · +${money(gainAt(plan.target2))}원` : ''}` });
    return new ZoneRenderer({ bands, markers, back: this.colors.labelBack });
  }
}
