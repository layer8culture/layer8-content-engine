(() => {
  const scriptUrl = new URL(document.currentScript.src);
  const sectionUrl = new URL("section.html", scriptUrl);
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  const closeMobileNav = () => {
    const nativeDialog = document.querySelector('dialog[aria-label="Main navigation"]');
    if (nativeDialog?.open) {
      nativeDialog.close();
      return;
    }
    const menuButton = document.querySelector('body > nav button[aria-expanded="true"]');
    if (menuButton) menuButton.click();
  };

  const addNavLink = container => {
    if (!container || container.querySelector('[data-projects-nav]')) return;
    const guidesLink = Array.from(container.querySelectorAll("a")).find(link =>
      link.getAttribute("href") === "/guides/"
    );
    if (!guidesLink) return;

    const link = document.createElement("a");
    link.href = "/#projects";
    link.className = guidesLink.className;
    link.dataset.projectsNav = "true";
    link.textContent = "Projects";
    link.addEventListener("click", closeMobileNav);
    const item = guidesLink.parentElement.cloneNode(false);
    item.append(link);
    guidesLink.parentElement.before(item);
  };

  const installNav = () => {
    const installMobileLink = () => {
      const dialog = document.querySelector('dialog[aria-label="Main navigation"], [role="dialog"]');
      if (!dialog) return;
      const guidesLink = dialog.querySelector('a[href="/guides/"]');
      if (!guidesLink || dialog.querySelector("[data-projects-nav]")) return;
      const link = document.createElement("a");
      link.href = "/#projects";
      link.className = guidesLink.className;
      link.dataset.projectsNav = "true";
      link.textContent = "Projects";
      link.addEventListener("click", closeMobileNav);
      guidesLink.before(link);
    };

    const installAllLinks = () => {
      addNavLink(document.querySelector("body > nav > div > ul"));
      installMobileLink();
    };

    installAllLinks();
    const observer = new MutationObserver(installAllLinks);
    observer.observe(document.body, { childList: true, subtree: true });
  };

  const enableProjectMotion = section => {
    const motionButton = section.querySelector("[data-motion-toggle]");
    const motionLabel = section.querySelector("[data-motion-label]");
    const radioImage = section.querySelector("[data-radio-image]");
    const radioCard = section.querySelector("[data-radio-card]");
    const sonarCard = section.querySelector("[data-sonar-card]");
    let motionOn = !reducedMotion.matches;
    let radioVisible = false;
    let sonarVisible = false;

    const applyMotion = () => {
      section.dataset.motion = motionOn ? "on" : "paused";
      section.dataset.radarVisible = sonarVisible ? "true" : "false";
      radioImage.src = motionOn && radioVisible
        ? radioImage.dataset.motionSrc
        : radioImage.dataset.posterSrc;
      motionButton.setAttribute("aria-pressed", String(!motionOn));
      motionButton.disabled = reducedMotion.matches;
      motionLabel.textContent = reducedMotion.matches
        ? "Motion reduced"
        : motionOn ? "Pause motion" : "Enable motion";
      motionButton.querySelector("[aria-hidden]").textContent = motionOn ? "Ⅱ" : "▶";
    };

    motionButton.addEventListener("click", () => {
      if (reducedMotion.matches) return;
      motionOn = !motionOn;
      applyMotion();
    });

    reducedMotion.addEventListener("change", event => {
      if (event.matches) motionOn = false;
      applyMotion();
    });

    if ("IntersectionObserver" in window) {
      const observer = new IntersectionObserver(entries => {
        for (const entry of entries) {
          if (entry.target === radioCard) radioVisible = entry.isIntersecting;
          if (entry.target === sonarCard) sonarVisible = entry.isIntersecting;
        }
        applyMotion();
      }, { rootMargin: "80px" });
      observer.observe(radioCard);
      observer.observe(sonarCard);
    }

    applyMotion();
  };

  const loadProjects = async () => {
    const response = await fetch(sectionUrl, { credentials: "same-origin" });
    if (!response.ok) {
      throw new Error(`Projects section request failed: ${response.status} ${response.statusText}`);
    }
    const markup = await response.text();
    const template = document.createElement("template");
    template.innerHTML = markup.trim();
    const section = template.content.querySelector("#projects");
    const buildSection = document.querySelector("#build");
    const streamSection = document.querySelector("#stream");
    if (!section || !buildSection || !streamSection) {
      throw new Error("Projects integration could not find its section or homepage insertion points.");
    }
    streamSection.before(section);
    enableProjectMotion(section);
    if (window.location.hash === "#projects") {
      window.requestAnimationFrame(() => section.scrollIntoView({ block: "start" }));
    }
  };

  const installAfterHydration = () => {
    window.setTimeout(() => {
      installNav();
      if (window.location.pathname !== "/") return;
      loadProjects().catch(error => console.error("Unable to load the approved Projects section.", error));
    }, 900);
  };

  if (document.readyState === "complete") {
    installAfterHydration();
  } else {
    window.addEventListener("load", installAfterHydration, { once: true });
  }
})();
