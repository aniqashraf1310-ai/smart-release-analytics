import React, { useEffect, useState } from 'react';
import { Gauge } from 'lucide-react';
import { api } from '../lib/api';
import { CHANGE_TYPES, FIX_TYPE_OPTIONS, QA_STATUS_OPTIONS, TARGET_ENVS } from '../lib/constants';
import { RiskSummary } from './RiskPanel';
import { Button, Card, ErrorBox, Field, inputClass } from './ui';

const EMPTY = {
    app_name: 'Mobile Banking App', version: '', target_date: '', qa_status: 'Open', responsible_qa: '',
    ticket_id: '', fix_type: 'Binary Changes', description: '', progression: '',
    change_type: 'feature', target_env: 'production', patch_size_loc: 100, files_changed: 3,
    test_coverage: 70, ticket_complexity: 3, module_failure_rate: 0.05, has_db_migration: false,
    dependency_changes: 0,
};

const RISK_FIELDS = ['change_type', 'target_env', 'patch_size_loc', 'files_changed', 'test_coverage',
    'ticket_complexity', 'module_failure_rate', 'has_db_migration', 'dependency_changes', 'target_date'];

const CreateView = ({ onCreated, releases }) => {
    const [form, setForm] = useState(EMPTY);
    const [preview, setPreview] = useState(null);
    const [error, setError] = useState(null);
    const [busy, setBusy] = useState(false);

    const set = (name, value) => setForm(f => {
        const next = { ...f, [name]: value };
        if (name === 'fix_type' && value === 'Data Model Update') next.has_db_migration = true;
        return next;
    });

    const onText = (e) => {
        const { name, value } = e.target;
        let v = value;
        if (name === 'responsible_qa') v = value.replace(/[^a-zA-Z\s]/g, '');
        if (name === 'version') v = value.replace(/[^0-9.]/g, '');
        if (name === 'ticket_id') v = value.replace(/[^a-zA-Z0-9-,]/g, '');
        set(name, v);
    };

    // Live risk preview while the form is being filled in (nothing is saved).
    const riskKey = JSON.stringify(RISK_FIELDS.map(k => form[k]));
    useEffect(() => {
        const t = setTimeout(() => {
            const body = Object.fromEntries(RISK_FIELDS.map(k => [k, form[k]]));
            if (!body.target_date) delete body.target_date;
            api('/api/ml/score', { method: 'POST', body }).then(setPreview).catch(() => setPreview(null));
        }, 350);
        return () => clearTimeout(t);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [riskKey]);

    const submit = async (e) => {
        e.preventDefault();
        setError(null);
        if (form.ticket_id && releases.some(r => r.ticket_id === form.ticket_id)) {
            setError(`Ticket ID "${form.ticket_id}" has already been logged.`);
            return;
        }
        setBusy(true);
        try {
            const created = await api('/api/releases', { method: 'POST', body: form });
            setForm({ ...EMPTY, app_name: form.app_name });
            onCreated(created);
        } catch (err) {
            setError(err.message);
        } finally {
            setBusy(false);
        }
    };

    const num = (name, props = {}) => (
        <input type="number" name={name} value={form[name]} className={inputClass}
            onChange={e => set(name, e.target.value === '' ? '' : Number(e.target.value))} {...props} />
    );

    return (
        <div className="grid lg:grid-cols-[1fr_22rem] gap-6 items-start">
            <Card title="Insert New App Version" subtitle="Log the release and the change metadata the risk model uses.">
                <form onSubmit={submit} className="space-y-6">
                    <fieldset className="grid grid-cols-1 md:grid-cols-3 gap-4">
                        <Field label="App name"><input name="app_name" value={form.app_name} onChange={onText} className={inputClass} required /></Field>
                        <Field label="Version"><input name="version" value={form.version} onChange={onText} placeholder="3.0.1" className={inputClass} required /></Field>
                        <Field label="Target date"><input type="date" name="target_date" value={form.target_date} onChange={onText} className={inputClass} required /></Field>
                        <Field label="Responsible QA"><input name="responsible_qa" value={form.responsible_qa} onChange={onText} className={inputClass} required /></Field>
                        <Field label="Ticket ID" hint="Must be unique"><input name="ticket_id" value={form.ticket_id} onChange={onText} placeholder="MBA-101" className={inputClass} required /></Field>
                        <Field label="QA status">
                            <select name="qa_status" value={form.qa_status} onChange={onText} className={inputClass}>
                                {QA_STATUS_OPTIONS.map(s => <option key={s}>{s}</option>)}
                            </select>
                        </Field>
                        <Field label="Fix type">
                            <select name="fix_type" value={form.fix_type} onChange={onText} className={inputClass}>
                                {FIX_TYPE_OPTIONS.map(s => <option key={s}>{s}</option>)}
                            </select>
                        </Field>
                    </fieldset>

                    <fieldset>
                        <legend className="text-sm font-semibold text-primary mb-3 flex items-center gap-1">
                            <Gauge className="w-4 h-4" /> Risk metadata
                        </legend>
                        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                            <Field label="Change type">
                                <select value={form.change_type} onChange={e => set('change_type', e.target.value)} className={inputClass}>
                                    {CHANGE_TYPES.map(s => <option key={s}>{s}</option>)}
                                </select>
                            </Field>
                            <Field label="Target environment">
                                <select value={form.target_env} onChange={e => set('target_env', e.target.value)} className={inputClass}>
                                    {TARGET_ENVS.map(s => <option key={s}>{s}</option>)}
                                </select>
                            </Field>
                            <Field label="Patch size (LOC)">{num('patch_size_loc', { min: 0 })}</Field>
                            <Field label="Files changed">{num('files_changed', { min: 0 })}</Field>
                            <Field label="Test coverage (%)">{num('test_coverage', { min: 0, max: 100, step: 0.1 })}</Field>
                            <Field label="Ticket complexity (1-5)">{num('ticket_complexity', { min: 1, max: 5 })}</Field>
                            <Field label="Module failure rate (0-1)" hint="Share of past releases of this module that failed">
                                {num('module_failure_rate', { min: 0, max: 1, step: 0.01 })}
                            </Field>
                            <Field label="Dependency changes">{num('dependency_changes', { min: 0 })}</Field>
                            <label className="flex items-center gap-2 text-sm text-gray-700 mt-6">
                                <input type="checkbox" checked={form.has_db_migration} onChange={e => set('has_db_migration', e.target.checked)} />
                                Includes database migration
                            </label>
                        </div>
                    </fieldset>

                    <Field label="Detailed release notes">
                        <textarea name="description" value={form.description} onChange={onText} rows={3} className={inputClass} required />
                    </Field>
                    <Field label="Progression">
                        <textarea name="progression" value={form.progression} onChange={onText} rows={2} className={inputClass} placeholder="e.g. 50% complete, waiting for dev" />
                    </Field>
                    <ErrorBox error={error} />
                    <Button type="submit" disabled={busy} className="w-full py-3">
                        {busy ? 'Saving…' : 'Save as draft'}
                    </Button>
                    <p className="text-xs text-neutral">
                        The release is saved as a <b>draft</b>. Open it to record the official risk score and submit it for sign-off.
                    </p>
                </form>
            </Card>
            <Card title="Live risk preview" subtitle="Updates as you type; not saved." className="lg:sticky lg:top-4">
                {preview ? <RiskSummary score={preview} compact /> : <p className="text-sm text-neutral">Fill in the risk metadata to see a preview.</p>}
            </Card>
        </div>
    );
};

export default CreateView;
