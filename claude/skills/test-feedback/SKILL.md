---
name: test-feedback
description: Live feedback for a long test. Use when building or changing a test that runs longer than about ten seconds.
---

# Test feedback

A test that runs longer than about ten seconds shows what it is doing while it runs. I should never wait for the end to learn it already failed. When you build or change such a test, give it all of these:

- A health status for the whole run: passing so far, failed, or stalled. It changes the moment a check fails or data stops arriving, and it alerts me.
- A live graph of any readings the test collects, drawn as they arrive.
- A recording of every run with timestamps, replayable afterwards at a speed I choose.
- For a test of a web page, a screenshot at each step, shown live and in the replay.

Show it on one local browser page, with a one-line status in the terminal. Watch the test from outside (wrap the command, read the files it writes) before editing the test itself. Use what the machine already has, and make it work on Windows. Never stop a failed test automatically unless the test opts in, because hardware may need a safe shutdown.
