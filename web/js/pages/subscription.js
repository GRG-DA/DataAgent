/* ═══════════════════════════════════════════
   Subscription Page — plan status, trial, billing-site link.
   Purchases happen on the external billing site; plans are granted
   keylessly to the buyer's account (ADR-004).
   ═══════════════════════════════════════════ */

const SubscriptionPage = {
    async init() {},
    async destroy() {},

    async render() {
        const pc = document.getElementById('page-content');
        let planLine = 'Loading plan…';
        let planCode = 'free';
        let renewal = null;
        try {
            const sub = await API.mySubscription();
            planCode = sub.plan || 'free';
            renewal = sub.renewal || null;
            planLine = `Current plan: <strong>${App.escapeHtml(sub.plan || 'free')}</strong>`;
        } catch (e) {
            planLine = App.escapeHtml(e.message || 'Plan unavailable');
        }

        const billingCfg = (window.App && App.state && App.state.billing) || null;
        const externalUrl =
            (billingCfg && billingCfg.external_url) ||
            (window.APP_CONFIG && window.APP_CONFIG.billing && window.APP_CONFIG.billing.external_url) ||
            '';

        // Renewal state for subscription grants (ADR-004 keyless monthly plans).
        let renewalHtml = '';
        if (renewal && renewal.renews_at && planCode !== 'free') {
            const renewsOn = String(renewal.renews_at).slice(0, 10);
            const lastChecked = renewal.synced_at ? String(renewal.synced_at).slice(0, 10) : '';
            const stateLine = renewal.cancel_at_period_end
                ? `Cancels on ${renewsOn} — access continues until then, no further renewals.`
                : (renewal.auto_renew
                    ? `Auto-renews on ${renewsOn}.`
                    : `Renews on ${renewsOn}.`);
            renewalHtml = `
                <div id="renewal-block" class="border-t border-gray-100 dark:border-gray-700 pt-4 space-y-2">
                    <p class="text-sm text-secondary" id="renewal-status-line">${stateLine}</p>
                    ${lastChecked ? `<p class="text-xs text-muted">Last checked ${App.escapeHtml(lastChecked)}.</p>` : ''}
                    ${externalUrl ? `<a id="manage-subscription" class="btn btn-primary btn-sm" href="${App.escapeHtml(externalUrl)}" target="_blank" rel="noopener noreferrer">
                        <i class="fas fa-external-link-alt mr-1"></i>Manage on the billing site
                    </a>` : ''}
                </div>`;
        }

        pc.innerHTML = `
        <div class="max-w-2xl mx-auto animate-fade-in space-y-6">
            <div class="page-header">
                <div>
                    <h2><i class="fas fa-crown mr-2 text-indigo-500"></i>Subscription</h2>
                    <p class="text-secondary mt-1">Manage your plan, trial, and billing.</p>
                </div>
            </div>

            <div class="bg-white dark:bg-gray-800 rounded-xl border border-gray-100 dark:border-gray-700 p-8 space-y-4">
                <p class="text-sm text-secondary">${planLine}</p>

                ${renewalHtml}

                ${planCode === 'free' && externalUrl ? `
                <a class="btn btn-primary btn-sm" href="${App.escapeHtml(externalUrl)}" target="_blank" rel="noopener noreferrer">
                    <i class="fas fa-external-link-alt mr-1"></i>Upgrade on the billing site
                </a>` : ''}

                <div id="trial-block" class="border-t border-gray-100 dark:border-gray-700 pt-4">
                    <p class="text-xs text-secondary" id="trial-status-line">Checking trial eligibility…</p>
                    <button id="trial-start-btn" class="btn btn-primary btn-sm mt-2" style="display:none">
                        <i class="fas fa-bolt mr-1"></i>Start 14-day Pro trial
                    </button>
                    <p id="trial-status" class="text-xs mt-2" role="status" aria-live="polite"></p>
                </div>

                <div id="product-key-block" class="border-t border-gray-100 dark:border-gray-700 pt-4">
                    <p class="text-xs text-secondary">Have a product key? Activate it to unlock your paid plan on this account.</p>
                    <div class="flex gap-2 mt-2">
                        <input id="product-key-input" class="form-input" type="text" autocomplete="off"
                               spellcheck="false" placeholder="XXXX-XXXX-XXXX-XXXX" aria-label="Product key">
                        <button id="product-key-btn" class="btn btn-primary btn-sm">
                            <i class="fas fa-key mr-1"></i>Activate
                        </button>
                    </div>
                    <p id="product-key-status" class="text-xs mt-2" role="status" aria-live="polite"></p>
                </div>

                <div class="border-t border-gray-100 dark:border-gray-700 pt-4 text-xs text-muted">
                    Payments are handled on a separate billing site — this app never collects card details.
                    ${externalUrl ? '' : 'Billing site URL is not configured for this environment.'}
                </div>
            </div>
        </div>`;

        const trialBtn = document.getElementById('trial-start-btn');
        const trialLine = document.getElementById('trial-status-line');
        const trialStatus = document.getElementById('trial-status');
        if (trialBtn && trialLine && trialStatus && window.API) {
            const refreshTrial = async () => {
                try {
                    const t = await API.trialStatus();
                    if (t && t.active) {
                        trialLine.textContent = `Pro trial active${t.ends_at ? ` — valid until ${t.ends_at.slice(0, 10)}` : ''}. Paid features unlocked.`;
                        trialBtn.style.display = 'none';
                    } else if (t && t.eligible && t.enabled !== false) {
                        trialLine.textContent = `Try Pro free for ${t.days || 14} days — exports, automation, storytelling included. No card required.`;
                        trialBtn.style.display = '';
                    } else {
                        trialLine.textContent = 'Pro trial already used on this account.';
                        trialBtn.style.display = 'none';
                    }
                } catch (e) {
                    trialLine.textContent = '';
                    trialBtn.style.display = 'none';
                }
            };
            trialBtn.addEventListener('click', async () => {
                trialBtn.disabled = true;
                trialStatus.textContent = 'Starting trial…';
                try {
                    const res = await API.startTrial();
                    trialStatus.textContent = `✅ ${res.message || 'Pro trial active — paid features unlocked.'}`;
                    setTimeout(() => window.location.reload(), 1500);
                } catch (e) {
                    trialStatus.textContent = `❌ ${App.escapeHtml(e.message || 'Trial unavailable')}`;
                } finally {
                    trialBtn.disabled = false;
                }
            });
            refreshTrial();
        }

        const keyInput = document.getElementById('product-key-input');
        const keyBtn = document.getElementById('product-key-btn');
        const keyStatus = document.getElementById('product-key-status');
        if (keyInput && keyBtn && keyStatus && window.API) {
            const activateKey = async () => {
                const key = (keyInput.value || '').trim();
                if (!key) {
                    keyStatus.textContent = 'Enter a product key first.';
                    return;
                }
                keyBtn.disabled = true;
                keyStatus.textContent = 'Verifying key…';
                try {
                    await API.activateProductKey(key);
                    keyStatus.textContent = '✅ Product key activated — reloading plan…';
                    setTimeout(() => window.location.reload(), 1200);
                } catch (e) {
                    keyStatus.textContent = `❌ ${App.escapeHtml(e.message || 'Key activation failed')}`;
                } finally {
                    keyBtn.disabled = false;
                }
            };
            keyBtn.addEventListener('click', activateKey);
            keyInput.addEventListener('keydown', (ev) => {
                if (ev.key === 'Enter') activateKey();
            });
        }
    },
};

window.SubscriptionPage = SubscriptionPage;
(window._PAGES = window._PAGES || {})["#subscription"] = SubscriptionPage;
