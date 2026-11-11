// Shorts Studio template film. The backup writer inserts a validated JSON plan as PLAN below.
// Twelve scene types share one art direction; every motion is driven by useCurrentFrame().
import React from 'react';
import {AbsoluteFill, Easing, Sequence, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {noise2D} from '@remotion/noise';

type Item = {label: string; value?: number; text?: string};
type Scene = {type: string; title: string; subtitle?: string; text?: string; highlight?: string;
  value?: number; unit?: string; items?: Item[]; source?: string; icon?: string};
type Plan = {title: string; language: string; palette: string; scenes: Scene[]};

const PLAN: Plan = __PLAN__;

type Colors = {bg1: string; bg2: string; a: string; b: string; c: string; ink: string; soft: string};
const PALETTES: Record<string, Colors> = {
  aurora: {bg1: '#070b1f', bg2: '#1c1557', a: '#7c5cff', b: '#2de2c4', c: '#ff5c9a', ink: '#f6f7ff', soft: '#aab3d6'},
  sunset: {bg1: '#190611', bg2: '#4a1630', a: '#ff7a45', b: '#ffd166', c: '#ef476f', ink: '#fff8f0', soft: '#e6c2b4'},
  ocean: {bg1: '#03121f', bg2: '#0b3a5c', a: '#2ec4ff', b: '#7cf2d6', c: '#ffd166', ink: '#f0fbff', soft: '#a7c7d9'},
  forest: {bg1: '#06140d', bg2: '#17432c', a: '#5ee08c', b: '#d9f99d', c: '#ffb347', ink: '#f3fff6', soft: '#a9cdb6'},
  mono: {bg1: '#0b0b0d', bg2: '#2a2a31', a: '#f5f5f5', b: '#ffcc00', c: '#9aa0a6', ink: '#ffffff', soft: '#b9bcc2'},
  candy: {bg1: '#1b0a2a', bg2: '#3d1466', a: '#ff6ad5', b: '#8ef6ff', c: '#ffe66d', ink: '#fff6fd', soft: '#d9b8e8'},
};
const P = PALETTES[PLAN.palette] || PALETTES.aurora;
const RTL = PLAN.language.toLowerCase().startsWith('ar');
const FONT = RTL ? "'Noto Sans Arabic', 'Noto Naskh Arabic', 'Noto Sans', 'DejaVu Sans', sans-serif"
  : "'Noto Sans', 'DejaVu Sans', sans-serif";
const EMOJI = "'Noto Color Emoji', 'Apple Color Emoji', sans-serif";
const out = Easing.bezier(0.16, 1, 0.3, 1);
const ramp = (f: number, a: number, b: number, x = 0, y = 1) =>
  interpolate(f, [a, b], [x, y], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: out});
const fitSize = (text: string | undefined, max: number, min: number, chars: number) =>
  Math.round(Math.max(min, Math.min(max, max * Math.sqrt(chars / Math.max(chars, (text || '').length)))));
const fmt = (n: number) => {
  const abs = Math.abs(n);
  const digits = abs >= 100 || Number.isInteger(n) ? 0 : 1;
  return n.toLocaleString('en-US', {maximumFractionDigits: digits, minimumFractionDigits: digits});
};

const Background: React.FC = () => {
  const f = useCurrentFrame();
  const blobs = [P.a, P.b, P.c].map((color, i) => {
    const x = 540 + noise2D('x' + i, f / 260, i) * 520;
    const y = 960 + noise2D('y' + i, i, f / 300) * 760;
    return <div key={i} style={{position: 'absolute', left: x - 520, top: y - 520, width: 1040, height: 1040,
      borderRadius: '50%', background: `radial-gradient(circle, ${color}38 0%, ${color}00 62%)`}}/>;
  });
  return <AbsoluteFill style={{background: `linear-gradient(160deg, ${P.bg1} 0%, ${P.bg2} 55%, ${P.bg1} 100%)`}}>
    {blobs}
    <AbsoluteFill style={{opacity: 0.07, backgroundImage: `linear-gradient(${P.ink} 1px, transparent 1px),` +
      `linear-gradient(90deg, ${P.ink} 1px, transparent 1px)`, backgroundSize: '90px 90px',
      transform: `translateY(${(f * 0.6) % 90}px)`}}/>
    <AbsoluteFill style={{background: 'radial-gradient(ellipse at center, transparent 50%, rgba(0,0,0,0.55) 100%)'}}/>
  </AbsoluteFill>;
};

