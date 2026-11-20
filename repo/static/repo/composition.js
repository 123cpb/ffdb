/* A new mixture's composition boxes (_mixture_boxes.html), on a compound card or Add a mixture's
   form. Not disclosed has no components to fill in, so while it is chosen the boxes for them are
   hidden, unless something is typed in them: Check refuses that, and the curator must see what to
   clear. Without JavaScript they are always shown.

   One listener on the whole page, as in checks.js, takes in the cards cards.js adds later: a
   change bubbles up from the radio chosen. Those cards start with nothing chosen, so their boxes
   start shown, and need nothing when they are added. */

"use strict";

function showComposition(mixture) {
  const chosen = mixture.querySelector('input[name$="composition_status"]:checked');
  const details = mixture.querySelector("[data-composition-details]");
  const typed = [...details.querySelectorAll("textarea")].some((box) => box.value.trim() !== "");
  details.hidden = chosen?.value === "not_disclosed" && !typed;
}

document.querySelectorAll("[data-composition]").forEach(showComposition);

document.addEventListener("change", (event) => {
  const mixture = event.target.closest("[data-composition]");
  if (mixture) showComposition(mixture);
});
