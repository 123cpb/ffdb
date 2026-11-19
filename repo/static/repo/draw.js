/* The Draw button on a reference's Add data page: draw a compound in Ketcher, the structure
   editor, and add it to the compound table as a line of label and SMILES, which Check then
   reads like any pasted line. There is one editor for the page, loaded only when Draw is
   first pressed, because it is about 30 MB. Ketcher is served from FFDB's own static files,
   so this page may reach into its frame: browsers allow that only within one site. Without
   JavaScript, or in a browser without <dialog>, the button stays hidden and the table is
   typed or pasted as before. */

"use strict";

// How long to wait for Ketcher to start before saying it didn't load, and how often to look.
const WAIT_MS = 30000;
const LOOK_EVERY_MS = 200;
const READY = "Type the label, draw the structure or paste a SMILES or molfile into the editor, then press Use this structure.";

function setUpDrawing() {
  const drawButton = document.querySelector("[data-draw]");
  const dialog = document.getElementById("draw-dialog");
  // No button: not the Add data page. No showModal: a browser too old for <dialog>.
  if (!drawButton || !dialog || !dialog.showModal) return;

  const table = document.getElementById(drawButton.dataset.table);
  const label = dialog.querySelector("#draw-label");
  const status = dialog.querySelector("[role=status]");
  const editorBox = dialog.querySelector("[data-src]");
  let frame = null;  // Ketcher's page, made the first time Draw is pressed
  let editor = null;  // Ketcher's editor, once it has started

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
  function addLine(line) {
    const text = table.value;
    const newline = text && !text.endsWith("\n") ? "\n" : "";
    table.value = text + newline + line;
    table.focus();
    table.setSelectionRange(table.value.length, table.value.length);
    table.scrollTop = table.scrollHeight;
  }

  drawButton.addEventListener("click", async () => {
    label.value = "";  // the drawing is kept, so the next compound of a series starts from it
    // Opened before Ketcher's page is made: started in a hidden box, it lays itself out badly.
    dialog.showModal();
    if (editor) {
      status.textContent = READY;
      return;
    }
    status.textContent = "Loading the structure editor…";
    try {
      editor = await startEditor();
      status.textContent = READY;
    } catch {
      status.textContent = "The structure editor didn't load. Cancel, and type the line into the table instead.";
    }
  });

  dialog.querySelector("[data-use]").addEventListener("click", async () => {
    if (!editor) return;  // still loading, or it didn't load: the status line says which
    // A tab or run of spaces becomes one space, so the label can't split the line into more cells.
    const text = label.value.replace(/\s+/g, " ").trim();
    if (!text) {
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
    // Closed first: closing puts the focus back on the Draw button, and addLine moves it to the table.
    dialog.close();
    addLine(`${text}\t${smiles}\t`);
  });

  dialog.querySelector("[data-cancel]").addEventListener("click", () => dialog.close());

  drawButton.hidden = false;
}

setUpDrawing();
