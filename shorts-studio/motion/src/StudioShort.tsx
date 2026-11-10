import React from 'react';
import {AbsoluteFill, Audio, Sequence, Easing, interpolate, spring, useCurrentFrame, useVideoConfig, staticFile} from 'remotion';

export type Scene = {headline: string; kicker?: string; visual?: string; accent?: string};
export type Plan = {title: string; narration: string; description?: string; scenes: Scene[]};
export type StudioProps = {
  plan: Plan;
  durationSec: number;
  audioDuration: number;
  hasAudio: boolean;
  style: 'Neon kinetic' | 'Minimal' | 'Cyber';
};
type Colors = {bg: string; bg2: string; accent: string; bright: string; ink: string; muted: string};
const palettes: Record<StudioProps['style'], Colors> = {
  'Neon kinetic': {bg:'#090a1a', bg2:'#27204c', accent:'#a58cff', bright:'#3ce1c4', ink:'#f8f9ff', muted:'#b1b9d4'},
  Minimal: {bg:'#0c1620', bg2:'#23394c', accent:'#e9dcbb', bright:'#99c3cf', ink:'#f8faf7', muted:'#b5c7cd'},
  Cyber: {bg:'#070d17', bg2:'#183d4d', accent:'#35ebee', bright:'#ff66be', ink:'#f9ffff', muted:'#b6d5de'},
};
const fit = (f:number, start:number, end:number, low:number, high:number) =>
  interpolate(f,[start,end],[low,high],{extrapolateLeft:'clamp',extrapolateRight:'clamp'});

const Deco: React.FC<{visual:string, colors:Colors, index:number}> = ({visual,colors,index}) => {
  const f = useCurrentFrame();
  const spin = f * (index%2===0?0.28:-0.38);
  if(visual==='bars'){
    return <div style={{position:'absolute',top:315,left:75,right:75,height:320,display:'flex',alignItems:'end',gap:24,opacity:.73}}>
      {Array.from({length:14},(_,i)=><div key={i} style={{flex:1,borderRadius:28,height:(65+235*(.45+.45*Math.sin(f/12+i*1.4+index)))+'px',background:i%2?colors.bright:colors.accent,boxShadow:'0 0 50px '+colors.accent+'55',transform:'scaleY('+fit(f,0,18,.15,1)+')',transformOrigin:'bottom'}}/>)}
    </div>;
  }
  if(visual==='timeline') return <div style={{position:'absolute',top:345,left:110,right:110,height:240,display:'flex',justifyContent:'space-between',alignItems:'center'}}>
    <div style={{position:'absolute',top:120,left:0,width:'100%',height:7,background:colors.muted+'44'}} />
    <div style={{position:'absolute',top:120,left:0,width:fit(f,0,105,0,100)+'%',height:7,background:colors.bright}} />
    {[0,1,2,3].map((n)=><div key={n} style={{width:65,height:65,borderRadius:'50%',background:n*31<f?colors.accent:colors.bg2,border:'5px solid '+colors.bright,zIndex:2,boxShadow:'0 0 45px '+colors.bright+'44'}}/>)}
  </div>;
  if(visual==='wave') return <div style={{position:'absolute',left:30,right:30,top:350,height:240,display:'flex',alignItems:'center',justifyContent:'center',gap:9}}>
    {Array.from({length:50},(_,i)=><div key={i} style={{width:10,borderRadius:10,height:15+Math.abs(Math.sin(f/9+i/4))*170,background:i%3?colors.accent:colors.bright,opacity:.65}}/>)}
  </div>;
  if(visual==='grid') return <div style={{position:'absolute',left:100,right:100,top:255,height:380,display:'grid',gridTemplateColumns:'repeat(6,1fr)',gap:24,transform:'perspective(600px) rotateX(17deg) rotateZ('+(spin/8)+'deg)'}}>
    {Array.from({length:24},(_,i)=><div key={i} style={{borderRadius:16,border:'3px solid '+colors.accent,background:i%5===index%5?colors.bright+'aa':colors.accent+'11',opacity:fit(f,i,30+i,.2,.9)}}/>)}
  </div>;
  if(visual==='particles') return <div style={{position:'absolute',left:0,right:0,top:240,height:460}}>
    {Array.from({length:26},(_,i)=>{
      const x=70+(i*153)%950, y=30+(i*127)%400;
      return <div key={i} style={{position:'absolute',width:8+(i%5)*7,height:8+(i%5)*7,borderRadius:'50%',left:x,top:y+Math.sin(f/15+i)*32,background:i%2?colors.accent:colors.bright,opacity:.25+(i%6)/10,boxShadow:'0 0 36px '+colors.bright}}/>;
    })}
  </div>;
  return <div style={{position:'absolute',top:260,left:100,right:100,height:390,display:'flex',justifyContent:'center',alignItems:'center',transform:'rotate('+spin+'deg)'}}>
    {[390,300,205].map((diam,i)=><div key={i} style={{position:'absolute',width:diam,height:diam,borderRadius:'50%',border:(i===1?8:3)+'px solid '+(i===1?colors.bright:colors.accent),boxShadow:'0 0 '+(55+i*15)+'px '+colors.accent+'55',transform:'scale('+fit(f,i*6,30+i*6,.4,1)+')'}} />)}
    <div style={{width:66,height:66,borderRadius:'50%',background:colors.bright,boxShadow:'0 0 80px '+colors.bright}} />
  </div>;
};

