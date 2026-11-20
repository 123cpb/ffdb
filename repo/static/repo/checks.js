/* Checks as the curator types, on a reference's Add data page and the add-a-compound page. Each
   answer box (data-check-url) follows the parts of a form that name it (data-check): a compound
   card's material, its runs, each block of values, or the compound table. After a short pause in
   typing in one of them, the whole form is sent to the box's address, and the server's answer,
   HTML from the templates Check uses, goes in the box. So the page shows what Check would, without
   a reload. Each box is a live region, so a screen reader reads each answer. Draw and Remove
   change boxes by script, and say so with an input event, as typing does, so what they change is
   checked too.

   A block of values also has boxes that depend on its choice of property, kind and what it is
   against (data-boxes-url). When the choice changes, the server draws them again for the new
   choice, with what was typed in them, and they go in place of the old ones.

   Without JavaScript none of this runs, and Check and Save send the form as before. */

"use strict";

// How long the curator stops typing for before a check is sent.
const PAUSE_MS = 400;
const NO_ANSWER = '<p class="form-error">This couldn\'t be checked here. Check still checks it.</p>';

// Send the whole form, as Check sends it, CSRF token included, and give back the server's HTML.
// redirect: "error", because a curator logged out meanwhile is sent to the login page, which
// isn't an answer. Throws when there is no answer.
async function ask(url, form) {
  const response = await fetch(url, { method: "POST", body: new FormData(form), redirect: "error" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.text();
}

// Remove what Check or Save last said at the boxes inside element: it is out of date, and the
// answer box lists what still holds.
function dropOldProblems(element) {
  element.querySelectorAll(".form-error").forEach((message) => message.remove());
  element.querySelectorAll("[aria-invalid]").forEach((field) => field.removeAttribute("aria-invalid"));
}

function setUpAnswerBox(box) {
  const parts = [...document.querySelectorAll(`[data-check="${box.id}"]`)];
  let timer = null;
  let latest = 0;  // the number of the last check sent: only its answer is shown

  async function check(form) {
    const mine = ++latest;
    let html;
    try {
      html = await ask(box.dataset.checkUrl, form);
    } catch {
      html = NO_ANSWER;
    }
    if (mine !== latest) return;  // a newer check was sent while this one was on its way
    parts.forEach(dropOldProblems);
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

function setUpChoiceBoxes(boxes) {
  const choice = document.querySelector(`[data-boxes="${boxes.id}"]`);
  let latest = 0;  // the number of the last request sent: only its answer is used
  let typed = false;  // whether anything in the boxes was typed since then

  boxes.addEventListener("input", () => { typed = true; });

  async function redraw(form) {
    const mine = ++latest;
    typed = false;
    let html;
    try {
      html = await ask(boxes.dataset.boxesUrl, form);
    } catch {
      return;  // the old boxes stay, and Check still draws the right ones
    }
    if (mine !== latest) return;  // the choice changed again while this was on its way
    // The answer has what the boxes held when it was asked for: ask again with what is typed now.
    if (typed) return redraw(form);
    // The box the cursor is in, if it is about to be replaced, so the cursor can go back into it.
    const focused = boxes.contains(document.activeElement) ? document.activeElement.id : "";
    boxes.innerHTML = html;  // from the server's template, which escapes what was typed
    dropOldProblems(boxes);  // the block's answer box lists them
    if (focused) document.getElementById(focused)?.focus();
  }

  // change, not input, and no pause: a choice is made once, not typed a letter at a time.
  choice.addEventListener("change", () => redraw(choice.closest("form")));
}

document.querySelectorAll("[data-check-url]").forEach(setUpAnswerBox);
document.querySelectorAll("[data-boxes-url]").forEach(setUpChoiceBoxes);
