import React, { useState } from 'react';
import { ArrowLeft, CheckCircle2, Hourglass, UserPlus } from 'lucide-react';
import { api } from '../lib/api';
import { ROLE_INFO, ROLES } from '../lib/permissions';
import PermissionMatrix from './PermissionMatrix';
import { Button, ErrorBox, Field, inputClass } from './ui';

const RegisterView = ({ onBack }) => {
    const [form, setForm] = useState({ full_name: '', username: '', email: '', password: '', confirm: '', requested_role: '' });
    const [error, setError] = useState(null);
    const [busy, setBusy] = useState(false);
    const [done, setDone] = useState(null);
    const [showMatrix, setShowMatrix] = useState(false);

    const set = (k) => (e) => setForm(f => ({ ...f, [k]: e.target.value }));
    const pwOk = form.password.length >= 8 && /[A-Za-z]/.test(form.password) && /\d/.test(form.password);

    const submit = async (e) => {
        e.preventDefault();
        setError(null);
        if (!form.requested_role) return setError('Choose the role you need.');
        if (!pwOk) return setError('Password must be at least 8 characters and contain a letter and a digit.');
        if (form.password !== form.confirm) return setError('Passwords do not match.');
        setBusy(true);
        try {
            const { confirm, ...body } = form;
            await api('/api/auth/register', { method: 'POST', body: { ...body, email: body.email || null } });
            setDone(form);
        } catch (err) {
            setError(err.message);
        } finally {
            setBusy(false);
        }
    };

    if (done) {
        return (
            <div className="min-h-screen flex items-center justify-center p-4">
                <div className="bg-white shadow-xl rounded-2xl p-8 border border-primary-100 brand-top w-full max-w-md text-center">
                    <Hourglass className="w-10 h-10 text-primary mx-auto mb-3" />
                    <h1 className="text-2xl font-bold text-gray-800">Registration received</h1>
                    <p className="text-gray-600 mt-2">
                        Your account <b>{done.username.toLowerCase()}</b> is waiting for a Release Manager to approve it.
                        You requested the <b>{ROLE_INFO[done.requested_role].label}</b> role. The Release Manager confirms or changes this when they approve you.
                    </p>
                    <p className="text-sm text-neutral mt-3">You can sign in once your account has been approved.</p>
                    <Button className="mt-6 w-full" onClick={onBack}><ArrowLeft className="w-4 h-4" /> Back to sign in</Button>
                </div>
            </div>
        );
    }

    return (
        <div className="min-h-screen p-4 sm:p-8">
            <div className="max-w-3xl mx-auto bg-white shadow-xl rounded-2xl p-6 sm:p-8 border border-primary-100 brand-top">
                <button onClick={onBack} className="text-sm text-primary flex items-center gap-1 mb-4 hover:underline">
                    <ArrowLeft className="w-4 h-4" /> Back to sign in
                </button>
                <h1 className="text-2xl font-extrabold text-gray-800">Create an account</h1>
                <p className="text-sm text-neutral mb-6">
                    Choose the role you need. A Release Manager reviews every new account before it can be used.
                </p>

                <form onSubmit={submit} className="space-y-6">
                    <fieldset>
                        <legend className="text-sm font-semibold text-gray-700 mb-2">1. Your role</legend>
                        <div className="grid sm:grid-cols-3 gap-3" role="radiogroup">
                            {ROLES.map(r => {
                                const info = ROLE_INFO[r];
                                const Icon = info.icon;
                                const selected = form.requested_role === r;
                                return (
                                    <button type="button" key={r} role="radio" aria-checked={selected}
                                        onClick={() => setForm(f => ({ ...f, requested_role: r }))}
                                        className={`text-left flex flex-col justify-start rounded-xl border-2 p-4 transition ${selected ? 'border-primary bg-primary-50 shadow-md' : 'border-gray-200 hover:border-gray-300'}`}>
                                        <div className="flex items-center justify-between w-full">
                                            <span className="flex items-center gap-2 font-semibold text-gray-800">
                                                <Icon className="w-5 h-5" style={{ color: info.accent }} /> {info.label}
                                            </span>
                                            {selected && <CheckCircle2 className="w-5 h-5 text-primary" />}
                                        </div>
                                        <div className="text-xs text-neutral mt-0.5">Access level {info.level} of 3</div>
                                        <p className="text-sm text-gray-600 mt-2">{info.tagline}</p>
                                        <ul className="mt-2 space-y-0.5 text-xs text-gray-700">
                                            {info.abilities.map(a => <li key={a}>✓ {a}</li>)}
                                            {info.limits.map(a => <li key={a} className="text-gray-400">✕ {a}</li>)}
                                        </ul>
                                    </button>
                                );
                            })}
                        </div>
                        <button type="button" className="text-xs text-primary mt-2 hover:underline" onClick={() => setShowMatrix(s => !s)}>
                            {showMatrix ? 'Hide' : 'Compare'} full permissions
                        </button>
                        {showMatrix && <div className="mt-2 border rounded-lg p-3"><PermissionMatrix highlightRole={form.requested_role} /></div>}
                    </fieldset>

                    <fieldset className="grid sm:grid-cols-2 gap-4">
                        <legend className="text-sm font-semibold text-gray-700 mb-2 col-span-full">2. Your details</legend>
                        <Field label="Full name"><input className={inputClass} value={form.full_name} onChange={set('full_name')} required minLength={2} autoComplete="name" /></Field>
                        <Field label="Work email (optional)"><input type="email" className={inputClass} value={form.email} onChange={set('email')} autoComplete="email" /></Field>
                        <Field label="Username" hint="3–32 characters: letters, digits, dot, dash or underscore">
                            <input className={inputClass} value={form.username} onChange={set('username')} required pattern="[A-Za-z0-9._\-]{3,32}" autoComplete="username" />
                        </Field>
                        <div />
                        <Field label="Password" hint="At least 8 characters with a letter and a digit">
                            <input type="password" className={inputClass} value={form.password} onChange={set('password')} required autoComplete="new-password" />
                        </Field>
                        <Field label="Confirm password">
                            <input type="password" className={inputClass} value={form.confirm} onChange={set('confirm')} required autoComplete="new-password" />
                        </Field>
                    </fieldset>

                    <ErrorBox error={error} />
                    <Button type="submit" disabled={busy} className="w-full py-3">
                        <UserPlus className="w-4 h-4" /> {busy ? 'Submitting…' : 'Request account'}
                    </Button>
                </form>
            </div>
        </div>
    );
};

export default RegisterView;
