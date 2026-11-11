import React from 'react';
import {useCurrentFrame, useVideoConfig} from 'remotion';
import {enter, parseAccent} from './anim';
import {MONO} from '../fonts';

/** Kicker mono kecil + judul tebal dengan kata *miring* berwarna aksen. */
export const Header: React.FC<{kicker?: string; title?: string; accent: string; size?: number}> = ({
	kicker,
	title,
	accent,
	size = 76,
}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const a = enter(frame, fps, 0);
	return (
		<div style={{transform: `translateY(${(1 - a) * 30}px)`, opacity: a}}>
			{kicker ? (
				<div style={{fontFamily: MONO, fontSize: 30, letterSpacing: 4, color: '#ffffff99', marginBottom: 12}}>
					{kicker.toUpperCase()}
				</div>
			) : null}
			{title ? (
				<div style={{fontSize: size, fontWeight: 900, lineHeight: 1.05}}>
					{parseAccent(title).map((p, i) => (
						<span key={i} style={p.accent ? {fontStyle: 'italic', color: accent} : undefined}>
							{p.text}
						</span>
					))}
				</div>
			) : null}
		</div>
	);
};
