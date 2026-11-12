import fs from 'node:fs';
import {bundle} from '@remotion/bundler';
import {renderMedia, renderStill, selectComposition} from '@remotion/renderer';
import {execFileSync} from 'node:child_process';

const root = process.env.STUDIO_RENDER_ROOT || '/render/job';
const {seconds, cues: rawCues, words: rawWords, captions = true, language = 'en', endCard: rawEndCard = 0} = JSON.parse(fs.readFileSync(`${root}/config.json`, 'utf8'));
// Seconds of the "Like & Subscribe" card at the end (0 = none).
const endCard = Number.isFinite(rawEndCard) ? Math.max(0, Math.min(4, rawEndCard)) : 0;
if (!Number.isFinite(seconds) || seconds < 1 || seconds > 75) throw new Error('Invalid duration');
// Measured narration timing: one cue per spoken beat, in seconds of the final audio.
const cues = (Array.isArray(rawCues) ? rawCues : []).filter((c) => Number.isFinite(c?.start) && Number.isFinite(c?.end))
  .map((c) => ({start: c.start, end: c.end, text: String(c.text || '').slice(0, 600)}));
// Word-by-word captions: short groups of words, the spoken word highlighted.
const words = (Array.isArray(rawWords) ? rawWords : []).filter((w) => Number.isFinite(w?.start) && Number.isFinite(w?.end) && w.text)
  .map((w) => ({start: w.start, end: w.end, text: String(w.text).slice(0, 60)}));
const groups = [];
for (const w of words) {
  const last = groups[groups.length - 1];
  const text = last ? last.words.map((x) => x.text).join(' ') : '';
  if (!last || last.words.length >= 4 || text.length + w.text.length > 24 || /[.!?؟،,;:]$/.test(last.words[last.words.length - 1].text)
      || w.start - last.words[last.words.length - 1].end > 0.6) groups.push({words: [w]});
  else last.words.push(w);
}
const captionData = captions ? groups.map((g, i) => ({start: g.words[0].start,
  end: Math.max(g.words[g.words.length - 1].end + 0.15, i + 1 < groups.length ? Math.min(groups[i + 1].words[0].start, g.words[g.words.length - 1].end + 0.6) : 0),
  words: g.words})) : [];
