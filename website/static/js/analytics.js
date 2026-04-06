/**
 * imagic – comprehensive behavioural analytics
 *
 * Tracks:  button/CTA clicks, link clicks, scroll depth, time-on-page,
 *          page visibility, exit intent, FAQ toggles, section visibility,
 *          and funnels the events to both GA4 (gtag) and the server-side
 *          endpoint POST /api/e  for our own SQLite analytics DB.
 */
(function () {
  "use strict";

  /* ------------------------------------------------------------------ */
  /* helpers                                                             */
  /* ------------------------------------------------------------------ */
  var PAGE = location.pathname;
  var SESSION_START = Date.now();
  var MAX_SCROLL = 0;
  var SENT_SCROLL = {};          // milestones already reported {25:true …}
  var VISIBLE_SECTIONS = {};     // section id → first-seen timestamp

  /** Send to GA4 if available */
  function ga(name, params) {
    if (typeof gtag === "function") {
      gtag("event", name, params || {});
    }
  }

  /** Fire-and-forget beacon to server */
  function beacon(name, data) {
    var payload = Object.assign({ event: name, page: PAGE, ts: new Date().toISOString() }, data || {});
    var json = JSON.stringify(payload);
    if (navigator.sendBeacon) {
      navigator.sendBeacon("/api/e", new Blob([json], { type: "application/json" }));
    } else {
      var x = new XMLHttpRequest();
      x.open("POST", "/api/e", true);
      x.setRequestHeader("Content-Type", "application/json");
      x.send(json);
    }
  }

  /** Send to both GA4 and server */
  function track(name, data) {
    ga(name, data);
    beacon(name, data);
  }

  /** Readable label for an element */
  function label(el) {
    // data-track-label overrides everything
    var dl = el.getAttribute("data-track-label");
    if (dl) return dl;
    var txt = (el.innerText || el.textContent || "").trim().substring(0, 80);
    // strip emoji for cleaner labels
    txt = txt.replace(/[\u{1F000}-\u{1FFFF}]|[\u2600-\u27BF]/gu, "").trim();
    return txt || el.getAttribute("aria-label") || el.id || "";
  }

  /** Closest section/landmark id */
  function sectionOf(el) {
    var s = el.closest("section[id], [id].hero, [id].desktop-hero, [id].blog-hero, [id].community-hero, [id].contact-hero, [id].about-hero, [id].changelog-hero, [id].cta-section, footer");
    if (s) return s.id || s.className.split(/\s+/)[0];
    // fallback: nearest element with an id
    var p = el.closest("[id]");
    return p ? p.id : "";
  }

  /* ------------------------------------------------------------------ */
  /* 1. Button & CTA clicks                                             */
  /* ------------------------------------------------------------------ */
  document.addEventListener("click", function (e) {
    var el = e.target.closest("button, a.btn, [class*='btn-'], input[type='submit'], .filter-btn, .ed-tool-btn, .ed-ai-btn, .ed-section-header, .wf-step, .ch-link");
    if (!el) return;

    var data = {
      label: label(el),
      tag: el.tagName.toLowerCase(),
      section: sectionOf(el),
      classes: (el.className || "").substring(0, 120),
      href: el.getAttribute("href") || ""
    };
    track("click", data);
  }, true);   // capture phase so we catch even prevented clicks

  /* ------------------------------------------------------------------ */
  /* 2. Outbound link clicks                                            */
  /* ------------------------------------------------------------------ */
  document.addEventListener("click", function (e) {
    var a = e.target.closest("a[href]");
    if (!a) return;
    var href = a.href;
    if (!href) return;
    try {
      var url = new URL(href, location.origin);
      if (url.hostname !== location.hostname) {
        track("outbound_click", { url: href, label: label(a) });
      }
    } catch (_) { /* malformed href – ignore */ }
  }, true);

  /* ------------------------------------------------------------------ */
  /* 3. Scroll depth (milestones: 25 / 50 / 75 / 90 / 100 %)           */
  /* ------------------------------------------------------------------ */
  function scrollPct() {
    var doc = document.documentElement;
    var body = document.body;
    var scrollTop = window.pageYOffset || doc.scrollTop || body.scrollTop || 0;
    var scrollHeight = Math.max(doc.scrollHeight, body.scrollHeight) - Math.max(doc.clientHeight, body.clientHeight);
    return scrollHeight > 0 ? Math.round((scrollTop / scrollHeight) * 100) : 0;
  }

  var scrollTimer = null;
  window.addEventListener("scroll", function () {
    if (scrollTimer) return;
    scrollTimer = setTimeout(function () {
      scrollTimer = null;
      var pct = scrollPct();
      if (pct > MAX_SCROLL) MAX_SCROLL = pct;
      var milestones = [25, 50, 75, 90, 100];
      for (var i = 0; i < milestones.length; i++) {
        var m = milestones[i];
        if (pct >= m && !SENT_SCROLL[m]) {
          SENT_SCROLL[m] = true;
          track("scroll_depth", { percent: m });
        }
      }
    }, 250);
  }, { passive: true });

  /* ------------------------------------------------------------------ */
  /* 4. Time on page (sent on unload + every 30 s while visible)        */
  /* ------------------------------------------------------------------ */
  var engagedMs = 0;      // active time
  var lastActive = Date.now();
  var isActive = true;

  function markActive() { if (!isActive) { isActive = true; lastActive = Date.now(); } }
  function markIdle() {
    if (isActive) { engagedMs += Date.now() - lastActive; isActive = false; }
  }

  document.addEventListener("visibilitychange", function () {
    document.hidden ? markIdle() : markActive();
  });
  window.addEventListener("blur", markIdle);
  window.addEventListener("focus", markActive);

  // heartbeat every 30 s
  setInterval(function () {
    if (isActive) engagedMs += Date.now() - lastActive;
    lastActive = Date.now();
    beacon("heartbeat", { engaged_s: Math.round(engagedMs / 1000), max_scroll: MAX_SCROLL });
  }, 30000);

  /* ------------------------------------------------------------------ */
  /* 5. Page exit (beforeunload)                                        */
  /* ------------------------------------------------------------------ */
  window.addEventListener("beforeunload", function () {
    if (isActive) engagedMs += Date.now() - lastActive;
    var data = {
      engaged_s: Math.round(engagedMs / 1000),
      total_s: Math.round((Date.now() - SESSION_START) / 1000),
      max_scroll: MAX_SCROLL
    };
    beacon("page_exit", data);
    ga("page_exit", data);
  });

  /* ------------------------------------------------------------------ */
  /* 6. FAQ / details toggles                                           */
  /* ------------------------------------------------------------------ */
  document.addEventListener("toggle", function (e) {
    if (e.target.tagName === "DETAILS") {
      var summary = e.target.querySelector("summary");
      track("faq_toggle", {
        open: e.target.open,
        label: summary ? summary.textContent.trim().substring(0, 100) : ""
      });
    }
  }, true);

  /* ------------------------------------------------------------------ */
  /* 7. Section visibility (IntersectionObserver)                        */
  /* ------------------------------------------------------------------ */
  if ("IntersectionObserver" in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          var id = entry.target.id || entry.target.className.split(/\s+/)[0];
          if (id && !VISIBLE_SECTIONS[id]) {
            VISIBLE_SECTIONS[id] = Date.now();
            track("section_view", { section: id });
          }
        }
      });
    }, { threshold: 0.3 });

    // Observe all sections with ids and landmark classes
    document.querySelectorAll(
      "section[id], [id].hero, .hero, .desktop-hero, .blog-hero, .community-hero, " +
      ".contact-hero, .about-hero, .changelog-hero, .cta-section, .pipeline-section, " +
      ".ai-section, .camera-section, .footer, #pricing, #faq, #versions, #features, " +
      "#why-switch, #showcase, #desktop-checkout, #desktop-download, #desktop-benefits, " +
      "#desktop-faq, #blog-grid, #blog-filters, .blog-cta, .post-content, .related-posts, " +
      ".changelog-timeline, .channels-grid, .email-section, .pillars-grid, .includes-grid"
    ).forEach(function (el) { io.observe(el); });
  }

  /* ------------------------------------------------------------------ */
  /* 8. Form interactions (focus on first field, submit)                 */
  /* ------------------------------------------------------------------ */
  document.addEventListener("submit", function (e) {
    var form = e.target;
    track("form_submit", { form_id: form.id || "", action: form.action || "" });
  }, true);

  // Track first interaction with key inputs (email, search, etc.)
  var trackedInputs = {};
  document.addEventListener("focus", function (e) {
    var el = e.target;
    if ((el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT") && el.id && !trackedInputs[el.id]) {
      trackedInputs[el.id] = true;
      track("field_focus", { field_id: el.id, field_type: el.type || el.tagName.toLowerCase() });
    }
  }, true);

  /* ------------------------------------------------------------------ */
  /* 9. Copy events (useful for detecting license key / email copying)   */
  /* ------------------------------------------------------------------ */
  document.addEventListener("copy", function () {
    track("copy", { section: sectionOf(document.activeElement || document.body) });
  });

  /* ------------------------------------------------------------------ */
  /* 10. File uploads (web app)                                          */
  /* ------------------------------------------------------------------ */
  var fileInput = document.getElementById("file-input");
  if (fileInput) {
    fileInput.addEventListener("change", function () {
      track("file_upload", { count: fileInput.files ? fileInput.files.length : 0 });
    });
  }

  /* ------------------------------------------------------------------ */
  /* Done – log an initial page_enter event                              */
  /* ------------------------------------------------------------------ */
  track("page_enter", {
    referrer: document.referrer || "",
    screen_w: screen.width,
    screen_h: screen.height,
    viewport_w: window.innerWidth,
    viewport_h: window.innerHeight
  });

})();
