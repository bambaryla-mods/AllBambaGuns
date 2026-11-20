// Vanilla / Combat Extended switch, remembered across visits.
(function () {
  var root = document.documentElement;

  function setMode(mode) {
    if (mode !== "vanilla" && mode !== "ce") return;
    root.setAttribute("data-mode", mode);
    try { localStorage.setItem("abg-mode", mode); } catch (e) { /* private mode: the switch still works for this visit */ }
    var buttons = document.querySelectorAll("[data-set-mode]");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].setAttribute("aria-pressed", String(buttons[i].getAttribute("data-set-mode") === mode));
    }
  }

  var switches = document.querySelectorAll("[data-set-mode]");
  for (var i = 0; i < switches.length; i++) {
    switches[i].addEventListener("click", function () { setMode(this.getAttribute("data-set-mode")); });
  }

  // The inline script in <head> already chose the mode (from ?mode= or the last choice).
  setMode(root.getAttribute("data-mode") === "ce" ? "ce" : "vanilla");
})();
