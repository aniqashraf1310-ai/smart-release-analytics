import React, { useMemo, useState } from 'react';
import { FIX_TYPE_OPTIONS, GOV_STATUS_CONFIG, QA_STATUS_OPTIONS, TIER_CONFIG } from '../lib/constants';
import ReleaseCard from './ReleaseCard';
import { Card, inputClass } from './ui';

const TIER_ORDER = { CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3 };

const SearchView = ({ releases, onOpen, user }) => {
    const [f, setF] = useState({ query: '', date: '', qa: '', fixType: '', gov: '', tier: '', mine: false, sort: 'date' });
    const set = (e) => {
        const { name, type, checked, value } = e.target;
        setF(p => ({ ...p, [name]: type === 'checkbox' ? checked : value }));
    };

    const needsMe = (r) => r.gov_status === 'IN_REVIEW' && r.submitted_by !== user.id;

    const list = useMemo(() => {
        const q = f.query.toLowerCase();
        let out = releases.filter(r =>
            (!q || [r.version, r.app_name, r.ticket_id, r.responsible_qa, r.description].some(v => (v || '').toLowerCase().includes(q))) &&
            (!f.date || r.target_date === f.date) &&
            (!f.qa || r.qa_status === f.qa) &&
            (!f.fixType || r.fix_type === f.fixType) &&
            (!f.gov || r.gov_status === f.gov) &&
            (!f.tier || r.risk_tier === f.tier) &&
            (!f.mine || needsMe(r)));
        if (f.sort === 'risk') out = [...out].sort((a, b) => (TIER_ORDER[a.risk_tier] ?? 9) - (TIER_ORDER[b.risk_tier] ?? 9) || (b.risk_probability || 0) - (a.risk_probability || 0));
        return out;
    }, [releases, f]); // eslint-disable-line react-hooks/exhaustive-deps

    const counts = useMemo(() => ({
        review: releases.filter(r => r.gov_status === 'IN_REVIEW').length,
        highRisk: releases.filter(r => ['HIGH', 'CRITICAL'].includes(r.risk_tier) && !['DEPLOYED', 'CANCELLED'].includes(r.gov_status)).length,
        unscored: releases.filter(r => !r.risk_tier && r.gov_status === 'DRAFT').length,
    }), [releases]);

    return (
        <Card title={`Release History (${releases.length} total)`}>
            <div className="grid grid-cols-3 gap-3 mb-5 text-center">
                {[['In review', counts.review], ['High / critical risk open', counts.highRisk], ['Drafts not yet scored', counts.unscored]].map(([label, n]) => (
                    <div key={label} className="rounded-lg bg-gray-50 border p-3">
                        <div className="text-2xl font-bold text-primary tabular-nums">{n}</div>
                        <div className="text-xs text-neutral">{label}</div>
                    </div>
                ))}
            </div>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5">
                <input name="query" value={f.query} onChange={set} placeholder="Search version, app, ticket, QA" className={`${inputClass} col-span-2`} />
                <input type="date" name="date" value={f.date} onChange={set} className={inputClass} />
                <select name="sort" value={f.sort} onChange={set} className={inputClass}>
                    <option value="date">Sort by target date</option>
                    <option value="risk">Sort by risk (highest first)</option>
                </select>
                <select name="gov" value={f.gov} onChange={set} className={inputClass}>
                    <option value="">Any governance status</option>
                    {Object.entries(GOV_STATUS_CONFIG).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
                </select>
                <select name="tier" value={f.tier} onChange={set} className={inputClass}>
                    <option value="">Any risk tier</option>
                    {Object.entries(TIER_CONFIG).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
                </select>
                <select name="qa" value={f.qa} onChange={set} className={inputClass}>
                    <option value="">Any QA status</option>
                    {QA_STATUS_OPTIONS.map(s => <option key={s}>{s}</option>)}
                </select>
                <select name="fixType" value={f.fixType} onChange={set} className={inputClass}>
                    <option value="">Any fix type</option>
                    {FIX_TYPE_OPTIONS.map(s => <option key={s}>{s}</option>)}
                </select>
                <label className="flex items-center gap-2 text-sm text-gray-700 col-span-2">
                    <input type="checkbox" name="mine" checked={f.mine} onChange={set} /> Only releases waiting for review (not submitted by me)
                </label>
            </div>
            <div className="space-y-3">
                {list.length === 0 ? (
                    <p className="text-center text-neutral py-10 border-2 border-dashed rounded-lg">
                        {releases.length === 0 ? 'No releases have been added yet.' : 'No releases match your filters.'}
                    </p>
                ) : list.map(r => <ReleaseCard key={r.id} release={r} onOpen={onOpen} />)}
            </div>
        </Card>
    );
};

export default SearchView;
