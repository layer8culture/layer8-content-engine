import React from 'react';
import {
  registerRoot, Composition, AbsoluteFill, Sequence, Img, Video, Audio,
  useCurrentFrame, useVideoConfig, interpolate, spring, staticFile,
} from 'remotion';

const FPS = 30;
const CTA_SECONDS = 2.5;

const L8 = {bg0: '#000', bg1: '#050A1A', accent: '#0047FF', text: '#F5F5F5', muted: '#9B9B9B'};
const RADIO = {bg0: '#030508', bg1: '#07111F', accent: '#F5A524', accent2: '#FFB347', cyan: '#00BFFF', text: '#F8F9FA', muted: '#B7C0D1'};

const fontsCss = `@font-face{font-family:Bebas;src:url(${staticFile('BebasNeue-Regular.ttf')})}
@font-face{font-family:InterV;src:url(${staticFile('Inter-Variable.ttf')});font-weight:100 900}
@font-face{font-family:SyneV;src:url(${staticFile('Syne-Variable.ttf')});font-weight:400 800}`;

const defaults = {
  brand: 'layer8culture',
  kicker: 'AI FIELD GUIDE',
  beats: [{text: 'YOUR AI ISNT DUMB', accent: 'DUMB', seconds: 3}, {text: 'YOUR BRIEF IS', accent: 'BRIEF', seconds: 4}],
  prompt_box: '',
  cta: 'Save this. Full guide at layer8culture.io',
  hero: null,
  bgVideo: null,
  audio: null,
};

const secs = (b) => Math.max(1.5, Math.min(6, Number(b.seconds) || 3));

// Headline size from the longest word so no word ever breaks or overflows the 900px column.
const fitSize = (words, perChar, max) => {
  const longest = Math.max(...words.map((w) => w.length), 1);
  return Math.floor(Math.min(max, 900 / (longest * perChar)));
};

const KenBurns = ({src}) => {
  const f = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  const s = interpolate(f, [0, durationInFrames], [1.0, 1.12]);
  const y = interpolate(f, [0, durationInFrames], [0, -40]);
  return (
    <AbsoluteFill>
      <Img src={staticFile(src)} style={{width: '100%', height: '100%', objectFit: 'cover', transform: `scale(${s}) translateY(${y}px)`}} />
      <AbsoluteFill style={{background: 'linear-gradient(to bottom, rgba(0,0,0,.35) 0%, rgba(0,0,0,.55) 40%, rgba(0,0,0,.85) 70%, #000 100%)'}} />
    </AbsoluteFill>
  );
};

const L8Bg = () => {
  const f = useCurrentFrame();
  const x = interpolate(f, [0, 300], [70, 30], {extrapolateRight: 'clamp'});
  return <AbsoluteFill style={{background: `radial-gradient(90% 60% at ${x}% 20%, #0b1a4a 0%, ${L8.bg1} 45%, ${L8.bg0} 100%)`}} />;
};

