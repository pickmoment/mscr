import { useEffect, useRef, useState } from 'react';
import { CandlestickSeries, ColorType, CrosshairMode, HistogramSeries, LineSeries, LineStyle, createChart, type MouseEventParams, type Time } from 'lightweight-charts';
import { BarsResponse, ChartBar } from '../lib/api';
import { won } from '../lib/format';

export default function TickerChart({ data, light }: { data: BarsResponse | null; light: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const [legend, setLegend] = useState<ChartBar | null>(null);
  useEffect(() => {
    if (!ref.current || !data || !data.bars.length) return;
    const chart = createChart(ref.current, { autoSize: true, layout: { background: { type: ColorType.Solid, color: light ? '#ffffff' : '#10151d' }, textColor: light ? '#5f6b7a' : '#8290a4', fontFamily: "'Noto Sans KR', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif", fontSize: 12, attributionLogo: true }, grid: { vertLines: { color: light ? '#e8edf3' : '#1d2632' }, horzLines: { color: light ? '#e8edf3' : '#1d2632' } }, crosshair: { mode: CrosshairMode.Normal }, localization: { locale: 'ko-KR', dateFormat: 'yyyy-MM-dd' } });
    const candles = chart.addSeries(CandlestickSeries, { priceScaleId: 'right', priceFormat: { type: 'custom', minMove: 1, formatter: won }, upColor: '#ef4444', downColor: '#2563eb', wickUpColor: '#ef4444', wickDownColor: '#2563eb', borderVisible: false }, 0);
    candles.setData(data.bars.map(bar => ({ time: bar.time, open: bar.open, high: bar.high, low: bar.low, close: bar.close, color: bar.halted ? '#697586' : undefined, wickColor: bar.halted ? '#697586' : undefined, borderColor: bar.halted ? '#697586' : undefined })));
    const volume = chart.addSeries(HistogramSeries, { priceScaleId: 'volume', priceFormat: { type: 'volume' }, priceLineVisible: false, lastValueVisible: false }, 0);
    volume.setData(data.bars.map(bar => ({ time: bar.time, value: bar.volume, color: bar.close >= bar.open ? '#ef444477' : '#2563eb77' })));
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    if (data.volume_ma) { const volumeMa = chart.addSeries(LineSeries, { priceScaleId: 'volume', color: '#00e5a0', lineWidth: 2, priceLineVisible: false, lastValueVisible: false }, 0); volumeMa.setData(data.volume_ma); }
    chart.priceScale('right', 0).applyOptions({ scaleMargins: { top: 0.05, bottom: 0.22 } });
    const colors = ['#f5c451', '#5ba7ff', '#c08aff', '#39c6b5', '#ff8f70'];
    Object.entries(data.overlays).forEach(([key, points], index) => { const line = chart.addSeries(LineSeries, { title: key.toUpperCase(), priceScaleId: 'right', color: colors[index % colors.length], lineWidth: 1 }, 0); line.setData(points); });
    if (data.bb) for (const key of ['upper', 'lower']) { const line = chart.addSeries(LineSeries, { priceScaleId: 'right', color: '#8794a8', lineWidth: 1, lineStyle: LineStyle.Dashed }, 0); line.setData(data.bb[key] || []); }
    if (data.rsi) {
      const pane = chart.addPane();
      const line = pane.addSeries(LineSeries, { priceScaleId: 'right', color: '#e99eff', lineWidth: 2 });
      line.setData(data.rsi);
    }
    if (data.macd) {
      const pane = chart.addPane();
      const hist = pane.addSeries(HistogramSeries, { priceScaleId: 'right', color: '#5f738e' });
      hist.setData(data.macd.hist || []);
      const line = pane.addSeries(LineSeries, { priceScaleId: 'right', color: '#5ba7ff' });
      line.setData(data.macd.macd || []);
      const signal = pane.addSeries(LineSeries, { priceScaleId: 'right', color: '#f5c451' });
      signal.setData(data.macd.signal || []);
    }
    const onCrosshair = (param: MouseEventParams<Time>) => { if (typeof param.time !== 'string') return; const point = data.bars.find(bar => bar.time === param.time); setLegend(point || null); };
    chart.subscribeCrosshairMove(onCrosshair);
    return () => { chart.unsubscribeCrosshairMove(onCrosshair); chart.remove(); setLegend(null); };
  }, [data, light]);
  return <div style={{ height: '100%', position: 'relative' }}><div className="mono subtle" style={{ position: 'absolute', zIndex: 2, top: 8, left: 12 }}>{legend ? `${legend.time}  O ${won(legend.open)}  H ${won(legend.high)}  L ${won(legend.low)}  C ${won(legend.close)}  V ${legend.volume.toLocaleString()}` : '크로스헤어를 움직여 OHLCV 확인'}</div><div ref={ref} style={{ width: '100%', height: '100%' }} /></div>;
}
