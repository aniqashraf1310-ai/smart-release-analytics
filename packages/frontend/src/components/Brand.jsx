import React from 'react';
import { Sparkles } from 'lucide-react';

export const APP_NAME = 'Smart Release Analytic System';

// Logo mark + product name. `light` is for use on the gradient header.
const Brand = ({ light = false, size = 'lg' }) => (
    <div className="flex items-center gap-3">
        <div className={`${size === 'lg' ? 'w-11 h-11' : 'w-9 h-9'} rounded-2xl flex items-center justify-center shadow-lg ${light ? 'bg-white/20 ring-1 ring-white/40' : 'brand-gradient'}`}>
            <Sparkles className={`${size === 'lg' ? 'w-6 h-6' : 'w-5 h-5'} text-white`} />
        </div>
        <div className="leading-tight">
            <div className={`${size === 'lg' ? 'text-xl sm:text-3xl' : 'text-2xl'} font-extrabold tracking-tight whitespace-nowrap ${light ? 'text-white' : 'text-slate-900'}`}>
                Smart Release <span className={light ? 'text-amber-200' : 'brand-text'}>Analytic</span>
            </div>
            <div className={`text-xs font-medium uppercase tracking-[0.2em] ${light ? 'text-white/80' : 'text-neutral'}`}>System</div>
        </div>
    </div>
);

export default Brand;
