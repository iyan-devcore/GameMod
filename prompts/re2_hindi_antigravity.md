# Resident Evil 2: translating in Antigravity

Antigravity only translates this game. Claude does the code, the checking and the install.

Normally this runs by itself through the command line:

    .venv\Scripts\python.exe tools\translate_with_agy.py data\re2\batches --model claude-sonnet-4-6 --workers 3

To do it in an Antigravity chat instead, paste this:

    Translate only. Read data\re2\batches\TRANSLATOR_BRIEF.md and follow it. For each file in data\re2\batches\in that has no matching file in data\re2\batches\out, write out\part_NNNN.tsv with one line per input line: id, a tab, then the Hindi. Do not read, change or run anything else. Do not touch the game folder, the code or the database.
