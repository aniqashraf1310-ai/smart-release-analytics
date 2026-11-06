import React from 'react';
import { GOV_STATUS_CONFIG, PRIMARY_COLOR, TIER_CONFIG, pct } from '../lib/constants';

export const Card = ({ title, subtitle, children, className = '', actions }) => (
    <section className={`bg-white/90 backdrop-blur shadow-lg shadow-primary/5 rounded-2xl p-6 border border-primary-100 brand-top ${className}`}>
        {(title || actions) && (
            <div className="flex flex-wrap items-start justify-between gap-2 mb-4">
                <div>
                    {title && <h2 className="text-2xl font-semibold text-gray-800">{title}</h2>}
                    {subtitle && <p className="text-sm text-neutral mt-1">{subtitle}</p>}
                </div>
                {actions}
            </div>
        )}
        {children}
    </section>
);

export const GovStatusBadge = ({ status }) => {
    const cfg = GOV_STATUS_CONFIG[status] || GOV_STATUS_CONFIG.DRAFT;
    return <span className={`inline-flex items-center text-xs font-semibold px-2.5 py-0.5 rounded-full border ${cfg.class}`}>{cfg.label}</span>;
};

export const TierBadge = ({ tier, probability }) => {
    if (!tier) return <span className="inline-flex text-xs font-medium px-2.5 py-0.5 rounded-full bg-gray-100 text-gray-500">Not scored</span>;
    const cfg = TIER_CONFIG[tier];
    return (
        <span className={`inline-flex items-center text-xs font-semibold px-2.5 py-0.5 rounded-full ${cfg.class}`}>
            {cfg.label}{probability !== undefined && probability !== null ? ` · ${pct(probability, 0)}` : ''}
        </span>
    );
};

export const Button = ({ children, variant = 'primary', className = '', ...props }) => {
    const styles = {
        primary: 'brand-gradient text-white hover:brightness-110 shadow-primary/30',
        secondary: 'bg-gray-100 text-gray-800 hover:bg-gray-200',
        danger: 'bg-red-600 text-white hover:bg-red-700',
        ghost: 'text-primary hover:bg-primary-50',
    };
    return (
        <button
            className={`inline-flex items-center justify-center gap-1.5 px-4 py-2 rounded-xl text-sm font-semibold shadow-sm transition disabled:opacity-50 disabled:cursor-not-allowed ${styles[variant]} ${className}`}
            {...props}
        >
            {children}
        </button>
    );
};

export const Field = ({ label, hint, children }) => (
    <label className="block">
        <span className="block text-xs font-semibold text-gray-600 mb-1">{label}</span>
        {children}
        {hint && <span className="block text-xs text-neutral mt-1">{hint}</span>}
    </label>
);

export const inputClass = 'w-full p-2.5 border border-slate-300 rounded-lg bg-white text-sm focus:outline-none focus:ring-2 focus:ring-primary/40';

export const ErrorBox = ({ error }) => error
    ? <div className="bg-red-50 border-l-4 border-red-500 text-red-700 p-3 rounded text-sm">{error}</div>
    : null;

// Horizontal bar for a signed contribution (percentage points).
export const ContributionBar = ({ label, value, contribution, max }) => {
    const width = Math.min(100, (Math.abs(contribution) / (max || 1)) * 100);
    const positive = contribution > 0;
    return (
        <div className="grid grid-cols-[minmax(0,10rem)_1fr_4.5rem] items-center gap-2 text-sm">
            <div className="truncate text-gray-700" title={label}>
                {label}
                <span className="block text-xs text-neutral truncate">{String(value)}</span>
            </div>
            <div className="h-3 bg-gray-100 rounded relative overflow-hidden">
                <div className="h-3 rounded" style={{ width: `${width}%`, backgroundColor: positive ? PRIMARY_COLOR : '#2f855a' }} />
            </div>
            <div className={`text-right tabular-nums font-medium ${positive ? 'text-primary' : 'text-green-700'}`}>
                {positive ? '+' : ''}{contribution.toFixed(1)} pts
            </div>
        </div>
    );
};

export const Tabs = ({ tabs, active, onChange }) => (
    <div className="flex gap-1 border-b mb-4 overflow-x-auto">
        {tabs.map(t => (
            <button
                key={t.id}
                onClick={() => onChange(t.id)}
                className={`px-3 py-2 text-sm font-medium whitespace-nowrap border-b-2 -mb-px transition ${
                    active === t.id ? 'border-primary text-primary' : 'border-transparent text-gray-500 hover:text-gray-800'
                }`}
            >
                {t.label}
            </button>
        ))}
    </div>
);
