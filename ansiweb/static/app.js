// Session timeout: after the configured idle period, take the browser to the
// sign-in page instead of leaving a stale page open. The server enforces the
// timeout itself; this only keeps the window in step with it.
(function () {
  var meta = document.querySelector('meta[name="idle-timeout"]');
  if (!meta) return;
  var seconds = parseInt(meta.getAttribute('content'), 10);
  if (!seconds || seconds < 30) return;
  var timer;
  function expire() {
    window.location.href = '/login?expired=1';
  }
  function reset() {
    window.clearTimeout(timer);
    // A second's grace, so the server has already expired the session
    timer = window.setTimeout(expire, (seconds + 1) * 1000);
  }
  ['click', 'keydown', 'mousemove', 'scroll', 'touchstart'].forEach(function (evt) {
    window.addEventListener(evt, reset, { passive: true });
  });
  reset();
})();

// "Tick all" / "Untick all" on the Deploy page
document.addEventListener('click', function (e) {
  var all = e.target.closest('[data-check-all]');
  var none = e.target.closest('[data-check-none]');
  var name = all ? all.getAttribute('data-check-all') : (none ? none.getAttribute('data-check-none') : null);
  if (!name) return;
  e.preventDefault();
  document.querySelectorAll('input[type=checkbox][name="' + name + '"]').forEach(function (box) {
    box.checked = Boolean(all);
  });
});

// --- Odds and ends -------------------------------------------------------
(function () {
  // The Konami code puts the deployment into, let us say, a higher gear.
  var code = [38, 38, 40, 40, 37, 39, 37, 39, 66, 65];
  var at = 0;
  document.addEventListener('keydown', function (e) {
    at = (e.keyCode === code[at]) ? at + 1 : (e.keyCode === code[0] ? 1 : 0);
    if (at !== code.length) return;
    at = 0;
    document.body.classList.toggle('konami');
    var note = document.createElement('div');
    note.className = 'flash ok eggnote';
    note.textContent = 'Deployment mode: maximum effort. (Press it again to calm down.)';
    var main = document.querySelector('main') || document.body;
    main.insertBefore(note, main.firstChild);
    window.setTimeout(function () { note.remove(); }, 6000);
  });

  // Five clicks on the version number
  var ver = document.querySelector('.ver a');
  if (!ver) return;
  var clicks = 0, last = 0;
  ver.addEventListener('click', function (e) {
    var now = Date.now();
    clicks = (now - last < 800) ? clicks + 1 : 1;
    last = now;
    if (clicks < 5) return;
    e.preventDefault();
    clicks = 0;
    var tag = document.createElement('div');
    tag.className = 'ver egg';
    tag.innerHTML = 'Built one careful step at a time.<br>No PCs were harmed in the making of this software.' +
                    '<br><a href="/coffee">Take a break</a>';
    ver.parentNode.appendChild(tag);
    window.setTimeout(function () { tag.remove(); }, 9000);
  });
})();

// Tooltips: one box on <body>, so nothing can clip it.
(function () {
  var box = null;

  function show(el) {
    var text = el.getAttribute('data-tip');
    if (!text) return;
    if (!box) {
      box = document.createElement('div');
      box.id = 'tipbox';
      box.setAttribute('role', 'tooltip');
      document.body.appendChild(box);
    }
    box.textContent = text;
    box.classList.add('on');

    var at = el.getBoundingClientRect();
    var size = box.getBoundingClientRect();
    var margin = 8;
    // Above the element by default, below it when there is no room up there
    var top = at.top - size.height - margin;
    if (top < margin) top = at.bottom + margin;
    var left = at.left + (at.width / 2) - (size.width / 2);
    left = Math.max(margin, Math.min(left, window.innerWidth - size.width - margin));
    box.style.top = Math.round(top) + 'px';
    box.style.left = Math.round(left) + 'px';
  }

  function hide() {
    if (box) box.classList.remove('on');
  }

  document.addEventListener('mouseover', function (e) {
    var el = e.target.closest('[data-tip]');
    if (el) show(el); else hide();
  });
  document.addEventListener('focusin', function (e) {
    var el = e.target.closest('[data-tip]');
    if (el) show(el);
  });
  document.addEventListener('focusout', hide);
  document.addEventListener('scroll', hide, true);
  window.addEventListener('resize', hide);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') hide(); });
  // A tap on a touch screen has no hover, so treat it as show-then-hide
  document.addEventListener('click', function (e) {
    var el = e.target.closest('[data-tip]');
    if (el) { show(el); window.setTimeout(hide, 4000); } else { hide(); }
  });
})();

// Light and dark
(function () {
  var root = document.documentElement;

  function systemDark() {
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  }
  function current() {
    return root.getAttribute('data-theme') || (systemDark() ? 'dark' : 'light');
  }
  function label() {
    document.querySelectorAll('.themelabel').forEach(function (el) {
      el.textContent = current() === 'dark' ? 'Light mode' : 'Dark mode';
    });
  }
  document.addEventListener('click', function (e) {
    if (!e.target.closest('[data-theme-toggle]')) return;
    var next = current() === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    try { localStorage.setItem('ansiweb-theme', next); } catch (err) { /* nothing to do */ }
    label();
  });
  label();
})();

// Confirmation prompts for forms with data-confirm
document.addEventListener('submit', function (e) {
  var msg = e.target.getAttribute('data-confirm');
  if (msg && !window.confirm(msg)) { e.preventDefault(); }
});

// App form: show only the fields for the selected source
(function () {
  var src = document.getElementById('source');
  if (!src) return;
  function update() {
    document.querySelectorAll('.src').forEach(function (el) {
      el.style.display = el.classList.contains('src-' + src.value) ? '' : 'none';
    });
  }
  src.addEventListener('change', update);
  update();
})();

// Settings: show the KMS fields only when KMS activation is selected
(function () {
  var mode = document.getElementById('actmode');
  if (!mode) return;
  function update() {
    document.querySelectorAll('.kms').forEach(function (el) {
      el.style.display = mode.value === 'kms' ? '' : 'none';
    });
  }
  mode.addEventListener('change', update);
  update();
})();

// Live job log
(function () {
  var pre = document.getElementById('log');
  if (!pre) return;
  var url = pre.getAttribute('data-url'), offset = 0, done = false;
  function poll() {
    fetch(url + '?offset=' + offset, { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 30;
        if (d.text) { pre.appendChild(document.createTextNode(d.text)); }
        offset = d.offset;
        if (atBottom) { pre.scrollTop = pre.scrollHeight; }
        var st = document.getElementById('jobstatus');
        if (st) { st.innerHTML = '<span class="pill ' + d.status + '">' + d.status + '</span>'; }
        done = d.status !== 'running';
        if (!done || d.text) { setTimeout(poll, done ? 500 : 1500); }
      })
      .catch(function () { setTimeout(poll, 4000); });
  }
  poll();
})();
