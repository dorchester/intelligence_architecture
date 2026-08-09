# Consultant / engagement lead

You run engagements and approve what ships. Your surface is the **hosted
console** in a browser — no AWS account, no credentials beyond your login,
nothing to install. You are also the only person whose action a running
report ever waits for: the checkpoints are yours.

---

## 1. Getting in

The console lives at the project's App Runner URL (ask your team, or it's
the `ServiceUrl` output of the app stack). There is **no self-signup** —
a data steward creates your login and gives you your initial password.
Locked out or need a colleague added? That's a steward request, turned
around in a minute.

## 2. Running a report

1. Sign in, pick the client (e.g. **Sterling Pharma** in the demo data).
2. Start the run. Early on, **entity verification** checks the company is
   real and resolvable — a nonsense name fails fast and cheap (hundreds of
   tokens, not tens of thousands) before any heavy generation starts.
3. The pipeline runs in stages; the run view shows progress live.

Three behaviors worth trusting because they're structural:

- **Close the browser whenever you like.** The run's state is durable and
  server-side; it will be exactly where you left it, and a run waiting at a
  checkpoint consumes nothing while it waits — hours or days.
- **Every run is scoped** by `run_id` and client; nothing bleeds between
  engagements.
- **Which pipeline version ran is recorded** in the run itself — if a
  finding is questioned later, the exact code and prompts that produced it
  are identifiable.

## 3. Checkpoints: your actual job in the loop

The workflow pauses at a **midpoint** and a **final** checkpoint. At each
you choose:

- **Approve** — the run continues (midpoint) or finishes (final).
- **Request revision** — write concrete feedback; the relevant stages
  re-run *with your feedback injected into their prompts*. Vague feedback
  produces vague revisions: "the attrition section contradicts the
  composition chart" beats "make it better."
- **Reject** — the run ends as rejected, recorded as such.

Revisions are bounded (default: two per checkpoint), so a run cannot loop
forever; after the limit it's approve or reject. The checkpoint is the
deliberate human-in-the-loop — nothing ships that you didn't look at.

## 4. Reading the output like a pro

- The report's numbers come from governed aggregate data with small cells
  suppressed; if a breakdown seems oddly coarse, that's the privacy floor
  working, not an error.
- An **advisory reviewer** (a second model pass) flags arithmetic errors,
  unsupported figures, and internal contradictions before you see the
  draft. It's advisory — it never blocks, and its concerns are shown, not
  silently applied. Your checkpoint judgment is the control that counts.
- Artifacts (charts, JSON, the report itself) are stored per run and stay
  available after completion.

## 5. What to ask for, from whom

| You want | Ask |
|---|---|
| A login for a colleague | Data steward |
| A new client dataset onboarded | Data steward (admission), then the pipeline team |
| The report to analyze something it doesn't | The FDE — that's a stage/prompt change |
| A number explained | Start with the run view's trace (the engineer surface shows the exact context each prompt saw); the FDE can walk you through it |
| Something looks wrong with the platform | The FDE first; platform engineer if it's infrastructure |

## 6. What you never have to worry about

- Your login grants no cloud permissions — everything the app does on your
  behalf runs under its own narrowly-scoped identity.
- You can't break the pipeline, the data tiers, or anyone else's run from
  the console, no matter what you click.
- Walking away mid-run costs nothing and loses nothing.
