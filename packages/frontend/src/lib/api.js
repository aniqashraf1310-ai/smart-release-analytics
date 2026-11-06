// Thin fetch wrapper: adds the JWT, parses JSON, surfaces the API's error message.
const TOKEN_KEY = 'sra_token';

export const BACKEND_DOWN =
    'Cannot reach the backend API. Make sure it is running on port 5000 (see README: Quick start / Local development).';

export const session = {
    get token() {
        try { return sessionStorage.getItem(TOKEN_KEY); } catch { return null; }
    },
    set(token) {
        try { sessionStorage.setItem(TOKEN_KEY, token); } catch { /* ignore */ }
    },
    clear() {
        try { sessionStorage.removeItem(TOKEN_KEY); } catch { /* ignore */ }
    },
};

export class ApiError extends Error {
    constructor(status, message) {
        super(message);
        this.status = status;
    }
}

export async function api(path, { method = 'GET', body } = {}) {
    const headers = { 'Content-Type': 'application/json' };
    if (session.token) headers.Authorization = `Bearer ${session.token}`;
    let res;
    try {
        res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    } catch {
        throw new ApiError(0, BACKEND_DOWN);
    }
    // 502/503/504 with no JSON body come from the dev-server proxy, not the API:
    // the backend is not running or not reachable.
    if ([502, 503, 504].includes(res.status) && !(res.headers.get('content-type') || '').includes('application/json')) {
        throw new ApiError(res.status, BACKEND_DOWN);
    }
    if (res.status === 401 && path !== '/api/auth/login') {
        session.clear();
        window.dispatchEvent(new Event('sra:logout'));
    }
    const isJson = (res.headers.get('content-type') || '').includes('application/json');
    const data = isJson ? await res.json() : await res.text();
    if (!res.ok) {
        const detail = isJson ? data.detail : data;
        const message = Array.isArray(detail)
            ? detail.map(d => `${d.loc?.slice(-1)[0]}: ${d.msg}`).join('; ')
            : detail || `Request failed (${res.status})`;
        throw new ApiError(res.status, message);
    }
    return data;
}
