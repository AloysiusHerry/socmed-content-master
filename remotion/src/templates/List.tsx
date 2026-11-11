import React from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';
import {enter, exit} from '../parts/anim';
import {Header} from '../parts/Header';

export type ListData = {kicker?: string; title?: string; points: string[]};

/** Poin-poin muncul bergantian, merata di sepanjang adegan. */
export const List: React.FC<ListData & {accent: string; len: number}> = (p) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const step = Math.max(8, Math.floor((p.len - 20) / Math.max(1, p.points.length)));
	return (
		<AbsoluteFill style={{padding: '330px 60px 0', gap: 44, opacity: exit(frame, p.len)}}>
			<Header kicker={p.kicker} title={p.title} accent={p.accent} />
			<div style={{display: 'flex', flexDirection: 'column', gap: 26}}>
				{p.points.map((t, i) => {
					const a = enter(frame, fps, 10 + i * step, 15);
					return (
						<div
							key={i}
							style={{
								display: 'flex',
								alignItems: 'center',
								gap: 26,
								background: '#ffffff12',
								border: '2px solid #ffffff1f',
								borderRadius: 28,
								padding: '26px 30px',
								opacity: a,
								transform: `translateX(${(1 - a) * 120}px)`,
							}}
						>
							<div
								style={{
									flexShrink: 0,
									width: 72,
									height: 72,
									borderRadius: 20,
									background: p.accent,
									color: '#111',
									fontSize: 40,
									fontWeight: 900,
									display: 'flex',
									alignItems: 'center',
									justifyContent: 'center',
								}}
							>
								{i + 1}
							</div>
							<div style={{fontSize: 48, fontWeight: 800, lineHeight: 1.15}}>{t}</div>
						</div>
					);
				})}
			</div>
		</AbsoluteFill>
	);
};
