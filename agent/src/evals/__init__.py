"""The eval harness (docs/05-, issue #6).

Answers "how do you know it works" with a number, and produces the one-page
report `docs/07-` puts on screen in the meeting.

It runs headless against the framework-free core (ADR-002): buyer text in,
validated speech and actions out, no audio stack, no room, no vendor. That is
what makes it CI-runnable and mandatory on every prompt change.

Two modes, and the difference between them is the difference between two
claims:

  offline (default)  the model's reply comes from a fixture recorded or
                     authored beside the case. This measures THE PIPELINE:
                     given that reply, what does the buyer actually hear. It
                     needs no key and spends nothing, so it runs in CI on
                     every commit.
  live (--live)      the reply comes from the real model behind the real
                     ambassador prompt. This measures THE MODEL. It costs
                     money and varies, so it is opt-in and per-category.

Both modes evaluate the SAME assertions against the SAME user-facing outcome -
the sentences the buyer would have heard, in the order they were spoken. The
report states which mode produced each number, because a pass in offline mode
is not a claim about the model and must never be reported as one.
"""
