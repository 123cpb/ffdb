/* Checks as the curator types, on a reference's Add data page and the add-a-compound page. Each
   answer box (data-check-url) follows the parts of a form that name it (data-check): a compound
   card's material, its runs, or the compound table. After a short pause in typing in one of
   them, the whole form is sent to the box's address, and the server's answer, HTML from the
   templates Check uses, goes in the box. So the page shows what Check would, without a reload.
   Each box is a live region, so a screen reader reads each answer. Draw and Remove change boxes
   by script, and say so with an input event, as typing does, so what they change is checked too.
   Without JavaScript none of this runs, and Check and Save send the form as before. */

"use strict";

// How long the curator stops typing for before a check is sent.
const PAUSE_MS = 400;
const NO_ANSWER = '<p class="form-error">This couldn\'t be checked here. Check still checks it.</p>';

function setUpAnswerBox(box) {
  const parts = [...document.querySelectorAll(`[data-check="${box.id}"]`)];
  let timer = null;
  let latest = 0;  // the number of the last check sent: only its answer is shown

  async function check(form) {
    const mine = ++latest;
    let html;
    try {
      // The whole form, as Check sends it, CSRF token included. redirect: "error", because a
      // curator logged out meanwhile is sent to the login page, which isn't an answer.
      const response = await fetch(box.dataset.checkUrl, { method: "POST", body: new FormData(form), redirect: "error" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      html = await response.text();
    } catch {
      html = NO_ANSWER;
    }
    if (mine !== latest) return;  // a newer check was sent while this one was on its way
    // What Check or Save last said at the boxes is out of date: the answer lists what still holds.
    parts.forEach((part) => {
      part.querySelectorAll(".form-error").forEach((message) => message.remove());
      part.querySelectorAll("[aria-invalid]").forEach((field) => field.removeAttribute("aria-invalid"));
    });
    box.innerHTML = html;  // from the server's templates, which escape what was typed
  }

  parts.forEach((part) => {
    // input bubbles up from every box in the part, selects included.
    part.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => check(part.closest("form")), PAUSE_MS);
    });
  });
}

document.querySelectorAll("[data-check-url]").forEach(setUpAnswerBox);
