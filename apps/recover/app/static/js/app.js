(function () {
  function askConfirm(opts) {
    var sheet = document.querySelector("[data-confirm-sheet]");
    if (!sheet) {
      return Promise.resolve(window.confirm(opts.title || "Continue?"));
    }
    return new Promise(function (resolve) {
      var titleEl = sheet.querySelector("[data-confirm-title]");
      var bodyEl = sheet.querySelector("[data-confirm-body]");
      var okBtn = sheet.querySelector("[data-confirm-ok]");
      var cancelBtn = sheet.querySelector("[data-confirm-cancel]");
      if (titleEl) titleEl.textContent = opts.title || "Continue?";
      if (bodyEl) {
        bodyEl.hidden = !opts.body;
        bodyEl.textContent = opts.body || "";
      }
      if (okBtn) okBtn.textContent = opts.okLabel || "OK";

      function finish(value) {
        sheet.hidden = true;
        sheet.classList.remove("is-open");
        document.documentElement.classList.remove("sheet-open");
        document.body.classList.remove("sheet-open");
        sheet.removeEventListener("click", onBackdrop);
        if (okBtn) okBtn.removeEventListener("click", onOk);
        if (cancelBtn) cancelBtn.removeEventListener("click", onCancel);
        document.removeEventListener("keydown", onKey);
        resolve(value);
      }
      function onOk() {
        finish(true);
      }
      function onCancel() {
        finish(false);
      }
      function onBackdrop(event) {
        if (event.target === sheet) finish(false);
      }
      function onKey(event) {
        if (event.key === "Escape") finish(false);
      }

      sheet.addEventListener("click", onBackdrop);
      if (okBtn) okBtn.addEventListener("click", onOk);
      if (cancelBtn) cancelBtn.addEventListener("click", onCancel);
      document.addEventListener("keydown", onKey);
      if (sheet.parentElement !== document.body) {
        document.body.appendChild(sheet);
      }
      sheet.hidden = false;
      sheet.classList.add("is-open");
      document.documentElement.classList.add("sheet-open");
      document.body.classList.add("sheet-open");
      if (okBtn) okBtn.focus();
    });
  }

  function mountSheetsToBody() {
    document.querySelectorAll(".sheet").forEach(function (sheet) {
      if (sheet.parentElement !== document.body) {
        document.body.appendChild(sheet);
      }
    });
  }

  mountSheetsToBody();

  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (form.dataset.confirmBusy === "1") return;
      event.preventDefault();
      var submitter = event.submitter;
      askConfirm({
        title: form.dataset.confirm || "Continue?",
        body: form.dataset.confirmDetail || "",
        okLabel: form.dataset.confirmOk || "OK",
      }).then(function (ok) {
        if (!ok) return;
        form.dataset.confirmBusy = "1";
        // Native submit() omits the clicked button's name/value — preserve op=…
        if (submitter && submitter.name) {
          var existing = form.querySelector(
            'input[type="hidden"][data-confirm-submitter="' + submitter.name + '"]'
          );
          if (existing) existing.remove();
          var hidden = document.createElement("input");
          hidden.type = "hidden";
          hidden.name = submitter.name;
          hidden.value = submitter.value;
          hidden.setAttribute("data-confirm-submitter", submitter.name);
          form.appendChild(hidden);
        }
        HTMLFormElement.prototype.submit.call(form);
      });
    });
  });
})();
