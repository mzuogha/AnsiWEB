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
