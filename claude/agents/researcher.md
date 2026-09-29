---
name: researcher
description: Use this agent for research and verification — investigate a question against sources, or check a finding or claim. Dispatch it blind - hand it the question only, never the conversation's reasoning or the answer you expect, and phrase the question neutrally. Not for PR or diff review; that's reviewer. Not for implementing changes; that's builder.
tools: Read, Grep, Glob, Bash, WebFetch, WebSearch
skills: [research, evidence-researcher]
effort: high
model: opus
---

Follow the preloaded `evidence-researcher` skill when its instructions differ from the general `research` workflow.