const clean = (w: string) => w.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');

const Words: React.FC<{text: string; size: number; delay?: number; highlight?: string; color?: string;
  weight?: number; align?: 'center' | 'flex-start'}> = ({text, size, delay = 0, highlight, color = P.ink, weight = 800, align = 'center'}) => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const marked = new Set((highlight || '').split(/\s+/).map(clean).filter(Boolean));
  return <div style={{display: 'flex', flexWrap: 'wrap', justifyContent: align, direction: RTL ? 'rtl' : 'ltr',
    columnGap: size * 0.26, rowGap: size * 0.1, lineHeight: 1.12}}>
    {text.split(/\s+/).filter(Boolean).map((word, i) => {
      const s = spring({frame: f - delay - i * 3, fps, config: {damping: 16, stiffness: 130}});
      const hot = marked.has(clean(word));
      return <span key={i} style={{display: 'inline-block', fontSize: size, fontWeight: weight,
        color: hot ? P.b : color, opacity: Math.min(1, s * 1.3),
        transform: `translateY(${(1 - s) * size * 0.55}px) scale(${0.92 + 0.08 * s})`,
        textShadow: hot ? `0 0 36px ${P.b}77` : '0 6px 30px rgba(0,0,0,0.35)'}}>{word}</span>;
    })}
  </div>;
};

const Fade: React.FC<{delay: number; children: React.ReactNode; style?: React.CSSProperties}> = ({delay, children, style}) => {
  const f = useCurrentFrame();
  return <div style={{opacity: ramp(f, delay, delay + 14), transform: `translateY(${ramp(f, delay, delay + 18, 30, 0)}px)`, ...style}}>{children}</div>;
};

const Sub: React.FC<{text?: string; delay: number}> = ({text, delay}) => text ?
  <Fade delay={delay}><div style={{fontSize: fitSize(text, 46, 32, 60), color: P.soft, textAlign: 'center',
    fontWeight: 500, lineHeight: 1.3, direction: RTL ? 'rtl' : 'ltr'}}>{text}</div></Fade> : null;

const Kicker: React.FC<{text: string}> = ({text}) => {
  const f = useCurrentFrame();
  return <div style={{alignSelf: 'center', padding: '12px 30px', borderRadius: 999, border: `3px solid ${P.a}`,
    color: P.a, fontSize: 34, fontWeight: 700, letterSpacing: RTL ? 0 : 3, opacity: ramp(f, 0, 12),
    transform: `scale(${ramp(f, 0, 16, 0.7, 1)})`, direction: RTL ? 'rtl' : 'ltr'}}>{text}</div>;
};

const Stack: React.FC<{children: React.ReactNode; gap?: number; justify?: string}> = ({children, gap = 56, justify = 'center'}) =>
  <AbsoluteFill style={{padding: '170px 90px 330px', display: 'flex', flexDirection: 'column', justifyContent: justify, gap}}>{children}</AbsoluteFill>;

const Icon: React.FC<{icon?: string; size?: number}> = ({icon, size = 190}) => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  if (!icon) return null;
  const s = spring({frame: f, fps, config: {damping: 9, stiffness: 110}});
  return <div style={{alignSelf: 'center', fontFamily: EMOJI, fontSize: size, lineHeight: 1,
    transform: `scale(${s}) rotate(${(1 - s) * -25 + Math.sin(f / 18) * 4}deg)`}}>{icon}</div>;
};

const Hook: React.FC<{scene: Scene}> = ({scene}) => {
  const f = useCurrentFrame();
  return <Stack>
    <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center'}}>
      {[0, 1, 2].map((i) => {
        const t = ((f + i * 22) % 66) / 66;
        return <div key={i} style={{position: 'absolute', width: 300 + t * 760, height: 300 + t * 760,
          borderRadius: '50%', border: `4px solid ${i % 2 ? P.b : P.a}`, opacity: (1 - t) * 0.45}}/>;
      })}
    </AbsoluteFill>
    <Icon icon={scene.icon}/>
    <Words text={scene.title} size={fitSize(scene.title, 118, 70, 22)} highlight={scene.highlight} delay={4}/>
    <Sub text={scene.subtitle} delay={18}/>
  </Stack>;
};

