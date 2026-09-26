// The template picker adds one input per placeholder the template declares.
(function () {
  var select = document.querySelector("[data-template-select]");
  var holder = document.querySelector("[data-template-fields]");
  if (!select || !holder) return;
  select.addEventListener("change", function () {
    holder.textContent = "";
    var fields = select.selectedOptions[0].dataset.fields;
    if (!fields) return;
    fields.split(",").forEach(function (name) {
      var label = document.createElement("label");
      label.textContent = name.charAt(0).toUpperCase() + name.slice(1) + " ";
      var input = document.createElement("input");
      input.name = "field_" + name;
      input.required = true;
      label.appendChild(input);
      holder.appendChild(label);
    });
  });
})();
