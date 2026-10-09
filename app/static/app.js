// Shared page behaviour. Kept dependency-free per CLAUDE.md's UI rules.

function agentflowEscapeHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// Renders a small, safe subset of Markdown (headings, bold/italic, inline
// and fenced code, links, lists) to HTML. Coding agents reply in Markdown
// (bold, `[label](path)` links, bullet lists are common), so raw text
// display shows literal `**`/`[]()` syntax instead of formatting it.
//
// Everything is HTML-escaped up front; the regexes below only ever
// introduce our own fixed set of tags on top of already-escaped text, so
// agent output can never inject arbitrary HTML. Links are restricted to
// http(s) or root-relative paths to rule out `javascript:`-style URLs.
function agentflowRenderInline(escapedLine) {
  var line = escapedLine.replace(/`([^`]+)`/g, "<code>$1</code>");
  line = line.replace(
    /\[([^\]]+)\]\(((?:https?:\/\/|\/)[^\s)]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>'
  );
  line = line.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  line = line.replace(/(^|[^*])\*([^*\s][^*]*?)\*(?!\*)/g, "$1<em>$2</em>");
  return line;
}

function agentflowRenderMarkdown(text) {
  var codeBlocks = [];
  var withoutFences = text.replace(/```[^\n]*\n?([\s\S]*?)```/g, function (_match, code) {
    codeBlocks.push(code.replace(/\n$/, ""));
    return "\u0000CODEBLOCK" + (codeBlocks.length - 1) + "\u0000";
  });

  var lines = agentflowEscapeHtml(withoutFences).split("\n");
  var html = [];
  var listType = null;
  var paragraph = [];

  function flushParagraph() {
    if (paragraph.length) {
      html.push("<p>" + paragraph.join("<br>") + "</p>");
      paragraph = [];
    }
  }
  function closeList() {
    if (listType) {
      html.push("</" + listType + ">");
      listType = null;
    }
  }

  lines.forEach(function (line) {
    var codeBlockMatch = line.match(/^\u0000CODEBLOCK(\d+)\u0000$/);
    if (codeBlockMatch) {
      flushParagraph();
      closeList();
      var code = agentflowEscapeHtml(codeBlocks[Number(codeBlockMatch[1])]);
      html.push('<pre class="chat-code-block"><code>' + code + "</code></pre>");
      return;
    }

    var headingMatch = line.match(/^(#{1,6})\s+(.*)$/);
    if (headingMatch) {
      flushParagraph();
      closeList();
      var level = headingMatch[1].length;
      html.push("<h" + level + ">" + agentflowRenderInline(headingMatch[2]) + "</h" + level + ">");
      return;
    }

    var bulletMatch = line.match(/^[-*]\s+(.*)$/);
    var orderedMatch = line.match(/^\d+\.\s+(.*)$/);
    if (bulletMatch || orderedMatch) {
      flushParagraph();
      var wantType = bulletMatch ? "ul" : "ol";
      if (listType !== wantType) {
        closeList();
        html.push("<" + wantType + ">");
        listType = wantType;
      }
      html.push("<li>" + agentflowRenderInline((bulletMatch || orderedMatch)[1]) + "</li>");
      return;
    }

    closeList();

    if (line.trim() === "") {
      flushParagraph();
      return;
    }

    paragraph.push(agentflowRenderInline(line));
  });

  flushParagraph();
  closeList();

  return html.join("");
}

// Reads a fetch response as JSON. A proxy error page or a 500 is HTML, and
// `response.json()` on that throws "Unexpected token <", which is what used to be
// flashed at the user; give them the status instead.
function agentflowReadJson(response) {
  return response.text().then(function (text) {
    try {
      return text ? JSON.parse(text) : {};
    } catch (e) {
      return { error: "The server sent an unexpected reply (" + response.status + ")." };
    }
  });
}

function agentflowFlash(message, category) {
  category = category || "info";
  var list = document.querySelector(".flashes");
  if (!list) {
    var content = document.querySelector("main.content");
    if (!content) return;
    list = document.createElement("ul");
    list.className = "flashes";
    content.insertBefore(list, content.firstChild);
  }
  var item = document.createElement("li");
  item.className = "flash flash-" + category;
  item.textContent = message;
  list.insertBefore(item, list.firstChild);
  setTimeout(function () {
    item.remove();
  }, 5000);
}

// Generic AJAX action for buttons marked `data-ajax-action`. On success the
// closest `[data-row]` ancestor is removed from the DOM (falling back to a
// full reload if there isn't one) so list screens behave like an SPA instead
// of doing a full-page form post + redirect. See CLAUDE.md "AJAX actions".
document.addEventListener("click", function (event) {
  var button = event.target.closest("[data-ajax-action]");
  if (!button) return;

  var confirmMessage = button.dataset.confirm;
  if (confirmMessage && !window.confirm(confirmMessage)) return;

  var url = button.dataset.ajaxAction;
  var method = button.dataset.method || "POST";
  var row = button.closest("[data-row]");

  button.disabled = true;

  fetch(url, {
    method: method,
    headers: { "X-Requested-With": "XMLHttpRequest" },
  })
    .then(function (response) {
      return agentflowReadJson(response).then(function (data) {
        return { ok: response.ok, data: data };
      });
    })
    .then(function (result) {
      if (!result.ok) {
        throw new Error(result.data.error || "Request failed");
      }
      // Row removal (or the reload below) is itself the success feedback;
      // no toast needed on success. Errors still get one, in the catch below.
      if (row) {
        var onRemoved = button.dataset.onRemoved;
        row.remove();
        if (onRemoved) window.dispatchEvent(new CustomEvent(onRemoved));
      } else {
        window.location.reload();
      }
    })
    .catch(function (err) {
      button.disabled = false;
      agentflowFlash(err.message || "Something went wrong.", "error");
    });
});

// Git action buttons (stage/unstage/discard/delete) marked `data-git-action`;
// an optional `data-confirm` message gates destructive ones.
document.addEventListener("click", function (event) {
  var button = event.target.closest("[data-git-action]");
  if (!button) return;

  if (button.dataset.confirm && !window.confirm(button.dataset.confirm)) return;

  var url = button.dataset.url;
  var path = button.dataset.path;
  var row = button.closest("[data-row]");

  button.disabled = true;

  var formData = new FormData();
  formData.append("path", path);

  fetch(url, {
    method: "POST",
    headers: { "X-Requested-With": "XMLHttpRequest" },
    body: formData,
  })
    .then(function (response) {
      return response.json().then(function (data) {
        return { ok: response.ok, data: data };
      });
    })
    .then(function (result) {
      if (!result.ok) {
        throw new Error(result.data.error || "Request failed");
      }
      if (row) {
        row.remove();
      } else {
        window.location.reload();
      }
    })
    .catch(function (err) {
      button.disabled = false;
      agentflowFlash(err.message || "Something went wrong.", "error");
    });
});

// Navigation `<select>`s marked `data-nav-select` (e.g. the workspace repo
// switcher) navigate to the selected option's value on change.
document.addEventListener("change", function (event) {
  var select = event.target.closest("[data-nav-select]");
  if (!select) return;
  window.location.href = select.value;
});

// Sidebar drawer for narrow screens. The sidebar itself is `#primary-nav`;
// on wide screens it is always visible and these controls are inert.
(function () {
  var toggle = document.querySelector(".nav-toggle");
  var nav = document.getElementById("primary-nav");
  var backdrop = document.querySelector("[data-sidebar-backdrop]");
  if (!toggle || !nav) return;

  function setOpen(open) {
    var wasOpen = nav.classList.contains("open");
    nav.classList.toggle("open", open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (backdrop) backdrop.hidden = !open;
    // Keyboard users: move into the drawer when it opens and back to the
    // toggle when it closes, instead of leaving focus on a now-hidden link.
    if (open && !wasOpen) {
      var first = nav.querySelector("a[href]:not([aria-disabled])");
      if (first) first.focus();
    } else if (!open && wasOpen && nav.contains(document.activeElement)) {
      toggle.focus();
    }
  }

  toggle.addEventListener("click", function () {
    setOpen(!nav.classList.contains("open"));
  });
  if (backdrop) backdrop.addEventListener("click", function () { setOpen(false); });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") setOpen(false);
  });
  // Following a link inside the drawer closes it before the next page loads.
  nav.addEventListener("click", function (event) {
    if (event.target.closest("a")) setOpen(false);
  });
  // Leaving the narrow layout while the drawer is open must not leave a
  // stranded backdrop behind.
  window.matchMedia("(min-width: 861px)").addEventListener("change", function (event) {
    if (event.matches) setOpen(false);
  });
})();

