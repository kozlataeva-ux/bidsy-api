"use strict";

const form = document.querySelector("#signup-form");
const consent = document.querySelector("#personal-data-consent");
const submitButton = document.querySelector("#submit-button");
const formMessage = document.querySelector("#form-message");
const toast = document.querySelector("#signup-toast");

function showMessage(message, isError = false) {
  if (formMessage) {
    formMessage.textContent = message;
    formMessage.classList.toggle("error", isError);
  }
  if (toast) {
    toast.textContent = message;
    toast.classList.toggle("error", isError);
    toast.classList.add("show");
    window.clearTimeout(showMessage.timeout);
    showMessage.timeout = window.setTimeout(() => toast.classList.remove("show"), 3800);
  }
}

if (form && consent && submitButton) {
  const syncConsent = () => { submitButton.disabled = !consent.checked; };
  syncConsent();
  consent.addEventListener("change", syncConsent);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!consent.checked) {
      showMessage("Отметьте согласие на обработку персональных данных.", true);
      syncConsent();
      return;
    }

    submitButton.disabled = true;
    submitButton.setAttribute("aria-busy", "true");
    const payload = Object.fromEntries(new FormData(form).entries());
    payload.personal_data_consent = true;
    try {
      const response = await fetch("/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      let result;
      try {
        result = await response.json();
      } catch (_error) {
        throw new Error("Сервер временно недоступен. Попробуйте позже.");
      }
      if (!response.ok || result.status !== "success") {
        throw new Error(result.message || "Не удалось отправить заявку.");
      }
      form.reset();
      showMessage("Заявка отправлена. Спасибо!");
    } catch (error) {
      showMessage(error.message || "Ошибка отправки заявки. Попробуйте позже.", true);
    } finally {
      submitButton.removeAttribute("aria-busy");
      syncConsent();
    }
  });
}

document.querySelectorAll(".faq-item").forEach((item) => {
  item.addEventListener("toggle", () => {
    if (!item.open) return;
    document.querySelectorAll(".faq-item").forEach((other) => {
      if (other !== item) other.open = false;
    });
  });
});


const cookieBanner = document.querySelector("#cookie-banner");
if (cookieBanner) {
  let savedChoice = null;
  try { savedChoice = localStorage.getItem("cookie_consent"); } catch (_error) { /* Storage may be blocked. */ }
  if (!savedChoice) cookieBanner.hidden = false;

  const saveChoice = (choice) => {
    try {
      localStorage.setItem("cookie_consent", choice);
    } catch (_error) {
      showMessage("Браузер не позволил сохранить выбор cookie.", true);
    }
    cookieBanner.hidden = true;
  };
  document.querySelector("#accept-all-cookies")?.addEventListener("click", () => saveChoice("all"));
  document.querySelector("#necessary-cookies")?.addEventListener("click", () => saveChoice("necessary"));
}

// Analytics scripts are intentionally not loaded in this version.
