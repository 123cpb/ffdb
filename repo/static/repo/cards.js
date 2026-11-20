/* Adding and removing compound cards (_compound_cards.html), and the runs and blocks of values on
   each card: "+ Add another compound", "+ Add a run" and "+ Add a block of values", and a Remove
   on each. The buttons are hidden, so without JavaScript the page has one card with two runs and
   two blocks, as the server sends it.

   A new card, run or block is a copy of an empty one the server put in a <template>, with the
   placeholders in it replaced: __card__ in a card's names by its index and __cardnumber__ in its
   heading by its number, the index + 1; __prefix__ (Django's) and __number__ the same way in a
   run's or block's. The hidden count the server reads them by goes up by one: the page's
   card-TOTAL, or the formset's TOTAL_FORMS.

   Remove hides what it removes, and sets its removed box (a card's) or DELETE box (a run's or
   block's), which tells the server to skip it. It stays in the page: the server reads cards,
   runs and blocks by index up to the count, so the others keep their indexes and their numbers.

   A new card, run or block is announced with a card-part-added event, which phase_steps.js and
   draw.js listen for, to set it up as they set up those in the page when it loads. checks.js
   needs nothing: it listens on the whole page. */

"use strict";

// A copy of template's content, with each placeholder in replacements replaced by its value.
function fromTemplate(template, replacements) {
  let html = template.innerHTML;
  for (const [placeholder, value] of Object.entries(replacements)) {
    html = html.replaceAll(placeholder, value);
  }
  const holder = document.createElement("div");
  holder.innerHTML = html;  // the server's empty card, run or block: nothing typed is in it
  return holder.firstElementChild;
}

// Show the buttons for adding and removing runs and blocks inside part.
function showButtons(part) {
  part.querySelectorAll("[data-add-row], [data-remove-row]").forEach((button) => { button.hidden = false; });
}

// Say part was added, and put the cursor in its first box.
function added(part) {
  showButtons(part);
  part.dispatchEvent(new CustomEvent("card-part-added", { bubbles: true }));
  part.querySelector("input:not([type=hidden]), select, textarea").focus();
}

function setUpCards(list) {
  const form = list.closest("form");
  const total = form.querySelector('[name="card-TOTAL"]');
  const addButton = form.querySelector("[data-add-card]");
  const template = document.getElementById("card-template");

  // Remove is offered while two or more cards are shown, so one always is. Add is offered until
  // the page has as many cards as it can, removed ones included.
  function update() {
    const shown = [...list.querySelectorAll("[data-card]")].filter((card) => !card.hidden);
    shown.forEach((card) => { card.querySelector("[data-remove-card]").hidden = shown.length < 2; });
    addButton.hidden = Number(total.value) >= Number(total.dataset.max);
  }

  addButton.addEventListener("click", () => {
    const index = Number(total.value);
    const card = fromTemplate(template, { __cardnumber__: index + 1, __card__: index });
    list.append(card);
    total.value = index + 1;
    update();
    added(card);
  });

  // One listener for every card's Remove, those added later too: a click bubbles up to the list.
  list.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-card]");
    if (!button) return;
    const card = button.closest("[data-card]");
    card.querySelector('[name$="-removed"]').value = "1";
    card.hidden = true;
    update();
    addButton.focus();  // the Remove pressed is hidden now, so the focus goes somewhere near
  });

  update();
}

// "+ Add a run" and "+ Add a block of values" on every card, those added later too. The button
// names its formset's prefix, e.g. card-0-run, and the formset's list and template are named by it.
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-add-row]");
  if (!button) return;
  const prefix = button.dataset.addRow;
  const count = document.querySelector(`[name="${prefix}-TOTAL_FORMS"]`);
  const index = Number(count.value);
  const row = fromTemplate(document.getElementById(`${prefix}-template`), { __number__: index + 1, __prefix__: index });
  document.getElementById(`${prefix}-list`).append(row);
  count.value = index + 1;
  added(row);
});

// Remove on every run and block.
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove-row]");
  if (!button) return;
  const row = button.closest("[data-row]");
  row.querySelector('[name$="-DELETE"]').value = "1";
  row.hidden = true;
  // As typing does, so checks.js reads the card's runs again without this one.
  row.dispatchEvent(new Event("input", { bubbles: true }));
  const list = row.parentElement;  // the formset's list, named prefix-list
  document.querySelector(`[data-add-row="${list.id.replace(/-list$/, "")}"]`).focus();
});

document.querySelectorAll("#cards").forEach(setUpCards);
document.querySelectorAll("#cards [data-card]").forEach(showButtons);
