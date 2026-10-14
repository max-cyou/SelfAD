document.querySelectorAll("[data-submission-toggle]").forEach((button) => {
  button.addEventListener("click", () => {
    const detail = document.getElementById(button.getAttribute("aria-controls"));
    if (!detail) return;
    detail.hidden = !detail.hidden;
    button.setAttribute("aria-expanded", String(!detail.hidden));
    button.textContent = detail.hidden ? "View" : "Close";
  });
});
