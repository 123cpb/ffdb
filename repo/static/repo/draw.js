/* The Draw buttons on a reference's Add data page and the add-a-compound page: draw a compound
   in Ketcher, the structure editor, and put it where the button says (_draw_dialog.html):
   - The compound table's button (data-table) adds a line of label and SMILES to the table,
     which Check then reads like any pasted line.
   - A compound card's button (data-smiles) puts the SMILES in the card's SMILES box. The card
     has its own label box, so the dialog's is hidden, and the editor opens with the box's
     SMILES, if it has one, so it can be changed by drawing.
   There is one editor for the page, loaded only when Draw is first pressed, because it is about
   30 MB. Ketcher is served from FFDB's own static files, so this page may reach into its frame:
   browsers allow that only within one site. Without JavaScript, or in a browser without
   <dialog>, the buttons stay hidden and the table and the SMILES are typed as before. */

"use strict";

// How long to wait for Ketcher to start before saying it didn't load, and how often to look.
const WAIT_MS = 30000;
const LOOK_EVERY_MS = 200;
// What the status line says for each kind of Draw button: once the editor is ready, and if it
// didn't load.
const SAY = {
  table: {
    ready: "Type the label, draw the structure or paste a SMILES or molfile into the editor, then press Use this structure.",
    failed: "The structure editor didn't load. Cancel, and type the line into the table instead.",
  },
  card: {
    ready: "Draw the structure or paste a SMILES or molfile into the editor, then press Use this structure.",
    failed: "The structure editor didn't load. Cancel, and type the SMILES into the box instead.",
  },
};

function setUpDrawing() {
  const drawButtons = document.querySelectorAll("[data-draw]");
  const dialog = document.getElementById("draw-dialog");
  // No buttons: not an Add data page. No showModal: a browser too old for <dialog>.
  if (!drawButtons.length || !dialog || !dialog.showModal) return;

  const labelField = dialog.querySelector("[data-draw-label]");
  const label = dialog.querySelector("#draw-label");
  const status = dialog.querySelector("[role=status]");
  const editorBox = dialog.querySelector("[data-src]");
  let frame = null;  // Ketcher's page, made the first time Draw is pressed
  let editor = null;  // Ketcher's editor, once it has started
  let pressed = null;  // the Draw button that opened the dialog: what Use this structure fills

  const kind = (button) => (button.dataset.table ? "table" : "card");

  // Make Ketcher's page, the first time only, and wait for it to start. Its page sets
  // window.ketcher when it has, so this looks for that every 200 ms.
  async function startEditor() {
    if (!frame) {
      frame = document.createElement("iframe");
      frame.title = "Structure editor (Ketcher)";
      frame.src = editorBox.dataset.src;
      editorBox.append(frame);
    }
    for (let waited = 0; waited < WAIT_MS; waited += LOOK_EVERY_MS) {
      const found = frame.contentWindow?.ketcher;
      if (found) return found;
      await new Promise((resolve) => setTimeout(resolve, LOOK_EVERY_MS));
    }
    throw new Error("Ketcher didn't start");
  }

  // Add line at the end of the table, on a line of its own, with the cursor after it. The
  // line ends in a tab, ready for a sequence: Tab in a box moves to the next control, so the
  // curator couldn't type one. Without a sequence the tab is dropped as an empty cell.
  function addLine(table, line) {
    const text = table.value;
    const newline = text && !text.endsWith("\n") ? "\n" : "";
    table.value = text + newline + line;
    table.focus();
    table.setSelectionRange(table.value.length, table.value.length);
    table.scrollTop = table.scrollHeight;
  }

  // Open the editor with the card's SMILES. An empty box keeps the drawing already there, as
  // the table's Draw does, so the next compound of a series starts from the last.
  async function loadCard(button) {
    const smiles = document.getElementById(button.dataset.smiles).value.trim();
    if (!smiles) return;
    try {
      await editor.setMolecule(smiles);
    } catch {
      status.textContent = "The editor couldn't read the SMILES in the box. Draw the structure, or cancel and correct the SMILES.";
    }
  }

  async function openEditor(button) {
    pressed = button;
    label.value = "";
    labelField.hidden = kind(button) === "card";
    // Opened before Ketcher's page is made: started in a hidden box, it lays itself out badly.
    dialog.showModal();
    if (!editor) {
      status.textContent = "Loading the structure editor…";
      try {
        editor = await startEditor();
      } catch {
        status.textContent = SAY[kind(button)].failed;
        return;
      }
      // Cancelled while it loaded, and another Draw button pressed: that one carries on.
      if (pressed !== button) return;
    }
    status.textContent = SAY[kind(button)].ready;
    if (kind(button) === "card") await loadCard(button);
  }

  dialog.querySelector("[data-use]").addEventListener("click", async () => {
    if (!editor) return;  // still loading, or it didn't load: the status line says which
    const forTable = kind(pressed) === "table";
    // A tab or run of spaces becomes one space, so the label can't split the line into more cells.
    const text = label.value.replace(/\s+/g, " ").trim();
    if (forTable && !text) {
      status.textContent = "Type the label, as the reference prints it.";
      label.focus();
      return;
    }
    let smiles;
    try {
      // await inside try catches both ways getSmiles() can fail: a promise that fails, or an
      // error thrown at once, as Ketcher 3.18 does in its Macromolecules mode.
      smiles = (await editor.getSmiles()).trim();
    } catch {
      status.textContent = "Ketcher couldn't give a SMILES for this drawing. If it is in Macromolecules mode, switch to Molecules.";
      return;
    }
    if (!smiles) {
      status.textContent = "Draw the structure first, or paste a SMILES or molfile into the editor.";
      return;
    }
    // Closed first: closing puts the focus back on the Draw button, and what follows moves it
    // to the box filled, so the curator sees what went in.
    dialog.close();
    if (forTable) {
      addLine(document.getElementById(pressed.dataset.table), `${text}\t${smiles}\t`);
    } else {
      const box = document.getElementById(pressed.dataset.smiles);
      box.value = smiles;
      box.focus();
    }
  });

  dialog.querySelector("[data-cancel]").addEventListener("click", () => dialog.close());

  drawButtons.forEach((button) => {
    button.addEventListener("click", () => openEditor(button));
    button.hidden = false;
  });
}

setUpDrawing();
