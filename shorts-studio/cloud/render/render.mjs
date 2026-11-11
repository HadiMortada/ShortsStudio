import fs from 'node:fs';
import {bundle} from '@remotion/bundler';
import {renderMedia, renderStill, selectComposition} from '@remotion/renderer';
import {execFileSync} from 'node:child_process';

const root = process.env.STUDIO_RENDER_ROOT || '/render/job';
const {seconds, cues: rawCues, words: rawWords, captions = true, language = 'en'} = JSON.parse(fs.readFileSync(`${root}/config.json`, 'utf8'));
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
import {AbsoluteFill, Audio, Composition, registerRoot, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
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
const Film = () => <AbsoluteFill style={{backgroundColor:'#080b14',fontFamily:'DejaVu Sans, Noto Sans, sans-serif'}}>
  <Video cues={${JSON.stringify(cues).replace(/</g, '\\u003c')}}/><Captions/><Audio src={staticFile('narration.mp3')}/>
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

