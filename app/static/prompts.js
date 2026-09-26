// Prompt library: live preview of the template form (nothing is saved).
(function () {
  var button = document.querySelector("[data-preview-url]");
  if (!button) return;
  var form = document.getElementById(button.dataset.previewForm);
  var out = document.querySelector("[data-preview-output]");
  var text = out.querySelector("[data-preview-text]");
  var meta = out.querySelector("[data-preview-meta]");
  var vars = document.querySelector("[data-preview-variables]");

  button.addEventListener("click", function () {
    var body = new FormData(form);
    body.set("preview_variables", vars.value);
    button.disabled = true;
    fetch(button.dataset.previewUrl, {
      method: "POST", body: body, headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then(function (r) { return r.json().then(function (d) { return [r.ok, d]; }); })
      .then(function (res) {
        out.hidden = false;
        if (!res[0]) throw new Error(res[1].error || "Preview failed");
        text.textContent = res[1].prompt;
        var m = res[1].metadata, bits = [];
        if (m.fragments_included.length) bits.push("Fragments: " + m.fragments_included.join(", "));
        if (m.missing_variables.length) bits.push("Unset variables: " + m.missing_variables.join(", "));
        meta.textContent = bits.join(" · ");
      })
      .catch(function (err) { agentflowFlash(err.message, "error"); })
      .then(function () { button.disabled = false; });
  });
})();