const Words = ({text, accent = '', brand}) => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const words = String(text).trim().split(/\s+/);
  const acc = new Set(String(accent || '').toUpperCase().split(/\s+/).filter(Boolean));
  const radio = brand === 'radio';
  const size = radio ? fitSize(words, 0.62, 150) : fitSize(words, 0.43, 230);
  return (
    <div style={{display: 'flex', flexWrap: 'wrap', gap: radio ? '0 28px' : '0 34px', padding: '0 90px',
      fontFamily: radio ? 'SyneV' : 'Bebas', fontWeight: radio ? 700 : 400, fontSize: size, lineHeight: radio ? 1.02 : 0.9,
      textTransform: radio ? 'none' : 'uppercase'}}>
      {words.map((w, i) => {
        const isAcc = acc.has(w.toUpperCase().replace(/[^A-Z0-9']/g, ''));
        const s = radio
          ? interpolate(f - i * 6, [0, 24], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})
          : spring({frame: f - i * 5, fps, config: {damping: 14, mass: 0.6}});
        const color = isAcc ? (radio ? RADIO.accent : L8.accent) : (radio ? RADIO.text : L8.text);
        return (
          <span key={i} style={{color, display: 'inline-block', opacity: s, transform: `translateY(${(1 - s) * (radio ? 30 : 120)}px)`}}>{w}</span>
        );
      })}
    </div>
  );
};

const Prompt = ({text}) => {
  const f = useCurrentFrame();
  const n = Math.floor(interpolate(f, [10, 10 + Math.max(30, text.length * 1.1)], [0, text.length], {extrapolateRight: 'clamp'}));
  return (
    <div style={{margin: '60px 90px 0', padding: 46, border: `2px solid ${L8.accent}66`, borderRadius: 28, background: '#050A1Acc',
      fontFamily: 'Consolas, "Cascadia Mono", monospace', fontSize: 46, lineHeight: 1.45, color: '#9fb8ff', minHeight: 260}}>
      {text.slice(0, n)}<span style={{opacity: f % 20 < 10 ? 1 : 0, color: L8.accent}}>▍</span>
    </div>
  );
};

const Beat = ({children, frames, brand}) => {
  const f = useCurrentFrame();
  const o = interpolate(f, [frames - 8, frames], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const scale = interpolate(f, [0, frames], [1, brand === 'radio' ? 1.015 : 1.04]);
  return <AbsoluteFill style={{justifyContent: 'center', opacity: o, transform: `scale(${scale})`}}>{children}</AbsoluteFill>;
};

const Bar = ({color}) => {
  const f = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  return <div style={{position: 'absolute', bottom: 0, left: 0, height: 10, background: color, width: `${(f / durationInFrames) * 100}%`}} />;
};

const Wave = () => {
  const f = useCurrentFrame();
  return (
    <div style={{display: 'flex', gap: 10, alignItems: 'flex-end', height: 70}}>
      {Array.from({length: 16}).map((_, i) => (
        <i key={i} style={{display: 'block', width: 10, borderRadius: 5, background: `linear-gradient(${RADIO.cyan}, #0047FF)`,
          height: 14 + 50 * Math.abs(Math.sin(f / 14 + i * 0.7))}} />
      ))}
    </div>
  );
};

export const Reel = (raw) => {
  const p = {...defaults, ...raw};
  const radio = p.brand === 'radio';
  const C = radio ? RADIO : L8;
  let from = 0;
  const seqs = p.beats.map((b, i) => {
    const frames = Math.round(secs(b) * FPS);
    const el = (
      <Sequence key={i} from={from} durationInFrames={frames}>
        <Beat frames={frames} brand={p.brand}>
          <Words text={b.text} accent={b.accent} brand={p.brand} />
          {!radio && i === 1 && p.prompt_box ? <Prompt text={p.prompt_box} /> : null}
        </Beat>
      </Sequence>
    );
    from += frames;
    return el;
  });
  const ctaFrames = Math.round(CTA_SECONDS * FPS);
  return (
    <AbsoluteFill style={{color: C.text, fontFamily: 'InterV', background: C.bg0}}>
      <style>{fontsCss}</style>
      {p.bgVideo ? (
        <AbsoluteFill>
          <Video src={staticFile(p.bgVideo)} loop style={{width: '100%', height: '100%', objectFit: 'cover'}} />
          <AbsoluteFill style={{background: 'linear-gradient(to bottom, rgba(3,5,8,.25) 0%, rgba(3,5,8,.45) 45%, rgba(3,5,8,.85) 100%)'}} />
        </AbsoluteFill>
      ) : p.hero ? <KenBurns src={p.hero} /> : radio ? (
        <AbsoluteFill style={{background: `radial-gradient(90% 60% at 12% 100%, #5A371C 0%, rgba(7,17,31,0) 55%), linear-gradient(180deg, ${RADIO.bg1}, ${RADIO.bg0})`}} />
      ) : <L8Bg />}
      {p.audio ? <Audio src={staticFile(p.audio)} /> : null}
      <div style={{position: 'absolute', top: 240, left: 90, right: 90, letterSpacing: '0.3em', fontWeight: radio ? 600 : 700, fontSize: 30,
        color: radio ? RADIO.cyan : L8.muted, textTransform: 'uppercase'}}>
        {radio ? p.kicker : <>{p.kicker} <span style={{color: L8.accent}}>//</span> LAYER8</>}
      </div>
      {seqs}
      <Sequence from={from} durationInFrames={ctaFrames}>
        <Beat frames={ctaFrames + 8} brand={p.brand}>
          <div style={{padding: '0 90px', display: 'flex', flexDirection: 'column', gap: 40}}>
            {radio ? <Wave /> : null}
            <div style={{fontSize: 58, lineHeight: 1.3, fontWeight: 600, color: C.text}}>{p.cta}</div>
          </div>
        </Beat>
      </Sequence>
      <div style={{position: 'absolute', bottom: 330, left: 90, right: 90, display: 'flex', justifyContent: 'space-between',
        fontSize: 28, letterSpacing: '0.18em', color: C.muted}}>
        <span>{radio ? 'LAYER8CULTURE RADIO' : 'LAYER8CULTURE'}</span>
        <span>{radio ? 'LOFI · FOCUS' : "WE'RE THE EIGHTH"}</span>
      </div>
      <Bar color={radio ? RADIO.accent : L8.accent} />
    </AbsoluteFill>
  );
};

const calculateMetadata = ({props}) => {
  const p = {...defaults, ...props};
  const total = p.beats.reduce((a, b) => a + secs(b), 0) + CTA_SECONDS;
  return {durationInFrames: Math.round(total * FPS)};
};

registerRoot(() => (
  <Composition id="Reel" component={Reel} durationInFrames={300} fps={FPS} width={1080} height={1920}
    defaultProps={defaults} calculateMetadata={calculateMetadata} />
));
