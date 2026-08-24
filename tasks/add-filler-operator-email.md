# Draft — email to MC operators re: Add filler feature

Subject: New "Add filler" option in Set up Daily Programming — please live-test

Hi team,

The filler feature you asked for is now part of Set up Daily Programming — no more
opening Multi Grid to pad shows like The Point, Punjab Update, World News, Music
Project, You are Hired, Founders, Places We Must Go, Ang Pandday, or News Express.

How it works:

- Assign a show the way you always do (click the row, pick the file). The filler
  step now appears for every show — even when the pieces already match the breaks.
- The window tells you how much time is left over after the show's pieces
  (e.g. "8:12 leftover").
- Click "➕ Auto-fill leftover" and it covers that time from the right library
  automatically: Chinese shows get Chinese Filler / UNIAM, Filipino and Jus
  Punjabi shows get UNIAE, Korean gets K-FILLER. It slightly overfills on
  purpose — a spare filler is a one-click delete later, while coming up short
  means hand-inserting a filler in every market.
- You can also search and add fillers yourself, and remove any pick, before you
  hit Done. Extra fillers stack after the show's last piece.
- If a show doesn't need filler that day, just click "Done — no fillers" and
  everything works exactly like before.
- Languages without a filler library (e.g. Vietnamese, Hmong) don't get the auto
  button, but manual search still works for them.

**One favor before you use it everywhere: please live-test it on a single market
first.** You know these shows and hours better than we do, so you're the right
people to judge it:

1. Pick ONE market (use the market pill, not the whole network) and one show
   that you know underfills its hour — The Point is a good candidate.
2. Assign it, check the leftover time it reports against what you'd expect from
   Multi Grid, use Auto-fill (or pick manually), and Run.
3. Then look at that hour in Executive Editor: open bumper → show pieces →
   fillers after the last piece → close bumper last, times looking normal, no
   yellow triangles.
4. Also glance at the NEXT hour's show on the page — its "placed" badge should
   NOT light up just because fillers ran near the end of the previous hour.

If that one market looks right, it's good to use across the network. If anything
looks off, stop there and tell me what you saw — one market is easy to clean up.

This first version is built on my best guesses about your workflow, and all of it
is adjustable — tell me what would make it better. Some options already on the
table:

1. **Add fillers to an already-placed show.** Right now fillers are chosen during
   setup; if you only notice the gap after the show is placed, it's still Multi
   Grid. We can add an "add filler" action to placed shows too.
2. **Skip the extra step for shows that never need filler.** Currently every show
   shows the filler step (one extra click). We can limit it to your list of
   filler-prone shows instead.
3. **Auto-fill automatically for specific shows** — the way Korean dramas already
   pre-fill on weekdays — so the fillers are picked before the window even opens.
4. **Adjust which fillers each language uses**, or add pools for more languages.
5. **Change how picks are made** — e.g. exact-fit instead of slight overfill.

Thanks — let me know how the test goes.

Lee
