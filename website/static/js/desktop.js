function formatDesktopPrice(symbol, value) {
    return `${symbol}${value}`;
}

/* ── macOS Install Tutorial Modal ─────────────────────────────── */

function openMacTutorial() {
    const overlay = document.getElementById('macos-tutorial-overlay');
    if (overlay) {
        overlay.classList.add('active');
        document.body.style.overflow = 'hidden';
    }
}

function closeMacTutorial() {
    const overlay = document.getElementById('macos-tutorial-overlay');
    if (overlay) {
        overlay.classList.remove('active');
        document.body.style.overflow = '';
    }
}

function handleMacDownload(event) {
    // Let the download proceed via the default link behaviour,
    // then show the install tutorial modal on top.
    setTimeout(openMacTutorial, 300);
}

function applyDesktopPricing(code) {
    const flat = {
        USD: { symbol: '$', price: 10 },
        GBP: { symbol: '£', price: 10 },
        EUR: { symbol: '€', price: 10 },
        AUD: { symbol: 'A$', price: 10 },
        CAD: { symbol: 'C$', price: 10 },
    };
    const rates = {
        JPY: { symbol: '¥', rate: 162 },
        INR: { symbol: '₹', rate: 90 },
        BRL: { symbol: 'R$', rate: 5.5 },
        MXN: { symbol: 'MX$', rate: 18.5 },
    };

    let symbol = '$';
    let price = 10;
    if (flat[code]) {
        symbol = flat[code].symbol;
        price = flat[code].price;
    } else if (rates[code]) {
        symbol = rates[code].symbol;
        price = Math.round(10 * rates[code].rate);
    }

    document.querySelectorAll('[data-desktop-price-inline]').forEach((el) => {
        el.textContent = formatDesktopPrice(symbol, price);
    });
    document.querySelectorAll('[data-desktop-price]').forEach((el) => {
        el.innerHTML = `${formatDesktopPrice(symbol, price)}<span>one-time</span>`;
    });
}

async function startDesktopCheckout() {
    const enabled = document.body.dataset.desktopCheckoutEnabled === 'true';
    if (!enabled) {
        window.alert('Desktop checkout is not configured yet.');
        return;
    }

    const emailInput = document.getElementById('desktop-email');
    const email = (emailInput?.value || '').trim().toLowerCase();
    if (!email || !email.includes('@')) {
        window.alert('Enter a valid delivery email.');
        emailInput?.focus();
        return;
    }

    const button = document.getElementById('desktop-buy-btn');
    const previous = button?.textContent || '';
    if (button) {
        button.disabled = true;
        button.textContent = 'Opening checkout…';
    }

    try {
        const res = await fetch('/api/desktop/checkout', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email }),
        });
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.detail || 'Checkout unavailable.');
        }
        if (data.checkout_url) {
            window.location.href = data.checkout_url;
            return;
        }
        throw new Error('No checkout URL returned.');
    } catch (error) {
        window.alert(error.message || 'Could not start checkout.');
        if (button) {
            button.disabled = false;
            button.textContent = previous || 'Buy Desktop License';
        }
    }
}

function renderDesktopOrderStatus(data) {
    const root = document.getElementById('desktop-order-status');
    if (!root) {
        return;
    }

    if (data.pending) {
        root.innerHTML = '<p>Payment confirmed. Waiting for license fulfilment…</p>';
        return;
    }

    if (!data.ready) {
        root.innerHTML = '<p>We could not find this order yet.</p>';
        return;
    }

    const windowsBlock = data.download_url
        ? `<a class="btn btn-primary btn-lg" href="${data.download_url}"><i class="fa-brands fa-windows"></i> Download for Windows</a>`
        : '';

    const macosBlock = data.macos_download_url
        ? `<a class="btn btn-secondary btn-lg macos-download-btn" href="${data.macos_download_url}" onclick="handleMacDownload(event)"><i class="fa-brands fa-apple"></i> Download for macOS</a>`
        : '';

    const emailLine = data.email_sent
        ? `<p>We also emailed the same links to <strong>${data.delivery_email}</strong>.</p>`
        : `<p>Email delivery is still pending${data.email_error ? `: ${data.email_error}` : '.'}</p>`;

    root.innerHTML = `
        <div class="desktop-order-ready">
            <p><strong>Product key</strong></p>
            <div class="desktop-license-key">${data.license_key}</div>
            ${emailLine}
            <div class="desktop-download-actions">
                ${windowsBlock}
                ${macosBlock}
            </div>
        </div>`;
}

async function pollDesktopOrderStatus() {
    const sessionId = document.body.dataset.desktopSessionId || '';
    if (!sessionId) {
        return;
    }

    for (let attempt = 0; attempt < 10; attempt += 1) {
        const res = await fetch(`/api/desktop/order-status?session_id=${encodeURIComponent(sessionId)}`);
        const data = await res.json();
        renderDesktopOrderStatus(data);
        if (data.ready) {
            return;
        }
        await new Promise((resolve) => window.setTimeout(resolve, 2000));
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const navbar = document.getElementById('navbar');
    if (navbar) {
        window.addEventListener('scroll', () => {
            navbar.classList.toggle('scrolled', window.scrollY > 50);
        });
    }

    const hamburger = document.getElementById('hamburger');
    const navLinks = document.getElementById('nav-links');
    if (hamburger && navLinks) {
        hamburger.addEventListener('click', () => navLinks.classList.toggle('open'));
    }

    fetch('https://ipapi.co/currency/')
        .then((res) => (res.ok ? res.text() : 'USD'))
        .then((code) => applyDesktopPricing(code.trim()))
        .catch(() => {});

    pollDesktopOrderStatus();

    // Close macOS tutorial on overlay click or Escape key
    const tutorialOverlay = document.getElementById('macos-tutorial-overlay');
    if (tutorialOverlay) {
        tutorialOverlay.addEventListener('click', (e) => {
            if (e.target === tutorialOverlay) closeMacTutorial();
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && tutorialOverlay.classList.contains('active')) {
                closeMacTutorial();
            }
        });
    }
});