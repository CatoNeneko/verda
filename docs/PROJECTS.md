# Project facts and task state

vdra activates the context policy for the account and user in `work-memory.json`. For a typical local installation:

```json
{
  "enabled": true,
  "account": "default",
  "user": "default",
  "projects": {
    "garden-study": {
      "aliases": ["Garden Study", "花园课题"],
      "facts_uri": "viking://user/default/memories/entities/projects/garden-study.md"
    }
  }
}
```

Store stable boundaries and current decisions in the short main card. Use topic cards for supporting facts, and date and label superseded records. A source URI and digest allow the context builder to verify explicitly linked evidence before it is reused. Record dated checkpoints against the actual Agent session ID when handing off a task. Run `python -m vdra memory --home ./state` to see the state and record commands.

The Alpha can reuse native indexed evidence, active project facts and local checkpoints. The public package has no private corpus, no existing session bindings, and no background migration of Agent histories. The same server can serve independently installed client bundles on the same machine.
