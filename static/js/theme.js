/* Theme switcher: auto (follows the OS) → light → dark. Stored per browser. */
(function () {
  var KEY = "vozvrat-theme";
  var ORDER = ["auto", "light", "dark"];
  var LABEL = { auto: "Авто", light: "Светлая", dark: "Тёмная" };

  function read() {
    try {
      var value = localStorage.getItem(KEY);
      return ORDER.indexOf(value) >= 0 ? value : "auto";
    } catch (e) {
      return "auto";
    }
  }

  function apply(mode) {
    var root = document.documentElement;
    if (mode === "auto") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", mode);
    root.setAttribute("data-theme-mode", mode);
    document.querySelectorAll("[data-theme-toggle] .theme-label").forEach(function (el) {
      el.textContent = LABEL[mode];
    });
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-theme-toggle]");
    if (!button) return;
    var next = ORDER[(ORDER.indexOf(read()) + 1) % ORDER.length];
    try {
      localStorage.setItem(KEY, next);
    } catch (e) {}
    apply(next);
  });

  apply(read());
})();
