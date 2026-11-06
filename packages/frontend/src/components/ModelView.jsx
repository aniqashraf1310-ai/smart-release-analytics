import React, { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { TIER_CONFIG, fmtDateTime, pct } from '../lib/constants';
import { Card, ErrorBox, TierBadge } from './ui';

const Metric = ({ label, value, hint }) => (
    <div className="rounded-lg bg-gray-50 border p-3">
        <div className="text-xs text-neutral">{label}</div>
        <div className="text-xl font-bold text-gray-800 tabular-nums">{value}</div>
        {hint && <div className="text-xs text-neutral">{hint}</div>}
    </div>
);

const ModelView = () => {
    const [model, setModel] = useState(null);
    const [drift, setDrift] = useState(null);
    const [error, setError] = useState(null);

    useEffect(() => {
        api('/api/ml/model').then(setModel).catch(e => setError(e.message));
        api('/api/ml/drift').then(setDrift).catch(() => {});
    }, []);

    if (error) return <ErrorBox error={error} />;
    if (!model) return <p className="text-neutral">Loading…</p>;
    const m = model.metrics;
    const test = m.test;
    const sel = test.selected_model_calibrated;
    const cv = m.cross_validation_5fold;

    return (
        <div className="space-y-6">
            <Card title="Predictive risk model" subtitle={`${model.algorithm} · version ${model.version_tag} · trained ${fmtDateTime(model.trained_at)}`}>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                    <Metric label="ROC-AUC (test)" value={sel.roc_auc.toFixed(3)} hint={`ceiling ${test.bayes_ceiling_roc_auc.toFixed(3)}`} />
                    <Metric label="PR-AUC (test)" value={sel.pr_auc.toFixed(3)} hint={`no-skill ${test.failure_rate.toFixed(3)}`} />
                    <Metric label="Brier score" value={sel.brier.toFixed(3)} hint="lower is better" />
                    <Metric label="Training records" value={m.data.n_total.toLocaleString()} hint={`${m.data.n_real} real outcomes`} />
                </div>
                <p className="text-sm text-gray-700 mt-4"><b>Model selection:</b> {m.selection.reason}.</p>
                <p className="text-xs text-neutral mt-1">
                    The ceiling is the best ROC-AUC any model could reach on this data, since hidden factors and noise are deliberately part of the synthetic generator.
                </p>
                <div className="overflow-x-auto mt-4">
                    <table className="w-full text-sm">
                        <thead className="text-left text-neutral"><tr><th className="py-1">5-fold CV (mean ± sd)</th><th>ROC-AUC</th><th>PR-AUC</th><th>Brier</th></tr></thead>
                        <tbody>
                            {Object.entries(cv).map(([name, r]) => (
                                <tr key={name} className="border-t">
                                    <td className="py-1.5">{name.replaceAll('_', ' ')}</td>
                                    {['roc_auc', 'pr_auc', 'brier'].map(k => <td key={k} className="tabular-nums">{r[k].mean.toFixed(3)} ± {r[k].std.toFixed(3)}</td>)}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </Card>

            <Card title="Risk tiers" subtitle="Sized to review capacity; observed failure rates on the held-out test set.">
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                    {Object.entries(test.tiers).map(([tier, t]) => (
                        <div key={tier} className="rounded-lg border p-3" style={{ borderLeft: `6px solid ${TIER_CONFIG[tier].border}` }}>
                            <TierBadge tier={tier} />
                            <div className="text-2xl font-bold tabular-nums mt-1">{pct(t.observed_failure_rate, 0)}</div>
                            <div className="text-xs text-neutral">actually failed · {pct(t.share, 0)} of releases</div>
                        </div>
                    ))}
                </div>
                <p className="text-xs text-neutral mt-3">
                    Cut-offs: MEDIUM ≥ {pct(model.thresholds.medium)}, HIGH ≥ {pct(model.thresholds.high)}, CRITICAL ≥ {pct(model.thresholds.critical)}.
                </p>
            </Card>

            <Card title="Input drift" subtitle="Population Stability Index of recently scored releases against the training data.">
                {!drift ? <p className="text-sm text-neutral">No data.</p> : (
                    <>
                        <p className="text-sm mb-3">Overall: <b>{drift.overall}</b> ({drift.n} recent scores)</p>
                        <div className="grid grid-cols-2 md:grid-cols-5 gap-2 text-xs">
                            {Object.entries(drift.features).map(([f, d]) => (
                                <div key={f} className={`rounded border p-2 ${d.status === 'retrain' ? 'border-red-300 bg-red-50' : d.status === 'moderate' ? 'border-amber-300 bg-amber-50' : ''}`}>
                                    <div className="text-neutral truncate">{f}</div>
                                    <div className="font-semibold tabular-nums">{d.psi.toFixed(3)} · {d.status}</div>
                                </div>
                            ))}
                        </div>
                    </>
                )}
            </Card>
        </div>
    );
};

export default ModelView;
