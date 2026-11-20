/* Checks as the curator types, on a reference's Add data page and the add-a-compound page. Each
   answer box (data-check-url) follows the parts of a form that name it (data-check): a compound
   card's material, its runs, each block of values, or the compound table, on every card, including
   those cards.js adds. After a short pause in typing in one of them, the whole form is sent to the
   box's address, and the server's answer, HTML from the templates Check uses, goes in the box. So
   the page shows what Check would, without a reload. Each box is a live region, so a screen reader reads each answer. Draw and the
   Removes change boxes by script, and say so with an input event, as typing does, so what they
   change is checked too.

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

// What each answer box is waiting for, by its id: the timer of the check about to be sent, and
// the number of the last check sent, as only its answer is shown.
const timers = new Map();
const latestChecks = new Map();

async function check(box, form) {
  const mine = (latestChecks.get(box.id) || 0) + 1;
  latestChecks.set(box.id, mine);
  let html;
  try {
    html = await ask(box.dataset.checkUrl, form);
  } catch {
    html = NO_ANSWER;
  }
  if (mine !== latestChecks.get(box.id)) return;  // a newer check was sent while this one was on its way
  document.querySelectorAll(`[data-check="${box.id}"]`).forEach(dropOldProblems);
  box.innerHTML = html;  // from the server's templates, which escape what was typed
}

// What each block's choice boxes are waiting for, by their id: the number of the last request
// sent, as only its answer is used, and whether anything in them was typed since.
const latestBoxes = new Map();
const typedSince = new Map();

async function redraw(boxes, form) {
  const mine = (latestBoxes.get(boxes.id) || 0) + 1;
  latestBoxes.set(boxes.id, mine);
  typedSince.set(boxes.id, false);
  let html;
  try {
    html = await ask(boxes.dataset.boxesUrl, form);
  } catch {
    return;  // the old boxes stay, and Check still draws the right ones
  }
  if (mine !== latestBoxes.get(boxes.id)) return;  // the choice changed again while this was on its way
  // The answer has what the boxes held when it was asked for: ask again with what is typed now.
  if (typedSince.get(boxes.id)) return redraw(boxes, form);
  // The box the cursor is in, if it is about to be replaced, so the cursor can go back into it.
  const focused = boxes.contains(document.activeElement) ? document.activeElement.id : "";
  boxes.innerHTML = html;  // from the server's template, which escapes what was typed
  dropOldProblems(boxes);  // the block's answer box lists them
  if (focused) document.getElementById(focused)?.focus();
}

// Listening on the whole page, rather than on each part, takes in the cards, runs and blocks
// cards.js adds later: an input event bubbles up from the box typed in, selects included, through
// the part it is in to the page.
document.addEventListener("input", (event) => {
  const boxes = event.target.closest("[data-boxes-url]");
  if (boxes) typedSince.set(boxes.id, true);
  const part = event.target.closest("[data-check]");
  if (!part) return;
  const box = document.getElementById(part.dataset.check);
  clearTimeout(timers.get(box.id));
  timers.set(box.id, setTimeout(() => check(box, part.closest("form")), PAUSE_MS));
});

// change, not input, and no pause: a choice is made once, not typed a letter at a time.
document.addEventListener("change", (event) => {
  const choice = event.target.closest("[data-boxes]");
  if (choice) redraw(document.getElementById(choice.dataset.boxes), choice.closest("form"));
});
