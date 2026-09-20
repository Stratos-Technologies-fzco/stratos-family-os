# Stratos Family OS

A live collaborative wall. One repo, many terminals.

**Live: https://stratos-technologies-fzco.github.io/stratos-family-os/**

Forty nine real organisations, one card each, all of them empty. Your job is to fill them with
what AI could actually do inside those companies.

## Your job

Pick a card. Read what the people inside that organisation do all day. Then write down every AI
use case you can see, and for at least one of them, what five minutes on a screen would look like.

Not one use case. As many as you can see.

## How to contribute, from your terminal

**You have push access. No pull request, no waiting for anyone to merge.**

    git clone https://github.com/Stratos-Technologies-fzco/stratos-family-os.git
    cd stratos-family-os

Open `docs/index.html` and search for the company name. Every card sits between its markers:

    <!-- ===== START zerodha ===== -->
    ...the card...
    <!-- ===== END zerodha ===== -->

Paste your block inside that card's `<div class="cases">`. Then push straight to `main`:

    git pull --rebase
    git add -A
    git commit -m "yourname: use cases for Zerodha"
    git push

The site rebuilds and your use case is on the wall in about a minute, with your name on it.

### The block to copy

```html
<div class="case">
  <h3>The weekly field report writes itself</h3>
  <p><b>Today</b> a field officer visits twelve schools a week and writes the report by hand every Friday. Three hours, and most of it is the same every week.</p>
  <p><b>With AI</b> an agent reads the visit notes and last week's report and drafts this week's. Friday becomes twenty minutes.</p>
  <p><b>Five minutes</b> open three real visit notes, run it live, read the report out, change one note and run it again so they see it is not a template.</p>
  <p class="by">@yourhandle</p>
</div>
```

Keep the four lines. The last one is your GitHub name, that is how the counter knows it is yours.

### If the push is rejected

Somebody else pushed while you were editing. Pull their work in and push again:

    git pull --rebase
    git push

If that reports a conflict, open `docs/index.html`, find the `<<<<<<<` markers, keep both people's
cards, delete the marker lines, then `git add -A && git rebase --continue && git push`.

## What makes a good one

Bad: "AI chatbot for customer service."

Good names the person, says what they do today and how long it takes, then shows something moving
on a screen. Every card has a "What they do" section that tells you what the people inside actually
spend their days on. That is where the use case comes from, not from the industry.

If you are guessing about how a company works, say so in the card. That is fine and useful.

## Rules

- Edit inside a card only. Everything outside its markers belongs to somebody else.
- This page is public. Never write anything about prices, contracts, or private conversations.
- Nothing here is a client list or a claim about a commercial relationship with any organisation.
- Do not invent facts about a company.

## Who runs it

Sarfaraj manages this repo. Sahariar reviews which use cases are worth turning into a real demo.
Sahil decides which demos get built and shown.
