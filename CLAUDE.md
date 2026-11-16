# FFDB: how we build this

Read this at the start of every session. It describes the method, not the plan.
Charles owns this file. Change it when the method changes, and keep it to about a page.
The fuller version for Charles, with the build order and the open decisions, is
`docs/ffdb-development-guide.md`. If the two disagree, tell Charles.

## What FFDB is

A small public repository of structure-property data for fluid ferroelectric liquid
crystals, with every value tied to the paper or dataset it came from.

This folder holds the production version, started from an empty folder in November 2026.
The earlier prototype is at
`C:\Users\orang\OneDrive - University of Leeds\research_computational\ffdb\nf-repo-demo`.
It is a reference, not a base.

Scope, data model, data rules, permissions, hosting and the build order belong in the
planning document, `docs/ffdb.docx`. It is a Word file: read it by extracting its text
(for example with Python's `zipfile`, from `word/document.xml`) and never write to it.
If something isn't in the planning document, it is out of scope: ask before building it.
Already decided: no exploration features of any kind (cores, matched pairs, positions,
corners, islands, routes, boundary gaps), no tagged theory papers and no automatic PDF
extraction. In scope: search by name and structure, a molecule's properties with their
sources, a page per property, plots of stored curves, CSV export, a source on every data
point and a page per paper.

## Why it is built this way

The main aim is that Charles understands web development by building this. A working
site is the result of that, not a substitute for it.

The prototype was written by Claude Code from prompts and grew to about 15,000 lines of
Python. Code written that fast is hard to learn from. This build goes at the speed of
Charles's understanding: a slower build he can explain beats a faster one he can't.

Claude still writes the code from Charles's prompts. The prototype's notes also asked
Claude to explain changes as it went, and that was not enough. What is different here is
the size of each step and the check at step 5.

## The loop

Work in slices. A slice is one thing a visitor or curator can see or do, such as
"a page that shows one molecule". One slice at a time:

1. Charles picks the next slice from the build order and says what it should do and how
   he will check it.
2. Claude says which files will change, what will be added to each and why, and explains
   any idea that is new (a model, a migration, a view, a template, a form, a test) in a
   sentence or two. No code yet.
3. Claude writes the code for that slice only. If what was written differs from what
   step 2 said, Claude says so. Charles reads every line and asks about anything he
   can't explain before going on.
4. Run the tests and look at the result in the browser. Charles then makes one small
   change by hand, such as adding a field to a page, and runs the tests again.
5. Charles writes the slice's paragraph in the Technology section of `docs/ffdb.docx`
   without help and pastes it to Claude. Claude checks it against the code and says
   where the two disagree. Claude does not write or reword it.
6. Commit that one slice and push.

A slice is done after step 6, not before. Don't start the next slice until then.

## Rules for Claude

- Never build ahead of the current slice, even when the next step is obvious.
- If a prompt covers more than one slice, or doesn't say what the slice should do and
  how Charles will check it, say so before step 2.
- Keep a slice small enough to read in one sitting. If it needs more than about 200 new
  lines, propose a split.
- Use the simplest thing that works. Ask before adding a dependency.
- Question Charles's assumptions instead of going along with them, and say "I don't
  know" when that is the case.
- Use the prototype as a reference only. Bring across what the current slice needs and
  Charles can explain. Never copy a whole file.
- Every slice that adds code comes with at least one test. A setup slice has a written
  check instead.
- Don't add documentation files. The documents are this file, the development guide, a
  short README (how to set up and run it) and the planning document, which also holds
  Charles's notes on how the code works.
- Never commit secrets (`.env`), database files or publisher PDFs. Dataset files stay
  outside the repository and the import command is given their path. Ask before
  committing any data file, including test samples: git keeps everything ever committed.
- Keep the code able to run on any host: settings that differ between machines come from
  environment variables, the database is the only place the app stores anything, and
  nothing runs outside a web request. Production uses PostgreSQL, so use only Django's
  ordinary database features.
- Unpublished data waits in the review queue until an admin publishes it. There are no
  hidden records: everything in the public tables is public.
- Stop and ask when a slice touches an open decision: the licence, hosting, or which
  data goes in.