const Statement: React.FC<{scene: Scene}> = ({scene}) => {
  const f = useCurrentFrame();
  return <Stack>
    <Icon icon={scene.icon} size={150}/>
    <Words text={scene.title} size={fitSize(scene.title, 100, 62, 30)} highlight={scene.highlight}/>
    <div style={{alignSelf: 'center', height: 10, borderRadius: 10, width: ramp(f, 12, 40, 0, 420),
      background: `linear-gradient(90deg, ${P.a}, ${P.b})`}}/>
    <Sub text={scene.subtitle || scene.text} delay={20}/>
  </Stack>;
};

const NumberScene: React.FC<{scene: Scene}> = ({scene}) => {
  const f = useCurrentFrame();
  const value = scene.value ?? 0;
  const p = ramp(f, 6, 50);
  const r = 330;
  const c = 2 * Math.PI * r;
  const text = fmt(value * p);
  return <Stack gap={40}>
    <div style={{position: 'relative', alignSelf: 'center', width: 760, height: 760}}>
      <svg width={760} height={760} style={{position: 'absolute', transform: 'rotate(-90deg)'}}>
        <circle cx={380} cy={380} r={r} fill="none" stroke={P.ink + '18'} strokeWidth={26}/>
        <circle cx={380} cy={380} r={r} fill="none" stroke={P.a} strokeWidth={26} strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c * (1 - p * 0.82)}/>
      </svg>
      <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center', flexDirection: 'column'}}>
        <div style={{fontSize: Math.min(190, Math.round(500 / (0.6 * (fmt(value).length + (scene.unit || '').length * 0.5)))), fontWeight: 900, color: P.ink,
          direction: 'ltr', textShadow: `0 0 50px ${P.a}66`}}>{text}<span style={{color: P.b, fontSize: '0.5em'}}>{scene.unit}</span></div>
      </AbsoluteFill>
    </div>
    <Words text={scene.title} size={fitSize(scene.title, 76, 50, 34)} delay={10}/>
    <Sub text={scene.subtitle} delay={24}/>
  </Stack>;
};

const Bars: React.FC<{scene: Scene}> = ({scene}) => {
  const f = useCurrentFrame();
  const items = scene.items || [];
  const max = Math.max(...items.map((x) => Math.abs(x.value || 0)), 1);
  const colors = [P.a, P.b, P.c];
  return <Stack gap={44}>
    <Words text={scene.title} size={fitSize(scene.title, 80, 52, 30)}/>
    {items.map((item, i) => {
      const p = ramp(f, 10 + i * 7, 46 + i * 7);
      return <div key={i} style={{direction: RTL ? 'rtl' : 'ltr', opacity: ramp(f, 6 + i * 7, 18 + i * 7)}}>
        <div style={{display: 'flex', justifyContent: 'space-between', fontSize: 40, color: P.ink, fontWeight: 700, marginBottom: 14}}>
          <span>{item.label}</span><span style={{color: colors[i % 3], direction: 'ltr'}}>{fmt((item.value || 0) * p)}{scene.unit}</span>
        </div>
        <div style={{height: 54, borderRadius: 28, background: P.ink + '14', overflow: 'hidden'}}>
          <div style={{height: '100%', borderRadius: 28, width: `${(Math.abs(item.value || 0) / max) * 100 * p}%`,
            background: `linear-gradient(90deg, ${colors[i % 3]}, ${colors[(i + 1) % 3]})`,
            marginLeft: RTL ? 'auto' : 0, boxShadow: `0 0 30px ${colors[i % 3]}55`}}/>
        </div>
      </div>;
    })}
    <Sub text={scene.subtitle} delay={30}/>
  </Stack>;
};

