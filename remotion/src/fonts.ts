import {loadFont as loadInter} from '@remotion/google-fonts/Inter';
import {loadFont as loadMono} from '@remotion/google-fonts/JetBrainsMono';

export const {fontFamily: SANS} = loadInter('normal', {weights: ['500', '700', '800', '900'], subsets: ['latin']});
loadInter('italic', {weights: ['800', '900'], subsets: ['latin']});
export const {fontFamily: MONO} = loadMono('normal', {weights: ['500'], subsets: ['latin']});
