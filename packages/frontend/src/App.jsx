import React, { useCallback, useEffect, useState } from 'react';
import { Lock } from 'lucide-react';
import AuditView from './components/AuditView';
import CreateView from './components/CreateView';
import GovernanceView from './components/GovernanceView';
import Header, { NAV } from './components/Header';
import LoginView from './components/LoginView';
import ModelView from './components/ModelView';
import Notification from './components/Notification';
import NotificationBell from './components/NotificationBell';
import RegisterView from './components/RegisterView';
import ReleaseDetail from './components/ReleaseDetail';
import SearchView from './components/SearchView';
import UsersView from './components/UsersView';
import { api, session } from './lib/api';
import { can, ROLE_INFO } from './lib/permissions';

const NoAccess = ({ user }) => (
    <div className="bg-white shadow-xl rounded-2xl p-8 text-center border border-primary-100 brand-top">
        <Lock className="w-8 h-8 text-primary mx-auto mb-2" />
        <h2 className="text-xl font-semibold text-gray-800">Not available for your role</h2>
        <p className="text-sm text-neutral mt-1">{ROLE_INFO[user.role]?.label || 'Your account'} does not have access to this page.</p>
    </div>
);

const App = () => {
    const [user, setUser] = useState(null);
    const [authPage, setAuthPage] = useState('login');
    const [checkingSession, setCheckingSession] = useState(true);
    const [page, setPage] = useState('search');
    const [releases, setReleases] = useState([]);
    const [pendingAccounts, setPendingAccounts] = useState(0);
    const [openId, setOpenId] = useState(null);
    const [error, setError] = useState(null);
    const [notification, setNotification] = useState({ message: null, type: null });
    const [bellRefresh, setBellRefresh] = useState(0);
    const toastTimer = React.useRef(null);
    const loadReleasesRef = React.useRef(null);

    const notify = useCallback((message, type = 'success', extra = {}) => {
        setNotification({ message, type, ...extra });
        clearTimeout(toastTimer.current);
        toastTimer.current = setTimeout(() => setNotification({ message: null, type: null }), extra.releaseId ? 7000 : 4000);
    }, []);

    const onNotificationToast = useCallback(({ title, message, releaseId }) => {
        notify(message, 'info', { title, releaseId });
        loadReleasesRef.current?.();
    }, [notify]);

    // Restore a session after a page reload.
    useEffect(() => {
        if (!session.token) { setCheckingSession(false); return; }
        api('/api/auth/me').then(setUser).catch(() => session.clear()).finally(() => setCheckingSession(false));
    }, []);

    useEffect(() => {
        const onLogout = () => setUser(null);
        window.addEventListener('sra:logout', onLogout);
        return () => window.removeEventListener('sra:logout', onLogout);
    }, []);

    const loadReleases = useCallback(() => {
        api('/api/releases').then(d => { setReleases(d.releases); setError(null); }).catch(e => setError(`Could not load releases: ${e.message}`));
    }, []);
    loadReleasesRef.current = loadReleases;

    const loadPending = useCallback(() => {
        if (can(user, 'users.manage')) {
            api('/api/users?status=PENDING').then(d => setPendingAccounts(d.users.length)).catch(() => {});
        }
    }, [user]);

    useEffect(() => { if (user) { loadReleases(); loadPending(); } }, [user, loadReleases, loadPending]);

    const upsert = (r) => {
        setReleases(prev => {
            const exists = prev.some(x => x.id === r.id);
            return exists ? prev.map(x => (x.id === r.id ? { ...x, ...r } : x)) : [...prev, r];
        });
        setBellRefresh(k => k + 1);
    };

    const logout = () => { session.clear(); setUser(null); setReleases([]); setPage('search'); setAuthPage('login'); };

    if (checkingSession) return <div className="p-8 text-neutral">Loading…</div>;
    if (!user) {
        return authPage === 'register'
            ? <RegisterView onBack={() => setAuthPage('login')} />
            : <LoginView onLogin={(u) => { setUser(u); setPage('search'); }} onRegister={() => setAuthPage('register')} />;
    }

    const allowed = (id) => can(user, NAV.find(n => n.id === id)?.perm);

    return (
        <div className="min-h-screen p-4 sm:p-8 font-sans">
            <Header currentPage={page} setCurrentPage={(p) => { setPage(p); if (p === 'users') loadPending(); }}
                user={user} onLogout={logout} pendingAccounts={pendingAccounts}
                bell={<NotificationBell refreshKey={bellRefresh} onToast={onNotificationToast}
                    onOpenRelease={(id) => setOpenId(id)}
                    onOpenPage={(p) => { setPage(p); if (p === 'users') loadPending(); }} />} />
            <Notification message={notification.message} title={notification.title} type={notification.type}
                onClose={() => setNotification({ message: null, type: null })}
                onClick={notification.releaseId ? () => { setOpenId(notification.releaseId); setNotification({ message: null, type: null }); } : undefined} />
            <main className="max-w-6xl mx-auto">
                {error && <div className="bg-red-100 border-l-4 border-red-500 text-red-700 p-4 mb-6">{error}</div>}
                {!allowed(page) ? <NoAccess user={user} /> : (
                    <>
                        {page === 'search' && <SearchView releases={releases} user={user} onOpen={r => setOpenId(r.id)} />}
                        {page === 'create' && (
                            <CreateView releases={releases} onCreated={(r) => {
                                upsert(r);
                                notify('Draft saved. Record its risk score and submit it for review.');
                                setPage('search');
                                setOpenId(r.id);
                            }} />
                        )}
                        {page === 'governance' && <GovernanceView user={user} />}
                        {page === 'users' && <UsersView user={user} />}
                        {page === 'audit' && <AuditView />}
                        {page === 'model' && <ModelView />}
                    </>
                )}
            </main>
            {openId && (
                <ReleaseDetail
                    releaseId={openId}
                    user={user}
                    onClose={() => { setOpenId(null); loadReleases(); }}
                    onChanged={upsert}
                    onDeleted={(id) => { setReleases(prev => prev.filter(r => r.id !== id)); setOpenId(null); notify('Draft deleted'); }}
                />
            )}
        </div>
    );
};

export default App;
