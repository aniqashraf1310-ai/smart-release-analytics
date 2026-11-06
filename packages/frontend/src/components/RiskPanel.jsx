import React, { useEffect, useMemo, useState } from 'react';
import { ArrowRight, Lightbulb, SlidersHorizontal } from 'lucide-react';
import { api } from '../lib/api';
import { TARGET_ENVS, TIER_CONFIG, pct } from '../lib/constants';
import { ContributionBar, ErrorBox, TierBadge, inputClass } from './ui';

// Score headline + per-release drivers. `score` is the /score response shape
// ({probability, tier, explanation, mitigations}) or a stored prediction.
export const RiskSummary = ({ score, compact = false }) => {
    if (!score) return null;
    const exp = score.explanation || {};
    const items = (exp.contributions || []).filter(d => Math.abs(d.contribution_pp) >= 0.05);
    const max = Math.max(...items.map(d => Math.abs(d.contribution_pp)), 1);
    const tier = TIER_CONFIG[score.tier];
    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-4">
                <div className="rounded-xl px-4 py-3 border-l-8 bg-gray-50" style={{ borderColor: tier?.border }}>
                    <div className="text-xs text-neutral">Predicted failure risk</div>
                    <div className="text-3xl font-extrabold tabular-nums" style={{ color: tier?.bar }}>{pct(score.probability)}</div>
                </div>
                <div className="space-y-1 text-sm">
                    <TierBadge tier={score.tier} />
                    <div className="text-gray-600">Success probability <b className="tabular-nums">{pct(1 - score.probability)}</b></div>
                    {score.model_version && <div className="text-xs text-neutral">Model {score.model_version}</div>}
                </div>
            </div>
            {items.length > 0 && (
                <div>
                    <h4 className="text-sm font-semibold text-gray-700 mb-2">
                        {compact ? 'Top risk drivers' : 'What drives this score'}
                    </h4>
                    <div className="space-y-2">
                        {(compact ? items.slice(0, 4) : items).map(d => (
                            <ContributionBar key={d.feature} label={d.label} value={d.value} contribution={d.contribution_pp} max={max} />
                        ))}
                    </div>
                    {!compact && (
                        <p className="text-xs text-neutral mt-2">
                            Baseline for a typical release: {exp.base_value_pct?.toFixed(1)}%. Method: {exp.method}; values in {exp.unit}.
                        </p>
                    )}
                </div>
            )}
            {score.mitigations?.length > 0 && (
                <div>
                    <h4 className="text-sm font-semibold text-gray-700 mb-2 flex items-center gap-1">
                        <Lightbulb className="w-4 h-4 text-primary" /> Suggested mitigations
                    </h4>
                    <ul className="space-y-1 text-sm">
                        {score.mitigations.map(m => (
                            <li key={m.action} className="flex flex-wrap items-center gap-2">
                                <span className="text-gray-800">{m.action}</span>
                                <ArrowRight className="w-3 h-3 text-neutral" />
                                <span className="tabular-nums font-medium">{pct(m.probability)}</span>
                                <span className="text-green-700 text-xs tabular-nums">({m.delta_pp.toFixed(1)} pts)</span>
                                <TierBadge tier={m.tier} />
                            </li>
                        ))}
                    </ul>
                </div>
            )}
        </div>
    );
};

// Interactive "what if we changed X" simulator for a saved release.
export const WhatIfSimulator = ({ release }) => {
    const initial = useMemo(() => ({
        test_coverage: Number(release.test_coverage),
        patch_size_loc: Number(release.patch_size_loc),
        files_changed: Number(release.files_changed),
        dependency_changes: Number(release.dependency_changes),
        target_env: release.target_env,
        friday_or_weekend: [5, 6, 0].includes(new Date(release.target_date).getUTCDay()) ? 1 : 0,
    }), [release]);
    const [overrides, setOverrides] = useState(initial);
    const [result, setResult] = useState(null);
    const [error, setError] = useState(null);

    useEffect(() => setOverrides(initial), [initial]);

    useEffect(() => {
        const t = setTimeout(() => {
            api(`/api/releases/${release.id}/what-if`, { method: 'POST', body: { overrides } })
                .then(r => { setResult(r); setError(null); })
                .catch(e => setError(e.message));
        }, 250);
        return () => clearTimeout(t);
    }, [overrides, release.id]);

    const set = (k, v) => setOverrides(o => ({ ...o, [k]: v }));
    const slider = (k, label, min, max, step, suffix = '') => (
        <label className="block text-sm">
            <span className="flex justify-between text-gray-700"><span>{label}</span><b className="tabular-nums">{overrides[k]}{suffix}</b></span>
            <input type="range" min={min} max={max} step={step} value={overrides[k]}
                onChange={e => set(k, Number(e.target.value))} className="w-full accent-[#7C3AED]" />
        </label>
    );

    return (
        <div className="border rounded-lg p-4 bg-gray-50">
            <h4 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-1">
                <SlidersHorizontal className="w-4 h-4 text-primary" /> What-if simulator
                <span className="font-normal text-neutral">(nothing is saved)</span>
            </h4>
            <div className="grid sm:grid-cols-2 gap-x-6 gap-y-3">
                {slider('test_coverage', 'Test coverage', 0, 100, 1, '%')}
                {slider('patch_size_loc', 'Patch size', 0, Math.max(5000, initial.patch_size_loc), 10, ' LOC')}
                {slider('files_changed', 'Files changed', 0, Math.max(100, initial.files_changed), 1)}
                {slider('dependency_changes', 'Dependency changes', 0, Math.max(10, initial.dependency_changes), 1)}
                <label className="block text-sm">
                    <span className="text-gray-700">Target environment</span>
                    <select className={inputClass} value={overrides.target_env} onChange={e => set('target_env', e.target.value)}>
                        {TARGET_ENVS.map(e => <option key={e}>{e}</option>)}
                    </select>
                </label>
                <label className="flex items-center gap-2 text-sm text-gray-700 mt-5">
                    <input type="checkbox" checked={overrides.friday_or_weekend === 1}
                        onChange={e => set('friday_or_weekend', e.target.checked ? 1 : 0)} />
                    Friday / weekend deployment
                </label>
            </div>
            <ErrorBox error={error} />
            {result && (
                <div className="mt-4 flex flex-wrap items-center gap-3 text-sm">
                    <span>Current <b className="tabular-nums">{pct(result.baseline.probability)}</b></span>
                    <TierBadge tier={result.baseline.tier} />
                    <ArrowRight className="w-4 h-4 text-neutral" />
                    <span>Scenario <b className="tabular-nums">{pct(result.scenario.probability)}</b></span>
                    <TierBadge tier={result.scenario.tier} />
                    <span className={`tabular-nums font-semibold ${result.delta_pp <= 0 ? 'text-green-700' : 'text-primary'}`}>
                        {result.delta_pp > 0 ? '+' : ''}{result.delta_pp.toFixed(1)} pts
                    </span>
                </div>
            )}
        </div>
    );
};
