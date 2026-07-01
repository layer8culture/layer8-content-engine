(function () {
  var clarityProjectId = "xfqgiykq4y";
  var connectionString = "InstrumentationKey=fd7f9a68-fd29-4ad4-a440-ab802ba036b3;IngestionEndpoint=https://eastus2-3.in.applicationinsights.azure.com/;LiveEndpoint=https://eastus2.livediagnostics.monitor.azure.com/;ApplicationId=fc6e462b-4c6b-4299-a072-1673ccda94e5";

  (function (c, l, a, r, i, t, y) {
    c[a] = c[a] || function () {
      (c[a].q = c[a].q || []).push(arguments);
    };
    t = l.createElement(r);
    t.async = 1;
    t.src = "https://www.clarity.ms/tag/" + i;
    y = l.getElementsByTagName(r)[0];
    y.parentNode.insertBefore(t, y);
  })(window, document, "clarity", "script", clarityProjectId);

  function init() {
    if (!window.Microsoft || !window.Microsoft.ApplicationInsights) {
      return;
    }

    var appInsights = new window.Microsoft.ApplicationInsights.ApplicationInsights({
      config: {
        connectionString: connectionString,
        enableAutoRouteTracking: true,
        disableAjaxTracking: true,
        disableFetchTracking: true
      }
    });

    appInsights.loadAppInsights();
    appInsights.trackPageView({
      name: document.title,
      uri: window.location.href
    });

    window.aigraphTrack = function (name, properties) {
      appInsights.trackEvent({
        name: name,
        properties: properties || {}
      });
    };

    document.addEventListener("click", function (event) {
      var target = event.target;
      while (target && target !== document) {
        if (target.getAttribute && target.getAttribute("data-aigraph-event")) {
          var href = target.getAttribute("href") || "";
          var shouldDelayNavigation = href && target.tagName === "A" && !target.getAttribute("target") && href.indexOf("mailto:") !== 0;
          window.aigraphTrack(target.getAttribute("data-aigraph-event"), {
            href: href,
            path: window.location.pathname,
            text: (target.textContent || "").trim().slice(0, 120)
          });
          if (window.clarity) {
            window.clarity("event", target.getAttribute("data-aigraph-event"));
          }
          if (shouldDelayNavigation) {
            event.preventDefault();
            try {
              appInsights.flush();
            } catch (error) {
              // Navigation should not fail if telemetry flushing is unavailable.
            }
            window.setTimeout(function () {
              window.location.href = href;
            }, 250);
          }
          break;
        }
        target = target.parentNode;
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
