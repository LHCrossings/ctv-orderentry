/**
 * Shared date/time entry helpers for the portal's text inputs.
 *
 * Injected into every HTML page by the middleware in src/web/app.py (same
 * mechanism as broadcast-health.js — there is no shared base template). It
 * lands before </head>, so these are defined before any page's inline script
 * runs and before any inline on* handler can fire.
 *
 * Wire a field up with:
 *     <input onblur="formatDateInput(this)" />        date  → MM/DD/YYYY
 *     <input oninput="fmtAirtime(this)" />            time  → HH:MM:SS
 *
 * NOTE ON NAMING: there is deliberately no global `formatDate` here.
 * billing/monthly_logs.html defines its own formatDate(iso) that renders
 * "Jun 29, 2026" for display — a global of that name would clobber it.
 */

/**
 * Parse loose user input into an ISO yyyy-mm-dd string, or null if it can't
 * be understood. Returning null (rather than guessing) is what lets callers
 * leave a half-typed value alone instead of mangling it on an accidental blur.
 */
function parseDateInput(val) {
    val = (val || '').trim();
    if (!val) return null;
    const year = new Date().getFullYear();
    let m, d, y;

    const parts = val.split(/[\/\-]/);
    if (parts.length === 2 || parts.length === 3) {
        // Separator form (6/29/2026, 6-29-26, 6/29). Handled before the digit
        // branches: stripping the separators first leaves an ambiguous 7-digit
        // string that matches nothing, which is why an already-typed
        // "6/1/2026" used to come back unformatted.
        if (!parts.every(p => /^\d{1,4}$/.test(p))) return null;
        m = +parts[0];
        d = +parts[1];
        y = parts.length === 3 ? +parts[2] : year;
        if (y < 100) y += 2000;
    } else if (/^\d{3}$/.test(val)) {    // MDD      e.g. 629 → Jun 29
        m = +val.slice(0, 1); d = +val.slice(1); y = year;
    } else if (/^\d{4}$/.test(val)) {    // MMDD     e.g. 0629
        m = +val.slice(0, 2); d = +val.slice(2); y = year;
    } else if (/^\d{6}$/.test(val)) {    // MMDDYY
        m = +val.slice(0, 2); d = +val.slice(2, 4); y = 2000 + +val.slice(4);
    } else if (/^\d{8}$/.test(val)) {    // MMDDYYYY
        m = +val.slice(0, 2); d = +val.slice(2, 4); y = +val.slice(4);
    } else {
        return null;
    }

    // Nonsense like 1350 stays as typed rather than becoming "2026-13-50" and
    // being sent onward as a date filter.
    if (!(m >= 1 && m <= 12) || !(d >= 1 && d <= 31)) return null;
    return `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
}

/** Normalise a date field in place to MM/DD/YYYY. Unparseable input is left alone. */
function formatDateInput(el) {
    const parsed = parseDateInput(el.value);
    if (parsed) {
        const [y, m, d] = parsed.split('-');
        el.value = `${m}/${d}/${y}`;
    }
}

/** Digit-entry for an airtime field: types straight through to HH:MM:SS. */
function fmtAirtime(input) {
    const digits = input.value.replace(/\D/g, '').slice(0, 6);
    let v = digits;
    if (digits.length > 4)      v = digits.slice(0, 2) + ':' + digits.slice(2, 4) + ':' + digits.slice(4);
    else if (digits.length > 2) v = digits.slice(0, 2) + ':' + digits.slice(2);
    input.value = v;
    input.setSelectionRange(v.length, v.length);
}
