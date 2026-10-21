// U27: in the iOS home-screen app (no browser bar to go back with), hosted pages open in
// FileServe's viewer (/hosted/view): the page in a frame under a bar with Back and Open in Safari.
// Everywhere else the links open the page directly, as before.
(function () {
  var standalone = window.navigator.standalone === true ||
    (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches);
  if (!standalone) return;
  var prefix = (document.documentElement.getAttribute("data-stonepi-prefix") || "").replace(/\/$/, "");
  document.addEventListener("click", function (e) {
    var link = e.target.closest && e.target.closest("a[data-hosted-path]");
    if (!link || e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    window.location.href = prefix + "/hosted/view?p=" + encodeURIComponent(link.getAttribute("data-hosted-path"));
  });
})();
