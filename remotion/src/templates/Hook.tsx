import React from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';
import {enter, exit, parseAccent} from '../parts/anim';
import {MONO} from '../fonts';

export type HookData = {kicker?: string; title: string};

/** Pembuka: judul besar muncul kata demi kata, kata *aksen* miring berwarna. */
export const Hook: React.FC<HookData & {accent: string; len: number}> = ({kicker, title, accent, len}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const words = parseAccent(title).flatMap((p) =>
		p.text.split(/\s+/).filter(Boolean).map((w) => ({w, accent: p.accent})),
	);
	const k = enter(frame, fps, 0);
	return (
		<AbsoluteFill style={{justifyContent: 'center', padding: '0 80px', opacity: exit(frame, len), marginTop: -160}}>
			{kicker ? (
				<div
					style={{
						fontFamily: MONO,
						fontSize: 34,
						letterSpacing: 5,
						color: accent,
						marginBottom: 28,
						opacity: k,
						transform: `translateX(${(1 - k) * -40}px)`,
					}}
				>
					{kicker.toUpperCase()}
				</div>
			) : null}
			<div style={{display: 'flex', flexWrap: 'wrap', gap: '0 26px', fontSize: 132, fontWeight: 900, lineHeight: 1.02}}>
				{words.map((x, i) => {
					const a = enter(frame, fps, 4 + i * 4, 12);
					return (
						<span
							key={i}
							style={{
								display: 'inline-block',
								opacity: a,
								transform: `translateY(${(1 - a) * 60}px) scale(${0.8 + 0.2 * a})`,
								...(x.accent ? {fontStyle: 'italic', color: accent} : {}),
							}}
						>
							{x.w}
						</span>
					);
				})}
			</div>
		</AbsoluteFill>
	);
};
