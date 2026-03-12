// imagic — Landing Page JS

document.addEventListener('DOMContentLoaded', () => {
    // --- Navbar scroll effect ---
    const navbar = document.getElementById('navbar');
    window.addEventListener('scroll', () => {
        navbar.classList.toggle('scrolled', window.scrollY > 50);
    });

    // --- Mobile hamburger menu ---
    const hamburger = document.getElementById('hamburger');
    const navLinks = document.getElementById('nav-links');
    if (hamburger) {
        hamburger.addEventListener('click', () => {
            navLinks.classList.toggle('open');
        });
        // Close on link click
        navLinks.querySelectorAll('a').forEach(link => {
            link.addEventListener('click', () => navLinks.classList.remove('open'));
        });
    }

    // --- Smooth scroll for anchor links ---
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', (e) => {
            const target = document.querySelector(anchor.getAttribute('href'));
            if (target) {
                e.preventDefault();
                target.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        });
    });

    // --- Animate elements on scroll ---
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.style.opacity = '1';
                entry.target.style.transform = 'translateY(0)';
            }
        });
    }, { threshold: 0.1 });

    document.querySelectorAll('.feature-card, .step, .pricing-card, .benefit-card, .ai-feature-card, .version-card, .comparison-row').forEach(el => {
        el.style.opacity = '0';
        el.style.transform = 'translateY(24px)';
        el.style.transition = 'opacity 0.6s ease, transform 0.6s ease';
        observer.observe(el);
    });

    // --- Animate hero stats numbers ---
    const animateCounter = (el, target, suffix = '') => {
        let current = 0;
        const step = Math.max(1, Math.ceil(target / 30));
        const interval = setInterval(() => {
            current += step;
            if (current >= target) {
                current = target;
                clearInterval(interval);
            }
            el.textContent = current + suffix;
        }, 40);
    };

    const statObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting && !entry.target.dataset.animated) {
                entry.target.dataset.animated = 'true';
                const num = entry.target.querySelector('.number');
                const raw = num.textContent.trim();
                const parsed = parseInt(raw);
                if (!isNaN(parsed)) {
                    animateCounter(num, parsed);
                }
            }
        });
    }, { threshold: 0.5 });

    document.querySelectorAll('.hero-stat').forEach(el => statObserver.observe(el));

    // --- Pipeline steps: reveal on scroll ---
    const pipelineObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('visible');
            }
        });
    }, { threshold: 0.15 });

    document.querySelectorAll('.pipeline-step').forEach(step => {
        pipelineObserver.observe(step);
    });

    // --- Camera banner: slide up on scroll ---
    const cameraBanner = document.getElementById('camera-banner');
    if (cameraBanner) {
        const cameraObserver = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    entry.target.classList.add('visible');
                    cameraObserver.unobserve(entry.target);
                }
            });
        }, { threshold: 0.1 });
        cameraObserver.observe(cameraBanner);
    }

    // --- GEO-based currency pricing ---
    const FLAT_CURRENCIES = {
        USD: { symbol: '$', p5: 5, p10: 10 },
        GBP: { symbol: '£', p5: 5, p10: 10 },
        EUR: { symbol: '€', p5: 5, p10: 10 },
        AUD: { symbol: 'A$', p5: 5, p10: 10 },
        CAD: { symbol: 'C$', p5: 5, p10: 10 },
    };

    // EUR exchange rates for other currencies (approximate, for display only)
    const EUR_RATES = {
        JPY: { symbol: '¥', rate: 162 },
        INR: { symbol: '₹', rate: 90 },
        BRL: { symbol: 'R$', rate: 5.5 },
        MXN: { symbol: 'MX$', rate: 18.5 },
        PLN: { symbol: 'zł', rate: 4.3 },
        SEK: { symbol: 'kr', rate: 11.5 },
        NOK: { symbol: 'kr', rate: 11.8 },
        DKK: { symbol: 'kr', rate: 7.5 },
        CHF: { symbol: 'CHF', rate: 0.95 },
        CZK: { symbol: 'Kč', rate: 25 },
        HUF: { symbol: 'Ft', rate: 395 },
        TRY: { symbol: '₺', rate: 35 },
        ZAR: { symbol: 'R', rate: 19.5 },
        NZD: { symbol: 'NZ$', rate: 1.8 },
        SGD: { symbol: 'S$', rate: 1.45 },
        HKD: { symbol: 'HK$', rate: 8.5 },
        KRW: { symbol: '₩', rate: 1450 },
        THB: { symbol: '฿', rate: 37 },
        PHP: { symbol: '₱', rate: 61 },
        TWD: { symbol: 'NT$', rate: 34 },
        ILS: { symbol: '₪', rate: 3.9 },
        ARS: { symbol: 'ARS$', rate: 950 },
        CLP: { symbol: 'CL$', rate: 1020 },
        COP: { symbol: 'COL$', rate: 4500 },
        RUB: { symbol: '₽', rate: 100 },
        UAH: { symbol: '₴', rate: 43 },
        RON: { symbol: 'lei', rate: 5 },
        BGN: { symbol: 'лв', rate: 2 },
        HRK: { symbol: 'kn', rate: 7.5 },
        IDR: { symbol: 'Rp', rate: 17000 },
        MYR: { symbol: 'RM', rate: 4.8 },
        VND: { symbol: '₫', rate: 27000 },
        PKR: { symbol: 'Rs', rate: 300 },
        EGP: { symbol: 'E£', rate: 52 },
        NGN: { symbol: '₦', rate: 1600 },
        KES: { symbol: 'KSh', rate: 140 },
    };

    function roundPrice(amount) {
        if (amount < 10) return Math.round(amount);
        if (amount < 100) return Math.round(amount / 5) * 5;
        if (amount < 1000) return Math.round(amount / 10) * 10;
        if (amount < 10000) return Math.round(amount / 100) * 100;
        return Math.round(amount / 1000) * 1000;
    }

    function applyPricing(currencyCode) {
        let symbol, price5, price10;

        if (FLAT_CURRENCIES[currencyCode]) {
            const c = FLAT_CURRENCIES[currencyCode];
            symbol = c.symbol;
            price5 = c.p5;
            price10 = c.p10;
        } else if (EUR_RATES[currencyCode]) {
            const c = EUR_RATES[currencyCode];
            symbol = c.symbol;
            price5 = roundPrice(5 * c.rate);
            price10 = roundPrice(10 * c.rate);
        } else {
            return; // Unknown currency, keep USD defaults
        }

        // Update $10 price elements
        document.querySelectorAll('[data-price-10]').forEach(el => {
            el.textContent = symbol + price10;
        });
        document.querySelectorAll('[data-price-10-inline]').forEach(el => {
            el.textContent = symbol + price10;
        });

        // Update $5 / 500 images price elements
        document.querySelectorAll('[data-price-5]').forEach(el => {
            el.innerHTML = symbol + price5 + '<span>/500 images</span>';
        });

        // Update checkout button text
        document.querySelectorAll('[data-checkout-btn]').forEach(el => {
            el.textContent = 'Buy 500 Images — ' + symbol + price5;
        });

        // Update all generic currency symbols
        document.querySelectorAll('.currency-symbol').forEach(el => {
            el.textContent = symbol;
        });
    }

    // Detect visitor currency via free GEO API
    fetch('https://ipapi.co/currency/')
        .then(res => res.ok ? res.text() : 'USD')
        .then(code => applyPricing(code.trim()))
        .catch(() => {}); // Keep USD defaults on failure
});