// Light/dark toggle. The initial theme is applied by a tiny inline script in
// base.html before first paint; this only handles the explicit user choice.
document.addEventListener("click", function (event) {
  var button = event.target.closest("[data-theme-toggle]");
  if (!button) return;
  var current = document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
  var next = current === "light" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", next);
  try { localStorage.setItem("agentflow-theme", next); } catch (e) {}
});

// On narrow screens the tab strip scrolls sideways; make sure the current tab
// is on screen after navigation instead of hiding off the right edge.
(function () {
  var current = document.querySelector(".project-tabs .tab[aria-current='page']");
  if (current && current.scrollIntoView) {
    current.scrollIntoView({ inline: "center", block: "nearest" });
  }
})();

// Notifications: keep the sidebar badge current and surface browser
// notifications for sessions blocked on a human. Delivery is only requested
// once the browser has granted permission, so a denied browser falls back to
// the in-app badge and list.
(function () {
  var POLL_MS = 10000;
  var badges = document.querySelectorAll("[data-notify-badge]");
  if (!badges.length || !window.fetch) return;

  function setBadge(count) {
    badges.forEach(function (badge) {
      badge.textContent = count;
      badge.hidden = !count;
    });
  }

  function canShow() {
    return "Notification" in window && Notification.permission === "granted";
  }

  function poll() {
    fetch("/notifications/poll" + (canShow() ? "?deliver=1" : ""))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        setBadge(data.unread);
        data.browser.forEach(function (n) {
          var popup = new Notification("AgentFlow", { body: n.message, tag: "agentflow-" + n.id });
          popup.onclick = function () { window.focus(); window.location.href = n.url; };
        });
      })
      .catch(function () {});
  }

  setInterval(poll, POLL_MS);

  var enable = document.getElementById("enableBrowserNotifications");
  var status = document.getElementById("browserNotificationStatus");
  function describe() {
    if (!enable) return;
    if (!("Notification" in window)) {
      status.textContent = "This browser does not support notifications; in-app alerts still work.";
      enable.hidden = true;
    } else if (Notification.permission === "granted") {
      status.textContent = "Browser notifications are enabled.";
      enable.hidden = true;
    } else if (Notification.permission === "denied") {
      status.textContent = "Browser notifications are blocked in your browser settings; in-app alerts still work.";
      enable.hidden = true;
    } else {
      status.textContent = "";
      enable.hidden = false;
    }
  }
  if (enable) {
    describe();
    enable.addEventListener("click", function () {
      Notification.requestPermission().then(describe);
    });
  }

  document.addEventListener("click", function (event) {
    var link = event.target.closest("[data-notification-read]");
    if (link) {
      navigator.sendBeacon(link.dataset.notificationRead);
    }
  });
})();

