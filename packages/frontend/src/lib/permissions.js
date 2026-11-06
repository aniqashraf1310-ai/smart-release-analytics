// The server decides what each role may do (GET /api/auth/me returns the
// user's permission codes). The UI only uses this to show or hide actions;
// the API and the database enforce the same rules independently.
import { Code2, ClipboardCheck, Crown } from 'lucide-react';

export const can = (user, permission) => !!user?.permissions?.includes(permission);

export const ROLE_INFO = {
    DEVELOPER: {
        label: 'Developer',
        icon: Code2,
        level: 1,
        tagline: 'Prepares releases and submits them for approval.',
        abilities: ['Create and edit release drafts', 'Record the risk score and use the what-if simulator', 'Submit releases for review'],
        limits: ['Cannot approve any release', 'Cannot deploy or manage accounts'],
        badge: 'bg-cyan-50 text-cyan-800 border-cyan-300',
        accent: '#0891B2',
    },
    QA_LEAD: {
        label: 'QA Lead',
        icon: ClipboardCheck,
        level: 2,
        tagline: 'Confirms testing is complete: the first line of approval.',
        abilities: ['Create and edit releases (QA input)', 'Approve or reject releases as QA Lead', 'Acknowledge risk drivers on higher-risk releases'],
        limits: ['Cannot submit, give final approval or deploy', 'Cannot manage accounts'],
        badge: 'bg-pink-50 text-pink-700 border-pink-300',
        accent: '#DB2777',
    },
    RELEASE_MANAGER: {
        label: 'Release Manager',
        icon: Crown,
        level: 3,
        tagline: 'Gives the final go-live approval and runs deployments.',
        abilities: ['Final approval (two RMs needed for HIGH and CRITICAL risk)', 'Deploy, record outcomes and use emergency break-glass', 'Approve new accounts, change roles, disable accounts'],
        limits: ['Cannot create or submit releases', 'Cannot review their own break-glass or change their own account'],
        badge: 'bg-primary-50 text-primary-700 border-primary-200',
        accent: '#7C3AED',
    },
};

export const ROLES = Object.keys(ROLE_INFO);
