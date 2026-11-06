import React, { useState } from 'react';
import { LogIn } from 'lucide-react';
import { api, session } from '../lib/api';
import Brand from './Brand';
import { Button, ErrorBox, Field, inputClass } from './ui';

const DEMO_USERS = [
    ['dev1', 'Developer'], ['dev2', 'Developer'], ['qa1', 'QA Lead'], ['qa2', 'QA Lead'],
    ['rm1', 'Release Manager'], ['rm2', 'Release Manager'],
];

const LoginView = ({ onLogin, onRegister }) => {
    const [username, setUsername] = useState('dev1');
    const [password, setPassword] = useState('Passw0rd!');
    const [error, setError] = useState(null);
    const [busy, setBusy] = useState(false);

    const submit = async (e) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        try {
            const res = await api('/api/auth/login', { method: 'POST', body: { username, password } });
            session.set(res.token);
            onLogin(res.user);
        } catch (err) {
            setError(err.message);
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="min-h-screen flex items-center justify-center p-4">
            <div className="bg-white shadow-2xl shadow-primary/10 rounded-2xl p-8 border border-primary-100 brand-top w-full max-w-md">
                <Brand size="md" />
                <p className="text-sm text-neutral mt-3 mb-6">Risk-aware release governance, powered by predictive analytics</p>
                <form onSubmit={submit} className="space-y-4">
                    <Field label="Username">
                        <input className={inputClass} value={username} onChange={e => setUsername(e.target.value)} autoComplete="username" />
                    </Field>
                    <Field label="Password">
                        <input type="password" className={inputClass} value={password} onChange={e => setPassword(e.target.value)} autoComplete="current-password" />
                    </Field>
                    <ErrorBox error={error} />
                    <Button type="submit" disabled={busy} className="w-full">
                        <LogIn className="w-4 h-4" /> {busy ? 'Signing in…' : 'Sign in'}
                    </Button>
                </form>
                <p className="text-sm text-gray-600 mt-4 text-center">
                    New here? <button type="button" onClick={onRegister} className="text-primary font-semibold hover:underline">Create an account</button>
                </p>
                <div className="mt-6">
                    <p className="text-xs font-semibold text-gray-500 mb-2">Demo accounts (password Passw0rd!)</p>
                    <div className="flex flex-wrap gap-1.5">
                        {DEMO_USERS.map(([u, role]) => (
                            <button key={u} type="button" onClick={() => setUsername(u)}
                                className={`text-xs px-2 py-1 rounded border ${username === u ? 'border-primary text-primary bg-primary-50' : 'border-gray-200 text-gray-600 hover:bg-gray-50'}`}>
                                {u} <span className="text-neutral">· {role}</span>
                            </button>
                        ))}
                    </div>
                </div>
            </div>
        </div>
    );
};

export default LoginView;