// Session header controls (chat page): rename via prompt, fork and switch
// model. Each posts with the AJAX header and reports errors via flash.
(function () {
  function post(url, fields) {
    var body = new FormData();
    Object.keys(fields || {}).forEach(function (key) { body.append(key, fields[key]); });
    return fetch(url, {
      method: "POST",
      headers: { "X-Requested-With": "XMLHttpRequest" },
      body: body,
    }).then(function (response) {
      return agentflowReadJson(response).then(function (data) {
        if (!response.ok) throw new Error(data.error || "Request failed");
        return data;
      });
    });
  }

  document.addEventListener("click", function (event) {
    var rename = event.target.closest("#renameSession");
    if (rename) {
      var title = window.prompt("Session name (leave empty to auto-name):", rename.dataset.title || "");
      if (title === null) return;
      post(rename.dataset.url, { title: title })
        .then(function (data) {
          rename.dataset.title = data.title;
          document.getElementById("sessionTitle").textContent = data.title;
        })
        .catch(function (err) { agentflowFlash(err.message, "error"); });
      return;
    }

    var fork = event.target.closest("[data-ajax-post]");
    if (fork) {
      fork.disabled = true;
      post(fork.dataset.ajaxPost)
        .then(function (data) { window.location.href = data.url; })
        .catch(function (err) {
          fork.disabled = false;
          agentflowFlash(err.message, "error");
        });
    }
  });

  document.addEventListener("change", function (event) {
    var select = event.target.closest("#modelSwitch");
    if (!select) return;
    post(select.dataset.url, { model: select.value })
      .then(function (data) { agentflowFlash(data.message, "info"); })
      .catch(function (err) { agentflowFlash(err.message, "error"); });
  });
})();