const Donut: React.FC<{scene: Scene}> = ({scene}) => {
  const f = useCurrentFrame();
  const value = Math.max(0, Math.min(100, scene.value ?? 0));
  const p = ramp(f, 6, 52);
  const r = 280;
  const c = 2 * Math.PI * r;
  return <Stack gap={50}>
    <Words text={scene.title} size={fitSize(scene.title, 80, 52, 30)}/>
    <div style={{position: 'relative', alignSelf: 'center', width: 640, height: 640}}>
      <svg width={640} height={640} style={{position: 'absolute', transform: `rotate(${-90 + (1 - p) * -40}deg)`}}>
        <circle cx={320} cy={320} r={r} fill="none" stroke={P.ink + '16'} strokeWidth={80}/>
        <circle cx={320} cy={320} r={r} fill="none" stroke={P.b} strokeWidth={80}
          strokeDasharray={`${c * value / 100 * p} ${c}`}/>
      </svg>
      <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center'}}>
        <div style={{fontSize: 150, fontWeight: 900, color: P.ink, direction: 'ltr'}}>{Math.round(value * p)}<span style={{fontSize: 80, color: P.b}}>%</span></div>
      </AbsoluteFill>
    </div>
    <Sub text={scene.subtitle} delay={22}/>
  </Stack>;
};

const Timeline: React.FC<{scene: Scene}> = ({scene}) => {
  const f = useCurrentFrame();
  const items = scene.items || [];
  const step = 34 / Math.max(1, items.length - 1);
  return <Stack gap={50}>
    <Words text={scene.title} size={fitSize(scene.title, 80, 52, 30)}/>
    <div style={{position: 'relative', direction: RTL ? 'rtl' : 'ltr', paddingInlineStart: 90, display: 'flex', flexDirection: 'column', gap: 46}}>
      <div style={{position: 'absolute', insetInlineStart: 28, top: 20, bottom: 20, width: 8, borderRadius: 8, background: P.ink + '1c'}}/>
      <div style={{position: 'absolute', insetInlineStart: 28, top: 20, width: 8, borderRadius: 8,
        height: `calc(${ramp(f, 8, 50, 0, 100)}% - 40px)`, background: `linear-gradient(${P.a}, ${P.b})`}}/>
      {items.map((item, i) => {
        const d = 10 + i * step;
        return <div key={i} style={{position: 'relative', opacity: ramp(f, d, d + 12), transform: `translateX(${ramp(f, d, d + 16, RTL ? -40 : 40, 0)}px)`}}>
          <div style={{position: 'absolute', insetInlineStart: -80, top: 8, width: 44, height: 44, borderRadius: '50%',
            background: P.bg1, border: `8px solid ${i % 2 ? P.b : P.a}`}}/>
          <div style={{fontSize: 52, fontWeight: 900, color: i % 2 ? P.b : P.a, direction: 'ltr', textAlign: RTL ? 'right' : 'left'}}>{item.label}</div>
          {item.text ? <div style={{fontSize: fitSize(item.text, 40, 30, 50), color: P.ink, lineHeight: 1.3}}>{item.text}</div> : null}
        </div>;
      })}
    </div>
  </Stack>;
};

