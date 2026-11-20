/* The phase steps of compound cards' phase sequences (_run.html), shown one at a time. Every
   step is in the page, so without JavaScript they are all shown. This shows the top phase and the first
   phase below it, "+ Add a phase" shows the next step, and Remove, on the last step shown,
   clears it and hides it again. A hidden step is always empty, so nothing hidden is sent, and
   the server reads the steps the same way either way: an empty step is skipped. */

"use strict";

// The steps always shown: the top phase and the first phase below it.
const ALWAYS_SHOWN = 2;

function setUpPhaseSteps(run) {
  const steps = [...run.querySelectorAll("[data-phase-step]")];
  const addButton = run.querySelector("[data-add-phase]");
  const isFilled = (step) => [...step.querySelectorAll("input")].some((box) => box.value !== "");

  // How many steps are shown, from the top. When Check sends the card back, every step up to
  // the last one filled in is shown, so nothing typed is hidden.
  let shown = ALWAYS_SHOWN;
  steps.forEach((step, i) => {
    if (isFilled(step)) shown = Math.max(shown, i + 1);
  });

  function show() {
    steps.forEach((step, i) => {
      step.hidden = i >= shown;
      const remove = step.querySelector("[data-remove-phase]");
      if (remove) remove.hidden = i !== shown - 1;  // only the last step shown can be removed
    });
    addButton.hidden = shown >= steps.length;
  }

  addButton.addEventListener("click", () => {
    shown += 1;
    show();
    steps[shown - 1].querySelector("input").focus();  // the new step's temperature box
  });

  steps.forEach((step, i) => {
    const remove = step.querySelector("[data-remove-phase]");
    if (!remove) return;  // the first two steps have none
    remove.addEventListener("click", () => {
      step.querySelectorAll("input").forEach((box) => { box.value = ""; });
      // Cleared by script, which doesn't say the boxes changed, as typing does: this does, so
      // checks.js reads the run again without the step.
      step.dispatchEvent(new Event("input", { bubbles: true }));
      shown = i;
      show();
      addButton.focus();  // the Remove button pressed is hidden now, so the focus goes somewhere near
    });
  });

  show();
}

document.querySelectorAll("[data-phase-builder]").forEach(setUpPhaseSteps);

// The phase sequence on a card cards.js adds later: event.target is the card, or a block of
// values, which has none.
document.addEventListener("card-part-added", (event) => {
  event.target.querySelectorAll("[data-phase-builder]").forEach(setUpPhaseSteps);
});
