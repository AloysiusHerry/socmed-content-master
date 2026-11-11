import React from 'react';
import {useCurrentFrame, useVideoConfig} from 'remotion';
import {enter} from '../parts/anim';
import {MONO} from '../fonts';

/** Kartu putih membulat untuk data, masuk dengan pegas. */
export const Card: React.FC<{label?: string; source?: string; children: React.ReactNode}> = ({label, source, children}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const a = enter(frame, fps, 6);
	return (
		<div
			style={{
				background: '#fbfaf6',
				color: '#14161b',
				borderRadius: 36,
				padding: '40px 46px',
				boxShadow: '0 30px 80px #00000080',
				opacity: a,
				transform: `translateY(${(1 - a) * 80}px) scale(${0.94 + 0.06 * a})`,
			}}
		>
			{label ? (
				<div style={{fontFamily: MONO, fontSize: 30, fontWeight: 500, letterSpacing: 2, marginBottom: 22, color: '#14161bcc'}}>
					{label.toUpperCase()}
				</div>
			) : null}
			{children}
			{source ? <div style={{fontSize: 26, color: '#14161b88', marginTop: 24}}>{source}</div> : null}
		</div>
	);
};
