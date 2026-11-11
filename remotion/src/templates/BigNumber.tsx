import React from 'react';
import {AbsoluteFill, Easing, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {exit, fmtNum} from '../parts/anim';
import {Header} from '../parts/Header';
import {Card} from './Card';

export type NumberData = {
	kicker?: string;
	title?: string;
	value: number;
	from?: number;
	decimals?: number;
	prefix?: string;
	suffix?: string;
	label: string;
	source?: string;
};

/** Angka besar yang menghitung naik/turun di dalam kartu. */
export const BigNumber: React.FC<NumberData & {accent: string; len: number}> = (p) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const v = interpolate(frame, [8, 8 + fps * 1.4], [p.from ?? 0, p.value], {
		extrapolateLeft: 'clamp',
		extrapolateRight: 'clamp',
		easing: Easing.out(Easing.cubic),
	});
	// ukuran huruf menyesuaikan panjang angka final agar selalu muat di kartu (lebar isi ~870px)
	const chars = `${p.prefix ?? ''}${fmtNum(p.value, p.decimals ?? 0)}${p.suffix ?? ''}`.length;
	const size = Math.min(190, Math.floor(1500 / Math.max(chars, 1)));
	return (
		<AbsoluteFill style={{padding: '330px 60px 0', gap: 40, opacity: exit(frame, p.len)}}>
			<Header kicker={p.kicker} title={p.title} accent={p.accent} />
			<Card label={p.label} source={p.source}>
				<div style={{fontSize: size, fontWeight: 900, whiteSpace: 'nowrap', lineHeight: 1, letterSpacing: -4, fontVariantNumeric: 'tabular-nums'}}>
					{p.prefix ?? ''}
					{fmtNum(v, p.decimals ?? 0)}
					<span style={{fontSize: 90}}>{p.suffix ?? ''}</span>
				</div>
				<div style={{height: 14, borderRadius: 7, background: '#14161b14', marginTop: 28, overflow: 'hidden'}}>
					<div
						style={{
							height: '100%',
							width: `${Math.min(100, (v / (p.value || 1)) * 100)}%`,
							background: p.accent,
							borderRadius: 7,
						}}
					/>
				</div>
			</Card>
		</AbsoluteFill>
	);
};