// File browser: rename buttons prompt for the new name. (Forms marked
// `data-ajax-form` are handled once, by the global handler further down; this
// block used to register a second `submit` handler too, so every such form posted twice.)
(function () {
  function send(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "X-Requested-With": "XMLHttpRequest" },
      body: body,
    }).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) throw new Error(data.error || "Request failed");
        return data;
      });
    });
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-rename-url]");
    if (!button) return;
    var name = window.prompt("New name:", button.dataset.name || "");
    if (name === null || name.trim() === "" || name === button.dataset.name) return;
    var body = new FormData();
    body.append("new_name", name);
    send(button.dataset.renameUrl, body)
      .then(function () { window.location.reload(); })
      .catch(function (err) { agentflowFlash(err.message, "error"); });
  });
})();

// Buttons marked `data-ajax-reload` post to their URL and reload the page on
// success: for actions that change what the whole page shows (star, merge)
// rather than remove a row. Optional `data-confirm`. The server's message is
// flashed after the reload.
(function () {
  var pending = sessionStorage.getItem("agentflow:flash");
  if (pending) {
    sessionStorage.removeItem("agentflow:flash");
    document.addEventListener("DOMContentLoaded", function () { agentflowFlash(pending, "info"); });
  }
})();

document.addEventListener("click", function (event) {
  var button = event.target.closest("[data-ajax-reload]");
  if (!button) return;
  if (button.dataset.confirm && !window.confirm(button.dataset.confirm)) return;
  button.disabled = true;
  fetch(button.dataset.ajaxReload, {
    method: "POST",
    headers: { "X-Requested-With": "XMLHttpRequest" },
  })
    .then(function (response) {
      return agentflowReadJson(response).then(function (data) {
        if (!response.ok) throw new Error(data.error || "Request failed");
        if (data.message) sessionStorage.setItem("agentflow:flash", data.message);
        window.location.reload();
      });
    })
    .catch(function (err) {
      button.disabled = false;
      agentflowFlash(err.message || "Something went wrong.", "error");
    });
});

// An element marked `data-autorefresh="<ms>"` reloads the page after that
// delay: used while a Run is active so status and logs stay current.
(function () {
  var marker = document.querySelector("[data-autorefresh]");
  if (!marker) return;
  var delay = parseInt(marker.dataset.autorefresh, 10) || 3000;
  // A reload throws away anything being typed (e.g. Ralph steering text), so
  // while a form control has focus or unsaved text, wait and look again.
  function typing() {
    var el = document.activeElement;
    if (el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) && (el.value || el.tagName === "SELECT")) return true;
    return Array.prototype.some.call(document.querySelectorAll("main textarea, main input[type=text]"), function (f) {
      return f.value && f.value !== f.defaultValue;
    });
  }
  (function tick() {
    setTimeout(function () {
      if (typing()) tick();
      else window.location.reload();
    }, delay);
  })();
})();

// Forms marked `data-ajax-form` submit in the background (files included).
// On success the page reloads so lists/counts reflect the change and the
// server's flash message shows; failures show an error toast in place.
// Selects/inputs marked `data-ajax-change="<url>"` post their own value
// (named by the control's `name`) when changed, for inline editing.
function agentflowPost(url, body, method) {
  return fetch(url, {
    method: method || "POST",
    headers: { "X-Requested-With": "XMLHttpRequest" },
    body: body,
  }).then(function (response) {
    return agentflowReadJson(response).then(function (data) {
      if (!response.ok) throw new Error(data.error || "Request failed");
      return data;
    });
  });
}

