import React from 'react';
import {Composition} from 'remotion';
import {Short, ShortProps} from './Short';

const FPS = 30;

const sample: ShortProps = {
	audio: null,
	duration: 6,
	brand: {name: 'ShortsClips', accent: '#FFD84D'},
	scenes: [
		{start: 0, end: 3, template: 'hook', kicker: 'CONTOH', title: 'Judul *contoh*'},
		{start: 3, end: 6, template: 'number', value: 22700, label: 'mobil listrik'},
	],
	words: [],
};

export const Root: React.FC = () => (
	<Composition
		id="Short"
		component={Short}
		width={1080}
		height={1920}
		fps={FPS}
		durationInFrames={Math.ceil(sample.duration * FPS)}
		defaultProps={sample}
		// durasi mengikuti props.json (panjang narasi)
		calculateMetadata={({props}) => ({durationInFrames: Math.max(1, Math.ceil(props.duration * FPS))})}
	/>
);
