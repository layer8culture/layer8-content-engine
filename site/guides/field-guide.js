(() => {
  "use strict";
  const guide = document.querySelector("[data-visual-guide]");
  if (!guide) return;
  const cards = [...guide.querySelectorAll(".fg-card")];
  const panels = [...guide.querySelectorAll(".fg-panel")];
  const position = guide.querySelector(".fg-position");
  const previous = guide.querySelector("[data-previous]");
  const next = guide.querySelector("[data-next]");
  const motion = guide.querySelector(".fg-motion");
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
  let active = 0;
  let paused = reduced.matches;
  function updateMotion() {
    guide.dataset.motion = !paused && !reduced.matches ? "on" : "off";
    motion.textContent = reduced.matches ? "Reduced motion enabled" : paused ? "Enable motion" : "Pause motion";
    motion.setAttribute("aria-pressed", String(paused || reduced.matches));
    motion.disabled = reduced.matches;
  }
  function select(index, focus = false) {
    active = index;
    panels.forEach((panel, i) => { panel.hidden = i !== index; });
    cards.forEach((card, i) => {
      if (i === index) card.setAttribute("aria-current", "true");
      else card.removeAttribute("aria-current");
    });
    position.textContent = `${index + 1} / ${panels.length}`;
    previous.disabled = index === 0;
    next.disabled = index === panels.length - 1;
    if (focus) panels[index].querySelector("h2").focus({ preventScroll: true });
  }
  function applyHash(focus = false) {
    let id;
    try { id = decodeURIComponent(location.hash.slice(1)); }
    catch { return; }
    const index = panels.findIndex(panel => panel.id === id);
    if (index >= 0) select(index, focus);
    const target = document.getElementById(id);
    if (target) {
      for (let node = target; node && node !== guide; node = node.parentElement) {
        if (node.tagName === "DETAILS") node.open = true;
      }
      if (id === "full-reference") guide.querySelector(".fg-reference").open = true;
      if (location.hash) target.scrollIntoView({ block: "start" });
    }
  }
  cards.forEach((card, index) => card.addEventListener("click", event => {
    event.preventDefault();
    step(index);
  }));
  function step(index) {
    select(index, true);
    history.pushState(null, "", `#${panels[index].id}`);
    panels[index].scrollIntoView({ block: "start" });
  }
  previous.addEventListener("click", () => step(active - 1));
  next.addEventListener("click", () => step(active + 1));
  motion.addEventListener("click", () => { paused = !paused; updateMotion(); });
  reduced.addEventListener("change", updateMotion);
  window.addEventListener("hashchange", () => applyHash(true));
  window.addEventListener("popstate", () => {
    if (!location.hash) select(0);
    else applyHash(true);
  });
  select(0);
  applyHash();
  updateMotion();
  motion.hidden = false;
  guide.querySelector(".fg-controls").hidden = false;
  const observer = new IntersectionObserver(entries => entries.forEach(entry => {
    entry.target.dataset.visible = String(entry.isIntersecting);
  }));
  guide.querySelectorAll(".fg-diagram").forEach(diagram => observer.observe(diagram));

  // Preserve the exported shared nav markup; native dialog supplies modal focus/Escape.
  const menuButton = document.querySelector('body > nav button[aria-expanded]');
  if (menuButton) {
    const dialog = document.createElement("dialog");
    dialog.className = "fg-menu";
    dialog.setAttribute("aria-label", "Main navigation");
    const close = document.createElement("button");
    close.type = "button";
    close.textContent = "Close menu";
    close.addEventListener("click", () => dialog.close());
    dialog.append(close);
    document.querySelectorAll("body > nav > div > ul a").forEach(source => {
      const link = source.cloneNode(true);
      link.className = "";
      link.addEventListener("click", () => dialog.close());
      dialog.append(link);
    });
    document.body.append(dialog);
    menuButton.addEventListener("click", () => {
      dialog.showModal();
      menuButton.setAttribute("aria-expanded", "true");
    });
    dialog.addEventListener("close", () => menuButton.setAttribute("aria-expanded", "false"));
  }
  const nav = document.querySelector("body > nav");
  function navBackground() {
    nav.classList.toggle("bg-transparent", window.scrollY <= 24);
    nav.classList.toggle("bg-ink-bg/70", window.scrollY > 24);
    nav.classList.toggle("backdrop-blur-2xl", window.scrollY > 24);
  }
  window.addEventListener("scroll", navBackground, { passive: true });
  navBackground();
})();