document.addEventListener("submit", function (event) {
  var form = event.target.closest("form[data-ajax-form]");
  if (!form) return;
  event.preventDefault();
  var submit = form.querySelector("[type=submit]");
  if (submit) submit.disabled = true;
  // The submitter (e.g. a "Save & mark reviewed" button) contributes its own
  // name/value, as it would in a normal post.
  var body = event.submitter ? new FormData(form, event.submitter) : new FormData(form);
  agentflowPost(form.action, body, form.method.toUpperCase())
    .then(function (data) {
      // Endpoints may name a page to go to (e.g. a freshly created record).
      if (data && data.message) sessionStorage.setItem("agentflow:flash", data.message);
      if (data && data.redirect) window.location.href = data.redirect;
      else window.location.reload();
    })
    .catch(function (err) {
      if (submit) submit.disabled = false;
      agentflowFlash(err.message || "Something went wrong.", "error");
    });
});

document.addEventListener("change", function (event) {
  var control = event.target.closest("[data-ajax-change]");
  if (!control) return;
  var body = new FormData();
  body.append(control.name, control.value);
  control.disabled = true;
  agentflowPost(control.dataset.ajaxChange, body)
    .then(function () {
      control.disabled = false;
      var row = control.closest("[data-row]");
      if (row) row.classList.add("row-saved");
      if (row && control.dataset.removeOnChange) row.remove();
    })
    .catch(function (err) {
      control.disabled = false;
      agentflowFlash(err.message || "Something went wrong.", "error");
    });
});

// A page showing a running background job (e.g. a sprint being planned) marks
// an element `data-poll-plan="<status url>"`; the page reloads once the job
// reports anything other than RUNNING.
(function () {
  var marker = document.querySelector("[data-poll-plan]");
  if (!marker) return;
  var timer = setInterval(function () {
    fetch(marker.dataset.pollPlan, { headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (data.state === "RUNNING") return;
        clearInterval(timer);
        if (data.state === "FAILED") {
          agentflowFlash(data.error || "Planning failed.", "error");
          setTimeout(function () {
            window.location.reload();
          }, 3000);
        } else {
          window.location.reload();
        }
      })
      .catch(function () {});
  }, 3000);
})();

// Wiki [[slug]] link preview popover (app/shell.py's `wiki_links` filter
// renders each reference as a `.wiki-link` span carrying `data-wiki-slug`).
// Hover shows it on desktop; a tap (which also fires `click`) shows/hides it
// on touch, since there is no hover state to rely on there.
(function () {
  var activePopover = null;

  function closePopover() {
    if (activePopover) {
      activePopover.remove();
      activePopover = null;
    }
  }

  function showPopover(link) {
    if (activePopover && link.contains(activePopover)) return;
    closePopover();
    var slug = link.dataset.wikiSlug;
    fetch("/wiki/" + encodeURIComponent(slug) + "/preview")
      .then(function (r) {
        return r.ok ? r.json() : Promise.reject();
      })
      .then(function (data) {
        var pop = document.createElement("div");
        pop.className = "wiki-link-preview";
        var title = document.createElement("strong");
        title.textContent = data.title;
        var snippet = document.createElement("span");
        snippet.textContent = data.snippet;
        pop.appendChild(title);
        pop.appendChild(snippet);
        link.appendChild(pop);
        activePopover = pop;
      })
      .catch(function () {});
  }

  document.addEventListener("mouseover", function (event) {
    var link = event.target.closest(".wiki-link");
    if (link) showPopover(link);
  });
  document.addEventListener("mouseout", function (event) {
    var link = event.target.closest(".wiki-link");
    if (link && !link.contains(event.relatedTarget)) closePopover();
  });
  document.addEventListener("click", function (event) {
    var link = event.target.closest(".wiki-link");
    if (link) {
      event.preventDefault();
      if (activePopover && link.contains(activePopover)) {
        closePopover();
      } else {
        showPopover(link);
      }
      return;
    }
    if (!event.target.closest(".wiki-link-preview")) closePopover();
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closePopover();
  });
})();

