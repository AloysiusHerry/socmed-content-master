import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';

/** Latar gelap bergerak pelan: gradien + kisi tipis + cahaya aksen. */
export const Background: React.FC<{accent: string}> = ({accent}) => {
	const f = useCurrentFrame();
	const x = 50 + Math.sin(f / 90) * 18;
	const y = 35 + Math.cos(f / 120) * 10;
	return (
		<AbsoluteFill style={{background: '#0d0f14'}}>
			<AbsoluteFill
				style={{
					background: `radial-gradient(circle at ${x}% ${y}%, ${accent}2e 0%, transparent 45%),
					radial-gradient(circle at ${100 - x}% ${100 - y}%, #3b82f62a 0%, transparent 50%)`,
				}}
			/>
			<AbsoluteFill
				style={{
					backgroundImage:
						'linear-gradient(#ffffff0d 1px, transparent 1px), linear-gradient(90deg, #ffffff0d 1px, transparent 1px)',
					backgroundSize: '90px 90px',
					backgroundPosition: `0 ${(f * 0.6) % 90}px`,
					maskImage: 'radial-gradient(circle at 50% 45%, black 20%, transparent 75%)',
				}}
			/>
		</AbsoluteFill>
	);
};
