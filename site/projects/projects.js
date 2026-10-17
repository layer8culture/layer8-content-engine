(() => {
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  const closeMobileNav = () => {
    const nativeDialog = document.querySelector('dialog[aria-label="Main navigation"]');
    if (nativeDialog?.open) {
      nativeDialog.close();
      document.querySelector('body > nav button[aria-expanded="true"]')
        ?.setAttribute("aria-expanded", "false");
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
    link.href = "/projects/";
    link.className = guidesLink.className;
    link.dataset.projectsNav = "true";
    link.textContent = "Projects";
    if (/^\/projects\/?$/.test(window.location.pathname)) {
      link.classList.remove("text-ink-dim");
      link.classList.add("text-ink");
      link.setAttribute("aria-current", "page");
    }
    link.addEventListener("click", closeMobileNav);
    const item = guidesLink.parentElement.cloneNode(false);
    item.append(link);
    guidesLink.parentElement.before(item);
  };

  const dedupeNavLink = container => {
    const links = container?.querySelectorAll("[data-projects-nav]");
    if (!links || links.length < 2) return;
    links.forEach((link, index) => {
      if (index > 0) link.closest("li")?.remove();
    });
  };

  const installNav = () => {
    const installMobileLink = () => {
      const dialog = document.querySelector('dialog[aria-label="Main navigation"], [role="dialog"]');
      if (!dialog) return;
      const guidesLink = dialog.querySelector('a[href="/guides/"]');
      if (!guidesLink || dialog.querySelector("[data-projects-nav]")) return;
      const link = document.createElement("a");
      link.href = "/projects/";
      link.className = guidesLink.className;
      link.dataset.projectsNav = "true";
      link.textContent = "Projects";
      if (/^\/projects\/?$/.test(window.location.pathname)) {
        link.classList.remove("text-ink-dim");
        link.classList.add("text-ink");
        link.setAttribute("aria-current", "page");
      }
      link.addEventListener("click", closeMobileNav);
      guidesLink.before(link);
    };

    const installStaticMobileNav = () => {
      if (document.querySelector('script[src*="/_next/static/chunks/"]')) return;
      if (!/^\/projects\/?$/.test(window.location.pathname)) return;
      const button = document.querySelector('body > nav button[aria-expanded]');
      const desktopNav = document.querySelector("body > nav > div > ul");
      if (!button || !desktopNav ||
          document.querySelector('dialog[aria-label="Main navigation"], [role="dialog"]')) return;

      const dialog = document.createElement("dialog");
      dialog.id = "projects-mobile-menu";
      dialog.setAttribute("aria-label", "Main navigation");
      dialog.setAttribute("role", "dialog");
      dialog.className = "l8-projects__mobile-menu";
      const links = desktopNav.cloneNode(true);
      links.className = "l8-projects__mobile-links";
      links.querySelectorAll("li").forEach(item => item.className = "l8-projects__mobile-item");
      links.querySelectorAll("a").forEach(link => {
        link.classList.remove("relative", "inline-block", "text-ink-dim");
        link.classList.add("l8-projects__mobile-link");
      });
      dialog.append(links);
      document.body.append(dialog);
      button.setAttribute("aria-controls", dialog.id);
      button.addEventListener("click", () => {
        if (dialog.open) {
          dialog.close();
          button.setAttribute("aria-expanded", "false");
        } else {
          dialog.showModal();
          button.setAttribute("aria-expanded", "true");
          dialog.querySelector("a")?.focus();
        }
      });
      dialog.addEventListener("close", () => button.setAttribute("aria-expanded", "false"));
      dialog.addEventListener("click", event => {
        if (event.target === dialog) dialog.close();
      });
      links.addEventListener("click", event => {
        if (event.target.closest("a")) dialog.close();
      });
    };

    const installAllLinks = () => {
      addNavLink(document.querySelector("body > nav > div > ul"));
      installMobileLink();
      installStaticMobileNav();
      dedupeNavLink(document.querySelector("body > nav > div > ul"));
      dedupeNavLink(document.querySelector('dialog[aria-label="Main navigation"], [role="dialog"]'));
    };

    installAllLinks();
    const observer = new MutationObserver(installAllLinks);
    observer.observe(document.body, { childList: true, subtree: true });
  };

  const installStaticHeaderBackground = () => {
    if (document.querySelector('script[src*="/_next/static/chunks/"]')) return;
    const nav = document.querySelector("body > nav");
    if (!nav) return;
    const updateBackground = () => {
      const scrolled = window.scrollY > 24;
      nav.classList.toggle("bg-transparent", !scrolled);
      nav.classList.toggle("bg-ink-bg/70", scrolled);
      nav.classList.toggle("backdrop-blur-2xl", scrolled);
    };
    window.addEventListener("scroll", updateBackground, { passive: true });
    updateBackground();
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

  const initialize = () => {
    if (window.location.pathname === "/" && window.location.hash === "#projects") {
      window.location.replace("/projects/");
      return;
    }
    installNav();
    installStaticHeaderBackground();
    const section = document.querySelector("#projects");
    if (section) enableProjectMotion(section);
  };

  if (document.querySelector('script[src*="/_next/static/chunks/"]')) {
    const installAfterHydration = () => window.setTimeout(initialize, 900);
    if (document.readyState === "complete") {
      installAfterHydration();
    } else {
      window.addEventListener("load", installAfterHydration, { once: true });
    }
  } else {
    initialize();
  }
})();