// Wiki sidebar badge: same polling shape as the notifications badge, driven
// by /wiki/review-queue's pending count via the shell context, refreshed
// each time a review action succeeds elsewhere on the page.
window.addEventListener("wiki-review-resolved", function () {
  var badge = document.querySelector("[data-wiki-review-badge]");
  if (!badge) return;
  var next = Math.max(0, (parseInt(badge.textContent, 10) || 0) - 1);
  badge.textContent = next;
  badge.hidden = next === 0;
});

// Topic pickers (`data-topic-select`, sessions/_topics.html): "New topic..."
// reveals the form's inline name field and makes it required.
(function () {
  function sync(select) {
    var form = select.closest("form");
    var field = form && form.querySelector("[data-topic-new-field]");
    if (!field) return;
    var isNew = select.value === "new";
    field.hidden = !isNew;
    var input = field.querySelector("input");
    if (input) input.required = isNew;
    if (isNew && input) input.focus();
  }
  document.addEventListener("change", function (event) {
    var select = event.target.closest("[data-topic-select]");
    if (select) sync(select);
  });
  document.querySelectorAll("[data-topic-select]").forEach(function (select) {
    if (select.value === "new") sync(select);
  });
})();

// Topic rename buttons (`data-topic-rename="<url>"`, sessions/topics.html and
// topic.html) prompt for the name and update the row/heading in place.
document.addEventListener("click", function (event) {
  var button = event.target.closest("[data-topic-rename]");
  if (!button) return;
  var name = window.prompt("Topic name:", button.dataset.name || "");
  if (name === null || name.trim() === "" || name === button.dataset.name) return;
  var body = new FormData();
  body.append("name", name);
  agentflowPost(button.dataset.topicRename, body)
    .then(function (data) {
      button.dataset.name = data.name;
      var scope = button.closest("[data-row]") || document;
      var label = scope.querySelector("[data-topic-name]");
      if (label) label.textContent = data.name;
      agentflowFlash(data.message || "Topic renamed.", "info");
    })
    .catch(function (err) { agentflowFlash(err.message || "Something went wrong.", "error"); });
});

// Copy-to-clipboard buttons (`data-copy="<text>"`), e.g. the wiki's Copy link /
// Share. Falls back to a hidden textarea where the async clipboard API is
// unavailable (plain-http hosts other than localhost).
function agentflowCopy(text) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  return new Promise(function (resolve, reject) {
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    area.remove();
    if (ok) resolve(); else reject(new Error("Copy failed; select and copy the text by hand."));
  });
}

document.addEventListener("click", function (event) {
  var button = event.target.closest("[data-copy]");
  if (!button) return;
  agentflowCopy(button.dataset.copy)
    .then(function () { agentflowFlash("Copied to the clipboard.", "info"); })
    .catch(function (err) { agentflowFlash(err.message, "error"); });
});

