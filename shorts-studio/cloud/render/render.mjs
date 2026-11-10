import fs from 'node:fs';
import {bundle} from '@remotion/bundler';
import {renderMedia, renderStill, selectComposition} from '@remotion/renderer';
import {execFileSync} from 'node:child_process';

const root = '/render/job';
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
await renderMedia({...options,composition,outputLocation:`${root}/video.mp4`,codec:'h264',audioCodec:'aac',
  crf:18,pixelFormat:'yuv420p',concurrency:2,timeoutInMilliseconds:60000});
const probe = JSON.parse(execFileSync('ffprobe',['-v','error','-show_streams','-show_format','-of','json',`${root}/video.mp4`],{encoding:'utf8'}));
const video=probe.streams.find(s=>s.codec_type==='video'),audio=probe.streams.find(s=>s.codec_type==='audio');
if(video?.width!==1080||video?.height!==1920||!audio||Math.abs(Number(probe.format.duration)-seconds)>.5)throw new Error('Final media validation failed');
fs.writeFileSync(`${root}/metrics.json`,JSON.stringify({width:video.width,height:video.height,fps:video.avg_frame_rate,seconds:Number(probe.format.duration),audioCodec:audio.codec_name,bytes:Number(probe.format.size)}));
