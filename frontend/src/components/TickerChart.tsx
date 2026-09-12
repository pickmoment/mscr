import { useEffect, useRef, useState } from 'react';
import { CandlestickSeries, ColorType, CrosshairMode, HistogramSeries, LineSeries, LineStyle, PriceScaleMode, createChart, type LogicalRange, type MouseEventParams, type Time } from 'lightweight-charts';
import { BarsResponse, ChartBar, ChartPlotSpec } from '../lib/api';
import { compactVolume, money } from '../lib/format';
import { marketInfo } from '../lib/market';
import { MeasureBar, MeasureMode, MeasureRange, MeasureSpan, MeasureTool, SpanTool } from '../lib/measure';
import { alignTick, PositionLevel, PositionPlan, PositionZones } from '../lib/position';
import { alpha, readTokens, UI_FONT } from '../lib/tokens';

type Props = { data: BarsResponse | null; light: boolean; plan: PositionPlan | null; kind: string; onPlanChange: (plan: PositionPlan) => void; measure: MeasureMode; plotStyles: ChartPlotSpec[]; logScale: boolean; onLastBarChange?: (bar: ChartBar | null) => void };

// 오버레이·MACD는 가격 방향이 아니라 서로를 구분하는 색이라 상승/하락 토큰을 쓰면 안 된다.
const SERIES = ['#f5c451', '#5ba7ff', '#c08aff', '#39c6b5', '#ff8f70'];
// 일봉 이상은 서버가 'yyyy-MM-dd' 문자열(BusinessDay)로 준다 — 그대로 보여준다.
// 분봉은 서버가 KST 벽시계를 그대로 UTC epoch초로 인코딩해 보낸다(라이브러리가 숫자 시간을
// 항상 UTC getter로 읽으므로) — 여기서도 UTC getter로 되풀이해 읽어야 KST 시각이 그대로 나온다.
const formatBarTime = (time: Time): string => {
  if (typeof time === 'number') {
    const d = new Date(time * 1000);
    const pad = (value: number) => String(value).padStart(2, '0');
    return `${pad(d.getUTCMonth() + 1)}/${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
  }
  if (typeof time === 'string') return time;
  return `${time.year}-${String(time.month).padStart(2, '0')}-${String(time.day).padStart(2, '0')}`;
};

type Hover = { bar: ChartBar; prevClose: number | null; x: number; y: number; width: number; height: number };

export default function TickerChart({ data, light, plan, kind, onPlanChange, measure, plotStyles, logScale, onLastBarChange }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  // 크로스헤어가 가리키는 봉 + 그 봉을 담은 툴팁을 마우스 옆 어디에 띄울지 정할 좌표/패널 크기.
  const [hover, setHover] = useState<Hover | null>(null);
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
  // 봉구간 측정: 클릭으로 고른 봉 최대 2개. 세 번째 클릭은 새로 첫 봉부터 다시 잰다.
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
  // 선구간 측정: 클릭한 가격 최대 2개. 봉구간과 같은 규칙으로 세 번째 클릭은 처음부터 다시 잰다.
  const [span, setSpan] = useState<MeasureSpan | null>(null);
  const spanRef = useRef<MeasureSpan | null>(span);
  spanRef.current = span;
  const measureRef = useRef(measure);
  measureRef.current = measure;
  const measureToolRef = useRef<MeasureTool | null>(null);
  const spanToolRef = useRef<SpanTool | null>(null);
  useEffect(() => { measureToolRef.current?.update(); }, [measurePoints]);
  useEffect(() => { spanToolRef.current?.update(); }, [span]);
  // 도구를 끄거나 다른 도구로 바꾸면 그 도구가 그리던 것도 같이 지운다.
  useEffect(() => {
    if (measure !== 'bars') setMeasurePoints([]);
    if (measure !== 'lines') setSpan(null);
  }, [measure]);
  // 수식 지표의 표시 설정. 색·판을 바꾸면 다시 그려야 하지만, 수식만 고친 경우는 새 데이터가
  // 도착할 때 한 번만 그리면 된다 — 그래서 표시에 쓰는 필드만 골라 의존성 키를 만든다.
  const plotStylesRef = useRef(plotStyles);
  plotStylesRef.current = plotStyles;
  const plotStyleKey = JSON.stringify(plotStyles.map(item => [item.id, item.label, item.pane, item.style, item.color]));
  // 로그 눈금은 차트를 다시 만들지 않고 가격 축 옵션만 바꾼다 — 껐다 켤 때마다 줌이 풀리면 못 쓴다.
  const chartRef = useRef<ReturnType<typeof createChart> | null>(null);
  useEffect(() => {
    if (!ref.current || !data || !data.bars.length) return;
    const t = readTokens();
    const chart = createChart(ref.current, { autoSize: true, layout: { background: { type: ColorType.Solid, color: t.surface }, textColor: t.text3, fontFamily: UI_FONT, fontSize: 12, attributionLogo: true, panes: { separatorColor: t.line, separatorHoverColor: t.line2, enableResize: true } }, grid: { vertLines: { color: t.line }, horzLines: { color: t.line } }, crosshair: { mode: CrosshairMode.Normal }, localization: { locale: 'ko-KR', dateFormat: 'yyyy-MM-dd' }, timeScale: { timeVisible: true, secondsVisible: false } });
    const candles = chart.addSeries(CandlestickSeries, { priceScaleId: 'right', priceFormat: { type: 'custom', minMove: marketInfo().currency === 'USD' ? 0.01 : 1, formatter: money }, upColor: t.up, downColor: t.down, wickUpColor: t.up, wickDownColor: t.down, borderVisible: false }, 0);
    candles.setData(data.bars.map(bar => ({ time: bar.time, open: bar.open, high: bar.high, low: bar.low, close: bar.close, color: bar.halted ? t.text3 : undefined, wickColor: bar.halted ? t.text3 : undefined, borderColor: bar.halted ? t.text3 : undefined })));
    const volume = chart.addSeries(HistogramSeries, { priceScaleId: 'volume', priceFormat: { type: 'volume' }, priceLineVisible: false, lastValueVisible: false }, 0);
    volume.setData(data.bars.map(bar => ({ time: bar.time, value: bar.volume, color: bar.close >= bar.open ? alpha(t.up, .47) : alpha(t.down, .47) })));
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    if (data.volume_ma) { const volumeMa = chart.addSeries(LineSeries, { priceScaleId: 'volume', color: t.ok, lineWidth: 2, priceLineVisible: false, lastValueVisible: false }, 0); volumeMa.setData(data.volume_ma); }
    chart.priceScale('right', 0).applyOptions({ scaleMargins: { top: 0.05, bottom: 0.22 } });
    chartRef.current = chart;
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
    // 같은 보조판(sub1~3)을 고른 수식 지표들은 한 판에 겹쳐 그려 서로 비교할 수 있게 한다.
    const subPanes = new Map<string, ReturnType<typeof chart.addPane>>();
    for (const plot of data.plots || []) {
      const spec = plotStylesRef.current.find(item => item.id === plot.id);
      if (!spec || !plot.points.length) continue;
      const dashed = spec.style === 'dashed' ? LineStyle.Dashed : LineStyle.Solid;
      if (spec.pane === 'price' || spec.pane === 'volume') {
        const priceScaleId = spec.pane === 'price' ? 'right' : 'volume';
        const overlay = spec.style === 'histogram'
          ? chart.addSeries(HistogramSeries, { title: spec.label, priceScaleId, color: spec.color, priceLineVisible: false, lastValueVisible: false }, 0)
          : chart.addSeries(LineSeries, { title: spec.label, priceScaleId, color: spec.color, lineWidth: 1, lineStyle: dashed, priceLineVisible: false, lastValueVisible: spec.pane === 'price' }, 0);
        overlay.setData(plot.points);
        continue;
      }
      let pane = subPanes.get(spec.pane);
      if (!pane) { pane = chart.addPane(); subPanes.set(spec.pane, pane); }
      const series = spec.style === 'histogram'
        ? pane.addSeries(HistogramSeries, { title: spec.label, priceScaleId: 'right', color: spec.color })
        : pane.addSeries(LineSeries, { title: spec.label, priceScaleId: 'right', color: spec.color, lineWidth: 2, lineStyle: dashed });
      series.setData(plot.points);
    }
    const container = ref.current;
    const zones = new PositionZones(() => planRef.current, light);
    candles.attachPrimitive(zones);
    zonesRef.current = zones;
    const measureTool = new MeasureTool(() => measureRangeRef.current, { up: t.up, down: t.down, back: t.surface2 });
    candles.attachPrimitive(measureTool);
    measureToolRef.current = measureTool;
    const spanTool = new SpanTool(() => spanRef.current, { up: t.up, down: t.down, back: t.surface2 });
    candles.attachPrimitive(spanTool);
    spanToolRef.current = spanTool;
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
    const onCrosshair = (param: MouseEventParams<Time>) => {
      if (!param.point || param.time === undefined) { setHover(null); return; }
      const index = data.bars.findIndex(bar => bar.time === param.time);
      if (index === -1) { setHover(null); return; }
      setHover({ bar: data.bars[index], prevClose: index > 0 ? data.bars[index - 1].close : null, x: param.point.x, y: param.point.y, width: container.clientWidth, height: container.clientHeight });
    };
    chart.subscribeCrosshairMove(onCrosshair);
    const onClick = (param: MouseEventParams<Time>) => {
      const mode = measureRef.current;
      if (mode === 'bars') {
        if (param.time === undefined) return;
        const index = data.bars.findIndex(bar => bar.time === param.time);
        if (index === -1) return;
        const bar = data.bars[index];
        setMeasurePoints(current => current.length >= 2 ? [{ time: bar.time, close: bar.close, index }] : [...current, { time: bar.time, close: bar.close, index }]);
        return;
      }
      // 가격 좌표는 캔들 판(0번)에서만 뜻이 있다 — RSI·MACD 판을 클릭한 y는 가격이 아니다.
      if (mode !== 'lines' || !param.point || (param.paneIndex ?? 0) !== 0) return;
      const price = candles.coordinateToPrice(param.point.y);
      if (price === null) return;
      const level = alignTick(price, kindRef.current);
      setSpan(current => current && current.second === null ? { ...current, second: level } : { first: level, second: null });
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
      chartRef.current = null;
      zonesRef.current = null;
      measureToolRef.current = null;
      spanToolRef.current = null;
      chart.remove();
      setHover(null);
      setLastBar(null);
      setMeasurePoints([]);
      setSpan(null);
    };
  }, [data, light, plotStyleKey]);
  // 보조판(RSI·MACD·수식 지표)은 음수를 담을 수 있어 로그 눈금을 적용하지 않는다. 가격 축만 바꾼다.
  // 판이 다 붙기 전에 눈금을 바꾸면 이후 addPane 이 레이아웃을 잘못 잡아 보조판이 잘린다. 그래서
  // 차트를 만드는 effect 안이 아니라 그 다음에 도는 이 effect 에서 적용하고, 차트를 다시 만드는
  // 조건(data·light·표시 설정)에도 함께 반응해 재생성 직후 다시 적용한다.
  useEffect(() => { chartRef.current?.priceScale('right', 0).applyOptions({ mode: logScale ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal }); }, [logScale, data, light, plotStyleKey]);
  // 크로스헤어가 가리키는 봉에서 화면에 보이는 마지막 봉까지 가격이 얼마나 움직였는지 — "여기서 들어갔으면 지금은?" 감을 잡는 용도.
  const toLast = hover && lastBar && hover.bar.time !== lastBar.time && hover.bar.close ? (lastBar.close / hover.bar.close - 1) * 100 : null;
  const barPct = hover && hover.prevClose ? (hover.bar.close / hover.prevClose - 1) * 100 : null;
  const signed = (value: number) => `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
  // 부모(.chart-box)의 높이가 내용에 따라 늘어나므로 height:100%는 확정 높이가 없어 무너진다. 절대 배치로 홀더를 그대로 채운다.
  return <div style={{ position: 'absolute', inset: 0 }}>
    {/* 툴팁 크기를 재지 않아도 잘리지 않도록, 커서가 패널 절반을 넘으면 반대쪽으로 뒤집어 붙인다. */}
    {hover && <div className="chart-tip" style={{ left: hover.x, top: hover.y, transform: `translate(${hover.x > hover.width / 2 ? 'calc(-100% - 16px)' : '16px'}, ${hover.y > hover.height / 2 ? 'calc(-100% - 16px)' : '16px'})` }}>
      <div className="chart-tip__head">
        <span>{formatBarTime(hover.bar.time)}</span>
        {barPct != null && <span className={barPct >= 0 ? 'up' : 'down'}>{signed(barPct)}</span>}
      </div>
      <div className="chart-tip__grid">
        <i>시가</i><b>{money(hover.bar.open)}</b>
        <i>고가</i><b>{money(hover.bar.high)}</b>
        <i>저가</i><b>{money(hover.bar.low)}</b>
        <i>종가</i><b className={hover.bar.close >= hover.bar.open ? 'up' : 'down'}>{money(hover.bar.close)}</b>
        <i>거래량</i><b>{compactVolume(hover.bar.volume)}</b>
      </div>
      {(toLast != null || hover.bar.halted) && <div className="chart-tip__foot">
        {toLast != null && <span>마지막봉까지 <b className={toLast >= 0 ? 'up' : 'down'}>{signed(toLast)}</b></span>}
        {hover.bar.halted && <span className="warn">거래정지</span>}
      </div>}
    </div>}
    <div ref={ref} style={{ width: '100%', height: '100%' }} />
  </div>;
}