// Project wiki browser (app/templates/wikis/browser.html).
(function () {
  // Page-list drawer on narrow screens.
  document.addEventListener("click", function (event) {
    var toggle = event.target.closest("[data-drawer-toggle]");
    if (!toggle) return;
    var drawer = document.getElementById(toggle.dataset.drawerToggle);
    if (!drawer) return;
    var open = !drawer.classList.contains("is-open");
    drawer.classList.toggle("is-open", open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
  });

  var content = document.querySelector("[data-wiki-collapsible]");
  if (content) {
    // Copy buttons on code blocks.
    content.querySelectorAll("pre.wiki-code").forEach(function (pre) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "wiki-copy-code";
      button.textContent = "Copy";
      button.addEventListener("click", function () {
        agentflowCopy(pre.querySelector("code").innerText)
          .then(function () { button.textContent = "Copied"; setTimeout(function () { button.textContent = "Copy"; }, 1500); })
          .catch(function (err) { agentflowFlash(err.message, "error"); });
      });
      pre.appendChild(button);
    });

    // Collapsible sections: a toggle on each h2-h4 hides everything up to the
    // next heading of the same or a higher level.
    var headings = Array.prototype.slice.call(content.querySelectorAll("h2.wiki-heading, h3.wiki-heading, h4.wiki-heading"));
    headings.forEach(function (heading) {
      var level = parseInt(heading.tagName.substring(1), 10);
      var toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "wiki-collapse";
      toggle.setAttribute("aria-expanded", "true");
      toggle.setAttribute("aria-label", "Collapse section " + heading.textContent.replace(/#$/, "").trim());
      toggle.innerHTML = "&#9662;";
      heading.insertBefore(toggle, heading.firstChild);
      toggle.addEventListener("click", function () {
        var expand = toggle.getAttribute("aria-expanded") !== "true";
        toggle.setAttribute("aria-expanded", expand ? "true" : "false");
        var node = heading.nextElementSibling;
        while (node) {
          var match = /^H([1-6])$/.exec(node.tagName);
          if (match && parseInt(match[1], 10) <= level) break;
          node.classList.toggle("wiki-collapsed", !expand);
          // Expanding opens nested sections too, so nothing stays hidden
          // behind a heading whose toggle says it is open.
          var nested = match && node.querySelector(".wiki-collapse");
          if (nested && expand) nested.setAttribute("aria-expanded", "true");
          node = node.nextElementSibling;
        }
      });
    });
  }

  // Page information collapses on phones (it sits below the content there).
  var metaPanel = document.querySelector("[data-wiki-meta]");
  if (metaPanel && window.matchMedia("(max-width: 639px)").matches) metaPanel.open = false;

  // Search box: filters the page list by title instantly and, after a 300ms
  // pause, shows full-text results above the content.
  var input = document.querySelector("[data-wiki-search]");
  var panel = document.querySelector("[data-wiki-live-results]");
  if (!input || !panel) return;
  var items = Array.prototype.slice.call(document.querySelectorAll("[data-wiki-toc-item]"));
  var empty = document.querySelector("[data-wiki-toc-empty]");
  var timer = null;
  var latest = 0;

  function filterToc(query) {
    var shown = 0;
    items.forEach(function (item) {
      var match = !query || item.textContent.toLowerCase().indexOf(query) !== -1;
      item.hidden = !match;
      if (match) shown += 1;
    });
    if (query) {
      document.querySelectorAll(".wiki-toc-folder").forEach(function (folder) {
        folder.open = !!folder.querySelector("[data-wiki-toc-item]:not([hidden])");
      });
    }
    if (empty) empty.hidden = shown !== 0 || !query;
  }

  function showResults(query, results) {
    panel.replaceChildren();
    var heading = document.createElement("p");
    heading.className = "form-hint";
    heading.textContent = results.length + (results.length === 1 ? " page matches " : " pages match ") + "“" + query + "”";
    panel.appendChild(heading);
    if (results.length) {
      var list = document.createElement("ol");
      results.slice(0, 8).forEach(function (r) {
        var li = document.createElement("li");
        var a = document.createElement("a");
        a.href = r.url;
        a.textContent = r.title;
        var where = document.createElement("span");
        where.className = "form-hint";
        where.textContent = " " + r.root_label + " / " + r.path;
        var snippet = document.createElement("p");
        snippet.className = "wiki-snippet";
        snippet.innerHTML = r.snippet; // server-escaped text with <mark> only
        li.append(a, where, snippet);
        list.appendChild(li);
      });
      panel.appendChild(list);
    }
    panel.hidden = false;
  }

  input.addEventListener("input", function () {
    var query = input.value.trim();
    filterToc(query.toLowerCase());
    clearTimeout(timer);
    if (!query) { panel.hidden = true; return; }
    timer = setTimeout(function () {
      var ticket = ++latest;
      fetch(input.dataset.wikiSearch + "?q=" + encodeURIComponent(query), {
        headers: { "X-Requested-With": "XMLHttpRequest" },
      })
        .then(function (response) { return agentflowReadJson(response); })
        .then(function (data) {
          if (ticket !== latest) return; // a newer query is in flight
          if (data.error) throw new Error(data.error);
          showResults(query, data.results || []);
        })
        .catch(function (err) { agentflowFlash(err.message || "Search failed.", "error"); });
    }, 300);
  });
})();

// Backlog item "Add to sprint" picker: the form posts to the chosen sprint's
// add-items URL (each option's value). Disabled options are sprints that no
// longer accept items (task 59).
document.addEventListener("submit", function (event) {
  var form = event.target.closest("form[data-sprint-picker]");
  if (!form) return;
  var select = form.querySelector("[data-sprint-select]");
  if (select && select.value) form.action = select.value;
}, true);
