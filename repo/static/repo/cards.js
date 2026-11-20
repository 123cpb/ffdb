/* Adding and removing compound cards (_compound_cards.html), and the blocks of values on each
   card: "+ Add another compound" and "+ Add a block of values", and a Remove on each. A card has
   one phase sequence, so there is nothing to add or remove there. The buttons are hidden, so
   without JavaScript the page has one card with one block, as the server sends it.

   A new card or block is a copy of an empty one the server put in a <template>, with the
   placeholders in it replaced: __card__ in a card's names by its index and __cardnumber__ in its
   heading by its number, the index + 1; __prefix__ (Django's) and __number__ the same way in a
   block's. The hidden count the server reads them by goes up by one: the page's card-TOTAL, or
   the formset's TOTAL_FORMS.

   Remove hides what it removes, and sets its removed box (a card's) or DELETE box (a block's),
   which tells the server to skip it. It stays in the page: the server reads cards and blocks by
   index up to the count, so the others keep their indexes and their numbers.

   A new card or block is announced with a card-part-added event, which phase_steps.js and
   draw.js listen for, to set it up as they set up those in the page when it loads. checks.js
   needs nothing: it listens on the whole page.

   Make cards, under the compound table on a reference's Add data page, adds a card for each line
   of the table without a reload. The server reads the table and draws the cards, filled in
   (views.make_cards), or says why it made none. Without JavaScript the button sends the table's
   form, and the page comes back with the cards. */

"use strict";

// A copy of template's content, with each placeholder in replacements replaced by its value.
function fromTemplate(template, replacements) {
  let html = template.innerHTML;
  for (const [placeholder, value] of Object.entries(replacements)) {
    html = html.replaceAll(placeholder, value);
  }
  const holder = document.createElement("div");
  holder.innerHTML = html;  // the server's empty card or block: nothing typed is in it
  return holder.firstElementChild;
}

// Show the buttons for adding and removing blocks inside part.
function showButtons(part) {
  part.querySelectorAll("[data-add-row], [data-remove-row]").forEach((button) => { button.hidden = false; });
}

// Show part's buttons, and say it was added, so the other scripts set it up.
function announce(part) {
  showButtons(part);
  part.dispatchEvent(new CustomEvent("card-part-added", { bubbles: true }));
}

// Say part was added, and put the cursor in its first box.
function added(part) {
  announce(part);
  part.querySelector("input:not([type=hidden]), select, textarea").focus();
}

// Whether nothing has been typed or chosen on card: every box still holds what the server sent.
function isEmpty(card) {
  return [...card.querySelectorAll("input:not([type=hidden]), select, textarea")].every((box) => {
    if (box.tagName === "SELECT") return [...box.options].every((option) => option.selected === option.defaultSelected);
    if (box.type === "checkbox" || box.type === "radio") return box.checked === box.defaultChecked;
    return box.value === box.defaultValue;
  });
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

  // Make cards. When every card shown is empty, the new cards take the place of the page's cards,
  // numbered from 1, as without JavaScript; otherwise they come after them. The table's answer
  // box says how it went.
  const makeButton = document.querySelector("[data-make-cards]");  // only on the Add data page
  const tableAnswer = document.getElementById("table-result");
  makeButton?.addEventListener("click", async (event) => {
    event.preventDefault();  // so the table's form isn't sent
    const shown = [...list.querySelectorAll("[data-card]")].filter((card) => !card.hidden);
    const start = shown.every(isEmpty) ? 0 : Number(total.value);
    let response = null;
    let html = "";
    try {
      const url = `${makeButton.dataset.url}?start=${start}`;
      response = await fetch(url, { method: "POST", body: new FormData(makeButton.form), redirect: "error" });
      html = await response.text();
    } catch {
      // No answer: the message below says so.
    }
    if (response?.status === 422) {
      tableAnswer.innerHTML = html;  // why no card was made, from the server's templates, which escape what was typed
      return;
    }
    if (!response?.ok) {
      tableAnswer.innerHTML = '<p class="form-error">The cards couldn\'t be made here. Press Make cards again.</p>';
      return;
    }
    const holder = document.createElement("div");
    holder.innerHTML = html;  // the server's cards, from its templates
    const cards = [...holder.children];
    if (start === 0) list.replaceChildren();  // the empty cards go, and any removed ones
    list.append(...cards);
    total.value = start + cards.length;
    update();
    cards.forEach(announce);
    makeButton.form.elements.table.value = "";  // the cards hold its lines now
    const s = cards.length === 1 ? "" : "s";
    tableAnswer.innerHTML = `<p>Made ${cards.length} card${s} above: add their values, then Check and Save the cards.</p>`;
    cards[0].querySelector("input:not([type=hidden]), select, textarea").focus();
  });

  update();
}

// "+ Add a block of values" on every card, those added later too. The button names its formset's
// prefix, e.g. card-0-block, and the formset's list and template are named by it.
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

// Remove on every block.
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove-row]");
  if (!button) return;
  const row = button.closest("[data-row]");
  row.querySelector('[name$="-DELETE"]').value = "1";
  row.hidden = true;
  // As typing does, so checks.js reads the card again without this block.
  row.dispatchEvent(new Event("input", { bubbles: true }));
  const list = row.parentElement;  // the formset's list, named prefix-list
  document.querySelector(`[data-add-row="${list.id.replace(/-list$/, "")}"]`).focus();
});

document.querySelectorAll("#cards").forEach(setUpCards);
document.querySelectorAll("#cards [data-card]").forEach(showButtons);
