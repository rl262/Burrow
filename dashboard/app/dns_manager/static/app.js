/* dns_manager — light/dark theme toggle.
   The no-FOUC setter lives inline in <head>; this only wires the button.
   Persisted to localStorage as 'dnsm-theme'. Default (unset) = dark. */
(function () {
  function current() {
    return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  }
  function apply(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    try { localStorage.setItem('dnsm-theme', theme); } catch (e) {}
  }
  document.addEventListener('click', function (e) {
    var btn = e.target.closest && e.target.closest('#themeToggle, .theme-toggle');
    if (!btn) return;
    e.preventDefault();
    apply(current() === 'dark' ? 'light' : 'dark');
  });
})();
