import React from 'react';
import { Activity, FileClock, LogOut, PlusCircle, Scale, Search, Users } from 'lucide-react';
import { can, ROLE_INFO } from '../lib/permissions';
import Brand from './Brand';

// Each page declares the permission it needs; the menu only shows what the
// signed-in role can use (App.jsx guards the pages themselves too).
export const NAV = [
    { id: 'search', label: 'Releases', icon: Search, perm: 'release.view' },
    { id: 'create', label: 'New Release', icon: PlusCircle, perm: 'release.create' },
    { id: 'governance', label: 'Governance', icon: Scale, perm: 'release.view' },
    { id: 'users', label: 'Accounts', icon: Users, perm: 'users.manage' },
    { id: 'audit', label: 'Audit Log', icon: FileClock, perm: 'audit.view' },
    { id: 'model', label: 'Risk Model', icon: Activity, perm: 'model.view' },
];

const Header = ({ currentPage, setCurrentPage, user, onLogout, pendingAccounts = 0, bell = null }) => (
    <header className="max-w-6xl mx-auto brand-gradient shadow-xl shadow-primary/20 rounded-2xl p-4 sm:p-5 mb-6 text-white">
        <div className="flex flex-wrap items-center justify-between gap-3">
            <Brand light />
            <div className="flex items-center gap-2 sm:gap-3 text-sm">
                {bell}
                <div className="text-right hidden sm:block">
                    <div className="font-semibold">{user.full_name}</div>
                    <div className="text-xs text-white/80">{ROLE_INFO[user.role]?.label}</div>
                </div>
                <button onClick={onLogout} title="Sign out" aria-label="Sign out"
                    className="p-2 rounded-xl bg-white/15 hover:bg-white/25 ring-1 ring-white/30 transition">
                    <LogOut className="w-4 h-4" />
                </button>
            </div>
        </div>
        <nav className="flex flex-wrap gap-2 mt-4">
            {NAV.filter(n => can(user, n.perm)).map(({ id, label, icon: Icon }) => (
                <button
                    key={id}
                    onClick={() => setCurrentPage(id)}
                    className={`flex items-center px-3 py-2 rounded-xl font-semibold text-sm transition ${
                        currentPage === id ? 'bg-white text-primary-700 shadow-md' : 'bg-white/15 hover:bg-white/25 text-white ring-1 ring-white/20'
                    }`}
                >
                    <Icon className="w-4 h-4 mr-1.5" />
                    {label}
                    {id === 'users' && pendingAccounts > 0 && (
                        <span className="ml-1.5 text-xs font-bold bg-amber-300 text-slate-900 rounded-full px-1.5">{pendingAccounts}</span>
                    )}
                </button>
            ))}
        </nav>
    </header>
);

export default Header;