const ListScene: React.FC<{scene: Scene; numbered?: boolean}> = ({scene, numbered}) => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const items = scene.items || [];
  return <Stack gap={34}>
    <Words text={scene.title} size={fitSize(scene.title, 80, 52, 30)}/>
    {items.map((item, i) => {
      const s = spring({frame: f - 10 - i * 8, fps, config: {damping: 15}});
      const color = [P.a, P.b, P.c][i % 3];
      return <div key={i} style={{display: 'flex', alignItems: 'center', gap: 34, direction: RTL ? 'rtl' : 'ltr',
        padding: '30px 36px', borderRadius: 34, background: P.ink + '0d', border: `2px solid ${color}55`,
        opacity: Math.min(1, s * 1.4), transform: `translateX(${(1 - s) * (RTL ? -160 : 160)}px)`}}>
        <div style={{flex: '0 0 auto', width: 84, height: 84, borderRadius: numbered ? '50%' : 24, background: color,
          color: P.bg1, fontSize: 46, fontWeight: 900, display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
          {numbered ? i + 1 : '✓'}</div>
        <div style={{fontSize: fitSize(item.label + (item.text || ''), 46, 32, 40), color: P.ink, fontWeight: 700, lineHeight: 1.25}}>
          {item.label}{item.text ? <div style={{fontSize: '0.75em', color: P.soft, fontWeight: 500}}>{item.text}</div> : null}</div>
      </div>;
    })}
  </Stack>;
};

const Compare: React.FC<{scene: Scene}> = ({scene}) => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const [left, right] = scene.items || [];
  const card = (item: Item, i: number) => {
    const s = spring({frame: f - 8 - i * 10, fps, config: {damping: 14}});
    const color = i ? P.b : P.a;
    return <div style={{padding: '44px 48px', borderRadius: 40, background: `linear-gradient(135deg, ${color}2a, ${P.ink}08)`,
      border: `3px solid ${color}`, opacity: Math.min(1, s * 1.3), transform: `translateY(${(1 - s) * (i ? 120 : -120)}px)`,
      direction: RTL ? 'rtl' : 'ltr'}}>
      <div style={{fontSize: 58, fontWeight: 900, color}}>{item.label}</div>
      {item.text ? <div style={{fontSize: fitSize(item.text, 42, 30, 60), color: P.ink, marginTop: 12, lineHeight: 1.3}}>{item.text}</div> : null}
    </div>;
  };
  return <Stack gap={34}>
    <Words text={scene.title} size={fitSize(scene.title, 78, 52, 30)}/>
    {left ? card(left, 0) : null}
    <div style={{alignSelf: 'center', width: 120, height: 120, borderRadius: '50%', background: P.c, color: P.bg1,
      fontWeight: 900, fontSize: 48, display: 'flex', alignItems: 'center', justifyContent: 'center',
      transform: `scale(${ramp(f, 14, 26)}) rotate(${ramp(f, 14, 30, -180, 0)}deg)`}}>VS</div>
    {right ? card(right, 1) : null}
  </Stack>;
};

const Quote: React.FC<{scene: Scene}> = ({scene}) => {
  const f = useCurrentFrame();
  return <Stack gap={40}>
    <div style={{alignSelf: 'center', fontSize: 300, lineHeight: 0.6, color: P.a, fontWeight: 900, fontFamily: 'Georgia, serif',
      opacity: ramp(f, 0, 14), transform: `scale(${ramp(f, 0, 20, 1.6, 1)})`}}>“</div>
    <Words text={scene.title} size={fitSize(scene.title, 74, 46, 60)} weight={600} delay={6} highlight={scene.highlight}/>
    {scene.source ? <Fade delay={28}><div style={{textAlign: 'center', color: P.b, fontSize: 40, fontWeight: 700,
      direction: RTL ? 'rtl' : 'ltr'}}>— {scene.source}</div></Fade> : null}
  </Stack>;
};

