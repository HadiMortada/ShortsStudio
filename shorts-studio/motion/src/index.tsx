import {registerRoot, Composition} from 'remotion';
import {StudioShort, type StudioProps} from './StudioShort';
import React from 'react';

const demo: StudioProps = {
  durationSec: 30,
  audioDuration: 0,
  hasAudio: false,
  style: 'Neon kinetic',
  plan: {
    title: 'IDEAS INTO MOTION',
    description: 'Shorts Studio demo',
    narration: 'Make an original story every day, with engaging motion design.',
    scenes: [
      {headline: 'ONE GREAT IDEA', kicker: 'START WITH A HOOK', visual: 'orbits'},
      {headline: 'TURN IT INTO A STORY', kicker: 'MAKE EVERY SECOND COUNT', visual: 'timeline'},
      {headline: 'KEEP THE ENERGY', kicker: 'VISUAL RHYTHM', visual: 'bars'},
      {headline: 'MAKE IT MEMORABLE', kicker: 'A CLEAR TAKEAWAY', visual: 'wave'},
    ],
  },
};

const Root: React.FC = () => (
  <Composition
    id="StudioShort"
    component={StudioShort}
    fps={30}
    width={1080}
    height={1920}
    durationInFrames={900}
    defaultProps={demo}
    calculateMetadata={({props}) => ({
      durationInFrames: Math.ceil(30 * Math.min(60, Math.max(15, props.durationSec, props.audioDuration + 0.4))),
    })}
  />
);
registerRoot(Root);
