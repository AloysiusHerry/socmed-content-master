import {interpolate, spring} from 'remotion';

/** Masuk dengan pegas (0->1) mulai dari frame `delay`. */
export const enter = (frame: number, fps: number, delay = 0, damping = 14) =>
	spring({frame: frame - delay, fps, config: {damping, mass: 0.7}});

/** Memudar keluar di 8 frame terakhir adegan. */
export const exit = (frame: number, len: number) =>
	interpolate(frame, [len - 8, len], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});

/** Angka gaya Indonesia: 22.700 / 2,5 */
export const fmtNum = (n: number, decimals = 0) =>
	n.toLocaleString('id-ID', {minimumFractionDigits: decimals, maximumFractionDigits: decimals});

/** "Judul *miring*" -> potongan teks dengan tanda aksen. */
export const parseAccent = (t: string) =>
	t.split(/(\*[^*]+\*)/).filter(Boolean).map((p) => ({text: p.replace(/\*/g, ''), accent: p.startsWith('*')}));