const Orbit: React.FC<{scene: Scene}> = ({scene}) => {
  const f = useCurrentFrame();
  const items = scene.items || [];
  const cx = 450, cy = 470, rx = 320, ry = 330;
  return <Stack gap={30}>
    <div style={{position: 'relative', width: 900, height: 940, alignSelf: 'center'}}>
      <svg width={900} height={940} style={{position: 'absolute'}}>
        <ellipse cx={cx} cy={cy} rx={rx} ry={ry} fill="none" stroke={P.ink + '22'} strokeWidth={3} strokeDasharray="10 14"/>
        {items.map((_, i) => {
          const a = (i / items.length) * Math.PI * 2 + f / 120 - Math.PI / 2;
          return <line key={i} x1={cx} y1={cy} x2={cx + Math.cos(a) * rx} y2={cy + Math.sin(a) * ry}
            stroke={P.a} strokeOpacity={0.35 * ramp(f, 14 + i * 5, 28 + i * 5)} strokeWidth={3}/>;
        })}
      </svg>
      <div style={{position: 'absolute', left: cx - 190, top: cy - 190, width: 380, height: 380, borderRadius: '50%',
        background: `radial-gradient(circle at 35% 30%, ${P.a}, ${P.bg2})`, boxShadow: `0 0 90px ${P.a}66`,
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 40, textAlign: 'center',
        transform: `scale(${ramp(f, 0, 18, 0.4, 1)})`}}>
        <div style={{fontSize: fitSize(scene.title, 54, 34, 18), fontWeight: 900, color: P.ink, lineHeight: 1.15,
          direction: RTL ? 'rtl' : 'ltr'}}>{scene.title}</div>
      </div>
      {items.map((item, i) => {
        const a = (i / items.length) * Math.PI * 2 + f / 120 - Math.PI / 2;
        const x = cx + Math.cos(a) * rx, y = cy + Math.sin(a) * ry;
        const color = [P.b, P.c, P.a][i % 3];
        return <div key={i} style={{position: 'absolute', left: x - 125, top: y - 60, width: 250, minHeight: 120,
          borderRadius: 30, background: P.bg1 + 'ee', border: `3px solid ${color}`, color: P.ink, fontWeight: 800,
          fontSize: fitSize(item.label, 38, 26, 14), display: 'flex', alignItems: 'center', justifyContent: 'center',
          textAlign: 'center', padding: 16, opacity: ramp(f, 14 + i * 5, 26 + i * 5),
          transform: `scale(${ramp(f, 14 + i * 5, 30 + i * 5, 0.5, 1)})`, direction: RTL ? 'rtl' : 'ltr'}}>{item.label}</div>;
      })}
    </div>
    <Sub text={scene.subtitle} delay={30}/>
  </Stack>;
};

const Outro: React.FC<{scene: Scene}> = ({scene}) => {
  const f = useCurrentFrame();
  const pulse = 1 + Math.sin(f / 8) * 0.03;
  return <Stack>
    {scene.subtitle ? <Kicker text={scene.subtitle}/> : null}
    <Icon icon={scene.icon} size={160}/>
    <div style={{transform: `scale(${pulse})`}}>
      <Words text={scene.title} size={fitSize(scene.title, 104, 64, 26)} highlight={scene.highlight} delay={6}/>
    </div>
    <div style={{alignSelf: 'center', display: 'flex', gap: 18}}>
      {[P.a, P.b, P.c].map((color, i) => <div key={i} style={{width: 26, height: 26, borderRadius: '50%', background: color,
        transform: `scale(${ramp(f, 20 + i * 5, 32 + i * 5)}) translateY(${Math.sin(f / 7 + i) * 10}px)`}}/>)}
    </div>
  </Stack>;
};

const SCENES: Record<string, React.FC<{scene: Scene}>> = {
  hook: Hook, statement: Statement, number: NumberScene, bars: Bars, donut: Donut, timeline: Timeline,
  list: ListScene, steps: (props) => <ListScene {...props} numbered/>, compare: Compare, quote: Quote,
  orbit: Orbit, outro: Outro,
};

const Shell: React.FC<{index: number; length: number; children: React.ReactNode}> = ({index, length, children}) => {
  const f = useCurrentFrame();
  const enter = ramp(f, 0, 16);
  const exit = interpolate(f, [length - 10, length], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.in(Easing.cubic)});
  const sideways = index % 2 === 0;
  const tx = sideways ? (1 - enter) * 140 - exit * 140 : 0;
  const scale = sideways ? 1 : 1.08 - 0.08 * enter + exit * 0.06;
  return <AbsoluteFill style={{opacity: enter * (1 - exit), transform: `translateX(${tx}px) scale(${scale})`}}>{children}</AbsoluteFill>;
};

export default function Film() {
  const {durationInFrames} = useVideoConfig();
  const scenes = PLAN.scenes;
  const each = Math.floor(durationInFrames / scenes.length);
  return <AbsoluteFill style={{fontFamily: FONT, color: P.ink}}>
    <Background/>
    {scenes.map((scene, i) => {
      const length = i === scenes.length - 1 ? durationInFrames - each * i : each;
      const Component = SCENES[scene.type] || Statement;
      return <Sequence key={i} from={each * i} durationInFrames={length}>
        <Shell index={i} length={length}><Component scene={scene}/></Shell>
      </Sequence>;
    })}
  </AbsoluteFill>;
}
