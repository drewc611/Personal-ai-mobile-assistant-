# v2 only

Empty on purpose. PLAN.md puts voice in v2 and CLAUDE.md says not to start a
later phase without Andrew saying so.

When it does get built, it arrives through the Channel interface in
`channels/`, not around it. Twilio SMS and voice become another implementation
of `Channel`; the gate, the approvals, the audit log and the conversation
handler should need no change. That is the test of whether the interface was
drawn in the right place.

Two things are blocked until then:

- **Outbound calls where the agent speaks to a business** (bill negotiation,
  appointments) wait on a legal check on call recording consent and AI
  disclosure, and Andrew's sign-off on that check.
- **Hold for me** - the agent waits on hold and bridges Andrew in when a human
  answers - is the least legally complicated of the voice ideas, because
  Andrew is the one on the call. It is still a recorded outbound call, so it
  still waits.
