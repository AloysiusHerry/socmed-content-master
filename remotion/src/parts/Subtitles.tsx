import React from 'react';
import {useCurrentFrame, useVideoConfig} from 'remotion';
import {enter} from './anim';

export type Word = {w: string; t: number; e?: number};

/** Kelompokkan kata jadi baris pendek (maks 3 kata / 18 huruf), pecah di akhir kalimat. */
const group = (words: Word[]) => {
	const out: Word[][] = [];
	let cur: Word[] = [];
	for (const w of words) {
		const len = cur.reduce((a, x) => a + x.w.length + 1, 0);
		const prev = cur[cur.length - 1];
		// jeda bicara panjang = batas kalimat/adegan (edge-tts tidak menyertakan tanda baca)
		const pause = prev ? w.t - (prev.e ?? prev.t + 0.45) > 0.3 : false;
		if (cur.length && (pause || cur.length >= 3 || len + w.w.length > 18)) {
			out.push(cur);
			cur = [];
		}
		cur.push(w);
		if (/[.!?]$/.test(w.w)) {
			out.push(cur);
			cur = [];
		}
	}
	if (cur.length) out.push(cur);
	return out;
};

/** Subtitle karaoke: kotak gelap, kata yang sedang diucapkan disorot warna aksen. */
export const Subtitles: React.FC<{words: Word[]; accent: string}> = ({words, accent}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const t = frame / fps;
	const groups = React.useMemo(() => group(words), [words]);
	const gi = groups.findIndex((g, i) => t >= g[0].t && (i === groups.length - 1 || t < groups[i + 1][0].t));
	if (gi < 0) return null;
	const g = groups[gi];
	const last = g[g.length - 1];
	if (t > (last.e ?? last.t + 0.6) + 0.4) return null;
	const pop = enter(frame, fps, Math.round(g[0].t * fps), 16);
	return (
		<div
			style={{
				position: 'absolute',
				left: 60,
				right: 60,
				bottom: 330,
				display: 'flex',
				justifyContent: 'center',
				transform: `scale(${0.85 + 0.15 * pop})`,
				opacity: pop,
			}}
		>
			<div
				style={{
					background: '#111318e6',
					borderRadius: 22,
					padding: '14px 22px',
					fontSize: 74,
					fontWeight: 900,
					lineHeight: 1.15,
					textAlign: 'center',
					display: 'flex',
					flexWrap: 'wrap',
					justifyContent: 'center',
					gap: '0 18px',
				}}
			>
				{g.map((w, i) => {
					const on = t >= w.t && (i === g.length - 1 || t < g[i + 1].t);
					const said = t >= w.t;
					return (
						<span
							key={i}
							style={{
								padding: '0 10px',
								borderRadius: 12,
								background: on ? accent : 'transparent',
								color: on ? '#111' : said ? '#fff' : '#ffffff80',
							}}
						>
							{w.w}
						</span>
					);
				})}
			</div>
		</div>
	);
};
