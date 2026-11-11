import React from 'react';
import {AbsoluteFill, Audio, Sequence, staticFile, useVideoConfig} from 'remotion';
import {SANS} from './fonts';
import {Background} from './parts/Background';
import {Subtitles, Word} from './parts/Subtitles';
import {Brand} from './parts/Brand';
import {Hook, HookData} from './templates/Hook';
import {BigNumber, NumberData} from './templates/BigNumber';
import {Compare, CompareData} from './templates/Compare';
import {List, ListData} from './templates/List';

type Base = {start: number; end: number};
export type Scene = Base &
	(
		| ({template: 'hook'} & HookData)
		| ({template: 'number'} & NumberData)
		| ({template: 'compare'} & CompareData)
		| ({template: 'list'} & ListData)
	);

export type ShortProps = {
	audio: string | null;
	duration: number;
	brand: {name: string; accent: string};
	scenes: Scene[];
	words: Word[];
};

const render = (s: Scene, accent: string, len: number) => {
	switch (s.template) {
		case 'hook':
			return <Hook {...s} accent={accent} len={len} />;
		case 'number':
			return <BigNumber {...s} accent={accent} len={len} />;
		case 'compare':
			return <Compare {...s} accent={accent} len={len} />;
		case 'list':
			return <List {...s} accent={accent} len={len} />;
	}
};

export const Short: React.FC<ShortProps> = ({audio, brand, scenes, words}) => {
	const {fps} = useVideoConfig();
	return (
		<AbsoluteFill style={{fontFamily: SANS, color: '#fff'}}>
			<Background accent={brand.accent} />
			{scenes.map((s, i) => {
				const from = Math.round(s.start * fps);
				const len = Math.max(1, Math.round(s.end * fps) - from);
				return (
					<Sequence key={i} from={from} durationInFrames={len} layout="none">
						{render(s, brand.accent, len)}
					</Sequence>
				);
			})}
			<Brand name={brand.name} accent={brand.accent} />
			<Subtitles words={words} accent={brand.accent} />
			{audio ? <Audio src={staticFile(audio)} /> : null}
		</AbsoluteFill>
	);
};
