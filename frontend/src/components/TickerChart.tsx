import { useEffect, useRef, useState } from 'react';
import { CandlestickSeries, ColorType, CrosshairMode, HistogramSeries, LineSeries, LineStyle, createChart, type LogicalRange, type MouseEventParams, type Time } from 'lightweight-charts';
import { BarsResponse, ChartBar } from '../lib/api';
import { compactVolume, won } from '../lib/format';
import { MeasureBar, MeasureRange, MeasureTool } from '../lib/measure';
import { alignTick, PositionLevel, PositionPlan, PositionZones } from '../lib/position';
import { alpha, readTokens, UI_FONT } from '../lib/tokens';

type Props = { data: BarsResponse | null; light: boolean; plan: PositionPlan | null; kind: string; onPlanChange: (plan: PositionPlan) => void; measuring: boolean; onLastBarChange?: (bar: ChartBar | null) => void };

// 오버레이·MACD는 가격 방향이 아니라 서로를 구분하는 색이라 상승/하락 토큰을 쓰면 안 된다.
const SERIES = ['#f5c451', '#5ba7ff', '#c08aff', '#39c6b5', '#ff8f70'];

export default function TickerChart({ data, light, plan, kind, onPlanChange, measuring, onLastBarChange }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [legend, setLegend] = useState<ChartBar | null>(null);
  // 데이터 전체의 마지막 봉이 아니라 지금 화면(줌·스크롤)에 보이는 범위의 마지막 봉을 가리킨다.
  const [lastBar, setLastBar] = useState<ChartBar | null>(null);
  const lastBarChangeRef = useRef(onLastBarChange);
  lastBarChangeRef.current = onLastBarChange;
  // "포지션" 버튼의 기본 진입가도 이 화면 표시 범위 마지막 봉을 써야 해서 부모에도 알린다.
  useEffect(() => { lastBarChangeRef.current?.(lastBar); }, [lastBar]);
  // 차트는 data·light에만 반응해 다시 만든다. 드래그 중 매 프레임 바뀌는 계획 값은 ref로 읽어 재생성을 피한다.
  const planRef = useRef(plan);
  planRef.current = plan;
  const kindRef = useRef(kind);
  kindRef.current = kind;
  const changeRef = useRef(onPlanChange);
  changeRef.current = onPlanChange;
  const zonesRef = useRef<PositionZones | null>(null);
  useEffect(() => { zonesRef.current?.update(); }, [plan]);
  // 구간 측정: 클릭으로 고른 봉 최대 2개. 세 번째 클릭은 새로 첫 봉부터 다시 잰다.
  const [measurePoints, setMeasurePoints] = useState<MeasureBar[]>([]);
  const measureRangeRef = useRef<MeasureRange | null>(null);
  measureRangeRef.current = null;
  if (measurePoints.length === 2 && data) {
    const [a, b] = measurePoints;
    const lo = Math.min(a.index, b.index), hi = Math.max(a.index, b.index);
    let low = Infinity, high = -Infinity;
    for (let i = lo; i <= hi; i += 1) { const bar = data.bars[i]; if (bar.low < low) low = bar.low; if (bar.high > high) high = bar.high; }
    measureRangeRef.current = { a, b, low, high };
  }
  const measuringRef = useRef(measuring);
  measuringRef.current = measuring;
  const measureToolRef = useRef<MeasureTool | null>(null);
  useEffect(() => { measureToolRef.current?.update(); }, [measurePoints]);
  useEffect(() => { if (!measuring) setMeasurePoints([]); }, [measuring]);
  useEffect(() => {
    if (!ref.current || !data || !data.bars.length) return;
    const t = readTokens();
    const chart = createChart(ref.current, { autoSize: true, layout: { background: { type: ColorType.Solid, color: t.surface }, textColor: t.text3, fontFamily: UI_FONT, fontSize: 12, attributionLogo: true, panes: { separatorColor: t.line, separatorHoverColor: t.line2, enableResize: true } }, grid: { vertLines: { color: t.line }, horzLines: { color: t.line } }, crosshair: { mode: CrosshairMode.Normal }, localization: { locale: 'ko-KR', dateFormat: 'yyyy-MM-dd' } });
    const candles = chart.addSeries(CandlestickSeries, { priceScaleId: 'right', priceFormat: { type: 'custom', minMove: 1, formatter: won }, upColor: t.up, downColor: t.down, wickUpColor: t.up, wickDownColor: t.down, borderVisible: false }, 0);
    candles.setData(data.bars.map(bar => ({ time: bar.time, open: bar.open, high: bar.high, low: bar.low, close: bar.close, color: bar.halted ? t.text3 : undefined, wickColor: bar.halted ? t.text3 : undefined, borderColor: bar.halted ? t.text3 : undefined })));
    const volume = chart.addSeries(HistogramSeries, { priceScaleId: 'volume', priceFormat: { type: 'volume' }, priceLineVisible: false, lastValueVisible: false }, 0);
    volume.setData(data.bars.map(bar => ({ time: bar.time, value: bar.volume, color: bar.close >= bar.open ? alpha(t.up, .47) : alpha(t.down, .47) })));
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    if (data.volume_ma) { const volumeMa = chart.addSeries(LineSeries, { priceScaleId: 'volume', color: t.ok, lineWidth: 2, priceLineVisible: false, lastValueVisible: false }, 0); volumeMa.setData(data.volume_ma); }
    chart.priceScale('right', 0).applyOptions({ scaleMargins: { top: 0.05, bottom: 0.22 } });
    Object.entries(data.overlays).forEach(([key, points], index) => { const line = chart.addSeries(LineSeries, { title: key.toUpperCase(), priceScaleId: 'right', color: SERIES[index % SERIES.length], lineWidth: 1 }, 0); line.setData(points); });
    if (data.bb) for (const key of ['upper', 'lower']) { const line = chart.addSeries(LineSeries, { priceScaleId: 'right', color: t.text3, lineWidth: 1, lineStyle: LineStyle.Dashed }, 0); line.setData(data.bb[key] || []); }
    if (data.rsi) {
      const pane = chart.addPane();
      const line = pane.addSeries(LineSeries, { priceScaleId: 'right', color: SERIES[2], lineWidth: 2 });
      line.setData(data.rsi);
    }
    if (data.macd) {
      const pane = chart.addPane();
      const hist = pane.addSeries(HistogramSeries, { priceScaleId: 'right', color: t.text3 });
      hist.setData(data.macd.hist || []);
      const line = pane.addSeries(LineSeries, { priceScaleId: 'right', color: SERIES[1] });
      line.setData(data.macd.macd || []);
      const signal = pane.addSeries(LineSeries, { priceScaleId: 'right', color: SERIES[0] });
      signal.setData(data.macd.signal || []);
    }
    const container = ref.current;
    const zones = new PositionZones(() => planRef.current, light);
    candles.attachPrimitive(zones);
    zonesRef.current = zones;
    const measureTool = new MeasureTool(() => measureRangeRef.current, { up: t.up, down: t.down, back: t.surface2 });
    candles.attachPrimitive(measureTool);
    measureToolRef.current = measureTool;
    let dragging: PositionLevel | null = null;
    const paneY = (event: MouseEvent) => event.clientY - container.getBoundingClientRect().top;
    const onDown = (event: MouseEvent) => {
      const level = zones.levelAt(paneY(event));
      if (!level) return;
      dragging = level;
      zones.setDragging(level);
      // 드래그 중에는 차트가 같은 포인터로 스크롤·확대되지 않게 막는다.
      chart.applyOptions({ handleScroll: false, handleScale: false });
      event.preventDefault();
    };
    const onMove = (event: MouseEvent) => {
      const current = planRef.current;
      if (!dragging || !current) return;
      const price = candles.coordinateToPrice(paneY(event));
      if (price === null) return;
      changeRef.current({ ...current, [dragging]: alignTick(price, kindRef.current) });
    };
    const onUp = () => {
      if (!dragging) return;
      dragging = null;
      zones.setDragging(null);
      chart.applyOptions({ handleScroll: true, handleScale: true });
    };
    container.addEventListener('mousedown', onDown);
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    const onCrosshair = (param: MouseEventParams<Time>) => { if (typeof param.time !== 'string') return; const point = data.bars.find(bar => bar.time === param.time); setLegend(point || null); };
    chart.subscribeCrosshairMove(onCrosshair);
    const onClick = (param: MouseEventParams<Time>) => {
      if (!measuringRef.current || typeof param.time !== 'string') return;
      const index = data.bars.findIndex(bar => bar.time === param.time);
      if (index === -1) return;
      const bar = data.bars[index];
      setMeasurePoints(current => current.length >= 2 ? [{ time: bar.time, close: bar.close, index }] : [...current, { time: bar.time, close: bar.close, index }]);
    };
    chart.subscribeClick(onClick);
    const timeScale = chart.timeScale();
    const onVisibleRange = (range: LogicalRange | null) => {
      if (!range) { setLastBar(data.bars.at(-1) ?? null); return; }
      // 봉 i는 논리 좌표 [i-0.5, i+0.5]를 차지한다. floor는 절반 넘게 보이는 다음 봉을 놓쳐 한 봉 이전을
      // "마지막 봉"으로 잘못 고르므로, range.to가 속한 봉을 고르려면 반올림해야 한다.
      const index = Math.max(0, Math.min(data.bars.length - 1, Math.round(range.to)));
      setLastBar(data.bars[index] ?? null);
    };
    timeScale.subscribeVisibleLogicalRangeChange(onVisibleRange);
    onVisibleRange(timeScale.getVisibleLogicalRange());
    return () => {
      container.removeEventListener('mousedown', onDown);
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      chart.unsubscribeCrosshairMove(onCrosshair);
      chart.unsubscribeClick(onClick);
      timeScale.unsubscribeVisibleLogicalRangeChange(onVisibleRange);
      zonesRef.current = null;
      measureToolRef.current = null;
      chart.remove();
      setLegend(null);
      setLastBar(null);
      setMeasurePoints([]);
    };
  }, [data, light]);
  // 크로스헤어가 가리키는 봉에서 화면에 보이는 마지막 봉까지 가격이 얼마나 움직였는지 — "여기서 들어갔으면 지금은?" 감을 잡는 용도.
  const changePct = legend && lastBar && legend.close ? (lastBar.close / legend.close - 1) * 100 : null;
  // 부모(.chart-box)의 높이가 내용에 따라 늘어나므로 height:100%는 확정 높이가 없어 무너진다. 절대 배치로 홀더를 그대로 채운다.
  return <div style={{ position: 'absolute', inset: 0 }}><div className="chart-legend">
    {lastBar && <div className="legend-row">
      <span className="badge">마지막봉</span>
      <span className="mono">{lastBar.time}</span>
      <span className="legend-field"><i>O</i>{won(lastBar.open)}</span>
      <span className="legend-field"><i>H</i>{won(lastBar.high)}</span>
      <span className="legend-field"><i>L</i>{won(lastBar.low)}</span>
      <span className={`legend-field ${lastBar.close >= lastBar.open ? 'up' : 'down'}`}><i>C</i>{won(lastBar.close)}</span>
      <span className="legend-field"><i>V</i>{compactVolume(lastBar.volume)}</span>
    </div>}
    <div className="legend-row">
      {legend ? <>
        <span className="badge" data-tone="live">마우스</span>
        <span className="mono">{legend.time}</span>
        <span className="legend-field"><i>O</i>{won(legend.open)}</span>
        <span className="legend-field"><i>H</i>{won(legend.high)}</span>
        <span className="legend-field"><i>L</i>{won(legend.low)}</span>
        <span className={`legend-field ${legend.close >= legend.open ? 'up' : 'down'}`}><i>C</i>{won(legend.close)}</span>
        <span className="legend-field"><i>V</i>{compactVolume(legend.volume)}</span>
        {changePct != null && <span className="badge" data-tone={changePct >= 0 ? 'up' : 'down'}>→마지막봉 {changePct >= 0 ? '+' : ''}{changePct.toFixed(2)}%</span>}
      </> : <span className="subtle">크로스헤어를 움직여 OHLCV 확인</span>}
    </div>
  </div><div ref={ref} style={{ width: '100%', height: '100%' }} /></div>;
}