const rtl = /^(ar|fa|he|ur)/i.test(language);
fs.writeFileSync(`${root}/index.tsx`, `
import React from 'react';
import {AbsoluteFill, Audio, Composition, Sequence, interpolate, registerRoot, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import Video from './Video';
const CAPTIONS = ${JSON.stringify(captionData).replace(/</g, '\\u003c')};
const Captions = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / fps;
  const group = CAPTIONS.find((g) => t >= g.start && t < g.end);
  if (!group) return null;
  const pop = spring({frame: frame - Math.round(group.start * fps), fps, config: {damping: 14, stiffness: 180}});
  return <AbsoluteFill style={{pointerEvents: 'none'}}>
    <div dir="${rtl ? 'rtl' : 'ltr'}" style={{position: 'absolute', left: 70, right: 70, top: 1390, height: 140, display: 'flex',
      alignItems: 'center', justifyContent: 'center', transform: \`scale(\${0.86 + 0.14 * pop})\`}}>
      <div style={{background: 'rgba(8, 10, 22, 0.72)', borderRadius: 28, padding: '14px 30px', textAlign: 'center',
        fontFamily: "'Noto Sans', 'Noto Sans Arabic', 'DejaVu Sans', sans-serif", fontWeight: 900, fontSize: 64, lineHeight: 1.15,
        color: '#ffffff', textShadow: '0 4px 18px rgba(0,0,0,0.45)'}}>
        {group.words.map((w, i) => <span key={i} style={{color: t >= w.start && t < w.end + 0.08 ? '#FFD54A' : '#ffffff'}}>{(i ? ' ' : '') + w.text}</span>)}
      </div>
    </div>
  </AbsoluteFill>;
};
const AR = ${rtl ? 'true' : 'false'};
const WORDS = AR ? {like: 'أعجبني', sub: 'اشترك', done: 'تم الاشتراك', ask: 'أعجبك الفيديو؟'}
  : {like: 'Like', sub: 'Subscribe', done: 'Subscribed', ask: 'Enjoyed this?'};
const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'};
const Thumb = ({filled}) => <svg width="64" height="64" viewBox="0 0 24 24">
  <path d="M2 21h4V9H2v12zm20-11a2 2 0 0 0-2-2h-6.3l1-4.6v-.3c0-.4-.2-.8-.4-1.1L13.2 1 6.6 7.6c-.4.4-.6.9-.6 1.4V19a2 2 0 0 0 2 2h9c.8 0 1.5-.5 1.8-1.2l3-7.1c.1-.2.2-.5.2-.7v-2z"
    fill={filled ? '#3EA6FF' : 'none'} stroke={filled ? '#3EA6FF' : '#0F0F0F'} strokeWidth="1.6" strokeLinejoin="round"/></svg>;
const Bell = ({angle}) => <svg width="58" height="58" viewBox="0 0 24 24" style={{transform: 'rotate(' + angle + 'deg)', transformOrigin: '50% 10%'}}>
  <path d="M12 22a2.5 2.5 0 0 0 2.4-2h-4.8a2.5 2.5 0 0 0 2.4 2zm7-6V11c0-3.1-1.6-5.6-4.5-6.3V4a2.5 2.5 0 0 0-5 0v.7C6.6 5.4 5 7.9 5 11v5l-2 2v1h18v-1l-2-2z" fill="#ffffff"/></svg>;
const Tap = ({at}) => {
  const frame = useCurrentFrame();
  const t = interpolate(frame, [at - 8, at, at + 12], [0, 1, 0], clamp);
  const ring = interpolate(frame, [at, at + 14], [0, 1], clamp);
  return <div style={{position: 'absolute', right: 70, bottom: -30, width: 76, height: 76, borderRadius: '50%',
    background: 'rgba(255,255,255,' + (0.85 * t) + ')', boxShadow: '0 0 0 ' + (ring * 34) + 'px rgba(255,255,255,' + (0.35 * (1 - ring)) + ')',
    transform: 'scale(' + (1 - 0.25 * t) + ')'}}/>;
};
const EndCard = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const pop = (d) => spring({frame: frame - d, fps, config: {damping: 13, stiffness: 170}});
  const liked = frame >= 26, subscribed = frame >= 44;
  const likeBounce = 1 + 0.18 * Math.max(0, 1 - Math.abs(frame - 28) / 6);
  const bell = subscribed ? Math.sin((frame - 44) / 2.2) * 22 * Math.max(0, 1 - (frame - 44) / 22) : 0;
  const pill = {display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 22, height: 132, borderRadius: 66,
    fontFamily: "'Noto Sans', 'Noto Sans Arabic', 'DejaVu Sans', sans-serif", fontWeight: 800, fontSize: 56, position: 'relative'};
  return <AbsoluteFill style={{background: 'rgba(8,10,22,' + interpolate(frame, [0, 8], [0, 0.78], clamp) + ')',
    backdropFilter: 'blur(' + interpolate(frame, [0, 8], [0, 14], clamp) + 'px)', alignItems: 'center', justifyContent: 'center'}}>
    <div dir={AR ? 'rtl' : 'ltr'} style={{display: 'flex', flexDirection: 'column', gap: 44, width: 720, alignItems: 'stretch'}}>
      <div style={{...pill, height: 'auto', color: '#ffffff', fontSize: 64, opacity: pop(2), transform: 'translateY(' + ((1 - pop(2)) * 40) + 'px)'}}>{WORDS.ask}</div>
      <div style={{...pill, background: '#ffffff', color: '#0F0F0F', transform: 'scale(' + (pop(6) * (liked ? likeBounce : 1)) + ')'}}>
        <Thumb filled={liked}/>{WORDS.like}<Tap at={26}/></div>
      <div style={{...pill, background: subscribed ? '#272727' : '#FF0033', color: '#ffffff', transform: 'scale(' + pop(12) + ')'}}>
        {subscribed ? <Bell angle={bell}/> : null}{subscribed ? WORDS.done : WORDS.sub}<Tap at={44}/></div>
    </div>
  </AbsoluteFill>;
};
const Film = () => <AbsoluteFill style={{backgroundColor:'#080b14',fontFamily:'DejaVu Sans, Noto Sans, sans-serif'}}>
  <Video cues={${JSON.stringify(cues).replace(/</g, '\\u003c')}}/><Captions/><Audio src={staticFile('narration.mp3')}/>
  ${endCard ? `<Sequence from={${Math.round((seconds - endCard) * 30)}}><EndCard/></Sequence>` : ''}
</AbsoluteFill>;
registerRoot(() => <Composition id="Film" component={Film} width={1080} height={1920} fps={30} durationInFrames={${Math.ceil(seconds * 30)}}/>);
`);
const serveUrl = await bundle({entryPoint:`${root}/index.tsx`,publicDir:`${root}/public`,onProgress:()=>{}});
// Modal's image ships /usr/bin/chromium. Elsewhere (GitHub Actions) STUDIO_CHROMIUM may name a
// browser, or be empty to use the Chrome Headless Shell installed by `remotion browser ensure`.
const browserExecutable = process.env.STUDIO_CHROMIUM ?? '/usr/bin/chromium';
const options = {serveUrl,browserExecutable:browserExecutable || null,chromiumOptions:{disableWebSecurity:false},logLevel:'error'};
const composition = await selectComposition({...options,id:'Film'});
await renderStill({...options,composition,output:`${root}/preview.png`,frame:Math.floor(composition.durationInFrames*.23)});
const reviewFrames = [.03,.2,.38,.56,.74,.92];
for(let i=0;i<reviewFrames.length;i++) {
  await renderStill({...options,composition,output:`${root}/review-${i}.png`,scale:.35,
    frame:Math.min(composition.durationInFrames-1,Math.floor(composition.durationInFrames*reviewFrames[i]))});
}
execFileSync('python',['-c', `
from PIL import Image, ImageOps, ImageDraw
from pathlib import Path
root=Path('${root}')
sheet=Image.new('RGB',(1134,1384),'#141621')
draw=ImageDraw.Draw(sheet)
for i in range(6):
    image=Image.open(root/f'review-{i}.png').convert('RGB')
    sheet.paste(image,((i%3)*378, (i//3)*692+20))
    draw.text(((i%3)*378+8,(i//3)*692+3),f'Sample {i+1}',fill='white')
sheet.save(root/'review.png')
`],{timeout:30000});
await renderMedia({...options,composition,outputLocation:`${root}/video.mp4`,codec:'h264',audioCodec:'aac',
  crf:18,pixelFormat:'yuv420p',concurrency:2,timeoutInMilliseconds:60000});
const probe = JSON.parse(execFileSync('ffprobe',['-v','error','-show_streams','-show_format','-of','json',`${root}/video.mp4`],{encoding:'utf8'}));
const video=probe.streams.find(s=>s.codec_type==='video'),audio=probe.streams.find(s=>s.codec_type==='audio');
if(video?.width!==1080||video?.height!==1920||!audio||Math.abs(Number(probe.format.duration)-seconds)>.5)throw new Error('Final media validation failed');
fs.writeFileSync(`${root}/metrics.json`,JSON.stringify({width:video.width,height:video.height,fps:video.avg_frame_rate,seconds:Number(probe.format.duration),audioCodec:audio.codec_name,bytes:Number(probe.format.size)}));

