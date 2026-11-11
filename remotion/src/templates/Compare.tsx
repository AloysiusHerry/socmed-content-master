import React from 'react';
import {AbsoluteFill, Easing, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {exit, fmtNum} from '../parts/anim';
import {Header} from '../parts/Header';
import {Card} from './Card';

export type CompareData = {
	kicker?: string;
	title?: string;
	label?: string;
	unit?: string;
	items: {name: string; value: number; color?: string}[];
	source?: string;
};

const COLORS = ['#f59e0b', '#22c55e', '#3b82f6', '#ef4444'];

/** Batang perbandingan yang tumbuh bersamaan; pemenang (nilai terkecil/terbesar) ditandai. */
export const Compare: React.FC<CompareData & {accent: string; len: number}> = (p) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const max = Math.max(...p.items.map((x) => x.value), 1);
	const g = interpolate(frame, [12, 12 + fps * 1.6], [0, 1], {
		extrapolateLeft: 'clamp',
		extrapolateRight: 'clamp',
		easing: Easing.out(Easing.cubic),
	});
	return (
		<AbsoluteFill style={{padding: '330px 60px 0', gap: 40, opacity: exit(frame, p.len)}}>
			<Header kicker={p.kicker} title={p.title} accent={p.accent} />
			<Card label={p.label} source={p.source}>
				{p.items.map((x, i) => {
					const c = x.color ?? COLORS[i % COLORS.length];
					return (
						<div key={i} style={{marginBottom: i === p.items.length - 1 ? 0 : 30}}>
							<div style={{display: 'flex', alignItems: 'center', gap: 14, fontSize: 40, fontWeight: 800, marginBottom: 10}}>
								<div style={{width: 22, height: 22, borderRadius: 5, background: c}} />
								{x.name}
							</div>
							<div style={{display: 'flex', alignItems: 'center', gap: 18}}>
								<div style={{flex: 1, height: 64, background: '#14161b10', borderRadius: 12, overflow: 'hidden'}}>
									<div style={{height: '100%', width: `${(x.value / max) * 100 * g}%`, background: c, borderRadius: 12}} />
								</div>
								<div style={{fontSize: 40, fontWeight: 900, minWidth: 230, textAlign: 'right', fontVariantNumeric: 'tabular-nums'}}>
									{fmtNum(x.value * g)}
									{p.unit ? <span style={{fontSize: 28, fontWeight: 700}}> {p.unit}</span> : null}
								</div>
							</div>
						</div>
					);
				})}
			</Card>
		</AbsoluteFill>
	);
};