const SceneFrame:React.FC<{scene:Scene; index:number; count:number; frames:number; colors:Colors; title:string}> = ({scene,index,count,frames,colors,title}) => {
  const frame=useCurrentFrame();
  const {fps}=useVideoConfig();
  const appear=spring({frame,fps,config:{damping:17,stiffness:85}});
  const exit=fit(frame,frames-13,frames,1,0);
  const offset=fit(frame,0,frames,-25,24);
  const headline=(scene.headline||'').toUpperCase();
  return <AbsoluteFill style={{overflow:'hidden',background:'radial-gradient(ellipse at 65% 25%,'+colors.bg2+' 0%,'+colors.bg+' 73%)',color:colors.ink,fontFamily:'Arial, Helvetica, sans-serif'}}>
    <AbsoluteFill style={{opacity:.30,backgroundImage:'linear-gradient('+colors.accent+'11 1px, transparent 1px),linear-gradient(90deg,'+colors.accent+'11 1px, transparent 1px)',backgroundSize:'85px 85px',transform:'translateY('+(frame%85)+'px)'}}/>
    <div style={{position:'absolute',top:-260,right:-210,width:670,height:670,borderRadius:600,background:colors.accent,opacity:.10,filter:'blur(110px)'}}/>
    <div style={{position:'absolute',left:68,right:68,top:95,display:'flex',justifyContent:'space-between',alignItems:'center',fontWeight:800,fontSize:27,letterSpacing:7,opacity:.78}}>
      <span>SHORTS / STUDIO</span><span>{String(index+1).padStart(2,'0')} / {String(count).padStart(2,'0')}</span>
    </div>
    <Deco visual={scene.visual||'orbits'} colors={colors} index={index}/>
    <div style={{position:'absolute',top:800,left:80,right:80,transform:'translateY('+((1-appear)*100+offset)+'px)',opacity:appear*exit}}>
      <div style={{fontSize:32,fontWeight:900,letterSpacing:5,color:colors.bright,marginBottom:38}}>{(scene.kicker||'DISCOVER SOMETHING NEW').toUpperCase()}</div>
      <div style={{fontSize:headline.length>42?76:headline.length>25?91:105,letterSpacing:-3,lineHeight:1.08,fontWeight:950,textTransform:'uppercase',overflowWrap:'break-word',textShadow:'0 15px 60px #0009'}}>{headline}</div>
      <div style={{marginTop:42,width:105,height:12,background:colors.accent,borderRadius:40,transform:'scaleX('+appear+')',transformOrigin:'left'}}/>
    </div>
    <div style={{position:'absolute',bottom:185,left:80,right:80}}>
      <div style={{height:5,background:colors.muted+'33',borderRadius:5,overflow:'hidden'}}>
        <div style={{width:Math.min(100,100*(index+frame/frames)/count)+'%',height:'100%',background:colors.bright}}/>
      </div>
      <div style={{marginTop:31,display:'flex',justifyContent:'space-between',fontSize:22,letterSpacing:3,color:colors.muted}}>
        <span>{title.toUpperCase().slice(0,41)}</span><span>9:16 / 1080P</span>
      </div>
    </div>
    <div style={{position:'absolute',inset:0,opacity:1-exit,background:colors.bg,pointerEvents:'none'}}/>
  </AbsoluteFill>;
};

const NarrationCaptions:React.FC<{text:string;durationFrames:number;colors:Colors}> = ({text,durationFrames,colors})=>{
  const frame=useCurrentFrame();
  const words=text.trim().split(/\s+/).filter(Boolean);
  const groupSize=6;
  const groups:Array<string>=[];
  for(let i=0;i<words.length;i+=groupSize)groups.push(words.slice(i,i+groupSize).join(' '));
  if(!groups.length)return null;
  const unit=durationFrames/groups.length;
  const idx=Math.min(groups.length-1,Math.max(0,Math.floor(frame/unit)));
  const local=frame-idx*unit;
  const pop=fit(local,0,6,.84,1);
  return <div style={{position:'absolute',bottom:310,left:72,right:72,display:'flex',justifyContent:'center',zIndex:10,pointerEvents:'none'}}>
    <div style={{fontFamily:'Arial,Helvetica,sans-serif',fontSize:49,fontWeight:900,lineHeight:1.2,color:colors.ink,textAlign:'center',padding:'18px 26px',background:'#030815bb',borderRadius:24,boxShadow:'0 12px 60px #0009',transform:'scale('+pop+')'}}>
      {groups[idx]}
    </div>
  </div>;
};

export const StudioShort:React.FC<StudioProps>=({plan,durationSec,audioDuration,hasAudio,style})=>{
  const {durationInFrames}=useVideoConfig();
  const colors=palettes[style]||palettes['Neon kinetic'];
  const scenes=plan.scenes?.length?plan.scenes:[{headline:plan.title,visual:'orbits'}];
  const n=scenes.length;
  const per=Math.ceil(durationInFrames/n);
  return <AbsoluteFill style={{background:colors.bg}}>
    {scenes.map((scene,i)=><Sequence key={i} from={i*per} durationInFrames={Math.min(per,durationInFrames-i*per)}>
      <SceneFrame scene={scene} index={i} count={n} frames={per} colors={colors} title={plan.title}/>
    </Sequence>)}
    <NarrationCaptions text={plan.narration} durationFrames={durationInFrames} colors={colors}/>
    {hasAudio&&<Audio src={staticFile('narration.wav')} />}
  </AbsoluteFill>;
};
