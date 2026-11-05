# Task: real Hindi subtitles for Resident Evil 2 (2019 remake, Steam)

You are working in `D:\side_projects\GameMod`, a Python app that puts real Hindi subtitles into PC games by changing the game's own data files, with no screen overlay. It already works for The Last of Us Part I (TLOU1). Your job is to add Resident Evil 2 (RE2), translate its subtitles yourself, and get them working in the game and in the app.

You have no memory of the earlier work. Everything you need is in this file or in the files it names.

**If `data\re2\PROGRESS.md` exists, a previous session started this job. Read it first and carry on from where it stopped. Do not redo finished work.**

## What the owner wants

1. Hindi is a choice in the game's own language options. Picking it shows Hindi subtitles.
2. **English and all menus, item names, documents and hints stay exactly as shipped.** The owner rejected an earlier test for changing menu text, saying: "i just want subtitles nothing else should be change and keep english too". The only allowed visible menu change is one language name becoming हिन्दी.
3. Natural spoken Hindi in Devanagari, as good as the TLOU1 translation, which the owner liked.
4. An on/off switch in `GameMod.exe`, with a subtitle pack that friends can use.

The owner is not a modder. When you need them to do something, give short numbered steps, ask for one thing at a time, and say exactly what to look for.

## Rules

- **Translate the lines yourself, using your own judgment on every line.** Never write a script that generates Hindi from templates, dictionaries or word replacement. Never use an outside translation service, and do not use `gamemod.cli translate`: it calls a free Gemini key limited to about 20 requests a day. Do not sample some lines and copy the pattern onto the rest.
- **Never change existing game files**, including `re2.exe` and existing `.pak` files. Do not inject DLLs. Do not use REFramework or an overlay. Add new files only, so that uninstalling means deleting them. Write into the game folder only at the steps below that call for it, and only with the game closed.
- **Do not break TLOU1.** Do not run install or uninstall for TLOU1. Do not write to `data\tlou1\`, `subtitles\tlou1\` or the TLOU1 game folder. All its tests must still pass.
- **Never put test text or markers in the game.** In-game tests use real translated lines.
- **Packs contain Hindi only.** No English lines, game fonts or `.pak` files may go in `subtitles\` or `release\`. Fonts and the mod's `.pak` are built on each player's PC from their own game files.
- **Report honestly.** Say a step is done only after you have run the check and seen the output. Only the owner can confirm how the game looks, so never claim that yourself. If something failed or was skipped, say so.
- Treat tools you download as untrusted. Keep them outside this project, and check the licence before copying any of their code here.
- Stop and ask at each **GATE** below. Otherwise keep working without asking for permission on small decisions.

## Use helpers if they are available

If your environment offers helpers of any kind (subagents, parallel agents in the Agent Manager, background tasks or similar), use them for translating batches and for review. Start with 3 at once and add more if your limits allow.

- You, the main agent, do the code, the game test, batch hand-out and final checks.
- Each helper gets only the translator brief and its own list of batch files. Never give two helpers the same file. Give each one consecutive files, so conversations stay together.
- Helpers do not edit code, read the database or start other work.
- If helpers cannot run commands, have them write the `out` files and run `apply` yourself.
- If there are no helpers, do the same work yourself, one batch at a time, with the same files and checks. Say which way you worked.
- Use the strongest model you have for story lines. Avoid "lite" models: in small TLOU1 checks they got speaker gender wrong. A faster model is acceptable only for short repeated barks, and only after you have spot-checked its output.

You may hit usage limits partway. After every step and every few batches, update `data\re2\PROGRESS.md` with what is done, what is next and the exact commands to continue, so that a fresh session can pick it up. Do not paste translations into chat, and do not re-read finished batches.

## What is already known

### Checked on this PC on 2026-11-05 (read-only)

| Topic | Finding |
|---|---|
| Install | `D:\SteamLibrary\steamapps\common\RESIDENT EVIL 2  BIOHAZARD RE2` (two spaces before BIOHAZARD). Steam app 883710, build 11636119, `re2.exe` |
| Base archive | `re_chunk_000.pak`, 23.7 GB, 42,222 files |
| Patches | `re_chunk_000.pak.patch_001.pak` (331 files), `patch_002.pak` (471 files) |
| **Empty patches** | `patch_003` to `patch_007` all exist at **0 bytes**. A 0-byte file named `883710` is also in the folder. The reason is unknown. Do not delete them |
| DLC | `dlc\re_dlc_stm_920565.pak` (116 files), `dlc\re_dlc_stm_920566.pak` (107 files) |
| Pak format | All five non-empty paks start with `KPKA`, version 4.0, feature flags 0. Tables are readable as 48-byte entries and the encryption byte is 0 on every entry. Compression values 0, 1 and 2 are all in use |
| **Text engine** | `re2.exe` contains HarfBuzz function names (such as `hb_shape_plan_execute`) and `via.gui.detail.GSUBTable` and `GPOSTable`, plus `FontManager`, `FontSlot` and `SubTitle`. The engine may be able to shape Hindi itself, but this is not proof |
| Mods | No `natives` folder and no REFramework DLL in the game folder |
| Language setting | `re2_config.ini` has no language entry, so it is probably stored elsewhere |

### From memory, not checked: verify before relying on it

- Paths in paks are stored only as hashes (Murmur3 x86-32 of the UTF-16 lower-case and upper-case path, seed `0xFFFFFFFF`). Community path lists come with tools such as REE.PAK.Tool (Ekey) and REtool (FluffyQuack). You can also find text files by reading each entry's first bytes.
- Compression values are likely 0 = none, 1 = deflate, 2 = zstd.
- A higher-numbered patch pak overrides lower ones. Fluffy Mod Manager installs mods this way. To replace an existing file, a new pak can reuse the hash values from the existing entry.
- Text lives in `.msg.<version>` files, with `GMSG` near the start. **One `.msg` file holds every language**, unlike TLOU1, which had one table per language. Strings are lightly obfuscated. REMSG_Converter (dtlnor) documents the format. Text may contain `<...>` tags.
- Entry names, attributes or file paths may reveal the scene or speaker.
- Fonts are `.oft.<version>` files, which may be obfuscated OTF or TTF files.

### The existing code

Read these before designing anything:

- `gamemod\adapters\base.py`: the interface a game must implement. `gamemod\adapters\tlou1\adapter.py` is the worked example.
- `gamemod\adapters\registry.py`: where a new adapter is listed.
- `gamemod\core\devanagari\`: the Hindi font builder. It draws each syllable cluster as one glyph on Private Use codes `U+E900-F6BF` and merges them into a game font, so the game needs no text shaper.
- `gamemod\core\translate\qa.py`, `core\store.py`, `core\pipeline.py`, `core\pack.py`: checks, database, install flow, subtitle packs. Reuse them.
- `data\tlou1\batches\TRANSLATOR_BRIEF.md`: the approved translation voice. Read it closely.
- `build.bat`, `tools\make_release.py`: builds the exe and the folder to share.

Problems you will hit in that code:

1. `gamemod\cli.py` is hard-wired to `Tlou1Adapter`. Add a `--game` option that defaults to `tlou1`. Search `gamemod\` and `tools\` for other TLOU1 assumptions.
2. `cli.py export` **deletes existing `part_*.tsv` files** in its folder. Never run it while helpers are working.
3. `cli.py apply` records the translator as `claude` unless given `--model`. Pass your real model name, such as `--model antigravity-<model>`, so the database stays accurate.
4. `export` treats order positions below 1,000,000 as story and the rest as combat. Choose RE2 order numbers with that in mind, or make the split a setting.
5. `qa.py` blocks control characters, including `\r`, and `apply` splits lines on `\r`. If `.msg` text uses `\r\n`, convert it to `\n` when extracting and restore it when building.
6. `build.bat` adds the TLOU1 glossary by name and exports one pack. It must handle every registered game.
7. The zstd reader will need a new dependency in `requirements.txt`, and possibly in `build.bat`.
8. The Private Use range was chosen to avoid codes in TLOU1's fonts. Check it against RE2's fonts too.

## Plan

Put the pak and `.msg` code in `gamemod\adapters\re_engine\`, since the owner has other RE Engine games, and RE2's own details in `gamemod\adapters\re2\`. Do not build support for those other games now. Write tests as you go, as the existing `tests\` do, including: a `.msg` rebuilt with no changes matches the original byte for byte, and changing one slot changes nothing else.

### Step 0: safety

This folder is not a git repository, and the working TLOU1 mod lives here.

1. Run `git init`. Add a `.gitignore` covering `.venv/`, `data/`, `release/`, `build_tmp/`, `GameMod.exe`, `__pycache__/` and `*.pyc`. Commit a snapshot of everything else. Stay local: add no remote and never push. Commit again after each step.
2. Run `.venv\Scripts\python.exe -m pytest -q` and record the result as the baseline.

### Step 1: investigate (read-only)

Write a pak reader that layers base, then patches in number order, then DLC. It must skip the 0-byte files. Then find out:

1. Which `.msg` files hold spoken subtitles (cutscenes, radio, in-game speech, bonus modes), and which hold menus, items, documents and hints. Give file counts, line counts and English samples for each group.
2. Which language slots exist in the files, and which the game's menus offer. Is there a separate subtitle language setting, or does one setting change all on-screen text?
3. Which language slot should Hindi take. It must be Latin-script and left-to-right, and must have no voice audio, so that the voice list is unaffected. Do not use Arabic or Chinese, Japanese or Korean, since those use different fonts or text direction. Do not use English. Do not use Russian if you need it for the gender hints below.
4. Whether Russian text is present, and whether entry names, attributes or paths identify speakers.
5. Which fonts that slot uses for subtitles and menus, their format, how many glyphs they hold, and which Private Use codes they already use.

Write the results to `data\re2\FINDINGS.md`.

**GATE 1.** Tell the owner in plain words: how many lines there are, what is in scope, which language slot you recommend and what they would see in the game's menu. Wait for their answer.

If the game has only one text language setting, menus must not turn into another language. Every non-subtitle text in the Hindi slot must then be a copy of the English text, in every `.msg` that can appear, as TLOU1 did with its `hrv.common` table. This is the design to use unless Step 1 finds something better.

### Step 2: prove it works in the game

This is the go or no-go test. Do not start the full translation before it passes.

1. Translate about 40 real lines from the opening scenes for Leon and Claire, following the brief in Step 3.
2. Build one new patch pak with those lines, the language name हिन्दी, the English copies from Step 1, and the merged fonts. Store files uncompressed.
3. **Use the existing cluster-as-glyph font method first.** It does not depend on the engine's shaper and is already written. Try raw Devanagari only if it fails.
4. To test font limits, build the fonts with every cluster used in `subtitles\tlou1\hindi.json` (read-only), roughly 3,000, not only those in the 40 lines. The final build uses the real set.
5. Name the pak with the first number above all existing patches, working it out at install time rather than hard-coding it. On this PC that means `patch_008`. Write to a temporary name and rename when complete.
6. With the game closed, test the removal first: list every file in the game folder with its size, install, confirm only the new file appeared, uninstall, and confirm the listing matches exactly. Then install again for the owner.

**GATE 2.** Ask the owner to launch the game, pick हिन्दी and play the opening. They check:

- Does the language appear, and is the rest of the language list unchanged?
- Are all menus still in English with the Hindi slot chosen?
- Do subtitles show in Hindi, with vowel signs in the right place and joined letters like क्ष and र्म correct?
- Is the text a good size, and do long lines fit?
- Does choosing English bring back the normal game?

Then uninstall, and confirm the game folder listing matches the original.

If it fails, change one thing at a time:

| What happens | Try |
|---|---|
| Game ignores the pak | Check how the loader treats the empty patches. Try loose files if the engine supports them. Using an empty placeholder number needs the owner's approval, and uninstalling must leave it at 0 bytes |
| Boxes or blanks instead of Hindi | The wrong font was changed, or the subtitle font differs from the menu font. Check the font slots |
| Crash or missing text with many glyphs | Font memory or atlas limit. Reduce clusters to the real set, or use a smaller font base |
| Letters misplaced or broken | Find out whether the engine's shaper is altering the text. Try raw Devanagari |
| Menus change language | The English copies are incomplete. Find which `.msg` was missed |

If Hindi cannot be made to work, stop and discuss it with the owner. Roman-script Hinglish is a possible fallback, but only if the owner chooses it.

### Step 3: write the brief and run a pilot

Create `data\re2\batches\TRANSLATOR_BRIEF.md`, a self-contained file that helpers can follow without reading anything else:

- Copy from the TLOU1 brief: "Style", "Who is speaking: gender", "Swearing", "Line format rules" and "Approved examples". Those examples are the owner's approved voice, so match it. Remove TLOU1-specific names and context from everything you copy, but keep the example table as it is.
- Replace its job steps with this game's paths and with commands using `--game re2` and `--model`.
- Add the RE2 section below, after checking the names against the game's text.
- If `.msg` data identifies speakers, put that in each line's `hint`. Keep the Russian too, since its verb endings show gender.
- Make sure the duplicate handling in `lines_to_translate` and `can_share_translation` does not give Leon and Claire the same gendered line. They often speak the same English.

Translate about 200 varied lines: a Leon cutscene, a Claire and Sherry scene, Ada, Irons, Marvin, radio lines and short reactions.

**GATE 3.** Show the owner about 30 lines as a table of English and Hindi, including the difficult ones. Wait for approval and apply their feedback to the brief before going on.

### Step 4: translate everything

1. Run `.venv\Scripts\python.exe -m gamemod.cli --game re2 export data\re2\batches\in --count 250`.
2. For each file, the helper reads `in\part_NNNN.tsv`, writes `out\part_NNNN.tsv` with one `id<TAB>Hindi` line per input line, and runs:
   `.venv\Scripts\python.exe -m gamemod.cli --game re2 apply data\re2\batches\out\part_NNNN.tsv --model <name>`
3. `REJECTED` lines were not saved and must be fixed and applied again from a `.fix.tsv`. `check` lines were saved but flagged. Fix them where the flag is right. Confirm how `apply` treats a line that is already saved before relying on it for corrections.
4. A helper may read the last 20 lines of the previous `in` file for context.
5. When all files are done, run `fill`, then `status`. Keep going until nothing is unfinished. Do not stop at "most". Report any line left in English.

**Review.** Have a helper or a fresh pass read about 50 lines per 1,000, plus every Sherry scene, and look for:

- Wrong speaker gender, or adjectives and verbs that do not agree with the noun.
- Stiff, formal Hindi: यह and वह instead of ये and वो, or words like कृपया, आवश्यक, अत्यंत where people would not say them.
- Forms of address that change partway through a conversation.
- Names spelled differently from the glossary. Run a search for these across all lines.
- Lines that have become much longer than the English.

If a batch is poor, redo that batch rather than patching a few lines. Write what was found and fixed to `data\re2\review_notes.md`.

### Step 5: finish the app

1. Complete the adapter: `id = "re2"`, title, `steam_app_id = 883710`, `folder_names`, `required_files`, and `where_to_enable` using the real menu path. Refuse to install while `re2.exe` is running.
2. Map the app's install states to this design. `GAME_UPDATED`: our pak is missing or changed, or a new non-empty pak appeared above ours. `INSTALLED_ELSEWHERE`: our pak exists but this copy did not record it. `INTERRUPTED`: a leftover temporary file. Identify our pak by a recorded hash, not by its number, since numbers will differ on friends' PCs and may clash with their other mods.
3. Register it in `registry.py`. Check that the Library page shows it, and that TLOU1 still works there.
4. Run `export-pack` for RE2 and confirm `subtitles\re2\hindi.json` contains only Hindi. Run `build.bat`. To smoke-test `GameMod.exe`, close it with `taskkill /T`, since a one-file build runs as two processes. On the owner's PC, the `release` copy showing "another folder has changed this game" for TLOU1 is expected and is not a bug.
5. Run the full test suite and compare it with the Step 0 baseline.
6. Install the full translation.

**GATE 4.** Ask the owner to play. Have them check: the opening as Leon, the first Marvin scene, a Claire and Sherry scene, a scene with a lot of dialogue, and the English option. Then have them turn the mod off in the app, and confirm the game folder listing matches the original.

### When you finish

Tell the owner in plain language: how many lines are translated, anything left in English, which helpers and models did the work, test results, what they confirmed in the game, what remains unconfirmed, and how to undo the mod. Do not include translations in that message.

## RE2 section for the translator brief

Verify these names and details against the game's text, and fix anything wrong.

**Story:** September 1998. A viral outbreak has turned Raccoon City into a city of zombies. Leon, a rookie cop on his first day, and Claire, a college student searching for her brother Chris, fight through the police station and the secret Umbrella lab beneath the city. This is survival horror, so keep lines tense and short.

**Male:** Leon S. Kennedy लियोन एस. केनेडी (usually लियोन), Marvin Branagh मार्विन ब्रैना, Brian Irons ब्रायन आयरन्स (Chief Irons = चीफ़ आयरन्स), William Birkin विलियम बर्किन, Robert Kendo रॉबर्ट केंडो, Ben Bertolucci बेन बर्टोलुची, Chris Redfield क्रिस रेडफ़ील्ड, Elliot Edward इलियट एडवर्ड, Daniel Cortini डेनियल कोर्टिनी, HUNK हंक, Mr. X मिस्टर एक्स.

**Female:** Claire Redfield क्लेयर रेडफ़ील्ड, Ada Wong एडा वॉन्ग, Sherry Birkin शेरी बर्किन (a young girl), Annette Birkin एनेट बर्किन (Sherry's mother), Katherine Warren कैथरीन वॉरेन, Emma Kendo एमा केंडो (Robert's daughter), Jill Valentine जिल वैलेंटाइन.

**Voices:**

- Leon: earnest, decent, sometimes dryly funny.
- Claire: tough and quick, and gentle with Sherry.
- Ada: cool, teasing, gives little away.
- Marvin: exhausted, wounded, still in command.
- Irons: arrogant and vile.
- Sherry: a frightened but brave child, so use simple words.
- Annette: cold and desperate.
- Kendo: broken by grief.
- Ben: a cynical reporter.

**How characters address each other:**

- Leon and Claire, Leon and Ada: तुम.
- Marvin to Leon or Claire: तुम. Leon or Claire to Marvin: आप.
- Claire or Leon to Sherry: तुम. Sherry to adults, including her mother: आप.
- Enemies, threats and anger: तू. Irons uses तू when he threatens anyone.
- Polite strangers: आप.
- Keep the same form through a conversation unless the mood truly changes.

**Fixed terms:** Raccoon City = रैकून सिटी. Police station = पुलिस स्टेशन. RPD = आरपीडी. Umbrella = अम्ब्रेला (the company, never छाता). S.T.A.R.S. = स्टार्स. NEST = नेस्ट. G-virus = जी-वायरस. T-virus = टी-वायरस. Virus = वायरस. Zombie = ज़ॉम्बी. Licker = लिकर. Tyrant = टायरेंट. Plant 43 = प्लांट 43. Sewer = सीवर. Orphanage = अनाथालय. Lab = लैब. Chief = चीफ़. Rookie = रूकी. Officer = ऑफ़िसर. Gun shop = गन शॉप.

**Sherry is a child.** Irons is a predator in this story, and some of his lines toward or about her are menacing. Keep the menace and the fear, but no Hindi line may be sexual or suggestive about her. If the English hints at that, soften it to plain threat. List each such line's id in `data\re2\softened_lines.md` so the owner can read them. The TLOU1 translation made the same choice for a 14-year-old character.

**Shared lines:** Leon and Claire often have the same English line. A line with no "I" or "you" may be reused for both, so keep it gender-neutral.
