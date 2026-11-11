import fs from 'node:fs';
import {bundle} from '@remotion/bundler';
import {renderMedia, renderStill, selectComposition} from '@remotion/renderer';
import {execFileSync} from 'node:child_process';

const root = process.env.STUDIO_RENDER_ROOT || '/render/job';
const {seconds} = JSON.parse(fs.readFileSync(`${root}/config.json`, 'utf8'));
if (!Number.isFinite(seconds) || seconds < 1 || seconds > 75) throw new Error('Invalid duration');
fs.writeFileSync(`${root}/index.tsx`, `
import React from 'react';
import {AbsoluteFill, Audio, Composition, registerRoot, staticFile} from 'remotion';
import Video from './Video';
const Film = () => <AbsoluteFill style={{backgroundColor:'#080b14',fontFamily:'DejaVu Sans, Noto Sans, sans-serif'}}>
  <Video/><Audio src={staticFile('narration.mp3')}/>
</AbsoluteFill>;
registerRoot(() => <Composition id="Film" component={Film} width={1080} height={1920} fps={30} durationInFrames={${Math.ceil(seconds * 30)}}/>);
`);
const serveUrl = await bundle({entryPoint:`${root}/index.tsx`,publicDir:`${root}/public`,onProgress:()=>{}});
const options = {serveUrl,browserExecutable:'/usr/bin/chromium',chromiumOptions:{disableWebSecurity:false},logLevel:'error'};
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

