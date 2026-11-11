import React from 'react';

/** Badge nama channel di kanan atas. */
export const Brand: React.FC<{name: string; accent: string}> = ({name, accent}) => (
	<div
		style={{
			position: 'absolute',
			top: 90,
			right: 60,
			display: 'flex',
			alignItems: 'center',
			gap: 12,
			background: '#000000aa',
			border: '2px solid #ffffff22',
			borderRadius: 999,
			padding: '12px 26px 12px 14px',
			fontSize: 34,
			fontWeight: 800,
		}}
	>
		<div style={{width: 40, height: 40, borderRadius: 10, background: accent, transform: 'rotate(45deg) scale(0.75)'}} />
		{name}
	</div>
);
